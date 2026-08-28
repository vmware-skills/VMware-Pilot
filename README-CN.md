<!-- mcp-name: io.github.vmware-skills/vmware-pilot -->
# VMware Pilot

> **作者**: Wei Zhou, VMware by Broadcom — wei-wz.zhou@broadcom.com
> 本项目由 VMware 工程师维护的社区项目，非 VMware 官方产品。
> VMware 官方开发者工具请访问 [developer.broadcom.com](https://developer.broadcom.com)。

[English](README.md) | 中文

多步骤工作流编排 — 跨 VMware MCP 技能的状态机、审批门控、审计日志。

> **配套技能**负责其他领域：
>
> | 技能 | 范围 | 安装 |
> |------|------|------|
> | **[vmware-aiops](https://github.com/vmware-skills/VMware-AIops)** | VM 生命周期、部署、Guest Ops、集群 | `uv tool install vmware-aiops` |
> | **[vmware-monitor](https://github.com/vmware-skills/VMware-Monitor)** | 只读：资源清单、健康检查、告警、事件 | `uv tool install vmware-monitor` |
> | **[vmware-storage](https://github.com/vmware-skills/VMware-Storage)** | 数据存储、iSCSI、vSAN 管理 | `uv tool install vmware-storage` |
> | **[vmware-vks](https://github.com/vmware-skills/VMware-VKS)** | Tanzu 命名空间、TKC 集群生命周期 | `uv tool install vmware-vks` |
> | **[vmware-nsx](https://github.com/vmware-skills/VMware-NSX)** | NSX 网络：Segment、网关、NAT | `uv tool install vmware-nsx-mgmt` |
> | **[vmware-nsx-security](https://github.com/vmware-skills/VMware-NSX-Security)** | DFW 防火墙规则、安全组 | `uv tool install vmware-nsx-security` |
> | **[vmware-aria](https://github.com/vmware-skills/VMware-Aria)** | Aria Ops：指标、告警、容量 | `uv tool install vmware-aria` |
> | **[vmware-avi](https://github.com/vmware-skills/VMware-AVI)** | AVI 负载均衡、Pool 管理、AKO K8s 运维 | `uv tool install vmware-avi` |

## 安装

```bash
uv tool install vmware-pilot
vmware-pilot mcp          # 启动 MCP server（stdio）
```

## MCP 工具（13 个 — 4 读 / 9 写）

| 工具 | 说明 |
|------|------|
| `get_skill_catalog` | 获取所有可用技能和工具（用于工作流设计） |
| `list_workflows` | 列出内置模板和自定义模板 |
| `review_workflow` | 执行前对已规划的工作流做合理性检查 |
| `design_workflow` | 自然语言目标 → 草稿工作流 |
| `update_draft` | 编辑草稿工作流的步骤 |
| `confirm_draft` | 确认草稿 → 可执行状态 |
| `plan_workflow` | 从模板创建执行计划，返回 workflow_id |
| `create_workflow` | 从步骤列表直接创建自定义工作流 |
| `run_workflow` | 执行工作流，在审批门控处暂停 |
| `get_workflow_status` | 查询状态 + 差异报告 + 审计日志 |
| `approve` | 人工审批，继续执行 |
| `rollback` | 中止并按逆序回滚已完成的步骤 |
| `cancel_workflow` | 取消工作流，置为终态 CANCELLED |

## 内置模板（14 个）

| 模板 | 步骤数 | 审批 | 使用的技能 |
|------|:------:|:----:|-----------|
| `clone_and_test` | 6 | 是 | aiops, monitor |
| `incident_response` | 4 | 是 | monitor, aiops |
| `plan_and_approve` | 3 | 是 | aiops |
| `compliance_scan` | 3 | 否 | monitor, aria |
| `network_segment_setup` | 2-6 | 是 | nsx, nsx-security |
| `vks_cluster_deploy` | 4 | 是 | vks |
| `rolling_restart` | 2+3n | 是 | aiops, monitor |
| `capacity_expansion` | 5 | 是 | aria, aiops, monitor |
| `disaster_recovery` | 5 | 是 | aiops, monitor, nsx |
| `patch_deployment` | 1+3n | 是 | aiops, monitor |
| `storage_expansion` | 6 | 是 | storage |
| `baseline_capture` | 1-5 | 否 | monitor, nsx, storage |
| `baseline_audit` | 2-5 | 否 | monitor, nsx, storage, aria |
| `baseline_remediate` | 3+n | 是 | 按需 |

## MCP 配置

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

> 备用方式：`{"command": "uvx", "args": ["--from", "vmware-pilot", "vmware-pilot-mcp"]}` 同样可用，
> 但 `uvx` 每次启动都会重新到 PyPI 解析包，在有 TLS 中间人代理的企业网络下会失败
> （`invalid peer certificate: UnknownIssuer`）。上面的入口点走 PATH，完全不联网；
> 若必须用 `uvx`，请设置 `UV_NATIVE_TLS=true`。

## 审计与安全

所有操作通过 vmware-policy（`@vmware_tool` 装饰器）自动审计：
- 每个工具调用记录到 `~/.vmware/audit.db`（SQLite, WAL 模式）
- 策略规则通过 `~/.vmware/rules.yaml` 执行（拒绝规则、维护窗口、风险等级）
- 查看最近操作：`vmware-audit log --last 20`
- 查看被拒绝的操作：`vmware-audit log --status denied`

## 许可证

MIT
