"""Workflow executor — runs workflow steps, handles approval gates and rollback.

The executor does NOT call VMware APIs directly. It delegates to skill MCP tools
via a pluggable dispatch interface. This keeps pilot decoupled from any specific skill.

IMPORTANT — dispatch honesty: when no dispatch function is configured (the
default for the MCP server, which has no way to call sibling skills itself),
the executor does NOT pretend to execute steps. Executable steps are recorded
as ``status="not_executed"`` and the run finishes with
``outcome="dispatch_required"`` (state stays ``pending``), listing each pending
step's skill/tool/params so the calling agent can perform them and never with
state ``completed``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from vmware_pilot.models import (
    REDACTED_PLACEHOLDER,
    Workflow,
    WorkflowState,
    WorkflowStep,
    WorkflowStore,
    is_sensitive_key,
)

_log = logging.getLogger("vmware-pilot.executor")


# Type for the dispatch function: (skill, tool, params) → result
DispatchFn = Callable[[str, str, dict[str, Any]], Any]

# A dispatch that raises is only ONE of the two ways a step fails. Every skill
# in this family reports failure by *returning* {"error": ...} (that is what
# @vmware_tool / _safe_error produce), and an MCP client hands back a result
# object carrying isError=True. Treating "it returned" as "it worked" makes a
# workflow whose every step failed report `completed` — and because rollback
# refuses a completed workflow, the false success also removes the way back.
# Same shape as the Policy v1.8.4 fix (a returned error envelope audited as ok);
# found again here on a real run against a live vCenter, 2026-09-12.


class _StepReturnedFailure(Exception):
    """A step whose dispatch returned a failure instead of raising one."""

    def __init__(self, message: str, payload: Any) -> None:
        super().__init__(message)
        self.message = message
        self.payload = payload if isinstance(payload, dict) else {"error": message}


#: What a step that merely previewed is recorded as. Across the family a
#: destructive tool called without ``confirm=True`` returns
#: ``{"action": "preview", "blast_radius": ...}`` and changes nothing (HLD §7,
#: 2026-09-19). Read as success, a forgotten ``confirm`` would record a change
#: that never happened — and a previewed undo would read as "rolled back".
_PREVIEW_ONLY = (
    "step only previewed; nothing was changed. The tool returned "
    "action='preview' because it was called without confirm=True — the template "
    "must pass confirm=True (Pilot's approve step is the human decision). The "
    "preview's blast_radius is kept in this step's result."
)


def _top_level_payloads(result: Any) -> list[dict[str, Any]]:
    """The dicts a dispatch result may carry at its top level.

    A plain dict, or an MCP ``CallToolResult``'s ``structuredContent`` (and the
    ``{"result": ...}`` wrapper FastMCP puts around non-object returns), or its
    single text block when that block is a JSON object. Never recurses: a
    nested ``action`` is data.
    """
    if isinstance(result, dict):
        return [result]
    payloads: list[dict[str, Any]] = []
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        payloads.append(structured)
        if isinstance(structured.get("result"), dict):
            payloads.append(structured["result"])
    content = getattr(result, "content", None)
    if isinstance(content, list) and len(content) == 1:
        text = getattr(content[0], "text", None)
        if isinstance(text, str) and text.lstrip().startswith("{"):
            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = None
            if isinstance(parsed, dict):
                payloads.append(parsed)
    return payloads


def _returned_failure(result: Any) -> str | None:
    """Return the failure message if this dispatch result reports failure.

    Only a TOP-LEVEL, non-empty ``error`` counts: ``{"error": None}`` is an
    explicit "no error", and an ``error`` nested inside a row (an alarm's
    ``error_count``, say) is data, not a failed call. A TOP-LEVEL
    ``"action": "preview"`` is a failure too: the tool changed nothing.
    """
    if any(p.get("action") == "preview" for p in _top_level_payloads(result)):
        return _PREVIEW_ONLY
    if getattr(result, "isError", False):
        # Unwrap the content block if there is one: the skill's own teaching
        # message is the useful part, not the repr of the transport object.
        content = getattr(result, "content", None)
        if isinstance(content, list) and content and getattr(content[0], "text", None):
            return str(content[0].text)
        return str(content or "the tool reported isError")
    if isinstance(result, dict):
        err = result.get("error")
        if err not in (None, "", [], {}):
            return str(err)
    return None


_NO_DISPATCH_REASON = (
    "No dispatch function is configured — vmware-pilot cannot invoke other "
    "skills' MCP tools itself. The step was recorded but NOT executed. "
    "The calling agent must perform this skill/tool call with the listed "
    "params, or an embedder must construct WorkflowExecutor with a real "
    "dispatch callable."
)

# States from which run_until_checkpoint may legally start.
_RUNNABLE_STATES = (
    WorkflowState.PENDING,
    WorkflowState.RUNNING,
    WorkflowState.AWAITING_APPROVAL,
)

# States from which rollback may legally start.
_ROLLBACK_FORBIDDEN_STATES = (
    WorkflowState.DRAFT,
    WorkflowState.COMPLETED,
    WorkflowState.ROLLING_BACK,
    WorkflowState.CANCELLED,
)

# Terminal states — a workflow here has reached its end and cannot be run or
# cancelled. CANCELLED is the explicit "dead, never run" terminal state added
# for approval-rejected / operator-cancelled workflows.
_TERMINAL_STATES = (
    WorkflowState.COMPLETED,
    WorkflowState.FAILED,
    WorkflowState.CANCELLED,
)


class WorkflowExecutor:
    """Execute workflow steps sequentially, pausing at approval gates."""

    def __init__(self, store: WorkflowStore, dispatch: DispatchFn | None = None) -> None:
        self._store = store
        self._has_dispatch = dispatch is not None
        self._dispatch = dispatch if dispatch is not None else _noop_dispatch

    def run_until_checkpoint(self, wf: Workflow) -> dict[str, Any]:
        """Execute steps until an approval gate or completion.

        Returns the current workflow state as a dict. The dict carries an
        ``outcome`` key: ``completed`` | ``awaiting_approval`` |
        ``dispatch_required`` | ``failed``.
        """
        # ── Cancelled/rejected refusal (terminal, never runs) ─────────
        # A workflow whose approval was rejected (or that an operator
        # cancelled) is CANCELLED and must never be picked up by a run — even
        # though it may still read PENDING in a stale view. Refuse loudly with
        # a teaching error rather than silently executing dead work.
        if wf.state == WorkflowState.CANCELLED:
            return {
                "error": (
                    f"Workflow '{wf.id}' is CANCELLED and cannot be run. Its "
                    "approval was rejected or it was explicitly cancelled "
                    "(cancel_workflow) — a cancelled workflow is terminal. "
                    "Create a new plan if you still need this operation."
                ),
                "workflow_id": wf.id,
                "state": wf.state.value,
            }

        # ── Transition guard: embedders cannot skip gates ─────────────
        if wf.state not in _RUNNABLE_STATES:
            allowed = ", ".join(s.value for s in _RUNNABLE_STATES)
            return {
                "error": (
                    f"Workflow '{wf.id}' cannot be run from state "
                    f"'{wf.state.value}'. Allowed start states: {allowed}. "
                    "Drafts must be confirmed first (confirm_draft); "
                    "completed/failed workflows cannot be re-run — create a "
                    "new plan instead."
                ),
                "workflow_id": wf.id,
                "state": wf.state.value,
            }

        # ── Crash-resume safety: a step stuck in "running" means a previous
        # process died mid-dispatch. Its side effects are UNKNOWN — never
        # skip it as if done, and never blindly re-execute it.
        for step in wf.steps:
            if step.status == "running":
                step.status = "interrupted"
                step.completed_at = _now()
                wf.state = WorkflowState.FAILED
                wf.log(
                    "interrupted_step_detected",
                    f"Step {step.index}: {step.tool} was 'running' when a previous run died",
                )
                self._store.save(wf)
                result = wf.to_dict()
                result["outcome"] = "failed"
                result["error"] = (
                    f"Step {step.index} ({step.skill}.{step.tool}) was "
                    "interrupted mid-execution by a previous crash. Its side "
                    "effects are unknown — verify the target system state "
                    "manually, then either rollback() this workflow (it refuses "
                    "until you pass acknowledge_unknown_effects=True, which says "
                    "you checked) or create a new plan that retries from a "
                    "verified state."
                )
                return result

        wf.state = WorkflowState.RUNNING
        wf.log("run_started")
        self._store.save(wf)

        for step in wf.steps:
            # "not_executed" steps (recorded by a previous dispatch-less run)
            # are still pending work and may be retried once dispatch exists.
            if step.status not in ("pending", "not_executed"):
                continue

            # Check if this step requires approval
            if step.action == "require_approval":
                wf.state = WorkflowState.AWAITING_APPROVAL
                wf.log("awaiting_approval", f"Step {step.index}: {step.action}")
                self._store.save(wf)
                return self._finish(wf, "awaiting_approval")

            # No dispatcher → record honestly, never fake success.
            if not self._has_dispatch:
                step.status = "not_executed"
                step.result = {"reason": _NO_DISPATCH_REASON}
                wf.log(
                    "step_not_executed",
                    f"Step {step.index}: {step.tool} — no dispatch configured",
                )
                self._store.save(wf)
                continue

            # Execute the step
            step.status = "running"
            step.started_at = _now()
            self._store.save(wf)

            try:
                resolved_params = self._resolve_step_refs(
                    step.params, wf.steps, current_index=step.index
                )
                # ── Redacted-secret guard ─────────────────────────────
                # Secrets are masked to '***' in the DB and only the
                # per-process live cache holds real values. If this workflow
                # was loaded in a FRESH process (crash/restart) the resolved
                # params carry the placeholder — refuse to dispatch it rather
                # than silently sending password='***' to a sibling skill.
                redacted_key = _first_redacted_sensitive_key(resolved_params)
                if redacted_key is not None:
                    raise ValueError(
                        f"Step {step.index} param '{redacted_key}' is the "
                        f"redacted placeholder '{REDACTED_PLACEHOLDER}' — the "
                        "workflow store masks secrets and the live value did not "
                        "survive the process restart. Re-source the secret from "
                        "its environment variable or secret store and re-create "
                        "the workflow with create_workflow; re-running this "
                        "stored copy only resends the placeholder. Use "
                        "cancel_workflow to retire the stale one."
                    )
                result = self._dispatch(step.skill, step.tool, resolved_params)
                returned_failure = _returned_failure(result)
                if returned_failure is not None:
                    raise _StepReturnedFailure(returned_failure, result)
                step.status = "success"
                step.result = result
                step.completed_at = _now()
                wf.log("step_completed", f"Step {step.index}: {step.tool} → success")
            except _StepReturnedFailure as failed:
                # The skill reported failure by RETURNING it. Keep its own
                # payload: the teaching message is what the operator needs.
                step.status = "failed"
                step.result = failed.payload
                step.completed_at = _now()
                wf.state = WorkflowState.FAILED
                wf.log("step_failed", f"Step {step.index}: {step.tool} → {failed.message}")
                for remaining in wf.steps:
                    if remaining.status == "pending":
                        remaining.status = "skipped"
                self._store.save(wf)
                return self._finish(wf, "failed")
            except Exception as exc:
                step.status = "failed"
                step.result = {"error": str(exc)}
                step.completed_at = _now()
                wf.state = WorkflowState.FAILED
                wf.log("step_failed", f"Step {step.index}: {step.tool} → {exc}")

                # Skip remaining steps
                for remaining in wf.steps:
                    if remaining.status == "pending":
                        remaining.status = "skipped"

                self._store.save(wf)
                return self._finish(wf, "failed")

        # All steps walked. If any were only recorded (no dispatch), the
        # workflow is NOT complete — work remains for the calling agent.
        if any(s.status == "not_executed" for s in wf.steps):
            wf.state = WorkflowState.PENDING
            wf.log(
                "dispatch_required",
                f"{sum(1 for s in wf.steps if s.status == 'not_executed')} "
                "step(s) recorded but not executed (no dispatch configured)",
            )
            self._store.save(wf)
            return self._finish(wf, "dispatch_required")

        # All steps genuinely completed
        wf.state = WorkflowState.COMPLETED
        wf.log("workflow_completed")
        self._store.save(wf)
        return self._finish(wf, "completed")

    def _finish(self, wf: Workflow, outcome: str) -> dict[str, Any]:
        """Build the run result dict, surfacing any not-executed steps."""
        result = wf.to_dict()
        result["outcome"] = outcome
        pending_dispatch = [
            {"index": s.index, "skill": s.skill, "tool": s.tool, "params": s.params}
            for s in wf.steps
            if s.status == "not_executed"
        ]
        if pending_dispatch:
            result["pending_dispatch"] = pending_dispatch
            result["message"] = (
                "Workflow is NOT completed: no dispatch function is "
                "configured, so the listed steps were recorded but not "
                "executed. Perform each pending step's skill/tool call "
                "yourself (in order), or re-run with a real dispatcher."
            )
        return result

    def resume_after_approval(self, wf: Workflow, approver: str = "") -> dict[str, Any]:
        """Continue execution after human approval.

        ``approver`` is mandatory — the audit trail must name a human.
        """
        if wf.state != WorkflowState.AWAITING_APPROVAL:
            return {"error": f"Workflow {wf.id} is not awaiting approval (state: {wf.state.value})"}

        if not approver or not approver.strip():
            return {
                "error": (
                    "approver is required for the audit trail. Provide the "
                    "name of the person approving."
                ),
                "workflow_id": wf.id,
                "state": wf.state.value,
            }
        approver = approver.strip()

        wf.approved_by = approver
        wf.log("approved", f"Approved by {approver}")

        # Mark the approval step as done
        for step in wf.steps:
            if step.action == "require_approval" and step.status == "pending":
                step.status = "success"
                step.result = {"approved_by": approver}
                step.completed_at = _now()
                break

        # Continue executing remaining steps
        return self.run_until_checkpoint(wf)

    def rollback(self, wf: Workflow) -> dict[str, Any]:
        """Rollback completed steps in reverse order.

        Rollback results are persisted on the workflow record
        (``wf.rollback_results``); if any rollback step fails,
        ``blocked_reason`` is set to ``"rollback_failed"``.
        """
        # ── Transition guard ──────────────────────────────────────────
        if wf.state in _ROLLBACK_FORBIDDEN_STATES:
            return {
                "error": (
                    f"Workflow '{wf.id}' cannot be rolled back from state "
                    f"'{wf.state.value}'. Drafts have executed nothing, "
                    "completed workflows must not be silently reversed "
                    "(create an explicit reversal plan instead), and a "
                    "rollback already in progress cannot be restarted."
                ),
                "workflow_id": wf.id,
                "state": wf.state.value,
            }

        wf.state = WorkflowState.ROLLING_BACK
        wf.log("rollback_started")
        self._store.save(wf)

        rollback_results: list[dict[str, Any]] = []
        # Persist results into the workflow record as they accumulate.
        wf.rollback_results = rollback_results
        for step in reversed(wf.completed_steps()):
            if not step.rollback_tool:
                rollback_results.append(
                    {
                        "step": step.index,
                        "tool": step.tool,
                        "status": "skipped",
                        "reason": "no rollback defined",
                    }
                )
                continue

            try:
                resolved_rb = self._resolve_step_refs(
                    step.rollback_params, wf.steps, current_index=step.index + 1
                )
                result = self._dispatch(step.skill, step.rollback_tool, resolved_rb)
                # A rollback that *returns* a failure is not a rollback. Saying
                # "rolled back" when the estate never changed back is the worst
                # place in this file to be optimistic.
                returned_failure = _returned_failure(result)
                if returned_failure is not None:
                    raise _StepReturnedFailure(returned_failure, result)
                step.status = "rolled_back"
                rollback_results.append(
                    {
                        "step": step.index,
                        "tool": step.rollback_tool,
                        "status": "success",
                        "result": result,
                    }
                )
                wf.log("rollback_step", f"Step {step.index}: {step.rollback_tool} → success")
            except Exception as exc:
                rollback_results.append(
                    {
                        "step": step.index,
                        "tool": step.rollback_tool,
                        "status": "failed",
                        "error": str(exc),
                    }
                )
                wf.log("rollback_failed", f"Step {step.index}: {step.rollback_tool} → {exc}")
                # Continue rolling back other steps even if one fails

            self._store.save(wf)

        wf.state = WorkflowState.FAILED
        if any(r["status"] == "failed" for r in rollback_results):
            wf.blocked_reason = "rollback_failed"
        wf.log("rollback_completed")
        self._store.save(wf)

        result = wf.to_dict()
        result["rollback_results"] = rollback_results
        return result

    def cancel(self, wf: Workflow, reason: str = "") -> dict[str, Any]:
        """Transition a non-terminal workflow to the terminal CANCELLED state.

        Use this when an approval is rejected, a review flags the plan, or an
        operator decides the workflow must not run. A CANCELLED workflow is
        dead: ``run_until_checkpoint`` / ``resume_after_approval`` refuse to
        execute it. The cancellation is recorded in the workflow audit log.

        Cancel is only valid from a non-terminal state. Already-completed,
        already-failed, or already-cancelled workflows raise a teaching error
        (there is nothing left to cancel). Pending side effects are NOT undone
        — cancel stops future steps; use ``rollback`` to reverse completed work.
        """
        # ── Transition guard ──────────────────────────────────────────
        if wf.state in _TERMINAL_STATES:
            return {
                "error": (
                    f"Workflow '{wf.id}' cannot be cancelled from terminal "
                    f"state '{wf.state.value}'. Only non-terminal workflows "
                    "(draft, pending, running, awaiting_approval, rolling_back) "
                    "can be cancelled. A cancelled/completed/failed workflow is "
                    "already done — create a new plan instead. To reverse "
                    "completed steps, use rollback()."
                ),
                "workflow_id": wf.id,
                "state": wf.state.value,
            }

        reason = (reason or "").strip()
        wf.state = WorkflowState.CANCELLED
        wf.blocked_reason = reason or "cancelled"
        wf.log("cancelled", reason or "no reason given")
        # Steps not yet executed are dead — mark them so status reflects reality.
        for step in wf.steps:
            if step.status in ("pending", "not_executed"):
                step.status = "skipped"
        self._store.save(wf)

        result = wf.to_dict()
        result["outcome"] = "cancelled"
        return result

    @staticmethod
    def _resolve_step_refs(
        params: dict[str, Any],
        steps: list[WorkflowStep],
        current_index: int,
    ) -> dict[str, Any]:
        """Replace ``__from_step_N__:key`` references with actual step results.

        Format: ``"__from_step_0__:plan_id"`` → result of step 0, key ``plan_id``.
        If the key part is omitted, the entire step result is used.

        Recurses into nested dicts/lists. Raises ``ValueError`` with a
        teaching message when a reference is invalid:
          - index is negative, out of range, or not strictly before
            ``current_index`` (forward references can never have a result yet)
          - the referenced step did not finish with status ``success``
        """

        def _resolve_ref(v: str) -> Any:
            if v.endswith("__"):
                idx_part = v[len("__from_step_") : -2]
                result_key: str | None = None
            else:
                ref_part, sep, result_key = v.partition(":")
                if not sep or not ref_part.endswith("__"):
                    raise ValueError(
                        f"Malformed step reference '{v}'. Expected "
                        "'__from_step_N__' or '__from_step_N__:key', where N is "
                        "the 0-based index of an earlier step. Correct the step "
                        "params with update_draft, then run review_workflow to "
                        "confirm every reference resolves before executing."
                    )
                idx_part = ref_part[len("__from_step_") : -2]

            try:
                idx = int(idx_part)
            except ValueError:
                raise ValueError(
                    f"Malformed step reference '{v}': '{idx_part}' is not an "
                    "integer step index. Expected '__from_step_N__' with N a "
                    "whole number such as 0 or 1. Run review_workflow to see the "
                    "step indices, then fix the reference with update_draft."
                ) from None

            if idx < 0 or idx >= len(steps):
                raise ValueError(
                    f"Step reference '{v}' points to step {idx}, but this "
                    f"workflow has steps 0..{len(steps) - 1}. Run review_workflow "
                    "to see the step list with its indices, then correct the "
                    "reference index in the step params with update_draft."
                )
            if idx >= current_index:
                raise ValueError(
                    f"Step reference '{v}' is a forward/self reference "
                    f"(step {idx} has not produced a result before step "
                    f"{current_index} runs). References must point to an "
                    "EARLIER step. Run review_workflow to see the execution "
                    "order, then use update_draft to reorder the steps or "
                    "re-point the reference at a step before "
                    f"{current_index}."
                )

            source = steps[idx]
            if source.status != "success":
                raise ValueError(
                    f"Step reference '{v}' points to step {idx} "
                    f"({source.tool}) which has status '{source.status}', "
                    "not 'success' — its result cannot be used. Run "
                    f"get_workflow_status to see why step {idx} did not "
                    "succeed, resolve that cause and re-run the workflow; if "
                    "it cannot succeed, use cancel_workflow and re-author "
                    "without the reference."
                )

            if result_key is not None:
                if isinstance(source.result, dict):
                    return source.result.get(result_key, source.result)
                return source.result
            return source.result

        def _resolve_value(v: Any) -> Any:
            if isinstance(v, dict):
                return {k: _resolve_value(item) for k, item in v.items()}
            if isinstance(v, list):
                return [_resolve_value(item) for item in v]
            if isinstance(v, str) and v.startswith("__from_step_"):
                return _resolve_ref(v)
            return v

        return {k: _resolve_value(v) for k, v in params.items()}


def _first_redacted_sensitive_key(params: Any) -> str | None:
    """Return the first sensitive-key whose value is the redacted placeholder.

    Recurses through dicts/lists exactly like the persistence redaction, so a
    secret nested in a sub-dict is caught too. Returns ``None`` when every
    sensitive param holds a real value (the in-process happy path) — and never
    fires for a non-sensitive key that merely happens to equal ``'***'``.
    """
    if isinstance(params, dict):
        for key, value in params.items():
            if is_sensitive_key(key) and value == REDACTED_PLACEHOLDER:
                return key
            nested = _first_redacted_sensitive_key(value)
            if nested is not None:
                return nested
    elif isinstance(params, list):
        for item in params:
            nested = _first_redacted_sensitive_key(item)
            if nested is not None:
                return nested
    return None


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _noop_dispatch(skill: str, tool: str, params: dict[str, Any]) -> Any:
    """Defensive placeholder — never invoked: run_until_checkpoint records
    steps as not_executed when no dispatch is configured instead of calling
    this. Kept so accidental invocation is loud rather than fake-successful."""
    raise RuntimeError(
        f"No dispatch configured for {skill}.{tool} — refusing to fake "
        "execution. " + _NO_DISPATCH_REASON
    )
