# Agent Bridge v2 Lightweight Desktop Collaboration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the v1 JSON/file-lock bridge with a lightweight SQLite-based collaboration system that gives Codex, Claude Code, Reasonix, and ZCode trustworthy task delivery, native notifications, desktop integrations, safe per-agent automation, and an on-demand TUI on Windows and macOS.

**Architecture:** A standard-library Python package owns the SQLite state machine and exposes stable CLI/MCP services. Mutations write a transactional outbox, and a detached dispatcher runs for at most 30 seconds to notify, integrate, or safely launch a target; thin host adapters and native notification helpers contain all platform-specific behavior. No bridge process remains running when the system is idle.

**Tech Stack:** Python 3.9–3.13 standard library (`sqlite3`, `argparse`, `unittest`), setuptools build backend, Rust `windows` crate for WinRT Toast, Swift/UserNotifications for macOS, PowerShell 5.1+, Bash 3.2+, SQLite WAL, ANSI/VT terminal rendering, GitHub Actions.

## Global Constraints

- Python 3.9 through 3.13; do not use syntax introduced after Python 3.9.
- Python runtime dependencies are standard-library only.
- Default data root is `~/.agent-bridge`; `AGENT_BRIDGE_HOME` may override it with another local path.
- SQLite databases on network shares and cloud-sync folders are unsupported.
- Windows 11 x64/ARM64 and macOS Intel/Apple Silicon are release platforms.
- Task state and delivery evidence are separate; launch success is never delivery acknowledgment.
- Automatic execution cannot exceed the target agent's local profile.
- No network listener, cloud service, default telemetry, Electron app, web dashboard, or resident bridge daemon.
- Idle bridge process count is zero; one dispatcher burst lasts at most 30 seconds.
- Existing task ownership rules and non-interactive CLI/MCP concepts remain compatible.
- Every behavior change follows red-green-refactor and ends in an independently testable commit.
- The approved design is `docs/superpowers/specs/2026-07-23-agent-bridge-v2-lightweight-desktop-design.md`.

---

## File and Interface Map

### Python package

- `pyproject.toml`: package metadata, console scripts, wheel contents, Python floor.
- `src/agent_bridge/version.py`: package and protocol version constants.
- `src/agent_bridge/paths.py`: local-path validation and data-root resolution.
- `src/agent_bridge/models.py`: enums and immutable value objects shared across services.
- `src/agent_bridge/store.py`: SQLite connection policy, transactions, queries, and integrity checks.
- `src/agent_bridge/migrations/*.sql`: ordered schema migrations.
- `src/agent_bridge/migrate_v1.py`: idempotent v1 JSON importer and JSON exporter.
- `src/agent_bridge/state_machine.py`: legal task transitions and actor authorization.
- `src/agent_bridge/service.py`: application service used by CLI, MCP, integrations, and TUI.
- `src/agent_bridge/outbox.py`: transactional outbox enqueue and due-item queries.
- `src/agent_bridge/dispatcher.py`: lease acquisition, bounded burst loop, retry, and evidence.
- `src/agent_bridge/delivery.py`: channel result normalization and aggregate evidence.
- `src/agent_bridge/launchers.py`: local execution-policy checks and argv-safe process start.
- `src/agent_bridge/terminals.py`: integrated-terminal and OS-terminal opening.
- `src/agent_bridge/notifications.py`: helper protocol client and pure-Python degradation.
- `src/agent_bridge/adapters/base.py`: host capability and adapter contracts.
- `src/agent_bridge/adapters/{codex,claude,reasonix,zcode}.py`: host-specific detection and managed configuration.
- `src/agent_bridge/setup.py`: plan/backup/install/validate/rollback/uninstall orchestration.
- `src/agent_bridge/cli.py`: public command parser and text/JSON presentation.
- `src/agent_bridge/mcp.py`: JSON-RPC/MCP adapter over `BridgeService`.
- `src/agent_bridge/tui/*.py`: on-demand renderer, input adapters, controller.

### Compatibility and integrations

- `scripts/bridge.py`: compatibility entry point calling `agent_bridge.cli.main`.
- `scripts/bridge_mcp.py`: compatibility entry point calling `agent_bridge.mcp.main`.
- `integrations/{codex,claude,reasonix,zcode}/`: versioned host integration templates and manifests.

### Native code

- `native/windows-notify/`: WinRT Toast helper and URI activation handler.
- `native/macos-notify/`: Swift notification application bundle.

### Tests and release

- `tests/unit/`: isolated Python service tests.
- `tests/integration/`: SQLite concurrency, dispatcher, migration, and CLI/MCP round trips.
- `tests/installers/`: managed-config and lifecycle fixture tests.
- `tests/platform/`: helper protocol and real-platform smoke harnesses.
- `tests/fixtures/`: representative host configuration fixtures.
- `.github/workflows/ci.yml`: Python/platform CI matrix.
- `.github/workflows/release.yml`: wheels, helpers, checksums, and SBOM.
- `docs/installation/`, `docs/architecture/`, `docs/release/`: user and maintainer docs.
- `REVIEW_FOR_ZCODE.md`: final design/code/test review handoff.

---

### Task 1: Establish the v2 package and compatibility boundary

**Files:**
- Create: `pyproject.toml`
- Create: `src/agent_bridge/__init__.py`
- Create: `src/agent_bridge/version.py`
- Create: `src/agent_bridge/paths.py`
- Create: `src/agent_bridge/models.py`
- Create: `tests/unit/test_paths.py`
- Create: `tests/unit/test_models.py`
- Modify: `scripts/bridge.py`
- Modify: `scripts/bridge_mcp.py`

**Interfaces:**
- Produces: `get_data_root(env: Mapping[str, str]) -> Path`
- Produces: `require_local_data_root(path: Path) -> Path`
- Produces: `TaskState`, `DeliveryStatus`, `ExecutionPolicy`, `AgentProfile`
- Produces: `BRIDGE_VERSION = "2.0.0"` and independent schema/protocol versions

- [ ] **Step 1: Write failing path and model tests**

