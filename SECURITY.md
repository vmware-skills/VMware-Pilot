# Security Policy

## Disclaimer

This is a community-maintained open-source project and is **not affiliated with, endorsed by, or sponsored by VMware, Inc. or Broadcom Inc.** "VMware" is a trademark of Broadcom Inc.

**Author**: Wei Zhou, VMware by Broadcom — wei-wz.zhou@broadcom.com

## Reporting Vulnerabilities

If you discover a security vulnerability, please report it privately:

- **Email**: wei-wz.zhou@broadcom.com
- **GitHub**: Open a [private security advisory](https://github.com/vmware-skills/VMware-Pilot/security/advisories/new)

Do **not** open a public GitHub issue for security vulnerabilities.

## Security Design

### Credential Management

- VMware-Pilot is an orchestration layer and **does not connect to vCenter, NSX, or AVI directly**
- All authentication is handled by the companion skills it delegates to (vmware-aiops, vmware-nsx, vmware-storage, etc.)
- No credentials are stored, loaded, or managed by this package
- The `vmware-pilot` CLI only launches the MCP server and prints its version; every
  workflow operation goes through MCP tool calls, and none of them take credentials

### Approval Gates

A workflow pauses at each `require_approval` step until `approve()` is called with a named approver; one gate covers every step after it. There is no approval timeout — a paused workflow waits until it is approved or cancelled.

1. **Review before every run** — `run_workflow` reviews the plan and refuses one whose destructive step (skill-catalog risk high/critical, or a destructive name) or unclassifiable step has no gate before it
2. **Custom workflows are rejected, not warned** — for a custom YAML, `create_workflow`, or AI-designed workflow, that same finding makes `plan_workflow`, `create_workflow` and `confirm_draft` refuse to save it and `run_workflow` refuse it even with `force=True`; only built-in templates keep the audited `force=True` override
3. **Credentials and guest commands count as destructive** — `vks get_tkc_kubeconfig` and `get_supervisor_kubeconfig` return a live Supervisor credential, and the aiops guest tools (`vm_guest_exec`, `vm_guest_exec_output`, `vm_guest_upload`, `vm_guest_provision`) run arbitrary commands or write files inside a VM as the account the caller names; all are gated like a delete
4. **The gate is in pilot's workflow, not in the target skill** — on the MCP server pilot executes nothing itself; the calling agent performs each step through the companion skill, which applies its own policy rules

### Rollback (explicit, best-effort — never automatic)

- A failed step stops the workflow (`failed`); remaining steps are marked `skipped` and nothing is undone automatically
- `rollback()` must be called deliberately. It reverses, last first, only the steps pilot recorded as `success`, dispatching each step's `rollback_tool` — which only happens when an embedder supplied a dispatcher. On the MCP server (no dispatcher) the steps the agent performed are recorded `not_executed`, so `rollback()` reverses nothing but approval gates; the agent must call each performed step's `rollback_tool` itself
- Steps without a `rollback_tool` are reported `skipped`; a failed undo does not stop the rest and sets `blocked_reason: rollback_failed`
- Rollback results are kept on the workflow record and each `rollback` call is audited to `~/.vmware/audit.db`

### State Persistence

- Workflow state is persisted in a local SQLite database, `~/.vmware/workflows.db` (WAL mode); pilot sets `~/.vmware` to 0700 and the database file to 0600 (best-effort)
- State includes: step params, step results, approval status, rollback records, and error context — step results are whatever a companion skill returned, so treat the file as sensitive operational data
- Params whose key names a secret (`password`, `token`, `api_key`, …) are masked to `***` before they are written; values under other key names are stored as given
- Baseline files under `~/.vmware/baselines/` are not written by pilot; if the agent saves one there it is an inventory of VMs, hosts, network segments, datastores and alarms and should be kept owner-only

### SSL/TLS Verification

- VMware-Pilot itself makes no outbound network connections
- All network communication is delegated to companion skills, which enforce their own TLS settings

### Transitive Dependencies

- `vmware-policy` is the only transitive dependency auto-installed; it provides the `@vmware_tool` decorator and audit logging
- All other dependencies are standard Python packages (`mcp`, `pyyaml`, `typer`)
- No post-install scripts or background services are started during installation

### Prompt Injection Protection

- Error text returned to the agent goes through vmware-policy `sanitize()` (C0/C1 control characters stripped, 500-character cap); exceptions pilot did not author are reduced to their class name
- Step results passed from one step to the next (`__from_step_N__` references) are not sanitized by pilot — the companion skill that produced them is responsible for its own output
- Workflow names used as YAML template filenames are rejected if they contain path separators, a leading dot, or null bytes

## Static Analysis

This project is scanned with [Bandit](https://bandit.readthedocs.io/) before every release, targeting 0 Medium+ issues:

```bash
uvx bandit -r vmware_pilot/
```

## Supported Versions

| Version | Supported |
|---------|-----------|
| 1.5.x   | Yes       |
| < 1.5   | No        |
