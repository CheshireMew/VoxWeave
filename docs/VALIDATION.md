# Windows 0.1 validation

## 2026-08-10 实时 VAD 与耳机监听复验

先用固定版本 Silero VAD 6.2.1 读取现有 48 kHz 真实语音文件 `D:\Tools\VoxWeave\validation\selected-speaker-99c491ed.wav`，按生产环境的 0.5 秒音频块持续送入流式检测器。全零输入没有打开推理；真实文件 45 个音频块中 44 个被判定为语音，最高概率 1.0，文件结束后状态正确回到静音。

随后把同一真实语音送入完整生产处理器，实际加载 `Guaiguai V2`、Silero ONNX、RMVPE 和 `cuda:0`，没有替换 VAD 或 RVC。首个静音块的 RVC 调用次数为 0、输出峰值为 0、VAD 概率为 0.009；语音块的 VAD 概率为 0.999，RVC 推理 138 ms，变声输出峰值为 0.3481；连续静音后重新关闭推理并恢复零输出。

最后经正式认证服务和公开 `realtime.start/status/stop` 链路打开 Windows WDM-KS 的“麦克风阵列”到 `Headphones 1`。实际设备以 48 kHz 双声道运行，预计缓冲延迟 540 ms；环境静音期间连续 50 个回调全部跳过 RVC，耳机输出峰值为 0，音频中断为 0。停止只释放 PortAudio 流，已预热工作进程继续以同一 PID 32412 驻留。完整回归为 121 passed、2 skipped；Ruff、compileall、pip check、翻译 JSON 和 `git diff --check` 全部通过。

## 2026-08-10 内聚与边界重构复验

在独立数据根 `D:\Tools\VoxWeave\validation\architecture-refactor-20260810-final` 启动当前源码服务，没有读写正式任务库。公开 CLI 经 `OperationRouter` 提交模型扫描任务并实际检查 13 个本地模型；实机链由认证服务提交 22.086042 秒真实 WAV 和 `local.public-yujie-v2.default`，使用 `cuda:0` 完成 RVC 推理。最终 WAV 完整解码通过，SHA-256 为 `b7e140957480ac483abbc15cd15d4d5d920e65f9bcccf5b78a9ebfb4e19ca638`。

验证继续沿生产者、持久边界和消费者检查：任务事件从 queued 经各 running 阶段到唯一 completed；任务结果、转换清单、产物表和磁盘文件的输出哈希完全一致；桌面任务列表使用的 `task_result_path` 从该真实任务解析到同一 WAV。过程中发现 CLI 的 `scan-models` 分支没有生成长任务所需的 `request_id`，现已把 ID 生成收口到 CLI 唯一发送边界并增加回归测试。隔离服务已停止，日志、数据库、检查点、清单与输出保留在上述目录。

最终全仓库 120 项测试中 118 项通过、2 项按环境条件跳过；Ruff、`compileall` 和 `pip check` 同时通过。架构测试会阻止 repository 跨聚合查询、服务穿透 Controller、Controller 恢复操作分支，以及游标、失败暂存、可取消子进程和旧上帝方法重新出现。

## 2026-08-10 实时变声真机验收

先直接启动实时 RVC 工作进程，使用 `Guaiguai V2`、`cuda:0`、默认 MME 麦克风与扬声器运行 0.5 秒档。工作进程依次发出 warming、running、metrics、stopped，设备采样率 44100 Hz、双声道、预计缓冲延迟 540 ms；真实麦克风输入峰值 0.466，RVC 输出峰值 0.1612，最近推理 132 ms，两次音频回调没有中断，退出码为 0。

随后在独立数据根 `D:\Tools\VoxWeave\validation\realtime-20260810-130111` 启动当前源码服务，经公开 `model.import` 任务实际检查并登记同一模型。离屏加载完整 `Main.qml` 后，由实时变声页的真实开始按钮经 Bridge 和认证 HTTP 协议创建会话，而不是直接调用管理器。QML 消费到 running/streaming 状态和非零生产指标：麦克风峰值 0.04、输出峰值 0.0768、推理 129 ms、音频中断 0；再由页面停止按钮收口为 stopped。隔离服务已正常停止，正式服务和正式数据库没有重启或写入。

加入服务端设备预检、GPU 调度闸门和指标合并后，在同一隔离根再次从 QML 按钮复验：心跳后的预计延迟仍为 540 ms，推理 136 ms，输入/输出峰值 0.0363/0.083，两次回调零中断，最终为 stopped。全仓库结果为 87 passed、2 skipped；Ruff、compileall、pip check 和全部 QML `qmllint` 同时通过，QML 警告数为 0。两个 skip 是隔离构建环境中未重复安装的 setuptools/wheel 检查，不影响运行链。

schema v6 迁移在 `D:\Tools\VoxWeave\validation\realtime-migration-20260810-130847` 验证。先以只读 URI 对正式数据库做 SQLite 在线备份，再只迁移副本；迁移前后的 13 个模型、28 个任务、231 条任务事件、2 条批量规则和 2 条批量项数量完全一致，新建的实时会话与事件表为空，metadata 版本为 6。正式数据库没有由该验证打开为可写模式。

## 2026-08-10 全链加固复验

在独立数据根 `D:\Tools\VoxWeave\validation\hardening-20260810-122614` 启动当前源码服务，没有读写正式任务库。服务通过公开 `model.scan` 长任务实际检查并登记 13 个模型，随后 `verify_real_user_chain.py` 经认证服务提交“公开御姐 V2”转换任务。22.086042 秒真实 WAV 使用 `cuda:0` 和 RMVPE 完成推理，输出完整解码通过，最终 SHA-256 为 `c7645a7fac172b19db2e0d1c35990e37fc2ecdd0ee64648e1cb5913058df4223`。

