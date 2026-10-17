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
| `create_workflow_from_template` | 从模板创建并保存工作流（不是预演），返回 workflow_id |
| `create_workflow` | 从步骤列表直接创建自定义工作流（若破坏性步骤之前没有审批门控则拒绝） |
| `run_workflow` | 执行工作流，在审批门控处暂停 |
| `get_workflow_status` | 查询状态 + 差异报告 + 审计日志 |
| `approve` | 人工审批，继续执行。审批点之前仍有 `not_executed` 步骤时拒绝，直到 `acknowledge_not_executed=True` 表明这些步骤已由你执行 |
| `rollback` | 显式、尽力而为地逆序撤销 pilot 记录为成功的步骤——从不自动执行。不带 `confirm=True` 时只预览（`blast_radius`） |
| `cancel_workflow` | 取消工作流，置为终态 CANCELLED。不带 `confirm=True` 时只预览（`blast_radius`） |

## 内置模板（15 个）

`n` 为虚拟机（或漂移项）数量；区间取决于设置了哪些可选参数。`clone_and_test` 需要
`snapshot_name`（克隆所用的快照），在 staging 关机状态下调整规格；`change_spec` 为 guest 命令时
有 8 步（在 staging 中执行命令前多一道审批）。`investigate_alert` 的审批是综合判断检查点，其步骤全部为只读。参数与步骤详见 `skills/vmware-pilot/references/templates.md`。

| 模板 | 步骤数 | 审批 | 使用的技能 |
|------|:------:|:----:|-----------|
| `clone_and_test` | 7-8 | 是 | aiops, monitor |
| `incident_response` | 4 | 是 | monitor, aiops |
| `investigate_alert` | 4（`deep_dive` 时 8） | 是 | monitor, aria |
| `plan_and_approve` | 3 | 是 | aiops |
| `compliance_scan` | 1-3 | 否 | monitor, aria |
| `network_segment_setup` | 3-6 | 是 | nsx, nsx-security |
| `vks_cluster_deploy` | 5 | 是 | vks |
| `rolling_restart` | 2+3n | 是 | aiops, monitor |
| `capacity_expansion` | 5 | 是 | aria, aiops, monitor |
| `disaster_recovery` | 5 | 是 | aiops, monitor, nsx |
| `patch_deployment` | 1+3n | 是 | aiops, monitor |
| `storage_expansion` | 6 | 是 | storage |
| `baseline_capture` | 1-5 | 否 | monitor, nsx, storage |
| `baseline_audit` | 1-5 | 否 | monitor, nsx, storage, aria |
| `baseline_remediate` | 3+n | 是 | 按需 |

## 升级到 2.0

