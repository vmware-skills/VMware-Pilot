"""Confirmation gate for Pilot's own destructive tools: rollback, cancel_workflow.

HLD §7 (revised 2026-09-16) applied to Pilot itself:

* L2 — a bare call (``confirm=False``) changes nothing and returns a preview;
* L1 — preview and acting response carry ``blast_radius``: which executed
  steps are undone, which stay applied, which are skipped;
* L3 — ``confirm=True`` refuses when a blocker is present (the transition is not
  allowed from the workflow's state) or anything is unmeasured (the record or a
  step status cannot be read). Unmeasured means Pilot cannot say what the call
  would leave behind, and "cannot say" is not "nothing".

A step left ``running`` or ``interrupted`` (a Pilot process stopped mid-dispatch)
is unmeasured too: whether it changed the estate is unknown, and only the target
system can say. After checking there, the caller re-runs with
``acknowledge_unknown_effects=True`` — a boolean acknowledgement, the weaker form
HLD §7 allows for "does a measurement apply at all": Pilot cannot measure the
step, a human looked instead. It covers only those steps; blockers and
unrecognised statuses still refuse.

``cancel_workflow`` is here although it undoes nothing: stopping a workflow
part-way leaves its executed steps applied — a half-done change — and the
preview is where the user sees that before deciding.

Pure functions over a loaded ``Workflow``; the MCP tools in
``mcp_server/tools/lifecycle.py`` call them.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from vmware_pilot.executor import _ROLLBACK_FORBIDDEN_STATES, _TERMINAL_STATES
from vmware_pilot.models import Workflow, WorkflowStep, WorkflowStore, _redact_for_persistence

_log = logging.getLogger("vmware-pilot.lifecycle_gate")

#: Identifier lists are capped; counts never are.
CAP = 16

#: Every status the executor writes. Anything else is a record Pilot did not
#: write, and it cannot tell whether that step changed the estate.
KNOWN_STEP_STATUSES = frozenset({
    "pending", "running", "success", "failed", "skipped",
    "rolled_back", "not_executed", "interrupted",
})

#: Statuses whose side effects are unknown (a process died mid-dispatch).
_UNKNOWN_EFFECT_STATUSES = ("running", "interrupted")


def _is_gate(step: WorkflowStep) -> bool:
    """Approval gates record a decision; they change nothing on any estate."""
    return step.action == "require_approval"


def _ref(step: WorkflowStep) -> dict[str, Any]:
    return {"index": step.index, "skill": step.skill, "tool": step.tool}


#: Name parts that mark a secret, matched per token of the key (``db_password``,
#: ``apiToken``, ``client-secret``). Wider than the persistence redaction on
#: purpose: that one also drives the executor's dispatch guard, while this one
#: only decides what a preview prints.
_SECRET_TOKENS = frozenset({
    "password", "passwd", "pwd", "secret", "token", "apikey", "auth", "credential",
    "credentials", "bearer", "authorization",
})
#: Tokens that end a name but mean a count or a mode, not a secret value.
_NOT_SECRET_TAILS = frozenset({"count", "type", "mode", "url", "name", "id", "expiry", "ttl"})


def _key_tokens(key: str) -> list[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", key)
    return [t for t in re.split(r"[^A-Za-z0-9]+", spaced.lower()) if t]


def _is_secret_name(key: Any) -> bool:
    if not isinstance(key, str):
        return False
    tokens = _key_tokens(key)
    joined = "".join(tokens)
    hit = any(t in _SECRET_TOKENS for t in tokens) or joined in {"apikey", "privatekey"} or (
        "api" in tokens and "key" in tokens) or ("private" in tokens and "key" in tokens) or (
        "auth" in tokens and "key" in tokens)
    return hit and not (tokens and tokens[-1] in _NOT_SECRET_TAILS)


def _redact_preview(obj: Any) -> Any:
    """``obj`` with every value under a secret-looking key replaced by ``***``."""
    if isinstance(obj, dict):
        return {k: ("***" if _is_secret_name(k) else _redact_preview(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact_preview(v) for v in obj]
    return obj


def _rollback_ref(step: WorkflowStep) -> dict[str, Any]:
    """A step with the rollback call it would get: tool and parameters, secrets redacted.

    A rollback's parameters often carry ``confirm: True`` and run on the
    ``rollback`` call alone, so the preview must show them for the human
    deciding ``confirm=True`` to see what they are approving.
    """
    return {**_ref(step), "rollback_tool": step.rollback_tool,
            "rollback_params": _redact_preview(_redact_for_persistence(step.rollback_params or {}))}


def _listed(steps: list[WorkflowStep], key: str, entry=_ref) -> dict[str, Any]:
    return {f"{key}_count": len(steps), key: [entry(s) for s in steps[:CAP]]}


def _unknown_effect_steps(wf: Workflow) -> list[WorkflowStep]:
    return [s for s in wf.steps if s.status in _UNKNOWN_EFFECT_STATUSES]


def _unmeasured(wf: Workflow, acknowledge_unknown_effects: bool) -> list[str]:
    unrecognised = [
        f"step {s.index} ({s.skill}.{s.tool}) has status {s.status!r}, which Pilot does "
        "not recognise — it cannot tell whether that step changed anything"
        for s in wf.steps
        if s.status not in KNOWN_STEP_STATUSES
    ]
    if acknowledge_unknown_effects:
        return unrecognised
    unknown = [
        f"step {s.index} ({s.skill}.{s.tool}) is '{s.status}': a Pilot process stopped "
        f"while dispatching it, so whether {s.tool} took effect is unknown — check in the "
        "target system whether it did (and whether anything must be reversed by hand), "
        "then re-run with acknowledge_unknown_effects=True"
        for s in _unknown_effect_steps(wf)
    ]
    return [*unrecognised, *unknown]


def _executed(wf: Workflow) -> list[WorkflowStep]:
    return [s for s in wf.steps if s.status == "success" and not _is_gate(s)]


def _common(wf: Workflow, state_after: str, acknowledged: bool) -> dict[str, Any]:
    return {
        "workflow": {"id": wf.id, "type": wf.workflow_type, "state": wf.state.value},
        "state_after": state_after,
        **_listed(_unknown_effect_steps(wf), "unknown_effects"),
        "unknown_effects_acknowledged": acknowledged,
    }


def rollback_blast_radius(
    wf: Workflow, acknowledge_unknown_effects: bool = False
) -> dict[str, Any]:
    """What ``rollback`` would undo, leave applied, and not touch."""
    executed = _executed(wf)
    undo = [s for s in reversed(executed) if s.rollback_tool]
    left = [s for s in executed if not s.rollback_tool]
    # Steps the calling agent may have performed from pending_dispatch: Pilot
    # recorded them as not_executed and will not reverse them.
    agent_side = [s for s in wf.steps if s.status == "not_executed" and s.rollback_tool]
    blockers = []
    if wf.state in _ROLLBACK_FORBIDDEN_STATES:
        blockers.append(
            f"workflow is in state '{wf.state.value}': rollback is not allowed from draft "
            "(nothing ran), completed (create an explicit reversal plan instead), "
            "rolling_back (already in progress) or cancelled"
        )
    return {
        **_common(wf, "failed", acknowledge_unknown_effects),
        **_listed(undo, "would_roll_back", _rollback_ref),
        **_listed(left, "left_in_place"),
        **_listed(agent_side, "not_reversed_by_pilot", _rollback_ref),
        "blockers": blockers,
        "unmeasured": _unmeasured(wf, acknowledge_unknown_effects),
    }


def cancel_blast_radius(
    wf: Workflow, acknowledge_unknown_effects: bool = False
) -> dict[str, Any]:
    """What ``cancel_workflow`` would leave half-applied and what it would skip."""
    skipped = [s for s in wf.steps if s.status in ("pending", "not_executed")]
    blockers = []
    if wf.state in _TERMINAL_STATES:
        blockers.append(
            f"workflow is in terminal state '{wf.state.value}': there is nothing left to "
            "cancel; create a new plan instead"
        )
    return {
        **_common(wf, "cancelled", acknowledge_unknown_effects),
        **_listed(_executed(wf), "left_in_place"),
        **_listed(skipped, "would_skip"),
        "blockers": blockers,
        "unmeasured": _unmeasured(wf, acknowledge_unknown_effects),
    }


def load_for_gate(
    store: WorkflowStore, workflow_id: str, verb: str
) -> tuple[Workflow | None, dict[str, Any] | None]:
    """Load a workflow, or return the refusal that says why it cannot be gated.

    A record that exists but cannot be parsed is unmeasured, not missing: its
    blast radius cannot be computed, so neither preview nor action proceeds.
    """
    try:
        wf = store.load(workflow_id)
    except Exception as exc:  # noqa: BLE001 — any parse failure means "unreadable"
        _log.error("workflow %s unreadable for %s", workflow_id, verb, exc_info=True)
        reason = (
            f"the stored record could not be read ({type(exc).__name__}), so what "
            f"{verb} would change cannot be measured"
        )
        return None, {
            "error": (
                f"Workflow '{workflow_id}' could not be read: {reason}. Nothing was "
                f"changed. The record in ~/.vmware/workflows.db is damaged — verify the "
                "estate by hand (each step's skill/tool), then create a new plan."
            ),
            "workflow_id": workflow_id,
            "blast_radius": {"blockers": [], "unmeasured": [reason]},
        }
    if wf is None:
        return None, {"error": f"Workflow '{workflow_id}' not found"}
    return wf, None


def refusal(verb: str, wf: Workflow, radius: dict[str, Any]) -> dict[str, Any] | None:
    """The L3 refusal for ``confirm=True``, or ``None`` when the call may act."""
    reasons = [*radius["blockers"], *radius["unmeasured"]]
    if not reasons:
        return None
    return {
        "error": (
            f"Refusing to {verb} workflow '{wf.id}': " + "; ".join(reasons) + ". Nothing "
            f"was changed. Resolve that first (get_workflow_status('{wf.id}') shows every "
            "step), then preview again."
        ),
        "workflow_id": wf.id,
        "state": wf.state.value,
        "blast_radius": radius,
    }