验证从任务生产者继续追到最终消费者：SQLite 事件顺序为 queued、多个 running 阶段、唯一 completed；转换结果通过 `voxweave-conversion-result v1` Schema，清单哈希、磁盘文件哈希和任务结果一致；桌面 Bridge 从同一服务任务列表选择该输出，拆分后的 QML `MediaPlayer.source` 最终解析为同一文件。隔离目录保留 `validation-summary.json`、结构化服务日志、数据库、检查点、清单和输出，服务已停止。全仓库 Python 测试、Ruff、compileall 和全 QML 静态检查同时通过，QML 警告数为 0。

另使用 SQLite 在线备份把正式数据库只读复制到 `D:\Tools\VoxWeave\validation\migration-20260810-123526` 后执行 schema v5 迁移。迁移前后的 13 个模型、28 个任务和任务状态分布完全一致：cancelled 2、completed 21、failed 4、interrupted 1；2 条批量项全部保留，没有产生重复项历史。正式数据库没有由该验证打开为可写模式。

本记录对应 2026-08-09 的源码状态。验收机为 Windows 11、NVIDIA GeForce RTX 2080 SUPER、CUDA 可用；macOS 和 Linux 没有真机结果，不写成已验证。

## 冷启动与运行时

从没有 VoxWeave 环境的新 D 盘数据目录运行 `scripts/bootstrap.ps1`，使用 D 盘 Python、pip 缓存和临时目录完成源码依赖安装。随后认证握手、锁定 RVC revision 检查、CUDA 检测、13 个本地模型扫描和一次真实推理均通过。冷环境 WAV 为 22.086042 秒、PCM 24-bit、完整解码通过。验收后源码指针恢复到正式数据根；冷目录保留用于追溯，没有打包或删除。

## 模型与推理

当前机器 13 个模型全部符合 `voxweave-rvc-model v1` Schema。重点三个模型的 ID 和权重 SHA-256 分别为：

| 模型 | 稳定 ID | 权重 SHA-256 |
| --- | --- | --- |
| 公开御姐 V2 | `local.public-yujie-v2.default` | `94efc06f24776483676d9d1d675eb9b68f6ffee4e233a5f53d629b01b9ca0610` |
| Keruan V1 | `local.keruan-v1.default` | `9b3808d83e99b7c7b01cc1e33cee79ab8f1ebed08708b72066917465ea336e56` |
| Guaiguai V2 | `local.guaiguai-v2.default` | `7543d35e1c8997cd1637d2b0970577103ecbfd8fa6ca5bc7568a8207c5ed07d1` |

同一 22.086042 秒真实语音分别转换后，三个输出都完整解码，产物清单的模型/索引哈希与实际加载文件一致，输出 SHA-256 彼此不同。公开御姐 +9/+12 的混合素材 A/B 预览也走完分离、伴奏混回和响度链，两个 15 秒产物哈希不同。

## 视频

此前三段真实视频全部以混合语音模式跑完整片：

| 案例 | 输入时长 | 输出时长 | RVC 块 | 原/变声音轨响度差 | 结果 |
| --- | ---: | ---: | ---: | ---: | --- |
| A | 755.498333 s | 755.498333 s | 17 | 0.6 LU | 完整解码通过 |
| B | 723.487120 s | 723.487120 s | 16 | 0.9 LU | 完整解码通过 |
| C | 627.455667 s | 627.456000 s | 15 | 0.4 LU | 完整解码通过 |

三个成片均有一条原 H.264 视频流、原 AAC 音轨和新增命名 AAC 变声音轨。对视频流和原音轨分别做 stream-copy SHA-256，源文件与成片逐一完全一致；因此视频未重编码、原音轨未替换。案例 C 的时长误差为 0.000333 秒，小于 30 fps 的一帧。

## 歌曲与多人

歌曲使用 Wikimedia Commons 上由表演者以 CC0 发布的 60 秒 `Freesoftwaresong_126_mix.ogg`。歌曲模式记录 `vocals-bs-roformer-368` 分离模型，主唱转换并混回伴奏后得到 60.000375 秒 WAV，完整解码通过，-12.2 LUFS、-1.0 dBFS。该分离权重本身仍是 `LicenseRef-Unknown`，只存在于开发机，不属于发布物。

两说话人加重叠测试由真实男声和已生成女声交替组成。全局 WeSpeaker 聚类得到两个主说话人，各 4 段；两个低置信重叠区间标为 `unresolved`。只选择 `speaker-1` 且跳过重叠后，两个选中片段明显变化，未选说话人、短未知段和重叠段的逐样本误差均为 0；源/输出响度为 -24.6/-24.4 LUFS。

## 任务与桌面链

已验证一次性批量、持续监控的 5 秒文件稳定判断、失败隔离、重复扫描不重复入队、任务取消、服务退出后标记中断和显式重试。一次故意在最终封装失败的任务重试时，通过检查任务事件确认源提取、RVC 输出和响度三个阶段都在重新计算 SHA-256 后复用。

QML 使用的 Bridge 实际创建转换任务；后台完成后，同一个任务出现在共享任务列表，播放器 `MediaPlayer.source` 绑定到最终 WAV，本机诊断 JSON 也从设置页 Bridge 导出成功。视频、歌曲、模型和任务产物都保存在所选数据目录；没有生成安装器、压缩包或运行时整合包。