```python
class PathTests(unittest.TestCase):
    def test_override_is_resolved_without_requiring_existence(self):
        root = get_data_root({"AGENT_BRIDGE_HOME": "./local-data"})
        self.assertTrue(root.is_absolute())
        self.assertEqual(root.name, "local-data")

    def test_unc_path_is_rejected_on_windows(self):
        with mock.patch("agent_bridge.paths.os.name", "nt"):
            with self.assertRaisesRegex(ValueError, "local filesystem"):
                require_local_data_root(Path(r"\\server\share\bridge"))


class ModelTests(unittest.TestCase):
    def test_agent_profile_defaults_to_manual(self):
        profile = AgentProfile(name="zcode")
        self.assertEqual(profile.execution_policy, ExecutionPolicy.MANUAL)
        self.assertEqual(profile.max_concurrency, 1)
```

- [ ] **Step 2: Run the new tests and verify import failure**

Run: `py -3 -m unittest tests.unit.test_paths tests.unit.test_models -v`

Expected: `ModuleNotFoundError: No module named 'agent_bridge'`.

- [ ] **Step 3: Add package metadata and console entry points**

```toml
[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "agent-bridge"
version = "2.0.0"
requires-python = ">=3.9"
dependencies = []

[project.scripts]
bridge = "agent_bridge.cli:main"
bridge-mcp = "agent_bridge.mcp:main"
```

- [ ] **Step 4: Implement Python 3.9-compatible shared models**

```python
class TaskState(str, enum.Enum):
    PENDING = "pending"
    WORKING = "working"
    INPUT_REQUIRED = "input_required"
    REVIEW_REQUESTED = "review_requested"
    CHANGES_REQUESTED = "changes_requested"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class AgentProfile:
    name: str
    execution_policy: ExecutionPolicy = ExecutionPolicy.MANUAL
    launch_argv: Tuple[str, ...] = ()
    terminal_preference: str = "auto"
    max_concurrency: int = 1
    cooldown_seconds: int = 30
    workspace_allowlist: Tuple[str, ...] = ()
```

- [ ] **Step 5: Replace compatibility scripts with import-only wrappers**

```python
#!/usr/bin/env python3
from agent_bridge.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
```

Keep the old v1 file reachable through Git history and migration fixtures; do
not keep two active implementations.

- [ ] **Step 6: Install editable package and run tests**

Run: `py -3 -m pip install -e .`

Expected: exit 0 and `bridge --help` resolves the new entry point.

Run: `py -3 -m unittest tests.unit.test_paths tests.unit.test_models -v`

Expected: all tests pass.

- [ ] **Step 7: Commit the package boundary**

```powershell
git add pyproject.toml src scripts tests/unit
git commit -m "feat: establish agent bridge v2 package"
```

### Task 2: Build the SQLite schema, store, and v1 migration

**Files:**
- Create: `src/agent_bridge/migrations/0001_initial.sql`
- Create: `src/agent_bridge/store.py`
- Create: `src/agent_bridge/migrate_v1.py`
- Create: `tests/unit/test_store.py`
- Create: `tests/integration/test_migrate_v1.py`
- Create: `tests/fixtures/v1/board.json`
- Create: `tests/fixtures/v1/agents/zcode/agent.json`

**Interfaces:**
- Consumes: `get_data_root()`, model enums
- Produces: `Store.open(path: Path) -> Store`
- Produces: `Store.transaction(immediate: bool = False)`
- Produces: `Store.integrity_report() -> IntegrityReport`
- Produces: `import_v1(store: Store, v1_root: Path) -> ImportReport`
- Produces: `export_json(store: Store, destination: Path) -> Path`

- [ ] **Step 1: Write failing schema and transaction tests**

```python
class StoreTests(unittest.TestCase):
    def test_open_applies_pragmas_and_schema(self):
        store = Store.open(self.db_path)
        self.assertEqual(store.scalar("PRAGMA journal_mode"), "wal")
        self.assertEqual(store.scalar("PRAGMA foreign_keys"), 1)
        self.assertEqual(store.scalar("PRAGMA busy_timeout"), 5000)
        self.assertEqual(store.scalar("SELECT MAX(version) FROM schema_migrations"), 1)

    def test_task_and_outbox_rollback_together(self):
        store = Store.open(self.db_path)
        with self.assertRaises(RuntimeError):
            with store.transaction(immediate=True) as connection:
                connection.execute("INSERT INTO tasks (...) VALUES (...)")
                connection.execute("INSERT INTO outbox (...) VALUES (...)")
                raise RuntimeError("crash")
        self.assertEqual(store.scalar("SELECT COUNT(*) FROM tasks"), 0)
        self.assertEqual(store.scalar("SELECT COUNT(*) FROM outbox"), 0)
```

- [ ] **Step 2: Run the store tests and verify failure**

Run: `py -3 -m unittest tests.unit.test_store -v`

Expected: import or missing-schema failure.

- [ ] **Step 3: Add the normalized schema**

`0001_initial.sql` creates the tables from design section 6, including:

```sql
CREATE TABLE tasks (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  sender TEXT NOT NULL REFERENCES agents(name),
  assignee TEXT NOT NULL REFERENCES agents(name),
  state TEXT NOT NULL CHECK (state IN (
    'pending','working','input_required','review_requested',
    'changes_requested','completed','failed'
  )),
  subject TEXT NOT NULL,
  body TEXT NOT NULL DEFAULT '',
  priority INTEGER NOT NULL DEFAULT 0,
  revision INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE outbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  idempotency_key TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  due_at TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  completed_at TEXT
);
```

Add foreign keys, delivery, event, dependency, artifact, lease, notification,
metadata, and migration tables plus the indexes named in the design.

- [ ] **Step 4: Implement connection, migration, backup, and integrity policy**

```python
@classmethod
def open(cls, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=5.0, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    store = cls(path, connection)
    store.apply_migrations()
    return store
```

Before a non-initial migration, use SQLite's backup API to write a timestamped
backup. Check migration SHA-256 values against `schema_migrations`.

- [ ] **Step 5: Write and run failing idempotent v1 import tests**

```python
def test_import_is_idempotent_and_preserves_delivery_history(self):
    first = import_v1(self.store, self.fixture_root)
    second = import_v1(self.store, self.fixture_root)
    self.assertEqual(first.imported_tasks, 1)
    self.assertEqual(second.imported_tasks, 0)
    self.assertEqual(self.store.scalar("SELECT COUNT(*) FROM tasks"), 1)
    self.assertEqual(self.store.scalar("SELECT COUNT(*) FROM import_ledger"), 1)
```

