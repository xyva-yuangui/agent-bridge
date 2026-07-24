"""Reversible host setup lifecycle independent from the SQLite service."""

from __future__ import annotations

import shutil
import sys
import os
import json
import hashlib
import plistlib
from importlib import resources
import tempfile
import shutil as _which_shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

from .adapters import ADAPTER_TYPES, adapter_for
from .adapters.base import HostAdapter, canonical_host_name
from .managed_config import MANAGED_CONFIG_VERSION, OWNER, ManagedMutation, backup_file, content_hash
from .notifications import MacOSNotifier, WindowsNotifier, windows_notification_capability, macos_notification_capability, macos_signing_assessment
from .path_ownership import (
    PathBackend,
    default_path_backend,
    ensure_launcher_path,
    has_launcher_path_receipt,
    launcher_directory,
    launcher_path_receipt,
    path_status,
    remove_launcher_path,
)
from .permissions import secure_directory, secure_file


def _home(value: Optional[Path]) -> Path:
    # Canonicalize the user root once.  In particular, a Windows 8.3 spelling
    # and its long spelling must identify the same owned runtime receipt.
    return (Path.home() if value is None else Path(value)).expanduser().resolve(strict=False)


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
    if adapter._valid_installation_marker() or adapter.config_path.exists():
        return True
    # Global desktop application locations describe the current interactive
    # user only.  Applying those probes to an explicit alternate --home would
    # install unrelated integrations into test, portable, or managed profiles.
    if adapter.home != Path.home().resolve(strict=False):
        return False
    if any(_which_shutil.which(name) is not None for name in names):
        return True
    display = {
        "codex": ("Codex",),
        "claude": ("Claude", "Claude Code"),
        "reasonix": ("Reasonix",),
        "zcode": ("ZCode",),
    }[adapter.name]
    candidates = []
    if os.name == "nt":
        roots = [
            Path(value) for value in (
                os.environ.get("LOCALAPPDATA"),
                os.environ.get("ProgramFiles"),
                os.environ.get("ProgramFiles(x86)"),
            ) if value
        ]
        for root in roots:
            for product in display:
                candidates.extend((
                    root / product / (product + ".exe"),
                    root / "Programs" / product / (product + ".exe"),
                ))
    elif sys.platform == "darwin":
        for root in (Path("/Applications"), Path.home() / "Applications"):
            candidates.extend(root / (product + ".app") for product in display)
    return any(candidate.exists() for candidate in candidates)


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
    secure_directory(path.parent)
    descriptor, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True)
            stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
        secure_file(path)
    finally:
        temporary.unlink(missing_ok=True)


def _runtime_receipt(home: Path) -> Path:
    return _data_root(home) / "runtime-receipt.json"


def _snapshot_runtime(home: Path) -> Optional[Path]:
    """Copy an owned runtime so a later setup failure can restore the upgrade."""
    receipt = _runtime_receipt(home)
    if not receipt.is_file():
        return None
    data = _data_root(home)
    snapshot = data / (".runtime-rollback-" + next(tempfile._get_candidate_names()))
    snapshot.mkdir(parents=True)
    skill = data / "skill"
    launcher = home / ".local" / "bin" / ("bridge.cmd" if os.name == "nt" else "bridge")
    if skill.is_dir():
        shutil.copytree(skill, snapshot / "skill")
    if launcher.is_file():
        shutil.copy2(launcher, snapshot / "launcher")
    shutil.copy2(receipt, snapshot / "runtime-receipt.json")
    return snapshot


def _restore_runtime_snapshot(home: Path, snapshot: Path) -> None:
    data = _data_root(home)
    skill = data / "skill"
    launcher = home / ".local" / "bin" / ("bridge.cmd" if os.name == "nt" else "bridge")
    if skill.exists():
        shutil.rmtree(skill)
    if (snapshot / "skill").is_dir():
        shutil.copytree(snapshot / "skill", skill)
    if (snapshot / "launcher").is_file():
        launcher.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(snapshot / "launcher", launcher)
    shutil.copy2(snapshot / "runtime-receipt.json", _runtime_receipt(home))
    shutil.rmtree(snapshot)


def _restore_profile_files(profile: Path, receipt: Path, before: Tuple[Optional[bytes], Optional[bytes]]) -> None:
    for path, content in ((profile, before[0]), (receipt, before[1])):
        if content is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
    if profile.parent.is_dir() and not any(profile.parent.iterdir()):
        profile.parent.rmdir()


