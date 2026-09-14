"""Regression — every built-in template step names a tool its skill registers.

Pilot is a dispatcher: it hands the agent ``(skill, tool, params)`` and the agent
calls that tool. Nothing checked that the tool exists. On 2026-09-14, comparing
all 68 template steps with the companions' live ``mcp.list_tools()`` found:

* ``investigate_alert`` — the flagship "find the root cause of this alert"
  template — named ``monitor:list_alarms``, ``monitor:list_events`` and
  ``aria:capacity_overview`` (none registered; the real ones are ``get_alarms``,
  ``get_events``, ``get_capacity_overview``) and passed ``resource_name`` /
  ``hours`` to Aria tools that take neither. An agent following it failed on
  step one.
* ``compliance_scan`` — called ``aria:get_capacity_overview`` without its
  required ``cluster_id``.

The truth is ``tests/fixtures/companion_tools.json``, written only by
``tests/fixtures/snapshot_companion_tools.py`` from the live registries; the
family smoke gate re-reads them and fails when the snapshot goes stale.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from vmware_pilot.mcp_server._catalog import SKILL_CATALOG
from vmware_pilot.templates import BUILTIN_TEMPLATES

FIXTURE = pathlib.Path(__file__).parents[2] / "fixtures" / "companion_tools.json"

#: Arguments for every built-in, including each variant that changes the steps.
#: Caller-supplied steps (baseline_remediate's drift items) use a real tool and
#: its required parameters, so a failure here is always the template's own.
_ARGS = {
    "clone_and_test": [
        {"target_vm": "db01", "change_spec": {"memory_mb": 32768}, "target": "vc"},
        {"target_vm": "db01", "target": "vc",
         "change_spec": {"command": "/usr/bin/apt-get", "arguments": "-y upgrade",
                         "username": "ops", "password": "pw"}},
    ],
    "incident_response": [{"alert_entity": "vm-1", "alert_name": "cpu"}],
    "investigate_alert": [
        {"alert_entity": "vm-1"},
        {"alert_entity": "vm-1", "deep_dive": True,
         "target": "vc", "aria_target": "ops"},
    ],
    "plan_and_approve": [{"operations": [{"action": "power_off", "vm_name": "a"}]}],
    "compliance_scan": [{}, {"cluster_id": "c-1"},
                        {"check_alarms": False, "check_capacity": False}],
    "network_segment_setup": [{"segment_id": "s", "display_name": "s", "subnet": "10.0.0.1/24",
                               "transport_zone_path": "/tz", "tier1_id": "t1",
                               "nat_source": "10.0.0.0/24", "nat_translated": "1.1.1.1",
                               "dfw_policy_id": "p"}],
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
    "baseline_remediate": [{"drift_items": [{"resource": "r", "skill": "aiops", "action": "fix",
                                             "tool": "vm_power_off", "params": {"vm_name": "r"}}]}],
}


def _registry() -> dict:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert data and all(data.values()), f"{FIXTURE.name} is empty — the check would verify nothing"
    return data


def _skill(name: str) -> str:
    return name.removeprefix("vmware-")


def _step_problems(template: str, step, registry: dict) -> list[str]:
    where = f"{template} step {step.index} ({step.action})"
    skill = _skill(step.skill)
    if skill not in registry:
        return [f"{where}: unknown skill {step.skill!r}"]
    tool = registry[skill].get(step.tool)
    if tool is None:
        return [f"{where}: {skill} registers no tool {step.tool!r}"]
    given = set(step.params)
    problems = [
        f"{where}: {skill}:{step.tool} has no parameter {p!r} (it takes {tool['properties']})"
        for p in sorted(given - set(tool["properties"]))
    ]
    problems += [f"{where}: {skill}:{step.tool} requires {p!r}, which the step does not pass"
                 for p in tool["required"] if p not in given]
    return problems


@pytest.mark.unit
def test_every_builtin_has_arguments_here():
    assert set(_ARGS) == set(BUILTIN_TEMPLATES), "a built-in template is not checked"


@pytest.mark.unit
def test_every_template_step_names_a_registered_tool_with_real_parameters():
    registry = _registry()
    problems, checked = [], 0
    for name, variants in _ARGS.items():
        for kwargs in variants:
            wf = BUILTIN_TEMPLATES[name](**kwargs)
            assert wf.steps, f"{name}({kwargs}) built no steps"
            for step in wf.steps:
                if _skill(step.skill) == "pilot":
                    continue  # approval gates are Pilot's own
                checked += 1
                problems += _step_problems(name, step, registry)
    # Positive control: the templates dispatch well over 40 companion steps.
    assert checked >= 40, f"only {checked} steps were checked"
    assert not problems, "\n".join(problems)


@pytest.mark.unit
def test_the_skill_catalog_lists_only_registered_tools():
    registry = _registry()
    missing = [f"{skill}:{tool}" for skill, info in SKILL_CATALOG.items()
               for tool in info.get("tools", {}) if tool not in registry.get(_skill(skill), {})]
    assert sum(len(i.get("tools", {})) for i in SKILL_CATALOG.values()) > 0
    assert not missing, f"catalog names tools no skill registers: {missing}"
