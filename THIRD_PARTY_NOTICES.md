# Third-party notices

VoxWeave 源码使用 AGPL-3.0-only。以下项目没有因此改用 VoxWeave 许可证；分发者仍须遵守各项目的原始条款。本仓库当前只发布源码，不附带 RVC、FFmpeg、Python 环境、推理权重或声音模型。

| 组件 | 用途 | 许可证/状态 | 来源 |
| --- | --- | --- | --- |
| Retrieval-based-Voice-Conversion-WebUI | RVC 推理核心 | MIT；运行时锁定提交 `4338f12c…` | https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI |
| PySide6 / Qt for Python | 桌面 UI | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only | https://doc.qt.io/qtforpython-6/ |
| FFmpeg | 解封装、滤镜、编码和封装 | 取决于用户安装的构建；当前开发机构建报告 GPL v3 enabled | https://ffmpeg.org/legal.html |
| FastAPI | 本机 HTTP API | MIT | https://github.com/fastapi/fastapi |
| Uvicorn | ASGI 服务 | BSD-3-Clause | https://github.com/encode/uvicorn |
| Pydantic | 请求数据边界 | MIT | https://github.com/pydantic/pydantic |
| NumPy | 音频数组处理 | BSD-3-Clause（发行物可能含兼容许可组件） | https://github.com/numpy/numpy |
| SciPy | 聚类与数值处理 | BSD-3-Clause | https://github.com/scipy/scipy |
| SoundFile / libsndfile | WAV/FLAC 音频读写 | BSD-3-Clause / LGPL-2.1-or-later | https://github.com/bastibe/python-soundfile |
| Silero VAD | 语音活动检测 | MIT | https://github.com/snakers4/silero-vad |
| WeSpeaker | 说话人嵌入代码 | Apache-2.0 | https://github.com/wenet-e2e/wespeaker |
| `wespeaker-resnet34-LM` ONNX | 说话人嵌入权重 | CC-BY-4.0；锁定 HF revision `f0c48c…` | https://huggingface.co/Wespeaker/wespeaker-resnet34-LM |
| python-audio-separator / PyMSS integration | 人声分离代码路径 | MIT | https://github.com/nomadkaraoke/python-audio-separator |
| `vocals-bs-roformer-368` | 可选人声分离权重 | `LicenseRef-Unknown`，不随 VoxWeave 分发，默认不下载 | https://huggingface.co/baicai1145/pymss |

用户导入的 `.pth/.index` 保持模型作者的独立许可。VoxWeave 只在本机记录其路径、来源、许可证声明和哈希；当前开发机上的私人模型不属于本项目发布物。
