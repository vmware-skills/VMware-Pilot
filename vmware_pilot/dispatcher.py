"""Pilot runs its own steps: the opt-in stdio dispatcher (HLD §19, D-26).

Off by default. Without it Pilot's server executes nothing: every step goes back
to the calling agent in ``pending_dispatch``, so an approval gate is only an
attestation and a workflow never reaches ``completed``.

With it, each step ``(skill, tool, params)`` runs in **that skill's own MCP
server**: Pilot starts ``vmware-<skill>-mcp`` over stdio, calls the one tool and
stops the process. The call therefore passes through the skill's own
``@vmware_tool`` — its ``rules.yaml`` policy, its confirmation gate, its audit
row — and the skill reads its own config and credentials. Pilot holds none.

stdio only. A child server has no caller identity, so on a network transport
every step would run as the OS user that started Pilot and §18 roles could not
apply. ``server.main`` refuses to start that way, and :class:`StdioDispatcher`
refuses a call that carries a network caller.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from vmware_pilot.executor import StepEffectUnknownError
from vmware_pilot.renames import _server

#: ``VMWARE_PILOT_DISPATCH=stdio`` turns the dispatcher on (or ``vmware-pilot mcp --dispatch``).
ENV_FLAG = "VMWARE_PILOT_DISPATCH"
#: Seconds one step may take before it is reported as an unknown effect.
ENV_TIMEOUT = "VMWARE_PILOT_DISPATCH_TIMEOUT"
DEFAULT_TIMEOUT = 1800.0

_NAME = re.compile(r"^[a-z][a-z0-9-]*$")


class DispatchRefusedError(ValueError):
    """The step was not started: unknown skill, not installed, or a network caller.

    A ``ValueError`` so its authored text reaches the agent (``_safe_error``).
    Nothing ran, so the executor records an ordinary failed step.
    """


class DispatchConnectionLostError(StepEffectUnknownError):
    """The child went away after the call was sent. Whether it took effect is unknown."""


class DispatchTimeoutError(StepEffectUnknownError):
    """The child did not answer in time. Whether the tool took effect is unknown."""


def dispatch_requested(flag: bool = False) -> bool:
    """True when ``--dispatch`` was given or ``VMWARE_PILOT_DISPATCH`` is ``stdio``."""
    return flag or os.environ.get(ENV_FLAG, "").strip().lower() == "stdio"


def _family_skills() -> frozenset[str]:
    from vmware_pilot.mcp_server._catalog import SKILL_CATALOG

    return frozenset(SKILL_CATALOG)


def _interpreter_bin() -> Path:
    """Where console scripts installed alongside Pilot's own interpreter live."""
    return Path(sys.executable).parent


def resolve_command(skill: Any) -> str:
    """The ``vmware-<skill>-mcp`` executable for a family skill, or refuse."""
    if not isinstance(skill, str) or not skill.strip():
        raise DispatchRefusedError(f"Step names no skill ({skill!r}); Pilot cannot run it.")
    bare = _server(skill).removeprefix("vmware-")
    if not _NAME.match(bare):
        raise DispatchRefusedError(f"Skill name {skill!r} is not a family skill name.")
    if bare == "pilot":
        raise DispatchRefusedError(
            "A step names skill 'pilot' with a tool other than an approval gate; Pilot does "
            "not dispatch to itself. Use a require_approval step for a gate."
        )
    family = _family_skills()
    if bare not in family:
        raise DispatchRefusedError(
            f"Skill {skill!r} is not a VMware family skill Pilot can run "
            f"(known: {', '.join(sorted(family))})."
        )
    name = f"vmware-{bare}-mcp"
    beside = _interpreter_bin() / name
    if beside.is_file() and os.access(beside, os.X_OK):
        return str(beside)
    found = shutil.which(name)
    # A relative PATH entry (".") would resolve against the MCP client's cwd.
    if found and os.path.isabs(found):
        return found
    raise DispatchRefusedError(
        f"{name} is not installed where Pilot can find it (next to {sys.executable} or on "
        f"PATH), so this step was not run. Install the skill (uv tool install vmware-{bare}) "
        "and restart Pilot, or run Pilot without --dispatch and perform the step yourself."
    )


def _timeout_from_env() -> float:
    raw = os.environ.get(ENV_TIMEOUT, "").strip()
    if not raw:
        return DEFAULT_TIMEOUT
    try:
        value = float(raw)
    except ValueError:
        raise DispatchRefusedError(
            f"{ENV_TIMEOUT}={raw!r} is not a number of seconds."
        ) from None
    if value <= 0:
        raise DispatchRefusedError(f"{ENV_TIMEOUT} must be positive, got {raw!r}.")
    return value


