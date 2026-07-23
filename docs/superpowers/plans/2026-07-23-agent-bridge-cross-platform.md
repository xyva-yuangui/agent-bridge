# agent-bridge Cross-Platform Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver one canonical agent-bridge implementation that installs and works reliably on Windows and macOS, preserves task state under concurrency, exposes observable delivery, and integrates with Codex, Claude Code, Reasonix, and ZCode.

**Architecture:** Keep the local JSON board and standard-library Python CLI, but centralize locking, state transitions, delivery tracking, notifications, and application adapters behind testable functions. Ship complete Windows PowerShell and POSIX Bash installers that deploy one canonical scripts directory and configure detected applications with native paths.

**Tech Stack:** Python 3.9+ standard library, PowerShell 5.1+, Bash 3.2+, JSON/TOML configuration, `unittest`.

## Global Constraints

- Python 3.9 or newer; no third-party runtime or test dependencies.
- All state remains local under `~/.agent-bridge`.
- Windows and macOS are first-class platforms.
- CLI and board compatibility are preserved where safe.
- Process launch is not delivery acknowledgment.
- Every behavior change follows red-green-refactor.
- No network daemon or cloud service.

---

## File Structure

- `scripts/bridge.py`: CLI, storage, state machine, delivery, routing, notification dispatch.
- `scripts/bridge_mcp.py`: complete JSON-RPC/MCP adapter over the CLI.
- `scripts/notify_windows.ps1`: dependency-free Windows notification helper.
- `install.ps1`: native Windows installation and four-app configuration.
- `install.sh`: macOS/Linux installation and four-app configuration.
- `tests/test_bridge.py`: storage, lifecycle, routing, delivery, cleanup unit tests.
- `tests/test_cli.py`: subprocess and encoding behavior.
- `tests/test_concurrency.py`: multi-process board and activity stress.
- `tests/test_mcp.py`: MCP protocol and CLI parity.
- `tests/test_installers.py`: installer contract and notification helper tests.
- `tests/test_e2e.py`: isolated send/ack/claim/question/answer/review/done workflow.

### Task 1: Restore the canonical baseline and test harness

**Files:**
- Create: `scripts/bridge.py`
- Create: `scripts/bridge_mcp.py`
- Create: `tests/__init__.py`
- Create: `tests/support.py`

**Interfaces:**
- Consumes: installed v1.2 implementation from `~/.agent-bridge/skill/scripts/`.
- Produces: importable `bridge` module and `run_bridge()` subprocess helper.

- [ ] **Step 1: Restore the existing implementation without behavior changes**

Copy the installed v1.2 `bridge.py` and `bridge_mcp.py` into `scripts/`.
This establishes the reviewed baseline; it is not a new behavior.

- [ ] **Step 2: Add the shared test loader**

```python
ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"

def load_bridge():
    spec = importlib.util.spec_from_file_location("bridge_under_test", SCRIPTS / "bridge.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def run_bridge(home, *args, encoding="utf-8"):
    env = os.environ.copy()
    env["AGENT_BRIDGE_HOME"] = str(home)
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "bridge.py"), *args],
        capture_output=True, text=True, encoding=encoding, env=env,
    )
```

- [ ] **Step 3: Run baseline discovery**

Run: `python -m unittest discover -s tests -v`

Expected: zero discovered behavior tests and exit 0.

- [ ] **Step 4: Commit**

```bash
git add scripts tests
git commit -m "chore: restore canonical agent bridge source"
```

### Task 2: Make storage, archive, cleanup, and stale locks reliable

**Files:**
- Modify: `scripts/bridge.py`
- Create: `tests/test_bridge.py`
- Create: `tests/test_concurrency.py`

**Interfaces:**
- Produces: `_locked_file(path, mode)`, `_append_archive(project_id, tasks)`,
  `append_activity(project_id, entry)`, `_auto_stale_working(project_id, me)`.

- [ ] **Step 1: Write failing storage tests**

