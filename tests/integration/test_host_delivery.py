from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_bridge.adapters import ADAPTER_TYPES
from agent_bridge.dispatcher import Dispatcher
from agent_bridge.host_delivery import HostDeliveryChannel
from agent_bridge.path_ownership import PosixPathBackend
from agent_bridge.service import BridgeService
from agent_bridge.setup import apply_setup_plan, build_setup_plan
from agent_bridge.store import Store


class ProductionHostDeliveryTests(unittest.TestCase):
    def test_one_setup_routes_real_dispatch_cards_to_all_four_desktop_hosts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "portable home"
            home.mkdir()
            for adapter_type in ADAPTER_TYPES:
                adapter = adapter_type(home)
                adapter.config_path.parent.mkdir(parents=True, exist_ok=True)
                adapter.config_path.write_text(
                    "" if adapter.fixture_suffix == ".toml" else "{}",
                    encoding="utf-8",
                )

            with patch("agent_bridge.setup._install_windows_native"), patch(
                "agent_bridge.setup._install_macos_native"
            ):
                report = apply_setup_plan(
                    build_setup_plan(home=home, auto=True),
                    path_backend=PosixPathBackend({}),
                )
            self.assertEqual(
                {"codex", "claude", "reasonix", "zcode"},
                set(report.applied_hosts),
            )

            database = home / ".agent-bridge" / "agent-bridge.sqlite3"
            store = Store.open(database)
            try:
                service = BridgeService(store)
                tasks = {
                    name: service.send_task("coordinator", name, "Production route", name)
                    for name in ("codex", "claude", "reasonix", "zcode")
                }
                dispatched = Dispatcher(
                    store, {"host": HostDeliveryChannel(database)}
                ).run_burst()

                self.assertEqual(4, dispatched.delivered)
                self.assertEqual(0, dispatched.retried)
                self.assertEqual(
                    4,
                    store.scalar(
                        "SELECT COUNT(*) FROM delivery_attempts "
                        "WHERE channel = 'host' AND status = 'plugin_delivered'"
                    ),
                )
                self.assertEqual(
                    0,
                    store.scalar(
                        "SELECT COUNT(*) FROM outbox WHERE completed_at IS NULL"
                    ),
                )
                for adapter_type in ADAPTER_TYPES:
                    adapter = adapter_type(home)
                    card = json.loads(
                        adapter.task_card_path(tasks[adapter.name].id).read_text(
                            encoding="utf-8"
                        )
                    )
                    self.assertEqual(adapter.name, card["host_identity"])
                    self.assertEqual(tasks[adapter.name].id, card["task_id"])
                    self.assertNotIn("coordinator", card["delivery_token"])
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
