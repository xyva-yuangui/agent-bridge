# Agent Bridge

[English](README.md) | **简体中文**

Agent Bridge 为 Codex、Claude Code、Reasonix 和 ZCode 提供同一份本地任务看板。它只使用 Python 标准库，数据保存在 `~/.agent-bridge`；没有常驻守护进程、网络监听器、云同步或默认遥测。

[许可证](LICENSE)（Apache-2.0） · [安全报告](SECURITY.md) · [贡献指南](CONTRIBUTING.md) · [架构](docs/architecture/v2.md) · [Windows 安装](docs/installation/windows.md) · [macOS 安装](docs/installation/macos.md) · [v1 迁移](docs/installation/migration-v1.md) · [发布清单](docs/release/checklist.md)

## 环境与支持范围

- Python 3.9+（发布 CI 覆盖 Python 3.9–3.13）。
- Windows 10/11 + PowerShell 5.1+，或 macOS/Linux + Bash。
- 数据库必须放在本地文件系统；不支持网络共享或同步盘。

四个主机集成的版本化 session-card 模板随包发布。`bridge setup status` 会报告 Codex、Claude Code、Reasonix 和 ZCode 的实际能力；主机不可用时应使用终端回退。

Windows 已有源码和 CI 检查。macOS 的源码 CI 不能证明通知权限、动作、签名、Gatekeeper、公证或 Intel/Apple Silicon 行为；这些都必须通过 real-machine 发布验收。

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

安装器只配置检测到的主机，并只写入带收据的启动器、PATH 条目和受管配置。正常安装必须能从发行包安装；仅本地开发可显式使用降级 source fallback（`-DevSourceFallback` 或 `--dev-source-fallback`），它会设置 `PYTHONPATH` 并显示 DEGRADED 提示。重启主机后运行 `bridge doctor --strict`。

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

`bridge uninstall` 默认 preserves task data；只有显式传入 `--purge-data` 且先显示精确数据目录时才删除数据。恢复时运行 `bridge setup status`、`bridge doctor` 和 `bridge inbox`；本项目没有需要重启的守护进程。

## 测试与发布

```bash
python -m unittest discover -s tests -v
python -m compileall -q src scripts tests
python -m build
```

发布流程生成 Windows/macOS 平台 wheel、sdist、portable archive、SHA-256 校验、SPDX 与 CycloneDX SBOM。签名、公证与干净环境安装的 real-machine 验收命令见[发布清单](docs/release/checklist.md)。
