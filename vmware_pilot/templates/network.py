"""NSX network workflow templates — segment + gateway + NAT + firewall setup."""

from __future__ import annotations

from vmware_pilot.templates._common import (
    Workflow,
    WorkflowState,
    WorkflowStep,
    datetime,
    new_workflow_id,
    timezone,
)


def network_segment_setup(
    segment_id: str,
    display_name: str,
    subnet: str,
    transport_zone_path: str,
    tier1_id: str = "",
    tier0_path: str = "",
    nat_source: str = "",
    nat_translated: str = "",
    dfw_policy_id: str = "",
    target: str = "",
) -> Workflow:
    """Set up a complete app network: segment + gateway + NAT + firewall.

    Known limitation (not restructured; see RELEASE_NOTES "Known and not
    fixed"): every NSX create runs before the only approval step, which
    guards only the final verification.

    Steps:
      1. Create Tier-1 gateway (if tier1_id provided)
      2. Create network segment
      3. Create NAT rule (if nat_source provided)
      4. Create DFW policy (if dfw_policy_id provided)
      5. Approve before finalizing
      6. Verify segment connectivity
    """
    now = datetime.now(tz=timezone.utc).isoformat()
    steps: list[WorkflowStep] = []
    idx = 0

    if tier1_id:
        steps.append(
            WorkflowStep(
                index=idx,
                action="create_gateway",
                skill="nsx",
                tool="create_tier1_gateway",
                params={
                    "tier1_id": tier1_id,
                    "display_name": f"{display_name}-gw",
                    "tier0_path": tier0_path,
                    "target": target,
                },
                rollback_tool="delete_tier1_gateway",
                rollback_params={"tier1_id": tier1_id, "target": target, "confirm": True},
            )
        )
        idx += 1

    steps.append(
        WorkflowStep(
            index=idx,
            action="create_segment",
            skill="nsx",
            tool="create_segment",
            params={
                "segment_id": segment_id,
                "display_name": display_name,
                "transport_zone_path": transport_zone_path,
                "subnet": subnet,
                "target": target,
            },
            rollback_tool="delete_segment",
            rollback_params={"segment_id": segment_id, "target": target, "confirm": True},
        )
    )
    idx += 1

    if nat_source and nat_translated and tier1_id:
        steps.append(
            WorkflowStep(
                index=idx,
                action="create_nat",
                skill="nsx",
                tool="create_nat_rule",
                params={
                    "tier1_id": tier1_id,
                    "rule_id": f"{segment_id}-snat",
                    "action": "SNAT",
                    "source_network": nat_source,
                    "translated_network": nat_translated,
                    "target": target,
                },
                rollback_tool="delete_nat_rule",
                rollback_params={
                    "tier1_id": tier1_id,
                    "rule_id": f"{segment_id}-snat",
                    "target": target,
                    "confirm": True,
                },
            )
        )
        idx += 1

    if dfw_policy_id:
        steps.append(
            WorkflowStep(
                index=idx,
                action="create_firewall",
                skill="nsx-security",
                tool="create_dfw_policy",
                params={
                    "policy_id": dfw_policy_id,
                    "display_name": f"{display_name}-policy",
                    "target": target,
                },
                rollback_tool="delete_dfw_policy",
                rollback_params={"policy_id": dfw_policy_id, "target": target, "confirm": True},
            )
        )
        idx += 1

    steps.append(
        WorkflowStep(
            index=idx,
            action="require_approval",
            skill="pilot",
            tool="approve",
            params={"message": f"Network '{display_name}' created. Verify and finalize?"},
        )
    )
    idx += 1

    steps.append(
        WorkflowStep(
            index=idx,
            action="verify",
            skill="nsx",
            tool="list_segments",
            params={"target": target},
        )
    )

    return Workflow(
        id=new_workflow_id(),
        workflow_type="network_segment_setup",
        state=WorkflowState.PENDING,
        steps=steps,
        params={"segment_id": segment_id, "display_name": display_name, "target": target},
        created_at=now,
        updated_at=now,
    )
