"""Command-line entry point for Agent Bridge v2."""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from urllib.parse import urlsplit
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from . import dispatcher
from .launchers import LaunchDeliveryChannel, launch_stored_agent
from .migrate_v1 import export_json, import_v1
from .notifications import (
    MacOSNotificationChannel,
    WindowsNotificationChannel,
    macos_activation_argv,
    macos_notification_capability,
    macos_signing_assessment,
    windows_notification_capability,
)
from .paths import get_data_root, require_local_data_root
from .presentation import configure_streams, error_view, render, task_page, task_view, tasks_view
from .service import BridgeService
from .store import Store
from .version import BRIDGE_VERSION, SCHEMA_VERSION


MCP_EXCLUDED_COMMANDS = frozenset(("dispatch", "tui", "setup", "uninstall", "open-action"))
UNAVAILABLE_COMMANDS = frozenset(("setup", "uninstall"))


class CommandUnavailable(RuntimeError):
    """A public command whose interactive implementation belongs to a later component."""


def parse_identity(argv: Optional[Sequence[str]] = None) -> str:
    """Read identity with argparse so a dangling ``--as`` is a parse error."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--as", dest="identity", default=os.environ.get("AGENT_BRIDGE_NAME", "unknown"))
    namespace, _ = parser.parse_known_args(argv)
    return str(namespace.identity).strip() or "unknown"


def open_service(data_root: Optional[str] = None) -> BridgeService:
    root = get_data_root(os.environ) if data_root is None else require_local_data_root(Path(data_root))
    return BridgeService(Store.open(root / "agent-bridge.sqlite3"))


def _argument(arguments: Dict[str, Any], name: str, default: Any = None) -> Any:
    return arguments.get(name, default)


def _project(arguments: Dict[str, Any]) -> str:
    return str(_argument(arguments, "project", "default") or "default")


def _metadata(service: BridgeService, key: str) -> Optional[str]:
    row = service.store.connection.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
    return None if row is None else str(row["value"])


def _put_metadata(service: BridgeService, key: str, value: str) -> None:
    with service.store.transaction(immediate=True) as connection:
        connection.execute(
            "INSERT INTO metadata(key, value, updated_at) VALUES (?, ?, datetime('now')) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, value),
        )


def _delivery_result(task: Any) -> Dict[str, Any]:
    """Render a committed task mutation, then request a detached delivery burst."""
    dispatcher.request_dispatch()
    return {"task": task_view(task)}


def execute_command(
    service: BridgeService, identity: str, command: str, arguments: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Execute one public command directly against the v2 service and store."""
    arguments = dict(arguments or {})
    project_id = _project(arguments)
    if command != "dispatch":
        # This is deliberately one indexed probe: a dispatcher never launches
        # another dispatcher while it is draining the outbox.
        dispatcher.tick(service.store)
    if command in UNAVAILABLE_COMMANDS:
        raise CommandUnavailable("{0} is unavailable in the v2 service layer".format(command))
    if command == "dispatch":
        configured = service.store.scalar(
            "SELECT 1 FROM agents WHERE execution_policy = 'auto' AND launch_argv_json <> '[]' LIMIT 1"
        )
        channels = {"launcher": LaunchDeliveryChannel(str(service.store.path))} if configured else {}
        notification_capability, notification_channel = _native_notification_channel(service.store.path)
        if notification_capability.available:
            channels["notification"] = notification_channel
        report = dispatcher.Dispatcher(service.store, channels).run_burst()
        return {"dispatch": {
            "acquired": report.acquired,
            "processed": report.processed,
            "delivered": report.delivered,
            "retried": report.retried,
            "failed": report.failed,
            "coalesced": report.coalesced,
            "timed_out": report.timed_out,
        }}
    if command == "open-action":
        uri = _argument(arguments, "activation_uri")
        if uri is not None:
            notification_id, action = _activation_uri(str(uri))
        else:
            notification_id = str(_argument(arguments, "notification_id", ""))
            action = str(_argument(arguments, "action", ""))
        if not notification_id or len(notification_id) > 256 or not all(
            character.isascii() and (character.isalnum() or character in "._-")
            for character in notification_id
        ):
            raise ValueError("invalid notification ID")
        if action not in ("view", "claim", "snooze"):
            raise ValueError("invalid notification action")
        row = service.store.connection.execute(
            "SELECT task_id FROM notification_mappings WHERE notification_id = ?", (notification_id,)
        ).fetchone()
        if row is None:
            raise KeyError("unknown native notification")
        task_id = str(row["task_id"])
        if action == "claim":
            task = service.claim(task_id, identity)
        else:
            task = service.show(task_id)
        return {"open_action": {"action": action, "task": task_view(task)}}
    if command == "whoami":
        return {"identity": identity}
    if command == "send":
        assignee = _argument(arguments, "to")
        if not assignee:
            raise ValueError("--to is required by the v2 service layer")
        task = service.send_task(identity, str(assignee), str(_argument(arguments, "subject")), str(_argument(arguments, "body", "")), project_id)
        if _metadata(service, "coordinator:" + project_id) is None:
            _put_metadata(service, "coordinator:" + project_id, identity)
        return _delivery_result(task)
    if command == "claim":
        return _delivery_result(service.claim(str(_argument(arguments, "task_id")), str(_argument(arguments, "actor", identity)), str(_argument(arguments, "body", ""))))
    if command == "done":
        files = _argument(arguments, "files") or ""
        task = service.done(
            str(_argument(arguments, "task_id")), str(_argument(arguments, "actor", identity)),
            str(_argument(arguments, "result", _argument(arguments, "body", ""))),
            artifacts=tuple(str(files).split(",")),
        )
        return _delivery_result(task)
    if command == "question":
        return _delivery_result(service.question(str(_argument(arguments, "task_id")), str(_argument(arguments, "actor", identity)), str(_argument(arguments, "body", ""))))
    if command == "answer":
        return _delivery_result(service.answer(str(_argument(arguments, "task_id")), str(_argument(arguments, "actor", identity)), str(_argument(arguments, "body", ""))))
    if command == "review":
        task_id = str(_argument(arguments, "task_id"))
        actor = str(_argument(arguments, "actor", identity))
        body = str(_argument(arguments, "body", ""))
        verdict = _argument(arguments, "verdict")
        task = service.request_review(task_id, actor, body) if verdict is None else service.review(task_id, actor, str(verdict), body)
        return _delivery_result(task)
    if command == "show":
        return {"task": task_view(service.show(str(_argument(arguments, "task_id"))))}
    if command == "status":
        return tasks_view(service.status(str(_argument(arguments, "actor", identity))))
    if command == "inbox":
        page = service.inbox(str(_argument(arguments, "actor", identity)), int(_argument(arguments, "limit", 100)), _argument(arguments, "cursor"))
        return task_page(page)
    if command == "board":
        return tasks_view(service.board(project_id))
    if command == "agents":
        rows = service.store.connection.execute("SELECT * FROM agents ORDER BY name").fetchall()
        return {"agents": [dict(row) for row in rows]}
    if command == "wake":
        agent = str(_argument(arguments, "agent"))
        row = service.store.connection.execute(
            "SELECT path FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        if row is None:
            raise KeyError("unknown project: {0}".format(project_id))
        result = launch_stored_agent(
            service.store, agent, str(row["path"]), "wake:{0}:{1}".format(agent, project_id)
        )
        return {"launch": {"started": result.started, "reason": result.reason, "pid": result.pid}}
    if command == "who-coordinates":
        return {"project_id": project_id, "coordinator": _metadata(service, "coordinator:" + project_id)}
    if command == "context":
        key = "context:" + project_id
        addition = _argument(arguments, "add")
        if addition is not None:
            old = _metadata(service, key) or ""
            _put_metadata(service, key, (old + "\n" + str(addition)).strip())
        return {"project_id": project_id, "context": _metadata(service, key) or ""}
    if command == "log":
        entry = {"agent": identity, "project_id": project_id, "what": str(_argument(arguments, "what"))}
        _put_metadata(service, "log:" + uuid.uuid4().hex, json.dumps(entry, sort_keys=True))
        return {"ok": True, "entry": entry}
    if command == "activity":
        since = _activity_since(_argument(arguments, "since"))
        query = (
            "SELECT task_events.* FROM task_events JOIN tasks ON tasks.id = task_events.task_id "
            "WHERE tasks.project_id = ?"
        )
        parameters = [project_id]
        if since is not None:
            query += " AND task_events.created_at >= ?"
            parameters.append(since)
        rows = service.store.connection.execute(
            query + " ORDER BY task_events.created_at ASC, task_events.id ASC", parameters
        ).fetchall()
        return {"events": [dict(row) for row in rows]}
    if command == "doctor":
        report = service.store.integrity_report()
        actual_version = service.store.scalar("SELECT MAX(version) FROM schema_migrations")
        notification_capability, _ = _native_notification_channel(service.store.path)
        checks = {
            "data_root": service.store.path.parent.is_dir(),
            "schema_version": actual_version == SCHEMA_VERSION,
            "integrity": report.ok,
            "native_notifications": notification_capability.available,
        }
        signing = None
        if sys.platform == "darwin":
            signing = macos_signing_assessment(notification_capability.helper_path)
            checks["native_notification_signing"] = signing.status in ("signed", "notarized") and signing.gatekeeper in ("accepted", "notarized")
        strict = bool(_argument(arguments, "strict"))
        return {
            "ok": all(checks.values()) if strict else report.ok,
            "message": report.message,
            "database": str(service.store.path),
            "checks": checks,
            "notification_capability": {
                "available": notification_capability.available,
                "helper_path": notification_capability.helper_path,
                "detail": notification_capability.detail,
                "expiry_detail": notification_capability.expiry_detail,
                "signing_status": notification_capability.signing_status,
                "gatekeeper": notification_capability.gatekeeper,
                **({"signing": {"status": signing.status, "detail": signing.detail, "gatekeeper": signing.gatekeeper}} if signing is not None else {}),
            },
            "strict": strict,
        }
    if command == "project":
        action = str(_argument(arguments, "action"))
        if action == "list":
            rows = service.store.connection.execute("SELECT * FROM projects ORDER BY id").fetchall()
            return {"projects": [dict(row) for row in rows]}
        name = str(_argument(arguments, "name", project_id) or project_id)
        if action == "init":
            workspace = str(Path(_argument(arguments, "workspace") or Path.cwd()).resolve())
            with service.store.transaction(immediate=True) as connection:
                connection.execute("INSERT OR IGNORE INTO projects(id, path) VALUES (?, ?)", (name, workspace))
            if _argument(arguments, "goal") is not None:
                _put_metadata(service, "goal:" + name, str(_argument(arguments, "goal")))
            return {"project": {"id": name, "path": workspace, "goal": _metadata(service, "goal:" + name) or ""}}
        if action == "show":
            row = service.store.connection.execute("SELECT * FROM projects WHERE id = ?", (name,)).fetchone()
            if row is None:
                raise KeyError("unknown project: {0}".format(name))
            return {"project": dict(row), "goal": _metadata(service, "goal:" + name) or ""}
        raise ValueError("unknown project action: {0}".format(action))
    if command == "clean":
        if not _argument(arguments, "all") and _argument(arguments, "days") is None:
            raise ValueError("clean requires --all or --days")
        states = tuple(str(_argument(arguments, "status") or "completed,failed").split(","))
        placeholders = ",".join("?" for state in states)
        where = "project_id = ? AND state IN ({0})".format(placeholders)
        parameters = [project_id] + list(states)
        if _argument(arguments, "days") is not None:
            where += " AND updated_at < datetime('now', ?)"
            parameters.append("-{0} days".format(int(_argument(arguments, "days"))))
        if _argument(arguments, "dry_run"):
            count = int(service.store.scalar("SELECT COUNT(*) FROM tasks WHERE " + where, parameters) or 0)
        else:
            with service.store.transaction(immediate=True) as connection:
                task_ids = [row["id"] for row in connection.execute("SELECT id FROM tasks WHERE " + where, parameters)]
                if task_ids:
                    cancelled = []
                    for row in connection.execute("SELECT id, payload_json FROM outbox"):
                        try:
                            payload = json.loads(row["payload_json"])
                        except (TypeError, ValueError):
                            continue
                        if isinstance(payload, dict) and payload.get("task_id") in task_ids:
                            cancelled.append(row["id"])
                    if cancelled:
                        connection.execute(
                            "DELETE FROM outbox WHERE id IN ({0})".format(
                                ",".join("?" for ignored in cancelled)
                            ),
                            cancelled,
                        )
                count = connection.execute("DELETE FROM tasks WHERE " + where, parameters).rowcount
        return {"ok": True, "removed": count, "dry_run": bool(_argument(arguments, "dry_run"))}
    if command == "migrate":
        report = import_v1(service.store, Path(str(_argument(arguments, "source"))))
        return {"imported_tasks": report.imported_tasks, "imported_agents": report.imported_agents, "imported_deliveries": report.imported_deliveries, "backup_path": str(report.backup_path)}
    if command == "export":
        destination = export_json(service.store, Path(str(_argument(arguments, "destination"))))
        return {"destination": str(destination)}
    raise ValueError("unknown command: {0}".format(command))


def _native_notification_channel(database_path: Path) -> tuple[Any, Any]:
    """Select only the platform's helper; a configured foreign helper is degraded."""
    if sys.platform == "darwin":
        capability = macos_notification_capability()
        return capability, MacOSNotificationChannel(database_path, capability.helper_path, macos_activation_argv())
    capability = windows_notification_capability()
    return capability, WindowsNotificationChannel(database_path, capability.helper_path)


def _activity_since(value: Any) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("invalid ISO-8601 --since value")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("invalid ISO-8601 --since value") from error
    if parsed.tzinfo is None:
        raise ValueError("invalid ISO-8601 --since value")
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _activation_uri(value: str) -> tuple[str, str]:
    """Parse only the helper's opaque, query-free activation URI."""
    parsed = urlsplit(value)
    if parsed.scheme != "agent-bridge" or parsed.netloc != "action" or parsed.username or parsed.password or parsed.port is not None or parsed.query or parsed.fragment:
        raise ValueError("invalid activation URI")
    parts = parsed.path.split("/")
    if len(parts) != 3 or parts[0] or parts[1] not in ("view", "claim", "snooze"):
        raise ValueError("invalid activation URI")
    action, notification_id = parts[1], parts[2]
    if "%" in parsed.path:
        raise ValueError("invalid activation URI")
    if not notification_id or len(notification_id) > 256 or not all(
        character.isascii() and (character.isalnum() or character in "._-") for character in notification_id
    ):
        raise ValueError("invalid activation URI")
    return notification_id, action


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bridge")
    parser.add_argument("--version", action="version", version=BRIDGE_VERSION)
    parser.add_argument(
        "--data-root",
        help="absolute Agent Bridge database directory (overrides AGENT_BRIDGE_HOME)",
    )
    parser.add_argument("--as", dest="identity", default=os.environ.get("AGENT_BRIDGE_NAME", "unknown"))
    parser.add_argument("--json", action="store_true", dest="as_json")
    subparsers = parser.add_subparsers(dest="command", required=True)
    def command(name: str, **kwargs: Any) -> argparse.ArgumentParser:
        return subparsers.add_parser(name, **kwargs)
    command("whoami")
    command("status").add_argument("--oneliner", action="store_true")
    inbox = command("inbox"); inbox.add_argument("--limit", type=int, default=100); inbox.add_argument("--cursor")
    send = command("send"); send.add_argument("--to"); send.add_argument("--subject", required=True); send.add_argument("--body", default=""); send.add_argument("--project", default="default"); send.add_argument("--no-wake", action="store_true")
    for name in ("claim", "show"):
        item = command(name); item.add_argument("task_id")
    done = command("done"); done.add_argument("task_id"); done.add_argument("--result", default=""); done.add_argument("--files")
    board = command("board"); board.add_argument("--project", default="default")
    for name in ("question", "answer"):
        item = command(name); item.add_argument("task_id"); item.add_argument("--body", required=True)
    review = command("review"); review.add_argument("task_id"); review.add_argument("--verdict", choices=("approve", "changes")); review.add_argument("--body", default="")
    wake = command("wake"); wake.add_argument("agent"); wake.add_argument("--project", default="default")
    command("agents")
    activity = command("activity"); activity.add_argument("--project", default="default"); activity.add_argument("--since")
    context = command("context"); context.add_argument("--project", default="default"); context.add_argument("--show", action="store_true"); context.add_argument("--add")
    clean = command("clean"); clean.add_argument("--project", default="default"); clean.add_argument("--all", action="store_true"); clean.add_argument("--days", type=int); clean.add_argument("--status"); clean.add_argument("--dry-run", action="store_true")
    doctor = command("doctor"); doctor.add_argument("--strict", action="store_true")
    project = command("project"); project.add_argument("action", choices=("init", "list", "show")); project.add_argument("--name"); project.add_argument("--workspace"); project.add_argument("--goal")
    coordinates = command("who-coordinates"); coordinates.add_argument("--project", default="default")
    log = command("log"); log.add_argument("--what", required=True); log.add_argument("--project", default="default")
    migrate = command("migrate"); migrate.add_argument("source")
    export = command("export"); export.add_argument("destination")
    dispatch = command("dispatch"); dispatch.add_argument("--burst", action="store_true")
    for name in UNAVAILABLE_COMMANDS:
        command(name, help="reserved for a later v2 component")
    tui = command("tui", help="open the on-demand terminal dashboard")
    tui.add_argument("--project", default="default")
    open_action = command("open-action")
    action_source = open_action.add_mutually_exclusive_group(required=True)
    action_source.add_argument("--activation-uri")
    open_action.add_argument("--notification-id")
    open_action.add_argument("--action", choices=("view", "claim", "snooze"))
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    configure_streams()
    parser = build_parser()
    arguments = parser.parse_args(argv)
    service = open_service(arguments.data_root)
    try:
        if arguments.command == "tui":
            # Keep interactive terminal ownership outside the JSON command
            # renderer.  The controller itself handles non-VT fallback output.
            from .tui.controller import default_input_adapter, run_tui
            return run_tui(
                service, default_input_adapter(), sys.stdout,
                actor=str(arguments.identity).strip() or "unknown", project_id=str(arguments.project),
                dispatch_tick=lambda: dispatcher.tick(service.store),
            )
        result = execute_command(service, str(arguments.identity).strip() or "unknown", arguments.command, vars(arguments))
        if arguments.command == "status" and arguments.oneliner and not arguments.as_json:
            print("agent-bridge {0}: {1} assigned task(s)".format(arguments.identity, len(result["tasks"])))
        else:
            print(render(result, arguments.as_json))
        if arguments.command == "doctor" and arguments.strict and not result["ok"]:
            return 1
        return 0
    except (CommandUnavailable, KeyError, PermissionError, ValueError, RuntimeError) as error:
        print(render(error_view(error), arguments.as_json), file=os.sys.stderr)
        return 1
    finally:
        service.store.close()


if __name__ == "__main__":
    raise SystemExit(main())
