"""Static catalog of skills + tools used for custom workflow design.

Building-block reference surfaced by ``get_skill_catalog`` and ``design_workflow``.
"""

from __future__ import annotations

SKILL_CATALOG = {
    "aiops": {
        "description": "VM lifecycle, deployment, clusters, guest operations, alarm management",
        "tools": {
            "power_on_vm": {"risk": "medium", "desc": "Power on a VM"},
            # risk high, not medium: vmware-aiops itself publishes this tool with
            # destructiveHint=True. Pilot's own label used to be the more
            # optimistic of the two, and since the label is what the approval
            # gate reads, the disagreement resolved in favour of not gating it.
            "power_off_vm": {"risk": "high", "desc": "Power off a VM (graceful/force)"},
            # clone_and_test dispatches this for a cpu/memory change_spec. Missing from
            # the catalog it read as "unclassifiable", and run_workflow refused the
            # built-in template unless force=True. Label = what vmware-aiops
            # publishes: risk medium, destructiveHint False.
            "reconfigure_vm": {"risk": "medium", "desc": "Change a VM's CPU / memory"},
            "deploy_linked_clone": {"risk": "medium", "desc": "Instant clone from snapshot"},
            "deploy_vm_from_template": {"risk": "medium", "desc": "Clone from vSphere template"},
            "deploy_vm_from_ova": {"risk": "medium", "desc": "Deploy from OVA file"},
            "batch_clone_vms": {"risk": "medium", "desc": "Batch clone multiple VMs"},
            # risk high, as for power_off_vm: vmware-aiops publishes all four guest
            # tools with destructiveHint=True (its own risk_level is medium). They
            # run an arbitrary command or write an arbitrary file inside the guest
            # as the account the caller names — `/bin/rm -rf /` is a well-formed
            # argument. Labelled medium, a custom workflow whose only step was
            # that command was saved and dispatched with no approval gate.
            # download_vm_guest_file is not here: aiops keeps it destructiveHint=False.
            "run_vm_guest_command": {"risk": "high", "desc": "Execute command inside VM"},
            "run_vm_guest_command_with_output": {"risk": "high", "desc": "Execute and capture stdout"},
            "upload_vm_guest_file": {"risk": "high", "desc": "Upload file to VM"},
            "provision_vm_guest": {"risk": "high", "desc": "Multi-step VM provisioning"},
            "create_vm_plan": {"risk": "medium", "desc": "Create multi-step execution plan"},
            "apply_vm_plan": {"risk": "medium", "desc": "Execute a created plan"},
            "rollback_vm_plan": {"risk": "medium", "desc": "Rollback a failed plan"},
            "restore_vm_clean_slate": {"risk": "high", "desc": "Revert VM to baseline snapshot"},
            "create_cluster": {"risk": "medium", "desc": "Create cluster with HA/DRS"},
            "delete_cluster": {"risk": "high", "desc": "Delete empty cluster"},
            "acknowledge_vcenter_alarm": {"risk": "medium", "desc": "Acknowledge alarm"},
            # risk high since aiops 1.12.1 publishes it destructiveHint=True:
            # vSphere has no per-alarm clear, so it clears every triggered alarm
            # with the named one's status and entity type, not only the one named.
            "reset_vcenter_alarm": {"risk": "high", "desc": "Clear triggered alarms (all matching status + entity type); previews unless confirm=True"},
        },
    },
    "monitor": {
        "description": "Read-only monitoring: inventory, alarms, events, VM info",
        "tools": {
            "list_virtual_machines": {"risk": "low", "desc": "List all VMs"},
            "list_esxi_hosts": {"risk": "low", "desc": "List ESXi hosts"},
            "get_alarms": {"risk": "low", "desc": "Get active alarms with remediation hints"},
            "get_events": {"risk": "low", "desc": "Recent events by severity"},
            "get_vm_info": {"risk": "low", "desc": "Detailed VM info (CPU/mem/disk/NIC)"},
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
            # inspections: each opened with "get_" before 2.0 and returns a
            # live Supervisor bearer token. "high" here is pilot's gating decision for
            # *credential access*, not a copy of vmware-vks's own label — VKS
            # publishes both with readOnlyHint=False, destructiveHint=False,
            # risk_level "medium" (they destroy nothing in the cluster). Pilot
            # gates every step that hands a credential to the calling agent,
            # and the only tier that gates is the destructive one. Without an
            # entry, the read-only name heuristic placed them in the read tier:
            # confidently wrong rather than merely uninformed.
            "issue_tkc_kubeconfig": {
                "risk": "high",
                "desc": "Fetch a TKC kubeconfig — returns a live session credential",
            },
            "issue_supervisor_kubeconfig": {
                "risk": "high",
                "desc": "Fetch the Supervisor kubeconfig — returns a live bearer token",
            },
        },
    },
    "storage": {
        "description": "Storage management: datastores, iSCSI, vSAN",
        "tools": {
            "enable_iscsi": {"risk": "medium", "desc": "Enable iSCSI adapter"},
            "add_iscsi_target": {"risk": "medium", "desc": "Add iSCSI target"},
            "rescan_storage": {"risk": "medium", "desc": "Rescan all HBAs"},
            "get_vsan_health": {"risk": "low", "desc": "vSAN cluster health"},
            "get_vsan_capacity": {"risk": "low", "desc": "vSAN capacity overview"},
        },
    },
    "avi": {
        "description": "AVI (NSX ALB) load balancing: virtual services, pool members, AKO on Kubernetes",
        "tools": {
            "list_virtual_services": {"risk": "low", "desc": "List Virtual Services with VIP and health"},
            "get_virtual_service_status": {"risk": "low", "desc": "Detailed status for one Virtual Service"},
            "get_virtual_service_analytics": {"risk": "low", "desc": "VS metrics — use to confirm a drain reached 0 connections"},
            "set_virtual_service_enabled": {"risk": "high", "desc": "Enable or disable a Virtual Service"},
            "list_lb_pools": {"risk": "low", "desc": "List pools on the Controller"},
            "list_lb_pool_members": {"risk": "low", "desc": "List pool members with health and enabled state"},
            "disable_lb_pool_member": {"risk": "high", "desc": "Drain a pool member gracefully before maintenance"},
            "enable_lb_pool_member": {"risk": "medium", "desc": "Return a pool member to service after maintenance"},
            "check_ssl_cert_expiry": {"risk": "low", "desc": "Certificates expiring within N days"},
            "get_ako_status": {"risk": "low", "desc": "AKO pod status on a Kubernetes context"},
            "upgrade_ako_config": {"risk": "medium", "desc": "Apply AKO Helm upgrade (previews unless confirm=True)"},
            "restart_ako": {"risk": "high", "desc": "Restart the AKO pod"},
            "force_ako_sync": {"risk": "high", "desc": "Force AKO to resync all K8s resources with the Controller"},
        },
    },
    # The five below were missing until 2026-09-15 while the family grew from
    # eight companions to thirteen. Labels are each skill's own risk_level and
    # destructiveHint, with one rule on top: a tool that declares
    # readOnlyHint=False is never labelled "low", because the review reads "low"
    # as an inspection and would pass a write as one.
    "debug": {
        "description": "Offline incident correlation and evidence-graded investigation cases",
        # Reads, plus one write below. The case writes (open_case,
        # submit_case_evidence, …) touch the local case ledger, not VMware, and
        # a design catalog has no use for them.
        "tools": {
            "build_incident_timeline": {"risk": "low", "desc": "Correlate fetched events into spikes and ranked hypotheses"},
            "list_symptom_categories": {"risk": "low", "desc": "Symptom categories and what to check for each"},
            "get_case_readiness": {"risk": "low", "desc": "Strongest conclusion reachable per category with these skills"},
            "plan_case": {"risk": "low", "desc": "Which skill and tool to fetch next for a case"},
            # The one write listed. Its 2.0 name starts with "get", a read-only
            # name hint, so without an entry the review would call it an
            # inspection. It writes timeline.md into the case folder
            # (readOnlyHint=False); debug publishes it at low, and a write is
            # never labelled low here.
            "record_case_timeline": {"risk": "medium", "desc": "Correlate a case's evidence; writes timeline.md"},
        },
    },
    "harden": {
        "description": "Compliance baselines, violations, drift and remediation suggestions",
        "tools": {
            "list_baselines": {"risk": "low", "desc": "Available compliance baselines"},
            # Read-only, but harden publishes it at medium: a scan is a long
            # sweep of every host in scope.
            "scan_target": {"risk": "medium", "desc": "Scan a vCenter target against a baseline"},
            "list_violations": {"risk": "low", "desc": "Violations from the latest scan, per node"},
            "list_drift_events": {"risk": "low", "desc": "What changed against the baseline, and when"},
            "get_remediation": {"risk": "low", "desc": "Stored remediation suggestion for one violation"},
        },
    },
    "log-insight": {
        "description": "Log Insight search, aggregation and alerts",
        "tools": {
            "search_logs": {"risk": "low", "desc": "Search log events in a time window"},
            "aggregate_logs": {"risk": "low", "desc": "Counts over time, with spike detection"},
            "list_log_alerts": {"risk": "low", "desc": "Defined alerts"},
            "get_log_alert_history": {"risk": "low", "desc": "Recent triggers of one alert"},
        },
    },
    "privateai": {
        "description": "GPU hosts, vGPU profiles and utilisation for Private AI Foundation",
        "tools": {
            "list_gpu_hosts": {"risk": "low", "desc": "Hosts with GPUs"},
            "get_gpu_utilization": {"risk": "low", "desc": "GPU utilisation per vGPU VM"},
            "check_gpu_host_readiness": {"risk": "low", "desc": "Whether each GPU host can serve vGPU"},
            "validate_vgpu_profile": {"risk": "low", "desc": "Pre-flight a vGPU profile change"},
            "assign_vgpu": {"risk": "high", "desc": "Set a VM's vGPU profile (VM must be off)"},
        },
    },
    "vdi": {
        "description": "Horizon VDI: pools, sessions, machines, images",
        "tools": {
            "get_vdi_health_summary": {"risk": "low", "desc": "Sessions, problem machines and pool availability"},
            "list_vdi_sessions": {"risk": "low", "desc": "Sessions filtered by user, pool or state"},
            "get_desktop_pool_utilization": {"risk": "low", "desc": "Capacity and use per desktop pool"},
            "get_vdi_task_status": {"risk": "low", "desc": "Status of a pool's long-running task"},
            # vdi publishes this at low, but it is a write (readOnlyHint=False):
            # labelled low, the review would call it an inspection.
            "send_vdi_session_message": {"risk": "medium", "desc": "Message users before maintenance"},
            "set_vdi_machine_maintenance": {"risk": "medium", "desc": "Enter or exit maintenance mode for machines"},
            "logoff_vdi_session": {"risk": "high", "desc": "Force-logoff sessions"},
            "reset_vdi_machine": {"risk": "high", "desc": "Hard-reset desktop machines"},
            "push_desktop_pool_image": {"risk": "high", "desc": "Push a new image — recreates every desktop in the pool"},
        },
    },
}
