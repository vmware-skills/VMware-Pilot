"""Shared MCP instance + helpers for VMware Pilot tools.

Holds the single FastMCP instance every tool module registers onto and small
helpers (template-name validation, YAML persistence). The store/executor
accessors live in ``vmware_pilot.mcp_server.server`` (the patch target the test-suite uses);
the ``_get_store``/``_get_executor`` wrappers here defer to them at call time so
that ``monkeypatch.setattr(server, "_store", ...)`` is observed.
"""

from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP
from vmware_policy import sanitize

from vmware_pilot import __version__
from vmware_pilot.executor import WorkflowExecutor
from vmware_pilot.models import WorkflowStore

logger = logging.getLogger(__name__)

#: Exception types this package raises on purpose, whose text it authors and
#: therefore trusts to reach the agent verbatim. ``ValueError`` is the whole
#: list because it is the whole vocabulary: the executor's step-reference and
#: redacted-secret refusals, and ``_validate_template_name``'s traversal
#: rejection. Each names what to run next.
#:
#: ``RuntimeError`` is deliberately absent. It is Python's generic catch-all, so
#: allowing it through would pass any driven skill's raw text as if pilot had
#: written it — and pilot dispatches to every other skill in the family, so that
#: text is exactly the untrusted material this wrapper exists to withhold.
#: ``executor._noop_dispatch`` does raise one with an authored message; it is
#: documented as never invoked, and it wants a domain exception of its own
#: rather than a hole here.
_TEACHING_ERRORS = (ValueError,)


def _safe_error(exc: Exception, tool: str) -> str:
    """Return an agent-safe error string; log full detail server-side only.

    Pilot orchestrates the other skills, so an unplanned exception here can be
    carrying a driven skill's raw API text — a vCenter fault body, a NSX
    response, a task URL with credentials in its userinfo. Full traceback goes
    to the server log; the agent sees only a control-char-stripped,
    length-capped message.

    This does not touch gate refusals. Every guard in the executor — terminal
    state, missing approver, not-awaiting-approval — *returns* its payload
    rather than raising, so a refusal never reaches this function and cannot be
    reduced to a class name by it.

    500 rather than the family's usual 300: three of the executor's six authored
    refusals are already longer than 300 characters before interpolation, the
    redacted-secret one at 403, and in each the remedy comes last. Capping at
    300 would cut off the instruction and leave the diagnosis.
    """
    logger.error("Tool %s failed", tool, exc_info=True)
    if isinstance(exc, _TEACHING_ERRORS):
        return sanitize(str(exc), 500)
    return f"{type(exc).__name__}: operation failed."

mcp = FastMCP(
    "vmware-pilot",
    instructions=(
        "VMware workflow orchestration. Plan and execute multi-step operations "
        "(clone-test-approve-commit, incident response) with state persistence, "
        "approval gates, and automatic rollback. "
        "Use plan_workflow to create a plan, run_workflow to execute, "
        "approve to continue past gates, rollback to abort."
    ),
)

# FastMCP takes no version argument and leaves the lowlevel server's at
# None, which makes `initialize` answer with the MCP SDK's version rather
# than ours. Set it so a client can tell which release it is talking to.
mcp._mcp_server.version = __version__


def _get_store() -> WorkflowStore:
    # Defer to the canonical accessor in vmware_pilot.mcp_server.server so test patches of
    # server._store are honoured. Imported lazily to avoid a circular import.
    from vmware_pilot.mcp_server import server

    return server._get_store()


def _get_executor() -> WorkflowExecutor:
    from vmware_pilot.mcp_server import server

    return server._get_executor()


def _validate_template_name(name: str) -> str | None:
    """Return an error message if ``name`` is unsafe as a template filename.

    ``name`` is user-supplied and becomes a filename — reject traversal so a
    template cannot be written outside the workflows dir.
    """
    if not name or "/" in name or "\\" in name or name.startswith(".") or "\x00" in name:
        return (
            f"Invalid workflow name {name!r}: must be non-empty with no path "
            "separators, leading dots, or null bytes"
        )
    return None


def _save_as_yaml(name: str, description: str, steps: list[dict[str, Any]]) -> None:
    """Save a dynamic workflow as YAML for future reuse."""
    import os
    from pathlib import Path

    import yaml

    name_error = _validate_template_name(name)
    if name_error:
        raise ValueError(name_error)

    workflows_dir = Path("~/.vmware/workflows").expanduser()
    workflows_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(workflows_dir, 0o700)
    except OSError:
        pass

    spec = {
        "name": name,
        "description": description,
        "steps": [
            {k: v for k, v in s.items() if v}  # strip empty values
            for s in steps
        ],
    }

    path = workflows_dir / f"{name}.yaml"
    with open(path, "w") as fh:
        yaml.dump(spec, fh, default_flow_style=False, allow_unicode=True)

    logger.info("Saved custom workflow template: %s", path)
