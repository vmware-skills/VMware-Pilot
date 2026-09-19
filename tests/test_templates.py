"""Tests for workflow templates."""

import pytest

from vmware_pilot.models import WorkflowStep
from vmware_pilot.templates import (
    BUILTIN_TEMPLATES as TEMPLATES,
    baseline_audit,
    baseline_capture,
    baseline_remediate,
    capacity_expansion,
    clone_and_test,
    compliance_scan,
    disaster_recovery,
    incident_response,
    investigate_alert,
    network_segment_setup,
    parallel_group,
    patch_deployment,
    plan_and_approve,
    rolling_restart,
    storage_expansion,
    vks_cluster_deploy,
)


@pytest.mark.unit
class TestCloneAndTest:
    def test_creates_workflow(self):
        wf = clone_and_test(
            target_vm="db01",
            change_spec={"memory_mb": 32768},
            monitor_minutes=5,
            target="vcenter1",
        )
        assert wf.workflow_type == "clone_and_test"
        assert wf.state.value == "pending"
        assert len(wf.steps) == 6
        assert wf.params["target_vm"] == "db01"
        assert wf.params["staging_vm"] == "db01-staging"

    def test_step_actions(self):
        wf = clone_and_test(target_vm="web01", change_spec={"cpu": 4})
        actions = [s.action for s in wf.steps]
        assert actions == [
            "clone", "apply_changes", "monitor",
            "require_approval", "apply_to_production", "cleanup",
        ]

    def test_has_approval_gate(self):
        wf = clone_and_test(target_vm="app01", change_spec={"cpu": 4})
        approval_steps = [s for s in wf.steps if s.action == "require_approval"]
        assert len(approval_steps) == 1

    def test_an_empty_change_spec_is_refused_at_plan_time(self):
        """It used to build a vm_guest_exec step with no command at all."""
        with pytest.raises(ValueError, match="change_spec"):
            clone_and_test(target_vm="app01", change_spec={})


#: A guest change_spec as vmware-aiops vm_guest_exec takes it, minus vm_name/target.
_GUEST_SPEC = {"command": "/usr/bin/apt-get", "arguments": "-y upgrade",
               "username": "ops", "password": "pw"}


