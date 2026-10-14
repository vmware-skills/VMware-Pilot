"""Workflows saved before the 2.0 tool renames keep working (HLD §16 D-15, [M4]).

In 2.0 ten companion skills renamed their MCP tools verb-first and expose only
the new names. A workflow a user saved earlier — a row in ``workflows.db``, a
YAML file in ``~/.vmware/workflows/``, a draft, an undo descriptor — still says
``tool: "vm_delete"``. Dispatched as written it would hit a tool that no longer
exists; classified as written it would miss its catalog entry and be judged on
its name alone.

Pilot translates at one place, ``WorkflowStep`` construction, which every path
goes through: templates, the YAML loader, ``create_workflow`` /
``update_draft``, and loading a stored row. These tests use OLD names on
purpose; they are the only place in this repo that should.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from typing import Any

import pytest

import vmware_pilot.mcp_server.server as server
from vmware_pilot import custom_loader
from vmware_pilot.executor import WorkflowExecutor
from vmware_pilot.models import WorkflowState, WorkflowStep, WorkflowStore

GATE = {"index": 0, "action": "require_approval", "skill": "pilot", "tool": "approve",
        "params": {"message": "ok?"}}


def _step(index: int, skill: str, tool: str, **extra: Any) -> dict[str, Any]:
    return {"index": index, "action": f"step_{index}", "skill": skill, "tool": tool,
            "params": extra.pop("params", {"vm_name": "db02"}), **extra}


def _row(wf_id: str, steps: list[dict[str, Any]], params: dict[str, Any],
         state: str = "pending") -> dict[str, Any]:
    """A workflow exactly as an earlier Pilot wrote it to workflows.db."""
    return {
        "id": wf_id, "workflow_type": "restart_db", "state": state,
        "steps": [{"status": "pending", "result": None, "started_at": "",
                   "completed_at": "", "rollback_tool": "", "rollback_params": {},
                   "group_id": "", **s} for s in steps],
        "params": params, "created_at": "2026-09-01T00:00:00+00:00",
        "updated_at": "2026-09-01T00:00:00+00:00", "approved_by": "", "diff_report": {},
        "audit_log": [], "blocked_reason": "", "rollback_results": [],
    }


def _write_raw(store: WorkflowStore, row: dict[str, Any]) -> None:
    """Insert straight into SQLite, bypassing the live-object cache."""
    with closing(sqlite3.connect(str(store._path))) as conn:
        conn.execute(
            "INSERT INTO workflows (id, type, state, data, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (row["id"], row["workflow_type"], row["state"], json.dumps(row),
             row["created_at"], row["updated_at"]),
        )
        conn.commit()


@pytest.fixture
def calls() -> list[tuple[str, str]]:
    return []


@pytest.fixture
def store(tmp_path, monkeypatch, calls):
    s = WorkflowStore(tmp_path / "wf.db")

    def dispatch(skill: str, tool: str, params: dict[str, Any]) -> dict[str, Any]:
        calls.append((skill, tool))
        return {"ok": True}

    monkeypatch.setattr(server, "_store", s)
    monkeypatch.setattr(server, "_executor", WorkflowExecutor(s, dispatch=dispatch))
    return s


# ── the translation itself ───────────────────────────────────────────────


@pytest.mark.unit
class TestStepTranslation:
    @pytest.mark.parametrize("skill,old,new", [
        ("aiops", "vm_delete", "delete_vm"),
        ("aiops", "vm_power_off", "power_off_vm"),
        ("vdi", "pool_list", "list_desktop_pools"),
        ("avi", "pool_list", "list_lb_pools"),
        ("monitor", "vm_info", "get_vm_info"),
        ("vks", "list_vm_snapshots", "list_vm_service_snapshots"),
        ("aiops", "scan_datastore_images", "scan_all_datastore_images"),
        ("log-insight", "log_search", "search_logs"),
        # The skill spelled as its package name, or with an underscore.
        ("vmware-aiops", "vm_delete", "delete_vm"),
        ("log_insight", "log_search", "search_logs"),
    ])
    def test_old_name_becomes_the_new_one_for_that_skill(self, skill, old, new):
        assert WorkflowStep(**_step(0, skill, old)).tool == new

    @pytest.mark.parametrize("skill,tool", [
        ("storage", "scan_datastore_images"),   # storage kept this name
        ("monitor", "list_vm_snapshots"),       # monitor's NEW name, vks's old one
        ("aiops", "delete_vm"),                 # already new
        ("nsx", "delete_segment"),              # nsx renamed nothing
        ("pilot", "approve"),
        ("aiops", "quiesce_appliance"),         # never existed
        ("unknown", "vm_delete"),               # no skill to resolve it against
    ])
    def test_everything_else_is_left_alone(self, skill, tool):
        assert WorkflowStep(**_step(0, skill, tool)).tool == tool

    def test_rollback_tool_is_translated_for_the_steps_skill(self):
        step = WorkflowStep(**_step(0, "aiops", "vm_power_off", rollback_tool="vm_power_on",
                                    rollback_params={"vm_name": "db02"}))
        assert (step.tool, step.rollback_tool) == ("power_off_vm", "power_on_vm")


# ── a row saved by an earlier Pilot ─────────────────────────────────────


@pytest.mark.unit
class TestSavedWorkflow:
    def test_saved_vm_delete_runs_as_delete_vm_after_the_gate(self, store, calls):
        _write_raw(store, _row("wf-old-1", [GATE, _step(1, "aiops", "vm_delete")],
                               {"custom": True}))
        assert store.load("wf-old-1").steps[1].tool == "delete_vm"

        paused = server.run_workflow("wf-old-1")
        assert paused["state"] == "awaiting_approval", paused
        assert calls == []

        server.approve("wf-old-1", approver="ops")
        assert calls == [("aiops", "delete_vm")]

    def test_saved_vm_delete_without_a_gate_is_still_refused(self, store, calls):
        _write_raw(store, _row("wf-old-2", [_step(0, "aiops", "vm_delete")], {"custom": True}))
        result = server.run_workflow("wf-old-2", force=True)
        assert "error" in result
        assert "step 0 (aiops.delete_vm" in result["error"], "names the step by its 2.0 name"
        assert calls == []

    def test_a_saved_medium_write_is_classified_by_its_catalog_entry(self, store, calls):
        """Translation decides the tier, not only the dispatch.

        ``power_on_vm`` is a catalogued medium write, which custom workflows
        may run without a gate (like the built-in templates do). Untranslated,
        ``vm_power_on`` is in no catalog and its name matches no pattern, so
        the step is ``unknown`` and the run is refused.
        """
        _write_raw(store, _row("wf-old-3", [_step(0, "aiops", "vm_power_on")], {"custom": True}))
        result = server.run_workflow("wf-old-3")
        assert "error" not in result, result
        assert calls == [("aiops", "power_on_vm")]

    def test_an_unknown_name_is_still_refused(self, store, calls):
        _write_raw(store, _row("wf-old-4", [_step(0, "aiops", "quiesce_appliance")],
                               {"custom": True}))
        result = server.run_workflow("wf-old-4", force=True)
        assert "error" in result
        assert "step 0 (aiops.quiesce_appliance" in result["error"]
        assert calls == []

    def test_saved_undo_descriptor_rolls_back_with_the_new_name(self, store, calls):
        row = _row("wf-old-5", [_step(0, "aiops", "vm_power_on", status="success",
                                      rollback_tool="vm_power_off",
                                      rollback_params={"vm_name": "db02"})],
                   {"custom": True}, state="failed")
        _write_raw(store, row)
        result = server.rollback("wf-old-5", confirm=True)
        assert "error" not in result, result
        assert calls == [("aiops", "power_off_vm")]


# ── the other doors ─────────────────────────────────────────────────────

_OLD_YAML = """\
name: restart_db
steps:
  - action: require_approval
    skill: pilot
    tool: approve
    params: {message: "proceed"}
  - action: stop_replica
    skill: aiops
    tool: vm_power_off
    params: {vm_name: "{{replica_vm}}"}
    rollback_tool: vm_power_on
    rollback_params: {vm_name: "{{replica_vm}}"}
  - action: list_pools
    skill: vdi
    tool: pool_list
    params: {}
