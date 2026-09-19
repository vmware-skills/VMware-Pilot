"""Tanzu Kubernetes (VKS) workflow templates — namespace + TKC cluster deploy."""

from __future__ import annotations

from vmware_pilot.templates._common import (
    Workflow,
    WorkflowState,
    WorkflowStep,
    datetime,
    new_workflow_id,
    timezone,
)


def vks_cluster_deploy(
    namespace_name: str,
    cluster_id: str,
    storage_policy: str,
    tkc_name: str,
    k8s_version: str,
    vm_class: str = "best-effort-medium",
    worker_count: int = 3,
    target: str = "",
) -> Workflow:
    """Deploy a complete VKS environment: namespace + TKC cluster + verify.

    Steps:
      1. Approve before namespace creation
      2. Create vSphere Namespace
      3. Approve before cluster creation
      4. Create TKC cluster
      5. Verify cluster health

    Every step that passes ``confirm=True`` sits behind an approval step: that
    flag tells the companion tool a human already decided.
    """
    now = datetime.now(tz=timezone.utc).isoformat()
    steps = [
        WorkflowStep(
            index=0,
            action="require_approval",
            skill="pilot",
            tool="approve",
            params={
                "message": f"Create vSphere Namespace '{namespace_name}' on cluster "
                f"'{cluster_id}' (storage policy '{storage_policy}'). Approve?"
            },
        ),
        WorkflowStep(
            index=1,
            action="create_namespace",
            skill="vks",
            tool="create_namespace",
            params={
                "name": namespace_name,
                "cluster_id": cluster_id,
                "storage_policy": storage_policy,
                "target": target,
                "confirm": True,
            },
            rollback_tool="delete_namespace",
            rollback_params={
                "name": namespace_name,
                "target": target,
                "confirm": True,
            },
        ),
        WorkflowStep(
            index=2,
            action="require_approval",
            skill="pilot",
            tool="approve",
            params={
                "message": f"Namespace '{namespace_name}' created. Deploy TKC cluster '{tkc_name}'?"
            },
        ),
        WorkflowStep(
            index=3,
            action="create_tkc",
            skill="vks",
            tool="create_tkc_cluster",
            params={
                "name": tkc_name,
                "namespace": namespace_name,
                "k8s_version": k8s_version,
                "vm_class": vm_class,
                "worker_count": worker_count,
                "target": target,
                "confirm": True,
            },
            rollback_tool="delete_tkc_cluster",
            rollback_params={
                "name": tkc_name,
                "namespace": namespace_name,
                "target": target,
                "confirm": True,
            },
        ),
        WorkflowStep(
            index=4,
            action="verify_cluster",
            skill="vks",
            tool="get_tkc_cluster",
            params={"name": tkc_name, "namespace": namespace_name, "target": target},
        ),
    ]

    return Workflow(
        id=new_workflow_id(),
        workflow_type="vks_cluster_deploy",
        state=WorkflowState.PENDING,
        steps=steps,
        params={"namespace": namespace_name, "tkc_name": tkc_name, "target": target},
        created_at=now,
        updated_at=now,
    )
