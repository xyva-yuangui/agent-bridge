# ZCode Acceptance: agent-bridge 1.3.0

Date: 2026-07-23

## Review scope

- Source branch: `fix/cross-platform-reliability`
- Base: `0f9662e`
- Candidate code: `0743613`
- Review range: `0f9662e..HEAD` (includes this acceptance report)
- Review checkout:
  `C:\tmp\roundtable\.worktrees\cross-platform-fixes`
- Canonical repository after integration: `C:\tmp\roundtable`

## Requirements addressed

- Durable board, activity, archive, cleanup, and Windows portable locking.
- Central task lifecycle with ownership checks for question/answer and review.
- Observable delivery: `queued`, `wake_launched`, `acknowledged`,
  `unavailable`, and `failed`.
- No false acknowledgment when a wake process merely starts.
- Dependency-free Windows system notifications with separate safe argv.
- `wake_argv` arrays preserve executable paths containing spaces.
- Registered-target validation, recent-agent routing, and coordinator renewal.
- GBK-safe CLI output with ASCII status prefixes.
- Complete 20-tool MCP surface at version 1.3.0.
- Idempotent Windows and POSIX installers for Codex, Claude, Reasonix, and
  ZCode, including install/reinstall/uninstall behavior.
- Native Windows paths in Codex and Reasonix MCP configuration.
- ZCode local plugin and `UserPromptSubmit` hook.
- Documentation aligned with the implemented lifecycle and delivery semantics.

## Windows evidence

Run from the review checkout listed above:

```powershell
python -m unittest discover -s tests -v
python -m compileall -q scripts tests
.\install.ps1 -Auto -Python C:\path\to\python.exe
bridge doctor --strict
```

Observed on this host:

- 28 tests passed, 0 failures.
- `compileall` exited 0.
- Isolated install, reinstall, and uninstall passed.
- Dependency-free notification helper exited 0.
- 40 concurrent send processes preserved every task and valid JSON.
- GBK subprocess output completed without `UnicodeEncodeError`.
- MCP initialize, complete tool list, and `bridge_whoami` call passed.
- Installed `bridge.py`, `bridge_mcp.py`, and `notify_windows.ps1` hashes
  exactly match the candidate source.
- Codex TOML, Reasonix TOML, Claude JSON, ZCode config JSON, and ZCode hook JSON
  parse successfully.
- Live `bridge doctor --strict` exits 0. The historical `test` profile has a
  stale heartbeat and is reported as an operational warning only.

## macOS acceptance

The POSIX installer contract and platform-neutral Python behaviors pass on
Windows. Before a macOS release claim, run on a real macOS host:

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q scripts tests
tmp_home="$(mktemp -d)"
./install.sh --auto --python "$(command -v python3)" --install-root "$tmp_home"
AGENT_BRIDGE_HOME="$tmp_home/.agent-bridge" \
AGENT_BRIDGE_CONFIG_HOME="$tmp_home" \
  "$tmp_home/.local/bin/bridge" --as codex doctor --strict
```

Also verify a native macOS notification, one MCP client restart, and one
send/status/claim/done exchange between two installed applications.

## ZCode checklist

- [ ] Inspect `0f9662e..HEAD`.
- [ ] Run the full test and compile commands.
- [ ] Confirm ZCode loads `agent-bridge@local` version 1.3.0.
- [ ] Confirm a task becomes `acknowledged` only after ZCode checks in.
- [ ] Confirm no existing ZCode enabled-plugin entries are removed.
- [ ] Record `approve` or `changes` through agent-bridge review.