```python
def test_stale_working_task_is_failed(self):
    self.write_task(status="working", updated="2000-01-01T00:00:00Z")
    self.bridge._auto_stale_working("default", "codex")
    self.assertEqual(self.read_task()["status"], "failed")

def test_append_activity_rotates_and_keeps_valid_json_lines(self):
    self.bridge.MAX_ACTIVITY_ENTRIES = 4
    for index in range(7):
        self.bridge.append_activity("default", {"agent": "a", "action": "x", "n": index})
    lines = self.activity_path.read_text(encoding="utf-8").splitlines()
    self.assertLessEqual(len(lines), 4)
    for line in lines:
        json.loads(line)

def test_auto_clean_archives_removed_tasks(self):
    self.write_ten_tasks_with_one_old_completed()
    self.bridge._auto_clean("default", "codex")
    self.assertNotIn(self.old_id, self.board_ids())
    self.assertIn(self.old_id, self.archive_ids())

def test_clean_requires_explicit_scope(self):
    result = run_bridge(self.home, "--as", "codex", "clean")
    self.assertNotEqual(result.returncode, 0)
    self.assertIn("--all or --days", result.stderr)
```

- [ ] **Step 2: Verify red**

Run: `python -m unittest tests.test_bridge.StorageTests -v`

Expected: failures for stale detection, missing activity rotation, missing auto-clean archive, and unsafe default clean.

- [ ] **Step 3: Implement minimal storage fixes**

Implement:

```python
def _auto_stale_working(project_id, me):
    stale_ids = []
    atomic_update_board(board_path(project_id), _detect)
    for task_id in stale_ids:
        append_activity(project_id, {
            "agent": me,
            "action": "auto-failed",
            "task_id": task_id,
            "subject": f"stale working task (> {STALE_WORKING_HOURS}h)",
        })

def append_activity(project_id, entry):
    with _locked_file(str(activity_path(project_id)), "a") as stream:
        stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
    _maybe_rotate(activity_path(project_id))

def cmd_clean(args):
    if not args.clean_all and args.days is None:
        raise SystemExit("error: specify --all or --days N")
```

Add `_append_archive` and call it after `_auto_clean` removes tasks.
Portable lock files store PID/timestamp and remove only provably stale owners.

- [ ] **Step 4: Verify green**

Run: `python -m unittest tests.test_bridge.StorageTests -v`

Expected: all storage tests pass.

- [ ] **Step 5: Add and run concurrent stress**

Spawn 40 CLI processes sending to a registered target and assert:

```python
self.assertTrue(all(result.returncode == 0 for result in results))
self.assertEqual(len(board["tasks"]), 41)
```

Run: `python -m unittest tests.test_concurrency -v`

Expected: all concurrent processes succeed; board and activity JSON remain valid.

- [ ] **Step 6: Commit**

```bash
git add scripts/bridge.py tests/test_bridge.py tests/test_concurrency.py
git commit -m "fix: make bridge storage durable under concurrency"
```

### Task 3: Centralize the task state machine and delivery acknowledgment

**Files:**
- Modify: `scripts/bridge.py`
- Modify: `tests/test_bridge.py`
- Create: `tests/test_e2e.py`

**Interfaces:**
- Produces: `_transition_task(task: dict, actor: str, action: str) -> dict`,
  `_attempt_delivery(project_id: str, task_id: str, target: str, subject: str) -> dict`,
  `_ack_pending_tasks(project_id: str, agent: str) -> int`.

- [ ] **Step 1: Write failing lifecycle tests**

```python
def test_answer_returns_task_to_assignee_inbox(self):
    task_id = self.create_claim_and_question()
    self.run_as("alice", "answer", task_id, "--body", "answer")
    result = self.run_as("bob", "inbox")
    self.assertIn(task_id, result.stdout)
    self.assertEqual(self.task(task_id)["status"], "pending")

def test_review_approve_completes_task(self):
    task_id = self.create_claim_and_review_request()
    self.run_as("alice", "review", task_id, "--verdict", "approve")
    self.assertEqual(self.task(task_id)["status"], "completed")

def test_non_sender_cannot_review(self):
    result = self.run_as("mallory", "review", self.task_id, "--verdict", "approve")
    self.assertNotEqual(result.returncode, 0)

def test_status_acknowledges_pending_delivery(self):
    task_id = self.send_task()
    self.run_as("bob", "status", "--oneliner")
    self.assertEqual(self.task(task_id)["delivery"]["status"], "acknowledged")
```

