<!-- mcp-name: io.github.vmware-skills/vmware-pilot -->

# VMware Pilot

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
| `plan_workflow` | Generate execution plan from template, returns workflow_id |
| `create_workflow` | Create custom workflow from step list (refused if a destructive step has no approval gate before it) |
| `run_workflow` | Execute workflow, pauses at approval gates |
| `get_workflow_status` | Query state + diff report + audit log |
| `approve` | Human approval, continue execution |
| `rollback` | Explicit, best-effort undo of steps pilot recorded as succeeded — never automatic. Previews (`blast_radius`) unless `confirm=True` |
| `cancel_workflow` | Cancel a workflow — move it to the terminal CANCELLED state. Previews (`blast_radius`) unless `confirm=True` |

## Built-in Templates (15)

`n` is the number of VMs (or drift items); ranges depend on which optional parameters are set.
`clone_and_test` has 7 steps when `change_spec` is a guest command (an extra gate before it runs
in staging). `investigate_alert`'s approvals are synthesis checkpoints; its steps are all reads.
See `skills/vmware-pilot/references/templates.md` for parameters and steps.

| Template | Steps | Approval | Skills Used |
|----------|:-----:|:--------:|-------------|
| `clone_and_test` | 6-7 | Yes | aiops, monitor |
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

## License

MIT