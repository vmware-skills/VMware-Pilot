"""Static catalog of skills + tools used for custom workflow design.

Building-block reference surfaced by ``get_skill_catalog`` and ``design_workflow``.
"""

from __future__ import annotations

SKILL_CATALOG = {
    "aiops": {
        "description": "VM lifecycle, deployment, clusters, guest operations, alarm management",
        "tools": {
            "vm_power_on": {"risk": "medium", "desc": "Power on a VM"},
            # risk high, not medium: vmware-aiops itself publishes this tool with
            # destructiveHint=True. Pilot's own label used to be the more
            # optimistic of the two, and since the label is what the approval
            # gate reads, the disagreement resolved in favour of not gating it.
            "vm_power_off": {"risk": "high", "desc": "Power off a VM (graceful/force)"},
            # clone_and_test dispatches this for a cpu/memory change_spec. Missing from
            # the catalog it read as "unclassifiable", and run_workflow refused the
            # built-in template unless force=True. Label = what vmware-aiops
            # publishes: risk medium, destructiveHint False.
            "vm_reconfigure": {"risk": "medium", "desc": "Change a VM's CPU / memory"},
            "deploy_linked_clone": {"risk": "medium", "desc": "Instant clone from snapshot"},
            "deploy_vm_from_template": {"risk": "medium", "desc": "Clone from vSphere template"},
            "deploy_vm_from_ova": {"risk": "medium", "desc": "Deploy from OVA file"},
            "batch_clone_vms": {"risk": "medium", "desc": "Batch clone multiple VMs"},
            # risk high, as for vm_power_off: vmware-aiops publishes all four guest
            # tools with destructiveHint=True (its own risk_level is medium). They
            # run an arbitrary command or write an arbitrary file inside the guest
            # as the account the caller names — `/bin/rm -rf /` is a well-formed
            # argument. Labelled medium, a custom workflow whose only step was
            # that command was saved and dispatched with no approval gate.
            # vm_guest_download is not here: aiops keeps it destructiveHint=False.
            "vm_guest_exec": {"risk": "high", "desc": "Execute command inside VM"},
            "vm_guest_exec_output": {"risk": "high", "desc": "Execute and capture stdout"},
            "vm_guest_upload": {"risk": "high", "desc": "Upload file to VM"},
            "vm_guest_provision": {"risk": "high", "desc": "Multi-step VM provisioning"},
            "vm_create_plan": {"risk": "medium", "desc": "Create multi-step execution plan"},
            "vm_apply_plan": {"risk": "medium", "desc": "Execute a created plan"},
            "vm_rollback_plan": {"risk": "medium", "desc": "Rollback a failed plan"},
            "vm_clean_slate": {"risk": "high", "desc": "Revert VM to baseline snapshot"},
            "cluster_create": {"risk": "medium", "desc": "Create cluster with HA/DRS"},
            "cluster_delete": {"risk": "high", "desc": "Delete empty cluster"},
            "acknowledge_vcenter_alarm": {"risk": "medium", "desc": "Acknowledge alarm"},
            "reset_vcenter_alarm": {"risk": "medium", "desc": "Clear alarm"},
        },
    },
    "monitor": {
        "description": "Read-only monitoring: inventory, alarms, events, VM info",
        "tools": {
            "list_virtual_machines": {"risk": "low", "desc": "List all VMs"},
            "list_esxi_hosts": {"risk": "low", "desc": "List ESXi hosts"},
            "get_alarms": {"risk": "low", "desc": "Get active alarms with remediation hints"},
            "get_events": {"risk": "low", "desc": "Recent events by severity"},
            "vm_info": {"risk": "low", "desc": "Detailed VM info (CPU/mem/disk/NIC)"},
        },
    },
    "nsx": {
        "description": "NSX networking: segments, gateways, NAT, routing, IP pools",
        "tools": {
            "list_segments": {"risk": "low", "desc": "List network segments"},
            "create_segment": {"risk": "medium", "desc": "Create network segment"},
            "delete_segment": {"risk": "high", "desc": "Delete network segment"},
            "create_tier1_gateway": {"risk": "medium", "desc": "Create Tier-1 gateway"},
            "delete_tier1_gateway": {"risk": "high", "desc": "Delete Tier-1 gateway"},
            "create_nat_rule": {"risk": "medium", "desc": "Create NAT rule on Tier-1"},
            "delete_nat_rule": {"risk": "high", "desc": "Delete NAT rule"},
            "create_static_route": {"risk": "medium", "desc": "Create static route"},
        },
    },
    "nsx-security": {
        "description": "NSX security: DFW policies/rules, security groups, traceflow, IDPS",
        "tools": {
            "list_dfw_policies": {"risk": "low", "desc": "List firewall policies"},
            "create_dfw_policy": {"risk": "medium", "desc": "Create firewall policy"},
            "delete_dfw_policy": {"risk": "high", "desc": "Delete firewall policy"},
            "create_dfw_rule": {"risk": "medium", "desc": "Create firewall rule"},
            "delete_dfw_rule": {"risk": "high", "desc": "Delete firewall rule"},
            "create_group": {"risk": "medium", "desc": "Create security group"},
            "run_traceflow": {"risk": "medium", "desc": "Network path trace"},
        },
    },
    "aria": {
        "description": "Aria Operations: metrics, alerts, capacity planning, anomaly detection",
        "tools": {
            "list_alerts": {"risk": "low", "desc": "List alerts with filters"},
            "acknowledge_alert": {"risk": "medium", "desc": "Acknowledge alert"},
            "get_capacity_overview": {"risk": "low", "desc": "Capacity recommendations"},
            "get_remaining_capacity": {"risk": "low", "desc": "CPU/mem/disk remaining"},
            "get_time_remaining": {"risk": "low", "desc": "Days until capacity exhaustion"},
            "list_anomalies": {"risk": "low", "desc": "ML-detected metric anomalies"},
            "list_rightsizing_recommendations": {
                "risk": "low",
                "desc": "Over/under-provisioned VMs",
            },
        },
    },
    "vks": {
        "description": "Tanzu Kubernetes: Supervisor, Namespaces, TKC clusters",
        "tools": {
            "create_namespace": {"risk": "medium", "desc": "Create vSphere Namespace"},
            "delete_namespace": {"risk": "high", "desc": "Delete vSphere Namespace"},
            "create_tkc_cluster": {"risk": "medium", "desc": "Create TKC cluster"},
            "scale_tkc_cluster": {"risk": "medium", "desc": "Scale worker nodes"},
            "upgrade_tkc_cluster": {"risk": "medium", "desc": "Upgrade K8s version"},
            "delete_tkc_cluster": {"risk": "high", "desc": "Delete TKC cluster"},
            # Both kubeconfig tools are listed so the gate stops reading them as
            # inspections: each opens with "get_" and returns a live Supervisor
            # bearer token. "high" here is pilot's gating decision for
            # *credential access*, not a copy of vmware-vks's own label — VKS
            # publishes both with readOnlyHint=False, destructiveHint=False,
            # risk_level "medium" (they destroy nothing in the cluster). Pilot
            # gates every step that hands a credential to the calling agent,
            # and the only tier that gates is the destructive one. Without an
            # entry, the read-only name heuristic placed them in the read tier:
            # confidently wrong rather than merely uninformed.
            "get_tkc_kubeconfig": {
                "risk": "high",
                "desc": "Fetch a TKC kubeconfig — returns a live session credential",
            },
            "get_supervisor_kubeconfig": {
                "risk": "high",
                "desc": "Fetch the Supervisor kubeconfig — returns a live bearer token",
            },
        },
    },
    "storage": {
        "description": "Storage management: datastores, iSCSI, vSAN",
        "tools": {
            "storage_iscsi_enable": {"risk": "medium", "desc": "Enable iSCSI adapter"},
            "storage_iscsi_add_target": {"risk": "medium", "desc": "Add iSCSI target"},
            "storage_rescan": {"risk": "medium", "desc": "Rescan all HBAs"},
            "vsan_health": {"risk": "low", "desc": "vSAN cluster health"},
            "vsan_capacity": {"risk": "low", "desc": "vSAN capacity overview"},
        },
    },
    "avi": {
        "description": "AVI (NSX ALB) load balancing: virtual services, pool members, AKO on Kubernetes",
        "tools": {
            "vs_list": {"risk": "low", "desc": "List Virtual Services with VIP and health"},
            "vs_status": {"risk": "low", "desc": "Detailed status for one Virtual Service"},
            "vs_analytics": {"risk": "low", "desc": "VS metrics — use to confirm a drain reached 0 connections"},
            "vs_toggle": {"risk": "high", "desc": "Enable or disable a Virtual Service"},
            "pool_list": {"risk": "low", "desc": "List pools on the Controller"},
            "pool_members": {"risk": "low", "desc": "List pool members with health and enabled state"},
            "pool_member_disable": {"risk": "high", "desc": "Drain a pool member gracefully before maintenance"},
            "pool_member_enable": {"risk": "medium", "desc": "Return a pool member to service after maintenance"},
            "ssl_expiry_check": {"risk": "low", "desc": "Certificates expiring within N days"},
            "ako_status": {"risk": "low", "desc": "AKO pod status on a Kubernetes context"},
            "ako_config_upgrade": {"risk": "medium", "desc": "Apply AKO Helm upgrade (dry_run=true by default)"},
            "ako_restart": {"risk": "high", "desc": "Restart the AKO pod"},
            "ako_sync_force": {"risk": "high", "desc": "Force AKO to resync all K8s resources with the Controller"},
        },
    },
}
