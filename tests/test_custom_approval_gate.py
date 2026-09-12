"""Custom workflows must put a human approval gate ahead of every gated step.

Built-in templates are authored and reviewed here. Custom workflows — a YAML file
in ``~/.vmware/workflows/``, a ``create_workflow`` step list, or an AI-designed
draft — are not. Until this change the only thing standing between an ungated
``vm_power_off`` in a custom workflow and the calling agent was a *warning* from a
helper script and a ``run_workflow`` refusal that ``force=True`` switched off.
A warning is not a control, and a flag that turns a refusal off is a refusal only
for callers who chose to keep it.

So for custom workflows the rule is now reject, at every door:

* ``create_workflow`` refuses to persist the workflow (and to write its YAML);
* ``confirm_draft`` refuses to move the draft to PENDING (and to write its YAML);
* ``plan_workflow`` refuses to plan a custom YAML template, and the YAML loader
  itself refuses to build the workflow, so an embedder cannot get one either;
* ``run_workflow`` refuses a custom workflow with an ungated step even with
  ``force=True`` — the fix is one inserted step, and that step *is* the human
  consent ``force`` would otherwise stand in for;
* ``scripts/validate_workflow.py`` reports it as an error and exits non-zero.

"Gated" means exactly what ``review()`` already gates: the ``destructive`` tier
(the skill catalog's high/critical risk, or a destructive name) and the
``unknown`` tier (pilot cannot rule out that the step changes state). No second
list is kept here — the classification is ``review.classify_step`` and the
coverage is ``review.review``.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

import vmware_pilot.mcp_server.server as server
from vmware_pilot import custom_loader
from vmware_pilot.executor import WorkflowExecutor
from vmware_pilot.models import Workflow, WorkflowState, WorkflowStep, WorkflowStore

GATE = {"action": "require_approval", "skill": "pilot", "tool": "approve",
        "params": {"message": "ok?"}}
READ = {"action": "check", "skill": "monitor", "tool": "get_alarms", "params": {}}
POWER_OFF = {"action": "stop", "skill": "aiops", "tool": "vm_power_off",
             "params": {"vm_name": "db02"}}
UNKNOWN = {"action": "frob", "skill": "nsx", "tool": "quiesce_appliance",
           "params": {"id": "x"}}
#: The reproduction from the 2026-09-11 review: saved, then dispatched ungated.
GUEST_RM = {"action": "wipe", "skill": "aiops", "tool": "vm_guest_exec",
            "params": {"vm_name": "db02", "command": "/bin/rm", "arguments": "-rf /",
                       "username": "root"}}


@pytest.fixture
def store(tmp_path, monkeypatch):
    s = WorkflowStore(tmp_path / "wf.db")
    monkeypatch.setattr(server, "_store", s)
    monkeypatch.setattr(server, "_executor", WorkflowExecutor(s))
    return s


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Where ``_save_as_yaml`` would write, so a test can prove it did not."""
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    return h


def _yaml_files(home: Path) -> list[Path]:
    d = home / ".vmware" / "workflows"
    return sorted(d.glob("*.yaml")) if d.exists() else []


# ── the shared check ─────────────────────────────────────────────────────


@pytest.mark.unit
class TestGateViolations:
    def _wf(self, steps):
        return Workflow(
            id="wf-v", workflow_type="custom_x", state=WorkflowState.PENDING,
            steps=[WorkflowStep(index=i, action=s["action"], skill=s["skill"],
                                tool=s["tool"], params=s["params"])
                   for i, s in enumerate(steps)],
            params={"custom": True}, created_at="", updated_at="",
        )

    def test_ungated_destructive_is_a_violation(self):
        from vmware_pilot.approval_gate import gate_violations

        v = gate_violations(self._wf([READ, POWER_OFF]))
        assert [(x.step_index, x.skill, x.tool) for x in v] == [(1, "aiops", "vm_power_off")]
        assert "high" in v[0].reason

    def test_unclassifiable_is_a_violation(self):
        from vmware_pilot.approval_gate import gate_violations

        v = gate_violations(self._wf([UNKNOWN]))
        assert [x.kind for x in v] == ["ungated_unclassified"]

    def test_a_gate_after_the_step_does_not_count(self):
        from vmware_pilot.approval_gate import gate_violations

        assert len(gate_violations(self._wf([POWER_OFF, GATE]))) == 1

    def test_a_gate_before_covers_every_later_step(self):
        from vmware_pilot.approval_gate import gate_violations

        assert gate_violations(self._wf([READ, GATE, POWER_OFF, UNKNOWN])) == ()

    def test_read_only_workflow_has_no_violations(self):
        from vmware_pilot.approval_gate import gate_violations

        assert gate_violations(self._wf([READ, READ])) == ()


