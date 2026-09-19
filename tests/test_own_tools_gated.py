"""Pilot's own destructive tools preview by default (HLD §7, 2026-09-19).

``rollback`` dispatches the undo of every step Pilot recorded as succeeded.
``cancel_workflow`` is reverse-included: stopping a workflow part-way leaves a
half-applied change in place. Both now take ``confirm: bool = False``:

* L2 — a bare call changes nothing (the workflow record is untouched);
* L1 — preview and the acting response carry ``blast_radius``;
* L3 — ``confirm=True`` refuses on a blocker (a state the transition is not
  allowed from) or on anything unmeasured (a record or step that cannot be read).
"""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

import vmware_pilot.mcp_server.server as server
from vmware_pilot.executor import WorkflowExecutor
from vmware_pilot.models import Workflow, WorkflowState, WorkflowStep, WorkflowStore

_CAP = 16


class _SpyExecutor(WorkflowExecutor):
    """Records whether the acting method was reached."""

    def __init__(self, store: WorkflowStore) -> None:
        super().__init__(store)
        self.calls: list[str] = []

    def rollback(self, wf: Workflow) -> dict[str, Any]:
        self.calls.append("rollback")
        return super().rollback(wf)

    def cancel(self, wf: Workflow, reason: str = "") -> dict[str, Any]:
        self.calls.append("cancel")
        return super().cancel(wf, reason=reason)


@pytest.fixture
def store(tmp_path: Path, monkeypatch) -> WorkflowStore:
    s = WorkflowStore(tmp_path / "wf.db")
    monkeypatch.setattr(server, "_store", s)
    monkeypatch.setattr(server, "_executor", _SpyExecutor(s))
    return s


def _executor() -> _SpyExecutor:
    return server._executor  # type: ignore[return-value]


def _wf(wid: str, state: WorkflowState = WorkflowState.FAILED) -> Workflow:
    """Gate passed, a segment created (reversible), a tag applied (irreversible),
    a delete that failed, one step never reached."""
    return Workflow(
        id=wid, workflow_type="network_segment_setup", state=state,
        steps=[
            WorkflowStep(index=0, action="require_approval", skill="pilot", tool="approve",
                         params={}, status="success"),
            WorkflowStep(index=1, action="create_segment", skill="nsx", tool="create_segment",
                         params={"segment_id": "app"}, status="success",
                         rollback_tool="delete_segment",
                         rollback_params={"segment_id": "app", "confirm": True}),
            WorkflowStep(index=2, action="tag", skill="nsx", tool="tag_apply",
                         params={}, status="success"),
            WorkflowStep(index=3, action="nat", skill="nsx", tool="create_nat_rule",
                         params={}, status="failed"),
            WorkflowStep(index=4, action="verify", skill="nsx", tool="list_segments",
                         params={}, status="skipped" if state == WorkflowState.FAILED
                         else "pending"),
        ],
        params={}, created_at="", updated_at="",
    )


def _db_state(store: WorkflowStore, wid: str) -> str:
    with closing(sqlite3.connect(store._path)) as conn:  # noqa: SLF001
        return conn.execute("SELECT state FROM workflows WHERE id = ?", (wid,)).fetchone()[0]


