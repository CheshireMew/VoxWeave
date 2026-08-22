# Third-party notices

VoxWeave 源码使用 AGPL-3.0-only。以下项目没有因此改用 VoxWeave 许可证；分发者仍须遵守各项目的原始条款。Windows ZIP 附带启动桌面应用所需的 CPython 3.12、PySide6/Qt 和 Python 依赖，但不附带 RVC、托管 RVC Python 环境、FFmpeg、推理权重或声音模型。首次安装功能只在用户确认后从锁定的上游地址下载并校验这些大体积运行文件，再放入用户选择的数据目录。

| 组件 | 用途 | 许可证/状态 | 来源 |
| --- | --- | --- | --- |
| Retrieval-based-Voice-Conversion-WebUI | RVC 推理核心 | MIT；运行时锁定提交 `4338f12c…` | https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI |
| PySide6 / Qt for Python 6.11.1 | 桌面 UI；随 Windows ZIP 分发独立 DLL | 社区版按 LGPL-3.0-only 分发；也可选 GPL-3.0-only | https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.11.1-src/ |
| Qt 6.11.1 | PySide6 的 Qt DLL；随 Windows ZIP 分发独立 DLL | LGPL-3.0-only/GPL-3.0-only，个别模块及第三方代码保留各自条款 | https://download.qt.io/official_releases/qt/6.11/6.11.1/single/ |
| FFmpeg / gyan.dev essentials build | 解封装、滤镜、编码和封装 | GPL-3.0；托管安装器锁定 `8.1.2-essentials` | https://www.gyan.dev/ffmpeg/builds/ |
| CPython 3.12 | Windows ZIP 中的应用解释器 | PSF-2.0；精确补丁版本记录在每个发布清单 | https://www.python.org/downloads/source/ |
| Python Standalone Builds / CPython | 另行下载的托管 RVC 环境解释器 | MPL-2.0 / PSF-2.0；托管安装器锁定 CPython 3.12.13 x64 build 20260807 | https://github.com/astral-sh/python-build-standalone |
| FastAPI | 本机 HTTP API | MIT | https://github.com/fastapi/fastapi |
| Uvicorn | ASGI 服务 | BSD-3-Clause | https://github.com/encode/uvicorn |
| websockets | Uvicorn 的本机 WebSocket 传输 | BSD-3-Clause | https://github.com/python-websockets/websockets |
| Pydantic | 请求数据边界 | MIT | https://github.com/pydantic/pydantic |
| PyInstaller 6.22.0 | Windows EXE bootloader 与运行钩子 | GPL-2.0-only WITH Bootloader-exception；许可证随 ZIP 分发 | https://github.com/pyinstaller/pyinstaller |
| NumPy | 音频数组处理 | BSD-3-Clause（发行物可能含兼容许可组件） | https://github.com/numpy/numpy |
| SciPy | 聚类与数值处理 | BSD-3-Clause | https://github.com/scipy/scipy |
| SoundFile / libsndfile | WAV/FLAC 音频读写 | BSD-3-Clause / LGPL-2.1-or-later | https://github.com/bastibe/python-soundfile |
| python-sounddevice / PortAudio | RVC 运行环境中的实时音频采集与播放 | MIT / MIT | https://python-sounddevice.readthedocs.io/ |
| Silero VAD | 语音活动检测 | MIT | https://github.com/snakers4/silero-vad |
| WeSpeaker | 说话人嵌入代码 | Apache-2.0 | https://github.com/wenet-e2e/wespeaker |
| `wespeaker-resnet34-LM` ONNX | 说话人嵌入权重 | CC-BY-4.0；锁定 HF revision `f0c48c…` | https://huggingface.co/Wespeaker/wespeaker-resnet34-LM |
| python-audio-separator / PyMSS integration | 人声分离代码路径 | MIT | https://github.com/nomadkaraoke/python-audio-separator |
| `vocals-bs-roformer-368` | 可选人声分离权重 | `LicenseRef-Unknown`，不随 VoxWeave 分发，默认不下载 | https://huggingface.co/baicai1145/pymss |
| `chaye741/RVC-Voice-Models` 中文社区声音模型 | 可选中文女声与男声下载 | `LicenseRef-Unknown`；上游没有模型卡或明确授权说明，按单个模型和索引下载，不随 EXE 分发 | https://huggingface.co/chaye741/RVC-Voice-Models |

正式 Windows ZIP 的 `licenses/runtime-components.json` 记录所有随应用分发的直接和传递 Python 组件及精确版本，`licenses/python/` 保存构建环境中每个组件实际安装的许可证文件，`licenses/CPython/` 保存应用解释器许可证，`licenses/GNU/` 保存完整 GPL-3.0 与 LGPL-3.0 文本。`QT_PYSIDE_COMPLIANCE.md` 说明 Qt/PySide 对应源码、动态库替换方式和发布者需要维持的源码入口。根目录 `release-manifest.json` 记录源码提交、组件、文件大小和 SHA-256；发布脚本在 ZIP 生成后解压到新目录并逐文件复验。

用户导入的 `.pth/.index` 保持模型作者的独立许可。VoxWeave 只在本机记录其路径、来源、许可证声明和哈希；当前开发机上的私人模型不属于本项目发布物。
