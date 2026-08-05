#!/usr/bin/env bash
set -eu

auto=0
agent=""
identity=""
python_requested=""
wake_cmd=""
uninstall=0
purge_data=0
dev_source_fallback=0
install_root="${HOME}"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --auto) auto=1 ;;
    --agent) agent="$2"; shift ;;
    --as) identity="$2"; shift ;;
    --python) python_requested="$2"; shift ;;
    --wake-cmd) wake_cmd="$2"; shift ;;
    --uninstall) uninstall=1 ;;
    --purge-data) purge_data=1 ;;
    --dev-source-fallback) dev_source_fallback=1 ;;
    --install-root) install_root="$2"; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

source_root="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
resolve_python() {
  if [ -n "$python_requested" ]; then
    candidate="$python_requested"
    if [ -x "$candidate" ] && "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)'; then
      printf '%s\n' "$candidate"; return
    fi
  else
    for candidate in python3 python; do
      if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)'; then
        command -v "$candidate"; return
      fi
    done
  fi
  echo "Python 3.9 or newer was not found; pass --python." >&2
  exit 1
}

python_path="$(resolve_python)"

if [ "$auto" -eq 1 ]; then
  agents="codex claude reasonix zcode"
elif [ -n "$agent" ]; then
  agents="$agent"
elif [ -n "$identity" ]; then
  agents="$identity"
else
  echo "choose --auto or --agent codex|claude|reasonix|zcode" >&2
  exit 2
fi

# Never bootstrap during removal.  In particular, uninstall must not replace
# a user package or touch a shared interpreter merely to obtain an entrypoint.
if [ "$uninstall" -eq 1 ]; then
  package_importable=0
  user_owned_package=0
  if "$python_path" -c 'import agent_bridge.cli' 2>/dev/null; then package_importable=1; fi
  if "$python_path" -c 'import importlib.metadata as m, pathlib, site; d=m.distribution("agent-bridge"); root=pathlib.Path(d.locate_file("")).resolve(); user=pathlib.Path(site.USER_SITE).resolve(); raise SystemExit(0 if root == user or user in root.parents else 1)' 2>/dev/null; then
    user_owned_package=1
  fi
  if [ "$package_importable" -ne 1 ]; then
    owned_runtime="$install_root/.agent-bridge/skill/runtime"
    if [ ! -f "$owned_runtime/agent_bridge/cli.py" ]; then
      echo "Agent Bridge is not importable and its owned runtime is absent; refusing to install anything during uninstall." >&2
      exit 1
    fi
    runtime_program='import sys; sys.path.insert(0, sys.argv[1]); from agent_bridge.cli import main; raise SystemExit(main(sys.argv[2:]))'
    bridge_args=(-c "$runtime_program" "$owned_runtime")
  fi
  bridge_args+=(uninstall --home "$install_root")
  if [ -n "$agent" ]; then bridge_args+=(--agent "$agent"); fi
  if [ "$purge_data" -eq 1 ]; then bridge_args+=(--purge-data); fi
  "$python_path" "${bridge_args[@]}"
  # Only --user-installed distributions live below USER_SITE. pip uninstalls
  # the package's RECORD, so it cannot remove other distributions or a global
  # / virtualenv installation that this script does not own.
  shared_runtime="$install_root/.agent-bridge/runtime-receipt.json"
  if [ ! -f "$shared_runtime" ] && [ "$user_owned_package" -eq 1 ]; then
    "$python_path" -m pip uninstall --disable-pip-version-check --yes agent-bridge
  elif [ -f "$shared_runtime" ]; then
    echo "Retaining the shared Agent Bridge distribution because other host integrations remain." >&2
  else
    echo "Retaining Agent Bridge distribution outside this interpreter's user site." >&2
  fi
  printf '%s\n' "OK agent-bridge lifecycle completed"
  exit 0
fi

bootstrap_wheel="$source_root/bootstrap/agent_bridge-2.0.0-py3-none-any.whl"
bootstrap_metadata="$source_root/bootstrap/agent_bridge-2.0.0.bootstrap.json"
install_failed=0
if [ -f "$bootstrap_wheel" ]; then
  if [ ! -f "$bootstrap_metadata" ]; then
    echo "Offline bootstrap metadata is missing; use a complete release archive." >&2
    exit 1
  fi
  if ! "$python_path" -c 'import hashlib,json,os,sys; wheel, metadata=sys.argv[1:]; value=json.load(open(metadata, encoding="utf-8")); actual=hashlib.sha256(open(wheel,"rb").read()).hexdigest(); raise SystemExit(0 if value.get("version") == "2.0.0" and value.get("wheel") == os.path.basename(wheel) and value.get("sha256") == actual else 1)' "$bootstrap_wheel" "$bootstrap_metadata"; then
    echo "Offline bootstrap metadata does not match its wheel; use a complete verified release archive." >&2
    exit 1
  fi
  if ! "$python_path" -m pip install --disable-pip-version-check --no-index --no-deps --force-reinstall --user "$bootstrap_wheel"; then install_failed=1; fi
elif "$python_path" -c 'import setuptools.build_meta'; then
  if ! "$python_path" -m pip install --disable-pip-version-check --no-build-isolation --no-deps --force-reinstall --user "$source_root"; then install_failed=1; fi
else
  echo "Offline bootstrap wheel is missing and setuptools.build_meta is unavailable. Use a complete release archive containing bootstrap/agent_bridge-2.0.0-py3-none-any.whl." >&2
  exit 1
fi
if [ "$install_failed" -ne 0 ]; then
  if [ "$dev_source_fallback" -ne 1 ] && [ "${AGENT_BRIDGE_DEV_SOURCE_FALLBACK:-}" != "1" ]; then
    echo "Package installation failed. Fix pip or pass --dev-source-fallback for degraded checkout-only use." >&2
    exit 1
  fi
  echo "DEGRADED development fallback: importing this checkout through PYTHONPATH." >&2
  export PYTHONPATH="${source_root}/src${PYTHONPATH:+:${PYTHONPATH}}"
fi

portable_macos_app="$source_root/native/macos-universal2/AgentBridgeNotifier.app"
if [ -d "$portable_macos_app" ]; then
  export AGENT_BRIDGE_MACOS_NOTIFY_APP="$portable_macos_app"
elif [ "$(uname -s)" = "Darwin" ]; then
  echo "DEGRADED macOS native notifications: portable AgentBridgeNotifier.app is absent; terminal fallback remains available." >&2
fi
bridge_args+=(setup --home "$install_root")
if [ "$auto" -eq 1 ] || [ -z "$agent" ]; then bridge_args+=(--auto); fi
if [ -n "$agent" ]; then bridge_args+=(--agent "$agent"); fi
if [ -n "$identity" ] || [ -n "$wake_cmd" ]; then
  echo "--as and --wake-cmd are legacy options; setup uses local host scope only." >&2
fi
"$python_path" "${bridge_args[@]}"
printf '%s\n' "OK agent-bridge lifecycle completed"