- [ ] **Step 2: Verify red**

Run: `python -m unittest tests.test_bridge.LifecycleTests tests.test_e2e -v`

Expected: answer remains `working`, review remains `review_approved`, unauthorized review succeeds, and delivery acknowledgment is absent.

- [ ] **Step 3: Implement transitions and delivery**

Use a central action table:

```python
TRANSITIONS = {
    "claim": ({"pending", "changes_requested"}, "working", "assignee"),
    "question": ({"working"}, "input_required", "assignee"),
    "answer": ({"input_required"}, "pending", "sender"),
    "request_review": ({"working"}, "review_requested", "assignee"),
    "approve": ({"review_requested"}, "completed", "sender"),
    "changes": ({"review_requested"}, "changes_requested", "sender"),
    "done": ({"working", "pending", "changes_requested"}, "completed", "assignee"),
}
```

`_attempt_delivery` records `queued`, notification result, wake launch result, and detail. `_ack_pending_tasks` marks actionable target tasks acknowledged during `status`, `inbox`, and `claim`.

- [ ] **Step 4: Verify green**

Run: `python -m unittest tests.test_bridge.LifecycleTests tests.test_e2e -v`

Expected: all lifecycle and end-to-end tests pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/bridge.py tests/test_bridge.py tests/test_e2e.py
git commit -m "fix: close task lifecycle and delivery loops"
```

### Task 4: Fix wake argv, notifications, routing, coordinator leases, and encoding

**Files:**
- Modify: `scripts/bridge.py`
- Create: `scripts/notify_windows.ps1`
- Modify: `tests/test_bridge.py`
- Create: `tests/test_cli.py`
- Create: `tests/test_installers.py`

**Interfaces:**
- Produces: `_load_wake_argv(profile)`, `_desktop_notify(title, message) -> NotificationResult`,
  `route_task(skill, exclude="")`.

- [ ] **Step 1: Write failing platform tests**

```python
def test_wake_argv_preserves_path_with_spaces(self):
    profile = {"wake_argv": [r"C:\Program Files\Agent\agent.exe", "run"]}
    self.assertEqual(self.bridge._load_wake_argv(profile)[0], profile["wake_argv"][0])

def test_windows_notification_uses_file_and_separate_arguments(self):
    with mock.patch.object(self.bridge.sys, "platform", "win32"), \
         mock.patch.object(self.bridge.subprocess, "run") as run:
        run.return_value.returncode = 0
        result = self.bridge._desktop_notify("O'Brien", "x'; exit 9; '")
    argv = run.call_args.args[0]
    self.assertIn("-File", argv)
    self.assertIn("O'Brien", argv)
    self.assertTrue(result.ok)

def test_cli_does_not_crash_with_gbk_output(self):
    result = run_bridge(self.home, "--as", "codex", "status", "--oneliner",
                        encoding="gbk")
    self.assertEqual(result.returncode, 0)
    self.assertNotIn("UnicodeEncodeError", result.stderr)
```

- [ ] **Step 2: Verify red**

Run: `python -m unittest tests.test_bridge.PlatformTests tests.test_cli -v`

Expected: legacy wake splitting, BurntToast command interpolation, and GBK status failures.

- [ ] **Step 3: Implement platform fixes**

- Store wake commands as `wake_argv`.
- Support legacy strings using `shlex.split`, never plain `.split()`.
- Use `notify_windows.ps1 -Title <title> -Message <message>` argv.
- Check notification return codes and surface failure.
- Configure stdout/stderr error handling and ASCII status prefixes.
- Reject unknown targets when profiles exist.
- Prefer recent capable agents in `route_task`.
- Replace coordinator if its profile is missing or heartbeat is stale.

- [ ] **Step 4: Verify green**

Run: `python -m unittest tests.test_bridge.PlatformTests tests.test_cli tests.test_installers.NotificationTests -v`

Expected: all platform tests pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/bridge.py scripts/notify_windows.ps1 tests
git commit -m "fix: make wake notification and cli behavior cross platform"
```