def _refuse_network_caller() -> None:
    from vmware_policy.caller import current_caller

    if current_caller() is not None:
        raise DispatchRefusedError(
            "Pilot does not run steps for a network caller: the skill's server would act "
            "with no caller identity, so roles could not apply. Serve Pilot over stdio to "
            "use --dispatch, or perform the steps from pending_dispatch yourself."
        )


class StdioDispatcher:
    """``WorkflowExecutor`` dispatch: one child ``vmware-<skill>-mcp`` per call."""

    def __init__(self, timeout: float | None = None) -> None:
        self._timeout = timeout

    def preflight(self, steps: list[Any]) -> None:
        """Refuse before step 0 if any step's skill cannot be started."""
        _refuse_network_caller()
        problems = []
        for step in steps:
            try:
                resolve_command(step.skill)
            except DispatchRefusedError as exc:
                problems.append(f"step {step.index} ({step.skill}.{step.tool}): {exc}")
        if problems:
            raise DispatchRefusedError(
                "Nothing was run — these steps could not be started: " + " | ".join(problems)
            )

    def __call__(self, skill: str, tool: str, params: dict[str, Any]) -> Any:
        from vmware_policy.caller import current_caller

        if current_caller() is not None:
            raise DispatchRefusedError(
                "Pilot does not run steps for a network caller: the skill's server would act "
                "with no caller identity, so roles could not apply. Serve Pilot over stdio to "
                "use --dispatch, or perform the steps from pending_dispatch yourself."
            )
        command = resolve_command(skill)
        timeout = self._timeout if self._timeout is not None else _timeout_from_env()
        # The executor is synchronous and may itself be running inside an event
        # loop (FastMCP calls sync tools on its loop thread): run the client in a
        # thread of its own.
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, _call(command, tool, params, timeout))
            return future.result()


def _leaf(exc: BaseException) -> str:
    """The first concrete error inside an (anyio) ExceptionGroup, as one short line."""
    # Duck-typed: BaseExceptionGroup is builtin only from 3.11 (exceptiongroup backport on 3.10).
    while getattr(exc, "exceptions", None):
        exc = exc.exceptions[0]
    text = str(exc).strip().splitlines()[0][:200] if str(exc).strip() else ""
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def _payload(result: Any) -> Any:
    """The tool's own data, as the workflow stores it and step references read it.

    A raw MCP frame would be stored instead, and ``__from_step_N__:plan_id``
    would look for ``plan_id`` on the frame (live run, 2026-10-18). FastMCP puts
    a dict return in ``structuredContent`` and wraps anything else as
    ``{"result": ...}``; a frame with ``isError`` becomes the family's error
    envelope so the executor records a failure.
    """
    texts = [t for t in (getattr(c, "text", None) for c in result.content or []) if t]
    structured = result.structuredContent
    if isinstance(structured, dict) and set(structured) == {"result"}:
        structured = structured["result"]
    if result.isError:
        if isinstance(structured, dict) and structured.get("error"):
            return structured
        return {"error": " ".join(texts) or "the tool reported isError with no message"}
    if structured is not None:
        return structured
    parsed = [_maybe_json(t) for t in texts]
    return parsed[0] if len(parsed) == 1 else parsed


def _maybe_json(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return text


async def _call(command: str, tool: str, params: dict[str, Any], timeout: float) -> Any:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    server = StdioServerParameters(
        command=command, args=[], env=dict(os.environ), cwd=str(Path.home())
    )
    name = Path(command).name
    sent = False

    async def run() -> Any:
        nonlocal sent
        async with stdio_client(server) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                sent = True
                return await session.call_tool(tool, params)

    try:
        return _payload(await asyncio.wait_for(run(), timeout))
    except asyncio.TimeoutError:
        raise DispatchTimeoutError(
            f"{name} did not answer {tool} within {timeout:g} s; the call was abandoned and "
            "its effect is UNKNOWN — check the target system before retrying or rolling back "
            f"(raise {ENV_TIMEOUT} for long operations)."
        ) from None
    except Exception as exc:  # noqa: BLE001 — the transport's errors arrive as ExceptionGroups
        cause = _leaf(exc)
        if sent:
            raise DispatchConnectionLostError(
                f"{name} ended while running {tool} ({cause}); whether the call took effect "
                "is UNKNOWN — check the target system before retrying or rolling back."
            ) from None
        raise DispatchRefusedError(
            f"{name} did not start or did not finish the MCP handshake ({cause}); nothing "
            f"was run. Start {name} by hand to see its error (install, config, credentials)."
        ) from None
