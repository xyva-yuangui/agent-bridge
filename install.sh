#!/usr/bin/env bash
set -eu

auto=0
agent=""
identity=""
python_requested=""
wake_cmd=""
uninstall=0
install_root="${HOME}"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --auto) auto=1 ;;
    --agent) agent="$2"; shift ;;
    --as) identity="$2"; shift ;;
    --python) python_requested="$2"; shift ;;
    --wake-cmd) wake_cmd="$2"; shift ;;
    --uninstall) uninstall=1 ;;
    --install-root) install_root="$2"; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

source_root="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
bridge_home="${install_root}/.agent-bridge"
skill_home="${bridge_home}/skill"
launcher_home="${install_root}/.local/bin"

resolve_python() {
  if [ -n "$python_requested" ]; then
    candidates="$python_requested"
  else
    candidates="python3 python"
  fi
  for candidate in $candidates; do
    if command -v "$candidate" >/dev/null 2>&1; then
      resolved="$(command -v "$candidate")"
      if "$resolved" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)'; then
        printf '%s\n' "$resolved"
        return
      fi
    fi
  done
  echo "Python 3.9 or newer was not found; pass --python." >&2
  exit 1
}

install_shared() {
  python_path="$1"
  stage="${bridge_home}/.skill-stage-$$"
  mkdir -p "${stage}/scripts" "$launcher_home" "${install_root}/.agents/skills"
  # Copy bridge.py, bridge_mcp.py, notify_windows.ps1, and future helpers together.
  cp -R "${source_root}/scripts/." "${stage}/scripts/"
  for name in SKILL.md README.md README.zh-CN.md; do
    if [ -f "${source_root}/${name}" ]; then
      cp "${source_root}/${name}" "${stage}/${name}"
    fi
  done
  if [ -e "$skill_home" ]; then
    mv "$skill_home" "${bridge_home}/.skill-backup-$$"
  fi
  mv "$stage" "$skill_home"
  rm -rf "${bridge_home}/.skill-backup-$$"
  printf '#!/usr/bin/env sh\nexec "%s" "%s/scripts/bridge.py" "$@"\n' "$python_path" "$skill_home" > "${launcher_home}/bridge"
  chmod +x "${launcher_home}/bridge" "${skill_home}/scripts/bridge.py" "${skill_home}/scripts/bridge_mcp.py"
  rm -rf "${install_root}/.agents/skills/agent-bridge"
  ln -s "$skill_home" "${install_root}/.agents/skills/agent-bridge"
}

register_agent_profile() {
  name="$1"
  python_path="$2"
  "$python_path" - "$bridge_home" "$name" "$wake_cmd" <<'PY'
import json
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path

home = Path(sys.argv[1])
name = sys.argv[2]
wake = sys.argv[3]
skills = {
    "codex": ["architecture", "hard-reasoning", "complex-impl", "orchestrate"],
    "claude": ["frontend", "ui", "writing", "analysis"],
    "reasonix": ["review", "plan", "headless", "refactor"],
    "zcode": ["review", "testing", "implementation", "acceptance"],
}[name]
path = home / "agents" / name / "agent.json"
old = {}
if path.exists():
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        old = {}
profile = {
    "name": name,
    "skills": skills,
    "strengths": ", ".join(skills),
    "last_seen": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
}
if wake:
    profile["wake_argv"] = shlex.split(wake)
elif isinstance(old.get("wake_argv"), list):
    profile["wake_argv"] = old["wake_argv"]
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
PY
}

set_managed_block() {
  path="$1"
  marker="$2"
  body="$3"
  python_path="$4"
  "$python_path" - "$path" "$marker" "$body" <<'PY'
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
name = sys.argv[2]
body = sys.argv[3]
start = f"# >>> agent-bridge:{name} >>>"
end = f"# <<< agent-bridge:{name} <<<"
text = path.read_text(encoding="utf-8") if path.exists() else ""
text = re.sub(
    rf"(?ms)^{re.escape(start)}.*?^{re.escape(end)}\s*",
    "",
    text,
)
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(
    text.rstrip() + "\n\n" + start + "\n" + body + "\n" + end + "\n",
    encoding="utf-8",
)
PY
}

configure_codex() {
  python_path="$1"
  body="[mcp_servers.agent_bridge]
command = '${python_path}'
args = ['${skill_home}/scripts/bridge_mcp.py', '--as', 'codex']
startup_timeout_sec = 15
tool_timeout_sec = 60"
  set_managed_block "${install_root}/.codex/config.toml" "codex-mcp" "$body" "$python_path"
  set_managed_block "${install_root}/AGENTS.md" "directive" "At the start of every turn, call agent-bridge status and inbox and handle pending tasks." "$python_path"
}

