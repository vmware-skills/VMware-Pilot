"""Read-only query/discovery tools: review, status, list, skill catalog."""

from __future__ import annotations

from vmware_policy import vmware_tool

from vmware_pilot.mcp_server._catalog import SKILL_CATALOG
from vmware_pilot.mcp_server._shared import _get_store, _safe_error, mcp
from vmware_pilot.review import review as _review_workflow_impl


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
@vmware_tool(risk_level="low")
def review_workflow(workflow_id: str) -> dict:
    """[READ] Sanity-check a planned workflow before execution.

    Performs structural validation only — does NOT call into other skills.
    Catches the common authoring errors before they hit production:

      - Delete-then-use: a step deletes resource X, a later step references X
      - Missing required params: a step has empty params or placeholder values
      - Cross-skill order issues: surfacing the cross-skill dispatch sequence
      - Risk profile: count of destructive / write / read-only / unclassified steps
      - Approval coverage: is every destructive OR unclassifiable step gated
        behind a preceding require_approval?

    Each step is placed in a tier from the tool's entry in ``get_skill_catalog``
    first, then from name patterns. A step that matches neither is reported as
    ``ungated_unclassified`` rather than assumed safe: pilot dispatches nothing
    itself and cannot inspect a sibling skill's annotations, so "this tool is
    unknown to me" is the honest finding, and it needs the same gate a known
    destructive step does. The remedy is in the message — add the tool to
    SKILL_CATALOG with the risk its own skill declares, or add a gate.

    Medium-risk writes (create / scale / enable) are classified and counted but
    not gated: the family gates destructive work, and several built-in templates
    deliberately create in staging *before* asking for approval.

    Args:
        workflow_id: The workflow ID returned by ``plan_workflow``.

    Returns:
        Dict with keys:
          - ``verdict``: ``"approved"`` if no structural issues, otherwise ``"needs_revision"``
          - ``findings``: list of {severity, kind, message, step_index}. Kinds
            ``ungated_destructive``, ``ungated_unclassified`` and
            ``destructive_in_parallel_group`` are what ``run_workflow`` refuses on;
            ``force=True`` overrides that only for a built-in template, never
            for a custom workflow's missing approval gate.
          - ``summary``: counts — total/destructive/write/read_only/approval_gates,
            parallel_groups, est_duration_min, plus ``classified_steps`` and
            ``unclassified_steps`` so an "approved" verdict can be told apart from
            a workflow this review could not read.
    """
    try:
        wf = _get_store().load(workflow_id)
        if not wf:
            return {"error": f"Workflow '{workflow_id}' not found"}
        return _review_workflow_impl(wf)
    except Exception as e:
        return {
            "error": _safe_error(e, "review_workflow"),
            "hint": f"Review failed for '{workflow_id}'. "
            f"Use get_workflow_status() to inspect raw state.",
        }


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
@vmware_tool(risk_level="low")
def get_workflow_status(workflow_id: str) -> dict:
    """[READ] Get current workflow state, diff report, and audit log.

    Use this to poll a workflow after run_workflow and to find out why one
    stopped: outcome='dispatch_required' means you must perform the pending
    steps yourself, 'awaiting_approval' means call approve. Returns a
    point-in-time snapshot and does not advance the workflow.

    Args:
        workflow_id: The workflow ID to query.

    Returns:
        Full workflow state including steps, audit log, and diff report.
    """
    try:
        wf = _get_store().load(workflow_id)
        if not wf:
            return {"error": f"Workflow '{workflow_id}' not found"}
        return wf.to_dict()
    except Exception as e:
        return {
            "error": _safe_error(e, "get_workflow_status"),
            "hint": "Use list_workflows() to see active workflow IDs.",
        }


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
@vmware_tool(risk_level="low")
def list_workflows() -> dict:
    """[READ] List all available workflow templates (built-in + custom).

    Use this first to see whether a template already covers the goal, then
    pass its name to plan_workflow; if none fit, use create_workflow instead.
    Built-in templates are always available. Custom templates are loaded
    from ~/.vmware/workflows/*.yaml — drop a YAML file there to add
    your own workflows.

    Returns:
        dict with builtin and custom workflow lists, each with name,
        description, steps count, plus active_workflows — the IDs of
        in-flight runs to pass to get_workflow_status.
    """
    try:
        from vmware_pilot.custom_loader import list_custom_workflows
        from vmware_pilot.templates import BUILTIN_TEMPLATES

        builtin = [
            {
                "name": name,
                "type": "builtin",
                "description": (fn.__doc__ or "").split("\n")[0].strip(),
            }
            for name, fn in BUILTIN_TEMPLATES.items()
        ]
        custom = [{**c, "type": "custom"} for c in list_custom_workflows()]

        active = _get_store().list_active()

        return {
            "templates": builtin + custom,
            "active_workflows": active,
        }
    except Exception as e:
        return {
            "error": _safe_error(e, "list_workflows"),
            # Not "check the file named in 'error'": no file name can ever
            # appear there. list_custom_workflows() swallows a malformed YAML
            # per-file and returns the rest, so a bad template cannot fail this
            # call — it just goes missing. Anything that does raise here is
            # reduced to its class name by _safe_error unless it is a
            # ValueError. The old hint sent the caller looking for a filename
            # that is structurally impossible to produce.
            "hint": "Not a malformed custom template: unreadable YAML in "
            "~/.vmware/workflows/ is skipped silently, so a bad file goes "
            "missing from 'templates' rather than failing this call — that is "
            "also why a workflow you expected can be absent from the list. "
            "This failure comes from the workflow store or the built-in "
            "template registry; its traceback is in the MCP server log. "
            "Built-in templates are unaffected, so plan_workflow with a "
            "built-in name still works.",
        }


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
@vmware_tool(risk_level="low")
def get_skill_catalog() -> dict:
    """[READ] Get the complete catalog of available skills and tools for workflow design.

    Use this to understand what building blocks are available when designing
    a custom workflow, then feed the skill and tool names into create_workflow
    or update_draft steps. Note this is a static curated catalog, not a live
    query of each skill's registry, so it may lag a skill's actual tool list;
    pilot cannot call these tools itself — the calling agent does.

    Returns:
        dict mapping skill name → {description, tools: {tool_name: {risk, desc}}}.
    """
    return SKILL_CATALOG
