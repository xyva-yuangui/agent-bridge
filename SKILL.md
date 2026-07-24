---
name: agent-bridge
description: Cross-agent collaboration: run bridge status every turn; announce pending tasks to the user; if pending tasks exist, handle them before responding. Supports continuous multi-agent workflows.
---

# agent-bridge

Use agent-bridge whenever another registered local agent can own, review, or
accept part of the current work.

## Turn protocol

At the start of every turn:

1. Call `bridge status --oneliner` or the MCP `bridge_status` tool.
2. If work is pending, call `bridge inbox` or `bridge_inbox`.
3. Read each assigned task with `bridge show <id>` before acting.
4. Claim work before editing and report completion with an explicit result.

Calling status or inbox acknowledges delivery of actionable tasks. Launching a
wake command is not an acknowledgment.

## Lifecycle

Legal transitions are:

```text
pending -> working
working -> completed
working -> input_required
input_required -> pending
working -> review_requested
review_requested -> completed
review_requested -> changes_requested
changes_requested -> working
working -> failed
```

Only the assignee may claim, ask a question, request review, or complete. Only
the original sender may answer a question or issue the requested review
verdict.

## Commands

```bash
bridge status --oneliner
bridge inbox
bridge show <task-id>
bridge send --to <agent> --subject "..." --body "..."
bridge claim <task-id>
bridge question <task-id> --body "..."
bridge answer <task-id> --body "..."
bridge review <task-id>
bridge review <task-id> --verdict approve --body "..."
bridge review <task-id> --verdict changes --body "..."
bridge done <task-id> --result "..." --files "..."
bridge board
bridge agents
bridge activity
bridge doctor --strict
```

Use `--skill <tag>` only when the recipient is not already known. Routing
requires an exact skill tag, prefers agents with recent heartbeats, and uses
name ordering only as a tie-breaker.

## Delivery interpretation

- `queued`: stored, not attempted yet.
- `wake_launched`: process launched, awaiting agent check-in.
- `acknowledged`: target checked status/inbox or claimed.
- `unavailable`: no usable channel.
- `failed`: attempt failed.

Do not tell a user that another agent received or accepted work unless the
delivery is `acknowledged` or the agent responded through the task lifecycle.
