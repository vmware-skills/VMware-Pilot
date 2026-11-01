<!-- mcp-name: io.github.vmware-skills/vmware-pilot -->

# VMware Pilot

For a read-only check of this skill with your model and agent host, use the shared [model-fit self-test](https://github.com/vmware-skills/marketplace/blob/main/MODEL_FIT_SELF_TEST.md) in the marketplace root.

> **Author**: Wei Zhou, VMware by Broadcom — wei-wz.zhou@broadcom.com
> This is a community-driven project by a VMware engineer, not an official VMware product.
> For official VMware developer tools see [developer.broadcom.com](https://developer.broadcom.com).

English | [中文](README-CN.md)

Multi-step workflow orchestration for VMware MCP skills — state machine, approval gates, audit trail.

> **Companion skills** handle everything else:
>
> | Skill | Scope | Install |
> |-------|-------|---------|
> | **[vmware-aiops](https://github.com/vmware-skills/VMware-AIops)** | VM lifecycle, deployment, guest ops, cluster | `uv tool install vmware-aiops` |
> | **[vmware-monitor](https://github.com/vmware-skills/VMware-Monitor)** | Read-only: inventory, health, alarms, events | `uv tool install vmware-monitor` |
> | **[vmware-storage](https://github.com/vmware-skills/VMware-Storage)** | Datastores, iSCSI, vSAN management | `uv tool install vmware-storage` |
> | **[vmware-vks](https://github.com/vmware-skills/VMware-VKS)** | Tanzu Namespaces, TKC cluster lifecycle | `uv tool install vmware-vks` |
> | **[vmware-nsx](https://github.com/vmware-skills/VMware-NSX)** | NSX networking: segments, gateways, NAT | `uv tool install vmware-nsx-mgmt` |
> | **[vmware-nsx-security](https://github.com/vmware-skills/VMware-NSX-Security)** | DFW firewall rules, security groups | `uv tool install vmware-nsx-security` |
> | **[vmware-aria](https://github.com/vmware-skills/VMware-Aria)** | Aria Ops: metrics, alerts, capacity | `uv tool install vmware-aria` |
> | **[vmware-avi](https://github.com/vmware-skills/VMware-AVI)** | AVI load balancing, pool management, AKO K8s ops | `uv tool install vmware-avi` |

## Install

```bash
uv tool install vmware-pilot
vmware-pilot mcp          # start the MCP server (stdio)
```

### Offline / Air-Gapped Install (from source)

This project uses the modern PEP 517 build system (hatchling), so there is **no
`setup.py`** by design — that is expected, not a missing file. If you cloned the
source and hit `ERROR: File "setup.py" or "setup.cfg" not found ... editable mode
currently requires a setuptools-based build`, your `pip` is older than 21.3 and
cannot do an *editable* (`-e`) install with a non-setuptools backend. Editable
mode is a developer convenience, not needed to run the tool — do one of:

```bash
# From the source tree — a normal (non-editable) install builds a wheel:
pip install .              # NOT  pip install -e .

# ...or upgrade pip first, and editable works too:
pip install --upgrade pip && pip install -e .
```

For a **truly air-gapped host**, build the wheels on a connected machine and copy
them over — the target then needs no network:

```bash
# On a connected machine, collect this package + its dependencies as wheels:
pip wheel . -w dist        # → dist/*.whl   (or: uv build, for just this package)

# Copy dist/ to the air-gapped host, then install offline:
pip install --no-index --find-links dist vmware-pilot
```

## MCP Tools (13 — 4 read, 9 write)

| Tool | Description |
|------|-------------|
| `get_skill_catalog` | List all available skills and tools for workflow design |
| `list_workflows` | List built-in and custom templates |
| `review_workflow` | Sanity-check a planned workflow before execution |
| `design_workflow` | Natural language goal → draft workflow |
| `update_draft` | Edit draft workflow steps |
| `confirm_draft` | Finalize draft → ready to execute |
| `create_workflow_from_template` | Create and save a workflow from a template (not a dry run), returns workflow_id |
| `create_workflow` | Create custom workflow from step list (refused if a destructive step has no approval gate before it) |
| `run_workflow` | Execute workflow, pauses at approval gates |
| `get_workflow_status` | Query state + diff report + audit log |
| `approve` | Human approval, continue execution. Refused while steps before the gate are `not_executed`, until `acknowledge_not_executed=True` says you ran them |
| `rollback` | Explicit, best-effort undo of steps pilot recorded as succeeded — never automatic. Previews (`blast_radius`) unless `confirm=True` |
| `cancel_workflow` | Cancel a workflow — move it to the terminal CANCELLED state. Previews (`blast_radius`) unless `confirm=True` |

## Built-in Templates (15)

`n` is the number of VMs (or drift items); ranges depend on which optional parameters are set.
`clone_and_test` needs `snapshot_name` (the snapshot to clone from), resizes staging while it is
powered off, and has 8 steps when `change_spec` is a guest command (an extra gate before it runs
in staging). `investigate_alert`'s approvals are synthesis checkpoints; its steps are all reads.
See `skills/vmware-pilot/references/templates.md` for parameters and steps.

| Template | Steps | Approval | Skills Used |
|----------|:-----:|:--------:|-------------|
| `clone_and_test` | 7-8 | Yes | aiops, monitor |
| `incident_response` | 4 | Yes | monitor, aiops |
| `investigate_alert` | 4 (8 with `deep_dive`) | Yes | monitor, aria |
| `plan_and_approve` | 3 | Yes | aiops |
| `compliance_scan` | 1-3 | No | monitor, aria |
| `network_segment_setup` | 3-6 | Yes | nsx, nsx-security |
| `vks_cluster_deploy` | 5 | Yes | vks |
| `rolling_restart` | 2+3n | Yes | aiops, monitor |
| `capacity_expansion` | 5 | Yes | aria, aiops, monitor |
| `disaster_recovery` | 5 | Yes | aiops, monitor, nsx |
| `patch_deployment` | 1+3n | Yes | aiops, monitor |
| `storage_expansion` | 6 | Yes | storage |
| `baseline_capture` | 1-5 | No | monitor, nsx, storage |
| `baseline_audit` | 1-5 | No | monitor, nsx, storage, aria |
| `baseline_remediate` | 3+n | Yes | varies |

## Upgrading to 2.0

2.0 renames one MCP tool: `plan_workflow` → `create_workflow_from_template`. It always created and saved a
workflow from a template (it is a write, not a dry run); the new name says so and sets it apart from
`create_workflow`, which saves your own step list. Parameters and results are unchanged, and so are the CLI
commands. The skill catalog also follows vmware-vks's renames (`get_supervisor_kubeconfig` →
`issue_supervisor_kubeconfig`, `get_tkc_kubeconfig` → `issue_tkc_kubeconfig`). Workflows saved with the old
names are translated when loaded. If an agent prompt, script or `~/.vmware/rules.yaml` names `plan_workflow`,
update it. Requires vmware-policy ≥ 1.19.0. Details: [RELEASE_NOTES.md](RELEASE_NOTES.md#v200--create_workflow_from_template-and-vks-kubeconfig-tools-at-their-new-names).

## MCP Configuration

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

> Fallback: `{"command": "uvx", "args": ["--from", "vmware-pilot", "vmware-pilot-mcp"]}` still
> works, but `uvx` re-resolves against PyPI on every start and fails behind a TLS-inspecting
> corporate proxy (`invalid peer certificate: UnknownIssuer`). The installed entry point above
> touches the network zero times; set `UV_NATIVE_TLS=true` if you must use `uvx`.

### Pilot runs the steps itself: `--dispatch`

By default pilot executes nothing: `run_workflow` returns each step in `pending_dispatch` and your agent performs it.
Add `--dispatch` (or set `VMWARE_PILOT_DISPATCH=stdio`) and pilot runs each step itself, in that skill's own MCP
server (`vmware-<skill>-mcp`, which must be installed), so the skill's policy rules, confirmation gate and audit
apply as usual. Nothing after an approval gate runs before `approve`, and workflows reach `completed`.

```json
{"mcpServers": {"vmware-pilot": {"command": "vmware-pilot", "args": ["mcp", "--dispatch"]}}}
```

stdio only: pilot refuses to start with `--dispatch` and `--transport https`/`http` (the skill servers it starts would
have no caller identity, so roles could not apply). A step that takes longer than `VMWARE_PILOT_DISPATCH_TIMEOUT`
seconds (default 1800) is recorded `interrupted` — its effect is unknown — and rollback waits for you to check it.
`run_workflow` and `approve` take as long as the steps they run: set your MCP client's tool timeout above your
longest step. If a call does time out, Pilot carries on and answers nothing else until the step ends; then
`get_workflow_status` shows the outcome. Never perform those steps yourself.

<!-- usage: remote-and-exposure -->
## Remote access (HTTPS) and limiting tools

By default `vmware-pilot mcp` speaks stdio to one local client and offers every tool. Two options change that
(vmware-policy ≥ 1.21.0).

**Serve over HTTPS** so clients on other machines can connect. Every caller needs a bearer token:

```bash
vmware-audit token create alice          # prints the token once; give it to alice
vmware-audit token list                  # names only
vmware-audit token revoke alice          # refused from its next request, no restart
vmware-pilot mcp --transport https --host 0.0.0.0 --port 8443 \
    --cert /etc/vmware-mcp/server.pem --key /etc/vmware-mcp/server-key.pem \
    --public-name mcp.example.com
```

Connect a client (Claude Code shown; any MCP client that supports streamable HTTP and custom headers works):

```bash
claude mcp add --transport http vmware-pilot https://mcp.example.com:8443/mcp \
    --header "Authorization: Bearer $VMWARE_MCP_TOKEN"
```

```json
{
  "mcpServers": {
    "vmware-pilot": {
      "type": "http",
      "url": "https://mcp.example.com:8443/mcp",
      "headers": { "Authorization": "Bearer ${VMWARE_MCP_TOKEN}" }
    }
  }
}
```

- The server will not start without a token, refuses Host/Origin values outside `--public-name`, and checks
  before it binds that unauthenticated requests get 401. Use a certificate your clients trust for those names.
- Behind a TLS-terminating reverse proxy on the same machine use `--transport http --host 127.0.0.1
  --public-name mcp.example.com` and pass the `Authorization` header through.
- Behind a load balancer or gateway on another machine, keep `--transport https` (a self-signed certificate is
  fine), list in `--public-name` the host name the balancer sends as `Host`, and have it forward `Authorization`.
  Its health probe can use `GET /healthz`, which needs no token and returns only `{"status": "ok"}`
  (vmware-policy ≥ 1.23.0).
- Send `traceparent` or `X-Request-ID` with each request to have it stored on the audit row;
  `vmware-audit log --correlation-id ID` then finds the calls behind one platform request.
- The audit log records each caller as `token:<name>`. Calls naming different targets run in parallel; calls that
  name no target run one at a time, so name the target when several people share a server.
- **Everyone who can reach the endpoint acts with this server's account.** Tokens identify callers; they grant
  nothing on the platform. Run one endpoint per privilege level.
- **Several people or roles on one endpoint:** run `vmware-audit roles enable` once, then give each token a role (`vmware-audit token create NAME --role reader`, or a role in `roles.yaml` naming tools and targets; vmware-policy ≥ 1.24.0). A role limits what the token may call on this endpoint; it cannot grant more than the account has. See vmware-policy's README, "Roles for network callers".

**Offer only read tools, plus the writes you choose:**

```bash
vmware-pilot mcp --expose read --allow-write cancel_workflow
# or: VMWARE_PILOT_MCP_EXPOSE=read VMWARE_PILOT_MCP_ALLOW_WRITE=cancel_workflow vmware-pilot mcp
```

Tools not offered do not appear in the client's tool list. A bad value or an unknown write tool stops the server
(exit 2). This limits what clients are offered; it is not authorization, so the account itself should be
least-privilege.

Details: `skills/vmware-pilot/references/setup-guide.md` ("Serving MCP over HTTPS", "Limiting which tools are offered").

## License

MIT