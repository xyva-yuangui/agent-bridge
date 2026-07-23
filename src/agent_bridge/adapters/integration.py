"""Host-consumer entrypoint for preparing an explicit Agent Bridge ACK."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional, Sequence

from . import adapter_for


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="agent-bridge-host-consumer")
    parser.add_argument("--host", required=True)
    parser.add_argument("--home", required=True)
    parser.add_argument("--task-id", required=True)
    parsed = parser.parse_args(argv)
    try:
        acknowledgement = adapter_for(parsed.host, Path(parsed.home)).integration_acknowledgement(parsed.task_id)
    except (KeyError, ValueError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 1
    payload = acknowledgement.as_shared_payload()
    payload["delivery_token"] = acknowledgement.delivery_token
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