@pytest.mark.unit
class TestCloneAndTestMapsOntoTheRealAiopsTools:
    """Step params must be ones the aiops tool accepts, checked when planning.

    A mistake here used to surface at dispatch — after the staging clone had
    already been created — as a schema rejection from aiops.
    """

    #: vmware-aiops vm_reconfigure(vm_name, cpu, memory_mb, target) — no memory_gb.
    RECONFIGURE_PARAMS = {"vm_name", "cpu", "memory_mb", "target"}
    #: vmware-aiops vm_guest_exec(vm_name, command, username, arguments,
    #: password, working_directory, target, confirm). confirm since 2026-09-19:
    #: the tool previews without it, and the step runs after Pilot's approval.
    GUEST_EXEC_PARAMS = {"vm_name", "command", "username", "arguments", "password",
                         "working_directory", "target", "confirm"}

    def _apply_steps(self, wf):
        return [s for s in wf.steps if s.action in ("apply_changes", "apply_to_production")]

    def test_memory_gb_is_sent_as_memory_mb(self):
        wf = clone_and_test(target_vm="db01", change_spec={"memory_gb": 32, "cpu": 4})
        for step in self._apply_steps(wf):
            assert step.tool == "vm_reconfigure"
            assert set(step.params) <= self.RECONFIGURE_PARAMS, step.params
            assert step.params["memory_mb"] == 32768
            assert step.params["cpu"] == 4

    def test_memory_gb_and_memory_mb_together_is_refused(self):
        with pytest.raises(ValueError, match="memory_gb"):
            clone_and_test(target_vm="db01", change_spec={"memory_gb": 32, "memory_mb": 1024})

    def test_a_fractional_megabyte_is_refused(self):
        with pytest.raises(ValueError, match="memory_mb"):
            clone_and_test(target_vm="db01", change_spec={"memory_gb": 0.0001})

    def test_a_reconfigure_spec_with_a_foreign_key_is_refused(self):
        with pytest.raises(ValueError, match="command"):
            clone_and_test(target_vm="db01", change_spec={"cpu": 4, "command": "/bin/true"})

    def test_the_callers_change_spec_is_not_mutated(self):
        spec = {"memory_gb": 8}
        clone_and_test(target_vm="db01", change_spec=spec)
        assert spec == {"memory_gb": 8}

    def test_a_guest_spec_builds_guest_steps_with_the_given_account(self):
        wf = clone_and_test(target_vm="db01", change_spec=_GUEST_SPEC)
        for step in self._apply_steps(wf):
            assert step.tool == "vm_guest_exec"
            assert set(step.params) <= self.GUEST_EXEC_PARAMS, step.params
            assert step.params["username"] == "ops"

    def test_a_guest_spec_without_a_username_is_refused(self):
        """vmware-aiops has no root default any more; neither may pilot."""
        spec = {k: v for k, v in _GUEST_SPEC.items() if k != "username"}
        with pytest.raises(ValueError, match="username") as err:
            clone_and_test(target_vm="db01", change_spec=spec)
        assert "root" in str(err.value), "say that there is no default account"

    def test_a_guest_spec_with_a_blank_username_is_refused(self):
        with pytest.raises(ValueError, match="username"):
            clone_and_test(target_vm="db01", change_spec={**_GUEST_SPEC, "username": " "})

    def test_a_guest_spec_without_a_command_is_refused(self):
        spec = {k: v for k, v in _GUEST_SPEC.items() if k != "command"}
        with pytest.raises(ValueError, match="command"):
            clone_and_test(target_vm="db01", change_spec=spec)

    def test_a_guest_spec_with_an_unknown_key_is_refused(self):
        with pytest.raises(ValueError, match="script"):
            clone_and_test(target_vm="db01", change_spec={**_GUEST_SPEC, "script": "x"})

    def test_the_staging_guest_command_is_behind_an_approval_gate(self):
        """vm_guest_exec is destructive; the staging run needs its own gate."""
        wf = clone_and_test(target_vm="db01", change_spec=_GUEST_SPEC)
        first_guest = next(s.index for s in wf.steps if s.tool == "vm_guest_exec")
        assert any(
            s.action == "require_approval" and s.index < first_guest for s in wf.steps
        )

    def test_the_reconfigure_path_keeps_its_six_steps(self):
        """Control: only the guest path grew a gate."""
        wf = clone_and_test(target_vm="db01", change_spec={"cpu": 4})
        assert len(wf.steps) == 6

    def test_plan_workflow_returns_the_teaching_error(self, tmp_path, monkeypatch):
        """Refused at plan time: nothing is persisted, so no clone ever exists."""
        import vmware_pilot.mcp_server.server as server
        from vmware_pilot.executor import WorkflowExecutor
        from vmware_pilot.models import WorkflowStore

        store = WorkflowStore(tmp_path / "wf.db")
        monkeypatch.setattr(server, "_store", store)
        monkeypatch.setattr(server, "_executor", WorkflowExecutor(store))
        spec = {k: v for k, v in _GUEST_SPEC.items() if k != "username"}
        result = server.plan_workflow(
            "clone_and_test", {"target_vm": "db01", "change_spec": spec}
        )
        assert "username" in result["error"]
        assert store.list_all() == []


@pytest.mark.unit
class TestIncidentResponse:
    def test_creates_workflow(self):
        wf = incident_response(alert_entity="esxi-01", alert_name="HostMemoryUsage")
        assert wf.workflow_type == "incident_response"
        assert len(wf.steps) == 4
        assert wf.params["alert_entity"] == "esxi-01"

    def test_has_approval_gate(self):
        wf = incident_response(alert_entity="vm01", alert_name="CpuHigh")
        approval = [s for s in wf.steps if s.action == "require_approval"]
        assert len(approval) == 1


