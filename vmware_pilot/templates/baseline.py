"""Baseline workflow templates — capture infra state, audit for drift, remediate."""

from __future__ import annotations

from vmware_pilot.templates._common import (
    Any,
    Workflow,
    WorkflowState,
    WorkflowStep,
    datetime,
    new_workflow_id,
    timezone,
)


def baseline_capture(
    target: str = "",
    include_vms: bool = True,
    include_hosts: bool = True,
    include_network: bool = True,
    include_storage: bool = True,
    include_alarms: bool = True,
    baseline_name: str = "",
) -> Workflow:
    """Capture current infrastructure state as a baseline snapshot.

    Collects configuration from the relevant skills. Pilot does not write the
    baseline file: ``params["baseline_path"]`` (~/.vmware/baselines/{name}.json)
    is where the calling agent should save the collected step results. That
    data is an inventory of VMs, hosts, network segments, datastores and
    alarms — sensitive; save it owner-only. Use baseline_audit to compare later.

    Steps:
      1. Collect VM inventory (monitor)
      2. Collect host inventory (monitor)
      3. Collect network segments (nsx)
      4. Collect storage/datastores (storage)
      5. Collect active alarms (monitor)

    Args:
        target: vCenter target name.
        include_vms: Capture VM configurations.
        include_hosts: Capture ESXi host configurations.
        include_network: Capture NSX segment/gateway state.
        include_storage: Capture datastore/vSAN state.
        include_alarms: Capture current alarm state.
        baseline_name: Name for this baseline (default: auto-generated with timestamp).
    """
    now = datetime.now(tz=timezone.utc).isoformat()
    name = baseline_name or f"baseline-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    steps: list[WorkflowStep] = []
    idx = 0

    if include_vms:
        steps.append(
            WorkflowStep(
                index=idx,
                action="capture_vms",
                skill="monitor",
                tool="list_virtual_machines",
                params={"target": target},
            )
        )
        idx += 1

    if include_hosts:
        steps.append(
            WorkflowStep(
                index=idx,
                action="capture_hosts",
                skill="monitor",
                tool="list_esxi_hosts",
                params={"target": target},
            )
        )
        idx += 1

    if include_network:
        steps.append(
            WorkflowStep(
                index=idx,
                action="capture_segments",
                skill="nsx",
                tool="list_segments",
                params={"target": target},
            )
        )
        idx += 1

    if include_storage:
        steps.append(
            WorkflowStep(
                index=idx,
                action="capture_datastores",
                skill="storage",
                tool="list_all_datastores",
                params={"target": target},
            )
        )
        idx += 1

    if include_alarms:
        steps.append(
            WorkflowStep(
                index=idx,
                action="capture_alarms",
                skill="monitor",
                tool="get_alarms",
                params={"target": target},
            )
        )
        idx += 1

    return Workflow(
        id=new_workflow_id(),
        workflow_type="baseline_capture",
        state=WorkflowState.PENDING,
        steps=steps,
        params={
            "baseline_name": name,
            "baseline_path": f"~/.vmware/baselines/{name}.json",
            "target": target,
            "include_vms": include_vms,
            "include_hosts": include_hosts,
            "include_network": include_network,
            "include_storage": include_storage,
            "include_alarms": include_alarms,
        },
        created_at=now,
        updated_at=now,
    )


