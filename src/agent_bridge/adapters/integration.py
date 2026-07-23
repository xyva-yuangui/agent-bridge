"""Stdio JSON-RPC consumer for installed host session cards."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional, Sequence

from ..service import BridgeService
from ..store import Store
from . import adapter_for
from .base import TaskAcknowledgement


TOOLS = ("list_task_cards", "read_task_card", "acknowledge")


def _response(request_id: Any, result: Optional[dict] = None, error: Optional[str] = None) -> None:
    message = {"jsonrpc": "2.0", "id": request_id}
    if error is None:
        message["result"] = result or {}
    else:
        message["error"] = {"code": -32000, "message": error}
    print(json.dumps(message, ensure_ascii=False, sort_keys=True), flush=True)


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
                    _response(request_id, {"serverInfo": {"name": "agent-bridge-host-consumer", "version": "1.0.0"}, "capabilities": {"tools": {}}})
                elif method == "tools/list":
                    _response(request_id, {"tools": [{"name": name} for name in TOOLS]})
                elif method == "tools/call":
                    name = params.get("name")
                    arguments = params.get("arguments", {})
                    if not isinstance(arguments, dict):
                        raise ValueError("tool arguments must be an object")
                    if name == "list_task_cards":
                        cards = []
                        adapter._assert_contained(adapter.inbox_path)
                        for path in sorted(adapter.inbox_path.glob("*.json")):
                            card = adapter._read_card(path.stem)
                            if card is not None:
                                cards.append(card)
                        _response(request_id, {"cards": cards})
                    elif name == "read_task_card":
                        card = adapter._read_card(str(arguments.get("task_id", "")))
                        if card is None:
                            raise ValueError("queued task card is unavailable")
                        _response(request_id, {"card": card})
                    elif name == "acknowledge":
                        task_id = str(arguments.get("task_id", ""))
                        acknowledgement = TaskAcknowledgement(adapter.name, task_id, str(arguments.get("integration_version", "")), arguments.get("protocol_version"), str(arguments.get("delivery_token", "")))
                        prepared = adapter.integration_acknowledgement(task_id)
                        if acknowledgement != prepared:
                            raise ValueError("acknowledgement does not match queued task card")
                        service.acknowledge_integration(task_id, adapter.name, prepared.integration_version, prepared.protocol_version, prepared.delivery_token)
                        adapter.consume_acknowledged_card(task_id, prepared.delivery_token)
                        _response(request_id, {"acknowledged": True})
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
    parsed = parser.parse_args(argv)
    if parsed.command == "serve":
        return serve(parsed.host, Path(parsed.home), Path(parsed.data_root))
    if parsed.command == "--health":
        adapter = adapter_for(parsed.host, Path(parsed.home))
        return 0 if adapter.detect().found and adapter._consumer_is_installed() else 1
    parser.print_usage(sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