@pytest.mark.unit
class TestPlanAndApprove:
    def test_creates_workflow(self):
        ops = [
            {"action": "power_off", "vm_name": "db01"},
            {"action": "revert_snapshot", "vm_name": "db01", "snapshot_name": "baseline"},
            {"action": "power_on", "vm_name": "db01"},
        ]
        wf = plan_and_approve(operations=ops, target="vcenter1")
        assert wf.workflow_type == "plan_and_approve"
        assert wf.state.value == "pending"
        assert len(wf.steps) == 3
        assert wf.params["vm_names"] == ["db01"]

    def test_step_actions(self):
        ops = [{"action": "power_on", "vm_name": "web01"}]
        wf = plan_and_approve(operations=ops)
        actions = [s.action for s in wf.steps]
        assert actions == ["create_plan", "require_approval", "apply_plan"]

    def test_has_approval_gate(self):
        wf = plan_and_approve(operations=[{"action": "power_on", "vm_name": "x"}])
        approval = [s for s in wf.steps if s.action == "require_approval"]
        assert len(approval) == 1

    def test_rollback_on_apply_step(self):
        wf = plan_and_approve(operations=[{"action": "power_on", "vm_name": "x"}])
        apply_step = wf.steps[2]
        assert apply_step.rollback_tool == "vm_rollback_plan"

    def test_custom_description(self):
        wf = plan_and_approve(
            operations=[{"action": "power_on", "vm_name": "x"}],
            description="Restart db cluster",
        )
        assert "Restart db cluster" in wf.steps[1].params["message"]

    def test_multi_vm_names(self):
        ops = [
            {"action": "power_off", "vm_name": "db01"},
            {"action": "power_off", "vm_name": "db02"},
            {"action": "power_off", "vm_name": "db03"},
        ]
        wf = plan_and_approve(operations=ops)
        assert len(wf.params["vm_names"]) == 3


@pytest.mark.unit
class TestComplianceScan:
    def test_creates_workflow(self):
        wf = compliance_scan(target="vcenter1")
        assert wf.workflow_type == "compliance_scan"
        assert wf.state.value == "pending"
        assert len(wf.steps) == 3  # alarms + capacity + anomalies

    def test_no_approval_gate(self):
        wf = compliance_scan()
        approval = [s for s in wf.steps if s.action == "require_approval"]
        assert len(approval) == 0

    def test_skip_alarms(self):
        wf = compliance_scan(check_alarms=False)
        actions = [s.action for s in wf.steps]
        assert "check_alarms" not in actions
        assert "check_capacity" in actions

    def test_skip_capacity(self):
        wf = compliance_scan(check_capacity=False)
        actions = [s.action for s in wf.steps]
        assert "check_capacity" not in actions
        assert "check_alarms" in actions

    def test_uses_aria_and_monitor(self):
        wf = compliance_scan()
        skills = {s.skill for s in wf.steps}
        assert "monitor" in skills
        assert "aria" in skills

    def test_capacity_uses_aria_only_with_a_cluster_id(self):
        # Aria's get_capacity_overview requires cluster_id; without one the step
        # reads monitor's per-cluster rollup rather than a call Aria refuses.
        with_id = [s for s in compliance_scan(cluster_id="c-1").steps
                   if s.action == "check_capacity"][0]
        assert (with_id.skill, with_id.tool) == ("aria", "get_capacity_overview")
        assert with_id.params["cluster_id"] == "c-1"
        without = [s for s in compliance_scan().steps if s.action == "check_capacity"][0]
        assert (without.skill, without.tool) == ("monitor", "cluster_health_summary")


