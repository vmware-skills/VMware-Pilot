"""Pilot's opt-in stdio dispatcher (HLD §19, D-25).

The end-to-end tests start a real child MCP server over stdio — a stand-in
``vmware-monitor-mcp`` written into a temporary bin directory — because the
point of the dispatcher is the process boundary: spawning, the protocol, the
result frame, the timeout. A mock of ``_call`` would test none of that.
"""

from __future__ import annotations

import os
import stat
import sys
import textwrap
from pathlib import Path

import pytest

from vmware_pilot import dispatcher as dispatcher_mod
from vmware_pilot.dispatcher import DispatchRefusedError, StdioDispatcher, resolve_command
from vmware_pilot.executor import WorkflowExecutor
from vmware_pilot.lifecycle_gate import rollback_blast_radius
from vmware_pilot.models import Workflow, WorkflowState, WorkflowStep, WorkflowStore

_FAKE_SERVER = textwrap.dedent('''\
    #!{python}
    import json, os, sys, time
    from mcp.server.fastmcp import FastMCP

    LOG = os.environ["FAKE_SKILL_LOG"]
    mcp = FastMCP("fake")

    def _log(tool, args):
        with open(LOG, "a") as fh:
            fh.write(json.dumps({{"tool": tool, "args": args}}) + "\\n")

    @mcp.tool()
    def get_alarms(target: str = "") -> dict:
        _log("get_alarms", {{"target": target}})
        return {{"items": [], "target": target}}

    @mcp.tool()
    def investigate_vm(vm_name: str, target: str = "") -> dict:
        _log("investigate_vm", {{"vm_name": vm_name}})
        raise RuntimeError("VM 'nope' not found")

    @mcp.tool()
    def list_all_clusters(target: str = "") -> dict:
        _log("list_all_clusters", {{"target": target}})
        return {{"plan_id": "plan-42", "items": []}}

    @mcp.tool()
    def get_events(plan_id: str = "", target: str = "") -> str:
        _log("get_events", {{"plan_id": plan_id}})
        return "events for " + plan_id

    @mcp.tool()
    def get_vm_info(vm_name: str, target: str = "") -> dict:
        _log("get_vm_info", {{"vm_name": vm_name}})
        time.sleep(30)
        return {{"name": vm_name}}

    mcp.run()
''')


