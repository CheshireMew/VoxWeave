# 外部 Agent 与 MCP

VoxWeave 的 Agent 入口是公开 CLI、回环 API 和 stdio MCP，不在桌面应用中内置 Agent、聊天界面或模型调用。MCP 进程只负责把外部 Agent 的工具请求转发给本机 VoxWeave 服务，因此桌面端、CLI、脚本与 Agent 共享同一套参数校验、任务队列、资源仲裁、持久化结果和失败恢复行为。

## 启动

源码环境在已激活项目虚拟环境后直接启动：

```powershell
python -m voxweave.mcp_server
```

安装为 Python 包后也可以运行 `voxweave-mcp`。把该命令和参数填入 Codex、Claude Code 或其它支持 stdio MCP 的外部 Agent 配置即可。MCP 标准输入和输出只传输逐行 JSON-RPC；运行日志仍由 VoxWeave 后台服务写入数据目录，不会污染 MCP 消息。

## 工具发现与任务跟踪

MCP 的 `tools/list` 每次从本机服务的 `/v1/describe` 读取当前合同，并把 `conversion.run` 等操作转换为 `voxweave_conversion_run`。工具的输入 schema、只读/修改标记和长任务语义都来自同一份公开协议，不在 MCP 侧维护第二套易过期参数表。

长任务工具会立即返回任务记录。外部 Agent 应保存其中的 `task_id`，用 `voxweave_task_get` 查询终态；需要中止时调用 `voxweave_task_cancel`。网络或进程边界发生暂时故障时，VoxWeave 仍通过请求收据、输入/模型快照和任务检查点保证重试不会悄悄改用别的素材。

## 安全边界

MCP 只能调用 `describe` 中公开的操作，不能直接访问 SQLite、绕过模型校验或向任意网络地址转发请求。`storage.archive` 会被标记为可能移动文件的工具，并且仍要求协议中的明确确认字段。更新下载只接受项目 GitHub Release 中带发布者 SHA-256 摘要的 Windows ZIP，文件写入数据目录的 `downloads/updates`，不会自动替换正在运行的程序。