@pytest.mark.unit
class TestNetworkSegmentSetup:
    def test_creates_workflow(self):
        wf = network_segment_setup(
            segment_id="app-seg", display_name="App Segment",
            subnet="10.10.1.1/24", transport_zone_path="/infra/sites/default/tz-overlay",
            tier1_id="app-t1", nat_source="10.10.1.0/24", nat_translated="172.16.0.10",
        )
        assert wf.workflow_type == "network_segment_setup"
        assert len(wf.steps) >= 4  # gateway + segment + nat + approve + verify

    def test_has_approval_and_rollback(self):
        wf = network_segment_setup(
            segment_id="seg1", display_name="S", subnet="10.0.0.1/24",
            transport_zone_path="/tz", tier1_id="t1",
        )
        assert any(s.action == "require_approval" for s in wf.steps)
        assert any(s.rollback_tool for s in wf.steps)

    def test_minimal_without_nat(self):
        wf = network_segment_setup(
            segment_id="seg1", display_name="S", subnet="10.0.0.1/24",
            transport_zone_path="/tz",
        )
        actions = [s.action for s in wf.steps]
        assert "create_nat" not in actions
        assert "create_segment" in actions


@pytest.mark.unit
class TestVksClusterDeploy:
    def test_creates_workflow(self):
        wf = vks_cluster_deploy(
            namespace_name="dev", cluster_id="domain-c1",
            storage_policy="vsan-default", tkc_name="dev-tkc", k8s_version="v1.28",
        )
        assert wf.workflow_type == "vks_cluster_deploy"
        assert len(wf.steps) == 5

    def test_has_approval(self):
        wf = vks_cluster_deploy("ns", "c1", "pol", "tkc1", "v1.28")
        assert any(s.action == "require_approval" for s in wf.steps)

    def test_uses_vks_skill(self):
        wf = vks_cluster_deploy("ns", "c1", "pol", "tkc1", "v1.28")
        skills = {s.skill for s in wf.steps}
        assert "vks" in skills


@pytest.mark.unit
class TestRollingRestart:
    def test_creates_workflow(self):
        wf = rolling_restart(vm_names=["db01", "db02", "db03"])
        assert wf.workflow_type == "rolling_restart"
        # pre_check + approve + 3*(off+on+check) = 11
        assert len(wf.steps) == 11

    def test_has_rollback_per_vm(self):
        wf = rolling_restart(vm_names=["vm1"])
        power_off_steps = [s for s in wf.steps if "power_off" in s.action]
        assert all(s.rollback_tool == "vm_power_on" for s in power_off_steps)

    def test_scales_with_vm_count(self):
        wf2 = rolling_restart(vm_names=["a", "b"])
        wf5 = rolling_restart(vm_names=["a", "b", "c", "d", "e"])
        assert len(wf5.steps) > len(wf2.steps)


@pytest.mark.unit
class TestCapacityExpansion:
    def test_creates_workflow(self):
        wf = capacity_expansion(vm_name="db01", cpu=8, memory_mb=32768)
        assert wf.workflow_type == "capacity_expansion"
        assert len(wf.steps) == 5

    def test_uses_aria_and_aiops(self):
        wf = capacity_expansion(vm_name="x", cpu=4)
        skills = {s.skill for s in wf.steps}
        assert "aria" in skills
        assert "aiops" in skills


@pytest.mark.unit
class TestDisasterRecovery:
    def test_creates_workflow(self):
        wf = disaster_recovery(vm_name="prod-db", snapshot_name="last-good")
        assert wf.workflow_type == "disaster_recovery"
        assert len(wf.steps) == 5

    def test_approval_first(self):
        wf = disaster_recovery(vm_name="x")
        assert wf.steps[0].action == "require_approval"

    def test_uses_nsx_for_network_verify(self):
        wf = disaster_recovery(vm_name="x")
        skills = {s.skill for s in wf.steps}
        assert "nsx" in skills


