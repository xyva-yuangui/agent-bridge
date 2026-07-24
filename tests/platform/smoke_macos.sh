#!/usr/bin/env sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
export PYTHONPATH="$root/src"
cd "$root"

# Protocol/source smoke is usable on every macOS runner; real notification
# permission and signed-helper checks remain platform-release requirements.
python3 -m unittest tests.platform.test_macos_notify_protocol tests.platform.test_macos_notify_source tests.platform.test_tui_inputs -v