### Task 5: Bring MCP to CLI parity

**Files:**
- Modify: `scripts/bridge_mcp.py`
- Create: `tests/test_mcp.py`

**Interfaces:**
- Produces: MCP tools for every non-interactive CLI command.

- [ ] **Step 1: Write failing MCP tests**

Assert the tool list contains:

```python
REQUIRED = {
    "bridge_status", "bridge_inbox", "bridge_send", "bridge_claim",
    "bridge_done", "bridge_show", "bridge_board", "bridge_question",
    "bridge_answer", "bridge_review", "bridge_wake", "bridge_agents",
    "bridge_activity", "bridge_context", "bridge_clean", "bridge_doctor",
    "bridge_project", "bridge_whoami", "bridge_who_coordinates", "bridge_log",
}
```

Also call `bridge_whoami` and assert the response is UTF-8 JSON with identity.

- [ ] **Step 2: Verify red**

Run: `python -m unittest tests.test_mcp -v`

Expected: missing doctor/project/whoami/who-coordinates/log tools and version mismatch.

- [ ] **Step 3: Implement parity**

Add the missing tool specs, set `PROTOCOL_VERSION`, report the same
`BRIDGE_VERSION` as the CLI, and run child processes with:

```python
env = os.environ.copy()
env["PYTHONUTF8"] = "1"
subprocess.run(
    build_argv(spec, args, identity),
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="replace",
    timeout=40,
    env=env,
)
```

- [ ] **Step 4: Verify green**

Run: `python -m unittest tests.test_mcp -v`