@pytest.mark.unit
class TestPatchDeployment:
    def test_creates_workflow(self):
        wf = patch_deployment(
            vm_names=["web01", "web02"],
            patch_local_path="/tmp/patch.sh",
            patch_guest_path="/tmp/patch.sh",
            install_command="bash /tmp/patch.sh",
            username="ops",
        )
        assert wf.workflow_type == "patch_deployment"
        # approve + 2*(upload+install+verify) = 7
        assert len(wf.steps) == 7

    def test_scales_with_vm_count(self):
        wf = patch_deployment(["a", "b", "c"], "/p", "/p", "bash /p", "ops")
        assert len(wf.steps) == 10  # approve + 3*3

    def test_username_has_no_default(self):
        """vmware-aiops made the guest account required on 2026-09-11 (no root)."""
        import inspect

        param = inspect.signature(patch_deployment).parameters["username"]
        assert param.default is inspect.Parameter.empty

    def test_a_blank_username_is_refused_at_plan_time(self):
        with pytest.raises(ValueError, match="username"):
            patch_deployment(["a"], "/p", "/p", "bash /p", username="")

    def test_plan_workflow_names_the_missing_username(self, tmp_path, monkeypatch):
        """Omitting it is the likely mistake (it used to default), so the MCP
        answer must name it — not ``TypeError: operation failed.``"""
        import vmware_pilot.mcp_server.server as server
        from vmware_pilot.executor import WorkflowExecutor
        from vmware_pilot.models import WorkflowStore

        store = WorkflowStore(tmp_path / "wf.db")
        monkeypatch.setattr(server, "_store", store)
        monkeypatch.setattr(server, "_executor", WorkflowExecutor(store))
        result = server.plan_workflow("patch_deployment", {
            "vm_names": ["a"], "patch_local_path": "/p", "patch_guest_path": "/p",
            "install_command": "bash /p",
        })
        assert "username" in result["error"]
        assert store.list_all() == []

    def test_every_guest_step_carries_the_given_account(self):
        wf = patch_deployment(["a", "b"], "/p", "/p", "bash /p", username="ops")
        guest = [s for s in wf.steps if s.tool.startswith("vm_guest_")]
        assert len(guest) == 4
        assert {s.params["username"] for s in guest} == {"ops"}


@pytest.mark.unit
class TestStorageExpansion:
    def test_creates_workflow(self):
        wf = storage_expansion(host_name="esxi-01", iscsi_address="10.0.0.100")
        assert wf.workflow_type == "storage_expansion"
        assert len(wf.steps) == 6

    def test_has_rollback_on_add(self):
        wf = storage_expansion(host_name="h1", iscsi_address="10.0.0.1")
        add_step = [s for s in wf.steps if s.action == "add_iscsi_target"][0]
        assert add_step.rollback_tool == "storage_iscsi_remove_target"

    def test_uses_storage_skill(self):
        wf = storage_expansion(host_name="h1", iscsi_address="10.0.0.1")
        skills = {s.skill for s in wf.steps}
        assert "storage" in skills


@pytest.mark.unit
class TestBaselineCapture:
    def test_creates_workflow(self):
        wf = baseline_capture(target="vcenter1")
        assert wf.workflow_type == "baseline_capture"
        assert len(wf.steps) == 5  # vms + hosts + network + storage + alarms

    def test_selective_capture(self):
        wf = baseline_capture(include_vms=True, include_hosts=False,
                              include_network=False, include_storage=False, include_alarms=False)
        assert len(wf.steps) == 1
        assert wf.steps[0].action == "capture_vms"

    def test_uses_multiple_skills(self):
        wf = baseline_capture()
        skills = {s.skill for s in wf.steps}
        assert "monitor" in skills
        assert "nsx" in skills
        assert "storage" in skills

    def test_no_approval_gate(self):
        wf = baseline_capture()
        assert not any(s.action == "require_approval" for s in wf.steps)

    def test_custom_name(self):
        wf = baseline_capture(baseline_name="prod-golden")
        assert wf.params["baseline_name"] == "prod-golden"


