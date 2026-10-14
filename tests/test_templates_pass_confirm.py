"""Every built-in step that calls a gated companion tool passes confirm=True.

Destructive companion tools preview on a bare call (HLD §7, 2026-09-19). A
template step runs after Pilot's own ``approve`` gate — that gate is the human
decision — so the step must say ``confirm=True`` or it changes nothing (and the
executor now records that as a failure). The legacy spellings ``confirmed`` /
``dry_run`` are deprecated aliases on those tools and must not be sent.

The gated list here is written out from the assignment, independently of the
implementation's own table, so a tool dropped from that table turns this red.
"""

from __future__ import annotations

import pytest

from tests.eval.regression.test_template_steps_name_real_tools import _ARGS
from vmware_pilot.templates import BUILTIN_TEMPLATES
from vmware_pilot.templates._gated import GATED_TOOLS, confirmed_params

_EXPECTED_GATED = {
    "aiops": {
        "power_off_vm", "migrate_vm", "revert_vm_snapshot", "delete_vm_snapshot",
        "run_vm_guest_command", "run_vm_guest_command_with_output", "upload_vm_guest_file", "provision_vm_guest",
        "delete_cluster", "remove_cluster_host", "set_vm_ttl", "restore_vm_clean_slate",
        "rollback_vm_plan", "apply_vm_plan", "delete_vm",
        "create_dvs_portgroup", "add_host_vmk", "remove_host_vmk", "set_vmk_service",
        "create_drs_rule", "set_drs_rule_enabled", "delete_drs_rule",
    },
    "nsx": {"delete_segment", "delete_tier1_gateway", "delete_nat_rule",
            "delete_static_route", "delete_ip_pool"},
    "nsx-security": {"delete_dfw_policy", "delete_dfw_rule", "delete_group"},
    "avi": {"set_virtual_service_enabled", "disable_lb_pool_member", "restart_ako", "force_ako_sync",
            "upgrade_ako_config"},
    "aria": {"start_resource_maintenance", "end_resource_maintenance", "acknowledge_alert",
             "cancel_alert", "delete_alert_definition", "delete_report"},
    "vks": {"create_namespace", "delete_namespace", "create_tkc_cluster", "delete_tkc_cluster"},
    "storage": {"enable_iscsi", "add_iscsi_target",
                "remove_iscsi_target", "rescan_storage"},
    "vdi": {"reset_vdi_machine", "remove_vdi_machine", "set_vdi_machine_maintenance", "logoff_vdi_session",
            "disconnect_vdi_session", "cancel_vdi_task", "push_desktop_pool_image", "set_desktop_pool_enabled",
            "add_entitlement", "remove_entitlement"},
    "privateai": {"assign_vgpu"},
}

_LEGACY = ("confirmed", "dry_run")


def _calls():
    """Yield (where, skill, tool, params) for every forward and rollback call."""
    for name, variants in _ARGS.items():
        for kwargs in variants:
            wf = BUILTIN_TEMPLATES[name](**kwargs)
            for s in wf.steps:
                yield f"{name} step {s.index}", s.skill, s.tool, s.params
                if s.rollback_tool:
                    yield f"{name} step {s.index} rollback", s.skill, s.rollback_tool, \
                        s.rollback_params


@pytest.mark.unit
def test_gated_table_matches_the_assignment():
    assert {k: set(v) for k, v in GATED_TOOLS.items()} == _EXPECTED_GATED


@pytest.mark.unit
def test_every_gated_call_passes_confirm_true_and_no_legacy_alias():
    problems, gated_seen = [], 0
    for where, skill, tool, params in _calls():
        if tool not in _EXPECTED_GATED.get(skill, set()):
            continue
        gated_seen += 1
        if params.get("confirm") is not True:
            problems.append(f"{where}: {skill}:{tool} does not pass confirm=True")
        problems += [f"{where}: {skill}:{tool} still passes legacy {k!r}"
                     for k in _LEGACY if k in params]
    # Positive control: the built-ins call well over a dozen gated tools.
    assert gated_seen >= 15, f"only {gated_seen} gated calls found — the scan is not scanning"
    assert not problems, "\n".join(problems)


@pytest.mark.unit
def test_ungated_calls_are_not_given_confirm():
    """confirm is not a parameter of every tool — closed schemas reject it."""
    stray = [f"{where}: {skill}:{tool}" for where, skill, tool, params in _calls()
             if "confirm" in params and tool not in _EXPECTED_GATED.get(skill, set())]
    assert not stray, stray


@pytest.mark.unit
def test_confirmed_params_does_not_mutate_and_drops_legacy():
    original = {"name": "ns", "confirmed": True, "dry_run": False}
    out = confirmed_params("vks", "delete_namespace", original)
    assert out == {"name": "ns", "confirm": True}
    assert original == {"name": "ns", "confirmed": True, "dry_run": False}
    # An ungated tool is returned as a copy, untouched.
    assert confirmed_params("vks", "get_namespace", {"name": "ns"}) == {"name": "ns"}


@pytest.mark.unit
def test_baseline_remediate_confirms_a_gated_drift_item():
    wf = BUILTIN_TEMPLATES["baseline_remediate"](drift_items=[
        {"resource": "r", "skill": "aiops", "action": "fix", "tool": "power_off_vm",
         "params": {"vm_name": "r"}, "rollback_tool": "power_on_vm",
         "rollback_params": {"vm_name": "r"}},
    ])
    fix = next(s for s in wf.steps if s.tool == "power_off_vm")
    assert fix.params == {"vm_name": "r", "confirm": True}
    assert fix.rollback_params == {"vm_name": "r"}  # power_on_vm is not gated


@pytest.mark.unit
@pytest.mark.parametrize("hold", [{"dry_run": True}, {"confirmed": False}, {"confirm": False}])
def test_baseline_remediate_refuses_a_caller_hold_it_would_override(hold):
    """Overwriting a caller's explicit 'do not act' with confirm=True would turn
    their preview into a change. Refuse at plan time instead."""
    with pytest.raises(ValueError, match="confirm"):
        BUILTIN_TEMPLATES["baseline_remediate"](drift_items=[
            {"resource": "r", "skill": "nsx", "action": "fix", "tool": "delete_segment",
             "params": {"segment_id": "s", **hold}},
        ])


@pytest.mark.unit
def test_no_template_step_passes_confirm_true_before_the_first_approval():
    """confirm=True is Pilot saying "a human already decided". A step that sends
    it before any approval step hands the agent a change nobody approved (on the
    MCP server it becomes a pending_dispatch carrying confirm=True)."""
    problems, gated_steps = [], 0
    for name, variants in _ARGS.items():
        for kwargs in variants:
            wf = BUILTIN_TEMPLATES[name](**kwargs)
            approved = False
            for s in sorted(wf.steps, key=lambda st: st.index):
                if s.action == "require_approval":
                    approved = True
                    continue
                if s.params.get("confirm") is True:
                    gated_steps += 1
                    if not approved:
                        problems.append(f"{name} step {s.index}: {s.skill}:{s.tool} "
                                        "passes confirm=True before any approval step")
    # Positive control: many built-in steps carry confirm=True.
    assert gated_steps >= 15, f"only {gated_steps} confirm=True steps found"
    assert not problems, "\n".join(problems)
