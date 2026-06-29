#!/usr/bin/env bash
# install.sh — install agent-bridge for a given agent
# Usage: install.sh --agent zcode|reasonix|claude|codex --as <name> [--uninstall]

set -euo pipefail

AGENT=""
AS_NAME=""
SKILLS=""
STRENGTHS=""
UNINSTALL=false
AUTO=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --agent) AGENT="$2"; shift 2 ;;
        --as) AS_NAME="$2"; shift 2 ;;
        --skills) SKILLS="$2"; shift 2 ;;
        --strengths) STRENGTHS="$2"; shift 2 ;;
        --auto) AUTO=true; shift ;;
        --uninstall) UNINSTALL=true; shift ;;
        *) echo "Unknown: $1"; exit 1 ;;
    esac
done

usage() {
    echo "Usage:"
    echo "  install.sh --auto [--as <name>]                 detect installed apps, install for each"
    echo "  install.sh --agent <zcode|reasonix|claude|codex> --as <name> [options]"
    echo "  install.sh --uninstall --agent <name>"
    echo "Options:"
    echo "  --strengths \"...\"   free-text strengths shown in 'bridge agents' (routing is the coordinator's call, not a fixed map)"
    echo "  --skills a,b,c       optional capability tags (fallback auto-route only)"
}

# --auto: detect which of the four apps exist and install for each present one.
if $AUTO; then
    # each tool gets its OWN identity (= tool name) so they can address each other.
    declare -a detected
    is_present() { command -v "$1" >/dev/null 2>&1 || [ -d "/Applications/$2" ]; }
    is_present claude   Claude.app   && detected+=("claude")
    is_present codex    Codex.app    && detected+=("codex")
    is_present reasonix Reasonix.app && detected+=("reasonix")
    is_present zcode    ZCode.app    && detected+=("zcode")
    if [[ ${#detected[@]} -eq 0 ]]; then echo "No supported apps detected."; exit 1; fi
    echo "Detected: ${detected[*]} — installing each with identity = its own name"
    # sensible default strengths (descriptive only; routing is still the coordinator's call)
    default_strengths() {
        case "$1" in
            codex)    echo "hard reasoning, system architecture, complex implementation (GPT-5.5, high effort)";;
            claude)   echo "orchestration, code review, refactoring, large-context work, skills";;
            reasonix) echo "planning, headless automation, diff review, multi-model";;
            zcode)    echo "frontend/UI, Chinese-context work, cost-effective bulk edits";;
        esac
    }
    for a in "${detected[@]}"; do
        st="${STRENGTHS:-$(default_strengths "$a")}"
        "$0" --agent "$a" --as "$a" ${SKILLS:+--skills "$SKILLS"} --strengths "$st" || true
        echo "----"
    done
    exit 0
fi

if [[ -z "$AGENT" || -z "$AS_NAME" ]]; then
    usage
    exit 1
fi

SKILL_HOME="$HOME/.agent-bridge/skill"
BRIDGE_BIN="$SKILL_HOME/scripts/bridge.py"
MCP_BIN="$SKILL_HOME/scripts/bridge_mcp.py"
SKILL_MD="$SKILL_HOME/SKILL.md"

# ── shared skill install ──────────────────────────────────────────────────────

