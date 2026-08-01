# Setup Guide — vmware-pilot

## Prerequisites

- Python 3.10+
- `vmware-policy` package (auto-installed as dependency)
- At least one companion skill installed (aiops, monitor, nsx, etc.) for workflows to delegate to

## Installation

### Via uv (Recommended)

```bash
uv tool install vmware-pilot
```

### Via pip

```bash
uv tool install vmware-pilot

# China mainland mirror
pip install vmware-pilot -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### Verify Installation

```bash
# Confirm the CLI is on PATH, then start the MCP server
vmware-pilot version
vmware-pilot mcp
```

---

## MCP Configuration

### Claude Code

Add to your Claude Code MCP config:

```json
{
  "mcpServers": {
    "vmware-pilot": {
      "command": "vmware-pilot",
      "args": ["mcp"]
    }
  }
}
```

### Cursor / VS Code Copilot

```json
{
  "mcpServers": {
    "vmware-pilot": {
      "command": "vmware-pilot",
      "args": ["mcp"]
    }
  }
}
```

### Goose

```yaml
extensions:
  vmware-pilot:
    name: vmware-pilot
    type: stdio
    cmd: vmware-pilot
    args:
      - mcp
```

### Fallback: launching through `uvx`

The `uvx` form still works and needs no prior install:

```json
{
  "mcpServers": {
    "vmware-pilot": {
      "command": "uvx",
      "args": ["--from", "vmware-pilot", "vmware-pilot-mcp"]
    }
  }
}
```

Prefer the installed entry point above where you can. `uvx` re-resolves the package against
PyPI on every start, so on a corporate network with a TLS-inspecting proxy it fails before the
server ever runs:

```
invalid peer certificate: UnknownIssuer
```

`uv` ships its own certificate bundle and ignores the system trust store, which is what breaks.
Either set `UV_NATIVE_TLS=true` so it honours your corporate CA, or install the tool once
(`uv tool install vmware-pilot`) and launch `vmware-pilot mcp`, which touches the network
zero times.

---

## Configuration

Pilot itself requires **no vCenter/NSX/AVI credentials**. It is a pure orchestrator that delegates all infrastructure calls to companion skills.

### Workflow Database

Workflows are persisted to `~/.vmware/workflows.db` (SQLite, WAL mode). Created automatically on first use.

### Custom Templates Directory

```bash
mkdir -p ~/.vmware/workflows
```

Drop YAML workflow templates here. Hot-reloaded without server restart.

### Policy Rules (Optional)

vmware-policy rules at `~/.vmware/rules.yaml` apply to all pilot operations:

```yaml
# Example: deny destructive operations outside maintenance window
rules:
  - name: maintenance_window
    match:
      risk_level: [high, critical]
    deny_unless:
      time_range: "02:00-06:00"
      timezone: "Asia/Shanghai"
```

### Environment Scoping

Policy rules can scope by environment. Skills that connect to a VMware estate
may declare `environment:` (`production` / `staging` / `lab`) per target in
their own `config.yaml` — an optional label an environment-scoped `deny` rule
in `~/.vmware/rules.yaml` can match on (for example, to freeze writes on
`production`). A target with no label is simply not matched by such a rule.

vmware-pilot has no targets and needs no such declaration: it reports a
constant `local`. That is accurate rather than an exemption — pilot's own
writes land in `~/.vmware/workflows.db`, and its executor never calls VMware
APIs. When a workflow reaches an executable step, the MCP server records it as
`not_executed` and returns `dispatch_required`; the calling agent then performs
that step through the target skill's own MCP tool, where that skill's own
policy rules apply. Pilot's approval gate is a step in its own workflow — it
pauses before the agent dispatches the step, so it is not skipped.

### Audit Database

All tool calls are logged to `~/.vmware/audit.db` via vmware-policy. View with:

```bash
vmware-audit log --last 20
vmware-audit log --status denied
```

---

## Companion Skills Setup

For pilot to execute workflows, the relevant companion skills must be installed and configured:

| Skill | Install | Config Location |
|-------|---------|-----------------|
| vmware-aiops | `uv tool install vmware-aiops` | `~/.vmware-aiops/config.yaml` |
| vmware-monitor | `uv tool install vmware-monitor` | `~/.vmware-monitor/config.yaml` |
| vmware-nsx | `uv tool install vmware-nsx-mgmt` | `~/.vmware-nsx/config.yaml` |
| vmware-nsx-security | `uv tool install vmware-nsx-security` | `~/.vmware-nsx-security/config.yaml` |
| vmware-aria | `uv tool install vmware-aria` | `~/.vmware-aria/config.yaml` |
| vmware-vks | `uv tool install vmware-vks` | `~/.vmware-vks/config.yaml` |
| vmware-storage | `uv tool install vmware-storage` | `~/.vmware-storage/config.yaml` |
| vmware-avi | `uv tool install vmware-avi` | `~/.vmware-avi/config.yaml` |

Each skill has its own `doctor` command to verify connectivity:

```bash
vmware-aiops doctor
vmware-monitor doctor
vmware-avi doctor
# etc.
```

---

## Security

### Source Code

[github.com/vmware-skills/VMware-Pilot](https://github.com/vmware-skills/VMware-Pilot) — MIT license.

### Config File Contents

Pilot has no config file of its own. No credentials stored.

### TLS Verification

Not applicable to pilot directly. TLS settings are managed by each companion skill.

### Prompt Injection Protection

All text from VMware/NSX/AVI APIs passes through `_sanitize()` in each companion skill (truncation to 500 chars + C0/C1 control character removal).

### Least Privilege

Pilot itself needs no privileges. Each companion skill should use a service account with minimum required permissions.

### Audit Trail

Every tool call is logged to `~/.vmware/audit.db` with timestamp, tool name, parameters, result, and risk level.

---

## AI Platform Compatibility

| Platform | Transport | Status |
|----------|-----------|--------|
| Claude Code | stdio | Supported |
| Cursor | stdio | Supported |
| VS Code Copilot | stdio | Supported |
| Goose | stdio | Supported |
| Continue.dev | stdio | Supported |
| Ollama (local) | stdio | Supported (MCP client required) |

---

## Troubleshooting

### vmware-policy not found

```bash
pip install vmware-policy
# or
uv pip install vmware-policy
```

vmware-policy is a declared dependency and should install automatically with pilot.

### SQLite "database is locked"

The workflow database uses WAL mode for concurrent access. If you see lock errors, ensure only one MCP server instance is running, or increase the connection timeout.

### Custom template YAML parse error

Validate your template:

```bash
python3 scripts/validate_workflow.py ~/.vmware/workflows/my_workflow.yaml
```

Common issues: incorrect indentation, missing required fields (action, skill, tool, params), invalid YAML syntax.
