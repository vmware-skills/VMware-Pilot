"""Pilot runs its own steps: the opt-in stdio dispatcher (HLD §19, D-25).

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
    if found:
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


class StdioDispatcher:
    """``WorkflowExecutor`` dispatch: one child ``vmware-<skill>-mcp`` per call."""

    def __init__(self, timeout: float | None = None) -> None:
        self._timeout = timeout

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


def _payload(result: Any) -> Any:
    """The tool's own data, as the workflow stores it and step references read it.

    A raw MCP frame would be stored instead, and ``__from_step_N__:plan_id``
    would look for ``plan_id`` on the frame (live run, 2026-10-18). FastMCP puts
    a dict return in ``structuredContent`` and wraps anything else as
    ``{"result": ...}``; a frame with ``isError`` becomes the family's error
    envelope so the executor records a failure.
    """
    texts = [t for t in (getattr(c, "text", None) for c in result.content or []) if t]
    if result.isError:
        return {"error": " ".join(texts) or "the tool reported isError with no message"}
    structured = result.structuredContent
    if isinstance(structured, dict):
        if set(structured) == {"result"}:
            return structured["result"]
        return structured
    if len(texts) == 1:
        try:
            return json.loads(texts[0])
        except ValueError:
            return texts[0]
    return texts


async def _call(command: str, tool: str, params: dict[str, Any], timeout: float) -> Any:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    server = StdioServerParameters(
        command=command, args=[], env=dict(os.environ), cwd=str(Path.home())
    )

    async def run() -> Any:
        async with stdio_client(server) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool(tool, params)

    try:
        return _payload(await asyncio.wait_for(run(), timeout))
    except asyncio.TimeoutError:
        raise DispatchTimeoutError(
            f"{Path(command).name} did not answer {tool} within {timeout:g} s; the call was "
            "abandoned and its effect is UNKNOWN — check the target system before retrying "
            f"or rolling back (raise {ENV_TIMEOUT} for long operations)."
        ) from None
