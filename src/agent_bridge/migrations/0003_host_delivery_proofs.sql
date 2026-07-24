CREATE TABLE host_delivery_proofs (
  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  host_identity TEXT NOT NULL,
  integration_version TEXT NOT NULL,
  protocol_version INTEGER NOT NULL,
  token_sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL,
  consumed_at TEXT,
  PRIMARY KEY (task_id, host_identity, token_sha256)
);

CREATE INDEX idx_host_delivery_proofs_unconsumed
ON host_delivery_proofs(task_id, host_identity, consumed_at);