Run: `py -3 -m unittest tests.integration.test_migrate_v1 -v`

Expected: missing importer failure.

- [ ] **Step 6: Implement import ledger, backup, and portable export**

Read v1 JSON as UTF-8, validate required keys, copy the original tree into a
timestamped backup directory, import each source hash once, and write exported
JSON atomically with `os.replace`.

- [ ] **Step 7: Run storage and migration tests**

Run: `py -3 -m unittest tests.unit.test_store tests.integration.test_migrate_v1 -v`

Expected: all tests pass and the temporary database reports `integrity_check=ok`.

- [ ] **Step 8: Commit durable storage**

```powershell
git add src/agent_bridge tests
git commit -m "feat: add sqlite storage and v1 migration"
```

### Task 3: Implement the task state machine and application service

**Files:**
- Create: `src/agent_bridge/state_machine.py`
- Create: `src/agent_bridge/service.py`
- Create: `src/agent_bridge/outbox.py`
- Create: `tests/unit/test_state_machine.py`
- Create: `tests/integration/test_service_workflows.py`

**Interfaces:**
- Consumes: `Store`, `TaskState`, `DeliveryStatus`
- Produces: `authorize_transition(task, actor, action) -> TaskState`
- Produces: `BridgeService.send_task(...) -> TaskView`
- Produces: `claim`, `question`, `answer`, `request_review`, `review`, `done`
- Produces: `status(agent)`, `inbox(agent)`, `show(task_id)`, `board(project)`

- [ ] **Step 1: Write a complete transition-table test**

```python
LEGAL = {
    ("pending", "claim"): "working",
    ("working", "question"): "input_required",
    ("input_required", "answer"): "pending",
    ("working", "request_review"): "review_requested",
    ("review_requested", "approve"): "completed",
    ("review_requested", "changes"): "changes_requested",
    ("changes_requested", "claim"): "working",
    ("working", "done"): "completed",
    ("working", "fail"): "failed",
}

def test_every_declared_transition(self):
    for (source, action), target in LEGAL.items():
        task = task_for(source)
        actor = task.assignee if action not in {"answer", "approve", "changes"} else task.sender
        self.assertEqual(authorize_transition(task, actor, action).value, target)
```

Also test every wrong actor and wrong source state.

- [ ] **Step 2: Run transition tests and verify failure**

Run: `py -3 -m unittest tests.unit.test_state_machine -v`

Expected: missing module or transition table.

- [ ] **Step 3: Implement the explicit transition rules**

```python
RULES = {
    "claim": Transition(frozenset((PENDING, CHANGES_REQUESTED)), WORKING, ASSIGNEE),
    "question": Transition(frozenset((WORKING,)), INPUT_REQUIRED, ASSIGNEE),
    "answer": Transition(frozenset((INPUT_REQUIRED,)), PENDING, SENDER),
    "request_review": Transition(frozenset((WORKING,)), REVIEW_REQUESTED, ASSIGNEE),
    "approve": Transition(frozenset((REVIEW_REQUESTED,)), COMPLETED, SENDER),
    "changes": Transition(frozenset((REVIEW_REQUESTED,)), CHANGES_REQUESTED, SENDER),
    "done": Transition(frozenset((WORKING,)), COMPLETED, ASSIGNEE),
    "fail": Transition(frozenset((WORKING,)), FAILED, ASSIGNEE),
}
```

- [ ] **Step 4: Write failing workflow and outbox-atomicity tests**

```python
def test_question_answer_review_round_trip(self):
    task = self.service.send_task("codex", "zcode", "Review", "Body")
    self.service.claim(task.id, "zcode")
    self.service.question(task.id, "zcode", "Which platform?")
    self.service.answer(task.id, "codex", "Both")
    self.service.claim(task.id, "zcode")
    self.service.request_review(task.id, "zcode", "Ready")
    final = self.service.review(task.id, "codex", "approve", "Approved")
    self.assertEqual(final.state, TaskState.COMPLETED)
    self.assertEqual(self.count_events(task.id), 7)
```

- [ ] **Step 5: Implement transactional service mutations**

Each service mutation starts `BEGIN IMMEDIATE`, selects the task, validates
revision and actor, changes the row, appends one immutable `task_events` row,
and enqueues the target's delivery event before commit.

- [ ] **Step 6: Run state and workflow suites**

Run: `py -3 -m unittest tests.unit.test_state_machine tests.integration.test_service_workflows -v`

Expected: all tests pass.

- [ ] **Step 7: Commit the service layer**

```powershell
git add src/agent_bridge tests
git commit -m "feat: add transactional task lifecycle service"
```

### Task 4: Restore complete CLI and MCP parity on the service layer

**Files:**
- Create: `src/agent_bridge/cli.py`
- Create: `src/agent_bridge/mcp.py`
- Create: `src/agent_bridge/presentation.py`
- Create: `tests/integration/test_cli_v2.py`
- Create: `tests/integration/test_mcp_v2.py`
- Modify: `SKILL.md`

**Interfaces:**
- Consumes: `BridgeService`
- Produces: all documented `bridge` commands and `--json`
- Produces: MCP tools for every non-interactive command
- Produces: identity parsing through `parse_identity(argv) -> str`

- [ ] **Step 1: Write failing CLI contract tests**

```python
REQUIRED_COMMANDS = {
    "status", "inbox", "send", "claim", "done", "show", "board",
    "question", "answer", "review", "wake", "agents", "activity",
    "context", "clean", "doctor", "project", "whoami",
    "who-coordinates", "log", "dispatch", "tui", "setup",
    "uninstall", "migrate", "export", "open-action",
}

def test_help_exposes_required_commands(self):
    result = run_module("agent_bridge.cli", "--help")
    for command in REQUIRED_COMMANDS:
        self.assertIn(command, result.stdout)

def test_missing_as_value_is_a_parse_error_not_index_error(self):
    result = run_module("agent_bridge.mcp", "--as")
    self.assertEqual(result.returncode, 2)
    self.assertNotIn("IndexError", result.stderr)
```

- [ ] **Step 2: Run CLI tests and verify failure**

Run: `py -3 -m unittest tests.integration.test_cli_v2 -v`

