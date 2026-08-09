# VoxWeave

[English](docs/README.en.md)

VoxWeave 是本地运行的高质量离线 RVC 变声工作台。桌面端、命令行和本机 HTTP/WebSocket API 共用同一个后台服务、任务数据库和推理队列，因此人在界面中创建的任务也能由自动化工具查询、取消和重试。

0.1 支持音频、歌曲、视频、批量目录和持续监控目录；可以做人声/伴奏分离、VAD、多说话人聚类、指定说话人转换、最多四组参数试听，并为最终产物记录模型与索引哈希。它不包含模型训练、GPT-SoVITS、实时麦克风或虚拟声卡。

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

```powershell
.\scripts\run.ps1
```

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