# ── rollback ────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestRollbackPreview:
    def test_bare_call_previews_and_changes_nothing(self, store):
        wf = _wf("wf-rb-1")
        store.save(wf)
        out = server.rollback("wf-rb-1")

        assert out["action"] == "preview"
        assert "hint" in out and "confirm=True" in out["hint"]
        assert _executor().calls == []
        assert wf.state == WorkflowState.FAILED
        assert _db_state(store, "wf-rb-1") == "failed"
        assert [s.status for s in wf.steps][:3] == ["success", "success", "success"]

    def test_preview_measures_what_would_be_undone_and_left(self, store):
        store.save(_wf("wf-rb-2"))
        br = server.rollback("wf-rb-2")["blast_radius"]

        assert br["workflow"] == {"id": "wf-rb-2", "type": "network_segment_setup",
                                  "state": "failed"}
        assert br["would_roll_back_count"] == 1
        assert br["would_roll_back"] == [
            {"index": 1, "skill": "nsx", "tool": "create_segment",
             "rollback_tool": "delete_segment",
             "rollback_params": {"segment_id": "app", "confirm": True}}
        ]
        # Approval gates change nothing; only real steps are "left in place".
        assert br["left_in_place_count"] == 1
        assert br["left_in_place"] == [{"index": 2, "skill": "nsx", "tool": "tag_apply"}]
        assert br["blockers"] == []
        assert br["unmeasured"] == []

    def test_lists_are_capped_but_counts_are_not(self, store):
        steps = [
            WorkflowStep(index=i, action=f"s{i}", skill="nsx", tool="create_segment",
                         params={}, status="success", rollback_tool="delete_segment")
            for i in range(_CAP + 4)
        ]
        wf = Workflow(id="wf-rb-cap", workflow_type="t", state=WorkflowState.FAILED,
                      steps=steps, params={}, created_at="", updated_at="")
        store.save(wf)
        br = server.rollback("wf-rb-cap")["blast_radius"]
        assert br["would_roll_back_count"] == _CAP + 4
        assert len(br["would_roll_back"]) == _CAP


@pytest.mark.unit
class TestRollbackActs:
    def test_confirm_acts_once_and_returns_blast_radius(self, store):
        wf = _wf("wf-rb-3")
        store.save(wf)
        out = server.rollback("wf-rb-3", confirm=True)

        assert _executor().calls == ["rollback"]
        assert out["action"] == "rolled_back"
        assert out["blast_radius"]["would_roll_back_count"] == 1
        assert "rollback_results" in out
        assert "error" not in out


@pytest.mark.unit
class TestRollbackRefuses:
    @pytest.mark.parametrize("state", [WorkflowState.COMPLETED, WorkflowState.DRAFT,
                                       WorkflowState.CANCELLED, WorkflowState.ROLLING_BACK])
    def test_forbidden_state_is_a_blocker(self, store, state):
        wf = _wf(f"wf-rb-{state.value}", state=state)
        store.save(wf)

        preview = server.rollback(wf.id)
        assert preview["action"] == "preview"
        assert preview["blast_radius"]["blockers"], "the blocker must show in the preview"

        out = server.rollback(wf.id, confirm=True)
        assert "error" in out
        assert state.value in out["error"]
        assert _executor().calls == []
        assert wf.state == state

    def test_unknown_step_status_is_unmeasured_and_refused(self, store):
        wf = _wf("wf-rb-unk")
        wf.steps[2].status = "half-done"
        store.save(wf)

        preview = server.rollback("wf-rb-unk")
        assert any("step 2" in u for u in preview["blast_radius"]["unmeasured"])

        out = server.rollback("wf-rb-unk", confirm=True)
        assert "error" in out and "step 2" in out["error"]
        assert _executor().calls == []

    def test_unreadable_record_is_refused(self, store):
        store.save(_wf("wf-rb-bad"))
        store._cache.clear()  # noqa: SLF001 — force a DB read
        with closing(sqlite3.connect(store._path)) as conn:  # noqa: SLF001
            conn.execute("UPDATE workflows SET data = ? WHERE id = ?", ("{not json", "wf-rb-bad"))
            conn.commit()

        for confirm in (False, True):
            out = server.rollback("wf-rb-bad", confirm=confirm)
            assert "error" in out
            assert "could not be read" in out["error"]
            assert "operation failed" not in out["error"]
        assert _executor().calls == []

    def test_refusal_is_audited_as_a_failure(self, store):
        from vmware_policy.audit import get_engine

        wf = _wf("wf-rb-aud", state=WorkflowState.COMPLETED)
        store.save(wf)
        server.rollback(wf.id, confirm=True)
        rows = get_engine().query(tool="rollback", limit=1)
        assert rows, "the refusal wrote no audit row"
        assert rows[0]["status"] != "ok"