"""


@pytest.mark.unit
class TestOtherDoors:
    def test_yaml_written_with_old_names_plans_with_new_ones(self, store, tmp_path,
                                                            monkeypatch):
        (tmp_path / "restart_db.yaml").write_text(_OLD_YAML, encoding="utf-8")
        monkeypatch.setattr(custom_loader, "_WORKFLOWS_DIR", tmp_path)
        result = server.plan_workflow("restart_db", {"replica_vm": "db02"})
        assert "error" not in result, result
        wf = store.load(result["workflow_id"])
        assert [(s.tool, s.rollback_tool) for s in wf.steps] == [
            ("approve", ""), ("power_off_vm", "power_on_vm"), ("list_desktop_pools", "")]

    def test_confirm_draft_sees_the_new_name(self, store):
        wf_id = server.design_workflow(goal="wipe a guest")["workflow_id"]
        server.update_draft(wf_id, name="wipe", steps=[
            {"action": "wipe", "skill": "aiops", "tool": "vm_guest_exec",
             "params": {"vm_name": "db02", "command": "/bin/true"}}])
        result = server.confirm_draft(wf_id)
        assert "error" in result
        assert "step 0 (aiops.run_vm_guest_command" in result["error"]
        assert store.load(wf_id).state == WorkflowState.DRAFT

    def test_create_workflow_stores_the_new_name(self, store):
        result = server.create_workflow(name="pools", description="list pools", steps=[
            {"action": "list", "skill": "avi", "tool": "pool_list", "params": {}}])
        assert "error" not in result, result
        assert store.load(result["workflow_id"]).steps[0].tool == "list_lb_pools"


@pytest.mark.unit
def test_validate_script_names_the_new_tool_and_says_to_rename(tmp_path):
    from tests.test_custom_approval_gate import _load_script

    path = tmp_path / "restart_db.yaml"
    path.write_text(_OLD_YAML, encoding="utf-8")
    errors, warnings = _load_script().validate(path)
    assert errors == []
    assert any("'vm_power_off' is the pre-2.0 name of aiops's 'power_off_vm'" in w
               for w in warnings), warnings
    assert any("'pool_list' is the pre-2.0 name of vdi's 'list_desktop_pools'" in w
               for w in warnings), warnings
    assert not any("not in the catalog" in w and "power_off_vm" in w for w in warnings)
