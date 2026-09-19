"""A step that only previewed did nothing — it must not be recorded as success.

Across the family, destructive companion tools now take ``confirm: bool = False``
and a bare call returns ``{"action": "preview", "blast_radius": ...}`` instead of
acting (HLD §7, 2026-09-19). ``_returned_failure`` used to look only at a
top-level ``error``, so a template step that forgot ``confirm=True`` would have
been written down as ``success`` while the estate never changed — and a
rollback step that previewed would read as "rolled back".
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from vmware_pilot.executor import WorkflowExecutor, _returned_failure
from vmware_pilot.models import Workflow, WorkflowState, WorkflowStep, WorkflowStore

_PREVIEW = {
    "action": "preview",
    "blast_radius": {"vm": {"name": "db01"}, "blockers": [], "unmeasured": []},
    "hint": "Nothing was changed. Re-run with confirm=True.",
}


class _Text:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _CallToolResult:
    """Duck-typed stand-in for mcp's CallToolResult (isError=False)."""

    def __init__(self, payload: dict, structured: bool) -> None:
        self.isError = False
        self.content = [_Text(json.dumps(payload))]
        self.structuredContent = payload if structured else None


def _wf(wid: str) -> Workflow:
    return Workflow(
        id=wid, workflow_type="test", state=WorkflowState.PENDING,
        steps=[
            WorkflowStep(index=0, action="ok", skill="monitor", tool="get_alarms", params={}),
            WorkflowStep(index=1, action="off", skill="aiops", tool="vm_power_off",
                         params={"vm_name": "db01"}),
            WorkflowStep(index=2, action="after", skill="monitor", tool="get_alarms", params={}),
        ],
        params={}, created_at="", updated_at="",
    )


@pytest.mark.unit
class TestPreviewIsAFailure:
    def test_top_level_preview_is_a_failure_with_the_remedy(self):
        msg = _returned_failure(_PREVIEW)
        assert msg is not None
        assert "only previewed" in msg
        assert "confirm=True" in msg

    def test_preview_step_fails_the_workflow(self, tmp_path: Path):
        store = WorkflowStore(tmp_path / "wf.db")
        wf = _wf("wf-prev-1")
        store.save(wf)

        def dispatch(skill: str, tool: str, params: dict[str, Any]) -> Any:
            return dict(_PREVIEW) if tool == "vm_power_off" else {"ok": True}

        result = WorkflowExecutor(store, dispatch=dispatch).run_until_checkpoint(wf)

        assert result["state"] == "failed", "a preview changed nothing; it is not success"
        assert result["steps"][1]["status"] == "failed"
        assert result["steps"][2]["status"] == "skipped"
        # The preview itself (blast radius) is kept for the operator.
        assert wf.steps[1].result["blast_radius"]["vm"]["name"] == "db01"

    def test_preview_inside_a_call_tool_result_is_a_failure(self):
        assert _returned_failure(_CallToolResult(_PREVIEW, structured=True)) is not None
        assert _returned_failure(_CallToolResult(_PREVIEW, structured=False)) is not None

    def test_rollback_that_only_previews_is_not_a_rollback(self, tmp_path: Path):
        store = WorkflowStore(tmp_path / "wf.db")
        wf = Workflow(
            id="wf-prev-rb", workflow_type="test", state=WorkflowState.PENDING,
            steps=[
                WorkflowStep(index=0, action="create", skill="nsx", tool="create_segment",
                             params={}, rollback_tool="delete_segment", rollback_params={}),
                WorkflowStep(index=1, action="fail", skill="aiops", tool="failing_tool",
                             params={}),
            ],
            params={}, created_at="", updated_at="",
        )
        store.save(wf)

        def dispatch(skill: str, tool: str, params: dict[str, Any]) -> Any:
            if tool == "failing_tool":
                return {"error": "boom"}
            if tool == "delete_segment":
                return dict(_PREVIEW)
            return {"ok": True}

        ex = WorkflowExecutor(store, dispatch=dispatch)
        ex.run_until_checkpoint(wf)
        rb = ex.rollback(wf)

        assert [r["status"] for r in rb["rollback_results"]] == ["failed"]
        assert "only previewed" in rb["rollback_results"][0]["error"]
        assert wf.blocked_reason == "rollback_failed"


@pytest.mark.unit
class TestNestedPreviewIsData:
    """Negative controls: the word 'preview' elsewhere is data, not a failure."""

    @pytest.mark.parametrize(
        "result",
        [
            {"action": "deleted", "blast_radius": {"action": "preview"}},
            {"items": [{"action": "preview"}]},
            {"action": "powered_off", "hint": "the preview was accepted"},
            {"mode": "preview"},
            {"action": "noop", "reason": "already powered off"},
            "preview",
            ["preview"],
        ],
    )
    def test_not_a_failure(self, result: Any):
        assert _returned_failure(result) is None

    def test_acting_result_after_confirm_is_success(self, tmp_path: Path):
        store = WorkflowStore(tmp_path / "wf.db")
        wf = _wf("wf-prev-2")
        store.save(wf)

        def dispatch(skill: str, tool: str, params: dict[str, Any]) -> Any:
            if tool == "vm_power_off":
                return {"action": "powered_off", "blast_radius": {"action": "preview"}}
            return {"ok": True}

        result = WorkflowExecutor(store, dispatch=dispatch).run_until_checkpoint(wf)
        assert result["state"] == "completed"
