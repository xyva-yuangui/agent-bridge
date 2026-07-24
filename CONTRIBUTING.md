# Contributing to Agent Bridge

Thank you for improving the local-first coordination surface.

## Development

Use Python 3.9 through 3.13-compatible syntax and the standard library for the
runtime. Before changing behavior, add a focused failing `unittest`, then make
it pass. Run:

```bash
python -m unittest discover -s tests -v
python -m compileall -q src scripts tests
git diff --check
```

Do not add a resident service, network listener, telemetry, or a claim of
platform support that lacks the required real-machine evidence.

## Adapter contributions

An adapter must be registered in `agent_bridge.adapters.ADAPTER_TYPES`, provide
a versioned manifest under `integrations/`, use contained atomic task cards,
and make acknowledgement explicit. Preserve unrelated host configuration and
write tests for detection, install, repair, uninstall, degradation, and
acknowledgement. A terminal fallback is preferable to an unverified native
panel claim.

## Documentation and pull requests

Update English and Simplified Chinese user documentation, migration guidance,
and the release checklist when behavior changes. Explain platform evidence and
known degradation in the pull request. By contributing, you agree that your
contribution is licensed under Apache-2.0.
