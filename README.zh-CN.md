# agent-bridge

[English](README.md) | **简体中文**

Agent Bridge 为 Codex、Claude Code、Reasonix 和 ZCode 提供同一份本地任务看板。
它只使用 Python 标准库，数据保存在 `~/.agent-bridge`；没有常驻守护进程、网络监听器、
云同步或默认遥测。

[许可证](LICENSE)（Apache-2.0） · [安全报告](SECURITY.md) · [贡献指南](CONTRIBUTING.md) ·
[架构](docs/architecture/v2.md) · [Windows 安装](docs/installation/windows.md) ·
[macOS 安装](docs/installation/macos.md) · [v1 迁移](docs/installation/migration-v1.md) ·
[发布清单](docs/release/checklist.md)

## 环境与能力边界

- Python 3.9+（发布 CI 覆盖 Python 3.9–3.13）
- Windows 10/11 + PowerShell 5.1+，或 macOS/Linux + Bash
- 数据库必须位于本地文件系统；网络共享和同步盘不受支持

四个主机集成都以版本化 session-card 模板随包发布。`bridge setup status` 会报告
Codex、Claude Code、Reasonix、ZCode 的实际能力；主机不可用时应使用终端回退。
Windows 有源码与 CI 检查。macOS 的源码/CI 检查不能证明通知权限、动作、签名、
Gatekeeper、公证或 Intel/Apple Silicon 行为；这些都需要 real-machine 发布验收。

## 安装

Windows PowerShell：

```powershell
.\install.ps1 -Auto
.\install.ps1 -Agent codex -As codex -Python C:\path\to\python.exe
bridge setup status
```

macOS 或 Linux：

```bash
./install.sh --auto
./install.sh --agent codex --as codex --python /usr/bin/python3
bridge setup status
```

安装器只配置检测到的主机，且只写入带收据的启动器、PATH 条目和受管配置。重启主机后，
运行 `bridge doctor --strict`。

## 任务流程

```text
send -> pending -> claim -> working -> done -> completed
                         -> question -> input_required
input_required -> answer -> pending
working -> request review -> review_requested
review_requested -> approve -> completed
review_requested -> changes -> changes_requested -> claim -> working
```

```bash
bridge send --to reasonix --subject "审查补丁" --body "运行测试"
bridge inbox
bridge claim <task-id>
bridge question <task-id> --body "兼容目标是什么？"
bridge answer <task-id> --body "Python 3.9+"
bridge review <task-id>
bridge review <task-id> --verdict approve --body "已接受"
bridge done <task-id> --result "已实现并测试"
```

## 诚实的投递状态

每次投递都会记录：`queued`、`dispatching`、`os_posted`、`plugin_delivered`、`viewed`、
`launch_started`、`agent_acknowledged`、`claimed`、`retry_wait`、`failed`。

`os_posted`、`plugin_delivered` 和 `launch_started` 不是确认；目标使用 `status` 或
`inbox` 后才算 `agent_acknowledged`，认领后为 `claimed`。通知失败不会删除已保存的任务。

## 维护、迁移与卸载

```bash
bridge --version
bridge --help
bridge doctor --strict
bridge setup --repair
bridge setup --dry-run
bridge tui
bridge migrate path/to/v1-board.json
bridge export backup.json
bridge uninstall
bridge uninstall --purge-data
```

`bridge uninstall` 默认 preserves task data；只有明确传入 `--purge-data`，且先显示精确
数据目录时，才会删除数据。恢复时运行 `bridge setup status`、`bridge doctor` 和
`bridge inbox`；本项目没有需要重启的守护进程。

## 测试与发布

```bash
python -m unittest discover -s tests -v
python -m compileall -q src scripts tests
python -m build
```

发布流程会生成 wheel、sdist、portable archive、SHA-256 校验和 SPDX SBOM。原生助手、
签名、公证与在干净环境安装的验收命令见[发布清单](docs/release/checklist.md)。
