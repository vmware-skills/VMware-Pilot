"""Companion tools that preview unless called with ``confirm=True``.

Across the family, destructive (and a few reverse-included) MCP tools take
``confirm: bool = False``: a bare call returns ``{"action": "preview", ...}``
and changes nothing (HLD §7, revised 2026-09-16). In every built-in template a
step that calls such a tool comes after a ``require_approval`` step — Pilot's
own ``approve`` gate, which is the human decision — so the step itself says
``confirm=True``. ``tests/test_templates_pass_confirm.py`` fails any template
that sends ``confirm=True`` before its first approval step. The old spellings
``confirmed`` / ``dry_run`` were removed from those tools (Aria, AVI and Storage
2.5.0, VKS 3.5.0), where a call passing one is refused as an unknown argument.
Pilot never sends them: it drops them from a step and refuses caller params
that carry them.

The executor records a step that only previewed as *failed*, so a tool missing
from this table fails loudly on its first dispatch rather than silently doing
nothing — but the table is still what to update when a companion gates a tool.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Any

#: skill → tools that act only with ``confirm=True``.
GATED_TOOLS = MappingProxyType({
    "aiops": frozenset({
        "power_off_vm", "migrate_vm", "revert_vm_snapshot", "delete_vm_snapshot",
        "run_vm_guest_command", "run_vm_guest_command_with_output", "upload_vm_guest_file", "provision_vm_guest",
        "delete_cluster", "remove_cluster_host", "set_vm_ttl", "restore_vm_clean_slate",
        "rollback_vm_plan", "apply_vm_plan", "delete_vm",
        "create_dvs_portgroup", "add_host_vmk", "remove_host_vmk", "set_vmk_service",
        "create_drs_rule", "set_drs_rule_enabled", "delete_drs_rule",
    }),
    "nsx": frozenset({
        "delete_segment", "delete_tier1_gateway", "delete_nat_rule",
        "delete_static_route", "delete_ip_pool",
    }),
    "nsx-security": frozenset({"delete_dfw_policy", "delete_dfw_rule", "delete_group"}),
    "avi": frozenset({
        "set_virtual_service_enabled", "disable_lb_pool_member", "restart_ako", "force_ako_sync",
        "upgrade_ako_config",
    }),
    "aria": frozenset({
        "start_resource_maintenance", "end_resource_maintenance", "acknowledge_alert",
        "cancel_alert", "delete_alert_definition", "delete_report",
    }),
    "vks": frozenset({
        "create_namespace", "delete_namespace", "create_tkc_cluster", "delete_tkc_cluster",
    }),
    "storage": frozenset({
        "enable_iscsi", "add_iscsi_target", "remove_iscsi_target",
        "rescan_storage",
    }),
    "vdi": frozenset({
        "reset_vdi_machine", "remove_vdi_machine", "set_vdi_machine_maintenance", "logoff_vdi_session",
        "disconnect_vdi_session", "cancel_vdi_task", "push_desktop_pool_image", "set_desktop_pool_enabled",
        "add_entitlement", "remove_entitlement",
    }),
    "privateai": frozenset({"assign_vgpu"}),
})

#: Former aliases for ``confirm`` on the gated tools, removed from the companions.
LEGACY_CONFIRM_KEYS = ("confirmed", "dry_run")

#: Every key through which a caller can say "act" or "hold" on a gated tool.
_DECISION_KEYS = ("confirm", *LEGACY_CONFIRM_KEYS)


def _skill_key(skill: str) -> str:
    return skill.removeprefix("vmware-")


def is_gated(skill: str, tool: str) -> bool:
    """True when ``skill.tool`` previews unless called with ``confirm=True``."""
    return tool in GATED_TOOLS.get(_skill_key(skill), frozenset())


def confirmed_params(skill: str, tool: str, params: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``params`` that makes a gated tool act.

    Adds ``confirm=True`` and drops the legacy aliases. An ungated tool gets an
    unchanged copy: ``confirm`` is not a parameter it has, and the companions
    publish closed schemas. Never mutates ``params``.
    """
    if not is_gated(skill, tool):
        return dict(params)
    kept = {k: v for k, v in params.items() if k not in LEGACY_CONFIRM_KEYS}
    return {**kept, "confirm": True}


def refuse_caller_decision(skill: str, tool: str, params: dict[str, Any], where: str) -> None:
    """Raise if caller-supplied params for a gated tool already decide act/hold.

    For templates that take steps from the caller (``baseline_remediate``):
    overwriting a caller's ``dry_run=True`` with ``confirm=True`` would turn the
    preview they asked for into a change, so the plan is refused instead.
    """
    if not is_gated(skill, tool):
        return
    decided = sorted(k for k in _DECISION_KEYS if k in params)
    if decided:
        raise ValueError(
            f"{where}: {skill}:{tool} params carry {decided}. Pilot passes confirm=True "
            "itself for this tool, after its approval step — that step is the human "
            "decision. Remove those keys and call create_workflow_from_template again; "
            "to only look at "
            f"what {tool} would change, call it directly without confirm."
        )
