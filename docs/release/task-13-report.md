# Task 13 release-surface verification

## Migrated regression coverage

The retired v1 JSON-board tests were replaced, not deleted:

- `tests/test_bridge.py` now covers v2 SQLite-backed lifecycle authorization,
  revision-conflict protection, explicit cleanup scope, and one-time host ACK
  evidence.
- `tests/test_concurrency.py` runs 30 concurrent public v2 CLI sends and
  verifies every durable task is present.
- `tests/test_e2e.py` exercises the complete v2 CLI flow through question,
  answer, changes-requested, claim, and completion with deduplicated
  artifacts.
- The legacy MCP version expectation now asserts the package protocol version
  `2.0.0`.

These replace v1-only board files, portable locks, and false wake/ack semantics
with their v2 SQLite, transaction, and delivery-proof equivalents.

## Local package evidence (2026-07-24)

PyPI installation via `py -3 -m pip install --user build setuptools wheel`
was blocked by TLS EOF failures. The official PyPI `setuptools 82.0.1` and
`wheel 0.47.0` wheels were fetched with the platform TLS fallback and installed
user-scoped with `--no-deps`; the setuptools PEP 517 backend then produced the
following clean local artifacts:

| Artifact | SHA-256 |
| --- | --- |
| `agent_bridge-2.0.0-py3-none-any.whl` | `341C625551F24CA766CAF8626CDE304BECBBEAEFBE3445C7BF86AEBA5C71B6F6` |
| `agent_bridge-2.0.0.tar.gz` | `49F81F922B1B7C1E2B7FEEC1417DBAEF907304AAB265F5DF6E89E18FA2068AE4` |

The wheel and sdist were inspected for the Codex, Claude, Reasonix, and ZCode
integration manifests. A new virtual environment installed the wheel with
`--no-deps` and no `PYTHONPATH`; `bridge --version`, help, `setup --dry-run`,
and `doctor` ran successfully. Doctor honestly reported the unavailable native
Windows helper as degraded while database integrity and schema checks passed.

Generated `build/`, `src/agent_bridge.egg-info/`, and `artifacts/local-dist/`
are intentionally removed after inspection; release CI regenerates and
publishes signed evidence from a clean tag.