Expected: missing CLI module or missing commands.

- [ ] **Step 3: Implement one parser and shared presentation boundary**

Use subparser handler functions that call `BridgeService`; JSON output contains
stable machine fields, while text output uses encoding-safe ASCII status
prefixes. Configure stdout/stderr with `errors="replace"` when supported.

- [ ] **Step 4: Write failing MCP parity tests**

Initialize JSON-RPC, list tools, compare normalized tool names with
`REQUIRED_COMMANDS - {"dispatch", "tui", "setup", "uninstall", "open-action"}`, and execute
`whoami`, `send`, `show`, and `claim`.

- [ ] **Step 5: Implement MCP methods without subprocess recursion**

Create the same `BridgeService` in the MCP process and return JSON-serializable
views directly. Parse `--as` with `argparse`; never inspect the last argv item.

- [ ] **Step 6: Run CLI, MCP, and legacy GBK tests**

Run: `py -3 -m unittest tests.integration.test_cli_v2 tests.integration.test_mcp_v2 tests.test_cli -v`

Expected: all tests pass under UTF-8 and GBK subprocess output.

- [ ] **Step 7: Update the skill protocol from canonical constants**

Make `SKILL.md` match the legal transitions and delivery meanings. Add a test
that compares documented state strings with `TaskState`.

- [ ] **Step 8: Commit public protocol parity**

```powershell
git add src/agent_bridge scripts tests SKILL.md
git commit -m "feat: expose v2 service through cli and mcp"
```

### Task 5: Add transactional delivery evidence and the burst dispatcher

**Files:**
- Create: `src/agent_bridge/delivery.py`
- Create: `src/agent_bridge/dispatcher.py`
- Create: `tests/unit/test_delivery.py`
- Create: `tests/integration/test_dispatcher.py`
- Create: `tests/integration/test_dispatcher_faults.py`

**Interfaces:**
- Consumes: outbox rows and channel adapters
- Produces: `aggregate_delivery(attempts) -> DeliveryStatus`
- Produces: `Dispatcher.run_burst(deadline_seconds: float = 30.0) -> DispatchReport`
- Produces: `request_dispatch() -> bool`
- Produces: `tick() -> bool`

- [ ] **Step 1: Write failing delivery aggregation tests**

```python
def test_launch_is_weaker_than_acknowledgment(self):
    attempts = [
        attempt("launch", DeliveryStatus.LAUNCH_STARTED),
        attempt("notification", DeliveryStatus.OS_POSTED),
    ]
    self.assertEqual(aggregate_delivery(attempts), DeliveryStatus.LAUNCH_STARTED)

def test_claim_is_strongest_evidence(self):
    attempts = [attempt("plugin", DeliveryStatus.PLUGIN_DELIVERED),
                attempt("agent", DeliveryStatus.CLAIMED)]
    self.assertEqual(aggregate_delivery(attempts), DeliveryStatus.CLAIMED)
```

- [ ] **Step 2: Run delivery tests and verify failure**

Run: `py -3 -m unittest tests.unit.test_delivery -v`

Expected: missing delivery module.

- [ ] **Step 3: Implement evidence precedence and channel rows**

Define explicit precedence:

```python
EVIDENCE_RANK = {
    QUEUED: 0, DISPATCHING: 1, OS_POSTED: 2, PLUGIN_DELIVERED: 3,
    LAUNCH_STARTED: 4, VIEWED: 5, AGENT_ACKNOWLEDGED: 6, CLAIMED: 7,
}
```

`retry_wait` and `failed` remain attempt outcomes and do not erase stronger
evidence from another channel.

- [ ] **Step 4: Write failing lease, retry, coalescing, and crash tests**

Test two dispatchers competing for one lease, reclaim after expiry, two
identical outbox intents producing one channel effect, and an injected crash
after an effect but before completion followed by idempotent retry.

- [ ] **Step 5: Implement the bounded dispatcher**

Use a random owner nonce, compare-and-update lease acquisition inside
`BEGIN IMMEDIATE`, select bounded batches, mark each attempt before invoking
the adapter, and update retry due time with capped exponential backoff plus
jitter. Exit on no due work or deadline.

- [ ] **Step 6: Implement detached dispatch request and entry-point tick**

On Windows use `CREATE_NO_WINDOW | DETACHED_PROCESS`; on POSIX use
`start_new_session=True`. Pass a fixed argv list:

```python
[sys.executable, "-m", "agent_bridge.cli", "dispatch", "--burst"]
```

Do not pass task content. `tick()` performs one indexed due-row query and
returns immediately when none exists.

- [ ] **Step 7: Run dispatcher and fault suites**

Run: `py -3 -m unittest tests.unit.test_delivery tests.integration.test_dispatcher tests.integration.test_dispatcher_faults -v`

Expected: one effect per idempotency key, recoverable expired lease, no lost
outbox rows, and burst duration below its injected test deadline.

- [ ] **Step 8: Commit bounded delivery**

```powershell
git add src/agent_bridge tests
git commit -m "feat: add transactional burst delivery"
```

### Task 6: Enforce safe agent launch and terminal opening

**Files:**
- Create: `src/agent_bridge/launchers.py`
- Create: `src/agent_bridge/terminals.py`
- Create: `tests/unit/test_launchers.py`
- Create: `tests/unit/test_terminals.py`
- Create: `tests/integration/test_launch_deduplication.py`

**Interfaces:**
- Consumes: `AgentProfile`, project path, task ID
- Produces: `evaluate_launch(profile, workspace, running_count, last_launch) -> LaunchDecision`
- Produces: `launch_agent(decision) -> LaunchResult`
- Produces: `open_task_terminal(adapter, task_id, workspace) -> OpenResult`

- [ ] **Step 1: Write failing policy-boundary tests**

```python
def test_sender_cannot_override_manual_policy(self):
    profile = AgentProfile(name="zcode", execution_policy=ExecutionPolicy.MANUAL)
    decision = evaluate_launch(profile, self.workspace, 0, None, requested_auto=True)
    self.assertFalse(decision.allowed)
    self.assertEqual(decision.reason, "target policy is manual")

def test_workspace_outside_allowlist_is_rejected(self):
    profile = profile_with_allowlist(self.allowed)
    with self.assertRaisesRegex(LaunchPolicyError, "allowlist"):
        evaluate_launch(profile, self.other, 0, None, requested_auto=True)
```