configure_json_hook() {
  path="$1"
  name="$2"
  python_path="$3"
  "$python_path" - "$path" "$name" "$skill_home" "$python_path" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
identity = sys.argv[2]
skill = Path(sys.argv[3])
python = sys.argv[4]
root = {}
if path.exists():
    root = json.loads(path.read_text(encoding="utf-8"))
hooks = root.setdefault("hooks", {})
entries = [
    entry
    for entry in hooks.get("UserPromptSubmit", [])
    if ".agent-bridge" not in json.dumps(entry)
]
entries.append({
    "matcher": "",
    "hooks": [{
        "type": "command",
        "command": f'"{python}" "{skill / "scripts" / "bridge.py"}" --as {identity} status --oneliner',
        "timeout": 10,
    }],
})
hooks["UserPromptSubmit"] = entries
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(root, indent=2) + "\n", encoding="utf-8")
PY
}

configure_claude() {
  configure_json_hook "${install_root}/.claude/settings.json" "claude" "$1"
}

configure_reasonix() {
  python_path="$1"
  directive="${install_root}/.reasonix/agent-bridge-directive.md"
  mkdir -p "$(dirname "$directive")"
  printf '%s\n' "At the start of every turn, run agent-bridge status and inbox." > "$directive"
  body="[agent]
system_prompt_file = '${directive}'

[[plugins]]
name = 'agent-bridge'
command = '${python_path}'
args = ['${skill_home}/scripts/bridge_mcp.py', '--as', 'reasonix']

[sandbox]
allow_write = ['${bridge_home}']"
  set_managed_block "${install_root}/.reasonix/config.toml" "reasonix" "$body" "$python_path"
}

configure_zcode() {
  python_path="$1"
  plugin_root="${install_root}/.zcode/cli/plugins/cache/local/agent-bridge/1.3.0"
  mkdir -p "${plugin_root}/.zcode-plugin" "${plugin_root}/hooks"
  printf '%s\n' '{"name":"agent-bridge","version":"1.3.0","description":"Local cross-agent coordination and task delivery.","author":{"name":"agent-bridge contributors"},"license":"MIT"}' > "${plugin_root}/.zcode-plugin/plugin.json"
  "$python_path" - "$plugin_root" "$skill_home" "$python_path" "${install_root}/.zcode/cli/config.json" <<'PY'
import json
import sys
from pathlib import Path

plugin = Path(sys.argv[1])
skill = Path(sys.argv[2])
python = sys.argv[3]
config_path = Path(sys.argv[4])
hooks = {
    "hooks": {
        "UserPromptSubmit": [{
            "matcher": "*",
            "hooks": [{
                "type": "command",
                "command": f'"{python}" "{skill / "scripts" / "bridge.py"}" --as zcode status --oneliner',
                "async": False,
            }],
        }],
    },
}
(plugin / "hooks" / "hooks.json").write_text(
    json.dumps(hooks, indent=2) + "\n",
    encoding="utf-8",
)
config = {}
if config_path.exists():
    config = json.loads(config_path.read_text(encoding="utf-8"))
config.setdefault("plugins", {}).setdefault("enabledPlugins", {})["agent-bridge@local"] = True
config_path.parent.mkdir(parents=True, exist_ok=True)
config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
PY
}

uninstall_agent() {
  rm -f "${launcher_home}/bridge"
  rm -rf "${install_root}/.agents/skills/agent-bridge"
  rm -rf "$skill_home"
  for name in "$@"; do
    rm -rf "${bridge_home}/agents/${name}"
  done
  echo "agent-bridge program files removed; project boards were preserved"
}

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

if [ "$uninstall" -eq 1 ]; then
  uninstall_agent $agents
  exit 0
fi

python_path="$(resolve_python)"
install_shared "$python_path"
for name in $agents; do
  register_agent_profile "$name" "$python_path"
  case "$name" in
    codex) configure_codex "$python_path" ;;
    claude) configure_claude "$python_path" ;;
    reasonix) configure_reasonix "$python_path" ;;
    zcode) configure_zcode "$python_path" ;;
  esac
done

doctor_identity="${identity:-${agent:-codex}}"
AGENT_BRIDGE_HOME="$bridge_home" AGENT_BRIDGE_CONFIG_HOME="$install_root" PYTHONUTF8=1 \
  "$python_path" "${skill_home}/scripts/bridge.py" --as "$doctor_identity" doctor --strict
echo "agent-bridge installed for: $agents"
