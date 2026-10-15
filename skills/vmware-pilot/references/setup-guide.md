# Setup Guide — vmware-pilot

## Prerequisites

- Python 3.10+
- `vmware-policy` package (auto-installed as dependency)
- At least one companion skill installed (aiops, monitor, nsx, etc.) for workflows to delegate to

## Installation

### Via uv (Recommended)

```bash
uv tool install vmware-pilot==2.2.0
```

### Via pip

```bash
uv tool install vmware-pilot==2.2.0

# China mainland mirror
pip install vmware-pilot==2.2.0 -i https://pypi.tuna.tsinghua.edu.cn/simple
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
      "args": ["--from", "vmware-pilot==2.2.0", "vmware-pilot-mcp"]
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
(`uv tool install vmware-pilot==2.2.0`) and launch `vmware-pilot mcp`, which touches the network
zero times.

---

## Limiting which tools are offered

By default the MCP server offers every tool. To offer only read tools, start it with `--expose read`, or set
`VMWARE_PILOT_MCP_EXPOSE=read` in the MCP client's `env` block (useful when the client launches `vmware-pilot mcp` itself). To offer
chosen write tools as well, add `--allow-write` with a comma-separated list (or `VMWARE_PILOT_MCP_ALLOW_WRITE`):

```bash
vmware-pilot mcp --expose read --allow-write cancel_workflow
```

- A read tool is one annotated `readOnlyHint: true`; every other tool counts as a write.
- An unknown setting, or a name that is not a write tool of this server, stops the server before it serves (exit
  code 2), so it never offers more than you asked for. Pre-2.0 tool names are accepted.
- With `--expose read`, the server prints one line to stderr saying what it exposes.
- With `--expose read`, the server's instructions still describe the workflow tools it hides (`create_workflow`,
  `create_workflow_from_template`, `run_workflow`, `approve`, `rollback`); a call to a hidden tool is answered `Unknown tool`.
- Flags and environment variables are not mixed: if either flag is given, both environment variables are
  ignored, so a leftover `_ALLOW_WRITE` in the shell cannot widen `--expose read`. A blank flag value counts as
  not given.
- A tool that runs other steps (a plan, workflow or batch tool) carries every write it can execute, including
  writes this setting hides. Expose such a tool only if you would expose those writes too.
- A launcher that imports the server object can call
  `vmware_policy.exposure.restrict_tools(mcp, skill="pilot", expose="read", allow_write=[...])` before running it,
  instead of editing the SDK's tool registry.
- This limits what clients are offered. It is **not** authorization. This server's write tools change workflow state on this machine; the steps it runs act through the other skills' accounts;
  deny rules in `~/.vmware/rules.yaml` still apply to every exposed tool.

## Serving MCP over HTTPS

By default the MCP server speaks stdio to one local client. To let clients on other machines reach it, serve it over
HTTPS. Every caller then needs a bearer token.

```bash
vmware-audit token create alice                      # prints the token once; give it to alice
vmware-pilot mcp --transport https --host 0.0.0.0 --port 8443 \
    --cert /etc/vmware-mcp/server.pem --key /etc/vmware-mcp/server-key.pem \
    --public-name mcp.example.com
```

- Clients connect to `https://mcp.example.com:8443/mcp` and send `Authorization: Bearer <token>`.
- `--public-name` lists every host name clients use (comma-separated); requests for any other Host or Origin are
  refused. Use a certificate your clients trust for those names.
- Behind a reverse proxy that terminates TLS (Nginx, HAProxy) on the same machine, use
  `--transport http --host 127.0.0.1 --public-name mcp.example.com` (the names the proxy forwards in `Host`), and
  have the proxy pass the `Authorization` header through.
- Behind a load balancer or gateway on another machine, keep `--transport https` (a self-signed certificate is
  fine: the balancer is the client that has to trust it), list in `--public-name` the host name the balancer sends
  as `Host`, and have it forward `Authorization`. Point its health probe at `GET /healthz`: it needs no token, returns
  only `{"status": "ok"}` and accepts any `Host` (vmware-policy ≥ 1.23.0). Every other path still needs the token.
- Correlation: each audit row stores the caller's `traceparent` trace-id, else its `X-Request-ID` /
  `X-Correlation-ID`. Have the agent platform send one, and `vmware-audit log --correlation-id ID` lists the calls
  behind one platform request.
- `vmware-audit token list` / `token revoke NAME` manage callers; a revoked token is refused from its next request.
  Replace a leaked token under a new name.
- The server will not start without at least one token, and checks before it binds that unauthenticated requests
  are refused.
- Each audit row records the caller as `token:<name>`. Calls naming different targets run in parallel; calls that
  name no target run one at a time, so name the target when several people use one server.
- **Everyone who can reach the endpoint acts with this server's account.** Tokens identify callers; they grant
  nothing on the platform. Run one endpoint per privilege level (for example a read-only account behind
  `--expose read`, and a separate endpoint for operators).

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

vmware-pilot has no targets and needs no such declaration: it registers no
environment resolver, so its own calls are unlabeled and match no
environment-scoped rule. That is accurate rather than an exemption — pilot's own
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
"$(uv tool dir)/vmware-pilot/bin/python" scripts/validate_workflow.py ~/.vmware/workflows/my_workflow.yaml
```

Common issues: incorrect indentation, missing required fields (action, skill, tool, params), invalid YAML syntax, and a destructive step with no `require_approval` step before it (pilot refuses the file until one is added).
