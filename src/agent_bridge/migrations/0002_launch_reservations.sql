CREATE TABLE launch_reservations (
  idempotency_key TEXT PRIMARY KEY,
  agent_name TEXT NOT NULL REFERENCES agents(name),
  task_id TEXT REFERENCES tasks(id) ON DELETE SET NULL,
  workspace TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('reserved', 'started', 'failed')),
  pid INTEGER,
  reserved_at TEXT NOT NULL,
  started_at TEXT,
  expires_at TEXT NOT NULL,
  error TEXT
);

CREATE INDEX idx_launch_reservations_agent_expiry
ON launch_reservations(agent_name, expires_at);
