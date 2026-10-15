"""The family's real write surface, read from each skill's own MCP annotations.

Measured 2026-08-30 by standing up every sibling repo's FastMCP server in its own
virtualenv and reading ``mcp.list_tools()`` — the same objects an MCP client sees
— rather than by grepping names or copying a documentation table::

    for each repo:  server.list_tools() -> [(name, annotations.readOnlyHint,
                                             annotations.destructiveHint), ...]

A tool is in ``FAMILY_WRITE_TOOLS`` when it declares ``readOnlyHint: False``, and
in ``FAMILY_SELF_DECLARED_DESTRUCTIVE`` when it also declares
``destructiveHint: True``. Those are the skills' own claims about themselves, so
they are the right yardstick for "did pilot's review notice?".

At the time of measurement: 327 tools across 14 skills, 120 of them writes, 41 of
those self-declared destructive.

**This snapshot is a control, not the gate.** ``review()`` does not read it and
must never read it: an inventory of siblings that pilot cannot import is a list
that goes stale silently, which is the defect this whole change exists to remove.
The gate is fail-closed instead — a tool pilot cannot classify is treated as
needing an approval gate — so a tool added to any sibling tomorrow is covered
without this file being touched. What this file buys is the ability to say how
much of a *known* real surface the gate covers, with names, instead of asserting
the property against three invented examples.

Consequently, staleness here is harmless: it can understate coverage, never
overstate it. Regenerate it when you want a fresh number, not to make a test go
green.
"""

from __future__ import annotations

#: skill → tools declaring ``readOnlyHint: False``.
FAMILY_WRITE_TOOLS: dict[str, frozenset[str]] = {
    "aiops": frozenset({
        "acknowledge_vcenter_alarm", "add_host_vmk", "attach_iso_to_vm", "batch_clone_vms",
        "batch_deploy_from_spec", "batch_linked_clone_vms", "add_cluster_host",
        "configure_cluster", "create_cluster", "delete_cluster", "remove_cluster_host",
        "convert_vm_to_template", "create_drs_rule", "create_dvs_portgroup",
        "delete_drs_rule", "deploy_linked_clone", "deploy_vm_from_ova",
        "deploy_vm_from_template", "remove_host_vmk", "reset_vcenter_alarm",
        "set_drs_rule_enabled", "set_vmk_service", "apply_vm_plan", "cancel_vm_ttl",
        "restore_vm_clean_slate", "clone_vm", "create_vm", "create_vm_plan", "create_vm_snapshot",
        "delete_vm", "delete_vm_snapshot", "download_vm_guest_file", "run_vm_guest_command",
        "run_vm_guest_command_with_output", "provision_vm_guest", "upload_vm_guest_file", "migrate_vm",
        "power_off_vm", "power_on_vm", "reconfigure_vm", "revert_vm_snapshot",
        "rollback_vm_plan", "set_vm_ttl",
    }),
    "aria": frozenset({
        "acknowledge_alert", "cancel_alert", "create_alert_definition",
        "delete_alert_definition", "delete_report", "generate_report",
        "set_alert_definition_state",
    }),
    "avi": frozenset({
        "upgrade_ako_config", "restart_ako", "force_ako_sync", "disable_lb_pool_member",
        "enable_lb_pool_member", "set_virtual_service_enabled",
    }),
    "debug": frozenset({
        "close_case", "grade_case", "record_case_hypothesis", "open_case", "record_case_gap",
        "submit_case_evidence", "record_case_timeline",
    }),
    "nsx": frozenset({
        "configure_tier0_bgp", "create_ip_pool", "create_nat_rule", "create_segment",
        "create_static_route", "create_tier1_gateway", "delete_ip_pool", "delete_nat_rule",
        "delete_segment", "delete_static_route", "delete_tier1_gateway", "update_segment",
        "update_tier1_gateway",
    }),
    "nsx-security": frozenset({
        "apply_vm_tag", "create_dfw_policy", "create_dfw_rule", "create_group",
        "delete_dfw_policy", "delete_dfw_rule", "delete_group", "remove_vm_tag",
        "run_traceflow", "update_dfw_policy", "update_dfw_rule",
    }),
    "pilot": frozenset({
        "approve", "cancel_workflow", "confirm_draft", "create_workflow", "design_workflow",
        "create_workflow_from_template", "rollback", "run_workflow", "update_draft",
    }),
    "privateai": frozenset({"assign_vgpu"}),
    "storage": frozenset({
        "add_iscsi_target", "enable_iscsi", "remove_iscsi_target",
        "rescan_storage",
    }),
    "vdi": frozenset({
        "add_entitlement", "remove_entitlement", "set_vdi_machine_maintenance", "remove_vdi_machine",
        "reset_vdi_machine", "push_desktop_pool_image", "set_desktop_pool_enabled", "disconnect_vdi_session",
        "logoff_vdi_session", "send_vdi_session_message", "cancel_vdi_task",
    }),
    "vks": frozenset({
        "create_namespace", "create_tkc_cluster", "delete_namespace", "delete_tkc_cluster",
        "issue_tkc_kubeconfig", "scale_tkc_cluster", "update_namespace", "upgrade_tkc_cluster",
    }),
}

#: skill → the subset above that also declares ``destructiveHint: True``.
FAMILY_SELF_DECLARED_DESTRUCTIVE: dict[str, frozenset[str]] = {
    "aiops": frozenset({
        "delete_cluster", "remove_cluster_host", "delete_drs_rule", "remove_host_vmk",
        "cancel_vm_ttl", "restore_vm_clean_slate", "delete_vm", "delete_vm_snapshot",
        "power_off_vm", "revert_vm_snapshot", "rollback_vm_plan", "set_vm_ttl",
    }),
    "aria": frozenset({"cancel_alert", "delete_alert_definition", "delete_report"}),
    "avi": frozenset({"restart_ako", "force_ako_sync", "disable_lb_pool_member", "set_virtual_service_enabled"}),
    "nsx": frozenset({
        "delete_ip_pool", "delete_nat_rule", "delete_segment", "delete_static_route",
        "delete_tier1_gateway",
    }),
    "nsx-security": frozenset({
        "delete_dfw_policy", "delete_dfw_rule", "delete_group", "remove_vm_tag",
    }),
    "pilot": frozenset({"rollback"}),
    "privateai": frozenset({"assign_vgpu"}),
    "storage": frozenset({"remove_iscsi_target"}),
    "vdi": frozenset({
        "remove_entitlement", "remove_vdi_machine", "reset_vdi_machine", "push_desktop_pool_image",
        "disconnect_vdi_session", "logoff_vdi_session", "cancel_vdi_task",
    }),
    "vks": frozenset({"delete_namespace", "delete_tkc_cluster", "issue_tkc_kubeconfig"}),
}


def iter_writes():
    """(skill, tool) for every write tool measured, sorted for stable failures."""
    for skill in sorted(FAMILY_WRITE_TOOLS):
        for tool in sorted(FAMILY_WRITE_TOOLS[skill]):
            yield skill, tool


def iter_self_declared_destructive():
    """(skill, tool) for every write tool the owning skill calls destructive."""
    for skill in sorted(FAMILY_SELF_DECLARED_DESTRUCTIVE):
        for tool in sorted(FAMILY_SELF_DECLARED_DESTRUCTIVE[skill]):
            yield skill, tool