install_shared() {
    mkdir -p "$SKILL_HOME/scripts"
    # bridge.py is deployed from the repo — copy
    cp "$(dirname "$0")/scripts/bridge.py" "$BRIDGE_BIN" 2>/dev/null || {
        echo "⚠️  bridge.py not found in repo — ensure it's deployed"
    }
    chmod +x "$BRIDGE_BIN"
    cp "$(dirname "$0")/scripts/bridge_mcp.py" "$MCP_BIN" 2>/dev/null || true
    chmod +x "$MCP_BIN"
    cp "$(dirname "$0")/SKILL.md" "$SKILL_MD" 2>/dev/null || true

    # universal skill discovery: symlink to ~/.agents/skills/ (works for ZCode, Reasonix, Codex)
    mkdir -p "$HOME/.agents/skills"
    ln -sf "$SKILL_HOME" "$HOME/.agents/skills/agent-bridge"

    # bridge on PATH: try Homebrew bin first, then /usr/local/bin, then ~/.local/bin
    for bin_dir in /opt/homebrew/bin /usr/local/bin "$HOME/.local/bin"; do
        if [ -d "$bin_dir" ] && [ -w "$bin_dir" ]; then
            ln -sf "$BRIDGE_BIN" "$bin_dir/bridge"
            echo "✅ bridge on PATH: $bin_dir/bridge"
            break
        fi
    done

    # register agent capabilities if --skills provided
    if [ -n "$SKILLS" ]; then
        mkdir -p "$HOME/.agent-bridge/agents/$AS_NAME"
        python3 -c "
import json
af = '$HOME/.agent-bridge/agents/$AS_NAME/agent.json'
data = {}
try: data = json.load(open(af))
except: pass
data['name'] = '$AS_NAME'
data['skills'] = [s.strip() for s in '$SKILLS'.split(',')]
json.dump(data, open(af, 'w'), indent=2)
"
        echo "✅ capabilities registered: $SKILLS"
    fi

    # register free-text strengths (descriptive; coordinator decides routing per project)
    if [ -n "$STRENGTHS" ]; then
        mkdir -p "$HOME/.agent-bridge/agents/$AS_NAME"
        STRENGTHS="$STRENGTHS" AS_NAME="$AS_NAME" HOME="$HOME" python3 -c "
import json, os
af = os.path.expanduser('~/.agent-bridge/agents/%s/agent.json' % os.environ['AS_NAME'])
try: data = json.load(open(af))
except Exception: data = {}
data['name'] = os.environ['AS_NAME']
data['strengths'] = os.environ['STRENGTHS']
json.dump(data, open(af, 'w'), indent=2)
"
        echo "✅ strengths registered: $STRENGTHS"
    fi
}

# ── per-agent install ─────────────────────────────────────────────────────────

install_zcode() {
    echo "Installing for ZCode as '$AS_NAME'..."
    install_shared

    # 1. skill discovery: symlink into ~/.agents/skills/
    ln -sf "$SKILL_HOME" "$HOME/.agents/skills/agent-bridge"
    echo "✅ skill discoverable at ~/.agents/skills/agent-bridge"

    # 2. plugin registration — ZCode uses the Claude-Code plugin format
    #    (.claude-plugin/ + hooks/), tracked in installed_plugins.json.
    local plugin_dir="$HOME/.zcode/cli/plugins/cache/local/agent-bridge/0.1.0"
    rm -rf "$HOME/.zcode/cli/plugins/cache/local/agent-bridge/0.1.0/.zcode-plugin"  # drop old wrong manifest
    mkdir -p "$plugin_dir/.claude-plugin" "$plugin_dir/hooks"

    cat > "$plugin_dir/.claude-plugin/plugin.json" << PLUGINJSON
{
  "name": "agent-bridge",
  "version": "0.1.0",
  "description": "Cross-agent collaboration: UserPromptSubmit hook injects bridge status every turn.",
  "author": { "name": "agent-bridge" },
  "license": "MIT"
}
PLUGINJSON

    # 3. UserPromptSubmit hook (fires every turn, stdout injected into context)
    cat > "$plugin_dir/hooks/hooks.json" << 'HOOKJSON'
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "AGENT_BRIDGE_NAME=AS_NAME \"BRIDGE_BIN\" status --oneliner",
            "async": false
          }
        ]
      }
    ]
  }
}
HOOKJSON
    # portable in-place replace (sed -i differs across macOS/Linux; use python3)
    python3 - "$plugin_dir/hooks/hooks.json" "$AS_NAME" "$BRIDGE_BIN" <<'PY'
