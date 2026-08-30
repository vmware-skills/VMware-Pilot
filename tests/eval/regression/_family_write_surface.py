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
        "batch_deploy_from_spec", "batch_linked_clone_vms", "cluster_add_host",
        "cluster_configure", "cluster_create", "cluster_delete", "cluster_remove_host",
        "convert_vm_to_template", "create_drs_rule", "create_dvs_portgroup",
        "delete_drs_rule", "deploy_linked_clone", "deploy_vm_from_ova",
        "deploy_vm_from_template", "remove_host_vmk", "reset_vcenter_alarm",
        "set_drs_rule_enabled", "set_vmk_service", "vm_apply_plan", "vm_cancel_ttl",
        "vm_clean_slate", "vm_clone", "vm_create", "vm_create_plan", "vm_create_snapshot",
        "vm_delete", "vm_delete_snapshot", "vm_guest_download", "vm_guest_exec",
        "vm_guest_exec_output", "vm_guest_provision", "vm_guest_upload", "vm_migrate",
        "vm_power_off", "vm_power_on", "vm_reconfigure", "vm_revert_snapshot",
        "vm_rollback_plan", "vm_set_ttl",
    }),
    "aria": frozenset({
        "acknowledge_alert", "cancel_alert", "create_alert_definition",
        "delete_alert_definition", "delete_report", "generate_report",
        "set_alert_definition_state",
    }),
    "avi": frozenset({
        "ako_config_upgrade", "ako_restart", "ako_sync_force", "pool_member_disable",
        "pool_member_enable", "vs_toggle",
    }),
    "debug": frozenset({
        "case_close", "case_grade", "case_hypotheses", "case_open", "case_record_gap",
        "case_submit_evidence", "case_timeline",
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
        "plan_workflow", "rollback", "run_workflow", "update_draft",
    }),
    "privateai": frozenset({"vgpu_assign"}),
    "storage": frozenset({
        "storage_iscsi_add_target", "storage_iscsi_enable", "storage_iscsi_remove_target",
        "storage_rescan",
    }),
    "vdi": frozenset({
        "entitlement_add", "entitlement_remove", "machine_maintenance", "machine_remove",
        "machine_reset", "pool_push_image", "pool_set_enabled", "session_disconnect",
        "session_logoff", "session_send_message", "task_cancel",
    }),
    "vks": frozenset({
        "create_namespace", "create_tkc_cluster", "delete_namespace", "delete_tkc_cluster",
        "get_tkc_kubeconfig", "scale_tkc_cluster", "update_namespace", "upgrade_tkc_cluster",
    }),
}

#: skill → the subset above that also declares ``destructiveHint: True``.
FAMILY_SELF_DECLARED_DESTRUCTIVE: dict[str, frozenset[str]] = {
    "aiops": frozenset({
        "cluster_delete", "cluster_remove_host", "delete_drs_rule", "remove_host_vmk",
        "vm_cancel_ttl", "vm_clean_slate", "vm_delete", "vm_delete_snapshot",
        "vm_power_off", "vm_revert_snapshot", "vm_rollback_plan", "vm_set_ttl",
    }),
    "aria": frozenset({"cancel_alert", "delete_alert_definition", "delete_report"}),
    "avi": frozenset({"ako_restart", "ako_sync_force", "pool_member_disable", "vs_toggle"}),
    "nsx": frozenset({
        "delete_ip_pool", "delete_nat_rule", "delete_segment", "delete_static_route",
        "delete_tier1_gateway",
    }),
    "nsx-security": frozenset({
        "delete_dfw_policy", "delete_dfw_rule", "delete_group", "remove_vm_tag",
    }),
    "pilot": frozenset({"rollback"}),
    "privateai": frozenset({"vgpu_assign"}),
    "storage": frozenset({"storage_iscsi_remove_target"}),
    "vdi": frozenset({
        "entitlement_remove", "machine_remove", "machine_reset", "pool_push_image",
        "session_disconnect", "session_logoff", "task_cancel",
    }),
    "vks": frozenset({"delete_namespace", "delete_tkc_cluster", "get_tkc_kubeconfig"}),
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
