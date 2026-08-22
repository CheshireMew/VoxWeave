# Windows 发布流程

VoxWeave 的正式二进制发布物是免安装的 Windows x64 ZIP，不是安装器。ZIP 包含桌面应用本身、CPython 3.12、PySide6/Qt 和应用 Python 依赖；RVC、托管 RVC Python 环境、FFmpeg、推理权重、声音模型、用户数据和开发文件都不进入发布包。

## 构建前提

发布只能从一个已提交且工作区完全干净的提交开始。构建 Python 必须是 3.12，并安装 `requirements-build.lock`。输出根目录必须位于仓库和系统盘之外；当前开发机使用 D 盘。发布脚本读取 `pyproject.toml` 的版本、完整 Git 提交、提交时间和 `origin` 地址，任何一项无法确定都会停止。

```powershell
.\scripts\build-exe.ps1 `
  -Python D:\Tools\VoxWeave\.venv\Scripts\python.exe `
  -OutputRoot D:\Tools\VoxWeave\release-builds
```

输出目录名为 `VoxWeave-<version>-<commit前12位>`。同一版本和提交只允许存在一个目录，脚本不会覆盖、删除或用时间戳制造重复构建。需要重建时，先把原目录完整迁到 D 盘归档位置并保留原因；失败构建也保留在原目录供诊断。

## 必须通过的发布闸门

构建会按顺序完成以下检查，任何一步失败都不会报告成功：

1. PyInstaller 在独立工作目录创建新的 onedir 应用，不读取仓库里的旧 `build`、`dist` 或 `.archive`。
2. `packaging/runtime-components.json` 中每个组件的安装版本必须精确匹配，并把该发行包实际安装的全部 LICENSE、COPYING 和 NOTICE 文件复制进 `licenses/python/`。PyInstaller bootloader 与运行钩子按带 Bootloader Exception 的 GPL-2.0 一并登记；其余构建依赖由 `requirements-build.lock` 完整锁定但不冒充运行组件。
3. 发布目录必须带有 VoxWeave、CPython、GPL-3.0、LGPL-3.0 许可证，以及 Qt/PySide 对应源码和动态库替换说明。
4. `release-manifest.json` 记录源码提交、版本、目标平台、组件、每个文件的大小与 SHA-256。目录中不能夹带 `.pth`、`.index`、模型目录、测试目录或仓库元数据。
5. ZIP 按固定顺序和提交时间写入，生成独立 `.sha256`；随后解压到新目录，按清单逐文件复算并确认 `VoxWeave.exe`、`python312.dll`、`Qt6Core.dll` 存在。
6. 从解压目录启动 `VoxWeave.exe --voxweave-release-smoke`。程序使用离屏 Qt 平台，实际加载 PySide6、Qt 插件、完整 `Main.qml` 和应用 Bridge，不启动服务、不下载组件，30 秒内写出 `smoke-report.json` 并退出。

构建目录中的 `artifacts` 保存 ZIP、SHA-256、发布清单和发布摘要；`extracted-verification` 是实际复验的解压目录；`smoke-report.json` 是启动证据。

## 发布与保留

在 GitHub Release 上同时上传 ZIP、`.sha256`、`release-manifest.json` 和 `release-summary.json`，并在二进制下载旁保持 Qt 6.11.1 与 PySide6/Shiboken 6.11.1 对应源码入口。源码链接和用户替换动态库的方法已写入包内 `QT_PYSIDE_COMPLIANCE.md`。发布前还应人工确认 `smoke-report.json` 的 `ok` 为 `true`，版本、提交和 Release 标签一致。

构建工具不自动删除历史记录。需要释放空间时，只能把完整构建目录迁到仓库外的归档盘，或在得到明确删除授权后处理；仓库根目录不再承担发布产物存储。