@pytest.fixture
def fake_bin(tmp_path, monkeypatch):
    """A PATH holding only a stand-in vmware-monitor-mcp; returns its call log."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    script = bindir / "vmware-monitor-mcp"
    script.write_text(_FAKE_SERVER.format(python=sys.executable), encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    log = tmp_path / "calls.jsonl"
    # Only the stand-in: a real skill installed on this machine must not answer.
    monkeypatch.setenv("PATH", str(bindir))
    monkeypatch.setenv("FAKE_SKILL_LOG", str(log))
    # Never resolve a real skill installed next to the test interpreter.
    monkeypatch.setattr(dispatcher_mod, "_interpreter_bin", lambda: tmp_path / "nowhere")
    return log


def _calls(log: Path) -> list[str]:
    if not log.exists():
        return []
    import json

    return [json.loads(line)["tool"] for line in log.read_text(encoding="utf-8").splitlines()]


def _wf(wid: str, steps: list[WorkflowStep]) -> Workflow:
    return Workflow(id=wid, workflow_type="test", state=WorkflowState.PENDING, steps=steps,
                    params={}, created_at="", updated_at="")


# ── name resolution ──────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize("skill,why", [
    ("pilot", "dispatch to itself"), ("vmware-pilot", "dispatch to itself"),
    ("unknown", "not a VMware family skill"), ("../aiops", "not a family skill name"),
    ("aiops; rm", "not a family skill name"), ("", "names no skill"), (None, "names no skill"),
])
def test_only_family_skills_other_than_pilot_resolve(skill, why, fake_bin):
    # Each refusal must hold on its own: plant an executable for every name, so
    # "not installed" can never be the reason a bad name is refused.
    bindir = Path(os.environ["PATH"])
    for planted in ("vmware-pilot-mcp", "vmware-unknown-mcp"):
        (bindir / planted).write_text("#!/bin/sh\n", encoding="utf-8")
        (bindir / planted).chmod(0o755)
    with pytest.raises(DispatchRefusedError, match=why):
        resolve_command(skill)


@pytest.mark.unit
@pytest.mark.parametrize("skill", ["monitor", "vmware-monitor", "vmware_monitor", "Monitor"])
def test_a_family_skill_resolves_to_its_mcp_command(skill, fake_bin):
    assert Path(resolve_command(skill)).name == "vmware-monitor-mcp"


@pytest.mark.unit
def test_a_skill_that_is_not_installed_says_how_to_install_it(fake_bin):
    with pytest.raises(DispatchRefusedError, match="vmware-aiops-mcp") as err:
        resolve_command("aiops")
    assert "install" in str(err.value)


# ── end to end through a real child process ──────────────────────────────


@pytest.mark.unit
def test_a_step_runs_in_the_skills_own_server(tmp_path, fake_bin):
    store = WorkflowStore(tmp_path / "wf.db")
    wf = _wf("wf-d1", [WorkflowStep(index=0, action="look", skill="monitor", tool="get_alarms",
                                    params={"target": "lab"})])
    out = WorkflowExecutor(store, dispatch=StdioDispatcher()).run_until_checkpoint(wf)
    assert out["outcome"] == "completed", out
    assert _calls(fake_bin) == ["get_alarms"]


@pytest.mark.unit
def test_nothing_after_the_gate_runs_before_approve(tmp_path, fake_bin):
    store = WorkflowStore(tmp_path / "wf.db")
    wf = _wf("wf-d2", [
        WorkflowStep(index=0, action="look", skill="monitor", tool="get_alarms", params={}),
        WorkflowStep(index=1, action="require_approval", skill="pilot", tool="approve",
                     params={"message": "go?"}),
        WorkflowStep(index=2, action="look2", skill="monitor", tool="get_alarms", params={}),
    ])
    ex = WorkflowExecutor(store, dispatch=StdioDispatcher())
    assert ex.run_until_checkpoint(wf)["outcome"] == "awaiting_approval"
    assert _calls(fake_bin) == ["get_alarms"]
    assert ex.resume_after_approval(wf, approver="wei")["outcome"] == "completed"
    assert _calls(fake_bin) == ["get_alarms", "get_alarms"]


@pytest.mark.unit
def test_a_tool_error_in_the_child_fails_the_step(tmp_path, fake_bin):
    store = WorkflowStore(tmp_path / "wf.db")
    wf = _wf("wf-d3", [WorkflowStep(index=0, action="check", skill="monitor",
                                    tool="investigate_vm", params={"vm_name": "nope"})])
    out = WorkflowExecutor(store, dispatch=StdioDispatcher()).run_until_checkpoint(wf)
    assert out["outcome"] == "failed"
    assert "not found" in str(wf.steps[0].result)


@pytest.mark.unit
def test_a_timeout_is_an_unknown_effect_not_a_plain_failure(tmp_path, fake_bin):
    store = WorkflowStore(tmp_path / "wf.db")
    wf = _wf("wf-d4", [WorkflowStep(index=0, action="slow", skill="monitor", tool="get_vm_info",
                                    params={"vm_name": "x"})])
    out = WorkflowExecutor(store, dispatch=StdioDispatcher(timeout=3)).run_until_checkpoint(wf)
    assert out["outcome"] == "failed"
    assert wf.steps[0].status == "interrupted"
    assert "unknown" in str(wf.steps[0].result).lower()
    assert [e["index"] for e in rollback_blast_radius(wf)["unknown_effects"]] == [0]


@pytest.mark.unit
def test_a_network_caller_cannot_dispatch(tmp_path, fake_bin, monkeypatch):
    """Child servers have no caller identity: roles would not apply (HLD §19)."""
    import vmware_policy.caller as caller_mod

    monkeypatch.setattr(caller_mod, "current_caller", lambda: "token:bob")
    with pytest.raises(DispatchRefusedError, match="network"):
        StdioDispatcher()("monitor", "get_alarms", {})
    assert _calls(fake_bin) == []


# ── wiring into the server ───────────────────────────────────────────────


@pytest.mark.unit
def test_off_by_default(monkeypatch):
    import vmware_pilot.mcp_server.server as server

    monkeypatch.delenv("VMWARE_PILOT_DISPATCH", raising=False)
    monkeypatch.setattr(server, "_executor", None)
    monkeypatch.setattr(server, "_dispatch_enabled", False)
    assert server._get_executor()._has_dispatch is False


@pytest.mark.unit
@pytest.mark.parametrize("value", ["stdio", "STDIO", " stdio "])
def test_the_environment_variable_turns_it_on(monkeypatch, value):
    import vmware_pilot.mcp_server.server as server

    monkeypatch.setenv("VMWARE_PILOT_DISPATCH", value)
    monkeypatch.setattr(server, "_executor", None)
    monkeypatch.setattr(server, "_dispatch_enabled", False)
    assert server._get_executor()._has_dispatch is True


@pytest.mark.unit
@pytest.mark.parametrize("value", ["1", "yes", "https", ""])
def test_other_values_do_not_turn_it_on(monkeypatch, value):
    import vmware_pilot.mcp_server.server as server

    monkeypatch.setenv("VMWARE_PILOT_DISPATCH", value)
    monkeypatch.setattr(server, "_executor", None)
    monkeypatch.setattr(server, "_dispatch_enabled", False)
    assert server._get_executor()._has_dispatch is False


@pytest.mark.unit
@pytest.mark.parametrize("transport", ["https", "http"])
@pytest.mark.parametrize("how", ["flag", "env"])
def test_dispatch_on_a_network_transport_refuses_to_start(monkeypatch, transport, how):
    import vmware_pilot.mcp_server.server as server

    started = []
    monkeypatch.setattr("vmware_policy.serve.run_server", lambda *a, **k: started.append(k))
    monkeypatch.setenv("VMWARE_PILOT_DISPATCH", "stdio" if how == "env" else "")
    with pytest.raises(SystemExit) as err:
        server.main(transport=transport, dispatch=(how == "flag"))
    assert "stdio" in str(err.value)
    assert started == []


@pytest.mark.unit
def test_the_mcp_tools_run_steps_when_dispatch_is_on(tmp_path, fake_bin, monkeypatch):
    import vmware_pilot.mcp_server.server as server

    store = WorkflowStore(tmp_path / "wf.db")
    monkeypatch.setattr(server, "_store", store)
    monkeypatch.setattr(server, "_executor", WorkflowExecutor(store, dispatch=StdioDispatcher()))
    wf = _wf("wf-d5", [WorkflowStep(index=0, action="look", skill="monitor",
                                    tool="get_alarms", params={"target": "lab"})])
    store.save(wf)
    out = server.run_workflow("wf-d5")
    assert out["outcome"] == "completed", out
    assert "pending_dispatch" not in out
    assert "get_alarms" in _calls(fake_bin)


@pytest.mark.unit
def test_a_step_reference_reads_the_tools_own_result(tmp_path, fake_bin):
    """Live run 2026-10-18: results were stored as raw MCP frames, so
    __from_step_N__:key (plan_and_approve's plan_id) could not find the key."""
    import json

    store = WorkflowStore(tmp_path / "wf.db")
    wf = _wf("wf-d6", [
        WorkflowStep(index=0, action="plan", skill="monitor", tool="list_all_clusters",
                     params={}),
        WorkflowStep(index=1, action="use", skill="monitor", tool="get_events",
                     params={"plan_id": "__from_step_0__:plan_id"}),
    ])
    out = WorkflowExecutor(store, dispatch=StdioDispatcher()).run_until_checkpoint(wf)
    assert out["outcome"] == "completed", out
    args = [json.loads(line)["args"] for line in fake_bin.read_text(encoding="utf-8").splitlines()]
    assert args[1] == {"plan_id": "plan-42"}
    assert wf.steps[0].result == {"plan_id": "plan-42", "items": []}
    assert wf.steps[1].result == "events for plan-42"


@pytest.mark.unit
def test_rollback_does_not_list_read_only_steps(tmp_path):
    """Live run 2026-10-18: get_vm_info was reported 'not_reversible … stays applied'."""
    store = WorkflowStore(tmp_path / "wf.db")
    wf = _wf("wf-d7", [
        WorkflowStep(index=0, action="look", skill="monitor", tool="get_vm_info",
                     params={"vm_name": "a"}),
        WorkflowStep(index=1, action="resize", skill="aiops", tool="reconfigure_vm",
                     params={"vm_name": "a", "cpu": 2}),
        WorkflowStep(index=2, action="fail", skill="aiops", tool="failing_tool", params={}),
    ])
    ex = WorkflowExecutor(store, dispatch=lambda s, t, p: {"error": "x"} if t == "failing_tool"
                          else {"ok": True})
    ex.run_until_checkpoint(wf)
    radius = rollback_blast_radius(wf)
    assert [e["index"] for e in radius["left_in_place"]] == [1]
    result = ex.rollback(wf)
    assert [r["step"] for r in result["rollback_results"]] == [1]
