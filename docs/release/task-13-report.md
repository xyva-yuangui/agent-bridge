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

## Release pipeline and package-only acceptance (2026-07-24)

`.github/workflows/release.yml` now separates validation, Windows, macOS,
source-distribution, and aggregate publication jobs.  The Windows job runs the
locked Rust build and `verify-release.ps1`, stages the verified helper into the
package, retags a `win_amd64` wheel, installs that wheel with `--no-deps`, and
publishes a portable ZIP.  The macOS job builds a universal2 application,
checks `file` and both `lipo` architectures, and signs/notarizes it **before**
staging and packaging.  Tag runs require signing credentials; an explicitly
selected `workflow_dispatch sign=false` run is visibly marked as an unsigned
manual artifact.

The aggregate job refuses a missing platform artifact, produces and verifies
`SHA256SUMS.txt`, and emits both SPDX and CycloneDX SBOMs only after platform
staging/signing has completed.  It is the sole publisher and attestation
subject.

`tests/installers/test_package_only_install.py` proves the normal Windows
installer invocation (without the development-fallback switch) installs into a
temporary user site, has no `PYTHONPATH`, imports `agent_bridge` from that site
rather than `src/`, and starts the real host MCP entrypoint.  The test is
skipped only on machines without PowerShell or local setuptools; the Windows
CI matrix supplies both.

Using the locally available Python 3.14 setuptools/wheel toolchain, this
revision was also built with `pip wheel --no-build-isolation --no-deps`, retagged
to `py3-none-win_amd64` by `scripts/retag_wheel.py`, and installed into a fresh
virtual environment with `--force-reinstall --no-deps` and no `PYTHONPATH`.
`bridge --version` returned `2.0.0`; `bridge setup --dry-run --auto` completed;
and the imported module origin was the venv's `site-packages`, not `src/`.
The generated wheel, build directories, egg-info, and virtual environment were
removed immediately after the check.

The Chinese README was rewritten as valid UTF-8 after detecting mojibake.  It
now documents the explicit development fallback, four-host support, and the
real-machine macOS release requirement.
