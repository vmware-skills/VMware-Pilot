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

Workflows that include destructive steps (delete, migrate, reconfigure) pause for **human review** before proceeding:

1. **Pre-execution review** — the workflow presents a summary of pending destructive actions and waits for explicit approval
2. **Step-level confirmation** — each destructive step requires individual approval; read-only steps execute automatically
3. **Timeout** — unapproved steps time out after a configurable period (default 300 seconds), and the workflow enters a paused state

### Rollback on Failure

- If a workflow step fails after earlier steps have completed, the orchestrator automatically attempts to reverse completed steps in LIFO order
- Rollback actions are logged with the same audit trail as forward operations
- Steps that cannot be rolled back (e.g., notifications already sent) are marked as `rollback_skipped` in the workflow state

### State Persistence

- Workflow state is persisted in a local SQLite database (WAL mode)
- State includes: step history, approval status, rollback records, and error context
- No sensitive data (credentials, API responses) is stored in the workflow state

### SSL/TLS Verification

- VMware-Pilot itself makes no outbound network connections
- All network communication is delegated to companion skills, which enforce their own TLS settings

### Transitive Dependencies

- `vmware-policy` is the only transitive dependency auto-installed; it provides the `@vmware_tool` decorator and audit logging
- All other dependencies are standard Python packages (Click, Rich, python-dotenv)
- No post-install scripts or background services are started during installation

### Prompt Injection Protection

- All text passed between workflow steps is processed through `_sanitize()`
- Sanitization truncates to 500 characters and strips C0/C1 control characters
- Workflow step names and descriptions are validated against allowed character patterns

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
