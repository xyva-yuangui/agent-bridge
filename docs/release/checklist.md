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

Build the wheel, sdist, and portable archive from the clean release tag. Create
SHA-256 checksums and an SPDX SBOM. Install the wheel into a new virtual
environment, run `bridge --version`, `bridge --help`, `bridge doctor`, and
`bridge setup --dry-run`, then inspect the wheel and sdist manifests for all
four integration templates and migration SQL.

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
