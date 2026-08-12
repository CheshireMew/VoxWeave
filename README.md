<!-- readme-header:start -->

<p align="center">
  <img src="./assets/readme/logo.svg" width="112" alt="VoxWeave">
</p>

<h1 align="center">VoxWeave</h1>

<p align="center">
  <strong>在 Windows 本机完成离线媒体与实时麦克风 RVC 变声。</strong>
</p>

<p align="center">
  <strong>中文</strong> · <a href="./README.en.md">English</a> · <a href="./README.ja.md">日本語</a> | <a href="./docs/ARCHITECTURE.md">文档</a> | <a href="./CONTRIBUTING.md">贡献</a> | <a href="https://github.com/CheshireMew/VoxWeave/issues">反馈</a>
</p>

<p align="center">
  <a href="https://x.com/0xCheshire" title="X"><img src="https://img.shields.io/badge/X-%400xCheshire-000000?logo=x&amp;logoColor=white" alt="X：@0xCheshire"></a>
  <a href="https://t.me/CheshireBTC" title="Telegram"><img src="https://img.shields.io/badge/Telegram-CheshireBTC-26A5E4?logo=telegram&amp;logoColor=white" alt="Telegram：CheshireBTC"></a>
  <a href="https://blog.blacknico.com/" title="Blog"><img src="https://img.shields.io/badge/Blog-blog.blacknico.com-2E7D32?logo=rss&amp;logoColor=white" alt="博客：blog.blacknico.com"></a>
  <a href="https://blacknico.com/" title="Homepage"><img src="https://img.shields.io/badge/Home-blacknico.com-1F6FEB?logo=googlechrome&amp;logoColor=white" alt="个人主页：blacknico.com"></a>
</p>

<p align="center">
  <a href="https://github.com/CheshireMew/VoxWeave/stargazers"><img src="https://img.shields.io/github/stars/CheshireMew/VoxWeave?style=flat" alt="GitHub Stars"></a>
  <a href="https://github.com/CheshireMew/VoxWeave/forks"><img src="https://img.shields.io/github/forks/CheshireMew/VoxWeave?style=flat" alt="GitHub Forks"></a>
  <a href="https://github.com/CheshireMew/VoxWeave/blob/main/LICENSE"><img src="https://img.shields.io/github/license/CheshireMew/VoxWeave?style=flat" alt="Repository License"></a>
</p>

<!-- readme-header:end -->

VoxWeave 是一套在 Windows 本机运行的 RVC 变声工作站。你可以交给它音频、歌曲、视频、目录或麦克风输入，在桌面端完成试听、离线转换、实时变声和批量处理，并从任务中心查看结果、失败原因与产物位置。

<p align="center">
  <img src="./assets/readme/hero.svg" width="100%" alt="媒体、目录和麦克风输入经同一个本机服务生成带校验信息的离线文件或实时播放输出">
</p>

## 先判断它是否适合你

| 你要做什么 | VoxWeave 会交付什么 | 从哪里开始 |
| --- | --- | --- |
| 转换语音、歌曲或视频 | 不覆盖原文件的成品；视频保留原视频流和原音轨，并新增变声音轨 | 转换工作台 |
| 用麦克风实时变声 | 通过所选播放设备输出变声结果，并显示检测、推理耗时和音频中断状态 | 实时变声 |
| 处理整个目录或持续接收新文件 | 可取消、可重试、按内容去重的共享任务 | 批量与监控 |
| 接入脚本或 AI 工具 | 可发现的本机 HTTP/WebSocket 合同和稳定 JSON 结果 | CLI 与 API |

当前真机验收范围是 Windows 11、NVIDIA CUDA。源码保留 Linux 和 macOS 的运行边界，但这两个平台尚未完成真机验收。仓库不提供声音模型、虚拟声卡、模型训练、GPT-SoVITS、安装器或整合运行时。

## 快速开始

### 1. 准备环境

需要 Python 3.12、Git、FFmpeg，以及一块可用的 NVIDIA CUDA 显卡。运行数据必须放在源码目录之外；Python 环境、pip 缓存、临时文件、数据库、日志、下载和任务产物都会进入这个数据目录。

