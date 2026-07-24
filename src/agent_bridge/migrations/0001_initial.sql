CREATE TABLE schema_migrations (
  version INTEGER PRIMARY KEY,
  checksum TEXT NOT NULL,
  applied_at TEXT NOT NULL
);

CREATE TABLE projects (
  id TEXT PRIMARY KEY,
  path TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE agents (
  name TEXT PRIMARY KEY,
  capabilities_json TEXT NOT NULL DEFAULT '[]',
  integration_health TEXT NOT NULL DEFAULT 'unknown',
  last_seen TEXT,
  execution_policy TEXT NOT NULL DEFAULT 'manual' CHECK (execution_policy IN ('manual', 'prompt', 'auto')),
  launch_argv_json TEXT NOT NULL DEFAULT '[]',
  terminal_preference TEXT NOT NULL DEFAULT 'auto',
  max_concurrency INTEGER NOT NULL DEFAULT 1 CHECK (max_concurrency > 0),
  cooldown_seconds INTEGER NOT NULL DEFAULT 30 CHECK (cooldown_seconds >= 0),
  workspace_allowlist_json TEXT NOT NULL DEFAULT '[]'
);

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
  revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE task_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  revision INTEGER NOT NULL,
  kind TEXT NOT NULL,
  actor TEXT REFERENCES agents(name),
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  UNIQUE (task_id, revision, kind)
);

CREATE TABLE task_dependencies (
  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  depends_on_task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  PRIMARY KEY (task_id, depends_on_task_id),
  CHECK (task_id <> depends_on_task_id)
);

CREATE TABLE task_artifacts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  path TEXT NOT NULL,
  sha256 TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  UNIQUE (task_id, path)
);

CREATE TABLE delivery_attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  channel TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN (
    'queued','dispatching','os_posted','plugin_delivered','viewed',
    'launch_started','agent_acknowledged','claimed','retry_wait','failed'
  )),
  attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  error TEXT,
  idempotency_key TEXT UNIQUE
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

CREATE TABLE dispatcher_leases (
  name TEXT PRIMARY KEY,
  owner TEXT NOT NULL,
  acquired_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);

CREATE TABLE notification_mappings (
  notification_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  action TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE metadata (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE import_ledger (
  source_hash TEXT PRIMARY KEY,
  source_path TEXT NOT NULL,
  imported_at TEXT NOT NULL,
  record_count INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_tasks_project_updated ON tasks(project_id, updated_at DESC);
CREATE INDEX idx_tasks_assignee_state_updated ON tasks(assignee, state, updated_at DESC);
CREATE INDEX idx_task_events_task_created ON task_events(task_id, created_at);
CREATE INDEX idx_task_dependencies_dependency ON task_dependencies(depends_on_task_id);
CREATE INDEX idx_task_artifacts_task ON task_artifacts(task_id);
CREATE INDEX idx_delivery_attempts_task_status ON delivery_attempts(task_id, status);
CREATE INDEX idx_outbox_due_at ON outbox(due_at) WHERE completed_at IS NULL;
CREATE INDEX idx_outbox_idempotency_key ON outbox(idempotency_key);
CREATE INDEX idx_dispatcher_leases_expires_at ON dispatcher_leases(expires_at);
CREATE INDEX idx_notification_mappings_task ON notification_mappings(task_id);
