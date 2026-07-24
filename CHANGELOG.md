# Changelog

All notable changes to agent-bridge will be documented in this file.

## [1.3.0] — 2026-07-23

### Added
- Cross-platform portable file locking with stale lock detection and recovery
- Delivery tracking: `queued` → `wake_launched` → `acknowledged` → `unavailable` → `failed`
- `install.ps1` Windows PowerShell installer with auto-detection and uninstall
- `notify_windows.ps1` dependency-free system notification helper
- 29-test suite across 6 test files (lifecycle, concurrency, e2e, MCP, installers, CLI)
- 20-tool MCP surface (up from 14)
- `wake_argv` array support for paths with spaces
- GBK-safe CLI output with `_configure_stdio`
- `bridge whoami`, `bridge wake`, `bridge who-coordinates`, `bridge log` commands
- Continuous collaboration and Coordinator mode sections in SKILL.md
- Windows Path handling: case-insensitive `_under`, `creationflags` for subprocess

### Changed
- `_wake_agent`: passes `AGENT_BRIDGE_NAME` to child process, cross-platform `start_new_session`/`creationflags`
- Enhanced wake prompt: "Claim ALL pending tasks, keep going until inbox empty"
- `_desktop_notify`: Windows `msg.exe` fallback, macOS/Linux paths preserved
- `install.sh`: Claude wake registration (`claude -p`), `--install-root` support
- All file operations use `_locked_file` context manager instead of raw `fcntl`

### Fixed
- `_maybe_rotate` call preserved in `append_activity` (was accidentally removed)
- Coordinator heartbeat refresh on `cmd_send`
- Target validation when no agent profiles exist

## [1.2.0] — 2026-07-23

### Added
- Cross-platform `_locked_file` context manager (fcntl + portable fallback)
- Claude Code wake registration in install.sh
- Continuous collaboration and Coordinator mode in SKILL.md
- `--no-wake` flag on `bridge send`

### Changed
- `_wake_agent`: enhanced prompt, `AGENT_BRIDGE_NAME` env passthrough
- `_under`: case-insensitive path comparison for Windows
- `_desktop_notify`: Windows PowerShell toast notification

## [1.1.0] — 2026-07-23

### Added
- Auto-cleanup: silent clean of 7d+ old tasks, stale detection (>24h), overflow archive
- `bridge clean` command with `--days`, `--all`, `--dry-run`, `--status`
- 30-day inbox filter
- `bridge_clean` MCP tool

## [1.0.0] — 2026-06-29

### Initial release
- Shared task board with JSON file storage
- CLI (`bridge`) and MCP server (`bridge_mcp`)
- Support for Codex, Claude Code, Reasonix, ZCode
- Task lifecycle: send → claim → done, with question/answer and review
- Coordinator model with capability-based routing
- Auto-push (send → wake) and desktop notifications
- install.sh POSIX installer