# ── cancel_workflow ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestCancelPreview:
    def test_bare_call_previews_and_changes_nothing(self, store):
        wf = _wf("wf-c-1", state=WorkflowState.AWAITING_APPROVAL)
        store.save(wf)
        out = server.cancel_workflow("wf-c-1", reason="rejected")

        assert out["action"] == "preview"
        assert _executor().calls == []
        assert wf.state == WorkflowState.AWAITING_APPROVAL
        assert _db_state(store, "wf-c-1") == "awaiting_approval"
        assert wf.steps[4].status == "pending"

    def test_preview_names_what_is_left_half_applied_and_what_is_skipped(self, store):
        store.save(_wf("wf-c-2", state=WorkflowState.AWAITING_APPROVAL))
        br = server.cancel_workflow("wf-c-2")["blast_radius"]

        assert br["workflow"]["state"] == "awaiting_approval"
        assert br["left_in_place_count"] == 2  # segment + tag stay applied
        assert [s["index"] for s in br["left_in_place"]] == [1, 2]
        assert br["would_skip_count"] == 1
        assert br["would_skip"] == [{"index": 4, "skill": "nsx", "tool": "list_segments"}]
        assert br["blockers"] == [] and br["unmeasured"] == []


@pytest.mark.unit
class TestCancelActs:
    def test_confirm_cancels_once_with_blast_radius(self, store):
        wf = _wf("wf-c-3", state=WorkflowState.PENDING)
        store.save(wf)
        out = server.cancel_workflow("wf-c-3", reason="rejected", confirm=True)

        assert _executor().calls == ["cancel"]
        assert out["action"] == "cancelled"
        assert out["state"] == "cancelled"
        assert out["blast_radius"]["would_skip_count"] == 1


@pytest.mark.unit
class TestCancelRefuses:
    @pytest.mark.parametrize("state", [WorkflowState.COMPLETED, WorkflowState.FAILED,
                                       WorkflowState.CANCELLED])
    def test_terminal_state_is_a_blocker(self, store, state):
        wf = _wf(f"wf-c-{state.value}", state=state)
        store.save(wf)
        assert server.cancel_workflow(wf.id)["blast_radius"]["blockers"]

        out = server.cancel_workflow(wf.id, confirm=True)
        assert "terminal" in out["error"]
        assert _executor().calls == []

    def test_unknown_step_status_is_refused(self, store):
        wf = _wf("wf-c-unk", state=WorkflowState.PENDING)
        wf.steps[1].status = None  # type: ignore[assignment]
        store.save(wf)
        out = server.cancel_workflow("wf-c-unk", confirm=True)
        assert "error" in out and "step 1" in out["error"]
        assert _executor().calls == []

    def test_unreadable_record_is_refused(self, store):
        store.save(_wf("wf-c-bad", state=WorkflowState.PENDING))
        store._cache.clear()  # noqa: SLF001
        with closing(sqlite3.connect(store._path)) as conn:  # noqa: SLF001
            conn.execute("UPDATE workflows SET data = ? WHERE id = ?",
                         ('{"id": "wf-c-bad", "state": "sideways"}', "wf-c-bad"))
            conn.commit()
        out = server.cancel_workflow("wf-c-bad", confirm=True)
        assert "could not be read" in out["error"]
        assert _executor().calls == []


# ── schema + description ────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize("tool", ["rollback", "cancel_workflow"])
def test_schema_shows_confirm_default_false(tool):
    tools = {t.name: t for t in asyncio.run(server.mcp.list_tools())}
    prop = tools[tool].inputSchema["properties"]["confirm"]
    assert prop["default"] is False
    assert prop["type"] == "boolean"
    desc = tools[tool].description or ""
    assert "False (default) returns the blast radius and changes nothing" in (
        desc + prop.get("description", "")
    )
    assert "has not seen the preview" in desc


# ── steps whose effects are unknown (running / interrupted) ────────────────


def _with_unknown(wid: str, status: str, state: WorkflowState) -> Workflow:
    wf = _wf(wid, state=state)
    wf.steps[3].status = status  # create_nat_rule: a process died mid-dispatch
    return wf