- [ ] **Step 2: Run launch tests and verify failure**

Run: `py -3 -m unittest tests.unit.test_launchers -v`

Expected: missing launcher module.

- [ ] **Step 3: Implement policy evaluation and argv-safe launch**

Reject empty argv, shell metacharacter interpretation, excess concurrency,
cooldown violation, and disallowed workspace. Call `subprocess.Popen` with an
argv sequence, explicit cwd, minimal inherited environment, and platform
detachment flags; never use `shell=True`.

- [ ] **Step 4: Write terminal fallback tests**

Provide fake host adapters proving this order:

```text
host integrated terminal
Windows Terminal / macOS Terminal
plain current terminal instructions
```

Assert task ID and workspace are separate argv values.

- [ ] **Step 5: Implement terminal adapters**

Use host capability first. On Windows prefer `wt.exe`; on macOS invoke the
installed notification/app launcher with a structured open request instead of
building AppleScript source from task text.

- [ ] **Step 6: Run launch and deduplication tests**

Run: `py -3 -m unittest tests.unit.test_launchers tests.unit.test_terminals tests.integration.test_launch_deduplication -v`

Expected: policy tests pass and repeated outbox work produces one launch.

- [ ] **Step 7: Commit safe launching**

```powershell
git add src/agent_bridge tests
git commit -m "feat: enforce local launch and terminal policy"
```

### Task 7: Implement the host adapter API and four desktop integrations

**Files:**
- Create: `src/agent_bridge/adapters/__init__.py`
- Create: `src/agent_bridge/adapters/base.py`
- Create: `src/agent_bridge/adapters/codex.py`
- Create: `src/agent_bridge/adapters/claude.py`
- Create: `src/agent_bridge/adapters/reasonix.py`
- Create: `src/agent_bridge/adapters/zcode.py`
- Create: `integrations/codex/manifest.json`
- Create: `integrations/claude/manifest.json`
- Create: `integrations/reasonix/manifest.json`
- Create: `integrations/zcode/manifest.json`
- Create: `tests/unit/test_adapters.py`
- Create: `tests/installers/test_host_config.py`
- Create: `tests/fixtures/hosts/`

**Interfaces:**
- Produces: `HostAdapter.detect()`, `plan_install()`, `install()`, `uninstall()`
- Produces: `health_check()`, `notify_in_app()`, `launch()`, `open_terminal()`
- Produces: `HostCapabilities(surface, can_ack, can_open_terminal, can_receive_context, protocol_version, integration_version)`

- [ ] **Step 1: Write failing adapter contract tests**

```python
def test_every_adapter_reports_real_capabilities(self):
    for adapter_type in (CodexAdapter, ClaudeAdapter, ReasonixAdapter, ZCodeAdapter):
        adapter = adapter_type(self.fixture_home)
        capabilities = adapter.capabilities()
        self.assertIn(capabilities.surface, {
            Surface.NATIVE_PANEL, Surface.SESSION_CARD, Surface.TERMINAL_FALLBACK
        })
        self.assertGreaterEqual(capabilities.protocol_version, 1)

def test_missing_host_is_not_reported_as_delivered(self):
    result = ZCodeAdapter(self.empty_home).notify_in_app(self.task)
    self.assertEqual(result.status, DeliveryStatus.FAILED)
```

- [ ] **Step 2: Run adapter tests and verify failure**

Run: `py -3 -m unittest tests.unit.test_adapters -v`

Expected: missing adapters.

- [ ] **Step 3: Implement the strict abstract adapter and registry**

Use `abc.ABC`; return typed plan/result objects rather than booleans. The
registry has exact names and aliases from one canonical constant.

- [ ] **Step 4: Add representative host config fixtures and failing round-trip tests**

For each host, test absent config, existing unrelated keys, an older managed
block, non-ASCII paths, and install/uninstall round trip preserving unrelated
content byte-for-byte where the format permits.

- [ ] **Step 5: Implement four capability-aware adapters**

Each adapter uses only documented host mechanisms available in its fixture:
native extension when supported, otherwise Skill/Hook/MCP session card.
Adapters emit an explicit terminal fallback and health warning when the richer
surface cannot be installed.

- [ ] **Step 6: Add task-card ACK contract tests**

Feed each integration a bounded task payload and assert it calls the shared
`acknowledge` operation with host identity, task ID, integration version, and
protocol version.

- [ ] **Step 7: Run all adapter and config tests**

Run: `py -3 -m unittest tests.unit.test_adapters tests.installers.test_host_config -v`

Expected: four adapters pass detection, install, ACK, repair, and uninstall
fixtures without damaging unrelated config.

- [ ] **Step 8: Commit desktop integrations**

```powershell
git add src/agent_bridge/adapters integrations tests
git commit -m "feat: add four desktop host integrations"
```

### Task 8: Add the Windows native Toast helper

**Files:**
- Create: `native/windows-notify/Cargo.toml`
- Create: `native/windows-notify/src/main.rs`
- Create: `native/windows-notify/src/protocol.rs`
- Create: `native/windows-notify/src/registration.rs`
- Create: `native/windows-notify/src/toast.rs`
- Create: `tests/platform/test_windows_notify_protocol.py`
- Modify: `src/agent_bridge/notifications.py`

**Interfaces:**
- Consumes: one bounded JSON request on stdin
- Produces: one JSON response containing `ok`, `notification_id`, `status`, `detail`
- Supports: `post`, `register`, `unregister`, and URI actions `view`, `claim`, `snooze`

- [ ] **Step 1: Write failing helper-protocol tests with a fake executable**

```python
def test_post_result_requires_native_identifier(self):
    helper = fake_helper(stdout='{"ok":true,"status":"os_posted"}')
    result = WindowsNotifier(helper).post(self.notice)
    self.assertFalse(result.ok)
    self.assertIn("notification_id", result.detail)

def test_timeout_is_visible_failure(self):
    helper = sleeping_helper()
    result = WindowsNotifier(helper, timeout_seconds=0.1).post(self.notice)
    self.assertEqual(result.status, DeliveryStatus.FAILED)
```

- [ ] **Step 2: Run protocol tests and verify failure**