@pytest.mark.unit
class TestBaselineAudit:
    def test_creates_workflow(self):
        wf = baseline_audit(baseline_name="prod-golden", target="vc1")
        assert wf.workflow_type == "baseline_audit"
        assert wf.params["baseline_name"] == "prod-golden"

    def test_includes_anomaly_check(self):
        wf = baseline_audit()
        actions = [s.action for s in wf.steps]
        assert "check_anomalies" in actions

    def test_uses_aria(self):
        wf = baseline_audit()
        skills = {s.skill for s in wf.steps}
        assert "aria" in skills


@pytest.mark.unit
class TestBaselineRemediate:
    def test_creates_workflow(self):
        drifts = [
            {"resource": "vm-web01", "skill": "aiops", "tool": "vm_power_on", "params": {"vm_name": "vm-web01"}},
            {"resource": "seg-app", "skill": "nsx", "tool": "create_segment", "params": {"segment_id": "seg-app"}},
        ]
        wf = baseline_remediate(drift_items=drifts)
        assert wf.workflow_type == "baseline_remediate"
        # pre_check + approve + 2 fixes + post_verify = 5
        assert len(wf.steps) == 5

    def test_has_approval(self):
        wf = baseline_remediate(drift_items=[{"resource": "x", "skill": "aiops", "tool": "t", "params": {}}])
        assert any(s.action == "require_approval" for s in wf.steps)

    def test_scales_with_drift_count(self):
        drifts = [{"resource": f"r{i}", "skill": "aiops", "tool": "t", "params": {}} for i in range(5)]
        wf = baseline_remediate(drift_items=drifts)
        # pre_check + approve + 5 fixes + post_verify = 8
        assert len(wf.steps) == 8

    def test_with_rollback(self):
        drifts = [{"resource": "seg1", "skill": "nsx", "tool": "create_segment",
                   "params": {}, "rollback_tool": "delete_segment", "rollback_params": {"segment_id": "seg1"}}]
        wf = baseline_remediate(drift_items=drifts)
        fix_step = [s for s in wf.steps if s.action.startswith("fix_")][0]
        assert fix_step.rollback_tool == "delete_segment"


@pytest.mark.unit
class TestTemplateRegistry:
    def test_all_templates_registered(self):
        expected = [
            "clone_and_test", "incident_response", "investigate_alert",
            "plan_and_approve", "compliance_scan",
            "network_segment_setup", "vks_cluster_deploy", "rolling_restart",
            "capacity_expansion", "disaster_recovery", "patch_deployment", "storage_expansion",
            "baseline_capture", "baseline_audit", "baseline_remediate",
        ]
        for name in expected:
            assert name in TEMPLATES, f"{name} not registered"

    def test_templates_callable(self):
        for name, fn in TEMPLATES.items():
            assert callable(fn)

    def test_total_count(self):
        assert len(TEMPLATES) == 15


