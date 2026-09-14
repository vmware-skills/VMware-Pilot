"""Monitoring & incident workflow templates — incident response, causal-chain
alert investigation, and read-only compliance scan."""

from __future__ import annotations

from vmware_pilot.templates._common import (
    Workflow,
    WorkflowState,
    WorkflowStep,
    datetime,
    new_workflow_id,
    parallel_group,
    timezone,
)


def incident_response(
    alert_entity: str,
    alert_name: str,
    target: str = "",
) -> Workflow:
    """Auto-diagnose and remediate an alert.

    Steps:
      1. Get alarm details
      2. Collect diagnostics (events, VM info)
      3. Await approval for remediation
      4. Execute remediation (acknowledge alarm)
    """
    now = datetime.now(tz=timezone.utc).isoformat()

    steps = [
        WorkflowStep(
            index=0,
            action="get_alarms",
            skill="monitor",
            tool="get_alarms",
            params={"target": target},
        ),
        WorkflowStep(
            index=1,
            action="get_events",
            skill="monitor",
            tool="get_events",
            params={"hours": 1, "severity": "critical", "target": target},
        ),
        WorkflowStep(
            index=2,
            action="require_approval",
            skill="pilot",
            tool="approve",
            params={"message": f"Alert '{alert_name}' on '{alert_entity}'. Acknowledge and clear?"},
        ),
        WorkflowStep(
            index=3,
            action="acknowledge",
            skill="aiops",
            tool="acknowledge_vcenter_alarm",
            params={"entity_name": alert_entity, "alarm_name": alert_name, "target": target},
        ),
    ]

    return Workflow(
        id=new_workflow_id(),
        workflow_type="incident_response",
        state=WorkflowState.PENDING,
        steps=steps,
        params={
            "alert_entity": alert_entity,
            "alert_name": alert_name,
            "target": target,
        },
        created_at=now,
        updated_at=now,
    )


def compliance_scan(
    target: str = "",
    check_alarms: bool = True,
    check_capacity: bool = True,
    cluster_id: str = "",
) -> Workflow:
    """Periodic compliance scan — collect health data, report, and flag issues.

    Steps:
      1. Check active alarms (monitor)
      2. Check capacity — Aria's capacity overview for ``cluster_id`` when one is
         given, otherwise vmware-monitor's per-cluster rollup (CPU/memory %)
      3. Collect anomalies (aria)

    All steps are read-only — no approval gate needed.

    Args:
        target: vCenter/Aria target name.
        check_alarms: Include alarm check (default True).
        check_capacity: Include capacity check (default True).
        cluster_id: Aria resource id of a cluster. Aria's ``get_capacity_overview``
            requires one; without it the capacity step reads monitor's
            ``cluster_health_summary`` instead, which covers every cluster.
    """
    now = datetime.now(tz=timezone.utc).isoformat()
    steps = []
    idx = 0

    if check_alarms:
        steps.append(
            WorkflowStep(
                index=idx,
                action="check_alarms",
                skill="monitor",
                tool="get_alarms",
                params={"target": target},
            )
        )
        idx += 1

    if check_capacity:
        # get_capacity_overview requires cluster_id; the step used to omit it,
        # so an agent following this template was refused on this call.
        capacity = (
            WorkflowStep(
                index=idx,
                action="check_capacity",
                skill="aria",
                tool="get_capacity_overview",
                params={"cluster_id": cluster_id, "target": target},
            )
            if cluster_id
            else WorkflowStep(
                index=idx,
                action="check_capacity",
                skill="monitor",
                tool="cluster_health_summary",
                params={"target": target},
            )
        )
        steps.append(capacity)
        idx += 1

    steps.append(
        WorkflowStep(
            index=idx,
            action="check_anomalies",
            skill="aria",
            tool="list_anomalies",
            params={"target": target},
        )
    )

    return Workflow(
        id=new_workflow_id(),
        workflow_type="compliance_scan",
        state=WorkflowState.PENDING,
        steps=steps,
        params={
            "target": target,
            "check_alarms": check_alarms,
            "check_capacity": check_capacity,
            "cluster_id": cluster_id,
        },
        created_at=now,
        updated_at=now,
    )


