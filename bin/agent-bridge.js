#!/usr/bin/env node
'use strict';
// Thin, dependency-free launcher: proxies to the bridge CLI / install.sh.
// The real tool is pure Python stdlib; this just makes it npx-installable.
const { spawnSync } = require('child_process');
const path = require('path');

const root = path.join(__dirname, '..');
const args = process.argv.slice(2);

function run(cmd, cmdArgs) {
  const r = spawnSync(cmd, cmdArgs, { stdio: 'inherit' });
  if (r.error) {
    console.error(`agent-bridge: failed to run ${cmd}: ${r.error.message}`);
    process.exit(1);
  }
  process.exit(r.status === null ? 1 : r.status);
}

function python() {
  for (const p of ['python3', 'python']) {
    const r = spawnSync(p, ['--version'], { stdio: 'ignore' });
    if (!r.error && r.status === 0) return p;
  }
  console.error('agent-bridge: Python 3.9+ is required but was not found on PATH.');
  process.exit(1);
}

if (args.length === 0) {
  console.log(`agent-bridge — make the AI coding agents on your machine work as one team.

Setup:
  npx @xyva-yuangui/agent-bridge install --auto            wire every installed agent
  npx @xyva-yuangui/agent-bridge install --agent codex --as codex

Use (after install the 'bridge' command is on your PATH; or proxy via this CLI):
  agent-bridge send --to codex --subject "Design the auth module"
  agent-bridge inbox
  agent-bridge board

Docs: https://github.com/xyva-yuangui/agent-bridge`);
  process.exit(0);
}

if (args[0] === 'install') {
  run('bash', [path.join(root, 'install.sh'), ...args.slice(1)]);
} else if (args[0] === 'uninstall') {
  run('bash', [path.join(root, 'install.sh'), '--uninstall', ...args.slice(1)]);
} else {
  // proxy every other command straight to the bridge CLI
  run(python(), [path.join(root, 'scripts', 'bridge.py'), ...args]);
}