Run: `py -3 -m unittest tests.platform.test_windows_notify_protocol -v`

Expected: missing notification client.

- [ ] **Step 3: Implement the bounded Python helper client**

Serialize title, body, opaque task ID, action names, and timeout-limited expiry
as JSON. Use `subprocess.run(..., input=json_text, timeout=5, shell=False)`.
Reject output above the documented limit and malformed JSON.

- [ ] **Step 4: Add Rust request/response parsing and tests**

Define serde structs with `deny_unknown_fields`, maximum string lengths, and
the four fixed operations. Run:

`cargo test --manifest-path native/windows-notify/Cargo.toml`

Expected before WinRT implementation: protocol tests pass; Toast test remains
failing or platform-gated.

- [ ] **Step 5: Implement per-user AUMID/protocol registration and WinRT Toast**

Use the `windows` crate APIs, a per-user Start Menu shortcut or documented
unpackaged-app registration, Toast actions with opaque task IDs, and protocol
activation back to `bridge open-action`. Do not invoke PowerShell or embed
commands in XML.

- [ ] **Step 6: Build and run the Windows smoke harness**

Run:

```powershell
cargo build --release --manifest-path native/windows-notify/Cargo.toml
py -3 -m unittest tests.platform.test_windows_notify_protocol -v
bridge doctor --strict
```

Expected: helper registers, posts a Toast with an ID, remains in Notification
Center, and its View action opens the test task. Record manual evidence under
`artifacts/platform/windows/`.

- [ ] **Step 7: Enforce size and dependency limits**

Assert release helper is at most 5 MB and generate its dependency/license list.

- [ ] **Step 8: Commit Windows notifications**

```powershell
git add native/windows-notify src/agent_bridge/notifications.py tests/platform
git commit -m "feat: add native windows toast delivery"
```

### Task 9: Add the macOS UserNotifications helper

**Files:**
- Create: `native/macos-notify/Package.swift`
- Create: `native/macos-notify/Sources/AgentBridgeNotifier/main.swift`
- Create: `native/macos-notify/Sources/AgentBridgeNotifier/Protocol.swift`
- Create: `native/macos-notify/Sources/AgentBridgeNotifier/AppDelegate.swift`
- Create: `native/macos-notify/Info.plist`
- Create: `native/macos-notify/AgentBridgeNotifier.entitlements`
- Create: `tests/platform/test_macos_notify_protocol.py`
- Modify: `src/agent_bridge/notifications.py`

**Interfaces:**
- Matches the Windows JSON helper protocol
- Produces a signed/notarizable `.app` bundle and universal2 release artifact

- [ ] **Step 1: Reuse the protocol contract in a failing macOS client test**

Run: `py -3 -m unittest tests.platform.test_macos_notify_protocol -v`

Expected: helper discovery or response validation failure.

- [ ] **Step 2: Implement Swift bounded JSON protocol parsing**

Use `Codable` request/response types, reject unsupported operations and
oversized fields, and print exactly one JSON response to stdout.

- [ ] **Step 3: Implement notification category, actions, and activation**

Register View, Claim, and Snooze actions with UserNotifications. Store only
opaque task/notification identifiers. On activation, call the fixed installed
Agent Bridge action handler with separate argv and then exit.

- [ ] **Step 4: Build Intel and Apple Silicon artifacts**

Run the documented `swift build` commands for `x86_64` and `arm64`, combine
with `lipo`, and assemble the `.app` bundle.

Expected: `file` reports both architectures and the helper is at most 5 MB
before signing metadata.

- [ ] **Step 5: Run macOS real-machine smoke tests**

Verify authorization, posting, Notification Center persistence, all actions,
integrated-terminal preference, fallback terminal, and process exit. Record
evidence under `artifacts/platform/macos/`.

- [ ] **Step 6: Add signing and notarization scripts/documentation**

Scripts consume certificate identifiers from environment variables without
printing secrets. Unsigned local development builds remain possible and are
clearly reported by `doctor`.

- [ ] **Step 7: Commit macOS notifications**

```bash
git add native/macos-notify src/agent_bridge/notifications.py tests/platform
git commit -m "feat: add native macos notification delivery"
```

### Task 10: Build the on-demand cross-platform TUI

**Files:**
- Create: `src/agent_bridge/tui/__init__.py`
- Create: `src/agent_bridge/tui/model.py`
- Create: `src/agent_bridge/tui/render.py`
- Create: `src/agent_bridge/tui/input_windows.py`
- Create: `src/agent_bridge/tui/input_posix.py`
- Create: `src/agent_bridge/tui/controller.py`
- Create: `tests/unit/test_tui_model.py`
- Create: `tests/unit/test_tui_render.py`
- Create: `tests/platform/test_tui_inputs.py`

**Interfaces:**
- Consumes: paginated `BridgeService` queries and public mutations
- Produces: `run_tui(service, input_adapter, output) -> int`
- Produces: noninteractive compact table when VT/full-screen support is absent

- [ ] **Step 1: Write failing projection and layout tests**

```python
def test_dashboard_contains_agents_counts_tasks_and_details(self):
    view = build_dashboard(fake_snapshot())
    self.assertEqual(len(view.agents), 4)
    self.assertEqual(view.counts.review, 1)
    self.assertEqual(view.selected_task.delivery, "plugin_delivered")

def test_narrow_terminal_uses_stacked_layout(self):
    screen = render_dashboard(fake_dashboard(), width=79, height=24)
    self.assertNotIn("\x1b[999", screen)
    self.assertIn("#49d05a", screen)
```

- [ ] **Step 2: Run TUI tests and verify failure**

Run: `py -3 -m unittest tests.unit.test_tui_model tests.unit.test_tui_render -v`

Expected: missing TUI package.

- [ ] **Step 3: Implement pure projection and renderer functions**

Keep ANSI emission separate from data queries. Bound subject/body widths,
support wide three-column and narrow stacked layouts, and expose a no-color
mode.

- [ ] **Step 4: Implement Windows and POSIX key readers**

Map arrows, Enter, `c`, `r`, `o`, `/`, and `q` into shared action enums using
`msvcrt` on Windows and `termios`/`select` on POSIX. Restore console mode in
`finally`.

- [ ] **Step 5: Implement controller actions through BridgeService**

