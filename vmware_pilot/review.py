"""Structural review of a planned workflow.

Pure-function implementation so it is trivially testable. The MCP tool wrapper
in ``vmware_pilot/mcp_server/server.py`` just loads the workflow and calls ``review()``.

Why the classification below looks the way it does
--------------------------------------------------

The approval gate used to ask one question — does this tool's name contain
"delete", "remove", "destroy", "force_power_off", "shutdown", "drop" or
"rollback"? — and treated every other answer as safe. That is an allowlist by
omission, and it has to keep pace with fourteen sibling skills that pilot cannot
import and does not get told about.

It did not keep pace. Measured on 2026-08-30 against every sibling's own MCP
annotations: 120 tools across the family declare ``readOnlyHint: False``, and 24
of them matched a substring. The other 96 produced no finding at all, 17 of them
tools their own skill marks ``destructiveHint: True`` — ``vm_clean_slate``
(reverts a VM to a baseline snapshot), ``pool_push_image``, ``machine_reset``,
``session_logoff``, ``ako_restart``, ``vgpu_assign``. Adding those seventeen names
to the tuple would fix today's list and lose again on the next release.

So the shape changed rather than the contents:

*The catalog is read.* ``SKILL_CATALOG`` already carried a ``risk`` label for 69
tools, sitting in this same package, and nothing looked at it — including all 13
entries marked ``risk: high``.

*Absence of evidence is not evidence of safety.* A step whose tool pilot cannot
place gets ``unknown`` and needs an approval gate ahead of it, reported as what
it is — an unclassifiable step — rather than as a destructive one. This is the
part that survives the surface growing: a tool added to any sibling tomorrow
lands in ``unknown`` and is gated without a line here being edited.

Medium-tier writes stay ungated on purpose. The family gates destructive work,
and requiring approval ahead of every state change would flag four built-in
templates whose design is create-in-staging → approve → apply. They are
classified and counted, not invisible.
"""

from __future__ import annotations

from typing import Any

from vmware_pilot.mcp_server._catalog import SKILL_CATALOG
from vmware_pilot.models import Workflow

#: Risk tiers a step can be placed in. ``UNKNOWN`` is not a failure of the
#: classifier, it is an honest output of it, and it is gated like a risk.
TIER_DESTRUCTIVE = "destructive"
TIER_WRITE = "write"
TIER_READ = "read"
TIER_UNKNOWN = "unknown"

#: Tiers that require a preceding ``require_approval`` step.
_GATED_TIERS = (TIER_DESTRUCTIVE, TIER_UNKNOWN)

# Heuristic: any tool name containing one of these is "destructive".
# Aligns with the L3+ tier in capabilities.md across the family. Retained as an
# escalation signal — it can only ever *add* a tier, never clear one — because a
# tool named delete_* is destructive whether or not the catalog has heard of it.
_DESTRUCTIVE_HINTS = (
    "delete",
    "remove",
    "destroy",
    "force_power_off",
    "shutdown",
    "drop",
    "rollback",
)

# Heuristic: tools that are read-only (L1/L2).
#
# This tuple is the one place a *name* can still argue a tool is safe, so it is
# the one place a mistake here is silent rather than loud. It is consulted last,
# after the catalog, and it is deliberately not being extended: every name added
# here is a tool moved out of the gated set on the strength of a substring. The
# measured cost of leaving it short is 54 sibling read tools that land in
# ``unknown`` and draw a finding; the cost of lengthening it is a write tool that
# never draws one. ``vks.get_tkc_kubeconfig`` and ``get_supervisor_kubeconfig``
# — which hand back a live Supervisor credential — are what "``get`` means
# read" looks like when it is wrong, and they are in the catalog for exactly
# that reason.
_READONLY_HINTS = (
    "list",
    "get",
    "show",
    "browse",
    "scan",
    "status",
    "health",
    "fetch",
    "describe",
    "inspect",
)

# Common identifier-like param-name fragments — used to detect delete-then-use.
_ID_FRAGMENTS = ("name", "id", "vm", "segment", "rule", "policy")