# ── create_workflow ──────────────────────────────────────────────────────


@pytest.mark.unit
class TestCreateWorkflowRejects:
    def test_ungated_destructive_is_refused_and_not_persisted(self, store, home):
        result = server.create_workflow(
            name="restart_db", description="d", steps=[READ, POWER_OFF],
            save_as_template=True,
        )
        assert "error" in result
        assert store.list_all() == []
        assert _yaml_files(home) == [], "a refused workflow must not be saved as a template"

    def test_error_names_every_offending_step_and_shows_the_gate(self, store, home):
        result = server.create_workflow(
            name="restart_db", description="d", steps=[READ, POWER_OFF, UNKNOWN],
        )
        err = result["error"]
        assert "step 1 (aiops.vm_power_off" in err
        assert "step 2 (nsx.quiesce_appliance" in err
        assert "require_approval" in err
        # The remedy is concrete: the exact step to insert, and where.
        fix = result["fix"]
        assert fix["insert_before_step"] == 1
        assert fix["step"]["action"] == "require_approval"
        assert fix["step"]["skill"] == "pilot"
        assert fix["step"]["tool"] == "approve"
        assert [v["step_index"] for v in result["approval_gate_violations"]] == [1, 2]

    def test_gated_custom_workflow_is_created(self, store, home):
        result = server.create_workflow(
            name="restart_db", description="d", steps=[READ, GATE, POWER_OFF],
            save_as_template=True,
        )
        assert "error" not in result
        assert store.load(result["workflow_id"]) is not None
        assert [p.name for p in _yaml_files(home)] == ["restart_db.yaml"]

    def test_read_only_custom_workflow_needs_no_gate(self, store, home):
        result = server.create_workflow(name="look", description="d", steps=[READ])
        assert "error" not in result

    def test_an_ungated_guest_command_is_refused(self, store, home):
        """vmware-aiops publishes vm_guest_exec with destructiveHint=True."""
        result = server.create_workflow(name="wipe", description="d", steps=[GUEST_RM])
        assert "error" in result
        assert "step 0 (aiops.vm_guest_exec" in result["error"]
        assert store.list_all() == []

    def test_an_ungated_guest_command_is_not_dispatched(self, store):
        """The run door too, for a workflow persisted before the label changed."""
        _persist(store, [GUEST_RM], wf_id="wf-rm", workflow_type="wipe",
                 params={"custom": True})
        result = server.run_workflow("wf-rm", force=True)
        assert "error" in result
        assert result.get("outcome") != "dispatch_required"


# ── template names ───────────────────────────────────────────────────────


@pytest.mark.unit
class TestBuiltinNamesAreReserved:
    """A saved template named like a built-in replaced it for every later plan."""

    def test_create_workflow_refuses_to_save_under_a_builtin_name(self, store, home):
        result = server.create_workflow(
            name="patch_deployment", description="d", steps=[READ], save_as_template=True,
        )
        assert "error" in result
        assert "built-in" in result["error"]
        assert "patch_deployment" in result["error"]
        assert store.list_all() == []
        assert _yaml_files(home) == []

    def test_the_hint_does_not_suggest_a_builtin_name(self, store, home):
        from vmware_pilot.templates import BUILTIN_TEMPLATES

        result = server.create_workflow(
            name="patch_deployment", description="d", steps=[READ], save_as_template=True,
        )
        assert "hint" in result, "no refusal, so there is no hint to check"
        assert not any(f"'{n}'" in result["hint"] for n in BUILTIN_TEMPLATES)

    def test_confirm_draft_refuses_to_save_under_a_builtin_name(self, store, home):
        wf_id = server.design_workflow(goal="look")["workflow_id"]
        server.update_draft(wf_id, name="compliance_scan", steps=[READ])
        result = server.confirm_draft(wf_id, save_as_template=True)
        assert "error" in result
        assert "built-in" in result["error"]
        assert store.load(wf_id).state == WorkflowState.DRAFT
        assert _yaml_files(home) == []

    def test_the_builtin_still_plans_after_a_refused_save(self, store, home, monkeypatch):
        # The loader resolves its directory at import time; point it at the
        # directory _save_as_yaml writes to, or a save could never shadow anything.
        monkeypatch.setattr(custom_loader, "_WORKFLOWS_DIR", home / ".vmware" / "workflows")
        server.create_workflow(
            name="patch_deployment", description="d", steps=[READ], save_as_template=True,
        )
        planned = server.plan_workflow("patch_deployment", _BUILTIN_ARGS["patch_deployment"][0])
        assert "error" not in planned
        assert len(planned["steps"]) == 7

    def test_a_non_builtin_name_still_saves(self, store, home):
        result = server.create_workflow(
            name="my_patch_run", description="d", steps=[READ], save_as_template=True,
        )
        assert "error" not in result
        assert [p.name for p in _yaml_files(home)] == ["my_patch_run.yaml"]

    def test_without_save_as_template_the_name_is_only_a_label(self, store, home):
        """Not a template, so nothing is replaced — and it is still custom."""
        result = server.create_workflow(name="patch_deployment", description="d", steps=[READ])
        assert "error" not in result
        assert store.load(result["workflow_id"]).params["custom"] is True