Refresh every 250–500 ms only while open. Claim, retry, and open-terminal call
service methods and show their returned result; the TUI never issues SQL.

- [ ] **Step 6: Run render, input, and no-VT fallback tests**

Run: `py -3 -m unittest tests.unit.test_tui_model tests.unit.test_tui_render tests.platform.test_tui_inputs -v`

Expected: deterministic snapshots and restored terminal state.

- [ ] **Step 7: Commit the TUI**

```powershell
git add src/agent_bridge/tui tests
git commit -m "feat: add on-demand collaboration tui"
```

### Task 11: Implement safe setup, repair, migration, and uninstall

**Files:**
- Create: `src/agent_bridge/setup.py`
- Create: `src/agent_bridge/managed_config.py`
- Create: `tests/installers/test_setup_lifecycle.py`
- Create: `tests/installers/test_managed_config.py`
- Modify: `install.ps1`
- Modify: `install.sh`

**Interfaces:**
- Produces: `build_setup_plan()`, `apply_setup_plan()`, `repair()`, `uninstall()`
- Produces: `bridge setup --dry-run|--auto|--agent|--repair|status`
- Produces: `bridge uninstall [--purge-data]`
- Produces: rollback report and capability/degradation report

- [ ] **Step 1: Write failing managed-config round-trip tests**

```python
def test_install_and_uninstall_preserve_unrelated_content(self):
    original = self.fixture.read_bytes()
    installed = install_managed_block(original, "codex", self.payload)
    repaired = install_managed_block(installed, "codex", self.payload)
    self.assertEqual(installed, repaired)
    self.assertEqual(remove_managed_block(installed, "codex"), original)

def test_path_with_spaces_chinese_and_brackets(self):
    target = self.root / "用户 [one]" / "config.json"
    apply_atomic_edit(target, self.edit)
    self.assertTrue(target.exists())
```

- [ ] **Step 2: Run installer tests and verify failure**

Run: `py -3 -m unittest tests.installers.test_managed_config tests.installers.test_setup_lifecycle -v`

Expected: missing setup modules.

- [ ] **Step 3: Implement plan, backup, atomic edit, validation, and rollback**

Every planned mutation records target, original hash, backup path, managed
owner/version, validation function, and inverse action. Write temporary files
beside the target, flush, and replace atomically. On any failed validation,
apply inverse actions in reverse order and report each outcome.

- [ ] **Step 4: Implement setup lifecycle commands**

`--dry-run` never writes. `--auto` installs detected hosts. `--agent` limits
scope. `--repair` re-applies owned blocks after backup. `status` prints
capability, versions, notification helper, launch policy, and degradation.

- [ ] **Step 5: Implement conservative uninstall**

Remove only managed blocks, registered Agent Bridge URI/AUMID/app metadata,
owned PATH entries, and installed integration files. Keep data unless
`--purge-data`; display the exact deleted data root before purging.

- [ ] **Step 6: Reduce legacy installer scripts to safe bootstraps**

`install.ps1` and `install.sh` locate Python safely and invoke
`python -m pip install` plus `bridge setup`; quote each argument separately.
PowerShell uses `-LiteralPath` for filesystem operations.

- [ ] **Step 7: Run full lifecycle tests under non-admin temporary homes**

Run: `py -3 -m unittest tests.installers -v`

Expected: fresh install, reinstall, repair, upgrade, uninstall, and purge pass;
unrelated fixture bytes remain unchanged.

- [ ] **Step 8: Commit installation lifecycle**

```powershell
git add src/agent_bridge install.ps1 install.sh tests/installers
git commit -m "feat: add reversible cross-platform setup"
```

### Task 12: Add concurrency, fault, and performance release gates

**Files:**
- Create: `tests/integration/test_sqlite_concurrency.py`
- Create: `tests/integration/test_fault_injection.py`
- Create: `tests/integration/test_performance_budgets.py`
- Create: `tests/integration/test_end_to_end_v2.py`
- Create: `tests/platform/smoke_windows.ps1`
- Create: `tests/platform/smoke_macos.sh`
- Modify: `src/agent_bridge/store.py`
- Modify: `src/agent_bridge/dispatcher.py`

**Interfaces:**
- Produces: repeatable stress/fault harness and measured budget report
- Validates: zero lost tasks, zero duplicate transitions/effects, clean recovery

- [ ] **Step 1: Write the multi-process concurrency harness**

Spawn at least 40 processes that create tasks and at least 10 that concurrently
claim distinct tasks. Assert expected row counts, unique IDs, legal revisions,
complete event chains, and `PRAGMA integrity_check = ok`.

- [ ] **Step 2: Run the harness before tuning**

Run: `py -3 -m unittest tests.integration.test_sqlite_concurrency -v`

Expected: pass or expose a reproducible busy/transaction defect; never weaken
assertions to make timing failures disappear.

- [ ] **Step 3: Add deterministic fault injection**

Inject exceptions at named points:

```text
before_task_commit
after_task_commit
after_attempt_recorded
after_notification_effect
after_launch_effect
before_outbox_complete
```

Restart dispatcher after each point and assert durable task presence and at
most one effect per idempotency key.

- [ ] **Step 4: Add bounded performance tests**

Measure 1,000 local task creates, indexed inbox reads, no-work `tick`, and TUI
projection. Report percentiles and assert the approved P95 budgets with a
documented CI multiplier no greater than 2.0 for shared runners.

- [ ] **Step 5: Add complete four-agent workflow**

Create four profiles and run send, ACK, claim, question, answer, review
changes, reclaim, review approve, and completion while asserting task state
and delivery evidence at every step.

- [ ] **Step 6: Run all reliability gates repeatedly**

Run:

```powershell
1..5 | ForEach-Object {
  py -3 -m unittest tests.integration.test_sqlite_concurrency tests.integration.test_fault_injection tests.integration.test_end_to_end_v2 -v
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
```

Expected: five clean passes, zero locked-database leaks, no duplicate effects.

- [ ] **Step 7: Commit release gates**

```powershell
git add src/agent_bridge tests
git commit -m "test: enforce v2 reliability and performance"
```

### Task 13: Complete open-source packaging, CI, docs, and release artifacts

