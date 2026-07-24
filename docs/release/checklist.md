# Release checklist

This checklist distinguishes reproducible source/package verification from
real-machine platform acceptance. Do not publish only because CI is green.

## Before a tag

1. Confirm `git status --short` is empty and the version is consistent.
2. Run `python -m unittest discover -s tests -v` and
   `python -m compileall -q src scripts tests` on supported Python 3.9–3.13.
3. Run installer fixture tests and package install smoke tests without a source
   `PYTHONPATH`.
4. Build a locked Windows Rust helper, verify its hash/size metadata, and
   build the macOS universal2 Swift helper with its size check.
5. On real-machine Windows, verify notification registration, persistence,
   View/Claim/Snooze actions, the four host integrations, and fallback paths.
6. On real-machine macOS Intel and Apple Silicon, verify notification
   authorization, actions, persistence, `codesign`, `spctl`, and notarization.
   macOS CI source checks are not real-machine notification acceptance.

## Artifacts

The primary release asset is exactly one cross-platform
`agent-bridge-<version>-portable.zip`, built after the Windows helper and the
signed/notarized macOS universal2 app are available. It must contain the
offline bootstrap wheel, Windows helper, macOS app as an internal component,
both install scripts, LICENSE, both READMEs, all integration manifests,
installation docs, `SHA256SUMS.txt`, and `inventory.json`. Extract it under a
path containing spaces and non-ASCII characters, then run the appropriate
install script without a checkout or `PYTHONPATH`. The wheel and sdist are
supplementary verification artifacts, not platform-specific primary installers.
Do not assemble a Windows-only archive and label it as this release asset: the
aggregate macOS GitHub Actions job is responsible for the complete dual-platform
ZIP once it has the universal2 `.app`.
On macOS, confirm setup installs the receipted app bundle under
`~/.agent-bridge/native/macos-universal2`, records its hash/signing assessment
and fixed bridge activation argv, reports unsigned builds as signing-degraded
only, and removes only the hash-owned bundle on uninstall.

Create final SHA-256 checksums and SPDX/CycloneDX SBOMs after the portable ZIP
is built. Install the bootstrap wheel into a new virtual environment, run
`bridge --version`, `bridge --help`, `bridge doctor`, and `bridge setup
--dry-run`, then inspect its manifests for all four integration templates and
migration SQL.

When signing is requested, require the signing identity and (for macOS)
notarization profile. Never echo secret values. Verify signatures and checksum
each artifact after signing. Upload artifacts only after those checks succeed;
publication remains a separate explicit repository action.

## Known platform boundary

Windows release evidence is required for a Windows native-notification claim.
macOS Intel and Apple Silicon real-machine evidence is required for a macOS
native-notification claim. Without matching evidence, document the shipped
state as source/CI verified with terminal fallback, not as verified native
support.
