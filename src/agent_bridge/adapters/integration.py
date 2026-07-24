"""Stdio JSON-RPC consumer for installed host session cards."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from ..service import BridgeService
from ..store import Store
from . import adapter_for
from .base import TaskAcknowledgement


# Standard MCP names keep every installed desktop consumer usable without a
# host-specific private protocol.  Cards remain an optional presentation
# surface; lifecycle actions always bind to this process's fixed host identity.
TOOLS = (
    "bridge_inbox", "bridge_show", "bridge_ack", "bridge_claim",
    "bridge_question", "bridge_answer", "bridge_review", "bridge_done",
)

_STRING = {"type": "string"}
_INTEGER = {"type": "integer"}


def _schema(properties: Dict[str, Dict[str, Any]], required=()) -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


TOOL_SCHEMAS = {
    "bridge_inbox": _schema({"limit": _INTEGER}),
    "bridge_show": _schema({"task_id": _STRING}, ("task_id",)),
    "bridge_ack": _schema({
        "task_id": _STRING,
        "integration_version": _STRING,
        "protocol_version": _INTEGER,
        "delivery_token": _STRING,
    }, ("task_id", "integration_version", "protocol_version", "delivery_token")),
    "bridge_claim": _schema({"task_id": _STRING, "body": _STRING}, ("task_id",)),
    "bridge_question": _schema({"task_id": _STRING, "body": _STRING}, ("task_id", "body")),
    "bridge_answer": _schema({"task_id": _STRING, "body": _STRING}, ("task_id", "body")),
    "bridge_review": _schema({
        "task_id": _STRING,
        "verdict": {"type": "string", "enum": ["approve", "changes"]},
        "body": _STRING,
    }, ("task_id",)),
    "bridge_done": _schema({"task_id": _STRING, "result": _STRING}, ("task_id",)),
}


def _response(request_id: Any, result: Optional[dict] = None, error: Optional[str] = None) -> None:
    message = {"jsonrpc": "2.0", "id": request_id}
    if error is None:
        message["result"] = result or {}
    else:
        message["error"] = {"code": -32000, "message": error}
    print(json.dumps(message, ensure_ascii=False, sort_keys=True), flush=True)


def _validate_tool(name: Any, arguments: Any) -> Dict[str, Any]:
    if name not in TOOL_SCHEMAS:
        raise ValueError("unknown tool")
    if not isinstance(arguments, dict):
        raise ValueError("tool arguments must be an object")
    schema = TOOL_SCHEMAS[str(name)]
    unknown = set(arguments).difference(schema["properties"])
    if unknown:
        raise ValueError("unknown argument: {0}".format(sorted(unknown)[0]))
    for required in schema["required"]:
        if required not in arguments:
            raise ValueError("missing required argument: {0}".format(required))
    for key, value in arguments.items():
        expected = schema["properties"][key]["type"]
        if expected == "string":
            valid = isinstance(value, str)
        else:
            valid = isinstance(value, int) and not isinstance(value, bool)
        if not valid:
            raise ValueError("argument {0} must be a {1}".format(key, expected))
        choices = schema["properties"][key].get("enum")
        if choices is not None and value not in choices:
            raise ValueError("argument {0} is not an allowed value".format(key))
    return dict(arguments)


def _task_payload(task: Any) -> Dict[str, Any]:
    value = dict(task.__dict__)
    value["state"] = task.state.value
    return value


def serve(host: str, home: Path, data_root: Path) -> int:
    adapter = adapter_for(host, home)
    if not adapter.detect().found or not adapter._consumer_is_installed():
        return 2
    store = Store.open(data_root / "agent-bridge.sqlite3")
    service = BridgeService(store)
    try:
        _recover_acknowledged_cards(adapter, service)
        for line in sys.stdin:
            request = None
            try:
                request = json.loads(line)
                if not isinstance(request, dict):
                    _response(None, error="invalid request")
                    continue
                request_id = request.get("id")
                if request.get("jsonrpc") != "2.0":
                    raise ValueError("invalid JSON-RPC version")
                method = request.get("method")
                params = request.get("params", {})
                if not isinstance(params, dict):
                    raise ValueError("params must be an object")
                if method == "initialize":
                    _response(request_id, {
                        "protocolVersion": "2024-11-05",
                        "serverInfo": {"name": "agent-bridge-host-consumer", "version": "2.0.0"},
                        "capabilities": {"tools": {}},
                    })
                elif method == "notifications/initialized":
                    continue
                elif method == "ping":
                    _response(request_id, {})
                elif method == "tools/list":
                    _response(request_id, {"tools": [
                        {
                            "name": name,
                            "description": "Agent Bridge host-bound {0} operation.".format(name.removeprefix("bridge_")),
                            "inputSchema": TOOL_SCHEMAS[name],
                        }
                        for name in TOOLS
                    ]})
                elif method == "tools/call":
                    name = params.get("name")
                    arguments = _validate_tool(name, params.get("arguments", {}))
                    if name == "bridge_inbox":
                        page = service.inbox(adapter.name, int(arguments.get("limit", 100)))
                        _response(request_id, {"tasks": [_task_payload(task) for task in page.tasks], "next_cursor": page.next_cursor})
                    elif name == "bridge_show":
                        task = service.show(str(arguments.get("task_id", "")))
                        _response(request_id, {"task": _task_payload(task)})
                    elif name == "bridge_ack":
                        task_id = str(arguments.get("task_id", ""))
                        acknowledgement = TaskAcknowledgement(adapter.name, task_id, str(arguments.get("integration_version", "")), arguments.get("protocol_version"), str(arguments.get("delivery_token", "")))
                        prepared = adapter.integration_acknowledgement(task_id)
                        if acknowledgement != prepared:
                            raise ValueError("acknowledgement does not match queued task card")
                        service.acknowledge_integration(task_id, adapter.name, prepared.integration_version, prepared.protocol_version, prepared.delivery_token)
                        adapter.consume_acknowledged_card(task_id, prepared.delivery_token)
                        _response(request_id, {"acknowledged": True})
                    elif name == "bridge_claim":
                        task = service.claim(str(arguments.get("task_id", "")), adapter.name, str(arguments.get("body", "")))
                        _response(request_id, {"task": _task_payload(task)})
                    elif name == "bridge_question":
                        task = service.question(str(arguments.get("task_id", "")), adapter.name, str(arguments.get("body", "")))
                        _response(request_id, {"task": _task_payload(task)})
                    elif name == "bridge_answer":
                        task = service.answer(str(arguments.get("task_id", "")), adapter.name, str(arguments.get("body", "")))
                        _response(request_id, {"task": _task_payload(task)})
                    elif name == "bridge_review":
                        task_id = str(arguments.get("task_id", ""))
                        verdict = arguments.get("verdict")
                        task = service.request_review(task_id, adapter.name, str(arguments.get("body", ""))) if verdict is None else service.review(task_id, adapter.name, str(verdict), str(arguments.get("body", "")))
                        _response(request_id, {"task": _task_payload(task)})
                    elif name == "bridge_done":
                        task = service.done(str(arguments.get("task_id", "")), adapter.name, str(arguments.get("result", "")))
                        _response(request_id, {"task": _task_payload(task)})
                    else:
                        raise ValueError("unknown tool")
                else:
                    _response(request_id, error="unknown method")
            except json.JSONDecodeError:
                message = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
                print(json.dumps(message, ensure_ascii=False, sort_keys=True), flush=True)
            except (KeyError, TypeError, ValueError) as error:
                _response(request.get("id") if isinstance(request, dict) else None, error=str(error))
            except Exception:
                _response(request.get("id") if isinstance(request, dict) else None, error="internal server error")
    finally:
        store.close()
    return 0


def hook(host: str, home: Path, data_root: Path) -> int:
    """Claude SessionStart hook: consume event input once, emit a task card.

    Claude hook stdin is an event object, not a JSON-RPC stream.  Treating it
    as RPC caused a parse/error loop and never surfaced actual work.
    """
    adapter = adapter_for(host, home)
    if not adapter.detect().found or not adapter._consumer_is_installed():
        return 2
    try:
        raw = sys.stdin.read(64 * 1024)
        if raw.strip():
            event = json.loads(raw)
            if not isinstance(event, dict):
                raise ValueError("invalid hook event")
        store = Store.open(data_root / "agent-bridge.sqlite3")
        try:
            service = BridgeService(store)
            _recover_acknowledged_cards(adapter, service)
            _consume_pending_cards(adapter, service)
            tasks = service.inbox(adapter.name, 10).tasks
        finally:
            store.close()
        summary = "No pending Agent Bridge tasks." if not tasks else "Agent Bridge tasks:\n" + "\n".join("- [{0}] {1}: {2}".format(task.id[:8], task.state.value, task.subject) for task in tasks)
        summary += "\nUse the installed `bridge --as claude` commands to read, claim, question, review, or complete these tasks."
        print(json.dumps({"continue": True, "additionalContext": summary}, ensure_ascii=False), flush=True)
        return 0
    except (OSError, ValueError, json.JSONDecodeError):
        # Hooks must not turn a malformed host event into an agent crash.
        print(json.dumps({"continue": True, "additionalContext": "Agent Bridge task card unavailable."}), flush=True)
        return 0


def _recover_acknowledged_cards(adapter, service: BridgeService) -> None:
    """Finish filesystem cleanup after a crash following the durable ACK commit."""
    try:
        adapter._assert_contained(adapter.inbox_path)
        if not adapter.inbox_path.is_dir() or adapter.inbox_path.is_symlink():
            return
        paths = tuple(adapter.inbox_path.glob("*.json"))
    except (OSError, ValueError):
        return
    for path in paths:
        if path.is_symlink():
            continue
        try:
            acknowledgement = adapter.integration_acknowledgement(path.stem)
            if service.host_acknowledgement_is_claimed(
                acknowledgement.task_id, acknowledgement.host_identity, acknowledgement.delivery_token,
            ):
                adapter.consume_acknowledged_card(acknowledgement.task_id, acknowledgement.delivery_token)
        except (OSError, ValueError):
            continue


def _consume_pending_cards(adapter, service: BridgeService, limit: int = 100) -> None:
    """Consume the bounded card batch delivered to a one-shot host hook.

    Claude SessionStart is itself the integration consumer, so a successful
    hook invocation is the independent evidence boundary.  MCP-based hosts
    instead acknowledge explicitly through ``bridge_ack``.
    """
    try:
        adapter._assert_contained(adapter.inbox_path)
        if not adapter.inbox_path.is_dir() or adapter.inbox_path.is_symlink():
            return
        paths = tuple(sorted(adapter.inbox_path.glob("*.json")))[:limit]
    except (OSError, ValueError):
        return
    for path in paths:
        if path.is_symlink():
            continue
        try:
            acknowledgement = adapter.integration_acknowledgement(path.stem)
            service.acknowledge_integration(
                acknowledgement.task_id,
                acknowledgement.host_identity,
                acknowledgement.integration_version,
                acknowledgement.protocol_version,
                acknowledgement.delivery_token,
            )
            adapter.consume_acknowledged_card(
                acknowledgement.task_id,
                acknowledgement.delivery_token,
            )
        except (OSError, ValueError):
            continue


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="agent-bridge-host-consumer")
    subparsers = parser.add_subparsers(dest="command")
    serve_parser = subparsers.add_parser("serve")
    serve_parser.add_argument("--host", required=True)
    serve_parser.add_argument("--home", required=True)
    serve_parser.add_argument("--data-root", required=True)
    health_parser = subparsers.add_parser("--health")
    health_parser.add_argument("--host", required=True)
    health_parser.add_argument("--home", required=True)
    hook_parser = subparsers.add_parser("hook")
    hook_parser.add_argument("--host", required=True)
    hook_parser.add_argument("--home", required=True)
    hook_parser.add_argument("--data-root", required=True)
    parsed = parser.parse_args(argv)
    if parsed.command == "serve":
        return serve(parsed.host, Path(parsed.home), Path(parsed.data_root))
    if parsed.command == "--health":
        adapter = adapter_for(parsed.host, Path(parsed.home))
        return 0 if adapter.detect().found and adapter._consumer_is_installed() else 1
    if parsed.command == "hook":
        return hook(parsed.host, Path(parsed.home), Path(parsed.data_root))
    parser.print_usage(sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
