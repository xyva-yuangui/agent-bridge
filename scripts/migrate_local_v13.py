#!/usr/bin/env python3
"""Convert local v1.3 boards into the layout bridge-migrate expects, then import."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from agent_bridge.migrate_v1 import import_v1  # noqa: E402
from agent_bridge.store import Store  # noqa: E402

V1_HOME = Path.home() / ".agent-bridge"
STATE_MAP = {
    "pending": "pending", "working": "working", "completed": "completed",
    "failed": "failed", "input_required": "input_required",
    "review_requested": "review_requested", "changes_requested": "changes_requested",
    # v1-only states folded into their closest v2 terminal/active state
    "review_approved": "completed", "accepted": "working", "canceled": "failed",
}


def convert(board: dict, project_id: str) -> dict:
    tasks = []
    for task in board.get("tasks", []):
        state = STATE_MAP.get(task.get("status", "pending"))
        if state is None or not task.get("id") or not task.get("subject"):
            print("  skip malformed/unmappable task:", task.get("id"))
            continue
        tasks.append({
            "id": task["id"],
            "sender": str(task.get("from", "unknown")).strip().lower(),
            "assignee": str(task.get("to", "unknown")).strip().lower(),
            "state": state,
            "subject": task["subject"],
            "body": task.get("body", ""),
            "created_at": task.get("created"),
            "updated_at": task.get("updated", task.get("created")),
        })
    return {"project": project_id, "tasks": tasks}


def main() -> int:
    store = Store.open(V1_HOME / "agent-bridge.sqlite3")
    try:
        for board_path in sorted(V1_HOME.glob("projects/*/board.json")):
            project_id = board_path.parent.name
            board = json.loads(board_path.read_text(encoding="utf-8"))
            converted = convert(board, project_id)
            if not converted["tasks"]:
                print(f"== {project_id}: empty, skipped ==")
                continue
            with tempfile.TemporaryDirectory() as staging:
                root = Path(staging) / project_id
                root.mkdir()
                (root / "board.json").write_text(
                    json.dumps(converted, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                report = import_v1(store, root)
            print(
                f"== {project_id}: imported {report.imported_tasks} tasks, "
                f"{report.imported_agents} agents =="
            )
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