_RISK_TO_TIER = {
    "high": TIER_DESTRUCTIVE,
    "critical": TIER_DESTRUCTIVE,
    "medium": TIER_WRITE,
    "low": TIER_READ,
}


def _catalog_risk(skill: str, tool: str) -> str:
    """The catalog's risk label for ``skill.tool``, or ``""`` if it has none.

    Looks up ``(skill, tool)`` first. A workflow may name a skill the catalog
    does not carry (a skill added to the family after the catalog, or a misspelt
    skill name), so a bare tool-name match across skills is accepted
    as a fallback — and when that matches more than one skill with different
    labels, the highest wins. Guessing downward is the only guess that can hide a
    destructive step.
    """
    entry = SKILL_CATALOG.get(skill, {}).get("tools", {}).get(tool)
    if entry:
        return entry.get("risk", "")

    matches = {
        meta.get("risk", "")
        for candidate in SKILL_CATALOG.values()
        for name, meta in candidate["tools"].items()
        if name == tool
    }
    for risk in ("critical", "high", "medium", "low"):
        if risk in matches:
            return risk
    return ""


def classify_step(skill: str, tool: str, action: str = "") -> tuple[str, str]:
    """Place one step in a risk tier, and say what decided it.

    Returns ``(tier, reason)``. ``reason`` is carried into the finding message so
    an operator can tell "the catalog says this is high risk" from "pilot has
    never heard of this tool", which are the same verdict for opposite reasons.
    """
    name = (tool or "").lower()
    action_name = (action or "").lower()

    hit = next((h for h in _DESTRUCTIVE_HINTS if h in name or h in action_name), "")
    if hit:
        return TIER_DESTRUCTIVE, f"its name contains {hit!r}"

    risk = _catalog_risk(skill, tool)
    if risk:
        return _RISK_TO_TIER.get(risk, TIER_UNKNOWN), f"the skill catalog marks it risk {risk!r}"

    hit = next((h for h in _READONLY_HINTS if h in name), "")
    if hit:
        return TIER_READ, f"its name contains {hit!r}, which reads as an inspection"

    return TIER_UNKNOWN, "it is not in pilot's skill catalog and its name matches no pattern"