**Files:**
- Create: `LICENSE`
- Create: `SECURITY.md`
- Create: `CONTRIBUTING.md`
- Create: `docs/architecture/v2.md`
- Create: `docs/installation/windows.md`
- Create: `docs/installation/macos.md`
- Create: `docs/installation/migration-v1.md`
- Create: `docs/release/checklist.md`
- Create: `.github/workflows/ci.yml`
- Create: `.github/workflows/release.yml`
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `pyproject.toml`
- Create: `tests/installers/test_documentation_contract.py`

**Interfaces:**
- Produces: Apache-2.0 source release, platform wheels, sdist, portable ZIP,
  checksums, SBOM, and reproducible release checklist

- [ ] **Step 1: Write failing documentation and package contract tests**

Assert every documented command appears in CLI help, all four host names come
from the canonical registry, README links exist, package data contains
integration manifests, uninstall documents data preservation, and release docs
state real-machine notification requirements.

- [ ] **Step 2: Run contract tests and verify failure**

Run: `py -3 -m unittest tests.installers.test_documentation_contract -v`

Expected: missing files or outdated v1 instructions.

- [ ] **Step 3: Add Apache-2.0 governance and bilingual user documentation**

Document installation, capability levels, policies, migration, repair,
uninstall, privacy, security reporting, adapter contribution, limitations, and
no-daemon recovery behavior without claiming unverified native capability.

- [ ] **Step 4: Add CI matrix**

CI runs Python 3.9–3.13 unit/integration tests, Windows x64 and macOS helper
builds, package install tests, config fixtures, compile checks, and artifact
size checks. ARM64 and Intel/Apple universal artifacts are cross-built or run
on matching release runners and verified with platform metadata tools.

- [ ] **Step 5: Add release workflow**

Build from a clean tag, run tests, create platform wheels/sdist/portable ZIP,
generate SHA-256 files and SBOM, sign/notarize when credentials exist, and
refuse publication when required release inputs are missing.

- [ ] **Step 6: Build and inspect local artifacts**

Run:

```powershell
py -3 -m build
py -3 -m pip install --force-reinstall dist\agent_bridge-2.0.0-*.whl
bridge doctor --strict
```

Expected: installed version 2.0.0, package contains four integrations and the
matching platform helper or reports the pure-Python degradation.

- [ ] **Step 7: Run the entire Python suite and static checks**

Run:

```powershell
py -3 -m unittest discover -s tests -v
py -3 -m compileall -q src scripts tests
git diff --check
```

Expected: all tests pass, compile succeeds, and no whitespace errors.

- [ ] **Step 8: Commit the open-source release surface**

```powershell
git add LICENSE SECURITY.md CONTRIBUTING.md README.md README.zh-CN.md docs .github pyproject.toml tests
git commit -m "docs: prepare agent bridge v2 open source release"
```

### Task 14: Perform real-platform acceptance and ZCode design/code review

**Files:**
- Modify: `REVIEW_FOR_ZCODE.md`
- Create: `artifacts/platform/windows/acceptance.md`
- Create: `artifacts/platform/macos/acceptance.md`
- Create: `artifacts/release/capability-matrix.json`
- Create: `artifacts/release/checksums.txt`

**Interfaces:**
- Produces: reproducible release evidence and an Agent Bridge review task
- Consumes: approved design, implementation plan, implementation commit range

- [ ] **Step 1: Record Windows acceptance evidence**

On Windows 11, install as a normal user and verify setup/repair/uninstall,
Toast persistence and actions, all detected host integrations, integrated and
fallback terminal opening, GBK output, concurrency, migration, and idle process
count. Record commands, versions, hashes, and outcomes.

- [ ] **Step 2: Record macOS acceptance evidence**

On Intel and Apple Silicon coverage, verify installation, permission request,
UserNotifications actions, notification persistence, host integrations,
terminal preference/fallback, migration, and idle process count. Record
commands, versions, signing state, hashes, and outcomes.

- [ ] **Step 3: Produce the capability matrix and checksums**

The matrix reports actual `native-panel`, `session-card`, or
`terminal-fallback` capability for each host/platform and names every
degradation. Do not convert a fallback into a supported native claim.

- [ ] **Step 4: Write the complete ZCode handoff**

`REVIEW_FOR_ZCODE.md` includes:

```text
Design: docs/superpowers/specs/2026-07-23-agent-bridge-v2-lightweight-desktop-design.md
Plan: docs/superpowers/plans/2026-07-23-agent-bridge-v2-lightweight-desktop.md
Commit range: <design parent>..<implementation head>
Review: design conformance, concurrency, security, installers, portability,
        desktop UX, tests, documentation, artifacts, and v1 finding disposition
```

Include exact test commands and the Windows/macOS evidence paths.

- [ ] **Step 5: Send ZCode the review task through Agent Bridge**

```powershell
bridge send --to zcode --subject "Review Agent Bridge v2 design and implementation" --body "Review the approved design, implementation plan, full commit range, platform evidence, and v1 finding disposition in REVIEW_FOR_ZCODE.md. Run the listed commands and return approve or changes." --files "docs/superpowers/specs/2026-07-23-agent-bridge-v2-lightweight-desktop-design.md,docs/superpowers/plans/2026-07-23-agent-bridge-v2-lightweight-desktop.md,REVIEW_FOR_ZCODE.md,artifacts/platform/windows/acceptance.md,artifacts/platform/macos/acceptance.md,artifacts/release/capability-matrix.json"
```

Expected: task ID is printed. Delivery must reach `agent_acknowledged` or ZCode
must respond in the lifecycle before claiming receipt.

- [ ] **Step 6: Process ZCode feedback**

Read the review task, reproduce every claimed defect, implement valid changes
with focused tests and commits, document rejected claims with technical
evidence, and request re-review. Repeat until ZCode returns an approval verdict.

- [ ] **Step 7: Run final verification from a clean checkout**

Run all Python, Rust, Swift, installer, package, and platform smoke commands
from `docs/release/checklist.md`. Verify zero Critical/High issues and a clean
Git status.

- [ ] **Step 8: Commit final evidence**

```powershell
git add REVIEW_FOR_ZCODE.md artifacts docs/release/checklist.md
git commit -m "docs: record agent bridge v2 acceptance"
```

Only after this commit and ZCode approval may completion be reported to the
user.
