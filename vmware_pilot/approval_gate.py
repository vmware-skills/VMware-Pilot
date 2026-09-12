"""The approval-gate rule for custom workflows: reject, never warn.

A custom workflow — a YAML file in ``~/.vmware/workflows/``, a
``create_workflow`` step list, or an AI-designed draft — is not reviewed by
anyone who wrote pilot. So a step that could change the estate has to have a
human ``require_approval`` gate ahead of it, and a workflow without one is
refused at every door: saving it, confirming it, planning it from YAML, and
running it (``force=True`` included — the fix is one inserted step, and that
step *is* the human consent ``force`` would otherwise stand in for).

What counts as "needs a gate" is not decided here. It is ``review.review``'s
own ``ungated_destructive`` / ``ungated_unclassified`` findings, which come from
``review.classify_step`` reading ``SKILL_CATALOG``: the ``destructive`` tier
(catalog risk high/critical, or a destructive name) and the ``unknown`` tier
(pilot cannot rule out that the step changes state). Keeping a second list
here is how the helper script ended up with its own, shorter one.

Medium-risk writes (the catalog's ``write`` tier — create_segment,
vm_power_on, …) are deliberately *not* in that set; see the ``review`` module
docstring for why, and for the built-in templates that depend on it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from vmware_pilot.models import Workflow
from vmware_pilot.review import classify_step, review

#: ``review()`` finding kinds that mean "this step would run with no human gate
#: in front of it". The same two kinds ``run_workflow`` blocks on.
GATE_FINDING_KINDS = ("ungated_destructive", "ungated_unclassified")


@dataclass(frozen=True)
class GateViolation:
    """One step that needs a ``require_approval`` gate ahead of it and has none."""

    step_index: int
    skill: str
    tool: str
    kind: str
    reason: str

    def label(self) -> str:
        return f"step {self.step_index} ({self.skill}.{self.tool}: {self.reason})"


def gate_violations(wf: Workflow) -> tuple[GateViolation, ...]:
    """Every step of ``wf`` that ``review()`` says runs ungated, in step order."""
    by_index = {s.index: s for s in wf.steps}
    found = []
    for finding in review(wf)["findings"]:
        if finding["kind"] not in GATE_FINDING_KINDS:
            continue
        step = by_index[finding["step_index"]]
        _, reason = classify_step(step.skill, step.tool, step.action)
        found.append(GateViolation(step.index, step.skill, step.tool, finding["kind"], reason))
    return tuple(sorted(found, key=lambda v: v.step_index))


def is_custom_workflow(wf: Workflow) -> bool:
    """True unless ``wf`` was built by a vetted built-in template.

    Fail-closed: a workflow is only exempt when its type names a built-in *and*
    it carries neither custom marker. ``create_workflow`` / ``design_workflow``
    set ``params["custom"]``; the YAML loader sets ``params["_source"]`` — which
    is what catches a YAML file that reuses a built-in's name. A workflow
    persisted before either marker existed has a non-built-in type and so is
    custom too.
    """
    from vmware_pilot.templates import BUILTIN_TEMPLATES

    if wf.params.get("custom") or "_source" in wf.params:
        return True
    return wf.workflow_type not in BUILTIN_TEMPLATES


def gate_step_for(violation: GateViolation) -> dict[str, Any]:
    """The exact step to insert ahead of ``violation`` to satisfy the rule."""
    return {
        "action": "require_approval",
        "skill": "pilot",
        "tool": "approve",
        "params": {
            "message": (
                f"About to run {violation.skill}.{violation.tool}. "
                "Has a human reviewed the plan? Proceed?"
            )
        },
    }


def _yaml_snippet(violation: GateViolation) -> str:
    step = gate_step_for(violation)
    return (
        f"  - action: {step['action']}\n"
        f"    skill: {step['skill']}\n"
        f"    tool: {step['tool']}\n"
        "    params:\n"
        f'      message: "{step["params"]["message"]}"'
    )


def refusal(
    workflow_name: str,
    violations: tuple[GateViolation, ...],
    *,
    verb: str,
    source: str = "",
    retry: str = "",
    extra: str = "",
) -> dict[str, Any]:
    """Teaching refusal payload: which steps, why, and the step that fixes it.

    ``verb`` is what was refused ("save", "confirm", "plan", "run"); ``source``
    names the YAML file when there is one; ``retry`` says which call to make
    after fixing; ``extra`` carries a door-specific sentence (e.g. why ``force``
    did not help).
    """
    first = violations[0]
    where = f" (defined in {source})" if source else ""
    listing = "; ".join(v.label() for v in violations)
    if source:
        how = (
            f"Edit {source} and insert this step before step {first.step_index} — "
            f"one gate there covers every later step:\n{_yaml_snippet(first)}\n"
        )
    else:
        how = (
            f"Insert {gate_step_for(first)} before step {first.step_index} — one gate "
            "there covers every later step. "
        )
    message = (
        f"Refusing to {verb} custom workflow '{workflow_name}'{where}: "
        f"{len(violations)} step(s) could change the estate and have no "
        f"require_approval gate before them — {listing}. Custom workflows must put a "
        "human approval gate ahead of every destructive or unclassifiable step. "
        f"{how}"
    )
    if retry:
        message += f"Then {retry}."
    if extra:
        message += f" {extra}"
    return {
        "error": message,
        "approval_gate_violations": [asdict(v) for v in violations],
        "fix": {"insert_before_step": first.step_index, "step": gate_step_for(first)},
    }


class ApprovalGateError(ValueError):
    """Raised when a custom workflow is built with an ungated step.

    Carries the structured refusal so a caller can return it whole instead of
    reducing it to a string (``_safe_error`` caps ValueError text at 500
    characters, and the remedy is at the end).
    """

    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__(payload["error"])
        self.payload = payload


def enforce(wf: Workflow, *, verb: str, source: str = "", retry: str = "") -> None:
    """Raise ``ApprovalGateError`` if ``wf`` has any ungated gated step."""
    violations = gate_violations(wf)
    if violations:
        raise ApprovalGateError(
            refusal(wf.workflow_type, violations, verb=verb, source=source, retry=retry)
        )