```powershell
git clone https://github.com/CheshireMew/VoxWeave.git
cd VoxWeave
.\scripts\bootstrap.ps1 -DataRoot D:\Tools\VoxWeave
```

如果你已经有锁定兼容版本的 RVC 环境，可以在首次配置时一并声明：

```powershell
.\scripts\bootstrap.ps1 `
  -DataRoot D:\Tools\VoxWeave `
  -RvcRoot E:\path\to\Retrieval-based-Voice-Conversion-WebUI `
  -RvcPython E:\path\to\Retrieval-based-Voice-Conversion-WebUI\.venv\Scripts\python.exe `
  -Ffmpeg D:\path\to\ffmpeg.exe `
  -Ffprobe D:\path\to\ffprobe.exe
```

`requirements.lock` 是 Windows/Python 3.12 的已验收依赖集合，首次配置和 CI 使用同一约束文件。源码目录只保留一个被 Git 忽略的 `.voxweave.local.json` 数据目录指针。

### 2. 启动桌面端

直接双击仓库根目录的 `VoxWeave.vbs`，即可无控制台窗口启动。需要查看启动错误时，在 PowerShell 中运行：

```powershell
.\scripts\run.ps1
```

`VoxWeave.bat` 只负责把旧快捷方式转交给 `VoxWeave.vbs`，随后立即退出。源码更新后，先用 `.\scripts\voxweave.ps1 service stop` 正常关闭旧服务，再重新启动桌面端。

### 3. 没有 RVC 环境时安装运行组件

先启动桌面端，让本机服务可用；然后在新的 PowerShell 窗口提交安装任务：

```powershell
.\scripts\voxweave.ps1 --json execute runtime.install --arguments '{}'
```

安装任务把锁定的 RVC 源码、独立 Python 环境和必需推理资源放入数据目录。默认不会下载许可证无法确认的人声分离权重；WeSpeaker ONNX 权重按 CC-BY-4.0 安装。完整边界见 [第三方说明](THIRD_PARTY_NOTICES.md)。

### 4. 完成第一次转换

1. 在“模型库”中扫描本机目录，或添加你有权使用的 `.pth` 和可选 `.index`。
2. 打开“转换工作台”，选择输入文件、输出位置和目标模型。
3. 先生成试听，确认音高、F0、索引率等参数，再开始完整转换。
4. 在“任务中心”查看进度；完成后可播放或打开最终产物。

VoxWeave 默认不覆盖已有输出。提交任务时会固化输入文件、模型和索引身份，执行前后再次验证；结果清单记录最终文件及 SHA-256，避免重试时悄悄换用已经变化的素材或模型。

## 三条主要工作流

### 离线媒体

转换工作台接受 WAV、FLAC、MP3、MP4 和 MKV。语音模式可以先分析说话人，只转换选中的说话人；歌曲模式可以在用户自行准备分离模型后处理人声并混回伴奏；最多四组参数可以生成同步 A/B 试听。

长音频会在低能量位置分块，但只加载一次 RVC 模型。视频输出复制原视频流和原音轨，再新增命名后的变声音轨；最终发布前会完成媒体解码、清单和哈希检查。

### 实时麦克风

在“设置与诊断”中选择 Windows 音频接口、麦克风和播放设备，再到“实时变声”选择模型与参数。输入和输出必须属于同一个音频接口；连续模式建议使用耳机，避免播放声音被麦克风再次收录。

实时页提供 0.25、0.5 和 1.0 秒三档延迟预算。Silero VAD 和用户设置的麦克风启动阈值共同决定何时进入推理；测试模式会先录入并转换整句话，在停顿后播放，适合在开始连续监听前检查效果。

实时会话和离线任务共用 GPU：已有离线任务运行时不能启动实时会话；实时会话开始后，新任务会保持排队，并在会话停止后继续。

### 批量与持续监控

批量规则把输入目录、输出目录、模型和监控状态保存到数据库。新文件写入稳定后才会进入队列；输出目录会从输入枚举中排除，内容 SHA-256 用于去重，单个失败文件不会让整批结果消失。

任务可以取消、重试并在任务中心统一查看。VoxWeave 不会自动删除中间产物；需要释放空间时，在设置页二次确认后归档，或显式提交 `storage.archive` 长任务。

## CLI 与本机 API

桌面端、CLI 和自动化工具都是同一个本机服务的客户端。调用前先读取正在运行的服务实际声明的操作与 schema，不要在脚本里硬编码一份旧合同：

```powershell
.\scripts\voxweave.ps1 --json describe
.\scripts\voxweave.ps1 --json models
.\scripts\voxweave.ps1 --json execute runtime.inspect --arguments '{}'
```

所有请求使用 `voxweave-control v1`。长任务立即返回 `task_id`，随后用 `task get` 查询，或连接发现文件声明的已认证本机 WebSocket：

```powershell
.\scripts\voxweave.ps1 --json execute conversion.run --arguments '{
  "input":"D:\\media\\source.wav",
  "output":"D:\\media\\source-converted.wav",
  "model":"MODEL_ID_FROM_MODELS",
  "pitch":9,
  "f0":"rmvpe",
  "content_mode":"clean",
  "overwrite":false
}'