import sys
p, asn, binp = sys.argv[1:4]
s = open(p).read().replace("AS_NAME", asn).replace("BRIDGE_BIN", binp)
open(p, "w").write(s)
PY

    # 4. register the plugin in installed_plugins.json AND enable it in config.json
    PLUGIN_DIR="$plugin_dir" python3 - <<'PY' 2>/dev/null && echo "✅ ZCode: plugin registered + enabled" || echo "⚠️  Could not register ZCode plugin — is ~/.zcode present? (open ZCode once)"
import json, os, time
home = os.path.expanduser("~")
ip_path = os.path.join(home, ".zcode/cli/plugins/installed_plugins.json")
cfg_path = os.path.join(home, ".zcode/cli/config.json")
ts = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
# installed_plugins.json
ip = {"version": 1, "plugins": []}
if os.path.exists(ip_path):
    try: ip = json.load(open(ip_path))
    except Exception: pass
ip.setdefault("plugins", [])
entry = {"id": "agent-bridge@local", "name": "agent-bridge", "marketplace": "local",
         "version": "0.1.0", "installPath": os.environ["PLUGIN_DIR"],
         "installedAt": ts, "updatedAt": ts, "scope": "user",
         "source": {"source": "local", "path": os.environ["PLUGIN_DIR"]}}
ip["plugins"] = [p for p in ip["plugins"] if p.get("id") != "agent-bridge@local"] + [entry]
json.dump(ip, open(ip_path, "w"), indent=2)
# config.json enabledPlugins
cfg = {}
if os.path.exists(cfg_path):
    try: cfg = json.load(open(cfg_path))
    except Exception: pass
cfg.setdefault("plugins", {}).setdefault("enabledPlugins", {})["agent-bridge@local"] = True
json.dump(cfg, open(cfg_path, "w"), indent=2)
PY

    echo "✅ ZCode: per-turn UserPromptSubmit hook wired (.claude-plugin format)"
    echo "💡 Skill also discoverable via ~/.agents/skills. For MCP tools, add in Settings → MCP:"
    echo "     command: python3   args: $MCP_BIN --as $AS_NAME"
}