def investigate_alert(
    alert_entity: str,
    alert_name: str = "",
    deep_dive: bool = False,
    target: str = "",
    aria_target: str = "",
) -> Workflow:
    """Causal-chain root-cause investigation per investigation-protocol.md.

    Encodes the four-criteria root cause completeness loop from the Enterprise
    Harness Engineering framework as a pilot workflow.

    Stage 1 — parallel-group "round1-gather":
        Active vCenter alarms, recent vCenter events and active Aria alerts,
        concurrently. All three are read-only and independent. None of these
        tools filters by object, so the agent keeps the rows that concern
        ``alert_entity`` (alarms and alerts carry the object's name).

    Stage 2 — synthesis checkpoint:
        Pause for the AI agent to apply the four criteria
        (falsifiability / sufficiency / necessity / mechanism) and decide
        whether the root cause is complete.

    Stage 3 (only when ``deep_dive=True``) — parallel-group "round2-gather":
        Broader evidence: Aria anomalies, per-cluster capacity from
        vmware-monitor, and Aria alerts including cancelled ones. Used when
        round 1 fails the necessity or mechanism check.

    Stage 4 (only when ``deep_dive=True``) — final synthesis checkpoint.

    The agent is responsible for producing the structured report (root cause
    plus four-criteria evidence). Pilot only orchestrates the data gathering
    and the human-approval gates.

    Every step names a tool its skill registers, with parameters it accepts —
    tests/eval/regression/test_template_steps_name_real_tools.py holds that
    against the companions' registries. Until 2026-09-14 this template named
    ``list_alarms``, ``list_events`` and ``capacity_overview``, none of which
    exist, so an agent following it failed on the first step.

    Args:
        alert_entity: Resource that triggered the alert (VM name, host, cluster).
        alert_name: Optional human-readable alert label, surfaced in approval prompts.
        deep_dive: If True, append a second round of broader gathering and a
            second synthesis checkpoint (max three rounds total — a third would
            require a follow-up workflow).
        target: vCenter target for the vmware-monitor steps; also the Aria
            target unless ``aria_target`` is given. Empty = each skill's default.
        aria_target: Aria target for the vmware-aria steps, when the Aria target
            is named differently from the vCenter one (the usual case).
    """
    now = datetime.now(tz=timezone.utc).isoformat()
    label = alert_name or alert_entity
    ops_target = aria_target or target

    round1 = [
        WorkflowStep(
            index=0,
            action="gather_alarms",
            skill="monitor",
            tool="get_alarms",
            params={"target": target},
        ),
        WorkflowStep(
            index=1,
            action="gather_events",
            skill="monitor",
            tool="get_events",
            params={"hours": 2, "target": target},
        ),
        WorkflowStep(
            index=2,
            action="gather_aria_alerts",
            skill="aria",
            tool="list_alerts",
            params={"active_only": True, "target": ops_target},
        ),
    ]
    parallel_group("round1-gather", round1)

    checkpoint1 = WorkflowStep(
        index=3,
        action="require_approval",
        skill="pilot",
        tool="approve",
        params={
            "message": (
                f"Round 1 evidence gathered for '{label}'. "
                f"These reads cover the whole inventory — keep what concerns '{alert_entity}'. "
                "Apply the four-criteria check from references/investigation-protocol.md: "
                "(1) falsifiability, (2) sufficiency, (3) necessity, (4) mechanism. "
                "APPROVE if root cause is complete and you are ready to write the report. "
                "REJECT to escalate or relaunch with deep_dive=True."
            ),
        },
    )

    steps = round1 + [checkpoint1]

    if deep_dive:
        round2 = [
            WorkflowStep(
                index=4,
                action="gather_anomalies",
                skill="aria",
                tool="list_anomalies",
                params={"target": ops_target},
            ),
            WorkflowStep(
                index=5,
                action="gather_capacity",
                skill="monitor",
                tool="cluster_health_summary",
                params={"target": target},
            ),
            WorkflowStep(
                index=6,
                action="gather_recent_alerts",
                skill="aria",
                tool="list_alerts",
                params={"active_only": False, "target": ops_target},
            ),
        ]
        parallel_group("round2-gather", round2)

        checkpoint2 = WorkflowStep(
            index=7,
            action="require_approval",
            skill="pilot",
            tool="approve",
            params={
                "message": (
                    f"Round 2 evidence gathered for '{label}'. "
                    "Re-apply the four-criteria check. "
                    "APPROVE if root cause is now complete. "
                    "REJECT and escalate if a third round is needed — "
                    "the protocol caps at three rounds before human handoff."
                ),
            },
        )
        steps += round2 + [checkpoint2]

    return Workflow(
        id=new_workflow_id(),
        workflow_type="investigate_alert",
        state=WorkflowState.PENDING,
        steps=steps,
        params={
            "alert_entity": alert_entity,
            "alert_name": alert_name,
            "deep_dive": deep_dive,
            "target": target,
            "aria_target": aria_target,
        },
        created_at=now,
        updated_at=now,
    )