@pytest.mark.unit
class TestInvestigateAlert:
    def test_round1_only_when_no_deep_dive(self):
        wf = investigate_alert(alert_entity="vm-prod-01", alert_name="High CPU")
        # 3 gather + 1 checkpoint
        assert len(wf.steps) == 4
        assert wf.workflow_type == "investigate_alert"

    def test_round1_steps_share_group_id(self):
        wf = investigate_alert(alert_entity="vm-prod-01")
        gather = [s for s in wf.steps if s.action.startswith("gather_")]
        assert len(gather) == 3
        assert all(s.group_id == "round1-gather" for s in gather)

    def test_first_checkpoint_mentions_four_criteria(self):
        wf = investigate_alert(alert_entity="vm-prod-01")
        approval = [s for s in wf.steps if s.action == "require_approval"][0]
        msg = approval.params["message"].lower()
        assert "falsifiability" in msg
        assert "sufficiency" in msg
        assert "necessity" in msg
        assert "mechanism" in msg

    def test_deep_dive_adds_second_round(self):
        wf = investigate_alert(alert_entity="vm-prod-01", deep_dive=True)
        # 3 round1 gather + 1 checkpoint + 3 round2 gather + 1 checkpoint
        assert len(wf.steps) == 8
        round2 = [s for s in wf.steps if s.group_id == "round2-gather"]
        assert len(round2) == 3
        approvals = [s for s in wf.steps if s.action == "require_approval"]
        assert len(approvals) == 2

    def test_deep_dive_round2_has_distinct_group_id(self):
        wf = investigate_alert(alert_entity="vm-prod-01", deep_dive=True)
        groups = {s.group_id for s in wf.steps if s.group_id}
        assert groups == {"round1-gather", "round2-gather"}

    def test_target_propagates_to_steps(self):
        wf = investigate_alert(alert_entity="vm-prod-01", target="prod-vc")
        for s in wf.steps:
            if s.action.startswith("gather_"):
                assert s.params.get("target") == "prod-vc"

    def test_state_starts_pending(self):
        wf = investigate_alert(alert_entity="vm-prod-01")
        assert wf.state.value == "pending"

    def test_aria_target_applies_to_aria_steps_only(self):
        wf = investigate_alert(alert_entity="192.168.60.15", deep_dive=True,
                               target="home-vcenter", aria_target="home-aria")
        for s in wf.steps:
            if s.action.startswith("gather_"):
                want = "home-aria" if s.skill == "aria" else "home-vcenter"
                assert s.params["target"] == want, (s.action, s.params)

    def test_checkpoint_tells_the_agent_to_filter_by_entity(self):
        wf = investigate_alert(alert_entity="192.168.60.15")
        approval = [s for s in wf.steps if s.action == "require_approval"][0]
        assert "192.168.60.15" in approval.params["message"]


@pytest.mark.unit
class TestParallelGroup:
    def _make_step(self, idx: int, tool: str) -> WorkflowStep:
        return WorkflowStep(
            index=idx, action="execute", skill="vmware-monitor",
            tool=tool, params={},
        )

    def test_tags_all_steps_with_group_id(self):
        steps = [self._make_step(0, "list_alarms"), self._make_step(1, "list_events")]
        result = parallel_group("gather", steps)
        assert all(s.group_id == "gather" for s in result)

    def test_returns_same_list(self):
        steps = [self._make_step(0, "list_alarms")]
        assert parallel_group("g", steps) is steps

    def test_rejects_empty_group_id(self):
        with pytest.raises(ValueError):
            parallel_group("", [self._make_step(0, "list_alarms")])

    def test_default_group_id_is_empty(self):
        s = self._make_step(0, "list_alarms")
        assert s.group_id == ""


@pytest.mark.unit
def test_custom_template_shadowing_builtin_warns(monkeypatch, caplog):
    """Fix #10: a custom YAML overriding a built-in must warn loudly."""
    import logging

    from vmware_pilot import custom_loader, templates

    def _fake_custom():
        return {"clone_and_test": lambda **kw: None, "my_custom": lambda **kw: None}

    monkeypatch.setattr(custom_loader, "load_custom_templates", _fake_custom)
    with caplog.at_level(logging.WARNING, logger="vmware-pilot.templates"):
        all_templates = templates.get_all_templates()

    assert "clone_and_test" in all_templates
    assert "my_custom" in all_templates
    assert any("shadows built-in" in r.message for r in caplog.records)


@pytest.mark.unit
def test_no_shadow_no_warning(monkeypatch, caplog):
    import logging

    from vmware_pilot import custom_loader, templates

    monkeypatch.setattr(custom_loader, "load_custom_templates",
                        lambda: {"my_custom": lambda **kw: None})
    with caplog.at_level(logging.WARNING, logger="vmware-pilot.templates"):
        templates.get_all_templates()
    assert not [r for r in caplog.records if "shadows built-in" in r.message]
