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

from vmware_policy import describe_tool_parameters

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
# Environment declaration — deliberately absent
# ---------------------------------------------------------------------------
#
# This module registers no ``vmware_policy`` environment resolver. That is the
# fix for a real DENY → ALLOW regression, not an oversight, so here is why in
# full.
#
# ``set_environment_resolver`` writes to a single process-global slot shared by
# every skill in the interpreter. This module used to call it at *import* time
# with a resolver that answered ``"local"`` for every target it was asked about.
# In an MCP host that loads several of these skills into one process — the
# normal deployment — importing vmware-pilot therefore replaced whatever
# resolver a sibling had installed, and every one of that sibling's targets
# started resolving to ``"local"``. A rule scoped to
# ``environments: [production]`` stops matching, so it stops denying. The
# real-hardware round measured exactly that, and nothing surfaced it: a deny
# rule that under-matches is silent by construction.
#
# Two things were wrong and both had to go.
#
# The *timing*: registering a process-global at import means a bare ``import``
# — the thing a host does merely to enumerate a package — mutates enforcement
# for code that has nothing to do with this skill.
#
# The *answer*: a resolver is asked "what environment is target X in?" and this
# one replied ``"local"`` for every X. For X = ``prod-vc01`` that is not a
# conservative default, it is false, and it is precisely the value that makes
# production-scoped rules stop matching. Moving the same constant into
# ``main()`` would only narrow who gets lied to.
#
# So the registration is gone rather than relocated. vmware-pilot has no target
# config and no connection of its own — deliberately: it orchestrates other
# skills, and its own writes (plan / approve / rollback / cancel / the authoring
# tools) all land in the local workflow DB at ``~/.vmware/workflows.db``, never
# on a VMware estate. It therefore has no basis to answer the question for any
# target, and the honest thing is to leave the slot alone and let
# ``resolve_environment`` apply its documented default: unlabeled (``""``),
# which matches no environment-scoped rule and is never refused for lack of a
# label (HLD §6, D-3).
#
# Nothing is lost by declining. The approval gate on the real infrastructure
# change happens downstream anyway: the calling agent performs each step through
# the target skill's own MCP tool, in that skill's process, where that skill's
# resolver reports the target's declared environment and the gate applies.
#
# Declining is a fix for this skill, not for the hazard. One global slot with
# last-writer-wins semantics still means any skill that registers honestly can
# be silently displaced by the next one imported, and ``vmware_policy`` only
# logs a warning when that happens — a warning is not a control. The repair
# belongs there, and the shape it should take is: key the resolver by the
# registering skill so each skill's targets are resolved by its own lookup, and
# make a second registration for a skill that already has one an error rather
# than an overwrite. Fixing it here is not possible; every skill would have to
# agree, and the one that forgets is the one that breaks the others.

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
