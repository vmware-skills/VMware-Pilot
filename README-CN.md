<!-- mcp-name: io.github.vmware-skills/vmware-pilot -->

# VMware-Pilot

编排多步骤 VMware 工作，并明确展示执行状态。

English · [简体中文](README-CN.md)

社区维护 · MIT 开源 · 非 VMware/Broadcom 官方产品。

[![18 秒调查示意演示](https://raw.githubusercontent.com/vmware-skills/marketplace/main/assets/demos/vmware-skills-zh.gif)](https://github.com/vmware-skills/marketplace/blob/main/assets/demos/vmware-skills-zh.mp4)

使用合成数据的示意动画，非实时录屏。[观看家族配音介绍（约 2 分钟）](https://github.com/vmware-skills/marketplace/blob/main/assets/demos/vmware-skills-zh.mp4)

## 能解决什么问题？

- 通过模板或明确的定义构建工作流。
- 跟踪检查点、步骤结果与可恢复状态。
- 把专业操作交给对应 MCP 服务。

## 快速开始

安装已发布 CLI，并查看可用命令：

```sh
uv tool install vmware-pilot==2.5.1
vmware-pilot --help
```

需要 uv 与 Python >=3.10。远程查询前，在本地配置目标与凭据。[配置指南](skills/vmware-pilot/references/setup-guide.md).

Claude Code 及其他宿主安装方式见[家族门户](https://github.com/vmware-skills/marketplace/blob/main/README-CN.md)。

## 使用边界

需要工作流所用的配套 MCP 可执行程序。确认步骤由调用者控制，不是独立人工审批；回滚只能尽力执行。

## 详细文档

- [能力范围](skills/vmware-pilot/references/capabilities.md)
- [CLI 用法](skills/vmware-pilot/references/cli-reference.md)
- [Agent 使用约束](skills/vmware-pilot/references/agent-guardrails.md)
- [详细用法与架构](skills/vmware-pilot/references/detailed-guide-CN.md)
- [唯一技能指令正本](skills/vmware-pilot/SKILL.md)

属于 [VMware Skills 家族](https://github.com/vmware-skills/marketplace/blob/main/README-CN.md)：15 个 skill、14 个 MCP server 与一个共享 Policy 库。如果对你有用，欢迎 Star 家族门户。

[问题与反馈](https://github.com/vmware-skills/VMware-Pilot/issues) · [MIT](LICENSE)
