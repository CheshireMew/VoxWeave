# VoxWeave

[English](docs/README.en.md)

VoxWeave 是本地运行的高质量离线与实时 RVC 变声工作台。桌面端、命令行和本机 HTTP/WebSocket API 共用同一个后台服务、状态数据库和推理边界，因此界面中的转换任务与实时会话也能由自动化工具查询和控制。

0.1 支持音频、歌曲、视频、实时麦克风、批量目录和持续监控目录；可以做人声/伴奏分离、VAD、多说话人聚类、指定说话人转换、最多四组参数试听，并为最终产物或实时会话记录模型与索引哈希。它不包含模型训练、GPT-SoVITS 或虚拟声卡。

当前真实验收平台是 Windows 11、NVIDIA CUDA。源码保留 Windows、Linux 和 macOS 的运行边界，但后两个平台尚未经过真机验收。

## 从源码运行

需要 Python 3.12、Git 和 FFmpeg。Windows 首次配置时明确选择源码目录之外的数据目录：

```powershell
cd E:\path\to\VoxWeave
.\scripts\bootstrap.ps1 `
  -DataRoot D:\Tools\VoxWeave `
  -RvcRoot E:\path\to\Retrieval-based-Voice-Conversion-WebUI `
  -RvcPython E:\path\to\Retrieval-based-Voice-Conversion-WebUI\.venv\Scripts\python.exe `
  -Ffmpeg D:\path\to\ffmpeg.exe `
  -Ffprobe D:\path\to\ffprobe.exe
```

`bootstrap.ps1` 把 Python 环境、pip 缓存、临时文件、状态、下载和产物放进指定的数据目录。源码目录只留下被 Git 忽略的 `.voxweave.local.json` 指针。启动桌面端：

源码环境使用仓库内的 `requirements.lock` 锁定 Windows/Python 3.12 已验收的完整依赖集合，安装脚本和 CI 都通过同一约束文件解析依赖。

```powershell
.\scripts\run.ps1
```

Windows 也可以直接双击仓库根目录的 `VoxWeave.bat`。它调用同一个 PowerShell 启动入口，不维护第二套环境或服务逻辑。

如果没有现成 RVC 环境，可先完成源码环境配置，再请求后台安装锁定版本：

```powershell
.\scripts\voxweave.ps1 --json execute runtime.install --arguments '{}'
```

这会在数据目录内安装 RVC 源码、独立环境和必需推理资源。人声分离模型默认不下载，因为当前指定模型没有可确认的再分发许可证；用户审查来源后可显式传入 `install_separation: true`。WeSpeaker ONNX 模型使用 CC-BY-4.0，默认安装。

## CLI 与 AI 调用

先读取本轮服务真实能力，不要硬编码操作：

```powershell
.\scripts\voxweave.ps1 --json describe
.\scripts\voxweave.ps1 --json models
.\scripts\voxweave.ps1 --json execute runtime.inspect --arguments '{}'
```

所有请求使用 `voxweave-control v1`。长任务立即返回 `task_id`：

```powershell
.\scripts\voxweave.ps1 --json execute conversion.run --arguments '{
  "input":"D:\\media\\source.wav",
  "output":"D:\\media\\source_public-yujie_default.wav",
  "model":"公开御姐",
  "pitch":9,
  "f0":"rmvpe",
  "content_mode":"clean",
  "overwrite":false
}'
```

随后使用 `task.get` 查询，或连接发现文件所声明的本机 WebSocket。发现文件含随机回环端口、PID、协议版本和临时令牌；客户端会先验证进程和协议，不信任陈旧文件。完整合同见 [协议说明](docs/PROTOCOL.md)，架构见 [架构说明](docs/ARCHITECTURE.md)，当前实机结果见 [验收记录](docs/VALIDATION.md)。

实时变声页会列出 RVC 运行环境实际识别的 Windows 音频接口、麦克风和播放设备，并优先选择 Windows WASAPI 的默认输入和输出；WASAPI 不可用时才回退到其它接口。模型、音频接口、输入/播放设备、音高、算法、各项比例、麦克风启动阈值、延迟档和测试模式会统一写入用户设置；重启后按音频接口和设备名称恢复，不依赖可能变化的 PortAudio 临时编号。麦克风按单声道采集，Silero VAD 负责判断人声；当 VAD 漏检但实际输入达到用户设置的启动阈值时，音频仍会进入 RVC。测试模式也使用同一个阈值，环境噪音不会再通过另一条固定门限启动或延长录音。页面会显示语音检测、变声输出以及输入/输出电平。默认 0.5 秒音频块；性能不足时可切到 1.0 秒稳定档，增加延迟来换取连续性。测试模式会在用户说话时只录入和转换，检测到停顿后再播放整句话；播放期间和尾音结束前丢弃麦克风输入。普通连续模式仍建议使用耳机。输入和输出必须属于同一个音频接口。自动化调用依次使用 `realtime.devices`、`realtime.start`、`realtime.status` 和 `realtime.stop`。实时会话不占离线任务线程，但会独占 GPU：已有任务运行时不能启动，之后提交的任务保持排队并在实时会话停止后继续。

源码更新后需要重启已有后台时，先运行 `.\scripts\voxweave.ps1 service stop`，再运行 `.\scripts\run.ps1`。停止命令使用服务发现文件中的临时令牌调用正常关闭流程，不直接结束未知进程。

VoxWeave 不会自动删除中间产物。需要释放活动数据目录空间时，可在设置页选择归档位置并二次确认，也可以显式提交 `storage.archive` 长任务；它只处理已经结束且达到指定年龄的任务目录，搬迁后同步更新任务结果里的路径。同盘直接移动目录，跨盘先逐文件复制并校验，再移除源副本。

后台把结构化 JSON 日志写入数据目录的 `logs` 文件夹，单文件 10 MB、最多保留 5 个轮转文件。设置页导出的诊断 JSON 来自后台实时状态，包含运行时、模型、实时会话、任务、最近事件、各存储区域大小和日志清单，不包含模型或媒体文件本身。

## 模型

VoxWeave 不复制或改名外部模型。扫描只登记规范化路径、最终 SHA-256、权重结构、索引候选、来源和许可证。URL/目录模型只有在许可明确时才能加入官方目录；未知许可模型只能由用户在本机导入。详见 [模型政策](MODEL_POLICY.md) 和空的、可审计的 [官方目录](catalog/catalog.v1.json)。

模型文件可能模仿真实人物或角色。使用者必须取得适用的同意与授权，并遵守当地法律及平台规则。VoxWeave 不提供任何模型，也不主张用户模型的权利。

## 开发验证

```powershell
D:\Tools\VoxWeave\.venv\Scripts\python.exe -m ruff check .
D:\Tools\VoxWeave\.venv\Scripts\python.exe -m pytest
```

真实 CUDA 链验证脚本会通过正在运行的服务依次完成模型解析、任务提交、推理和最终媒体解码：

```powershell
D:\Tools\VoxWeave\.venv\Scripts\python.exe scripts\verify_real_user_chain.py `
  --input D:\media\voice.wav `
  --model 公开御姐 --model "Keruan V1" --model "Guaiguai V2" `
  --output-root D:\Tools\VoxWeave\validation\run
```

脚本默认禁止覆盖。现阶段不生成安装器、压缩包或整合运行时。

## 许可证

VoxWeave 源码使用 [AGPL-3.0-only](LICENSE)。第三方软件、推理组件和模型保持各自许可证，详见 [第三方说明](THIRD_PARTY_NOTICES.md)。
