# voxweave-control v1

`voxweave describe` 是操作和参数的运行时真源。本文件解释调用方式，不复制一份会与代码分叉的完整参数表。

请求体：

```json
{
  "protocol": "voxweave-control",
  "version": 1,
  "request_id": "client-generated-id",
  "operation": "runtime.inspect",
  "arguments": {}
}
```

成功响应保留同一协议、版本和 `request_id`，并在 `result` 中返回结果。失败响应返回稳定的 `error_type` 和可读 `error`。CLI 的 `--json` 模式只输出这一 JSON，不混入进度文字。

长任务的初次结果包含 `task_id`。`task.get` 返回持久化状态、阶段、进度、参数、错误和结果；`task.cancel` 设置取消请求，当前推理子进程会被终止；`task.retry` 只接受失败、取消或中断任务。服务 WebSocket 路径为 `/v1/events`，认证令牌取自数据目录的服务发现文件。

所有媒体路径必须是绝对路径。产物默认不覆盖，只有相应操作明确接受且收到 `overwrite: true` 时才覆盖。模型选择器可以是唯一 ID、显示名称或唯一别名；同一选择器解析出多个模型会作为冲突失败。

模型响应遵守 `voxweave-rvc-model v1`，转换结果遵守 `voxweave-conversion-result v1`。JSON Schema 位于 [`schemas/`](../schemas/)；Schema 用于外部客户端静态校验，实际可用操作仍以本轮 `describe` 为准。
