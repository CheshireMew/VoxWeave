# voxweave-control v1

`voxweave describe` 是操作、参数、提交结果和完成结果的运行时真源。每个操作都返回 `arguments_schema` 与 `result_schema`，长任务另外返回 `submission_schema`。本文件解释调用方式，不复制一份会与代码分叉的完整参数表。

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

成功响应保留同一协议、版本和 `request_id`，并在 `result` 中返回经过对应 Schema 校验的结果。失败响应返回稳定的 `error_type` 和可读 `error`；处理器产生不符合合同的输出会成为 `invalid_result`，不会让任务停在运行中。CLI 的 `--json` 模式只输出这一 JSON，不混入进度文字。所有会修改状态的操作和长任务都必须提供 `request_id`。ID 在整个服务内全局唯一，并与操作、规范化参数和 `actor` 绑定；完全相同的请求会回放第一次持久化的结果，不同请求复用该 ID 会返回 `idempotency_conflict`。客户端只可自动重试只读 HTTP 请求，或带有 `request_id` 的 `/v1/execute` 请求。

长任务的初次结果是完整任务记录并包含相同值的 `id` 和 `task_id`。`task.list` 只分页返回状态、进度和错误摘要，不传输可能很大的参数、快照与结果；需要查看某项时再用 `task.get` 读取完整记录和产物。`task.cancel` 设置取消请求，queued 任务立即进入终态，当前外部子进程会连同子进程树一起终止；`task.retry` 只接受失败、取消或中断任务，并沿用原执行快照。服务 WebSocket 路径为 `/v1/events`，认证令牌取自数据目录的服务发现文件。连接 `/v1/events?token=...&after_id=...` 会从游标后发送所有任务事件；增加 `task_id=...` 查询参数只订阅指定任务。HTTP 也可用 `GET /v1/tasks/{task_id}/events?after_id=...` 补取指定任务事件。

批量操作通过 `batch.list` 分页管理规则和当前汇总状态，`batch.get` 读取单条规则、运行与子项，`batch.update` 修改后续文件使用的目录、模型和完整转换参数，`batch.archive` 以可恢复方式停用规则，`batch.run` 创建持久父任务，`batch.retry` 只为失败、取消或中断的子任务创建重试并重新汇总父任务。扫描期间任何单文件提交失败都会记录在批量运行中并影响最终状态。

所有媒体路径必须是绝对路径。产物默认不覆盖，只有相应操作明确接受且收到 `overwrite: true` 时才覆盖。模型选择器可以是唯一 ID、显示名称或唯一别名；同一选择器解析出多个模型会作为冲突失败。`model.archive` 只停用或恢复模型登记，不删除权重、索引、预设或历史任务；归档模型不能进入新的转换和实时会话。

最终媒体先写入目标目录内的隐藏临时文件，完整解码和结果清单成功后才原子替换目标路径。发布意图先进入转换检查点，因此服务在文件发布后、任务状态提交前异常退出时，显式重试会按哈希识别已经发布的文件，不会重新推理或覆盖不匹配的文件。

`storage.archive` 是显式长任务，不存在后台自动清理。调用者必须给出绝对 `destination_root` 并传入 `confirm_source_removal: true`；可用 `older_than_days` 选择已结束的旧任务，或用 `task_ids` 指定一组终态任务。同盘移动目录；跨盘先复制并逐文件校验，产物记录保留原始 `path`，另以 `archive_path` 指向当前位置，随后才移除活动目录里的源副本。任务参数、执行快照和结果是历史事实，归档不会改写它们。无对应任务、仍在运行或目标冲突的目录不会被处理。

`runtime.inspect` 和 `diagnostics.snapshot` 都是可取消长任务。诊断结果包含后台当下的设置、运行时检查、模型合同、任务、最近 500 条事件、存储占用和轮转日志清单；它不读取或内嵌模型与媒体内容。

`settings.get` 返回完整设置和当前 `revision`。`settings.update` 必须带 `expected_revision`，可以单独更新界面语言，也可以只提交需要改变的实时字段；服务在一个版本事务中合并补丁，版本过期返回 `revision_conflict`。`settings.events` 用 `after_revision` 增量读取已提交版本，供多个窗口同步。实时配置包含模型 ID、音频接口名称、输入与播放设备名称、音高、F0 算法、索引率、音量包络、VAD 阈值、`input_gate_db` 麦克风启动阈值、音频块长度和测试模式。设备编号只用于当次 `realtime.start`，不会写入用户设置。

实时控制不进入长任务队列。先调用 `realtime.devices` 获取设备 ID；`realtime.audio_test` 可以对指定输入设备采样并返回峰值/RMS，或向指定输出设备播放低音量测试音。`realtime.prepare` 是明确的预热操作，会占用 GPU 并暂停离线队列，桌面端不会在页面打开后偷偷调用它；也可以直接调用 `realtime.start` 并等待同一预热流程。`realtime.start` 至少需要模型选择器、输入设备和输出设备，可用 `block_seconds` 选择 0.25、0.5 或 1.0 秒，用 `vad_threshold` 在 0.1 至 0.9 之间调整 Silero 语音检测阈值，用 `input_gate_db` 在 -60 至 -20 dB 之间设置麦克风启动电平。输入和输出必须属于同一个 Windows 音频接口。服务为实时功能维护唯一常驻工作进程；第一次启动或模型、设备及关键推理参数变化时完成 RVC、VAD 和音频处理器预热，相同配置再次启动时复用预热结果。工作进程启动和模型预热都有明确期限，超时会失败、终止进程并恢复离线队列。麦克风按单声道采集；Silero 检测到人声时进入 RVC，Silero 漏检但输入电平达到设置的启动阈值时也会进入 RVC。普通模式与测试模式共用这个门限，不再保留第二套固定峰值判断。`test_mode=true` 时，用户说话期间只转换并缓存音频，连续静音约 0.8 秒后再顺序播放整句；播放期间以及播放队列清空后的扬声器尾音隔离阶段都会丢弃麦克风输入。切换测试模式不会让模型重新预热。实时音频不会写入文件。`realtime.start` 立即返回已持久化的 starting 会话，调用方继续查询 `realtime.status`；`worker.state=ready` 和 `worker.model_ready=true` 表示预热完毕，running 状态的 `metrics` 包含 `speech_detected`、`speech_source`、`rvc_inference_active`、`test_phase`、`buffered_blocks`、`playback_active`、`microphone_suppressed`、VAD 概率、推理与缓冲耗时、音频中断次数和输入/输出电平。`realtime.stop` 只停止音频流；如果底层音频线程没有在停止期限内退出，当前工作进程会进入 failed 并退出，不会把会话伪装成 stopped，也不会复用该处理器。`realtime.release` 在没有活动会话时关闭驻留工作进程并释放 GPU。已有后台任务运行时启动会失败；实时会话期间提交的长任务保持 queued，停止后继续。健康状态下 `realtime.stop` 和 `realtime.release` 都是幂等操作。服务重启会把未结束会话标记为 interrupted，常驻状态不会跨服务进程恢复。

模型响应遵守 `voxweave-rvc-model v1`，转换结果遵守 `voxweave-conversion-result v1`。JSON Schema 位于 [`schemas/`](../schemas/)；Schema 用于外部客户端静态校验，实际可用操作仍以本轮 `describe` 为准。