def baseline_audit(
    baseline_name: str = "latest",
    target: str = "",
    include_vms: bool = True,
    include_hosts: bool = True,
    include_network: bool = True,
    include_storage: bool = True,
) -> Workflow:
    """Audit current state against a saved baseline — detect configuration drift.

    Collects current state using the same tools as baseline_capture. The
    comparison with the saved baseline file is the calling agent's job — pilot
    does not read the file, and the workflow's ``diff_report`` stays empty.

    Steps:
      1. Collect current VM inventory
      2. Collect current host inventory
      3. Collect current network state
      4. Collect current storage state
      5. Check Aria anomalies for context

    Args:
        baseline_name: Name of the baseline to compare against (default: "latest").
        target: vCenter target name.
    """
    now = datetime.now(tz=timezone.utc).isoformat()
    steps: list[WorkflowStep] = []
    idx = 0

    if include_vms:
        steps.append(
            WorkflowStep(
                index=idx,
                action="current_vms",
                skill="monitor",
                tool="list_virtual_machines",
                params={"target": target},
            )
        )
        idx += 1

    if include_hosts:
        steps.append(
            WorkflowStep(
                index=idx,
                action="current_hosts",
                skill="monitor",
                tool="list_esxi_hosts",
                params={"target": target},
            )
        )
        idx += 1

    if include_network:
        steps.append(
            WorkflowStep(
                index=idx,
                action="current_segments",
                skill="nsx",
                tool="list_segments",
                params={"target": target},
            )
        )
        idx += 1

    if include_storage:
        steps.append(
            WorkflowStep(
                index=idx,
                action="current_datastores",
                skill="storage",
                tool="list_all_datastores",
                params={"target": target},
            )
        )
        idx += 1

    # Capacity check for context
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
        workflow_type="baseline_audit",
        state=WorkflowState.PENDING,
        steps=steps,
        params={
            "baseline_name": baseline_name,
            "baseline_path": f"~/.vmware/baselines/{baseline_name}.json",
            "target": target,
        },
        created_at=now,
        updated_at=now,
    )


def baseline_remediate(
    drift_items: list[dict[str, Any]],
    target: str = "",
) -> Workflow:
    """Remediate configuration drifts detected by baseline_audit.

    Takes a list of drift items (from audit results) and generates
    remediation steps with approval gates.

    Each drift_item: {type, resource, expected, actual, action, skill, tool, params}

    Steps:
      1. Review drift summary
      2. Approve remediation
      3. Execute remediation steps (one per drift item)
      4. Re-audit to verify fixes

    Args:
        drift_items: List of drift items to remediate. Each must have:
            action (str), skill (str), tool (str), params (dict).
            Optional: resource (str), rollback_tool (str), rollback_params (dict).
        target: vCenter target name.
    """
    now = datetime.now(tz=timezone.utc).isoformat()
    steps: list[WorkflowStep] = []
    idx = 0

    # Pre-check: current alarms
    steps.append(
        WorkflowStep(
            index=idx,
            action="pre_check_alarms",
            skill="monitor",
            tool="get_alarms",
            params={"target": target},
        )
    )
    idx += 1

    # Approval before remediation
    resource_names = [d.get("resource", "unknown") for d in drift_items]
    steps.append(
        WorkflowStep(
            index=idx,
            action="require_approval",
            skill="pilot",
            tool="approve",
            params={
                "message": f"Remediate {len(drift_items)} drift(s): "
                f"{', '.join(resource_names[:5])}"
                f"{'...' if len(resource_names) > 5 else ''}. Proceed?"
            },
        )
    )
    idx += 1

    # One step per drift item
    for i, item in enumerate(drift_items):
        steps.append(
            WorkflowStep(
                index=idx,
                action=f"fix_{item.get('resource', f'item_{i}')}",
                skill=item.get("skill", "aiops"),
                tool=item.get("tool", "vm_create_plan"),
                params=item.get("params", {}),
                rollback_tool=item.get("rollback_tool", ""),
                rollback_params=item.get("rollback_params", {}),
            )
        )
        idx += 1

    # Post-fix verification
    steps.append(
        WorkflowStep(
            index=idx,
            action="post_verify",
            skill="monitor",
            tool="get_alarms",
            params={"target": target},
        )
    )

    return Workflow(
        id=new_workflow_id(),
        workflow_type="baseline_remediate",
        state=WorkflowState.PENDING,
        steps=steps,
        params={"drift_count": len(drift_items), "target": target},
        created_at=now,
        updated_at=now,
    )