Expected: initialization, complete tool list, and calls pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/bridge_mcp.py tests/test_mcp.py
git commit -m "feat: expose complete bridge cli through mcp"
```

### Task 6: Implement complete Windows and macOS installers

**Files:**
- Create: `install.ps1`
- Modify: `install.sh`
- Modify: `tests/test_installers.py`

**Interfaces:**
- Produces: `install.ps1 -Auto`, `install.sh --auto`, per-agent install,
  explicit Python and wake argv, uninstall, strict doctor.

- [ ] **Step 1: Write failing installer contract tests**

Tests assert:

- Both installers support auto, agent, identity, Python, wake, and uninstall.
- Both copy `bridge.py`, `bridge_mcp.py`, and the notification helper.
- Windows writes native paths and invokes the resolved Python.
- POSIX writes POSIX paths.
- Codex, Claude, Reasonix, and ZCode configuration functions exist.
- Neither installer references BurntToast.
- Both finish with strict doctor.

- [ ] **Step 2: Verify red**

Run: `python -m unittest tests.test_installers.InstallerContractTests -v`

Expected: missing PowerShell installer, missing auto/wake support, incomplete copy, and missing app adapters.

- [ ] **Step 3: Implement `install.ps1`**

Use PowerShell functions:

```powershell
Resolve-Python
Install-Shared
Register-AgentProfile
Configure-Codex
Configure-Claude
Configure-Reasonix
Configure-ZCode
Install-Agent
Uninstall-Agent
```

Write JSON through `ConvertTo-Json`, update text configs idempotently, and use
absolute Windows paths. Create `bridge.cmd` with the resolved Python path.

- [ ] **Step 4: Implement `install.sh`**

Parse `--auto`, `--agent`, `--as`, `--python`, `--wake-cmd`, and
`--uninstall`; copy the complete scripts directory; use Python snippets for
idempotent JSON/TOML updates; register wake argv arrays; run strict doctor.

- [ ] **Step 5: Verify green**

Run: `python -m unittest tests.test_installers -v`

Expected: all installer and notification helper contract tests pass.

- [ ] **Step 6: Run isolated Windows installation**

Run:

```powershell
.\install.ps1 -Auto -Python <absolute-tested-python> -InstallRoot <temporary-home>
```

Expected: launcher, canonical scripts, profiles, and configurations are
created; `bridge doctor --strict` exits 0.

- [ ] **Step 7: Commit**

```bash
git add install.ps1 install.sh tests/test_installers.py
git commit -m "feat: add verified windows and mac installers"
```

### Task 7: Update documentation and synchronize the live installation

**Files:**
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `SKILL.md`
- Modify: `scripts/bridge.py`
- Modify: `scripts/bridge_mcp.py`

**Interfaces:**
- Produces: accurate installation, lifecycle, delivery, troubleshooting, and
  test documentation.

- [ ] **Step 1: Add documentation assertions**

Extend installer tests to assert README commands exist in parsers and that the
documented lifecycle matches `TRANSITIONS`.

- [ ] **Step 2: Verify red**

Run: `python -m unittest tests.test_installers.DocumentationTests -v`

Expected: old lifecycle and unsupported commands fail.

- [ ] **Step 3: Update docs and version**

Document:

- Windows and macOS installation commands.
- Python requirement and launcher behavior.
- Delivery states and acknowledgment semantics.
- Correct question/answer and review flows.
- Exact test command.
- macOS real-host verification requirement.

Set the release version consistently in CLI and MCP.

- [ ] **Step 4: Verify green**

Run: `python -m unittest tests.test_installers.DocumentationTests -v`

Expected: documentation contract passes.

- [ ] **Step 5: Install the canonical source on this Windows host**

Run `install.ps1` with the tested Python path for detected agents. Verify:

- `~/.agent-bridge/skill/scripts` hashes equal repository hashes.
- `~/.agents/skills/agent-bridge` points to the canonical skill or is replaced
  by an exact synchronized copy when symlinks are unavailable.
- Codex and Reasonix MCP configs use native paths and a runnable Python.
- Hooks use Windows-native commands.

- [ ] **Step 6: Commit**

```bash
git add README.md README.zh-CN.md SKILL.md scripts
git commit -m "docs: publish reliable cross-platform workflow"
```

### Task 8: Full verification and ZCode acceptance handoff

**Files:**
- Create: `REVIEW_FOR_ZCODE.md`
- Update: agent-bridge task board through CLI.

**Interfaces:**
- Produces: reproducible evidence and a ZCode acceptance task.

- [ ] **Step 1: Run the full suite**

Run:

```powershell
python -m unittest discover -s tests -v
python -m compileall -q scripts tests
git status --short
```

Expected: all tests pass, compile exits 0, only intended files are tracked.

- [ ] **Step 2: Run runtime acceptance**

Verify:

- `bridge doctor --strict`
- GBK status subprocess
- MCP initialize/tools/list/tools/call
- 40-process concurrency
- send/status acknowledgment/claim/question/answer/review/done
- Windows notification helper exit 0
- installed-source hash equality

- [ ] **Step 3: Write ZCode review instructions**

`REVIEW_FOR_ZCODE.md` includes commit range, findings fixed, exact verification
commands, Windows evidence, macOS commands still requiring real-host execution,
and acceptance checklist.

- [ ] **Step 4: Send ZCode the acceptance task**

```bash
bridge send --to zcode \
  --subject "Acceptance: agent-bridge cross-platform reliability" \
  --body "Review C:/tmp/roundtable/REVIEW_FOR_ZCODE.md and run the listed tests." \
  --files "REVIEW_FOR_ZCODE.md"
```

Expected: task is created with delivery status visible; do not claim ZCode
approved until ZCode returns a verdict.

- [ ] **Step 5: Commit**

```bash
git add REVIEW_FOR_ZCODE.md
git commit -m "docs: add zcode acceptance checklist"
```
