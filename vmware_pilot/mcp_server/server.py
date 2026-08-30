"""MCP server for VMware Pilot — workflow orchestration.

Thin entrypoint. The shared FastMCP instance and helpers live in
``vmware_pilot.mcp_server._shared``; the 13 tools are defined across ``vmware_pilot.mcp_server.tools``
(lifecycle / query / authoring) and register themselves on import. The
store/executor singletons + accessors live here because the test-suite patches
``server._store`` / ``server._executor``; ``_shared`` defers to these accessors
so those patches are honoured.

Exposes 13 tools for AI agents to manage multi-step VMware workflows:
  plan_workflow / run_workflow / approve / rollback / cancel_workflow
  review_workflow / get_workflow_status / list_workflows / get_skill_catalog
  create_workflow / design_workflow / update_draft / confirm_draft
"""

from __future__ import annotations

import logging
from typing import Optional

from vmware_policy import describe_tool_parameters, set_environment_resolver

from vmware_pilot.mcp_server._catalog import SKILL_CATALOG
from vmware_pilot.mcp_server._shared import _save_as_yaml, _validate_template_name, mcp
from vmware_pilot.executor import WorkflowExecutor
from vmware_pilot.models import WorkflowStore

logger = logging.getLogger(__name__)

_store: WorkflowStore | None = None
_executor: WorkflowExecutor | None = None


def _get_store() -> WorkflowStore:
    global _store
    if _store is None:
        _store = WorkflowStore()
    return _store


def _get_executor() -> WorkflowExecutor:
    global _executor
    if _executor is None:
        _executor = WorkflowExecutor(_get_store())
    return _executor


# Importing the tool modules registers every @mcp.tool onto ``mcp`` and gives
# this module the tool functions to re-export (test-suite calls server.<tool>).
from vmware_pilot.mcp_server.tools.authoring import (  # noqa: E402
    confirm_draft,
    create_workflow,
    design_workflow,
    update_draft,
)
from vmware_pilot.mcp_server.tools.lifecycle import (  # noqa: E402
    approve,
    cancel_workflow,
    plan_workflow,
    rollback,
    run_workflow,
)
from vmware_pilot.mcp_server.tools.query import (  # noqa: E402
    get_skill_catalog,
    get_workflow_status,
    list_workflows,
    review_workflow,
)


# ---------------------------------------------------------------------------
# Environment declaration
# ---------------------------------------------------------------------------

#: What this skill reports as the environment of everything it touches.
#:
#: Policy rules scope by environment, and the baseline treats a target that
#: declares none as unknown — today that warns on state-changing operations,
#: and the next major release refuses them. Every other skill answers this from
#: its own config, where an operator labels each target ``production`` /
#: ``staging`` / ``lab``.
#:
#: vmware-pilot has no such config and no connection of its own — deliberately.
#: It orchestrates other skills, and its own writes (plan / approve / rollback /
#: cancel / the authoring tools) all land in the local workflow DB
#: (``~/.vmware/workflows.db``), never on a VMware estate. The executor does not
#: call VMware APIs; with no dispatch function configured — which is the case
#: for this MCP server, see ``vmware_pilot.executor`` — executable steps are
#: recorded as ``not_executed`` and the run reports ``dispatch_required``.
#:
#: So the approval gate on the real infrastructure change is not skipped, it
#: happens downstream: the calling agent performs each step through the target
#: skill's own MCP tool, in that skill's process, where that skill's resolver
#: reports the target's declared environment and the gate applies. Requiring
#: pilot to declare an environment it has no basis to know would block workflow
#: authoring permanently while protecting nothing.
#:
#: CAVEAT for embedders: this resolver is process-global in vmware_policy, not
#: per-server. An embedder that constructs ``WorkflowExecutor`` with a real
#: dispatch callable that invokes sibling skills *in this same process* would
#: have those skills' writes resolve to ``local`` instead of the target's real
#: declared environment — silently unscoping exactly the rules this mechanism
#: exists to enforce. Such an embedder must re-register the skill's own resolver
#: around the dispatched call. The shipped stdio server never dispatches, so
#: this cannot arise from normal use.
LOCAL_ENVIRONMENT = "local"


def _environment_for(target: Optional[str]) -> str:
    """Report the environment for policy scoping. Always ``local`` — see above."""
    return LOCAL_ENVIRONMENT


set_environment_resolver(_environment_for)

__all__ = [
    "mcp",
    "main",
    "SKILL_CATALOG",
    "_get_store",
    "_get_executor",
    "_validate_template_name",
    "_save_as_yaml",
    "plan_workflow",
    "run_workflow",
    "approve",
    "rollback",
    "cancel_workflow",
    "review_workflow",
    "get_workflow_status",
    "list_workflows",
    "get_skill_catalog",
    "create_workflow",
    "design_workflow",
    "update_draft",
    "confirm_draft",
]


def main() -> None:
    """Run the MCP server over stdio."""
    logging.basicConfig(level=logging.INFO)
    mcp.run(transport="stdio")

# The docstrings above are the schema. `describe_tool_parameters` copies each
# `Args:` entry into the JSON schema an agent actually reads, and closes the
# object. Without it every parameter reaches the model as a bare name and a
# type, which is how a wrong guess becomes an unfiltered result or a silent
# zero-row answer instead of an error (real-hardware round, 2026-08-30).
_DESCRIBED_PARAMS = describe_tool_parameters(mcp._tool_manager._tools)
