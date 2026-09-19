"""A step that passes ``confirm: True`` must come after an approval step — in every workflow.

``confirm=True`` is Pilot telling a gated companion tool "a human already
decided". Built-in templates are tested for it
(``test_templates_pass_confirm``); user-authored workflows were gated only by the
catalog risk tier, so a ``write``-tier step (which the tier rule deliberately
leaves ungated) could carry ``confirm: True`` with no human decision ahead of it.
The rule now lives in ``approval_gate.gate_violations``, which every custom door
uses: ``create_workflow``, ``update_draft`` / ``confirm_draft``, the YAML loader
and ``plan_workflow``, and ``run_workflow``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import vmware_pilot.mcp_server.server as server
from vmware_pilot import custom_loader
from vmware_pilot.approval_gate import ApprovalGateError, gate_violations
from vmware_pilot.executor import WorkflowExecutor
from vmware_pilot.models import Workflow, WorkflowState, WorkflowStep, WorkflowStore

GATE = {"action": "require_approval", "skill": "pilot", "tool": "approve",
        "params": {"message": "ok?"}}
#: A ``write``-tier tool: the tier rule alone does not require a gate for it.
SEGMENT = {"action": "make", "skill": "nsx", "tool": "create_segment",
           "params": {"name": "seg-a"}}
SEGMENT_CONFIRMED = {**SEGMENT, "params": {"name": "seg-a", "confirm": True}}


@pytest.fixture
def store(tmp_path, monkeypatch):
    s = WorkflowStore(tmp_path / "wf.db")
    monkeypatch.setattr(server, "_store", s)
    monkeypatch.setattr(server, "_executor", WorkflowExecutor(s))
    return s


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    return h


def _yaml_files(home: Path) -> list[Path]:
    d = home / ".vmware" / "workflows"
    return sorted(d.glob("*.yaml")) if d.exists() else []


def _wf(steps, custom=True):
    return Workflow(
        id="wf-c", workflow_type="custom_c", state=WorkflowState.PENDING,
        steps=[WorkflowStep(index=i, action=s["action"], skill=s["skill"],
                            tool=s["tool"], params=s["params"])
               for i, s in enumerate(steps)],
        params={"custom": True} if custom else {}, created_at="", updated_at="",
    )


# ── the shared check ─────────────────────────────────────────────────────


@pytest.mark.unit
class TestGateViolations:
    def test_positive_control_write_tier_without_confirm_needs_no_gate(self):
        assert gate_violations(_wf([SEGMENT])) == ()

    def test_confirm_true_without_a_preceding_approval_is_a_violation(self):
        (v,) = gate_violations(_wf([SEGMENT_CONFIRMED]))
        assert (v.step_index, v.skill, v.tool, v.kind) == (
            0, "nsx", "create_segment", "confirm_without_approval")
        assert "confirm=True" in v.reason

    def test_an_approval_after_the_step_does_not_count(self):
        assert len(gate_violations(_wf([SEGMENT_CONFIRMED, GATE]))) == 1

    def test_an_approval_before_covers_it(self):
        assert gate_violations(_wf([GATE, SEGMENT_CONFIRMED])) == ()

    def test_confirm_false_is_not_a_violation(self):
        step = {**SEGMENT, "params": {"name": "seg-a", "confirm": False}}
        assert gate_violations(_wf([step])) == ()

    @pytest.mark.parametrize("params", [{"confirm": "true"}, {"confirmed": True}])
    def test_other_spellings_of_acting_count(self, params):
        step = {**SEGMENT, "params": {"name": "seg-a", **params}}
        assert [v.kind for v in gate_violations(_wf([step]))] == ["confirm_without_approval"]

    def test_a_step_already_flagged_by_its_tier_is_listed_once(self):
        power_off = {"action": "stop", "skill": "aiops", "tool": "vm_power_off",
                     "params": {"vm_name": "db02", "confirm": True}}
        (v,) = gate_violations(_wf([power_off]))
        assert v.kind == "ungated_destructive"


# ── create_workflow ──────────────────────────────────────────────────────


@pytest.mark.unit
class TestCreateWorkflow:
    def test_refused_and_not_persisted(self, store, home):
        result = server.create_workflow(name="seg", description="d",
                                        steps=[SEGMENT_CONFIRMED], save_as_template=True)
        assert "step 0 (nsx.create_segment" in result["error"]
        assert "confirm=True" in result["error"]
        assert "require_approval" in result["error"]
        assert result["fix"]["insert_before_step"] == 0
        assert store.list_all() == []
        assert _yaml_files(home) == []

    def test_accepted_with_an_approval_first(self, store, home):
        result = server.create_workflow(name="seg", description="d",
                                        steps=[GATE, SEGMENT_CONFIRMED])
        assert "error" not in result


# ── design_workflow → update_draft → confirm_draft ───────────────────────


@pytest.mark.unit
class TestDraftFlow:
    def _draft(self, steps):
        wf_id = server.design_workflow(goal="make a segment")["workflow_id"]
        return wf_id, server.update_draft(wf_id, name="seg", steps=steps)

    def test_update_draft_reports_it(self, store, home):
        _, updated = self._draft([SEGMENT_CONFIRMED])
        assert [v["kind"] for v in updated["approval_gate_violations"]] == [
            "confirm_without_approval"]

    def test_confirm_draft_refuses_and_leaves_it_a_draft(self, store, home):
        wf_id, _ = self._draft([SEGMENT_CONFIRMED])
        result = server.confirm_draft(wf_id, save_as_template=True)
        assert "step 0 (nsx.create_segment" in result["error"]
        assert store.load(wf_id).state == WorkflowState.DRAFT
        assert _yaml_files(home) == []

    def test_confirm_draft_accepts_an_approved_draft(self, store, home):
        wf_id, _ = self._draft([GATE, SEGMENT_CONFIRMED])
        assert "error" not in server.confirm_draft(wf_id)


# ── custom YAML ──────────────────────────────────────────────────────────

_YAML = """\
name: seg_now
description: create a segment, confirmed, no gate
steps:
  - action: make
    skill: nsx
    tool: create_segment
    params: {name: "{{seg}}", confirm: true}
"""


@pytest.mark.unit
class TestCustomYaml:
    def test_loader_refuses(self, tmp_path, monkeypatch):
        (tmp_path / "seg_now.yaml").write_text(_YAML, encoding="utf-8")
        monkeypatch.setattr(custom_loader, "_WORKFLOWS_DIR", tmp_path)
        loader = custom_loader.load_custom_templates()["seg_now"]
        with pytest.raises(ApprovalGateError, match="confirm=True"):
            loader(seg="a")

    def test_plan_workflow_refuses(self, store, tmp_path, monkeypatch):
        (tmp_path / "seg_now.yaml").write_text(_YAML, encoding="utf-8")
        monkeypatch.setattr(custom_loader, "_WORKFLOWS_DIR", tmp_path)
        result = server.plan_workflow("seg_now", {"seg": "a"})
        assert "seg_now.yaml" in result["error"] and "confirm=True" in result["error"]
        assert store.list_all() == []
