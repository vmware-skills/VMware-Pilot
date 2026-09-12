"""Dynamic workflow authoring tools: create + draft design/update/confirm."""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any, Optional

from vmware_policy import vmware_tool

from vmware_pilot.approval_gate import gate_violations, refusal
from vmware_pilot.mcp_server._catalog import SKILL_CATALOG
from vmware_pilot.mcp_server._shared import (
    _get_store,
    _safe_error,
    _save_as_yaml,
    _validate_template_name,
    mcp,
)

logger = logging.getLogger(__name__)


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
@vmware_tool(risk_level="medium")
def create_workflow(
    name: str,
    description: str,
    steps: list[dict[str, Any]],
    save_as_template: bool = False,
) -> dict:
    """[WRITE] Create a custom workflow dynamically from a step list.

    Use this when you already know the steps and no built-in template matches;
    prefer plan_workflow when one does, and design_workflow when the user gave
    a goal rather than steps. Call get_skill_catalog first for the skill and
    tool names a step may target.

    Each step dict must have: action, skill, tool, params. Optional:
    rollback_tool, rollback_params. action="require_approval" (skill "pilot",
    tool "approve") inserts a human approval gate. A workflow whose
    destructive or unclassifiable step has no gate before it is REFUSED —
    nothing is saved — and the error names the step and the gate to insert.

    Args:
        name: Workflow name (used as workflow_type).
        description: Human-readable description.
        steps: List of step dicts, each with action/skill/tool/params.
        save_as_template: If True, save as YAML to ~/.vmware/workflows/ for reuse.
            Refused when name is a built-in template's name, since the saved
            template would replace the built-in.

    Returns:
        dict with workflow_id and plan summary. Next call review_workflow to
        check the plan, then run_workflow to execute. Nothing is undone
        automatically on failure; see rollback.
    """
    from datetime import datetime, timezone

    from vmware_pilot.models import Workflow, WorkflowState, WorkflowStep, new_workflow_id

    try:
        # Validate BEFORE persisting anything: an invalid template name must
        # not leave a half-created workflow behind.
        if save_as_template:
            name_error = _validate_template_name(name)
            if name_error:
                return {
                    "error": name_error,
                    "hint": (
                        "Choose a simple name of your own, like 'restart_db_replica' — "
                        "not a built-in template name (list_workflows shows them)."
                    ),
                }

        now = datetime.now(tz=timezone.utc).isoformat()
        wf_steps = []
        for i, s in enumerate(steps):
            wf_steps.append(
                WorkflowStep(
                    index=i,
                    action=s.get("action", f"step_{i}"),
                    skill=s.get("skill", "unknown"),
                    tool=s.get("tool", "unknown"),
                    params=s.get("params", {}),
                    rollback_tool=s.get("rollback_tool", ""),
                    rollback_params=s.get("rollback_params", {}),
                )
            )

        wf = Workflow(
            id=new_workflow_id(),
            workflow_type=name,
            state=WorkflowState.PENDING,
            steps=wf_steps,
            params={"description": description, "custom": True},
            created_at=now,
            updated_at=now,
        )
        # Refuse before anything is persisted — no DB row, no YAML template.
        violations = gate_violations(wf)
        if violations:
            return refusal(
                name,
                violations,
                verb="save",
                retry="call create_workflow again with the corrected steps",
            )
        _get_store().save(wf)

        # Optionally save as YAML template for reuse
        if save_as_template:
            _save_as_yaml(name, description, steps)

        return {
            "workflow_id": wf.id,
            "workflow_type": name,
            "state": wf.state.value,
            "steps": [
                {"index": s.index, "action": s.action, "skill": s.skill, "tool": s.tool}
                for s in wf.steps
            ],
            "saved_as_template": save_as_template,
            "message": f"Custom workflow created. Call run_workflow('{wf.id}') to execute.",
        }
    except Exception as e:
        return {
            "error": _safe_error(e, "create_workflow"),
            "hint": "Every step needs action, skill, tool and params. Run "
            "get_skill_catalog to confirm the skill and tool names are ones "
            "pilot can drive, then call create_workflow again.",
        }


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
@vmware_tool(risk_level="low")
def design_workflow(
    goal: str,
    constraints: str = "",
) -> dict:
    """[WRITE] Start designing a workflow from a natural language description.

    Call this when the user describes a complex operation and you need to
    design a multi-step workflow. Returns a DRAFT workflow with proposed steps
    for the user to review and edit before execution.

    Design flow: design_workflow → update_draft (add steps, then iterate on
    user feedback) → confirm_draft (state becomes PENDING) → run_workflow.

    Use get_skill_catalog() first to see which tools the steps may target.

    Args:
        goal: Natural language description of what the user wants to accomplish.
        constraints: Optional constraints (e.g. "must have approval before any destructive step",
                     "use NSX for networking", "target is vcenter-prod").

    Returns:
        dict with workflow_id (state=DRAFT), proposed steps placeholder,
        and instructions for the AI to fill in steps via update_draft.
    """
    from datetime import datetime, timezone

    from vmware_pilot.models import Workflow, WorkflowState, new_workflow_id

    now = datetime.now(tz=timezone.utc).isoformat()
    wf = Workflow(
        id=new_workflow_id(),
        workflow_type="custom_draft",
        state=WorkflowState.DRAFT,
        steps=[],
        params={"goal": goal, "constraints": constraints, "custom": True},
        created_at=now,
        updated_at=now,
    )
    wf.log("draft_created", f"Goal: {goal}")
    _get_store().save(wf)

    return {
        "workflow_id": wf.id,
        "state": "draft",
        "goal": goal,
        "constraints": constraints,
        "available_skills": list(SKILL_CATALOG.keys()),
        "next_step": (
            "Now design the workflow steps. Use get_skill_catalog() to see available tools, "
            "then call update_draft() to add steps. When done, call confirm_draft() to finalize."
        ),
    }


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
@vmware_tool(risk_level="medium")
def update_draft(
    workflow_id: str,
    name: str = "",
    description: str = "",
    steps: Optional[list[dict[str, Any]]] = None,  # noqa: UP045 — Optional, not PEP 604, in reflected MCP signature (踩坑 #33)
) -> dict:
    """[WRITE] Update a DRAFT workflow's name, description, or steps.

    Call this after design_workflow() to fill in the actual steps,
    or to modify steps based on user feedback. Use it only while the workflow
    is still DRAFT — after confirm_draft the steps are frozen and you must
    create_workflow a new one instead.

    Each step dict: {action, skill, tool, params, rollback_tool?, rollback_params?}
    Use action="require_approval" for approval gates. A draft may be saved
    without them while it is being designed; the result lists every step
    that still needs one, and confirm_draft refuses until none are left.

    Args:
        workflow_id: The draft workflow ID.
        name: Workflow name (optional, updates workflow_type).
        description: Human-readable description.
        steps: Complete list of steps (replaces all existing steps).

    Returns:
        Updated workflow summary for user review.
    """
    from vmware_pilot.models import WorkflowState, WorkflowStep

    wf = _get_store().load(workflow_id)
    if not wf:
        return {"error": f"Workflow '{workflow_id}' not found"}
    if wf.state != WorkflowState.DRAFT:
        return {"error": f"Workflow '{workflow_id}' is not a draft (state: {wf.state.value})"}

    if name:
        wf.workflow_type = name
    if description:
        wf.params["description"] = description

    if steps is not None:
        wf.steps = [
            WorkflowStep(
                index=i,
                action=s.get("action", f"step_{i}"),
                skill=s.get("skill", "unknown"),
                tool=s.get("tool", "unknown"),
                params=s.get("params", {}),
                rollback_tool=s.get("rollback_tool", ""),
                rollback_params=s.get("rollback_params", {}),
            )
            for i, s in enumerate(steps)
        ]
        wf.log("steps_updated", f"{len(steps)} steps")

    _get_store().save(wf)

    violations = gate_violations(wf)
    message = "Draft updated. Show to user for review. Call confirm_draft() when approved."
    if violations:
        first = violations[0]
        message = (
            f"Draft updated, but confirm_draft will refuse it: {len(violations)} step(s) "
            "that could change the estate have no require_approval gate before them "
            f"(first: step {first.step_index}, {first.skill}.{first.tool}). Add "
            '{"action": "require_approval", "skill": "pilot", "tool": "approve", '
            f'"params": {{"message": "..."}}}} before step {first.step_index}, then '
            "update_draft again."
        )

    return {
        "workflow_id": wf.id,
        "workflow_type": wf.workflow_type,
        "state": "draft",
        "steps": [
            {
                "index": s.index,
                "action": s.action,
                "skill": s.skill,
                "tool": s.tool,
                "has_rollback": bool(s.rollback_tool),
            }
            for s in wf.steps
        ],
        "approval_gate_violations": [asdict(v) for v in violations],
        "message": message,
    }


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
@vmware_tool(risk_level="medium")
def confirm_draft(
    workflow_id: str,
    save_as_template: bool = False,
) -> dict:
    """[WRITE] Confirm a draft workflow — changes state from DRAFT to PENDING.

    Use this once the user has approved the draft's steps; call update_draft
    instead if anything still needs changing. After confirmation, the workflow
    can be executed via run_workflow(). Optionally saves as a YAML template
    for future reuse. Refused — the draft stays a draft and nothing is saved —
    while any destructive or unclassifiable step lacks a require_approval
    gate before it.

    Args:
        workflow_id: The draft workflow ID to confirm.
        save_as_template: If True, save to ~/.vmware/workflows/ for reuse.
            Refused when the draft's name is a built-in template's name.

    Returns:
        Confirmed workflow summary. Call run_workflow() to execute.
    """
    from vmware_pilot.models import WorkflowState

    try:
        wf = _get_store().load(workflow_id)
        if not wf:
            return {"error": f"Workflow '{workflow_id}' not found"}
        if wf.state != WorkflowState.DRAFT:
            return {"error": f"Workflow '{workflow_id}' is not a draft (state: {wf.state.value})"}
        if not wf.steps:
            return {"error": "Cannot confirm a draft with no steps. Call update_draft() first."}

        violations = gate_violations(wf)
        if violations:
            return refusal(
                wf.workflow_type,
                violations,
                verb="confirm",
                retry=f"update_draft('{wf.id}', steps=[...]) and confirm_draft again",
            )

        # Validate the template name BEFORE flipping state to PENDING, so an
        # invalid name cannot leave a confirmed-but-unsaved-template state.
        will_save_template = save_as_template and wf.workflow_type != "custom_draft"
        if will_save_template:
            name_error = _validate_template_name(wf.workflow_type)
            if name_error:
                return {
                    "error": name_error,
                    "hint": "Rename the draft via update_draft(name=...) first.",
                }

        wf.state = WorkflowState.PENDING
        wf.log("draft_confirmed", f"Confirmed with {len(wf.steps)} steps")
        _get_store().save(wf)

        if will_save_template:
            steps_for_yaml = [
                {
                    "action": s.action,
                    "skill": s.skill,
                    "tool": s.tool,
                    "params": s.params,
                    **(
                        {"rollback_tool": s.rollback_tool, "rollback_params": s.rollback_params}
                        if s.rollback_tool
                        else {}
                    ),
                }
                for s in wf.steps
            ]
            _save_as_yaml(wf.workflow_type, wf.params.get("description", ""), steps_for_yaml)

        return {
            "workflow_id": wf.id,
            "workflow_type": wf.workflow_type,
            "state": "pending",
            "steps_count": len(wf.steps),
            "saved_as_template": save_as_template,
            "message": f"Workflow confirmed. Call run_workflow('{wf.id}') to execute.",
        }
    except Exception as e:
        return {
            "error": _safe_error(e, "confirm_draft"),
            "hint": f"Failed to confirm draft '{workflow_id}'. "
            f"Use get_workflow_status() to inspect it.",
        }
