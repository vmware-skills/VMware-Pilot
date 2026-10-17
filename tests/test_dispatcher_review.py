"""Independent review of the stdio dispatcher (2026-10-18): each test is a finding."""

from __future__ import annotations

import asyncio
import stat
import sys
import textwrap
from typing import Any

import pytest

from vmware_pilot import dispatcher as dispatcher_mod
from vmware_pilot.dispatcher import DispatchRefusedError, StdioDispatcher
from vmware_pilot.executor import StepEffectUnknownError, WorkflowExecutor
from vmware_pilot.lifecycle_gate import rollback_blast_radius
from vmware_pilot.models import Workflow, WorkflowState, WorkflowStep, WorkflowStore

_DYING_SERVER = textwrap.dedent('''\
    #!{python}
    import os
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("dying")

    @mcp.tool()
    def get_alarms(target: str = "") -> dict:
        os._exit(3)

    mcp.run()
''')


def _bin(tmp_path, monkeypatch, name: str, body: str) -> None:
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    script = bindir / name
    script.write_text(body, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", str(bindir))
    monkeypatch.setattr(dispatcher_mod, "_interpreter_bin", lambda: tmp_path / "nowhere")


def _wf(wid: str, steps: list[WorkflowStep]) -> Workflow:
    return Workflow(id=wid, workflow_type="test", state=WorkflowState.PENDING, steps=steps,
                    params={}, created_at="", updated_at="")


def _ok(skill: str, tool: str, params: dict[str, Any]) -> Any:
    return {"ok": True}


# H1 ─ steps already handed out must not be dispatched again


@pytest.mark.unit
def test_a_dispatcher_does_not_rerun_steps_handed_out_earlier(tmp_path):
    store = WorkflowStore(tmp_path / "wf.db")
    wf = _wf("wf-h1", [WorkflowStep(index=0, action="clone", skill="aiops",
                                    tool="deploy_linked_clone", params={"new_name": "s"})])
    WorkflowExecutor(store).run_until_checkpoint(wf)        # handed to the agent
    assert wf.steps[0].status == "not_executed"
    calls: list[str] = []
    out = WorkflowExecutor(store, dispatch=lambda s, t, p: calls.append(t) or {"ok": True}
                           ).run_until_checkpoint(wf)
    assert calls == []
    assert "error" in out and "handed out" in out["error"]
    assert wf.steps[0].status == "not_executed"


@pytest.mark.unit
def test_no_message_invites_a_rerun_with_a_dispatcher(tmp_path):
    store = WorkflowStore(tmp_path / "wf.db")
    wf = _wf("wf-h1b", [
        WorkflowStep(index=0, action="clone", skill="aiops", tool="deploy_linked_clone",
                     params={}),
        WorkflowStep(index=1, action="require_approval", skill="pilot", tool="approve",
                     params={"message": "go?"}),
    ])
    ex = WorkflowExecutor(store)
    out = ex.run_until_checkpoint(wf)
    refusal = ex.resume_after_approval(wf, approver="wei")["error"]
    for text in (out.get("message", ""), refusal):
        assert "run_until_checkpoint again" not in text
        assert "re-run with a real dispatcher" not in text


# H2 ─ the preview must show every rollback that will run


@pytest.mark.unit
def test_a_read_step_with_a_rollback_tool_is_previewed(tmp_path):
    store = WorkflowStore(tmp_path / "wf.db")
    wf = _wf("wf-h2", [
        WorkflowStep(index=0, action="look", skill="monitor", tool="list_virtual_machines",
                     params={}, rollback_tool="delete_vm",
                     rollback_params={"vm_name": "prod", "confirm": True}),
        WorkflowStep(index=1, action="fail", skill="aiops", tool="failing_tool", params={}),
    ])
    ex = WorkflowExecutor(store, dispatch=lambda s, t, p: {"error": "x"} if t == "failing_tool"
                          else {"ok": True})
    ex.run_until_checkpoint(wf)
    radius = rollback_blast_radius(wf)
    assert [(e["index"], e["rollback_tool"]) for e in radius["would_roll_back"]] == [
        (0, "delete_vm")]


# M1 ─ a child that dies or never starts


@pytest.mark.unit
def test_a_child_that_dies_mid_call_is_an_unknown_effect(tmp_path, monkeypatch):
    _bin(tmp_path, monkeypatch, "vmware-monitor-mcp", _DYING_SERVER.format(python=sys.executable))
    store = WorkflowStore(tmp_path / "wf.db")
    wf = _wf("wf-m1", [WorkflowStep(index=0, action="look", skill="monitor", tool="get_alarms",
                                    params={})])
    WorkflowExecutor(store, dispatch=StdioDispatcher(timeout=20)).run_until_checkpoint(wf)
    assert wf.steps[0].status == "interrupted"
    assert "vmware-monitor-mcp" in str(wf.steps[0].result)
    assert "TaskGroup" not in str(wf.steps[0].result)


@pytest.mark.unit
def test_a_server_that_does_not_start_says_so(tmp_path, monkeypatch):
    _bin(tmp_path, monkeypatch, "vmware-monitor-mcp", "#!/bin/sh\necho broken >&2\nexit 1\n")
    store = WorkflowStore(tmp_path / "wf.db")
    wf = _wf("wf-m1b", [WorkflowStep(index=0, action="look", skill="monitor", tool="get_alarms",
                                     params={})])
    WorkflowExecutor(store, dispatch=StdioDispatcher(timeout=20)).run_until_checkpoint(wf)
    assert wf.steps[0].status == "failed"
    message = str(wf.steps[0].result)
    assert "did not start" in message and "TaskGroup" not in message


# M2 ─ a rollback whose effect is unknown


@pytest.mark.unit
def test_a_rollback_that_times_out_marks_the_step_unknown(tmp_path):
    store = WorkflowStore(tmp_path / "wf.db")
    wf = _wf("wf-m2", [
        WorkflowStep(index=0, action="on", skill="aiops", tool="power_on_vm", params={},
                     rollback_tool="power_off_vm", rollback_params={"confirm": True}),
        WorkflowStep(index=1, action="fail", skill="aiops", tool="failing_tool", params={}),
    ])

    def dispatch(skill: str, tool: str, params: dict[str, Any]) -> Any:
        if tool == "power_off_vm":
            raise StepEffectUnknownError("timed out; effect unknown")
        return {"error": "x"} if tool == "failing_tool" else {"ok": True}

    ex = WorkflowExecutor(store, dispatch=dispatch)
    ex.run_until_checkpoint(wf)
    result = ex.rollback(wf)
    assert result["rollback_results"][0]["status"] == "unknown"
    assert wf.steps[0].status == "interrupted"
    assert [e["index"] for e in rollback_blast_radius(wf)["unknown_effects"]] == [0]


# M4 ─ a guest command that may still be running


@pytest.mark.unit
@pytest.mark.parametrize("result", [
    {"exit_code": -1, "timed_out": True, "stdout": "", "stderr": ""},
    {"exit_code": -1, "timed_out": False, "stdout": "", "stderr": "Process disappeared"},
], ids=["timed-out", "disappeared"])
def test_a_guest_command_that_may_still_run_is_unknown(tmp_path, result):
    store = WorkflowStore(tmp_path / "wf.db")
    wf = _wf("wf-m4", [WorkflowStep(index=0, action="run", skill="aiops",
                                    tool="run_vm_guest_command", params={"confirm": True})])
    WorkflowExecutor(store, dispatch=lambda s, t, p: result).run_until_checkpoint(wf)
    assert wf.steps[0].status == "interrupted"


@pytest.mark.unit
def test_a_guest_command_that_exited_non_zero_is_a_plain_failure(tmp_path):
    store = WorkflowStore(tmp_path / "wf.db")
    wf = _wf("wf-m4b", [WorkflowStep(index=0, action="run", skill="aiops",
                                     tool="run_vm_guest_command", params={"confirm": True})])
    WorkflowExecutor(store, dispatch=lambda s, t, p: {"exit_code": 2, "timed_out": False,
                                                      "stderr": "no such file"}
                     ).run_until_checkpoint(wf)
    assert wf.steps[0].status == "failed"
    assert "exit_code 2" in str(wf.steps[0].result)


# M6 ─ called from inside a running event loop (FastMCP calls sync tools there)


@pytest.mark.unit
def test_the_dispatcher_works_inside_a_running_event_loop(tmp_path, monkeypatch):
    _bin(tmp_path, monkeypatch, "vmware-monitor-mcp", textwrap.dedent(f'''\
        #!{sys.executable}
        from mcp.server.fastmcp import FastMCP
        mcp = FastMCP("ok")
        @mcp.tool()
        def get_alarms(target: str = "") -> dict:
            return {{"items": []}}
        mcp.run()
    '''))

    async def inside_loop() -> Any:
        return StdioDispatcher(timeout=20)("monitor", "get_alarms", {})

    assert asyncio.run(inside_loop()) == {"items": []}


# lows ─ preflight, references, payload shapes, settings


@pytest.mark.unit
def test_a_missing_skill_is_refused_before_any_step_runs(tmp_path, monkeypatch):
    _bin(tmp_path, monkeypatch, "vmware-monitor-mcp", "#!/bin/sh\n")
    store = WorkflowStore(tmp_path / "wf.db")
    calls: list[str] = []

    class Spy(StdioDispatcher):
        def __call__(self, skill, tool, params):
            calls.append(tool)
            return {"ok": True}

    wf = _wf("wf-pre", [
        WorkflowStep(index=0, action="look", skill="monitor", tool="get_alarms", params={}),
        WorkflowStep(index=1, action="clone", skill="aiops", tool="deploy_linked_clone",
                     params={}),
    ])
    out = WorkflowExecutor(store, dispatch=Spy()).run_until_checkpoint(wf)
    assert calls == []
    assert "vmware-aiops-mcp" in out["error"]


@pytest.mark.unit
def test_a_reference_to_a_missing_key_is_refused_not_replaced(tmp_path):
    store = WorkflowStore(tmp_path / "wf.db")
    seen: list[dict[str, Any]] = []
    wf = _wf("wf-ref", [
        WorkflowStep(index=0, action="plan", skill="aiops", tool="create_vm_plan", params={}),
        WorkflowStep(index=1, action="apply", skill="aiops", tool="apply_vm_plan",
                     params={"plan_id": "__from_step_0__:plan_id"}),
    ])

    def dispatch(skill: str, tool: str, params: dict[str, Any]) -> Any:
        seen.append(params)
        return {"id": "p1"}            # no plan_id key

    out = WorkflowExecutor(store, dispatch=dispatch).run_until_checkpoint(wf)
    assert out["outcome"] == "failed"
    assert len(seen) == 1, "the whole result must not be sent as plan_id"
    assert "returned no 'plan_id'" in str(wf.steps[1].result)


class _Text:
    def __init__(self, text: str) -> None:
        self.text = text


class _Frame:
    def __init__(self, texts: list[str], structured: Any = None, is_error: bool = False):
        self.content = [_Text(t) for t in texts]
        self.structuredContent = structured
        self.isError = is_error


@pytest.mark.unit
def test_payload_keeps_a_structured_error_envelope():
    frame = _Frame(['{"error": "x", "hint": "y"}'], {"error": "x", "hint": "y"}, is_error=True)
    assert dispatcher_mod._payload(frame) == {"error": "x", "hint": "y"}


@pytest.mark.unit
def test_payload_parses_each_block_of_a_list():
    frame = _Frame(['{"name": "a"}', '{"error": "b missing"}'])
    assert dispatcher_mod._payload(frame) == [{"name": "a"}, {"error": "b missing"}]


@pytest.mark.unit
@pytest.mark.parametrize("value", ["1", "true", "yes"])
def test_an_unrecognised_dispatch_value_is_refused_at_start(monkeypatch, value):
    import vmware_pilot.mcp_server.server as server

    monkeypatch.setattr("vmware_policy.serve.run_server", lambda *a, **k: None)
    monkeypatch.setenv("VMWARE_PILOT_DISPATCH", value)
    with pytest.raises(SystemExit, match="VMWARE_PILOT_DISPATCH"):
        server.main()


@pytest.mark.unit
def test_a_bad_timeout_is_refused_at_start(monkeypatch):
    import vmware_pilot.mcp_server.server as server

    monkeypatch.setattr("vmware_policy.serve.run_server", lambda *a, **k: None)
    monkeypatch.setenv("VMWARE_PILOT_DISPATCH", "stdio")
    monkeypatch.setenv("VMWARE_PILOT_DISPATCH_TIMEOUT", "ten")
    with pytest.raises(SystemExit, match="VMWARE_PILOT_DISPATCH_TIMEOUT"):
        server.main()


@pytest.mark.unit
def test_a_relative_path_entry_is_not_used(tmp_path, monkeypatch):
    rel = tmp_path / "rel"
    rel.mkdir()
    script = rel / "vmware-monitor-mcp"
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", "rel")
    monkeypatch.setattr(dispatcher_mod, "_interpreter_bin", lambda: tmp_path / "nowhere")
    with pytest.raises(DispatchRefusedError):
        dispatcher_mod.resolve_command("monitor")
