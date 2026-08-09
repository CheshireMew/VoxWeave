# 架构与数据边界

VoxWeave 的唯一业务入口是本机后台服务。QML 桌面端、`voxweave` CLI 和第三方自动化都只提交 `voxweave-control v1` 请求，不直接扫描模型、写任务库或调用 RVC。

```text
QML desktop ─┐
CLI/AI ──────┼─ authenticated loopback API ─ Controller ─ SQLite
HTTP client ─┘                                  │
                                  serialized task worker
                                                │
                     FFmpeg / analysis / official RVC adapter
                                                │
                              validated media + provenance
```

## 服务发现

服务只监听 `127.0.0.1` 的随机端口。独占进程锁成功后才写发现文件，内容包括 PID、端口、协议、版本、启动时间和随机令牌。客户端先验证 PID 存活，再用令牌请求 `/v1/handshake`；任一步失败都不会沿用该发现文件。令牌随服务进程更换。

## 持久状态

数据目录下的 SQLite 是模型、预设、任务、事件、批量规则和监控指纹的唯一真源。运行中的 GPU 推理只有一个工作线程；每次 RVC 与分离推理由独立子进程执行，因此取消或服务退出可以终止正在运行的计算。分析、哈希和媒体封装可在业务任务内部受控执行，但不会建立第二个任务系统。

任务重启语义是保守的：服务启动时把先前的非终态任务标记为 `interrupted`，不会根据零散临时文件猜测完成度。用户可以显式重试；已完成产物的清单带最终哈希。批量监控用输入规范路径、大小、修改时间和 SHA-256 去重，输出根永远被排除在输入枚举之外。

## 推理边界

VoxWeave 锁定 RVC 上游提交 `4338f12c3c28c80b3ac015e2d0df66c41592746d`。`rvc_worker.py` 是 VoxWeave 自己维护的窄适配器，只调用该提交的官方配置与 VC 模块。模型检查在 RVC Python 子进程中显式执行 `torch.load(..., weights_only=True)`；不安全或结构不兼容的权重不会进入 ready 状态。

媒体唯一生产链是：FFprobe 检查、FFmpeg 提取、按内容模式分析、可选人声分离、可选说话人选择、RVC、采样长度对齐与边界淡化、响度匹配、封装、完整解码。超过 90 秒的整轨会在低能量位置切成约 45 秒的块，由同一个已加载模型的 RVC 子进程批量处理，再按原采样位置重组；这避免 RMVPE 对超长输入的 cuDNN 限制，也避免每块重复加载权重。视频映射并复制所有原始流，再添加命名后的变声音轨；不会用重编码后的视频冒充“复制原流”。

## 跨平台边界

路径使用 `pathlib`，服务和数据库没有 Windows 专用格式；启动脚本同时提供 PowerShell 和 POSIX 版本。进程存活、可执行文件位置和 Qt 部署由平台适配层处理。0.1 只把 Windows CUDA 写为已验收，未获得对应机器前不宣称 macOS/Linux 可用。
