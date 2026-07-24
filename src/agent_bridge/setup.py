"""Reversible host setup lifecycle independent from the SQLite service."""

from __future__ import annotations

import shutil
import sys
import os
import json
import hashlib
import tempfile
import shutil as _which_shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

from .adapters import ADAPTER_TYPES, adapter_for
from .adapters.base import HostAdapter, canonical_host_name
from .managed_config import MANAGED_CONFIG_VERSION, OWNER, ManagedMutation, backup_file, content_hash
from .notifications import WindowsNotifier, windows_notification_capability, macos_notification_capability


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
    effects: Tuple[ManagedMutation, ...] = ()
    requested_agent: Optional[str] = None


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


def _host_application_present(adapter: HostAdapter) -> bool:
    """Probe host-owned config/executable state, never our integration receipt."""
    names = (adapter.name, adapter.name + "-cli")
    return adapter._valid_installation_marker() or adapter.config_path.exists() or any(
        _which_shutil.which(name) is not None for name in names
    )


def _owns_integration(adapter: HostAdapter) -> bool:
    """Do not let uninstall/repair serialize a merely nearby user config."""
    try:
        # Uninstall must remain possible after a Python/runtime upgrade makes
        # the live entrypoint unhealthy.  The receipt is durable ownership,
        # whereas consumer health is deliberately version-sensitive.
        if adapter.installation_artifact_path.is_file() and not adapter.installation_artifact_path.is_symlink():
            receipt = json.loads(adapter.installation_artifact_path.read_text(encoding="utf-8"))
            if isinstance(receipt, dict) and receipt.get("host_identity") == adapter.name and isinstance(receipt.get("entrypoint"), list):
                return True
        return adapter._consumer_is_installed()
    except (OSError, ValueError):
        return False


def _source_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _write_owned_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True)
            stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _runtime_receipt(home: Path) -> Path:
    return _data_root(home) / "runtime-receipt.json"


def _install_runtime(home: Path) -> None:
    """Copy the compatibility runtime used by retained bridge.py launchers."""
    data = _data_root(home); root = _source_root()
    data.mkdir(parents=True, exist_ok=True)
    stage = data / (".skill-stage-" + next(tempfile._get_candidate_names()))
    skill = data / "skill"; backup = data / (".skill-backup-" + next(tempfile._get_candidate_names()))
    try:
        shutil.copytree(root / "scripts", stage / "scripts")
        shutil.copytree(root / "src" / "agent_bridge", stage / "runtime" / "agent_bridge")
        for name in ("SKILL.md", "README.md", "README.zh-CN.md"):
            if (root / name).is_file(): shutil.copy2(root / name, stage / name)
        if skill.exists(): os.replace(skill, backup)
        os.replace(stage, skill)
        if backup.exists(): shutil.rmtree(backup)
    except BaseException:
        if not skill.exists() and backup.exists(): os.replace(backup, skill)
        raise
    launcher_dir = home / ".local" / "bin"; launcher_dir.mkdir(parents=True, exist_ok=True)
    launcher = launcher_dir / ("bridge.cmd" if os.name == "nt" else "bridge")
    if os.name == "nt":
        launcher.write_text('@echo off\r\n"{0}" "{1}" %*\r\n'.format(sys.executable, skill / "scripts" / "bridge.py"), encoding="utf-8")
    else:
        launcher.write_text('#!/usr/bin/env sh\nexec "{0}" "{1}" "$@"\n'.format(sys.executable, skill / "scripts" / "bridge.py"), encoding="utf-8")
        launcher.chmod(0o700)
    _write_owned_json(_runtime_receipt(home), {"owner": OWNER, "version": MANAGED_CONFIG_VERSION, "skill": str(skill), "launcher": str(launcher)})


def _install_profiles(home: Path, names: Tuple[str, ...]) -> None:
    for name in names:
        profile = _data_root(home) / "agents" / name / "agent.json"
        receipt = _data_root(home) / "agents" / name / "agent-bridge-profile.json"
        if profile.exists() and not receipt.exists():
            raise RuntimeError("refusing to overwrite unowned agent profile: " + name)
        _write_owned_json(profile, {
            "name": name, "skills": [], "strengths": "local host integration", "last_seen": "managed",
        })
        _write_owned_json(receipt, {"owner": OWNER, "profile": str(profile), "sha256": hashlib.sha256(profile.read_bytes()).hexdigest()})