.\scripts\voxweave.ps1 --json task get TASK_ID
```

服务只监听 `127.0.0.1` 的随机端口。发现文件包含 PID、协议版本和临时令牌；客户端会先验证进程和握手，不沿用陈旧文件。完整请求、任务和 WebSocket 合同见 [协议说明](docs/PROTOCOL.md)。

## 数据、模型与边界

- SQLite 是模型、预设、任务、批量规则、实时会话、事件、产物与归档记录的唯一状态真源。
- 结构化 JSON 日志位于数据目录的 `logs`，单文件 10 MB，最多保留 5 个轮转文件。
- 诊断导出包含运行时、模型、任务、实时会话、存储统计和日志清单，不嵌入模型或媒体内容。
- 外部模型按原路径登记；VoxWeave 计算权重和索引哈希，不复制、改名或上传模型。
- URL 与官方目录模型只有在来源、大小、SHA-256 和许可证明确时才允许安装。

声音模型可能模仿真实人物或角色。使用者必须取得声音主体、模型作者和素材权利人的必要许可，并遵守适用法律与平台规则。详见 [模型来源与授权政策](MODEL_POLICY.md)。

## 架构与验证

QML 桌面端、CLI 和第三方工具只通过认证的回环 API 进入后台服务，不直接扫描模型、修改任务库或调用 RVC。离线任务由一个串行工作线程处理，实时会话使用独立常驻进程；两者通过同一个 GPU 调度边界协调。

进一步阅读：

- [架构与数据边界](docs/ARCHITECTURE.md)
- [协议说明](docs/PROTOCOL.md)
- [Windows 0.1 实机验收记录](docs/VALIDATION.md)
- [公开 schema](schemas/)
- [变更记录](CHANGELOG.md)

## 开发

先按快速开始创建源码环境，再运行当前 Windows 验收入口：

```powershell
D:\Tools\VoxWeave\.venv\Scripts\python.exe -m ruff check .
D:\Tools\VoxWeave\.venv\Scripts\python.exe -m pytest
```

真实 CUDA 链脚本会经正在运行的服务完成模型解析、任务提交、RVC 推理和最终媒体解码：

```powershell
D:\Tools\VoxWeave\.venv\Scripts\python.exe scripts\verify_real_user_chain.py `
  --input D:\media\voice.wav `
  --model MODEL_ID_FROM_MODELS `
  --output-root D:\Tools\VoxWeave\validation\run
```

贡献前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。当前项目只验收 Windows，不生成安装器、压缩包或整合运行时。

## Star History

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/CheshireMew/VoxWeave/star-history/star-history-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/CheshireMew/VoxWeave/star-history/star-history.svg">
  <img alt="VoxWeave GitHub Star History" src="https://raw.githubusercontent.com/CheshireMew/VoxWeave/star-history/star-history.svg">
</picture>

图表由 GitHub Actions 定期生成，并发布到独立的 `star-history` 分支。

## 许可证与第三方组件

VoxWeave 源码使用 [AGPL-3.0-only](LICENSE)。RVC、Qt、FFmpeg、Python 依赖、推理组件和模型保持各自许可证；本仓库只发布源码，不随仓库分发这些运行时和权重。完整来源、锁定版本和再分发边界见 [第三方说明](THIRD_PARTY_NOTICES.md)。
