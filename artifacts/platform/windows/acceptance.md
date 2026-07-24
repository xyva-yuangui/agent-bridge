# Windows acceptance evidence

Date: 2026-07-24
Host: Windows (normal, non-administrator user session), Python 3.14.2
Candidate: `ce229dbfbae415f270c16db24f690976b294c46b` (Agent Bridge 2.0.0)

This report distinguishes operating-system acceptance from a visual UI
observation. No claim below means that a human clicked a Notification Center
entry; the host session was automated.

The previously reported production blockers have been remediated. The full
Windows suite exercises the real dispatcher-to-HostAdapter path for Codex,
Claude Code, Reasonix, and ZCode; host-bound MCP identities; Claude SessionStart
card consumption and durable acknowledgement; manual-recipient retry behavior;
fixed notification activation identity/actions; and concurrent Windows config
preservation.

## One installer invocation and four desktop clients

The all-host acceptance creates an isolated CJK-and-space user home and four
ordinary host configurations. One `install.ps1 -Auto` invocation installs the
bundled offline wheel under an isolated `PYTHONUSERBASE` with no `PYTHONPATH`,
registers all four managed integrations, verifies imports from `site-packages`,
starts every exact receipt entrypoint outside the checkout, repairs managed
blocks, and uninstalls them while preserving unrelated TOML/JSON content. No
administrator elevation is used.

The portable ZIP test assembles a deterministic cross-platform fixture ZIP,
extracts it beneath a CJK-and-space path, and installs from the bundled wheel.
It is not the final release ZIP because this Windows host cannot produce the
required signed universal2 macOS app. The aggregate release workflow is the
only producer of `agent-bridge-<version>-portable.zip`.

These paths are included in the final comprehensive result:
`Ran 252 tests in 158.694s`, `OK`.

## Production collaboration delivery

Automated acceptance verifies:

- one setup configures detected Codex, Claude Code, Reasonix, and ZCode hosts;
- a real dispatcher burst routes a session card through each recipient's
  owned HostAdapter integration rather than a test-only channel;
- each host consumer binds its configured identity, rejects caller actor
  overrides, and records acknowledgement only after consuming a card;
- the Claude SessionStart hook consumes queued cards, adds bounded context,
  records `host:claude agent_acknowledged`, and removes the card;
- recipients with no applicable delivery channel remain due for retry rather
  than being falsely completed;
- multiple optional channels increment one outbox attempt per burst, retain
  durable evidence across crash recovery, and avoid duplicate effects;
- per-agent launch policy is read from the public `agent.json` profile and
  survives repair.

## Native Windows Toast protocol and action routing

Checked-in baseline helper:

```text
src/agent_bridge/native/windows-x86_64/agent-bridge-windows-notify.exe
SHA-256 C0480D0F10DDF549E8F70B11094DBC29690E63202DE24F33B326E1C20661B22A
size     536,576 bytes (< 5 MiB)
```

The baseline helper's per-user register/status/post/action-uri/unregister
lifecycle was exercised. The dispatcher persisted only the opaque native ID.
View resolves the stored task and opens a safe terminal, Claim acts as the
stored assignee and requests dispatch, and Snooze creates a durable future-due
outbox item. The activation URI never supplies actor authority or shell text.
Owned-helper discovery works immediately after install without a refreshed
environment.

The helper's `os_posted` response is OS acceptance of a toast. Notification
Center persistence/history and physical View/Claim/Snooze clicks were not
visually inspected and remain manual release checks.

Rust 1.97.1 and rustfmt were installed, and
`cargo fmt --manifest-path native/windows-notify/Cargo.toml -- --check` passed.
A local locked Rust rebuild could not link because the host lacks Visual C++
`link.exe`. Therefore the checked-in helper was used only for baseline runtime
testing. The latest protocol/shortcut ownership and rollback source must be
rebuilt as x86-64 MSVC, Authenticode-signed, verified, and substituted by the
Windows release job before publication. Windows ARM64 is not a native target
in 2.0.0 and must be reported as degraded/unsupported.

## Reliability, portability, security, and performance

The final comprehensive suite passed all 252 tests in 158.694 seconds,
including GBK-safe CLI/MCP transport, v1 migration/export, SQLite concurrency,
fault injection, delivery deduplication, terminal safety, TUI console
restoration, scoped uninstall, access-control application, and transactional
native upgrade rollback.

Measured P95s:

- create: `0.988 ms`
- inbox: `1.828 ms`
- idle dispatch probe: `0.009 ms`
- TUI projection: `2.005 ms`
- documented CI multiplier: `2.0`

The TUI and dispatcher are bounded, on-demand processes. There is no default
resident daemon, network listener, web dashboard, or telemetry. Snooze is
durable and time-gated, but without a resident scheduler it re-dispatches on
the next bridge activity after its due time.

Bootstrap-wheel reproducibility
(`08ABF35E5CEFED024F0F183EC53B0F9247C0BE812A7E3B1E553FA774F51E490A`),
`compileall`, rustfmt, and `git diff --check` passed. ZCode must independently
rerun the review and platform release gates in `REVIEW_FOR_ZCODE.md`; macOS
real-machine UI, the newly rebuilt/signed Windows helper, and the final
dual-platform ZIP remain release gates rather than locally completed claims.