def _native_paths(home: Path) -> Tuple[Path, Path]:
    base = _data_root(home) / "native"
    return base / "agent-bridge-windows-notify.exe", base / "receipt.json"


def _install_windows_native(home: Path) -> None:
    if os.name != "nt": return
    source = _source_root() / "native" / "windows-notify" / "dist" / "windows-x86_64" / "agent-bridge-windows-notify.exe"
    if not source.is_file(): raise RuntimeError("Windows notifier release helper is missing")
    helper, receipt = _native_paths(home)
    configured = os.environ.get("AGENT_BRIDGE_WINDOWS_NOTIFY_HELPER", "")
    if configured and Path(configured).is_file() and Path(configured).absolute() != helper.absolute():
        raise RuntimeError("refusing to overwrite an unrelated Windows notifier environment value")
    if helper.exists() or receipt.exists():
        try: old = json.loads(receipt.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as error: raise RuntimeError("invalid native helper ownership receipt") from error
        if old.get("owner") != "agent-bridge.windows-notify" or old.get("helper_path") != str(helper) or old.get("sha256", "").lower() != hashlib.sha256(helper.read_bytes()).hexdigest():
            raise RuntimeError("Windows notifier ownership hash mismatch")
    helper.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(source, helper)
    _write_owned_json(receipt, {"schema": 1, "owner": "agent-bridge.windows-notify", "helper_path": str(helper), "sha256": hashlib.sha256(helper.read_bytes()).hexdigest()})
    activation = [sys.executable, str(_data_root(home) / "skill" / "scripts" / "bridge.py"), "--data-root", str(_data_root(home))]
    result = WindowsNotifier(helper).register(activation)
    if not result.ok: raise RuntimeError("native notification registration failed: " + result.detail)
    os.environ["AGENT_BRIDGE_WINDOWS_NOTIFY_HELPER"] = str(helper)
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
        winreg.SetValueEx(key, "AGENT_BRIDGE_WINDOWS_NOTIFY_HELPER", 0, winreg.REG_SZ, str(helper))


def _remove_windows_native(home: Path) -> None:
    if os.name != "nt": return
    helper, receipt = _native_paths(home)
    if not helper.exists() and not receipt.exists(): return
    try: owned = json.loads(receipt.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error: raise RuntimeError("refusing to remove unowned native helper") from error
    if owned.get("owner") != "agent-bridge.windows-notify" or owned.get("helper_path") != str(helper) or not helper.is_file() or owned.get("sha256", "").lower() != hashlib.sha256(helper.read_bytes()).hexdigest():
        raise RuntimeError("refusing to remove unowned native helper")
    result = WindowsNotifier(helper).unregister()
    if not result.ok: raise RuntimeError("native notification unregistration failed: " + result.detail)
    helper.unlink(); receipt.unlink(); os.environ.pop("AGENT_BRIDGE_WINDOWS_NOTIFY_HELPER", None)
    if helper.parent.is_dir() and not any(helper.parent.iterdir()):
        helper.parent.rmdir()
    import winreg
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
            current, _ = winreg.QueryValueEx(key, "AGENT_BRIDGE_WINDOWS_NOTIFY_HELPER")
            if current == str(helper): winreg.DeleteValue(key, "AGENT_BRIDGE_WINDOWS_NOTIFY_HELPER")
    except FileNotFoundError: pass


def _remove_runtime(home: Path) -> None:
    receipt = _runtime_receipt(home)
    if receipt.exists():
        owned = json.loads(receipt.read_text(encoding="utf-8"))
        if owned.get("owner") != OWNER: raise RuntimeError("refusing to remove unowned runtime")
        expected = {"skill": _data_root(home) / "skill", "launcher": home / ".local" / "bin" / ("bridge.cmd" if os.name == "nt" else "bridge")}
        for key, path in expected.items():
            if owned.get(key) != str(path) or path.is_symlink():
                raise RuntimeError("runtime receipt path escapes owned home")
            if path.is_dir(): shutil.rmtree(path)
            elif path.exists(): path.unlink()
        receipt.unlink()


def build_setup_plan(*, home: Optional[Path] = None, auto: bool = False, agent: Optional[str] = None) -> SetupPlan:
    """Create a no-write plan for currently detected hosts and an optional scope."""
    user_home = _home(home)
    adapters = _select_adapters(user_home, agent)
    selected = adapters if agent else tuple(item for item in adapters if _host_application_present(item))
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
    data = _data_root(user_home)
    effects = []
    for target, inverse in (
        (data / "skill", "remove owned runtime"),
        (user_home / ".local" / "bin" / ("bridge.cmd" if os.name == "nt" else "bridge"), "remove owned launcher"),
        (data / "runtime-receipt.json", "remove runtime receipt"),
        (data / "native" / "agent-bridge-windows-notify.exe", "unregister and remove owned native helper"),
    ):
        original = target.read_bytes() if target.is_file() else b""
        effects.append(ManagedMutation(target, OWNER, MANAGED_CONFIG_VERSION, content_hash(original), None, "owned receipt validates", inverse))
    for item in selected:
        effects.extend((
            ManagedMutation(item.installation_artifact_path, OWNER, MANAGED_CONFIG_VERSION, content_hash(item.installation_artifact_path.read_bytes() if item.installation_artifact_path.is_file() else b""), None, "adapter receipt validates", "remove adapter receipt"),
            ManagedMutation(data / "agents" / item.name / "agent.json", OWNER, MANAGED_CONFIG_VERSION, content_hash(b""), None, "profile is owned", "remove profile"),
        ))
    return SetupPlan(user_home, tuple(mutations), selected, tuple(item.name for item in selected), tuple(effects), agent)


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
    inverses = []
    try:
        if os.name == "nt":
            expected, ignored_receipt = _native_paths(plan.home)
            configured = os.environ.get("AGENT_BRIDGE_WINDOWS_NOTIFY_HELPER", "")
            if configured and Path(configured).is_file() and Path(configured).absolute() != expected.absolute():
                raise RuntimeError("refusing to overwrite an unrelated Windows notifier environment value")
        runtime_was_present = _runtime_receipt(plan.home).exists()
        if not runtime_was_present:
            inverses.append(("runtime", lambda: _remove_runtime(plan.home)))
        _install_runtime(plan.home)
        _install_profiles(plan.home, plan.scope)
        native_helper, native_receipt = _native_paths(plan.home)
        native_was_present = native_helper.exists() or native_receipt.exists()
        if not native_was_present:
            inverses.append(("native", lambda: _remove_windows_native(plan.home)))
        _install_windows_native(plan.home)
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
            if not _host_application_present(adapter) and not plan.requested_agent:
                raise RuntimeError("requested host application is not detected: {0}".format(adapter.name))
            if plan.requested_agent and not _host_application_present(adapter):
                adapter.config_path.parent.mkdir(parents=True, exist_ok=True)
            if not adapter._valid_installation_marker():
                adapter.marker_path.parent.mkdir(parents=True, exist_ok=True)
                _write_owned_json(adapter.marker_path, {"host": adapter.name, "mechanisms": [adapter.mechanism]})
            inverses.append((adapter.name, adapter.uninstall))
            adapter.install()
            if not adapter.detect().found:
                raise RuntimeError("post-install validation failed for host: {0}".format(adapter.name))
            applied.append(adapter)
    except BaseException as error:
        for name, inverse in reversed(inverses):
            try:
                inverse()
                rollback.append({"host": name, "outcome": "inverse applied"})
            except BaseException as rollback_error:
                rollback.append({"host": name, "outcome": "failed: {0}".format(rollback_error)})
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
    _remove_windows_native(user_home)
    _remove_runtime(user_home)
    purged = None
    if purge_data:
        data_root = _data_root(user_home)
        if data_root.exists():
            if data_root.is_symlink() or not data_root.is_dir():
                raise ValueError("refusing to purge a non-directory data root")
            shutil.rmtree(data_root)
        purged = str(data_root)
    return SetupReport(tuple(item.name for item in removable), removed_hosts=tuple(removed), purged_data_root=purged)
