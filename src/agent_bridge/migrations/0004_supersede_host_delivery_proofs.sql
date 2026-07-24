ALTER TABLE host_delivery_proofs ADD COLUMN superseded_at TEXT;
CREATE UNIQUE INDEX idx_one_active_host_delivery_proof
ON host_delivery_proofs(task_id, host_identity, integration_version, protocol_version)
WHERE consumed_at IS NULL AND superseded_at IS NULL;
