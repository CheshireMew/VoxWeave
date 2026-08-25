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

批量操作通过 `batch.list` 分页管理规则和当前汇总状态，`batch.get` 读取单条规则、运行与可重试子项，`batch.update` 修改后续文件使用的目录、多组模型/预设变体、命名模板、目录结构、输出格式、glob 过滤与冲突策略，`batch.archive` 以可恢复方式停用规则。每个变体可设置自己的扩展名及包含/排除 glob；同一源文件命中多项时会创建多个独立输出。`batch.plan` 在不提交转换的情况下返回输入文件数、输出数、总大小、目标示例与冲突，`batch.run` 创建持久父任务，`batch.retry` 为整条规则的失败项重试，`batch.item.retry` 则只重配并重试一个失败、取消或中断项。扫描期间任何单文件提交失败都会记录在批量运行中并影响最终状态。

转换工程由 `project.create/get/list/update/archive` 管理；`project.analyze` 建立波形与可编辑片段，`project.preview` 试听单片段，`project.run` 按片段分配的模型和参数渲染。每次工程更新都会产生修订，`project.history` 与 `project.restore` 可以查看并恢复历史文档。工程级 `default_parameters.processing_chain` 会作为完整成品的处理链执行，其中 `dereverb_strength` 会在其它 FFmpeg 成品滤镜前通过独立音频工作进程抑制晚期混响；片段参数只覆盖明确设置的推理参数。`result.get/list/update` 返回完整成品、父版本、根版本、代数、子版本和相对父版本的差异；`result.rerun` 会核对原输入哈希和精确模型/索引修订，再创建子版本，无法取得原修订时明确失败。

预设通过 `preset.create/copy/update/archive/list/export/import` 完成生命周期管理；导入会保留来源信息并标记需要重新确认的预设。模型登记可以维护封面、自定义名称、标签、收藏、备注和试听样本，列表返回使用次数、最近使用时间、相同权重的其它模型以及最近一次完整性状态；`model.verify` 重新核对权重和索引文件，`model.compare` 对同一输入用 2–8 个模型生成并列试听结果。

所有媒体路径必须是绝对路径。产物默认不覆盖，只有相应操作明确接受且收到 `overwrite: true` 时才覆盖。模型选择器可以是唯一 ID、显示名称或唯一别名；同一选择器解析出多个模型会作为冲突失败。`model.archive` 只停用或恢复模型登记，不删除权重、索引、预设或历史任务；归档模型不能进入新的转换和实时会话。

最终媒体先写入目标目录内的隐藏临时文件，完整解码和结果清单成功后才原子替换目标路径。发布意图先进入转换检查点，因此服务在文件发布后、任务状态提交前异常退出时，显式重试会按哈希识别已经发布的文件，不会重新推理或覆盖不匹配的文件。

`storage.inspect` 返回各数据区及结果、中间产物、失败运行等类别的占用、磁盘余量、可归档任务估算和迁移记录，不修改文件。`storage.archive` 是显式长任务，不存在后台自动清理。调用者必须给出绝对 `destination_root` 并传入 `confirm_source_removal: true`；同盘移动目录，跨盘先复制并逐文件校验。`storage.restore` 从已完成归档校验复制回原活动位置，归档副本保留为回退来源。`storage.migration.plan` 返回源/目标、文件数、字节数、冲突和计划摘要；`storage.migration.prepare` 只有在摘要仍匹配时生成迁移清单与外部启动命令。迁移启动器等待服务释放锁，排除服务发现、锁和 SQLite 临时文件，复制并校验全部持久数据，更新数据根指针后再启动应用；旧数据根不会被删除。

`update.check` 只读取项目 GitHub Release 元数据；GitHub 公共 API 限流时会返回明确的重试时间和发布页地址，进程环境存在 `GITHUB_TOKEN` 时会自动用于提高限额。`update.download` 只接受发布记录中带发布者 SHA-256 摘要的 Windows ZIP。`update.install` 把归档解压到按版本隔离的组件目录并验证唯一的 `VoxWeave.exe`，不会覆盖当前版本；`update.activate` 生成一次性健康令牌并由外部启动器切换，只有新版本写入健康标记后才提交激活状态，失败或超时自动恢复上一可执行文件。`update.rollback` 通过同一健康检查链切回指定或可用的已安装版本。外部 Agent 可以通过 stdio MCP 动态读取同一份 `describe` 合同，配置和任务跟踪方式见 [外部 Agent 与 MCP](MCP.md)。

`runtime.inspect` 和 `diagnostics.snapshot` 都是可取消长任务。诊断结果包含后台当下的设置、运行时检查、模型合同、任务、最近 500 条事件、存储占用和轮转日志清单；它不读取或内嵌模型与媒体内容。

`settings.get` 返回完整设置和当前 `revision`。`settings.update` 必须带 `expected_revision`，可以单独更新界面语言，也可以只提交需要改变的实时字段；服务在一个版本事务中合并补丁，版本过期返回 `revision_conflict`。`settings.events` 用 `after_revision` 增量读取已提交版本，供多个窗口同步。实时配置包含模型 ID、音频接口名称、输入与播放设备名称、音高、F0 算法、索引率、音量包络、VAD 阈值、`input_gate_db` 麦克风启动阈值、音频块长度和测试模式。设备编号只用于当次 `realtime.start`，不会写入用户设置。

实时控制不进入长任务队列。先调用 `realtime.devices` 获取设备 ID；`realtime.audio_test` 对指定输入设备采样，`realtime.calibrate` 返回噪声底、信号、信噪比、音高范围、设备稳定度以及推荐的噪声门、VAD、音高、索引率与块长度，`realtime.routing.test` 会实际打开指定入出设备完成闭环探测。`realtime.prepare` 是明确的预热操作，会占用 GPU 并暂停离线队列；也可以直接调用 `realtime.start` 并等待同一流程。`realtime.start` 至少需要模型选择器、输入设备和输出设备，可用 `block_seconds` 选择 0.25、0.5 或 1.0 秒，用 `vad_threshold` 调整 Silero 语音检测，用 `input_gate_db` 设置麦克风启动电平，用 `push_to_talk=true` 启用按住说话。输入和输出必须属于同一个 Windows 音频接口。服务维护唯一常驻实时工作进程；相同模型、设备及关键参数会复用预热结果。麦克风按单声道采集；Silero 或电平门打开时进入 RVC。`test_mode=true` 时先缓存整句，静音后再顺序播放，并在播放和尾音阶段丢弃麦克风回采。`realtime.control` 可以即时切换旁路、静音、录音、按键说话启用状态与按下状态。录音异步写出干声和湿声 WAV，停止后生成包含哈希、模型快照与运行指标的清单；`realtime.recording.promote` 核对清单与两个文件后创建离线工程。`realtime.scene.*` 保存可修订、可归档的模型、设备名称、参数、录音开关与全局快捷键；`realtime.routing.inspect` 识别 VB-CABLE 和 Voicemeeter。`realtime.start` 立即返回持久化的 starting 会话，调用方继续查询 `realtime.status`。`realtime.stop` 只停止音频流；线程超时会失败并退出工作进程，不会伪装成 stopped。`realtime.release` 在无活动会话时释放驻留进程和 GPU。已有后台任务运行时启动会失败；实时期间的新长任务保持 queued，停止后继续。服务重启会把未结束会话标记为 interrupted。

模型响应遵守 `voxweave-rvc-model v1`，转换结果遵守 `voxweave-conversion-result v1`。JSON Schema 位于 [`schemas/`](../schemas/)；Schema 用于外部客户端静态校验，实际可用操作仍以本轮 `describe` 为准。