# ── design_workflow → update_draft → confirm_draft ───────────────────────


@pytest.mark.unit
class TestDraftFlowRejects:
    def _draft(self, steps):
        wf_id = server.design_workflow(goal="restart the db")["workflow_id"]
        return wf_id, server.update_draft(wf_id, name="restart_db", steps=steps)

    def test_update_draft_keeps_the_draft_but_says_confirm_will_refuse(self, store, home):
        wf_id, updated = self._draft([POWER_OFF])
        assert "error" not in updated, "a draft is work in progress and may be iterated"
        assert [v["step_index"] for v in updated["approval_gate_violations"]] == [0]
        assert "confirm_draft" in updated["message"]

    def test_confirm_draft_refuses_and_leaves_it_a_draft(self, store, home):
        wf_id, _ = self._draft([POWER_OFF])
        result = server.confirm_draft(wf_id, save_as_template=True)
        assert "error" in result
        assert "step 0 (aiops.vm_power_off" in result["error"]
        assert store.load(wf_id).state == WorkflowState.DRAFT
        assert _yaml_files(home) == []

    def test_confirm_draft_accepts_a_gated_draft(self, store, home):
        wf_id, updated = self._draft([GATE, POWER_OFF])
        assert updated["approval_gate_violations"] == []
        result = server.confirm_draft(wf_id)
        assert "error" not in result
        assert store.load(wf_id).state == WorkflowState.PENDING


# ── custom YAML templates ────────────────────────────────────────────────

_UNGATED_YAML = """\
name: restart_db
description: stop the replica with no gate
steps:
  - action: check_health
    skill: monitor
    tool: get_alarms
    params: {target: "{{target}}"}
  - action: stop_replica
    skill: aiops
    tool: vm_power_off
    params: {vm_name: "{{replica_vm}}"}
"""

_GATED_YAML = _UNGATED_YAML.replace(
    "  - action: stop_replica",
    "  - action: require_approval\n    skill: pilot\n    tool: approve\n"
    "    params: {message: \"proceed\"}\n  - action: stop_replica",
)


@pytest.mark.unit
class TestCustomYamlRejects:
    def test_plan_workflow_refuses_an_ungated_yaml(self, store, tmp_path, monkeypatch):
        (tmp_path / "restart_db.yaml").write_text(_UNGATED_YAML, encoding="utf-8")
        monkeypatch.setattr(custom_loader, "_WORKFLOWS_DIR", tmp_path)

        result = server.plan_workflow("restart_db", {"target": "vc", "replica_vm": "db02"})
        assert "error" in result
        assert "restart_db.yaml" in result["error"], "must name the file to edit"
        assert "step 1 (aiops.vm_power_off" in result["error"]
        assert "require_approval" in result["error"]
        assert store.list_all() == []

    def test_the_loader_itself_refuses_so_embedders_cannot_bypass(self, tmp_path, monkeypatch):
        from vmware_pilot.approval_gate import ApprovalGateError

        (tmp_path / "restart_db.yaml").write_text(_UNGATED_YAML, encoding="utf-8")
        monkeypatch.setattr(custom_loader, "_WORKFLOWS_DIR", tmp_path)

        loader = custom_loader.load_custom_templates()["restart_db"]
        with pytest.raises(ApprovalGateError):
            loader(target="vc", replica_vm="db02")

    def test_a_yaml_that_shadows_a_builtin_name_is_still_custom(self, store, tmp_path,
                                                                monkeypatch):
        """The built-in exemption is for the vetted code, not for the name."""
        (tmp_path / "x.yaml").write_text(
            _UNGATED_YAML.replace("name: restart_db", "name: compliance_scan"),
            encoding="utf-8",
        )
        monkeypatch.setattr(custom_loader, "_WORKFLOWS_DIR", tmp_path)
        result = server.plan_workflow("compliance_scan", {"replica_vm": "db02"})
        assert "error" in result

    def test_gated_yaml_plans(self, store, tmp_path, monkeypatch):
        (tmp_path / "restart_db.yaml").write_text(_GATED_YAML, encoding="utf-8")
        monkeypatch.setattr(custom_loader, "_WORKFLOWS_DIR", tmp_path)
        result = server.plan_workflow("restart_db", {"target": "vc", "replica_vm": "db02"})
        assert "error" not in result


