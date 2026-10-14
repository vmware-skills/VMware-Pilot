# Aria vs Pilot: Boundary Definition

This document defines the responsibility boundaries between the VMware skill family members, ensuring no duplication of capabilities and clear guidance on when to use which skill.

---

## Role Separation

The VMware skill family follows a **Eyes / Brain / Hands** model:

| Skill | Role | Responsibility | Tool Count |
|---|---|---|---|
| **vmware-aria** | Eyes | Monitoring, alerts, capacity planning, anomaly detection | 27 read-heavy MCP tools |
| **vmware-pilot** | Brain | Workflow orchestration, state machine, approval gates | 5 MCP tools |
| **vmware-aiops** | Hands | VM lifecycle, deployment, clusters, guest operations | 34 MCP tools |

### vmware-aria (Eyes)

Aria is the **observability layer**. It answers questions about what is happening right now, what happened in the past, and what is likely to happen based on trends.

- Real-time and historical metrics (CPU, memory, storage, network)
- Alert retrieval and acknowledgement
- Capacity overview and runway forecasting
- Anomaly detection and health scoring
- Resource contention analysis

Aria tools are predominantly **read-only**. The only write operations are alert management actions (acknowledge, suspend) which do not change infrastructure state.

### vmware-pilot (Brain)

Pilot is the **orchestration layer**. It coordinates multi-step operations that require sequencing, state tracking, conditional logic, and human approval gates.

- Workflow template instantiation and execution
- State machine management (pending, approved, running, completed, failed, rolled-back)
- Approval gate enforcement (no step proceeds without explicit approval)
- Rollback coordination on failure
- Cross-skill delegation (pilot calls aria, aiops, vks, nsx -- never VMware APIs directly)

Pilot tools are **coordination-only**. They contain zero VMware API calls. Every infrastructure action is delegated to the appropriate skill.

### vmware-aiops (Hands)

AIOps is the **execution layer**. It performs atomic, single-step operations against vCenter and ESXi infrastructure.

- VM lifecycle: create, clone, delete, power on/off, suspend, reset
- VM reconfiguration: CPU, memory, disk, network
- Snapshot management: create, revert, delete
- Migration: vMotion, Storage vMotion
- Guest operations: file transfer, process execution
- Cluster and host management

AIOps tools are **imperative and atomic**. Each tool does one thing. Multi-step sequences are the responsibility of pilot.

---

## When to Use Which

| Scenario | Use | Why |
|---|---|---|
| "Check if cluster has capacity" | **aria** | Read-only monitoring query |
| "Clone VM, test changes, then apply to prod" | **pilot** | Multi-step workflow with approval gates |
| "Power on a VM" | **aiops** | Single atomic operation |
| "Alert triggered, diagnose and fix" | **pilot** (incident_response template) | Multi-step workflow: diagnose via aria, remediate via aiops, verify via aria |
| "Get resource health score" | **aria** | Monitoring data retrieval |
| "Scale TKC cluster" | **vks** | Single atomic operation against Tanzu |
| "Show me CPU trends for the last 7 days" | **aria** | Historical metric analysis |
| "Create a firewall rule" | **nsx-security** | Single atomic network security operation |
| "Migrate VM to another host" | **aiops** | Single atomic vMotion operation |
| "Rolling upgrade across 10 VMs" | **pilot** (rolling_update template) | Multi-step with sequencing, health checks, and rollback |
| "Check if VM has high memory usage" | **aria** | Real-time metric query |
| "Snapshot VM, patch OS, verify, delete snapshot" | **pilot** (maintenance_window template) | Multi-step with state tracking and rollback on failure |

### Decision Flowchart

```
Is this a single, atomic operation?
  |
  +-- YES --> Use the appropriate skill directly:
  |             - Infrastructure state change --> aiops
  |             - Monitoring / metrics / alerts --> aria
  |             - Network config --> nsx / nsx-security
  |             - Tanzu / Kubernetes --> vks
  |             - Storage --> storage
  |
  +-- NO (multiple steps, conditions, approvals)
        |
        +--> Use pilot
              pilot delegates each step to the appropriate skill
```

---

## Pilot Uses Aria as Data Source

Pilot workflows routinely include steps that call aria tools to gather context before making decisions or verifying outcomes. This is by design.

### Examples of Aria Calls Within Pilot Workflows

1. **Pre-check**: Before a maintenance window, pilot calls `aria.get_capacity_overview` to confirm the cluster can absorb the workload shift.
2. **Diagnosis**: In an incident response workflow, pilot calls `aria.get_alarms` and `aria.get_anomalies` to identify root cause before selecting a remediation path.
3. **Post-verification**: After a migration, pilot calls `aria.get_resource_health` to confirm the VM is healthy in its new location.

### What Pilot Does NOT Do

- Pilot does **not** duplicate aria's monitoring capabilities. It does not store metrics, compute health scores, or manage alert state.
- Pilot does **not** re-implement any monitoring logic. If aria exposes a tool for a monitoring task, pilot calls that tool.
- If Aria gains workflow features in the future (e.g., vRealize Automation / vRealize Orchestrator integration), pilot calls those features via the aria skill rather than re-implementing them.

---

## Key Rules

### Rule 1: Single Operation = Direct Skill Call

If the task is a single, self-contained operation with no dependencies on other steps, call the appropriate skill directly. Do not wrap a single operation in a pilot workflow.

```
# Correct: single operation, call aiops directly
aiops.power_on_vm(vm_name="web-prod-01")

# Wrong: unnecessary pilot wrapper for a single step
pilot.run_workflow(template="power_on", targets=["web-prod-01"])
```

### Rule 2: Multiple Steps with State/Approval = Pilot

If the task involves two or more steps where the outcome of one step affects the next, or where human approval is required between steps, use pilot.

```
# Correct: multi-step with approval
pilot.run_workflow(
    template="maintenance_window",
    targets=["db-prod-01"],
    steps=["snapshot", "patch", "verify", "cleanup"],
    require_approval=["patch"]
)
```

### Rule 3: Pilot Never Calls VMware APIs Directly

Pilot is a pure orchestrator. It has zero VMware SDK imports, zero vCenter connections, and zero direct API calls. Every infrastructure interaction is delegated to a skill (aiops, aria, vks, nsx, nsx-security, storage).

This separation ensures:
- **Testability**: Pilot logic can be tested with mocked skill responses.
- **Single responsibility**: API compatibility changes only affect the skill that owns the API.
- **Auditability**: Every action pilot takes is visible as a skill tool call with parameters.

### Rule 4: Aria Owns All Monitoring State

If you need to know the current state of any VMware resource (health, metrics, alerts, capacity), the answer comes from aria. No other skill maintains monitoring state or computes health scores.

### Rule 5: Skills Are Composable, Not Hierarchical

Pilot can call any skill, but skills do not call each other. The dependency graph is flat:

```
pilot --> aria
pilot --> aiops
pilot --> vks
pilot --> nsx
pilot --> nsx-security
pilot --> storage

aria -/-> aiops      (skills do not call each other)
aiops -/-> aria      (skills do not call each other)
```

The only exception is pilot itself, which exists specifically to compose skills into workflows.
