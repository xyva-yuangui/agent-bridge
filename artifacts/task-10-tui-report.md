# Task 10 — On-demand TUI

Implemented the dependency-free `bridge tui` dashboard.

- Added pure model, renderer, platform input adapters, and controller under
  `src/agent_bridge/tui/`.
- Added a paginated `BridgeService` UI API for board pages, agent presence,
  delivery evidence, retrying delivery, and safe terminal opening. The TUI
  does not query the store directly.
- `bridge tui --project <id>` uses full-screen VT output only for a supported
  interactive terminal. Redirected and explicit non-VT output uses a compact
  plain table.
- The interactive session refreshes at 250 ms only while open. It supports
  selection, view, claim, retry, open terminal, filter, and quit; Ctrl-C
  returns 130 and terminal adapters restore their saved modes in `finally`.
- Rendering bounds subject, body, and action-result output by display-cell
  width, including wide Unicode characters.

Verification performed on 2026-07-24:

```text
PYTHONPATH=src py -3 -m unittest \
  tests.unit.test_tui_model tests.unit.test_tui_render \
  tests.platform.test_tui_inputs tests.unit.test_tui_controller \
  tests.unit.test_tui_service_api tests.integration.test_service_workflows \
  tests.integration.test_cli_v2 -v

Ran 47 tests ... OK

PYTHONPATH=src py -3 -m compileall -q src
```

An additional full `unittest discover -s tests` attempt reached legacy v1
tests that fail independently of the v2 TUI surface (their tests expect the
removed v1 file-based bridge and stale documentation contract). The scoped v2
service/CLI regressions above are green.

## Review follow-up

Addressed the Task 10 review findings:

- Windows input now waits up to the supplied refresh interval when no key is
  ready; VT output requires an explicit capability, a non-dumb `TERM`, or the
  Windows console VT mode bit. Unset `TERM` is compact fallback.
- All display data is sanitized before model/rendering. C0/C1 controls,
  CSI, OSC, tabs, and line breaks cannot create terminal controls.
- Claim/retry/open/view errors are sanitized, bounded notices and do not end
  the interactive session. The alternate screen and terminal mode restore on
  normal exit, exceptions, Ctrl-C, and SIGTERM.
- Delivery evidence aggregates all durable attempts with
  `aggregate_delivery`, retaining the strongest proof and total attempt count.
- Dashboard loading follows bounded pages (cap 300), search covers loaded
  pages, and the controller accepts only a CLI-supplied dispatch tick callback
  (initial, refresh, and post-mutation).

Follow-up verification: 49 focused TUI/service/CLI/dispatcher tests passed,
plus `py -3 -m compileall -q src`.
