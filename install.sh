#!/usr/bin/env bash
set -eu

auto=0
agent=""
identity=""
python_requested=""
wake_cmd=""
uninstall=0
purge_data=0
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
source_package="${source_root}/src"
export PYTHONPATH="${source_package}${PYTHONPATH:+:${PYTHONPATH}}"
if ! "$python_path" -m pip install --disable-pip-version-check --no-deps --user "$source_root"; then
  echo "Package installation failed; using this checkout via PYTHONPATH." >&2
fi

bridge_args=(-m agent_bridge.cli)
if [ "$uninstall" -eq 1 ]; then
  bridge_args+=(uninstall --home "$install_root")
  if [ -n "$agent" ]; then bridge_args+=(--agent "$agent"); fi
  if [ "$purge_data" -eq 1 ]; then bridge_args+=(--purge-data); fi
else
  bridge_args+=(setup --home "$install_root")
  if [ "$auto" -eq 1 ] || [ -z "$agent" ]; then bridge_args+=(--auto); fi
  if [ -n "$agent" ]; then bridge_args+=(--agent "$agent"); fi
fi
if [ -n "$identity" ] || [ -n "$wake_cmd" ]; then
  echo "--as and --wake-cmd are legacy options; setup uses local host scope only." >&2
fi
"$python_path" "${bridge_args[@]}"
printf '%s\n' "OK agent-bridge lifecycle completed"
