# Contributing to agent-bridge

Thanks for your interest in contributing!

## Getting started

```bash
git clone https://github.com/xyva-yuangui/agent-bridge.git
cd agent-bridge
python -m unittest discover -s tests -v
```

All 29 tests should pass before you start making changes.

## Development workflow

1. **Fork** the repository and create a feature branch
2. **Make changes** — keep them focused and atomic
3. **Add tests** — every new feature or bug fix should have test coverage
4. **Run the full suite** — `python -m unittest discover -s tests -v`
5. **Run syntax check** — `python -m compileall -q scripts tests`
6. **Submit a PR** with a clear description of what changed and why

## Code style

- Python 3.9+ compatible (no 3.10+ features without fallback)
- Standard library only — zero external dependencies
- Follow PEP 8 with 4-space indentation
- Use type hints for new public functions
- Keep functions focused and under 50 lines where possible
- Write docstrings for public functions

## Testing

Tests use Python's built-in `unittest` framework. No external test runners needed.

```bash
# Run all tests
python -m unittest discover -s tests -v

# Run a specific test file
python -m unittest tests.test_bridge -v

# Run a specific test
python -m unittest tests.test_bridge.LifecycleTests.test_send_claim_done -v
```

## Project structure

```
scripts/           # Core Python code
  bridge.py        # CLI and board logic
  bridge_mcp.py    # MCP server (JSON-RPC over stdio)
  notify_windows.ps1  # Windows notification helper
tests/             # Test suite (29 tests)
install.sh         # POSIX installer (macOS, Linux)
install.ps1        # Windows PowerShell installer
SKILL.md           # Agent skill definition
```

## Cross-platform considerations

- File locking: `fcntl` on Unix, `O_CREAT|O_EXCL` on Windows
- Paths: use `Path.home()`, `os.path.normcase` for Windows case-insensitivity
- Subprocess: use `creationflags` on Windows, `start_new_session` on Unix
- Notifications: `osascript` on macOS, `notify-send` on Linux, `notify_windows.ps1` on Windows

## Reporting bugs

Use [GitHub Issues](https://github.com/xyva-yuangui/agent-bridge/issues). Include:
- Your OS and Python version
- Steps to reproduce
- Expected vs actual behavior
- Output of `bridge doctor --strict`

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
