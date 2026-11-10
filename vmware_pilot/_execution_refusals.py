"""Refusals for missing secrets and steps without execution evidence."""

from __future__ import annotations


from typing import Any

from vmware_pilot.models import (
    REDACTED_PLACEHOLDER,
    Workflow,
    WorkflowStep,
    is_sensitive_key,
)


from vmware_pilot._dispatch_result import (
    _EXIT_CODE_TOOLS as _EXIT_CODE_TOOLS,
    _PREVIEW_ONLY as _PREVIEW_ONLY,
    _guest_failure as _guest_failure,
    _guest_payloads as _guest_payloads,
    _guest_unknown as _guest_unknown,
    _returned_failure as _returned_failure,
    _top_level_payloads as _top_level_payloads,
)


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


def _unrun_refusal(wf: Workflow, unrun: list[WorkflowStep]) -> dict[str, Any]:
    """Teaching refusal: the gate would approve steps Pilot never ran."""
    listed = ", ".join(f"step {s.index} ({s.skill}.{s.tool})" for s in unrun)
    return {
        "error": (
            f"Refusing to approve workflow '{wf.id}': {len(unrun)} step(s) before this "
            f"gate — {listed} — were recorded but NOT executed (the server runs without "
            "--dispatch), so Pilot has no record that they ran or what they returned, whatever the "
            "approval message says. If you performed each of them yourself and checked "
            "its result, show those results to the approver and call approve again with "
            "acknowledge_not_executed=True; do not perform them again. If nobody "
            "performed them, cancel_workflow this one and create a new workflow."
        ),
        "workflow_id": wf.id,
        "state": wf.state.value,
        "not_executed_before_gate": [
            {"index": s.index, "skill": s.skill, "tool": s.tool} for s in unrun
        ],
    }