# ── run_workflow ─────────────────────────────────────────────────────────


def _persist(store, steps, *, wf_id, workflow_type, params):
    wf = Workflow(
        id=wf_id, workflow_type=workflow_type, state=WorkflowState.PENDING,
        steps=[WorkflowStep(index=i, action=s["action"], skill=s["skill"],
                            tool=s["tool"], params=s["params"])
               for i, s in enumerate(steps)],
        params=params, created_at="", updated_at="",
    )
    store.save(wf)
    return wf


@pytest.mark.unit
class TestRunWorkflowRejectsCustomEvenWithForce:
    @pytest.mark.parametrize("params,workflow_type", [
        ({"custom": True}, "restart_db"),                 # create_workflow / design
        ({"_source": "restart_db.yaml"}, "restart_db"),   # custom YAML
        ({"_source": "x.yaml"}, "compliance_scan"),       # YAML shadowing a built-in
        ({}, "restart_db"),                               # persisted before this change
    ])
    def test_force_does_not_override_for_custom(self, store, params, workflow_type):
        wf = _persist(store, [POWER_OFF], wf_id="wf-r1",
                      workflow_type=workflow_type, params=params)
        result = server.run_workflow("wf-r1", force=True)
        assert "error" in result
        assert "step 0 (aiops.vm_power_off" in result["error"]
        assert "force" in result["error"], "must say why force did not work"
        assert wf.steps[0].status == "pending"
        assert not any(e["action"] == "forced_run" for e in wf.audit_log)

    def test_gated_custom_workflow_still_runs(self, store):
        _persist(store, [GATE, POWER_OFF], wf_id="wf-r2",
                 workflow_type="restart_db", params={"custom": True})
        result = server.run_workflow("wf-r2")
        assert result["state"] == "awaiting_approval"

    def test_force_still_overrides_for_a_builtin(self, store):
        """Built-ins keep the audited escape hatch — see the clone_and_test pin below."""
        wf = _persist(store, [UNKNOWN], wf_id="wf-r3",
                      workflow_type="clone_and_test", params={"target_vm": "db01"})
        result = server.run_workflow("wf-r3", force=True)
        assert "error" not in result
        assert any(e["action"] == "forced_run" for e in wf.audit_log)


# ── built-in templates, exercised with arguments that actually build them ─

#: Arguments rich enough that every template builds. The older sweep in
#: ``test_approval_gate_coverage`` feeds ``"x"`` to parameters whose annotation
#: is a string under ``from __future__ import annotations``, which makes
#: ``clone_and_test`` raise and be *skipped* — the very template this pin is about.
_BUILTIN_ARGS = {
    "clone_and_test": [
        {"target_vm": "db01", "change_spec": {"memory_mb": 32768}, "target": "vc"},
        {"target_vm": "db01", "change_spec": {"memory_gb": 32, "cpu": 4}, "target": "vc"},
        {"target_vm": "db01", "target": "vc",
         "change_spec": {"command": "/usr/bin/apt-get", "arguments": "-y upgrade",
                         "username": "ops", "password": "pw"}},
    ],
    "incident_response": [{"alert_entity": "vm-1", "alert_name": "cpu"}],
    "investigate_alert": [{"alert_entity": "vm-1"}, {"alert_entity": "vm-1", "deep_dive": True}],
    "plan_and_approve": [{"operations": [{"action": "power_off", "vm_name": "a"}]}],
    "compliance_scan": [{}],
    "network_segment_setup": [{"segment_id": "s", "display_name": "s", "subnet": "10.0.0.1/24",
                               "transport_zone_path": "/tz", "nat_source": "10.0.0.0/24",
                               "nat_translated": "1.1.1.1", "dfw_policy_id": "p"}],
    "vks_cluster_deploy": [{"namespace_name": "n", "cluster_id": "c", "storage_policy": "p",
                            "tkc_name": "t", "k8s_version": "v1.30"}],
    "rolling_restart": [{"vm_names": ["a", "b"]}],
    "capacity_expansion": [{"vm_name": "a", "cpu": 4, "memory_mb": 8192}],
    "disaster_recovery": [{"vm_name": "a"}],
    "patch_deployment": [{"vm_names": ["a", "b"], "patch_local_path": "/p.deb",
                          "patch_guest_path": "/tmp/p.deb", "install_command": "dpkg -i",
                          "username": "ops"}],
    "storage_expansion": [{"host_name": "h", "iscsi_address": "10.0.0.9"}],
    "baseline_capture": [{}],
    "baseline_audit": [{}],
    "baseline_remediate": [{"drift_items": [{"resource": "r", "skill": "aiops",
                                             "tool": "vm_power_off", "params": {}}]}],
}


