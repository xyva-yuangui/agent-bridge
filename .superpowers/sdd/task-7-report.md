# Task 7 report

## RED

- Added `tests/unit/test_adapters.py` and `tests/installers/test_host_config.py` before the adapter implementation.
- `PYTHONPATH=src py -3 -m unittest tests.unit.test_adapters tests.installers.test_host_config -v` failed first with `ModuleNotFoundError: No module named 'agent_bridge.adapters'`.
- Added the no-fabricated-ACK capability test before its guard; it failed because a `can_ack=False` adapter still invoked the callback.
- Added direct legacy ZCode uninstall coverage before its cleanup; it failed because `agent-bridge@local` remained enabled.

## GREEN

- Implemented the ABC, typed values, canonical alias registry, four adapters, manifests, fixtures, ACK contract, and safe managed-config round trips.
- `PYTHONPATH=src py -3 -m unittest tests.unit.test_adapters tests.installers.test_host_config -v` passed: 15 tests.
- `PYTHONPATH=src py -3 -m compileall -q src tests integrations` completed successfully.
- `git diff --cached --check` completed successfully.

## Self-review

- Confirmed absent hosts return `DeliveryStatus.FAILED` from `notify_in_app` and do not invoke the acknowledgment callback.
- Confirmed all ACK callbacks carry host identity, task ID, integration version, and protocol version.
- Confirmed TOML fixtures preserve unrelated bytes and JSON fixtures restore exact original source when installed by this version; legacy managed JSON is repaired/removed without damaging unrelated JSON values.
- Confirmed manifests and runtime capability versions are tested together.

## Known scope boundary

- The existing full-suite command currently has unrelated v1-compatibility failures (23 errors and 3 failures), including missing `ensure_dirs` from `scripts/bridge.py`, legacy documentation/version assertions, and a Windows SQLite cleanup lock. The Task 7 targeted suite is green.

## Review-fix RED

- Replaced the adapter/config tests before reworking the implementation. The first run failed because `TaskAcknowledgement` and `marker_path` did not exist.
- The new explicit host-consumer-entrypoint test then failed with `ModuleNotFoundError: agent_bridge.adapters.integration`.
- Added a forged-card regression after implementation; it failed because a tampered card could promote its own version into a shared ACK.
- The red cases cover no local ACK from `notify_in_app`, atomic queued cards, missing consumer, forged/mismatched ACK rejection, stale directory detection, strict runtime capability types, TOML table ownership, and JSON concurrent edits.

## Review-fix GREEN

- `notify_in_app` now only atomically queues a bounded session-card file and returns `queued`; it does not receive or call an ACK callback.
- `TaskAcknowledgement` is produced by the host-consumer entrypoint and `acknowledge_integration` validates host, task, integration/protocol versions, and a per-delivery token before calling the shared callback.
- TOML installs named managed tables (`[mcp_servers.agent_bridge]` for Codex and `[agent_bridge]` for Reasonix); JSON uninstall removes only owned structured data and preserves concurrent user edits.
- Detection now requires a compatible installation marker rather than a directory.
- Fresh verification: 43 tests passed across adapter/config, delivery, terminal, and service-workflow suites; `compileall` passed.

## MCP consumer review-fix

- RED: the new subprocess JSON-RPC test failed for all four hosts because the old metadata-only entrypoint exited without serving protocol messages. A pending-card uninstall test also failed because host cards were left behind.
- GREEN: each installed adapter now registers a `python -m agent_bridge.adapters.integration serve` command and arguments. The stdio server implements initialize, tool discovery, bounded card list/read, and durable ACK. ACK writes `agent_acknowledged` through `BridgeService` with a unique delivery token, consumes the card once, and rejects replay.
- The host config write path now has a per-config exclusive lock and atomic fsync/replace write. Task IDs use a safe filename grammar; uninstall removes only regular cards from its own inbox.
- Fresh verification: 55 tests passed across adapters/config/MCP consumer/delivery/service/dispatcher suites plus `compileall` and `git diff --check`.

## Phase A host registrations

- RED: the parsed-config subprocess consumer test failed because Claude had no concrete `SessionStart` hook, Reasonix had no `[[plugins]]` registration, and ZCode had no registered local plugin bundle.
- GREEN: Codex keeps its real MCP table; Claude registers a concrete SessionStart command/argv; Reasonix registers `[[plugins]]` with command/args; ZCode installs and registers a copied local `plugin.json` bundle. The acceptance test now extracts and executes command/args from each installed config/bundle.

## Phase B reliability hardening

### RED

- Added regression coverage before the implementation changes for a symlinked session-card inbox, detection after deleting the installation artifact or mutating managed config, an external JSON config writer racing install, malformed JSON-RPC request shapes, durable ACK replay rejection, and a restart after the database ACK committed but before the card was removed.
- The focused run failed as expected: no `installation_artifact_path`, symlinked inboxes queued cards outside their intended directory, `BridgeService` had no durable ACK-claim API, the consumer exited on invalid request shapes, and config atomic writes did not accept an expected snapshot for retry.

### GREEN

- Host-owned paths now reject traversal and symlinks before reads, writes, cleanup, plugin bundle removal, or card creation. Detection and health require a valid host marker, exact managed consumer configuration, and a bridge-owned installation receipt.
- The callback ACK path was removed. The consumer validates a card and calls the single `BridgeService.claim_host_acknowledgement` transaction; its unique idempotency key rejects replays. On restart, durable claims are used only to remove cards stranded by a crash after commit.
- JSON-RPC parsing validates version, params, and tool-argument object shapes and returns errors without terminating the server.
- Managed JSON and TOML configuration updates use optimistic re-read/retry around fsync-and-replace, preserving an external writer's edit when the snapshot changes.

### Verification

- `PYTHONPATH=src py -3 -m unittest tests.unit.test_adapters tests.installers.test_host_config tests.unit.test_mcp_consumer tests.unit.test_delivery tests.unit.test_terminals tests.integration.test_service_workflows tests.integration.test_dispatcher tests.integration.test_dispatcher_faults -v` — 70 tests passed.
- `py -3 -m compileall -q src tests integrations` — passed.
- `git diff --check` — passed.

## Phase B review follow-up

- Added migration 3 for hashed, one-time host delivery proofs. Cards are staged, the proof is registered before publication, and `BridgeService.acknowledge_integration` consumes the matching proof and writes ACK evidence in one transaction; forged direct calls and replays are rejected.
- Config updates now use Windows `ReplaceFileW` with a captured displaced file and retry from that exact external edit when it lands between comparison and swap. Unsupported platforms fail closed for existing-file CAS writes.
- Install rolls back only its managed config and receipt if receipt creation fails. All generated registrations now record `sys.executable` rather than `python`.
- Verification: 70 targeted adapter/config/MCP/store/service/dispatcher tests passed, plus the injected receipt-failure rollback test; `compileall` and `git diff --check` passed.

## Final concurrency and ownership follow-up

- Migration 4 supersedes prior unconsumed logical delivery proofs before registering a replacement; ACK consumption accepts only the active proof and refuses a second host ACK.
- Per-card cross-process locks serialize staging/proof registration/publication so replacement cards cannot race through one deterministic pending filename.
- ZCode records prior same-key registration values and bundle existence, restores only values it still owns, and only removes a bundle it created.
- Focused adapter/config/MCP/store/service verification passed (52 tests), as did `compileall` and `git diff --check`.
