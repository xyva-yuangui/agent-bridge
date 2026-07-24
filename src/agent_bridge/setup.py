"""Reversible host setup lifecycle independent from the SQLite service."""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

from .adapters import ADAPTER_TYPES, adapter_for
from .adapters.base import HostAdapter, canonical_host_name
from .managed_config import MANAGED_CONFIG_VERSION, OWNER, ManagedMutation, backup_file, content_hash
from .notifications import windows_notification_capability, macos_notification_capability


def _home(value: Optional[Path]) -> Path:
    return (Path.home() if value is None else Path(value)).expanduser().absolute()


def _data_root(home: Path) -> Path:
    root = home / ".agent-bridge"
    resolved_home = home.resolve(strict=False)
    resolved_root = root.resolve(strict=False)
    if resolved_root.parent != resolved_home or resolved_root.name != ".agent-bridge":
        raise ValueError("refusing an unsafe Agent Bridge data root")
    return root


@dataclass(frozen=True)
class SetupPlan:
    home: Path
    mutations: Tuple[ManagedMutation, ...]
    adapters: Tuple[HostAdapter, ...]
    scope: Tuple[str, ...]


@dataclass(frozen=True)
class SetupReport:
    planned_hosts: Tuple[str, ...]
    applied_hosts: Tuple[str, ...] = ()
    removed_hosts: Tuple[str, ...] = ()
    dry_run: bool = False
    backups: Tuple[str, ...] = ()
    rollback: Tuple[Dict[str, str], ...] = ()
    purged_data_root: Optional[str] = None


def _select_adapters(home: Path, agent: Optional[str]) -> Tuple[HostAdapter, ...]:
    if agent:
        return (adapter_for(canonical_host_name(agent), home),)
    return tuple(adapter_type(home) for adapter_type in ADAPTER_TYPES)


def _owns_integration(adapter: HostAdapter) -> bool:
    """Do not let uninstall/repair serialize a merely nearby user config."""
    try:
        return adapter._installation_artifact_is_valid() or adapter._consumer_is_installed()
    except (OSError, ValueError):
        return False


def build_setup_plan(*, home: Optional[Path] = None, auto: bool = False, agent: Optional[str] = None) -> SetupPlan:
    """Create a no-write plan for currently detected hosts and an optional scope."""
    user_home = _home(home)
    adapters = _select_adapters(user_home, agent)
    selected = tuple(item for item in adapters if item._valid_installation_marker())
    mutations = []
    for item in selected:
        source = item.config_path.read_bytes() if item.config_path.exists() and item.config_path.is_file() else b""
        original_hash = content_hash(source)
        backup_path = _data_root(user_home) / "backups" / item.name / (item.config_path.name + "." + original_hash[:16] + ".bak")
        mutations.append(ManagedMutation(
            target=item.config_path,
            owner=OWNER,
            version=MANAGED_CONFIG_VERSION,
            original_hash=original_hash,
            backup_path=backup_path,
            validation="adapter.detect().found",
            inverse="adapter.uninstall()",
        ))
    return SetupPlan(user_home, tuple(mutations), selected, tuple(item.name for item in selected))


def apply_setup_plan(plan: SetupPlan, *, dry_run: bool = False) -> SetupReport:
    """Apply in order; validation failures invoke exact owned inverses in reverse."""
    if not isinstance(plan, SetupPlan):
        raise TypeError("plan must be a SetupPlan")
    if dry_run:
        return SetupReport(plan.scope, dry_run=True)
    data_root = _data_root(plan.home)
    applied = []
    backups = []
    rollback = []
    try:
        for mutation in plan.mutations:
            adapter = next(
                item for item in plan.adapters if item.config_path == mutation.target
            )
            current = adapter.config_path.read_bytes() if adapter.config_path.exists() else b""
            if content_hash(current) != mutation.original_hash:
                raise RuntimeError("planned host config changed: {0}".format(adapter.name))
            backup = backup_file(adapter.config_path, data_root / "backups" / adapter.name)
            if backup is not None:
                backups.append(str(backup))
            adapter.install()
            if not adapter.detect().found:
                raise RuntimeError("post-install validation failed for host: {0}".format(adapter.name))
            applied.append(adapter)
    except BaseException as error:
        for adapter in reversed(applied):
            try:
                adapter.uninstall()
                rollback.append({"host": adapter.name, "outcome": "removed owned integration"})
            except BaseException as rollback_error:
                rollback.append({"host": adapter.name, "outcome": "failed: {0}".format(rollback_error)})
        raise RuntimeError("setup failed: {0}; rollback={1}".format(error, rollback)) from error
    return SetupReport(plan.scope, tuple(item.name for item in applied), backups=tuple(backups))


def repair(*, home: Optional[Path] = None, agent: Optional[str] = None, dry_run: bool = False) -> SetupReport:
    """Re-apply the same owned configuration; repeated repair is idempotent."""
    plan = build_setup_plan(home=home, auto=True, agent=agent)
    owned = tuple(item for item in plan.adapters if _owns_integration(item))
    owned_targets = {item.config_path for item in owned}
    repair_plan = SetupPlan(plan.home, tuple(item for item in plan.mutations if item.target in owned_targets), owned, tuple(item.name for item in owned))
    return apply_setup_plan(repair_plan, dry_run=dry_run)


def _notification_status() -> Dict[str, object]:
    capability = macos_notification_capability() if sys.platform == "darwin" else windows_notification_capability()
    return {"available": capability.available, "helper_path": capability.helper_path, "detail": capability.detail}


def status(*, home: Optional[Path] = None, agent: Optional[str] = None) -> Dict[str, object]:
    user_home = _home(home)
    hosts = []
    for adapter in _select_adapters(user_home, agent):
        health = adapter.health_check()
        hosts.append({
            "host": adapter.name,
            "installed": health.ok,
            "capabilities": {
                "surface": health.capabilities.surface.value,
                "can_ack": health.capabilities.can_ack,
                "can_open_terminal": health.capabilities.can_open_terminal,
                "can_receive_context": health.capabilities.can_receive_context,
                "protocol_version": health.capabilities.protocol_version,
                "integration_version": health.capabilities.integration_version,
            },
            "degradation": health.warning,
            "launch_policy": "terminal-fallback" if not health.capabilities.can_open_terminal else "integrated-terminal",
        })
    return {"owner": OWNER, "managed_config_version": MANAGED_CONFIG_VERSION, "hosts": hosts, "notifications": _notification_status()}


def uninstall(*, home: Optional[Path] = None, agent: Optional[str] = None, purge_data: bool = False, dry_run: bool = False) -> SetupReport:
    """Remove owned host integration artifacts; data deletion requires opt-in."""
    user_home = _home(home)
    adapters = _select_adapters(user_home, agent)
    removable = tuple(item for item in adapters if _owns_integration(item))
    if dry_run:
        return SetupReport(tuple(item.name for item in removable), removed_hosts=tuple(item.name for item in removable), dry_run=True)
    removed = []
    for item in removable:
        # Adapter removal only removes exact blocks/registrations that it owns.
        item.uninstall()
        item._remove_installation_artifact()
        removed.append(item.name)
    purged = None
    if purge_data:
        data_root = _data_root(user_home)
        if data_root.exists():
            if data_root.is_symlink() or not data_root.is_dir():
                raise ValueError("refusing to purge a non-directory data root")
            shutil.rmtree(data_root)
        purged = str(data_root)
    return SetupReport(tuple(item.name for item in removable), removed_hosts=tuple(removed), purged_data_root=purged)