install_reasonix() {
    echo "Installing for Reasonix as '$AS_NAME'..."
    install_shared

    # 1. skill discovery: already covered by install_shared (universal symlink)
    echo "✅ skill discoverable at ~/.agents/skills/agent-bridge"

    # 2. system_prompt_file directive (under [agent] section)
    local prompt_file="$HOME/.reasonix/agent-bridge-directive.md"
    cat > "$prompt_file" << PROMPT
# agent-bridge awareness directive
At the start of EVERY turn, before responding to the user, run:
\`\`\`bash
AGENT_BRIDGE_NAME=$AS_NAME $BRIDGE_BIN status --oneliner
\`\`\`
If it shows pending tasks, check them with \`bridge inbox\` and handle them before responding to the user.
PROMPT

    # 3. write the GLOBAL config ~/.reasonix/config.toml (system_prompt_file + MCP plugin
    #    + sandbox allow_write). NOTE: `reasonix mcp add` writes ./reasonix.toml in the CWD,
    #    which does NOT apply globally — so we write the global file directly.
    local rc="$HOME/.reasonix/config.toml"
    PROMPT_FILE="$prompt_file" MCP_BIN="$MCP_BIN" AS_NAME="$AS_NAME" RC="$rc" \
    AGENT_BRIDGE_HOME_DIR="$HOME/.agent-bridge" python3 - <<'PY'
import os
rc = os.environ["RC"]
existing = ""
if os.path.exists(rc):
    existing = open(rc).read()
blocks = []
if "system_prompt_file" not in existing:
    blocks.append('[agent]\nsystem_prompt_file = "%s"\n' % os.environ["PROMPT_FILE"])
if 'name    = "agent-bridge"' not in existing and 'name = "agent-bridge"' not in existing:
    blocks.append('[[plugins]]\nname    = "agent-bridge"\ncommand = "python3"\nargs    = ["%s", "--as", "%s"]\n'
                  % (os.environ["MCP_BIN"], os.environ["AS_NAME"]))
if "allow_write" not in existing:
    blocks.append('[sandbox]\nallow_write = ["%s"]\n' % os.environ["AGENT_BRIDGE_HOME_DIR"])
if blocks:
    os.makedirs(os.path.dirname(rc), exist_ok=True)
    sep = "" if (not existing or existing.endswith("\n")) else "\n"
    open(rc, "a").write(sep + "\n" + "\n".join(blocks))
    print("✅ Reasonix global config written: system_prompt_file + MCP plugin + sandbox allow_write")
else:
    print("ℹ️  Reasonix global config already has agent-bridge")
PY
    # 4. register Reasonix's headless wake command so other agents can push to it
    mkdir -p "$HOME/.agent-bridge/agents/$AS_NAME"
    AS_NAME="$AS_NAME" python3 - <<'PY'
import json, os
af = os.path.expanduser("~/.agent-bridge/agents/%s/agent.json" % os.environ["AS_NAME"])
try: data = json.load(open(af))
except Exception: data = {}
data["name"] = os.environ["AS_NAME"]
data["wake"] = "reasonix run"   # headless: `reasonix run "<prompt>"`
json.dump(data, open(af, "w"), indent=2)
PY
    echo "✅ Reasonix wake command registered (headless push via 'reasonix run')"
    echo "✅ Reasonix configured (loads on next session)"
}

install_claude() {
    echo "Installing for Claude Code as '$AS_NAME'..."
    install_shared

    # 1. skill discovery
    mkdir -p "$HOME/.claude/skills"
    ln -sf "$SKILL_HOME" "$HOME/.claude/skills/agent-bridge"
    echo "✅ skill discoverable at ~/.claude/skills/agent-bridge"

    # 2. UserPromptSubmit hook — APPEND to existing, don't replace
    local settings="$HOME/.claude/settings.json"
    mkdir -p "$(dirname "$settings")"
    python3 -c "
import json, os
s = {}
if os.path.exists('$settings'):
    try: s = json.load(open('$settings'))
    except: pass
h = s.setdefault('hooks', {})
# ponytail: append to existing UserPromptSubmit hooks, don't replace
existing = h.get('UserPromptSubmit', [])
existing.append({
    'matcher': '*',
    'hooks': [{'type': 'command', 'command': 'AGENT_BRIDGE_NAME=$AS_NAME $BRIDGE_BIN status --oneliner', 'async': False}]
})
h['UserPromptSubmit'] = existing
json.dump(s, open('$settings','w'), indent=2)
" 2>/dev/null || echo "⚠️  Could not update settings.json"
    echo "✅ Claude Code: UserPromptSubmit hook appended"

    # 3. MCP server (works in CLI + desktop). Idempotent: remove then add.
    if command -v claude >/dev/null 2>&1; then
        claude mcp remove agent-bridge -s user >/dev/null 2>&1 || true
        claude mcp add -s user agent-bridge -- python3 "$MCP_BIN" --as "$AS_NAME" \
            && echo "✅ Claude Code: MCP server 'agent-bridge' registered" \
            || echo "⚠️  claude mcp add failed — register manually"
    fi
}

install_codex() {
    echo "Installing for Codex as '$AS_NAME'..."
    install_shared

    # 1. skill discovery: already covered by install_shared (universal symlink)
    echo "✅ skill discoverable at ~/.agents/skills/agent-bridge"

    # 2. AGENTS.md directive (global fallback + suggest project-level)
    local agents_md="$HOME/AGENTS.md"
    local directive="
## agent-bridge
At the start of every turn, run: \`AGENT_BRIDGE_NAME=$AS_NAME $BRIDGE_BIN status --oneliner\`
If pending tasks exist, call \`bridge inbox\` and handle them before responding.
"
    if [[ -f "$agents_md" ]]; then
        if ! grep -q "agent-bridge" "$agents_md" 2>/dev/null; then
            echo "$directive" >> "$agents_md"
            echo "✅ Codex AGENTS.md updated"
        else
            echo "ℹ️  Codex AGENTS.md already has agent-bridge"
        fi
    else
        echo "$directive" > "$agents_md"
        echo "✅ Codex AGENTS.md created"
    fi
    echo "💡 For project-level awareness, add the same directive to your project's AGENTS.md"

    # 3. MCP server — Codex reads [mcp_servers.*] from config.toml (no add CLI).
    local codex_cfg="${CODEX_HOME:-$HOME/.codex}/config.toml"
    if [[ -f "$codex_cfg" ]] && ! grep -q "mcp_servers.agent-bridge" "$codex_cfg" 2>/dev/null; then
        cat >> "$codex_cfg" << CODEXMCP

[mcp_servers.agent-bridge]
command = "python3"
args = ["$MCP_BIN", "--as", "$AS_NAME"]
CODEXMCP
        echo "✅ Codex: MCP server 'agent-bridge' appended to config.toml"
    else
        echo "ℹ️  Codex: MCP block already present (or config.toml missing)"
    fi

    # 4. register Codex's headless wake command (push). Prefer the app's real binary;
    #    the npm shim's vendored binary is often missing.
    local codex_bin=""
    for c in "/Applications/Codex.app/Contents/Resources/codex" "$(command -v codex 2>/dev/null)"; do
        if [[ -n "$c" && -x "$c" ]]; then codex_bin="$c"; break; fi
    done
    if [[ -n "$codex_bin" ]]; then
        mkdir -p "$HOME/.agent-bridge/agents/$AS_NAME"
        AS_NAME="$AS_NAME" WAKE="$codex_bin exec --skip-git-repo-check" python3 - <<'PY'
import json, os
af = os.path.expanduser("~/.agent-bridge/agents/%s/agent.json" % os.environ["AS_NAME"])
try: data = json.load(open(af))
except Exception: data = {}
data["name"] = os.environ["AS_NAME"]
data["wake"] = os.environ["WAKE"]   # headless: `codex exec "<prompt>"`
json.dump(data, open(af, "w"), indent=2)
PY
        echo "✅ Codex wake command registered (headless push via 'codex exec')"
    fi
}

uninstall_agent() {
    echo "Uninstalling agent-bridge for $AGENT..."
    # remove hook files
    rm -f "$HOME/.zcode/cli/plugins/cache/local/agent-bridge" 2>/dev/null || true
    rm -f "$HOME/.reasonix/agent-bridge-directive.md" 2>/dev/null || true
    # don't remove shared skill — other agents may use it
    echo "✅ Uninstalled (shared skill at $SKILL_HOME preserved)"
}

# ── main ──────────────────────────────────────────────────────────────────────

if $UNINSTALL; then
    uninstall_agent
    exit 0
fi

case "$AGENT" in
    zcode) install_zcode ;;
    reasonix) install_reasonix ;;
    claude) install_claude ;;
    codex) install_codex ;;
    *) echo "Unknown agent: $AGENT"; exit 1 ;;
esac

echo ""
echo "Running bridge doctor..."
AGENT_BRIDGE_NAME="$AS_NAME" python3 "$BRIDGE_BIN" doctor || true
echo ""
echo "✅ agent-bridge installed for $AGENT as '$AS_NAME'"
echo "   Skill: $SKILL_HOME"
echo "   Binary: $BRIDGE_BIN"
echo ""
echo "=== Cross-machine setup ==="
echo "  To share agent-bridge across machines, sync ~/.agent-bridge/ via:"
echo "    - Syncthing / Dropbox / iCloud (real-time, best)"
echo "    - git (manual push/pull, no real-time)"
echo "  Limitation: flock file locks don't cross machines."
echo "  Concurrent writes from different machines may race."
echo "  For multi-machine: use one machine as 'primary' for writes,"
echo "  or sync only after sessions end."
echo "  Future: bridge serve (HTTP) for real cross-machine sync."