def _replace_runtime_path(source: Path, destination: Path) -> None:
    """Atomically install a staged runtime, tolerating a brief Windows AV scan."""
    deadline = time.monotonic() + 2.0
    while True:
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)


def _install_runtime(home: Path) -> None:
    """Copy the compatibility runtime used by retained bridge.py launchers."""
    data = _data_root(home); root = _source_root()
    data.mkdir(parents=True, exist_ok=True)
    skill = data / "skill"
    launcher = home / ".local" / "bin" / ("bridge.cmd" if os.name == "nt" else "bridge")
    if skill.exists() or launcher.exists():
        receipt = _runtime_receipt(home)
        try: prior = json.loads(receipt.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error: raise RuntimeError("refusing to replace unowned runtime") from error
        if prior.get("owner") != OWNER or prior.get("skill") != str(skill) or prior.get("launcher") != str(launcher) or skill.is_symlink() or launcher.is_symlink():
            raise RuntimeError("refusing to replace unowned runtime")
    stage = data / (".skill-stage-" + next(tempfile._get_candidate_names()))
    backup = data / (".skill-backup-" + next(tempfile._get_candidate_names()))
    try:
        scripts = stage / "scripts"; scripts.mkdir(parents=True)
        bootstrap = resources.files("agent_bridge").joinpath("bootstrap")
        for name in ("bridge.py", "bridge_mcp.py", "notify_windows.ps1"):
            resource = bootstrap.joinpath(name)
            if not resource.is_file():
                raise RuntimeError("Agent Bridge bootstrap resource is missing: " + name)
            with resources.as_file(resource) as source:
                shutil.copy2(source, scripts / name)
        # The installed distribution is authoritative.  Resolving relative to
        # a checkout worked only in development and made a wheel install fail
        # when setup first created the self-contained repair runtime.
        package = resources.files("agent_bridge")
        with resources.as_file(package) as source_package:
            shutil.copytree(source_package, stage / "runtime" / "agent_bridge", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for name in ("SKILL.md", "README.md", "README.zh-CN.md"):
            if (root / name).is_file(): shutil.copy2(root / name, stage / name)
        if skill.exists(): _replace_runtime_path(skill, backup)
        _replace_runtime_path(stage, skill)
        if backup.exists(): shutil.rmtree(backup)
    except BaseException:
        if not skill.exists() and backup.exists(): os.replace(backup, skill)
        raise
    launcher_dir = home / ".local" / "bin"; launcher_dir.mkdir(parents=True, exist_ok=True)
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
        try:
            existing = json.loads(profile.read_text(encoding="utf-8")) if profile.exists() else {}
        except (OSError, ValueError) as error:
            raise RuntimeError("invalid owned agent profile: " + name) from error
        if not isinstance(existing, dict):
            raise RuntimeError("invalid owned agent profile: " + name)
        # This is the public, agent-owned policy profile.  Repair preserves
        # the policy fields a user or host changed after setup.
        defaults = {
            "name": name, "home": str(home), "skills": [], "strengths": "local host integration", "last_seen": "managed",
            "execution_policy": "manual", "launch_argv": [], "terminal_preference": "auto",
            "max_concurrency": 1, "cooldown_seconds": 30, "workspace_allowlist": [],
        }
        defaults.update(existing)
        defaults["name"] = name
        defaults["home"] = str(home)
        _write_owned_json(profile, defaults)
        _write_owned_json(receipt, {"owner": OWNER, "profile": str(profile), "sha256": hashlib.sha256(profile.read_bytes()).hexdigest()})


def _native_paths(home: Path) -> Tuple[Path, Path]:
    base = _data_root(home) / "native"
    return base / "agent-bridge-windows-notify.exe", base / "receipt.json"


def _macos_native_paths(home: Path) -> Tuple[Path, Path, Path]:
    base = _data_root(home) / "native" / "macos-universal2"
    app = base / "AgentBridgeNotifier.app"
    return app, app / "Contents" / "MacOS" / "AgentBridgeNotifier", base / "receipt.json"


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(path for path in root.rglob("*") if path.is_file()):
        digest.update(item.relative_to(root).as_posix().encode("utf-8") + b"\0")
        digest.update(item.read_bytes())
    return digest.hexdigest()


def _validate_macos_app(app: Path) -> Tuple[Path, str, str]:
    info = app / "Contents" / "Info.plist"
    if not app.is_dir() or app.is_symlink() or not info.is_file():
        raise RuntimeError("macOS notification app bundle is incomplete")
    try: metadata = plistlib.loads(info.read_bytes())
    except (ValueError, OSError) as error: raise RuntimeError("macOS notification Info.plist is invalid") from error
    if metadata.get("CFBundleIdentifier") != "org.agentbridge.notifier" or metadata.get("CFBundleExecutable") != "AgentBridgeNotifier":
        raise RuntimeError("macOS notification app bundle identity is invalid")
    executable = app / "Contents" / "MacOS" / "AgentBridgeNotifier"
    if not executable.is_file() or executable.is_symlink(): raise RuntimeError("macOS notification executable is missing")
    return executable, hashlib.sha256(executable.read_bytes()).hexdigest(), _tree_hash(app)


def _macos_source_app() -> Optional[Path]:
    configured = os.environ.get("AGENT_BRIDGE_MACOS_NOTIFY_APP", "").strip()
    if configured: return Path(configured)
    packaged = resources.files("agent_bridge").joinpath("native", "macos-universal2", "AgentBridgeNotifier.app")
    with resources.as_file(packaged) as source:
        return Path(source) if Path(source).is_dir() else None


def _install_macos_native(home: Path) -> None:
    if sys.platform != "darwin": return
    source = _macos_source_app()
    if source is None or not source.is_dir():
        return  # A wheel-only installation remains terminal-fallback capable.
    _validate_macos_app(source)
    app, executable, receipt = _macos_native_paths(home)
    old_activation = None
    if app.exists() or receipt.exists():
        try: owned = json.loads(receipt.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error: raise RuntimeError("invalid macOS native helper ownership receipt") from error
        if owned.get("owner") != "agent-bridge.macos-notify" or owned.get("app_path") != str(app) or not app.is_dir() or owned.get("app_sha256") != _tree_hash(app):
            raise RuntimeError("refusing to overwrite an unowned macOS notification app")
        old_activation = owned.get("activation_argv")
    app.parent.mkdir(parents=True, exist_ok=True)
    stage = app.parent / (".macos-stage-" + next(tempfile._get_candidate_names()))
    rollback_app = app.parent / (".macos-rollback-" + next(tempfile._get_candidate_names()))
    old_receipt = receipt.read_bytes() if receipt.is_file() else None
    installed_executable = executable
    try:
        shutil.copytree(source, stage)
        if app.exists():
            os.replace(app, rollback_app)
        _replace_runtime_path(stage, app)
        installed_executable, executable_hash, app_hash = _validate_macos_app(app)
        signing = macos_signing_assessment(installed_executable)
        activation = [sys.executable, str(_data_root(home) / "skill" / "scripts" / "bridge.py"), "--as", "notification-action", "--data-root", str(_data_root(home))]
        _write_owned_json(receipt, {"schema": 1, "owner": "agent-bridge.macos-notify", "source_app": str(source), "app_path": str(app), "executable_path": str(executable), "executable_sha256": executable_hash, "app_sha256": app_hash, "signing_status": signing.status, "gatekeeper": signing.gatekeeper, "activation_argv": activation})
        result = MacOSNotifier(installed_executable, activation_argv=activation).register(activation)
        if not result.ok:
            raise RuntimeError("macOS notification registration failed: " + result.detail)
    except BaseException:
        try:
            MacOSNotifier(installed_executable, activation_argv=()).unregister()
        except BaseException:
            pass
        if app.exists():
            shutil.rmtree(app)
        if rollback_app.exists():
            os.replace(rollback_app, app)
        if old_receipt is None:
            receipt.unlink(missing_ok=True)
        else:
            receipt.write_bytes(old_receipt)
        if old_activation and app.is_dir():
            old_executable, ignored_hash, ignored_tree_hash = _validate_macos_app(app)
            restored = MacOSNotifier(old_executable, activation_argv=old_activation).register(old_activation)
            if not restored.ok:
                raise RuntimeError("macOS notification rollback registration failed: " + restored.detail)
        raise
    finally:
        if stage.exists():
            shutil.rmtree(stage)
        if rollback_app.exists():
            shutil.rmtree(rollback_app)


def _remove_macos_native(home: Path) -> None:
    if sys.platform != "darwin": return
    app, executable, receipt = _macos_native_paths(home)
    if not app.exists() and not receipt.exists(): return
    try: owned = json.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error: raise RuntimeError("refusing to remove unowned macOS notification app") from error
    if owned.get("owner") != "agent-bridge.macos-notify" or owned.get("app_path") != str(app) or not app.is_dir() or owned.get("app_sha256") != _tree_hash(app):
        raise RuntimeError("refusing to remove unowned macOS notification app")
    result = MacOSNotifier(executable, activation_argv=owned.get("activation_argv", ())).unregister()
    if not result.ok: raise RuntimeError("macOS notification unregistration failed: " + result.detail)
    shutil.rmtree(app); receipt.unlink()
    if app.parent.is_dir() and not any(app.parent.iterdir()): app.parent.rmdir()


def _install_windows_native(home: Path) -> None:
    if os.name != "nt": return
    packaged = resources.files("agent_bridge").joinpath(
        "native", "windows-x86_64", "agent-bridge-windows-notify.exe"
    )
    helper, receipt = _native_paths(home)
    env_receipt = helper.parent / "environment-receipt.json"
    configured = os.environ.get("AGENT_BRIDGE_WINDOWS_NOTIFY_HELPER", "")
    old_helper = helper.read_bytes() if helper.is_file() else None
    old_receipt = receipt.read_bytes() if receipt.is_file() else None
    old_env_receipt = env_receipt.read_bytes() if env_receipt.is_file() else None
    old_activation = None
    if configured and Path(configured).is_file() and Path(configured).absolute() != helper.absolute():
        raise RuntimeError("refusing to overwrite an unrelated Windows notifier environment value")
    if helper.exists() or receipt.exists():
        try: old = json.loads(receipt.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as error: raise RuntimeError("invalid native helper ownership receipt") from error
        if old.get("owner") != "agent-bridge.windows-notify" or old.get("helper_path") != str(helper) or old.get("sha256", "").lower() != hashlib.sha256(helper.read_bytes()).hexdigest():
            raise RuntimeError("Windows notifier ownership hash mismatch")
        old_activation = old.get("activation_argv")
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
        try: prior_user, _ = winreg.QueryValueEx(key, "AGENT_BRIDGE_WINDOWS_NOTIFY_HELPER")
        except FileNotFoundError: prior_user = None
    stage = helper.parent / (".windows-stage-" + next(tempfile._get_candidate_names()) + ".exe")
    try:
        if packaged.is_file():
            helper.parent.mkdir(parents=True, exist_ok=True)
            # ``as_file`` supports both unpacked installs and zipped resources.
            with resources.as_file(packaged) as source:
                shutil.copy2(source, stage)
            os.replace(stage, helper)
        elif not helper.is_file():
            raise RuntimeError("Windows notifier release helper is missing")
        activation = [sys.executable, str(_data_root(home) / "skill" / "scripts" / "bridge.py"), "--as", "notification-action", "--data-root", str(_data_root(home))]
        _write_owned_json(receipt, {
            "schema": 1,
            "owner": "agent-bridge.windows-notify",
            "helper_path": str(helper),
            "sha256": hashlib.sha256(helper.read_bytes()).hexdigest(),
            "activation_argv": activation,
        })
        result = WindowsNotifier(helper).register(activation)
        if not result.ok:
            raise RuntimeError("native notification registration failed: " + result.detail)
        os.environ["AGENT_BRIDGE_WINDOWS_NOTIFY_HELPER"] = str(helper)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
            # A repair sees the value that the first setup itself registered.
            # Keep the original environment state for final uninstall.
            receipt_process, receipt_user = configured or None, prior_user
            if old_env_receipt is not None:
                try:
                    existing_environment = json.loads(old_env_receipt.decode("utf-8-sig"))
                except (UnicodeDecodeError, ValueError):
                    existing_environment = {}
                if existing_environment.get("owner") == OWNER and existing_environment.get("helper") == str(helper):
                    receipt_process = existing_environment.get("process")
                    receipt_user = existing_environment.get("user")
            winreg.SetValueEx(key, "AGENT_BRIDGE_WINDOWS_NOTIFY_HELPER", 0, winreg.REG_SZ, str(helper))
        _write_owned_json(env_receipt, {"owner": OWNER, "helper": str(helper), "process": receipt_process, "user": receipt_user})
    except BaseException:
        try:
            WindowsNotifier(helper).unregister()
        except BaseException:
            pass
        if old_helper is None:
            helper.unlink(missing_ok=True)
        else:
            helper.parent.mkdir(parents=True, exist_ok=True)
            helper.write_bytes(old_helper)
        if old_receipt is None:
            receipt.unlink(missing_ok=True)
        else:
            receipt.write_bytes(old_receipt)
        if old_env_receipt is None:
            env_receipt.unlink(missing_ok=True)
        else:
            env_receipt.write_bytes(old_env_receipt)
        if configured:
            os.environ["AGENT_BRIDGE_WINDOWS_NOTIFY_HELPER"] = configured
        else:
            os.environ.pop("AGENT_BRIDGE_WINDOWS_NOTIFY_HELPER", None)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
            try:
                if prior_user is None:
                    winreg.DeleteValue(key, "AGENT_BRIDGE_WINDOWS_NOTIFY_HELPER")
                else:
                    winreg.SetValueEx(key, "AGENT_BRIDGE_WINDOWS_NOTIFY_HELPER", 0, winreg.REG_SZ, prior_user)
            except FileNotFoundError:
                pass
        if old_activation and helper.is_file():
            restored = WindowsNotifier(helper).register(old_activation)
            if not restored.ok:
                raise RuntimeError("native notification rollback registration failed: " + restored.detail)
        raise
    finally:
        stage.unlink(missing_ok=True)


def _remove_windows_native(home: Path) -> None:
    if os.name != "nt": return
    helper, receipt = _native_paths(home)
    env_receipt = helper.parent / "environment-receipt.json"
    if not helper.exists() and not receipt.exists(): return
    try: owned = json.loads(receipt.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error: raise RuntimeError("refusing to remove unowned native helper") from error
    if owned.get("owner") != "agent-bridge.windows-notify" or owned.get("helper_path") != str(helper) or not helper.is_file() or owned.get("sha256", "").lower() != hashlib.sha256(helper.read_bytes()).hexdigest():
        raise RuntimeError("refusing to remove unowned native helper")
    result = WindowsNotifier(helper).unregister()
    if not result.ok: raise RuntimeError("native notification unregistration failed: " + result.detail)
    helper.unlink(); receipt.unlink()
    try: env_state = json.loads(env_receipt.read_text(encoding="utf-8"))
    except (OSError, ValueError): env_state = {}
    if os.environ.get("AGENT_BRIDGE_WINDOWS_NOTIFY_HELPER") == str(helper):
        if env_state.get("process") is None: os.environ.pop("AGENT_BRIDGE_WINDOWS_NOTIFY_HELPER", None)
        else: os.environ["AGENT_BRIDGE_WINDOWS_NOTIFY_HELPER"] = str(env_state["process"])
    import winreg
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
            current, _ = winreg.QueryValueEx(key, "AGENT_BRIDGE_WINDOWS_NOTIFY_HELPER")
            if current == str(helper):
                if env_state.get("user") is None: winreg.DeleteValue(key, "AGENT_BRIDGE_WINDOWS_NOTIFY_HELPER")
                else: winreg.SetValueEx(key, "AGENT_BRIDGE_WINDOWS_NOTIFY_HELPER", 0, winreg.REG_SZ, str(env_state["user"]))
    except FileNotFoundError: pass
    env_receipt.unlink(missing_ok=True)
    if helper.parent.is_dir() and not any(helper.parent.iterdir()):
        helper.parent.rmdir()


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
    agents = _data_root(home) / "agents"
    if agents.is_dir() and not agents.is_symlink():
        for profile_receipt in agents.glob("*/agent-bridge-profile.json"):
            try:
                profile_owned = json.loads(profile_receipt.read_text(encoding="utf-8"))
                profile = Path(profile_owned["profile"])
                if profile_owned.get("owner") != OWNER or profile.parent != profile_receipt.parent or not profile.is_file() or profile_owned.get("sha256") != hashlib.sha256(profile.read_bytes()).hexdigest():
                    continue
                profile.unlink(); profile_receipt.unlink()
                if not any(profile.parent.iterdir()): profile.parent.rmdir()
            except (OSError, ValueError, KeyError, TypeError):
                continue


def _remove_owned_profile(home: Path, name: str) -> None:
    """Remove one unchanged bridge-owned profile without touching siblings."""
    directory = _data_root(home) / "agents" / name
    profile = directory / "agent.json"
    receipt = directory / "agent-bridge-profile.json"
    if not receipt.is_file() or receipt.is_symlink():
        return
    try:
        owned = json.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if (
        owned.get("owner") != OWNER
        or owned.get("profile") != str(profile)
        or not profile.is_file()
        or profile.is_symlink()
        or owned.get("sha256") != hashlib.sha256(profile.read_bytes()).hexdigest()
    ):
        return
    profile.unlink()
    receipt.unlink()
    if directory.is_dir() and not any(directory.iterdir()):
        directory.rmdir()


def _marker_receipt(home: Path, adapter: HostAdapter) -> Path:
    return _data_root(home) / "host-markers" / (adapter.name + ".json")


def _write_owned_marker(home: Path, adapter: HostAdapter) -> None:
    marker = adapter.marker_path
    before = marker.read_bytes() if marker.exists() else b""
    owned = json.dumps({"host": adapter.name, "mechanisms": [adapter.mechanism]}, sort_keys=True).encode("utf-8") + b"\n"
    _write_owned_json(_marker_receipt(home, adapter), {"owner": OWNER, "path": str(marker), "present": marker.exists(), "before": before.hex(), "owned_hash": hashlib.sha256(owned).hexdigest()})
    marker.parent.mkdir(parents=True, exist_ok=True); marker.write_bytes(owned)


def _restore_owned_marker(home: Path, adapter: HostAdapter) -> None:
    receipt = _marker_receipt(home, adapter)
    try: state = json.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, ValueError): return
    marker = adapter.marker_path
    current = marker.read_bytes() if marker.exists() else b""
    if state.get("owner") != OWNER or state.get("path") != str(marker) or hashlib.sha256(current).hexdigest() != state.get("owned_hash"):
        return
    if state.get("present"): marker.write_bytes(bytes.fromhex(state.get("before", "")))
    else: marker.unlink(missing_ok=True)
    receipt.unlink(missing_ok=True)


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
        (data / "native" / "macos-universal2" / "AgentBridgeNotifier.app", "unregister and remove owned macOS notification app"),
        (launcher_path_receipt(user_home), "remove owned launcher PATH entry"),
    ):
        original = target.read_bytes() if target.is_file() else b""
        effects.append(ManagedMutation(target, OWNER, MANAGED_CONFIG_VERSION, content_hash(original), None, "owned receipt validates", inverse))
    for item in selected:
        effects.extend((
            ManagedMutation(item.installation_artifact_path, OWNER, MANAGED_CONFIG_VERSION, content_hash(item.installation_artifact_path.read_bytes() if item.installation_artifact_path.is_file() else b""), None, "adapter receipt validates", "remove adapter receipt"),
            ManagedMutation(data / "agents" / item.name / "agent.json", OWNER, MANAGED_CONFIG_VERSION, content_hash(b""), None, "profile is owned", "remove profile"),
        ))
    return SetupPlan(user_home, tuple(mutations), selected, tuple(item.name for item in selected), tuple(effects), agent)


def apply_setup_plan(plan: SetupPlan, *, dry_run: bool = False, path_backend: Optional[PathBackend] = None) -> SetupReport:
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
    runtime_snapshot = None
    backend = default_path_backend() if path_backend is None else path_backend
    try:
        if os.name == "nt":
            expected, ignored_receipt = _native_paths(plan.home)
            configured = os.environ.get("AGENT_BRIDGE_WINDOWS_NOTIFY_HELPER", "")
            if configured and Path(configured).is_file() and Path(configured).absolute() != expected.absolute():
                raise RuntimeError("refusing to overwrite an unrelated Windows notifier environment value")
        runtime_was_present = _runtime_receipt(plan.home).exists()
        if not runtime_was_present:
            inverses.append(("runtime", lambda: _remove_runtime(plan.home)))
        else:
            runtime_snapshot = _snapshot_runtime(plan.home)
            if runtime_snapshot is not None:
                inverses.append((
                    "runtime upgrade",
                    lambda snapshot=runtime_snapshot: _restore_runtime_snapshot(plan.home, snapshot),
                ))
        _install_runtime(plan.home)
        launcher_entry = str(launcher_directory(plan.home))
        # Register the scoped inverse before touching profile/registry state.
        # A fresh receipt means this invocation owns any entry it introduces;
        # an existing receipt belongs to an earlier successful setup and is
        # deliberately left untouched by a failed repair.
        if not has_launcher_path_receipt(plan.home, launcher_entry):
            inverses.append(("launcher PATH", lambda: remove_launcher_path(plan.home, backend=backend)))
        ensure_launcher_path(plan.home, launcher_directory(plan.home), backend=backend)
        for profile_name in plan.scope:
            profile = data_root / "agents" / profile_name / "agent.json"
            profile_receipt = profile.with_name("agent-bridge-profile.json")
            before = (
                profile.read_bytes() if profile.is_file() else None,
                profile_receipt.read_bytes() if profile_receipt.is_file() else None,
            )
            inverses.append((
                profile_name + " profile",
                lambda p=profile, r=profile_receipt, value=before: _restore_profile_files(p, r, value),
            ))
        _install_profiles(plan.home, plan.scope)
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
                inverses.append((adapter.name + " marker", lambda item=adapter: _restore_owned_marker(plan.home, item)))
                _write_owned_marker(plan.home, adapter)
            inverses.append((adapter.name, adapter.uninstall))
            adapter.install()
            if not adapter.detect().found:
                raise RuntimeError("post-install validation failed for host: {0}".format(adapter.name))
            applied.append(adapter)
        # Native components are last because each install is internally
        # transactional. A native failure can roll back host/runtime changes,
        # while a host failure can no longer leave an upgraded helper behind.
        native_helper, native_receipt = _native_paths(plan.home)
        native_was_present = native_helper.exists() or native_receipt.exists()
        if not native_was_present:
            inverses.append(("native", lambda: _remove_windows_native(plan.home)))
        _install_windows_native(plan.home)
        mac_app, ignored_mac_executable, mac_receipt = _macos_native_paths(plan.home)
        if not (mac_app.exists() or mac_receipt.exists()): inverses.append(("macOS native", lambda: _remove_macos_native(plan.home)))
        _install_macos_native(plan.home)
    except BaseException as error:
        for name, inverse in reversed(inverses):
            try:
                inverse()
                rollback.append({"host": name, "outcome": "inverse applied"})
            except BaseException as rollback_error:
                rollback.append({"host": name, "outcome": "failed: {0}".format(rollback_error)})
        raise RuntimeError("setup failed: {0}; rollback={1}".format(error, rollback)) from error
    if runtime_snapshot is not None and runtime_snapshot.exists():
        shutil.rmtree(runtime_snapshot)
    return SetupReport(plan.scope, tuple(item.name for item in applied), backups=tuple(backups))


def repair(*, home: Optional[Path] = None, agent: Optional[str] = None, dry_run: bool = False, path_backend: Optional[PathBackend] = None) -> SetupReport:
    """Re-apply the same owned configuration; repeated repair is idempotent."""
    plan = build_setup_plan(home=home, auto=True, agent=agent)
    owned = tuple(item for item in plan.adapters if _owns_integration(item))
    owned_targets = {item.config_path for item in owned}
    repair_plan = SetupPlan(plan.home, tuple(item for item in plan.mutations if item.target in owned_targets), owned, tuple(item.name for item in owned))
    return apply_setup_plan(repair_plan, dry_run=dry_run, path_backend=path_backend)


def _notification_status(home: Path) -> Dict[str, object]:
    if sys.platform == "darwin":
        app, executable, receipt = _macos_native_paths(home)
        try:
            owned = json.loads(receipt.read_text(encoding="utf-8"))
            if owned.get("owner") == "agent-bridge.macos-notify" and app.is_dir() and owned.get("app_sha256") == _tree_hash(app):
                return {
                    "available": True,
                    "helper_path": str(executable),
                    "detail": "owned macOS notification app installed; signing={0}; gatekeeper={1}".format(owned.get("signing_status", "unknown"), owned.get("gatekeeper", "unknown")),
                    "signing_status": owned.get("signing_status", "unknown"),
                    "gatekeeper": owned.get("gatekeeper", "unknown"),
                }
        except (OSError, ValueError): pass
    if os.name == "nt":
        helper, receipt = _native_paths(home)
        if helper.exists() or receipt.exists():
            try:
                owned = json.loads(receipt.read_text(encoding="utf-8-sig"))
                activation = owned.get("activation_argv")
                if (
                    owned.get("owner") != "agent-bridge.windows-notify"
                    or Path(str(owned.get("helper_path", ""))).resolve(strict=False)
                    != helper.resolve(strict=False)
                    or not helper.is_file()
                    or helper.is_symlink()
                    or owned.get("sha256", "").lower()
                    != hashlib.sha256(helper.read_bytes()).hexdigest()
                    or not isinstance(activation, list)
                    or len(activation) != 6
                    or not all(isinstance(item, str) and item for item in activation)
                    or not Path(activation[0]).is_absolute()
                    or not Path(activation[1]).is_absolute()
                    or activation[2:5] != ["--as", "notification-action", "--data-root"]
                    or Path(str(activation[5])).resolve(strict=False)
                    != _data_root(home).resolve(strict=False)
                ):
                    raise ValueError("invalid receipt")
                capability = windows_notification_capability(helper)
                return {
                    "available": capability.available,
                    "helper_path": capability.helper_path,
                    "detail": capability.detail,
                    "signing_status": capability.signing_status,
                    "gatekeeper": capability.gatekeeper,
                }
            except (OSError, ValueError, TypeError):
                return {
                    "available": False,
                    "helper_path": str(helper),
                    "detail": "owned Windows notification helper receipt is invalid; run bridge setup --repair",
                    "signing_status": "unknown",
                    "gatekeeper": "unknown",
                }
    capability = macos_notification_capability() if sys.platform == "darwin" else windows_notification_capability()
    return {
        "available": capability.available,
        "helper_path": capability.helper_path,
        "detail": capability.detail,
        "signing_status": capability.signing_status,
        "gatekeeper": capability.gatekeeper,
    }


def status(*, home: Optional[Path] = None, agent: Optional[str] = None, path_backend: Optional[PathBackend] = None) -> Dict[str, object]:
    user_home = _home(home)
    hosts = []
    for adapter in _select_adapters(user_home, agent):
        health = adapter.health_check()
        profile_path = _data_root(user_home) / "agents" / adapter.name / "agent.json"
        try:
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
            policy = str(profile["execution_policy"])
            terminal_preference = str(profile["terminal_preference"])
        except (OSError, ValueError, KeyError, TypeError):
            policy, terminal_preference = "unconfigured", "fallback"
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
            "execution_policy": policy,
            "terminal_preference": terminal_preference,
            "launch_policy": "terminal-fallback" if not health.capabilities.can_open_terminal else "integrated-terminal",
        })
    return {
        "owner": OWNER,
        "managed_config_version": MANAGED_CONFIG_VERSION,
        "hosts": hosts,
        "notifications": _notification_status(user_home),
        "launcher_path": path_status(user_home, backend=path_backend),
    }


def uninstall(*, home: Optional[Path] = None, agent: Optional[str] = None, purge_data: bool = False, dry_run: bool = False, path_backend: Optional[PathBackend] = None) -> SetupReport:
    """Remove owned host integration artifacts; data deletion requires opt-in."""
    user_home = _home(home)
    backend = default_path_backend() if path_backend is None else path_backend
    adapters = _select_adapters(user_home, agent)
    removable = tuple(item for item in adapters if _owns_integration(item))
    initially_owned = tuple(item for item in _select_adapters(user_home, None) if _owns_integration(item))
    if purge_data and any(item.name not in {target.name for target in removable} for item in initially_owned):
        raise ValueError("refusing to purge shared data while host integrations remain")
    if dry_run:
        return SetupReport(tuple(item.name for item in removable), removed_hosts=tuple(item.name for item in removable), dry_run=True)
    removed = []
    for item in removable:
        # Adapter removal only removes exact blocks/registrations that it owns.
        item.uninstall()
        _restore_owned_marker(user_home, item)
        item._remove_installation_artifact()
        _remove_owned_profile(user_home, item.name)
        removed.append(item.name)
    remaining = tuple(
        item for item in _select_adapters(user_home, None)
        if item.name not in removed and _owns_integration(item)
    )
    # Runtime, PATH, notification registration, and package profiles are
    # shared by all four host integrations.  A scoped uninstall must leave
    # them intact until the final owned host is removed.
    if not remaining:
        _remove_windows_native(user_home)
        _remove_macos_native(user_home)
        remove_launcher_path(user_home, backend=backend)
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
