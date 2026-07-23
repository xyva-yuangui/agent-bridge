"""A stdio JSON-RPC MCP server backed directly by the v2 service layer."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, Optional, Sequence

from .cli import MCP_EXCLUDED_COMMANDS, execute_command, open_service, parse_identity
from .presentation import configure_streams, error_view
from .version import BRIDGE_VERSION


COMMANDS = (
    "status", "inbox", "send", "claim", "done", "show", "board", "question", "answer",
    "review", "wake", "agents", "activity", "context", "clean", "doctor", "project",
    "whoami", "who-coordinates", "log", "migrate", "export",
)


def _schema(properties: Dict[str, Dict[str, Any]], required: Sequence[str] = ()) -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


STRING = {"type": "string"}
BOOLEAN = {"type": "boolean"}
INTEGER = {"type": "integer"}

TOOL_SCHEMAS = {
    "status": _schema({"oneliner": BOOLEAN, "actor": STRING}),
    "inbox": _schema({"limit": INTEGER, "cursor": STRING, "actor": STRING}),
    "send": _schema({"to": STRING, "subject": STRING, "body": STRING, "project": STRING, "no_wake": BOOLEAN}, ("to", "subject")),
    "claim": _schema({"task_id": STRING, "body": STRING, "actor": STRING}, ("task_id",)),
    "done": _schema({"task_id": STRING, "result": STRING, "files": STRING, "actor": STRING}, ("task_id",)),
    "show": _schema({"task_id": STRING}, ("task_id",)),
    "board": _schema({"project": STRING}),
    "question": _schema({"task_id": STRING, "body": STRING, "actor": STRING}, ("task_id", "body")),
    "answer": _schema({"task_id": STRING, "body": STRING, "actor": STRING}, ("task_id", "body")),
    "review": _schema({"task_id": STRING, "verdict": {"type": "string", "enum": ["approve", "changes"]}, "body": STRING, "actor": STRING}, ("task_id",)),
    "wake": _schema({"agent": STRING, "project": STRING}, ("agent",)),
    "agents": _schema({}),
    "activity": _schema({"project": STRING, "since": STRING}),
    "context": _schema({"project": STRING, "show": BOOLEAN, "add": STRING}),
    "clean": _schema({"project": STRING, "days": INTEGER, "all": BOOLEAN, "status": STRING, "dry_run": BOOLEAN}),
    "doctor": _schema({"strict": BOOLEAN}),
    "project": _schema({"action": {"type": "string", "enum": ["init", "list", "show"]}, "name": STRING, "workspace": STRING, "goal": STRING}, ("action",)),
    "whoami": _schema({}),
    "who-coordinates": _schema({"project": STRING}),
    "log": _schema({"what": STRING, "project": STRING}, ("what",)),
    "migrate": _schema({"source": STRING}, ("source",)),
    "export": _schema({"destination": STRING}, ("destination",)),
}


def _tool(command: str) -> Dict[str, Any]:
    return {
        "name": "bridge_" + command.replace("-", "_"),
        "description": "Execute bridge {0} through the v2 service.".format(command),
        "inputSchema": TOOL_SCHEMAS[command],
    }


def _validate_arguments(command: str, arguments: Any) -> Dict[str, Any]:
    if not isinstance(arguments, dict):
        raise ValueError("tool arguments must be an object")
    schema = TOOL_SCHEMAS[command]
    properties = schema["properties"]
    unknown = set(arguments).difference(properties)
    if unknown:
        raise ValueError("unknown argument: {0}".format(sorted(unknown)[0]))
    for name in schema["required"]:
        if name not in arguments:
            raise ValueError("missing required argument: {0}".format(name))
    for name, value in arguments.items():
        expected = properties[name]["type"]
        valid = (
            (expected == "string" and isinstance(value, str))
            or (expected == "boolean" and isinstance(value, bool))
            or (expected == "integer" and isinstance(value, int) and not isinstance(value, bool))
        )
        if not valid:
            raise ValueError("argument {0} must be a {1}".format(name, expected))
        choices = properties[name].get("enum")
        if choices is not None and value not in choices:
            raise ValueError("argument {0} is not an allowed value".format(name))
    return dict(arguments)


def _response(request_id: Any, result: Optional[Dict[str, Any]] = None, error: Optional[Dict[str, Any]] = None) -> None:
    message: Dict[str, Any] = {"jsonrpc": "2.0", "id": request_id}
    if error is None:
        message["result"] = result or {}
    else:
        message["error"] = error
    print(json.dumps(message, ensure_ascii=True), flush=True)


def _tool_result(value: Dict[str, Any], is_error: bool = False) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=True, sort_keys=True)}], "isError": is_error}


def _handle(service: Any, identity: str, request: Any) -> None:
    if not isinstance(request, dict):
        _response(None, error={"code": -32600, "message": "request must be an object"})
        return
    request_id = request.get("id")
    method = request.get("method")
    if request.get("jsonrpc") != "2.0" or not isinstance(method, str):
        _response(request_id, error={"code": -32600, "message": "invalid JSON-RPC request"})
        return
    parameters = request.get("params", {})
    if not isinstance(parameters, dict):
        _response(request_id, error={"code": -32602, "message": "params must be an object"})
        return
    if method == "initialize":
        _response(request_id, {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "agent-bridge", "version": BRIDGE_VERSION}})
        return
    if method == "tools/list":
        _response(request_id, {"tools": [_tool(command) for command in COMMANDS if command not in MCP_EXCLUDED_COMMANDS]})
        return
    if method == "tools/call":
        name = parameters.get("name")
        command = str(name or "").removeprefix("bridge_").replace("_", "-")
        if command not in COMMANDS or command in MCP_EXCLUDED_COMMANDS:
            _response(request_id, error={"code": -32602, "message": "unknown tool: {0}".format(name)})
            return
        try:
            arguments = _validate_arguments(command, parameters.get("arguments", {}))
        except ValueError as error:
            _response(request_id, error={"code": -32602, "message": str(error)})
            return
        try:
            value = execute_command(service, identity, command, arguments)
            _response(request_id, _tool_result(value))
        except (KeyError, PermissionError, ValueError, RuntimeError) as error:
            _response(request_id, _tool_result(error_view(error), True))
        return
    if method == "ping":
        _response(request_id, {})
    elif method != "notifications/initialized" and request_id is not None:
        _response(request_id, error={"code": -32601, "message": "method not found: {0}".format(method)})


def main(argv: Optional[Sequence[str]] = None) -> int:
    configure_streams()
    parser = argparse.ArgumentParser(prog="bridge-mcp")
    parser.add_argument("--as", dest="identity", default=None)
    parsed = parser.parse_args(argv)
    identity = parse_identity(["--as", parsed.identity]) if parsed.identity is not None else parse_identity([])
    service = open_service()
    try:
        for line in sys.stdin:
            if not line.strip():
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                _response(None, error={"code": -32700, "message": "parse error"})
                continue
            _handle(service, identity, request)
    finally:
        service.store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