def review(wf: Workflow) -> dict[str, Any]:
    """Sanity-check a planned workflow and return findings + summary.

    See docstring of ``vmware_pilot.mcp_server.server.review_workflow`` for behavior contract.
    """
    findings: list[dict[str, Any]] = []
    destructive_indices: list[int] = []
    unclassified_indices: list[int] = []
    readonly_indices: list[int] = []
    approval_indices: list[int] = []
    write_indices: list[int] = []
    #: step index -> why the classifier put it where it did, for the messages.
    reasons: dict[int, str] = {}

    # resource_id -> earliest step index that deletes it
    deleted_resources: dict[str, int] = {}

    for step in wf.steps:
        if step.action == "require_approval":
            approval_indices.append(step.index)
            continue

        tier, reason = classify_step(step.skill, step.tool, step.action)
        reasons[step.index] = reason
        is_destructive = tier == TIER_DESTRUCTIVE

        if is_destructive:
            destructive_indices.append(step.index)
            for k, v in step.params.items():
                if isinstance(v, str) and any(t in k.lower() for t in _ID_FRAGMENTS):
                    deleted_resources.setdefault(v, step.index)
        elif tier == TIER_UNKNOWN:
            unclassified_indices.append(step.index)
        elif tier == TIER_WRITE:
            write_indices.append(step.index)
        else:
            readonly_indices.append(step.index)

        for k, v in step.params.items():
            if v in ("", None) and k not in ("target", "description"):
                findings.append(
                    {
                        "severity": "low",
                        "kind": "empty_param",
                        "step_index": step.index,
                        "message": (
                            f"Step {step.index} ({step.tool}) has empty value for "
                            f"required-looking param '{k}'"
                        ),
                    }
                )
            if isinstance(v, str) and v.upper() in ("REVIEW", "TODO", "FIXME"):
                findings.append(
                    {
                        "severity": "high",
                        "kind": "placeholder_param",
                        "step_index": step.index,
                        "message": (
                            f"Step {step.index} ({step.tool}) has placeholder value "
                            f"'{v}' for param '{k}'"
                        ),
                    }
                )

        if not is_destructive:
            for k, v in step.params.items():
                if (
                    isinstance(v, str)
                    and v in deleted_resources
                    and deleted_resources[v] < step.index
                ):
                    findings.append(
                        {
                            "severity": "high",
                            "kind": "delete_then_use",
                            "step_index": step.index,
                            "message": (
                                f"Step {step.index} references resource '{v}' which "
                                f"step {deleted_resources[v]} deletes — operation will "
                                "fail at dispatch"
                            ),
                        }
                    )

    # Approval coverage check.
    by_index = {s.index: s for s in wf.steps}
    for d_idx in destructive_indices:
        if not any(a < d_idx for a in approval_indices):
            step = by_index[d_idx]
            findings.append(
                {
                    "severity": "high",
                    "kind": "ungated_destructive",
                    "step_index": d_idx,
                    "message": (
                        f"Step {d_idx} ({step.skill}.{step.tool}) is destructive — "
                        f"{reasons[d_idx]} — but has no preceding require_approval "
                        "gate. Add an approval step before it, or document why this "
                        "is safe."
                    ),
                }
            )

    # Steps pilot could not place. Gated for the same reason a destructive step
    # is, and reported differently because the evidence is different: this says
    # "unknown", not "dangerous". Overstating it is how a gate gets ignored.
    for u_idx in unclassified_indices:
        if not any(a < u_idx for a in approval_indices):
            step = by_index[u_idx]
            findings.append(
                {
                    "severity": "high",
                    "kind": "ungated_unclassified",
                    "step_index": u_idx,
                    "message": (
                        f"Step {u_idx} ({step.skill}.{step.tool}) cannot be "
                        f"classified — {reasons[u_idx]} — so this review cannot "
                        "rule out that it changes state, and it has no preceding "
                        "require_approval gate. Add the tool to SKILL_CATALOG with "
                        "the risk its own skill declares (readOnlyHint / "
                        "destructiveHint in that skill's MCP annotations), or put a "
                        "require_approval step before it."
                    ),
                }
            )

    # Group integrity
    groups: dict[str, list[int]] = {}
    for s in wf.steps:
        if s.group_id:
            groups.setdefault(s.group_id, []).append(s.index)
    for gid, indices in groups.items():
        indices.sort()
        if indices != list(range(min(indices), max(indices) + 1)):
            findings.append(
                {
                    "severity": "medium",
                    "kind": "non_contiguous_group",
                    "step_index": indices[0],
                    "message": f"Parallel group '{gid}' has non-contiguous steps {indices}",
                }
            )
        for i in indices:
            if i in destructive_indices:
                findings.append(
                    {
                        "severity": "high",
                        "kind": "destructive_in_parallel_group",
                        "step_index": i,
                        "message": (
                            f"Step {i} is destructive but belongs to parallel group "
                            f"'{gid}' — concurrent destructive ops bypass approval ordering"
                        ),
                    }
                )

    est_duration_min = (
        len(readonly_indices) * 0.2
        + len(destructive_indices) * 1.5
        + len(approval_indices) * 5.0
        + len(groups) * (-0.5)
    )
    est_duration_min = max(0.5, est_duration_min)

    verdict = "needs_revision" if any(f["severity"] == "high" for f in findings) else "approved"

    return {
        "workflow_id": wf.id,
        "verdict": verdict,
        "findings": findings,
        "summary": {
            "total_steps": len(wf.steps),
            "destructive_steps": len(destructive_indices),
            "write_steps": len(write_indices),
            "read_only_steps": len(readonly_indices),
            # Coverage of the review itself. Without these, an "approved" verdict
            # cannot be told apart from "every step was unrecognisable and this
            # review looked at nothing" — the failure the gate had been in for
            # some time before anyone measured it.
            "classified_steps": len(destructive_indices)
            + len(write_indices)
            + len(readonly_indices),
            "unclassified_steps": len(unclassified_indices),
            "approval_gates": len(approval_indices),
            "parallel_groups": len(groups),
            "est_duration_min": round(est_duration_min, 1),
        },
    }
