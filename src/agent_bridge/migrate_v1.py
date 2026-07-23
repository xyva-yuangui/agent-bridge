"""Idempotent import and portable export for the v1 JSON data layout."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Tuple

from .models import DeliveryStatus, ExecutionPolicy, TaskState
from .store import Store


@dataclass(frozen=True)
class ImportReport:
    imported_tasks: int
    imported_agents: int
    imported_deliveries: int
    backup_path: Path


def import_v1(store: Store, v1_root: Path) -> ImportReport:
    """Import one v1 board once, retaining original delivery records."""
    v1_root = Path(v1_root)
    board_path = _find_board(v1_root)
    board_bytes = board_path.read_bytes()
    source_hash = hashlib.sha256(board_bytes).hexdigest()
    board = _load_json(board_path, board_bytes)
    tasks = _validate_board(board, board_path)
    existing = store.scalar("SELECT 1 FROM import_ledger WHERE source_hash = ?", (source_hash,))
    if existing:
        return ImportReport(0, 0, 0, Path())

    backup_path = _backup_v1_tree(store, v1_root)
    project_id = str(board.get("project", "default"))
    agents = _read_agents(v1_root)
    for task in tasks:
        for name in (task["sender"], task["assignee"]):
            agents.setdefault(str(name), {})

    imported_agents = 0
    imported_tasks = 0
    imported_deliveries = 0
    with store.transaction(immediate=True) as connection:
        connection.execute(
            "INSERT OR IGNORE INTO projects(id, path, created_at) VALUES (?, ?, ?)",
            (project_id, str(v1_root.resolve()), _utc_now()),
        )
        for name, profile in sorted(agents.items()):
            cursor = connection.execute(
                "INSERT OR IGNORE INTO agents("
                "name, capabilities_json, last_seen, launch_argv_json"
                ") VALUES (?, ?, ?, ?)",
                (
                    name,
                    json.dumps(profile.get("skills", []), sort_keys=True),
                    profile.get("last_seen"),
                    json.dumps(profile.get("wake_argv", []), sort_keys=True),
                ),
            )
            imported_agents += cursor.rowcount
        for task in tasks:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO tasks("
                "id, project_id, sender, assignee, state, subject, body, priority, "
                "revision, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    task["id"], project_id, task["sender"], task["assignee"], task["state"],
                    task["subject"], task.get("body", ""), task.get("priority", 0),
                    task.get("revision", 0), task.get("created_at", _utc_now()),
                    task.get("updated_at", task.get("created_at", _utc_now())),
                ),
            )
            if cursor.rowcount:
                imported_tasks += 1
                for index, delivery in enumerate(task.get("deliveries", [])):
                    _validate_delivery(delivery, board_path)
                    connection.execute(
                        "INSERT INTO delivery_attempts("
                        "task_id, channel, status, attempts, created_at, updated_at, error, idempotency_key"
                        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            task["id"], delivery["channel"], delivery["status"],
                            delivery.get("attempts", 0), delivery.get("created_at", _utc_now()),
                            delivery.get("updated_at", delivery.get("created_at", _utc_now())),
                            delivery.get("error"),
                            "v1:{0}:{1}:{2}".format(source_hash, task["id"], index),
                        ),
                    )
                    imported_deliveries += 1
        connection.execute(
            "INSERT INTO import_ledger(source_hash, source_path, imported_at, record_count) "
            "VALUES (?, ?, ?, ?)",
            (source_hash, str(board_path), _utc_now(), imported_tasks),
        )
    return ImportReport(imported_tasks, imported_agents, imported_deliveries, backup_path)


def export_json(store: Store, destination: Path) -> Path:
    """Write a portable database snapshot with an atomic rename."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    tables = (
        "projects", "agents", "tasks", "task_events", "task_dependencies", "task_artifacts",
        "delivery_attempts", "outbox", "dispatcher_leases", "notification_mappings", "metadata",
        "import_ledger",
    )
    payload = {
        "format": "agent-bridge-v2-export",
        "schema_version": store.scalar("SELECT MAX(version) FROM schema_migrations"),
        "tables": {table: _rows(store, table) for table in tables},
    }
    payload.update(payload.pop("tables"))
    handle, temporary_name = tempfile.mkstemp(prefix=destination.name + ".", suffix=".tmp", dir=str(destination.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as temporary_file:
            json.dump(payload, temporary_file, ensure_ascii=False, indent=2, sort_keys=True)
            temporary_file.write("\n")
        os.replace(temporary_name, destination)
    except BaseException:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
        raise
    return destination


def _rows(store: Store, table: str) -> List[Dict[str, Any]]:
    return [dict(row) for row in store.connection.execute("SELECT * FROM " + table + " ORDER BY 1")]


def _find_board(v1_root: Path) -> Path:
    direct = v1_root / "board.json"
    if direct.is_file():
        return direct
    candidates = sorted(v1_root.glob("projects/*/board.json"))
    if len(candidates) == 1:
        return candidates[0]
    raise ValueError("v1 board.json was not found under {0}".format(v1_root))


def _load_json(path: Path, raw: bytes) -> Mapping[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("invalid UTF-8 JSON in {0}".format(path)) from error
    if not isinstance(value, dict):
        raise ValueError("v1 JSON object required in {0}".format(path))
    return value


def _validate_board(board: Mapping[str, Any], path: Path) -> List[Mapping[str, Any]]:
    tasks = board.get("tasks")
    if not isinstance(tasks, list):
        raise ValueError("v1 board requires a tasks list: {0}".format(path))
    required = ("id", "sender", "assignee", "state", "subject")
    for task in tasks:
        if not isinstance(task, dict) or any(not task.get(key) for key in required):
            raise ValueError("v1 task is missing required keys in {0}".format(path))
        if task["state"] not in {state.value for state in TaskState}:
            raise ValueError("v1 task has an invalid state in {0}".format(path))
    return tasks


def _validate_delivery(delivery: Any, path: Path) -> None:
    if not isinstance(delivery, dict) or not delivery.get("channel") or not delivery.get("status"):
        raise ValueError("v1 delivery is missing required keys in {0}".format(path))
    if delivery["status"] not in {status.value for status in DeliveryStatus}:
        raise ValueError("v1 delivery has an invalid status in {0}".format(path))


def _read_agents(v1_root: Path) -> Dict[str, Mapping[str, Any]]:
    agents: Dict[str, Mapping[str, Any]] = {}
    for profile_path in sorted(v1_root.glob("agents/*/agent.json")):
        profile = _load_json(profile_path, profile_path.read_bytes())
        name = profile.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("v1 agent requires a name: {0}".format(profile_path))
        agents[name] = profile
    return agents


def _backup_v1_tree(store: Store, v1_root: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    source_root = v1_root.resolve()
    backup_root = store.path.parent / "backups"
    try:
        backup_root.resolve().relative_to(source_root)
    except ValueError:
        pass
    else:
        backup_root = source_root.parent / (source_root.name + ".backups")
    backup_path = backup_root / ("v1-" + timestamp)
    shutil.copytree(str(v1_root), str(backup_path))
    return backup_path


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