2.0 改了一个 MCP 工具的名字：`plan_workflow` → `create_workflow_from_template`。它一直是从模板创建并保存工作流
（是写操作，不是预演）；新名字把这一点说清楚，也和 `create_workflow`（保存你自己写的步骤列表）区分开。参数和返回值不变，
CLI 命令也不变。技能目录同时跟随 vmware-vks 的改名（`get_supervisor_kubeconfig` → `issue_supervisor_kubeconfig`，
`get_tkc_kubeconfig` → `issue_tkc_kubeconfig`）。用旧名保存的工作流在加载时会自动转换。如果 agent 提示词、脚本或
`~/.vmware/rules.yaml` 里写了 `plan_workflow`，请改成新名。需要 vmware-policy ≥ 1.19.0。详见 [RELEASE_NOTES.md](RELEASE_NOTES.md#v200--create_workflow_from_template-and-vks-kubeconfig-tools-at-their-new-names)。

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

### 由 Pilot 自己执行步骤：`--dispatch`

默认情况下 Pilot 不执行任何步骤：`run_workflow` 把每一步放在 `pending_dispatch` 里返回，由你的 agent 去执行。
加上 `--dispatch`（或设置 `VMWARE_PILOT_DISPATCH=stdio`）后，Pilot 会在对应 skill 自己的 MCP server
（`vmware-<skill>-mcp`，需已安装）里执行每一步，因此该 skill 的策略规则、确认门和审计照常生效。审批点之后的步骤
在 `approve` 之前不会执行，工作流可以走到 `completed`。

```json
{"mcpServers": {"vmware-pilot": {"command": "vmware-pilot", "args": ["mcp", "--dispatch"]}}}
```

仅限 stdio：`--dispatch` 与 `--transport https`/`http` 同时使用时 Pilot 拒绝启动（它启动的 skill server 没有调用者
身份，角色无法生效）。超过 `VMWARE_PILOT_DISPATCH_TIMEOUT` 秒（默认 1800）的步骤记为 `interrupted`——效果未知——
回滚会等你确认后再进行。
`run_workflow` 和 `approve` 的耗时等于它们执行的步骤：请把 MCP 客户端的工具超时设得比最长的步骤更长；万一调用超时，
用 `get_workflow_status` 查看状态——不要自己去执行这些步骤。

## 审计与安全

所有操作通过 vmware-policy（`@vmware_tool` 装饰器）自动审计：
- 每个工具调用记录到 `~/.vmware/audit.db`（SQLite, WAL 模式）
- 策略规则通过 `~/.vmware/rules.yaml` 执行（拒绝规则、维护窗口、风险等级）
- 查看最近操作：`vmware-audit log --last 20`
- 查看被拒绝的操作：`vmware-audit log --status denied`

<!-- usage: remote-and-exposure -->
## 远程访问（HTTPS）与限制工具

默认情况下 `vmware-pilot mcp` 通过 stdio 服务一个本地客户端，并提供全部工具。以下两个选项可以改变这一点
（需 vmware-policy ≥ 1.21.0）。

**通过 HTTPS 提供服务**，让其他机器上的客户端连接。每个调用者都需要一个 bearer token：

```bash
vmware-audit token create alice          # 只显示一次，交给 alice
vmware-audit token list                  # 只列名字
vmware-audit token revoke alice          # 下一个请求起即拒绝，无需重启
vmware-pilot mcp --transport https --host 0.0.0.0 --port 8443 \
    --cert /etc/vmware-mcp/server.pem --key /etc/vmware-mcp/server-key.pem \
    --public-name mcp.example.com
```

连接客户端（以 Claude Code 为例；任何支持 streamable HTTP 和自定义请求头的 MCP 客户端都可以）：

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

- 没有 token 时服务不会启动；`--public-name` 之外的 Host/Origin 一律拒绝；绑定端口前会自检，确认未认证请求得到 401。
  证书要用客户端信任、且覆盖这些名字的证书。
- 在同一台机器上由反向代理终结 TLS 时，用 `--transport http --host 127.0.0.1 --public-name mcp.example.com`，
  并让代理透传 `Authorization` 头。
- 负载均衡或网关在另一台机器上时，保持 `--transport https`（自签证书也可以），在 `--public-name` 里写上负载均衡
  转发时带的 `Host` 名，并让它透传 `Authorization`。健康检查可以用 `GET /healthz`：不需要 token，只返回
  `{"status": "ok"}`（需 vmware-policy ≥ 1.23.0）。
- 每个请求带上 `traceparent` 或 `X-Request-ID`，它会记进审计记录；用 `vmware-audit log --correlation-id ID`
  就能查到平台上某一次请求对应的调用。
- 审计日志把每个调用者记为 `token:<名字>`。指定不同 target 的调用并行执行；不指定 target 的调用逐个执行，
  所以多人共用一个 server 时请写明 target。
- **能连到这个端点的人，都以该 server 配置的账号操作。** token 只用来识别调用者，不在平台上授予任何权限。
  每个权限级别单独起一个端点。
- **一个端点给多人或多种角色用：** 先运行一次 `vmware-audit roles enable`，再给每个 token 指定角色（`vmware-audit token create 名字 --role reader`，或在 `roles.yaml` 里定义可用的工具和 target；需 vmware-policy ≥ 1.24.0）。角色只能限定这个 token 在本端点上能调用什么，不能超出账号本身的权限。详见 vmware-policy README 的「网络调用者的角色」一节。

**只提供读工具，加上你选定的写工具：**

```bash
vmware-pilot mcp --expose read --allow-write cancel_workflow
# 或：VMWARE_PILOT_MCP_EXPOSE=read VMWARE_PILOT_MCP_ALLOW_WRITE=cancel_workflow vmware-pilot mcp
```

未提供的工具不会出现在客户端的工具列表里。取值错误或写工具名不存在时服务直接退出（exit 2）。
这只限制提供给客户端的工具，不是授权；账号本身仍应遵循最小权限。

详见 `skills/vmware-pilot/references/setup-guide.md`（"Serving MCP over HTTPS"、"Limiting which tools are offered"）。

## 许可证

MIT
