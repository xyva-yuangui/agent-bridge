"""Durable in-application session-card delivery for installed host receipts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Union

from .adapters import adapter_for
from .adapters.base import TaskCard
from .models import DeliveryStatus
from .service import BridgeService
from .store import Store


class HostDeliveryChannel:
    """Deliver to the recipient's installed session-card consumer.

    The host home is read from the receiver's owned setup receipt, rather than
    from a sender-controlled outbox payload or the process HOME environment.
    """

    effect_kind = "host"

    def __init__(self, database_path: Union[str, Path]) -> None:
        self.database_path = str(database_path)

    def _adapter(self, recipient: str):
        data_root = Path(self.database_path).parent
        receipt = data_root / "agents" / recipient / "agent-bridge-profile.json"
        profile = data_root / "agents" / recipient / "agent.json"
        try:
            owned = json.loads(receipt.read_text(encoding="utf-8"))
            value = json.loads(profile.read_text(encoding="utf-8"))
            home = value["home"]
            owned_profile = Path(str(owned.get("profile", ""))).resolve(strict=False)
            if owned.get("owner") != "agent-bridge" or owned_profile != profile.resolve(strict=False):
                raise ValueError("unowned host profile")
            if not isinstance(home, str) or not Path(home).is_absolute():
                raise ValueError("invalid host home")
            return adapter_for(recipient, Path(home))
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def applicable(self, item: Any) -> bool:
        recipient = item.payload.get("recipient")
        if not isinstance(recipient, str) or not recipient:
            return False
        adapter = self._adapter(recipient)
        return adapter is not None and adapter.detect().found and adapter.capabilities().can_receive_context

    def deliver(self, item: Any, idempotency_key: str, timeout_seconds: float) -> DeliveryStatus:
        del idempotency_key, timeout_seconds
        task_id = item.payload.get("task_id")
        recipient = item.payload.get("recipient")
        if not isinstance(task_id, str) or not isinstance(recipient, str):
            raise RuntimeError("host delivery item requires task_id and recipient")
        adapter = self._adapter(recipient)
        if adapter is None:
            raise RuntimeError("recipient has no owned host integration receipt")
        store = Store.open(Path(self.database_path))
        try:
            row = store.connection.execute(
                "SELECT subject, body, assignee FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if row is None or str(row["assignee"]) != adapter.name:
                raise RuntimeError("host delivery task is no longer addressed to the recipient")
            result = adapter.notify_in_app(TaskCard(task_id, str(row["subject"]), str(row["body"])), BridgeService(store))
            if not result.ok:
                raise RuntimeError(result.message)
            # `notify_in_app` only proves that the card was durably accepted;
            # agent acknowledgement is recorded later by the host consumer.
            return DeliveryStatus.PLUGIN_DELIVERED
        finally:
            store.close()
