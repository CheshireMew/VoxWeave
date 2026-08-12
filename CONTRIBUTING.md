# Contributing to VoxWeave

感谢你帮助改进 VoxWeave。项目当前只在 Windows 11、NVIDIA CUDA 上验收；涉及运行、界面或推理的改动，应至少说明在这个平台上验证到了哪一层。

Thank you for improving VoxWeave. The project currently validates Windows 11 with NVIDIA CUDA only. Changes to runtime behavior, the UI, or inference should state exactly what was verified on that platform.

## 开始之前 / Before you start

- 先搜索或创建 [Issue](https://github.com/CheshireMew/VoxWeave/issues)，说明用户要完成的结果、当前行为和期望行为。
- 不要提交声音模型、私人媒体、数据目录、`.voxweave.local.json`、运行时环境或任务产物。
- 不要让 QML、CLI 或自动化调用绕过本机服务，直接修改数据库或调用 RVC。
- 新增公开操作时，同步协议 schema、路由、测试和相关文档。
- 保持默认不覆盖输出、模型与输入身份校验、显式归档等现有边界。

- Search or open an [Issue](https://github.com/CheshireMew/VoxWeave/issues) and describe the user result, current behavior, and expected behavior.
- Do not commit voice models, private media, the data directory, `.voxweave.local.json`, runtime environments, or task artifacts.
- Keep QML, CLI, and automation clients behind the local service boundary; they must not write the database or invoke RVC directly.
- When adding a public operation, update its protocol schema, routing, tests, and relevant documentation together.
- Preserve the no-overwrite default, input/model identity checks, and explicit archive boundary.

## 本地环境 / Local environment

按 [README](README.md) 运行 `scripts\bootstrap.ps1`，并把数据目录放在源码目录之外。随后使用该数据目录中的 Python：

Run `scripts\bootstrap.ps1` as documented in the [README](README.en.md), with a data directory outside the source checkout. Use the Python environment created under that data directory:

```powershell
D:\Tools\VoxWeave\.venv\Scripts\python.exe -m ruff check .
D:\Tools\VoxWeave\.venv\Scripts\python.exe -m pytest
```

QML 改动还应通过仓库 CI 使用的 `qmllint` 入口。真实 CUDA、音频设备或媒体链无法在测试中覆盖时，请在 Pull Request 中明确未验证的部分，不要用 mock 结果代替实机结论。

QML changes must also pass the `qmllint` entry used by repository CI. If a real CUDA, audio-device, or media path cannot be exercised, state the unverified layer in the pull request instead of presenting mocked behavior as a physical-machine result.

## Pull Request

Pull Request 应只包含一个连贯结果，并说明：

1. 用户现在能完成什么，或哪个错误被修复。
2. 变更涉及哪些生产者、持久边界和消费者。
3. 实际运行了哪些检查，以及哪些环境相关验证没有运行。
4. 是否改变协议、schema、数据库迁移、许可证、模型政策或公开文档。

A pull request should contain one coherent result and explain:

1. What the user can now complete, or which failure is fixed.
2. Which producers, persistence boundaries, and consumers changed.
3. Which checks actually ran and which environment-dependent checks did not.
4. Whether the protocol, schemas, database migrations, licenses, model policy, or public documentation changed.

请不要为贡献流程生成安装器、压缩包或整合运行时。VoxWeave 源码使用 [AGPL-3.0-only](LICENSE)；提交代码即表示你有权按该许可证提供这些改动。第三方内容必须保留原始许可和来源说明。

Do not build an installer, archive, or bundled runtime for the contribution workflow. VoxWeave source is [AGPL-3.0-only](LICENSE); by contributing, you confirm that you may provide the change under that license. Preserve the original license and source notice for third-party material.
