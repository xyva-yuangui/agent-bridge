# Security policy

## Reporting a vulnerability

Please do not file public issues for a suspected vulnerability. Send a concise,
reproducible report to the repository maintainers through the private security
advisory facility when it is enabled; otherwise ask a maintainer for a private
reporting channel. Include affected version, platform, impact, and minimal
reproduction. We will acknowledge reports within seven days and coordinate a
fix and disclosure timeline with the reporter.

## Supported versions

Only the latest `2.x` release receives security fixes. This project is local
first: it has no network listener, cloud synchronization, or telemetry. Treat
host configuration files, local SQLite task data, and notification activation
arguments as sensitive local inputs.

## Release integrity

Verify the published SHA-256 checksum and SBOM before installing a release.
Windows and macOS helper binaries are separately built and their signing or
notarization status is release evidence, not an implicit runtime guarantee.
