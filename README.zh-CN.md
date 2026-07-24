# agent-bridge

[English](README.md) | **简体中文**

agent-bridge 让 Codex、Claude Code、Reasonix 和 ZCode 使用同一个本地任务看板。
它只依赖 Python 标准库，所有数据都保存在 `~/.agent-bridge`。

## 环境要求

- Windows 10/11 + PowerShell 5.1+，或 macOS/Linux + Bash
- Python 3.9+
- 至少安装一种受支持的 agent 应用

## 安装

安装脚本只负责安全地安装包并调用 `bridge setup`；Python 生命周期负责受管
运行时、启动器、配置、代理档案和 Windows 原生通知助手。`--auto` 只配置
检测到的主机，`--agent` 可显式创建该主机的受管配置。`bridge uninstall`
默认保留数据，只有 `--purge-data` 才删除显示出的精确数据目录。

Windows PowerShell：

```powershell
.\install.ps1 -Auto
.\install.ps1 -Agent codex -As codex -Python C:\path\to\python.exe
.\install.ps1 -Auto -Uninstall
```

macOS 或 Linux：

```bash
./install.sh --auto
./install.sh --agent codex --as codex --python /usr/bin/python3
./install.sh --auto --uninstall
```

安装器可以安全重复运行，会复制完整且唯一的脚本目录，注册能力和唤醒命令，
配置应用集成，最后执行 `bridge doctor --strict`。安装后请重启各应用，使 MCP
和钩子配置生效。

仓库已包含 macOS 安装器及平台无关测试；正式发布前仍应在真实 macOS 主机上
执行文末验收命令。

## 任务闭环

```text
send -> pending -> claim -> working -> done -> completed
                         -> question -> input_required
input_required -> answer -> pending
working -> request review -> review_requested
review_requested -> approve -> completed
review_requested -> changes -> changes_requested -> claim -> working
```

只有任务接收者可以认领、提问、请求审查和完成任务；只有原始发送者可以回答
问题或给出审查结论。

```bash
bridge send --to reasonix --subject "审查补丁" --body "请运行全部测试"
bridge inbox
bridge show <task-id>
bridge claim <task-id>
bridge question <task-id> --body "兼容哪个版本？"
bridge answer <task-id> --body "Python 3.9+"
bridge review <task-id>
bridge review <task-id> --verdict approve --body "验收通过"
bridge done <task-id> --result "已实现并测试"
```

## 送达状态

每次通知尝试都记录在 `task.delivery.status`：

- `queued`：任务已保存，等待尝试送达。
- `dispatching`：分发器当前正在处理此次尝试。
- `os_posted` / `plugin_delivered`：送达通道已接受通知，但任务尚未被确认。
- `viewed`：目标已打开送达界面。
- `launch_started`：已启动唤醒进程，但不表示对方已经读到。
- `agent_acknowledged`：目标调用了 `status` 或 `inbox`。

兼容术语：`wake_launched` 表示 `launch_started`，`acknowledged` 表示
`agent_acknowledged`，`unavailable` 表示降级能力而非投递成功。
- `claimed`：受理人已认领任务。
- `retry_wait`：失败尝试已安排重试。
- `failed`：送达尝试本身失败。

`bridge send` 不会把“进程已启动”误报成“已确认”。存在 agent 档案时，拼错或
未注册的目标会被拒绝。Windows 通知使用系统托盘 API，不依赖 BurntToast。

## 常用命令

```text
bridge status [--oneliner]       bridge inbox
bridge send --to NAME --subject TEXT [--body TEXT]
bridge claim ID                  bridge done ID --result TEXT
bridge show ID                   bridge board
bridge question ID --body TEXT   bridge answer ID --body TEXT
bridge review ID [--verdict approve|changes] [--body TEXT]
bridge agents                    bridge activity
bridge project init|list|show    bridge context --show|--add TEXT
bridge clean --days N|--all      bridge doctor [--strict]
```

MCP 服务暴露相同的非交互工作流。

## 排查

```bash
bridge doctor --strict
bridge status --oneliner
bridge agents
```

任务停在 `launch_started` 表示应用已被启动，但尚未检查收件箱。请重启目标应用、
检查钩子或 MCP 配置，再运行 `bridge inbox`。配置始终使用当前系统的原生路径，
Windows 不再依赖 `/c/...` 路径。

## 测试

在仓库根目录运行：

```bash
python -m unittest discover -s tests -v
python -m compileall -q scripts tests
```

Windows 测试包含隔离安装/重复安装/卸载、无外部依赖系统通知、GBK 输出、MCP、
完整状态流和 40 进程并发写入。macOS 还应执行一次使用临时目录的
`./install.sh --auto --install-root <temp-dir>` 安装冒烟测试。