def _builtin_cases():
    from vmware_pilot.templates import BUILTIN_TEMPLATES

    assert set(_BUILTIN_ARGS) == set(BUILTIN_TEMPLATES), "a built-in has no arguments here"
    for name in BUILTIN_TEMPLATES:
        for i, kwargs in enumerate(_BUILTIN_ARGS[name]):
            yield pytest.param(name, kwargs, id=f"{name}-{i}")


@pytest.mark.unit
@pytest.mark.parametrize("name,kwargs", list(_builtin_cases()))
def test_every_builtin_template_passes_the_custom_rule(name, kwargs):
    """Built-ins are exempt from enforcement, not from the rule. Measured."""
    from vmware_pilot.approval_gate import gate_violations
    from vmware_pilot.templates import BUILTIN_TEMPLATES

    wf = BUILTIN_TEMPLATES[name](**kwargs)
    assert wf.steps, "the template built nothing — the check went hollow"
    assert gate_violations(wf) == ()


@pytest.mark.unit
@pytest.mark.parametrize("name,kwargs", list(_builtin_cases()))
def test_every_builtin_template_plans_and_runs_without_force(store, name, kwargs):
    """Through the MCP doors a caller actually uses — not just gate_violations().

    run_workflow also blocks on ``destructive_in_parallel_group``, which the
    custom rule does not look at, so a built-in can pass the check above and
    still be refused here.
    """
    planned = server.plan_workflow(name, kwargs)
    assert "error" not in planned, planned
    ran = server.run_workflow(planned["workflow_id"])
    assert "error" not in ran, ran
    assert ran["outcome"] in ("awaiting_approval", "dispatch_required")


# ── scripts/validate_workflow.py ─────────────────────────────────────────

_SCRIPT = (Path(__file__).resolve().parents[1]
           / "skills" / "vmware-pilot" / "scripts" / "validate_workflow.py")


def _load_script():
    spec = importlib.util.spec_from_file_location("validate_workflow_script", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.unit
class TestValidateScriptRejects:
    def test_ungated_destructive_is_an_error_not_a_warning(self, tmp_path):
        path = tmp_path / "restart_db.yaml"
        path.write_text(_UNGATED_YAML, encoding="utf-8")
        errors, warnings = _load_script().validate(path)
        assert any("step 1 (aiops.vm_power_off" in e for e in errors), errors
        assert not any("vm_power_off" in w for w in warnings)

    def test_main_exits_nonzero(self, tmp_path, monkeypatch):
        path = tmp_path / "restart_db.yaml"
        path.write_text(_UNGATED_YAML, encoding="utf-8")
        monkeypatch.setattr("sys.argv", ["validate_workflow.py", str(path)])
        assert _load_script().main() == 1

    def test_it_uses_the_package_classification_not_its_own_list(self, tmp_path):
        """``get_tkc_kubeconfig`` returns a live credential; the catalog gates it."""
        path = tmp_path / "kc.yaml"
        path.write_text(
            "name: kc\nsteps:\n  - action: fetch\n    skill: vks\n"
            "    tool: get_tkc_kubeconfig\n    params: {name: t}\n",
            encoding="utf-8",
        )
        errors, _ = _load_script().validate(path)
        assert any("get_tkc_kubeconfig" in e for e in errors), errors

    def test_a_catalog_skill_is_not_an_unknown_skill(self, tmp_path):
        path = tmp_path / "avi.yaml"
        path.write_text(
            "name: avi\nsteps:\n  - action: look\n    skill: avi\n"
            "    tool: pool_members\n    params: {pool: p}\n",
            encoding="utf-8",
        )
        errors, _ = _load_script().validate(path)
        assert errors == []

    def test_gated_yaml_is_clean(self, tmp_path):
        path = tmp_path / "restart_db.yaml"
        path.write_text(_GATED_YAML, encoding="utf-8")
        errors, _ = _load_script().validate(path)
        assert errors == []
