"""Workflow lifecycle tools: plan, run, approve, rollback, cancel."""

from __future__ import annotations

import inspect
import logging
from typing import Any

from vmware_policy import vmware_tool

from vmware_pilot.approval_gate import (
    GATE_FINDING_KINDS,
    ApprovalGateError,
    gate_violations,
    is_custom_workflow,
    refusal,
)
from vmware_pilot.lifecycle_gate import (
    cancel_blast_radius,
    load_for_gate,
    rollback_blast_radius,
)
from vmware_pilot.lifecycle_gate import (
    refusal as gate_refusal,
)
from vmware_pilot.mcp_server._shared import _get_executor, _get_store, _safe_error, mcp
from vmware_pilot.models import WorkflowState
from vmware_pilot.review import review as _review_workflow_impl
from vmware_pilot.templates import get_all_templates

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
def plan_workflow(
    workflow_type: str,
    params: dict[str, Any],
) -> dict:
    """[WRITE] Create an execution plan for a multi-step workflow.

    Use this when the goal matches one of the built-in types below; use
    create_workflow instead when none of them fit. A custom YAML template whose
    destructive or unclassifiable step (or step passing confirm=True) has no
    require_approval gate before it is refused, naming the step and the file to fix.

    Available workflow types:
      - clone_and_test: Clone VM → apply changes → monitor → approve → commit
      - incident_response: Diagnose alert → collect info → approve → remediate
      - plan_and_approve: Wrap aiops batch operations with approval gate
      - compliance_scan: Read-only health/capacity/anomaly check (no approval)

    Args:
        workflow_type: One of the available workflow types.
        params: Workflow-specific parameters.
            clone_and_test: target_vm (str), change_spec (dict), monitor_minutes (int),
                target (str). change_spec is a resize ({"cpu": 4} and/or
                {"memory_mb": 32768}; memory_gb is converted) or one guest command
                ({"command": "/usr/bin/apt-get", "arguments": "...", "username":
                "<guest account>", "password": "..."}) — username is required,
                there is no root default.
            incident_response: alert_entity (str), alert_name (str), target (str).
            plan_and_approve: operations (list[dict]), target (str), description (str).
            compliance_scan: target (str), check_alarms (bool), check_capacity (bool).

    Returns:
        dict with workflow_id, steps summary, and plan details.
    """
    try:
        templates = get_all_templates()
        template_fn = templates.get(workflow_type)
        if not template_fn:
            return {
                "error": f"Unknown workflow type: {workflow_type}. "
                f"Available: {list(templates.keys())}"
            }

        # Check the params against the template's own signature first: a missing
        # or misspelt param is otherwise a TypeError, which _safe_error reduces
        # to its class name. The text here names only our own parameters.
        signature = inspect.signature(template_fn)
        try:
            signature.bind(**params)
        except TypeError as e:
            return {
                "error": f"plan_workflow('{workflow_type}'): {e}.",
                "hint": f"{workflow_type} takes {signature}. Supply the missing or "
                "misspelt params and call plan_workflow again.",
            }

        wf = template_fn(**params)  # custom YAML loaders raise ApprovalGateError
        _get_store().save(wf)

        return {
            "workflow_id": wf.id,
            "workflow_type": wf.workflow_type,
            "state": wf.state.value,
            "steps": [
                {"index": s.index, "action": s.action, "skill": s.skill, "tool": s.tool}
                for s in wf.steps
            ],
            "params": wf.params,
            "message": f"Plan created. Call run_workflow('{wf.id}') to execute.",
        }
    except ApprovalGateError as e:
        # Returned whole: the refusal names the file and the step to insert,
        # and _safe_error would cut the remedy off at 500 characters.
        return e.payload
    except Exception as e:
        return {
            "error": _safe_error(e, "plan_workflow"),
            "hint": "Check workflow_type and params. "
            "Use list_workflows() to see available templates.",
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
def run_workflow(workflow_id: str, force: bool = False) -> dict:
    """[WRITE] Advance a planned workflow. Pauses at approval gates.

    IMPORTANT — this MCP server has no dispatcher and cannot call other
    skills' MCP tools itself. Steps are recorded as 'not_executed' and the
    run finishes with outcome='dispatch_required' (NOT 'completed'),
    returning each pending step's skill/tool/params. YOU (the calling
    agent) must then perform those skill/tool calls in order. A workflow
    only reaches 'completed' when every step genuinely executed via a
    real dispatcher (embedders supplying one to WorkflowExecutor).

    Safety: the workflow is structurally reviewed before each run. Runs
    are REFUSED if review finds ungated destructive or unclassifiable steps,
    or destructive steps inside a parallel group. For a built-in template,
    force=True overrides that (forced runs are written to the workflow audit
    log). For a custom workflow (create_workflow, design_workflow, or a YAML
    template) an ungated step is refused even with force=True: add a
    require_approval step before it instead.

    When an approval gate is reached, the workflow pauses with state
    'awaiting_approval'. Call approve() to continue.

    Args:
        workflow_id: The workflow ID from plan_workflow.
        force: Bypass blocking review findings on a built-in template. Use
            only with explicit human consent; the bypass is audited. Has no
            effect on a custom workflow's missing approval gate.

    Returns:
        Current workflow state with 'outcome' (completed | awaiting_approval
        | dispatch_required | failed) and, when dispatch is required, a
        'pending_dispatch' list of steps for the agent to perform.
    """
    wf = _get_store().load(workflow_id)
    if not wf:
        return {"error": f"Workflow '{workflow_id}' not found"}

    if wf.state == WorkflowState.CANCELLED:
        return {
            "error": (
                f"Workflow '{workflow_id}' is CANCELLED and cannot be run "
                "(its approval was rejected or it was explicitly cancelled). "
                "Cancelled workflows are terminal — create a new plan instead."
            ),
            "state": wf.state.value,
        }
    if wf.state not in (WorkflowState.PENDING, WorkflowState.RUNNING):
        return {"error": f"Workflow '{workflow_id}' cannot be run (state: {wf.state.value})"}

    try:
        # ── Approval-gate enforcement (not just advisory) ─────────────
        review_result = _review_workflow_impl(wf)

        # ── Custom workflows: a missing gate is not overridable ──────
        # A built-in was reviewed by whoever wrote it; a custom workflow was
        # not, and the fix for its missing gate is one inserted step — which
        # is itself the human consent force=True would stand in for.
        gate_findings = [
            f for f in review_result.get("findings", []) if f.get("kind") in GATE_FINDING_KINDS
        ]
        # gate_violations also holds confirm_without_approval, which review()
        # does not report — a workflow stored by an earlier version can carry it.
        violations = gate_violations(wf) if is_custom_workflow(wf) else ()
        if violations:
            payload = refusal(
                wf.workflow_type,
                violations,
                verb="run",
                retry=(
                    "cancel_workflow this one and create_workflow the corrected steps "
                    "(a confirmed workflow's steps are frozen)"
                ),
                extra=(
                    "force=True does not override this for a custom workflow: the "
                    "approval gate is the human consent force would stand in for."
                ),
            )
            # Same shape as the built-in refusal below, so a caller reads one.
            return {
                **payload,
                "blocking_findings": gate_findings,
                "hint": (
                    "Add a require_approval step (skill 'pilot', tool 'approve') before "
                    f"step {payload['fix']['insert_before_step']}; force=True cannot "
                    "stand in for it on a custom workflow."
                ),
            }

        blocking = [
            f
            for f in review_result.get("findings", [])
            # ungated_unclassified is blocking for the same reason the other two
            # are: pilot is about to dispatch a step it cannot vouch for, and the
            # only safe reading of "I do not know what this tool does" is that a
            # human should. force=True still overrides, and is audited.
            if f.get("kind")
            in ("ungated_destructive", "ungated_unclassified", "destructive_in_parallel_group")
        ]
        if blocking:
            if not force:
                return {
                    "error": (
                        f"Refusing to run workflow '{workflow_id}': review found "
                        f"{len(blocking)} blocking safety finding(s) — destructive "
                        "or unclassifiable steps without a preceding "
                        "require_approval gate, and/or destructive steps inside a "
                        "parallel group."
                    ),
                    "blocking_findings": blocking,
                    "hint": (
                        "Fix the workflow (add a require_approval step before "
                        "destructive operations, or remove them from parallel "
                        "groups) via update_draft/create_workflow, or re-run "
                        "with force=True after explicit human confirmation "
                        "(forced runs are audited)."
                    ),
                }
            wf.log(
                "forced_run",
                f"force=True bypassed {len(blocking)} blocking review finding(s): "
                + ", ".join(f"{f['kind']}@step{f['step_index']}" for f in blocking),
            )
            logger.warning(
                "Workflow %s forced past %d blocking review findings",
                workflow_id,
                len(blocking),
            )
            _get_store().save(wf)

        return _get_executor().run_until_checkpoint(wf)
    except Exception as e:
        return {
            "error": _safe_error(e, "run_workflow"),
            "hint": f"Workflow '{workflow_id}' execution failed. "
            f"Use get_workflow_status() to check state, or rollback().",
        }


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
@vmware_tool(risk_level="high")
def approve(workflow_id: str, approver: str = "") -> dict:
    """[WRITE] Approve a workflow that is waiting for human confirmation.

    Use this only after showing the user the pending step and getting explicit
    human consent; use cancel_workflow instead when the approval is rejected.
    Only works when workflow state is 'awaiting_approval'.
    After approval, execution continues to the next steps.

    Args:
        workflow_id: The workflow ID to approve.
        approver: Name of the person approving (for audit trail).

    Note: this server has no dispatcher — after approval, remaining steps
    are recorded as 'not_executed' and the result carries
    outcome='dispatch_required' with a 'pending_dispatch' list for the
    calling agent to perform (see run_workflow).

    Returns:
        Updated workflow state after resuming.
    """
    try:
        wf = _get_store().load(workflow_id)
        if not wf:
            return {"error": f"Workflow '{workflow_id}' not found"}

        # Non-empty approver enforcement lives in the executor so embedders
        # cannot bypass the audit-trail requirement.
        return _get_executor().resume_after_approval(wf, approver=approver)
    except Exception as e:
        return {
            "error": _safe_error(e, "approve"),
            "hint": f"Approval failed for '{workflow_id}'. "
            f"Use get_workflow_status() to check state.",
        }


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
@vmware_tool(risk_level="high")
def rollback(
    workflow_id: str, confirm: bool = False, acknowledge_unknown_effects: bool = False
) -> dict:
    """[WRITE] Abort a workflow and rollback completed steps in reverse order.

    Without confirm=True this only previews: it returns blast_radius — the
    workflow's id, type and state, the executed steps whose rollback_tool would
    run with its rollback_params, secrets redacted (would_roll_back — these
    often carry confirm=True, and this call's confirm=True is the decision to
    run them), the executed steps that have none and stay applied
    (left_in_place), steps you performed from pending_dispatch that Pilot will
    not reverse (not_reversed_by_pilot), steps with unknown effects, and
    blockers — and changes nothing. Show that to the user and get their
    explicit decision. Do not set confirm=True on your own because the user
    asked for a rollback earlier — the user has not seen the preview yet.

    Nothing rolls back automatically: a failed step leaves the workflow
    'failed' and stops. This tool is the explicit, best-effort undo, and it
    only reverses steps pilot itself recorded as 'success' — which, on this
    server (no dispatcher), are approval gates only. Steps YOU performed from
    pending_dispatch are still 'not_executed' here, so to undo them call each
    one's rollback_tool with its rollback_params yourself, last step first.
    Embedders that pass a dispatcher to WorkflowExecutor get those calls made
    for them. Steps without a rollback_tool are skipped; a failed undo does not
    stop the rest. The workflow state is set to 'failed' afterwards.

    Refused with confirm=True: a workflow in draft, completed, rolling_back or
    cancelled state, a step whose status Pilot does not recognise, a workflow
    record that cannot be read, and — unless acknowledge_unknown_effects=True —
    a step left 'running' or 'interrupted' by a Pilot process that stopped
    mid-dispatch (listed in unknown_effects).

    Args:
        workflow_id: The workflow ID to rollback.
        confirm: False (default) returns the blast radius and changes nothing. True applies it.
        acknowledge_unknown_effects: Set True only after the user has checked, in
            the target system, whether each unknown_effects step took effect.
            Covers only those steps; every other refusal still applies.

    Returns:
        Preview: {"action": "preview", "blast_radius", "hint"}. Acting: the
        workflow state with "action": "rolled_back", rollback_results for each
        step pilot recorded as succeeded, and blast_radius. Check
        get_workflow_status afterwards to see which steps were actually
        reversed and which were skipped.
    """
    try:
        wf, unreadable = load_for_gate(_get_store(), workflow_id, "rollback")
        if wf is None:
            return unreadable or {"error": f"Workflow '{workflow_id}' not found"}

        radius = rollback_blast_radius(wf, acknowledge_unknown_effects)
        if not confirm:
            return {
                "action": "preview",
                "workflow_id": workflow_id,
                "blast_radius": radius,
                "hint": "Nothing was rolled back. Show blast_radius to the user; to roll "
                "back, re-run with confirm=True after their explicit decision.",
            }
        refused = gate_refusal("roll back", wf, radius)
        if refused:
            return refused

        result = _get_executor().rollback(wf)
        if "error" in result:
            return {**result, "blast_radius": radius}
        return {**result, "action": "rolled_back", "blast_radius": radius}
    except Exception as e:
        return {
            "error": _safe_error(e, "rollback"),
            "hint": f"Rollback failed for '{workflow_id}'. "
            f"Use get_workflow_status() to check state.",
        }


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
@vmware_tool(risk_level="high")
def cancel_workflow(
    workflow_id: str,
    reason: str = "",
    confirm: bool = False,
    acknowledge_unknown_effects: bool = False,
) -> dict:
    """[WRITE] Cancel a workflow — move it to the terminal CANCELLED state.

    Use this when an approval is REJECTED, a review flags the plan as unsafe,
    or an operator decides the workflow must never run. A cancelled workflow
    is dead: run_workflow and approve refuse to execute it. Without this, an
    approval-rejected PENDING workflow could still be picked up and run.

    Without confirm=True this only previews: it returns blast_radius — the
    workflow's id, type and state, the executed steps that stay applied
    (left_in_place: cancelling part-way leaves a half-applied change), the
    steps that would be skipped (would_skip), steps with unknown effects, and
    blockers — and changes nothing. Show that to the user and get their
    explicit decision. Do not set confirm=True on your own because the user
    asked to cancel earlier — the user has not seen the preview yet.

    Cancel only stops FUTURE steps. It does NOT undo already-completed steps —
    use rollback() to reverse those. Refused with confirm=True: an already
    completed/failed/cancelled workflow, a step whose status Pilot does not
    recognise, a workflow record that cannot be read, and — unless
    acknowledge_unknown_effects=True — a step left 'running' or 'interrupted'
    (listed in unknown_effects). The cancellation is written to the workflow
    audit log.

    Args:
        workflow_id: The workflow ID to cancel.
        reason: Optional human-readable reason (e.g. "approval rejected by
            on-call"), recorded in the audit log.
        confirm: False (default) returns the blast radius and changes nothing. True applies it.
        acknowledge_unknown_effects: Set True only after the user has checked, in
            the target system, whether each unknown_effects step took effect.
            Covers only those steps; every other refusal still applies.

    Returns:
        Preview: {"action": "preview", "blast_radius", "hint"}. Acting: the
        workflow state (state='cancelled', outcome='cancelled',
        action='cancelled') with blast_radius, or an error if refused.
    """
    try:
        wf, unreadable = load_for_gate(_get_store(), workflow_id, "cancel_workflow")
        if wf is None:
            return unreadable or {"error": f"Workflow '{workflow_id}' not found"}

        radius = cancel_blast_radius(wf, acknowledge_unknown_effects)
        if not confirm:
            return {
                "action": "preview",
                "workflow_id": workflow_id,
                "blast_radius": radius,
                "hint": "Nothing was cancelled. Show blast_radius to the user (left_in_place "
                "stays applied — rollback reverses what can be reversed); to cancel, re-run "
                "with confirm=True after their explicit decision.",
            }
        refused = gate_refusal("cancel", wf, radius)
        if refused:
            return refused

        result = _get_executor().cancel(wf, reason=reason)
        if "error" in result:
            return {**result, "blast_radius": radius}
        return {**result, "action": "cancelled", "blast_radius": radius}
    except Exception as e:
        return {
            "error": _safe_error(e, "cancel_workflow"),
            "hint": f"Cancel failed for '{workflow_id}'. Use get_workflow_status() to check state.",
        }
