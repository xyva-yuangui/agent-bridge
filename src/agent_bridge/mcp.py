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


def _tool(command: str) -> Dict[str, Any]:
    return {
        "name": "bridge_" + command.replace("-", "_"),
        "description": "Execute bridge {0} through the v2 service.".format(command),
        "inputSchema": {"type": "object", "properties": {}},
    }


def _response(request_id: Any, result: Optional[Dict[str, Any]] = None, error: Optional[Dict[str, Any]] = None) -> None:
    message: Dict[str, Any] = {"jsonrpc": "2.0", "id": request_id}
    if error is None:
        message["result"] = result or {}
    else:
        message["error"] = error
    print(json.dumps(message, ensure_ascii=True), flush=True)


def _tool_result(value: Dict[str, Any], is_error: bool = False) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=True, sort_keys=True)}], "isError": is_error}


def _handle(service: Any, identity: str, request: Dict[str, Any]) -> None:
    request_id = request.get("id")
    method = request.get("method")
    if method == "initialize":
        _response(request_id, {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "agent-bridge", "version": BRIDGE_VERSION}})
        return
    if method == "tools/list":
        _response(request_id, {"tools": [_tool(command) for command in COMMANDS if command not in MCP_EXCLUDED_COMMANDS]})
        return
    if method == "tools/call":
        parameters = request.get("params") or {}
        name = parameters.get("name")
        command = str(name or "").removeprefix("bridge_").replace("_", "-")
        if command not in COMMANDS or command in MCP_EXCLUDED_COMMANDS:
            _response(request_id, error={"code": -32602, "message": "unknown tool: {0}".format(name)})
            return
        try:
            value = execute_command(service, identity, command, dict(parameters.get("arguments") or {}))
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
                continue
            _handle(service, identity, request)
    finally:
        service.store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