_UNKNOWN_CASES = [
    ("rollback", "interrupted", WorkflowState.FAILED),
    ("rollback", "running", WorkflowState.FAILED),
    ("cancel_workflow", "interrupted", WorkflowState.RUNNING),
    ("cancel_workflow", "running", WorkflowState.RUNNING),
]


def _call(tool: str, wid: str, **kw):
    return getattr(server, tool)(wid, **kw)


@pytest.mark.unit
class TestUnknownEffects:
    @pytest.mark.parametrize("tool,status,state", _UNKNOWN_CASES)
    def test_preview_lists_the_step_as_unknown_and_unmeasured(self, store, tool, status, state):
        store.save(_with_unknown(f"wf-u-p-{tool}-{status}", status, state))
        br = _call(tool, f"wf-u-p-{tool}-{status}")["blast_radius"]
        assert br["unknown_effects_count"] == 1
        assert br["unknown_effects"] == [{"index": 3, "skill": "nsx", "tool": "create_nat_rule"}]
        assert any("step 3" in u and "acknowledge_unknown_effects=True" in u
                   for u in br["unmeasured"]), br["unmeasured"]
        assert _executor().calls == []

    @pytest.mark.parametrize("tool,status,state", _UNKNOWN_CASES)
    def test_confirm_alone_refuses_and_teaches_what_to_check(self, store, tool, status, state):
        wid = f"wf-u-r-{tool}-{status}"
        store.save(_with_unknown(wid, status, state))
        out = _call(tool, wid, confirm=True)
        assert "error" in out
        assert "step 3" in out["error"] and "create_nat_rule" in out["error"]
        assert "acknowledge_unknown_effects=True" in out["error"]
        assert _executor().calls == []
        assert _db_state(store, wid) == state.value

    @pytest.mark.parametrize("tool,status,state", _UNKNOWN_CASES)
    def test_acknowledged_unknowns_act(self, store, tool, status, state):
        wid = f"wf-u-a-{tool}-{status}"
        store.save(_with_unknown(wid, status, state))
        out = _call(tool, wid, confirm=True, acknowledge_unknown_effects=True)
        assert "error" not in out, out
        assert _executor().calls == ["rollback" if tool == "rollback" else "cancel"]
        br = out["blast_radius"]
        assert br["unknown_effects_count"] == 1, "the acting response still names them"
        assert br["unmeasured"] == []

    @pytest.mark.parametrize("tool", ["rollback", "cancel_workflow"])
    def test_acknowledgement_does_not_cover_anything_else(self, store, tool):
        state = WorkflowState.FAILED if tool == "rollback" else WorkflowState.RUNNING
        wid = f"wf-u-x-{tool}"
        wf = _with_unknown(wid, "interrupted", state)
        wf.steps[2].status = "half-done"
        store.save(wf)
        out = _call(tool, wid, confirm=True, acknowledge_unknown_effects=True)
        assert "error" in out and "step 2" in out["error"]
        assert _executor().calls == []

    def test_acknowledgement_does_not_lift_a_blocker(self, store):
        wid = "wf-u-blk"
        store.save(_with_unknown(wid, "interrupted", WorkflowState.COMPLETED))
        out = server.rollback(wid, confirm=True, acknowledge_unknown_effects=True)
        assert "error" in out and "completed" in out["error"]
        assert _executor().calls == []


@pytest.mark.unit
@pytest.mark.parametrize("tool", ["rollback", "cancel_workflow"])
def test_schema_shows_acknowledge_unknown_effects_default_false(tool):
    tools = {t.name: t for t in asyncio.run(server.mcp.list_tools())}
    prop = tools[tool].inputSchema["properties"]["acknowledge_unknown_effects"]
    assert prop["default"] is False
    assert prop["type"] == "boolean"


@pytest.mark.unit
def test_crash_message_names_the_way_forward(store):
    wf = _wf("wf-u-crash", state=WorkflowState.RUNNING)
    wf.steps[3].status = "running"
    wf.steps[4].status = "pending"
    store.save(wf)
    out = _executor().run_until_checkpoint(wf)
    assert out["outcome"] == "failed"
    assert "acknowledge_unknown_effects=True" in out["error"]
