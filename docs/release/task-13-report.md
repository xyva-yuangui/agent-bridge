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

## Offline bootstrap and cleanup regression fixes (2026-07-24)

The repository now tracks `bootstrap/agent_bridge-2.0.0-py3-none-any.whl` and
its SHA-256/source inventory metadata.  `scripts/bootstrap_wheel.py --check`
validates the portable wheel's version, RECORD, packaged source/resource hashes
and native helper; CI and tag validation execute that check.  The Windows and
shell installers prefer this wheel with `--no-index --no-deps --force-reinstall`
and therefore do not need build isolation, a network connection, or
`setuptools.build_meta`.  A checkout without that release payload can build
from source only when its backend is already available; otherwise it explains
that a complete release archive is required.

The compatibility runtime now copies the wheel-owned `bridge.py`,
`bridge_mcp.py`, and `notify_windows.ps1` bootstrap resources alongside the
runtime package.  The package-only installer test proves the normal installer
path, `site-packages` origin, no `PYTHONPATH`, and the real MCP entrypoint.

The 30-send concurrency regression now uses the synchronous public service
path, asserts all task and uncompleted-outbox rows, checks that no dispatcher
lease remains, and repeats five times.  This preserves the concurrency/outbox
coverage without detached dispatcher processes retaining a temporary SQLite
handle after the test finishes.

## Ephemeral signing and deterministic archive refinements (2026-07-24)

Tag releases (and manual `sign=true` runs) now require a base64 Developer ID
P12, its password, the signing identity and team, and Apple ID/app-specific
password/team credentials.  The macOS job masks each secret, creates an
ephemeral keychain, imports the private key with the codesign partition list,
verifies the identity, stores a `notarytool` profile in that keychain, then
signs/notarizes/staples **before** staging the application.  An `always()`
cleanup deletes the temporary credential/profile, keychain, and P12 before
wheel packaging.  Explicit `workflow_dispatch sign=false` remains the sole
unsigned path.

`retag_wheel.py` now writes sorted, fixed-timestamp, normalized-permission ZIP
entries with fixed compression and ordered RECORD data.  Its test retags the
same input twice and requires identical SHA-256 output; the bootstrap wheel
test rebuilds twice when the local backend is available.  The all-four-host
acceptance test now uses one normal offline-bootstrap install with an isolated
`PYTHONUSERBASE` and no `PYTHONPATH`, verifies module origin, launches every
receipt outside the checkout, then repairs and uninstalls.

## ZIP-first portable release (2026-07-24)

The aggregate release job now makes exactly one cross-platform primary asset:
`agent-bridge-2.0.0-portable.zip`.  It is constructed only after downloading
the locked Windows helper and signed/notarized macOS universal2 app.  Its
deterministic contents are the offline bootstrap wheel, Windows executable,
macOS `.app` as an internal component, both install scripts, LICENSE, bilingual
READMEs, integration manifests, installation docs, an inventory, and checksums.
Platform wheels, sdist, final checksum file, and SBOMs remain supplementary
verification artifacts; the workflow creates no macOS-only DMG/PKG installer.

`scripts/build_portable_zip.py` writes sorted fixed-time ZIP entries with
normalized permissions and validates the internal inventory/checksum manifest.
Its regression test builds byte-identical archives, checks the exact required
paths, and extracts the ZIP under a CJK/space path to run `install.ps1` from
the bundled wheel and native helper without a checkout.
