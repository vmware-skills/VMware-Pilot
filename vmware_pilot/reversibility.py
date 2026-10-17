"""What a step's rollback actually leaves behind.

A rollback that ran is not the same as a step that was undone. On the first
real-vSphere run (2026-10-17) a linked clone's rollback was ``power_off_vm``:
it succeeded, the step was marked ``rolled_back``, and the clone with its delta
disk stayed in the inventory. Pilot cannot know in general whether a
rollback_tool is a step's full inverse, so this table names the steps that
*create* something and the rollback tools that remove it; any other rollback of
such a step is partial, and the preview and the result say what stays.

A step with no rollback_tool at all is not reversible by Pilot: its change
stays applied after rollback (a production ``reconfigure_vm``, say).
"""

from __future__ import annotations

from types import MappingProxyType

from vmware_pilot.models import WorkflowStep
from vmware_pilot.renames import _server

#: (skill, tool) that creates an object → rollback tools that remove it.
_CREATES = MappingProxyType({
    ("aiops", "deploy_linked_clone"): frozenset({"delete_vm"}),
    ("aiops", "clone_vm"): frozenset({"delete_vm"}),
    ("aiops", "deploy_vm_from_template"): frozenset({"delete_vm"}),
    ("aiops", "deploy_vm_from_ova"): frozenset({"delete_vm"}),
    ("aiops", "create_vm"): frozenset({"delete_vm"}),
    ("aiops", "batch_clone_vms"): frozenset(),
    ("aiops", "batch_linked_clone_vms"): frozenset(),
})

OUTSIDE_PILOT_NOTE = (
    "not reversible by Pilot: these steps were handed to the caller (no dispatcher) and have no "
    "rollback_tool — if they were performed, their change stays after rollback (a clone stays in "
    "the inventory, a production resize stays applied); check and reverse by hand"
)

NOT_REVERSIBLE_NOTE = (
    "not reversible by Pilot: these steps have no rollback_tool, so their change stays "
    "applied after rollback — reverse it by hand (or with an explicit new plan) if needed"
)


def is_read_only(step: WorkflowStep) -> bool:
    """True when the catalog marks the step's tool low risk (a read).

    Only the catalog decides: the name heuristic reads ``retarget_vm`` as a read.
    """
    from vmware_pilot.review import _catalog_risk

    return _catalog_risk(step.skill, step.tool) == "low"


def leaves_behind(step: WorkflowStep) -> str:
    """What stays after ``step``'s rollback runs, or "" when it is a full undo."""
    # Same spellings the rest of Pilot accepts: aiops / vmware-aiops / vmware_aiops / AIops.
    skill = _server(step.skill).removeprefix("vmware-") if isinstance(step.skill, str) else ""
    removers = _CREATES.get((skill, step.tool))
    if removers is None or step.rollback_tool in removers:
        return ""
    what = step.params.get("new_name") or step.params.get("vm_name") or "the object it created"
    return (
        f"{step.tool} created {what!r}; its rollback {step.rollback_tool} does not remove "
        f"it — {what!r} (and any disk it owns) stays in the inventory. Delete it by hand "
        "after checking it is the one this workflow created."
    )
