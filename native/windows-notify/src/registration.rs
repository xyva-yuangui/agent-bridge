#[cfg(windows)]
use serde::{Deserialize, Serialize};
#[cfg(windows)]
use std::env;
#[cfg(windows)]
use std::fs;
#[cfg(windows)]
use std::path::PathBuf;
#[cfg(windows)]
use windows::core::{Interface, HSTRING, PCWSTR, PROPVARIANT};
#[cfg(windows)]
use windows::Win32::Storage::EnhancedStorage::PKEY_AppUserModel_ID;
#[cfg(windows)]
use windows::Win32::System::Com::{
    CoCreateInstance, CoInitializeEx, CoUninitialize, IPersistFile, CLSCTX_INPROC_SERVER,
    COINIT_APARTMENTTHREADED,
};
#[cfg(windows)]
use windows::Win32::UI::Shell::PropertiesSystem::IPropertyStore;
#[cfg(windows)]
use windows::Win32::UI::Shell::{IShellLinkW, ShellLink};
#[cfg(windows)]
use winreg::enums::{HKEY_CURRENT_USER, KEY_READ, KEY_WRITE};
#[cfg(windows)]
use winreg::RegKey;

pub const AUMID: &str = "OpenAI.AgentBridge.WindowsNotify";
pub const PROTOCOL: &str = "agent-bridge";
#[cfg(windows)]
const OWNERSHIP_KEY: &str = "Software\\AgentBridge\\WindowsNotify";

#[cfg(windows)]
#[derive(Debug, Deserialize, Serialize)]
struct PriorProtocol {
    existed: bool,
    description: Option<String>,
    url_protocol: Option<String>,
    command: Option<String>,
    activation: Option<String>,
}

#[cfg(windows)]
pub fn register(activation_argv: &[String]) -> Result<String, String> {
    if activation_argv
        .first()
        .map(|value| std::path::Path::new(value).is_absolute())
        != Some(true)
    {
        return Err("activation argv must begin with an absolute executable path".to_owned());
    }
    let executable = env::current_exe().map_err(|error| error.to_string())?;
    let executable = executable.to_string_lossy();
    if executable.contains('"') {
        return Err("helper executable path contains an unsupported quote".to_owned());
    }
    let classes = RegKey::predef(HKEY_CURRENT_USER)
        .open_subkey_with_flags("Software\\Classes", KEY_READ | KEY_WRITE)
        .or_else(|_| {
            RegKey::predef(HKEY_CURRENT_USER)
                .create_subkey("Software\\Classes")
                .map(|pair| pair.0)
        })
        .map_err(|error| error.to_string())?;
    backup_registration(&classes, executable.as_ref())?;
    let activation_json =
        serde_json::to_string(activation_argv).map_err(|error| error.to_string())?;
    let owned_command = format!("\"{}\" action-uri \"%1\"", executable);
    let mutation = (|| -> Result<(), String> {
        let (protocol, _) = classes
            .create_subkey(PROTOCOL)
            .map_err(|error| error.to_string())?;
        protocol
            .set_value("", &"URL:Agent Bridge action")
            .map_err(|error| error.to_string())?;
        protocol
            .set_value("URL Protocol", &"")
            .map_err(|error| error.to_string())?;
        let (command, _) = protocol
            .create_subkey("shell\\open\\command")
            .map_err(|error| error.to_string())?;
        // Only the helper's own resolved path and the fixed Windows protocol placeholder are registered.
        command
            .set_value("", &owned_command)
            .map_err(|error| error.to_string())?;
        protocol
            .set_value("AgentBridgeActivationArgvJson", &activation_json)
            .map_err(|error| error.to_string())?;
        create_start_menu_shortcut(&executable)?;
        let owned = shortcut_owned_path()?;
        if let Some(parent) = owned.parent() {
            fs::create_dir_all(parent).map_err(|error| error.to_string())?;
        }
        fs::copy(shortcut_path()?, owned).map_err(|error| error.to_string())?;
        let owner = RegKey::predef(HKEY_CURRENT_USER)
            .create_subkey(OWNERSHIP_KEY)
            .map_err(|error| error.to_string())?
            .0;
        owner
            .set_value("OwnedCommand", &owned_command)
            .map_err(|error| error.to_string())?;
        owner
            .set_value("OwnedActivationJson", &activation_json)
            .map_err(|error| error.to_string())?;
        Ok(())
    })();
    if let Err(error) = mutation {
        let _ = classes.delete_subkey_all(PROTOCOL);
        let registration_rollback = restore_registration(&classes);
        let shortcut_rollback = restore_start_menu_shortcut();
        if let Err(rollback) = registration_rollback {
            return Err(format!("{}; protocol rollback failed: {}", error, rollback));
        }
        if let Err(rollback) = shortcut_rollback {
            return Err(format!("{}; shortcut rollback failed: {}", error, rollback));
        }
        return Err(error);
    }
    Ok(format!(
        "registered per-user {} protocol for {}",
        PROTOCOL, AUMID
    ))
}

#[cfg(windows)]
pub fn launch_activation(uri: &str) -> Result<(), String> {
    let classes = RegKey::predef(HKEY_CURRENT_USER)
        .open_subkey("Software\\Classes")
        .map_err(|error| error.to_string())?;
    let protocol = classes
        .open_subkey(PROTOCOL)
        .map_err(|_| "Agent Bridge activation is not registered".to_owned())?;
    let raw: String = protocol
        .get_value("AgentBridgeActivationArgvJson")
        .map_err(|_| "Agent Bridge activation argv is missing".to_owned())?;
    let argv: Vec<String> = serde_json::from_str(&raw)
        .map_err(|_| "Agent Bridge activation argv is invalid".to_owned())?;
    if argv
        .first()
        .map(|value| std::path::Path::new(value).is_absolute())
        != Some(true)
    {
        return Err("Agent Bridge activation argv is unsafe".to_owned());
    }
    std::process::Command::new(&argv[0])
        .args(&argv[1..])
        .arg("open-action")
        .arg("--activation-uri")
        .arg(uri)
        .status()
        .map_err(|error| error.to_string())?;
    Ok(())
}

#[cfg(windows)]
pub fn unregister() -> Result<String, String> {
    use windows::UI::Notifications::ToastNotificationManager;
    verify_owned_registration()?;
    ToastNotificationManager::History()
        .and_then(|history| history.ClearWithId(&HSTRING::from(AUMID)))
        .map_err(|error| error.to_string())?;
    let classes = RegKey::predef(HKEY_CURRENT_USER)
        .open_subkey_with_flags("Software\\Classes", KEY_READ | KEY_WRITE)
        .map_err(|error| error.to_string())?;
    let detail = match classes.delete_subkey_all(PROTOCOL) {
        Ok(()) => format!("unregistered per-user {} protocol", PROTOCOL),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            "protocol was not registered".to_owned()
        }
        Err(error) => return Err(error.to_string()),
    };
    restore_registration(&classes)?;
    restore_start_menu_shortcut()?;
    Ok(detail)
}

#[cfg(windows)]
pub fn status() -> Result<String, String> {
    verify_owned_registration()?;
    Ok(format!(
        "per-user {} protocol and {} shortcut are registered",
        PROTOCOL, AUMID
    ))
}

#[cfg(windows)]
fn shortcut_path() -> Result<PathBuf, String> {
    let app_data = env::var_os("APPDATA").ok_or_else(|| "APPDATA is unavailable".to_owned())?;
    Ok(PathBuf::from(app_data)
        .join("Microsoft")
        .join("Windows")
        .join("Start Menu")
        .join("Programs")
        .join("Agent Bridge.lnk"))
}

#[cfg(windows)]
fn shortcut_backup_path() -> Result<PathBuf, String> {
    let local =
        env::var_os("LOCALAPPDATA").ok_or_else(|| "LOCALAPPDATA is unavailable".to_owned())?;
    Ok(PathBuf::from(local)
        .join("AgentBridge")
        .join("registration-backup")
        .join("Agent Bridge.lnk"))
}

#[cfg(windows)]
fn shortcut_owned_path() -> Result<PathBuf, String> {
    Ok(shortcut_backup_path()?.with_file_name("Agent Bridge.owned.lnk"))
}

#[cfg(windows)]
fn verify_owned_registration() -> Result<(), String> {
    let root = RegKey::predef(HKEY_CURRENT_USER);
    let owner = root
        .open_subkey_with_flags(OWNERSHIP_KEY, KEY_READ)
        .map_err(|_| "Agent Bridge registration ownership receipt is missing".to_owned())?;
    let expected: String = owner
        .get_value("OwnedCommand")
        .map_err(|_| "Agent Bridge owned protocol command is missing".to_owned())?;
    let expected_activation: String = owner
        .get_value("OwnedActivationJson")
        .map_err(|_| "Agent Bridge owned activation argv is missing".to_owned())?;
    let classes = root
        .open_subkey_with_flags("Software\\Classes", KEY_READ)
        .map_err(|error| error.to_string())?;
    let protocol = classes
        .open_subkey_with_flags(PROTOCOL, KEY_READ)
        .map_err(|_| "refusing to remove a protocol no longer owned by Agent Bridge".to_owned())?;
    let command: String = protocol
        .open_subkey_with_flags("shell\\open\\command", KEY_READ)
        .and_then(|key| key.get_value(""))
        .map_err(|_| "refusing to remove a protocol no longer owned by Agent Bridge".to_owned())?;
    let activation: String = protocol
        .get_value("AgentBridgeActivationArgvJson")
        .map_err(|_| {
            "refusing to remove activation data no longer owned by Agent Bridge".to_owned()
        })?;
    if command != expected || activation != expected_activation {
        return Err("refusing to remove a protocol no longer owned by Agent Bridge".to_owned());
    }
    let shortcut = shortcut_path()?;
    let owned = shortcut_owned_path()?;
    if !shortcut.is_file()
        || !owned.is_file()
        || fs::read(&shortcut).map_err(|error| error.to_string())?
            != fs::read(&owned).map_err(|error| error.to_string())?
    {
        return Err(
            "refusing to replace a Start Menu shortcut no longer owned by Agent Bridge".to_owned(),
        );
    }
    Ok(())
}

#[cfg(windows)]
fn optional_value(key: &RegKey, name: &str) -> Option<String> {
    key.get_value(name).ok()
}

#[cfg(windows)]
fn backup_registration(classes: &RegKey, executable: &str) -> Result<(), String> {
    let owner = RegKey::predef(HKEY_CURRENT_USER)
        .create_subkey(OWNERSHIP_KEY)
        .map_err(|error| error.to_string())?
        .0;
    let owned_command = format!("\"{}\" action-uri \"%1\"", executable);
    let recorded_owned_command: Option<String> = optional_value(&owner, "OwnedCommand");
    if let Ok(existing) = classes.open_subkey_with_flags(PROTOCOL, KEY_READ) {
        let command = existing
            .open_subkey("shell\\open\\command")
            .ok()
            .and_then(|key| optional_value(&key, ""));
        let already_owned = command.as_deref() == Some(owned_command.as_str())
            && recorded_owned_command.as_deref() == Some(owned_command.as_str());
        if already_owned {
            let current_activation = optional_value(&existing, "AgentBridgeActivationArgvJson");
            let recorded_activation: Option<String> = optional_value(&owner, "OwnedActivationJson");
            if current_activation != recorded_activation || current_activation.is_none() {
                return Err(
                    "refusing to overwrite activation data no longer owned by Agent Bridge"
                        .to_owned(),
                );
            }
        } else {
            let prior = PriorProtocol {
                existed: true,
                description: optional_value(&existing, ""),
                url_protocol: optional_value(&existing, "URL Protocol"),
                command,
                activation: optional_value(&existing, "AgentBridgeActivationArgvJson"),
            };
            owner
                .set_value(
                    "PreviousProtocolJson",
                    &serde_json::to_string(&prior).map_err(|error| error.to_string())?,
                )
                .map_err(|error| error.to_string())?;
        }
    } else {
        let prior = PriorProtocol {
            existed: false,
            description: None,
            url_protocol: None,
            command: None,
            activation: None,
        };
        owner
            .set_value(
                "PreviousProtocolJson",
                &serde_json::to_string(&prior).map_err(|error| error.to_string())?,
            )
            .map_err(|error| error.to_string())?;
    }
    let shortcut = shortcut_path()?;
    let backup = shortcut_backup_path()?;
    let owned = shortcut_owned_path()?;
    if shortcut.is_file()
        && owned.is_file()
        && fs::read(&shortcut).map_err(|error| error.to_string())?
            != fs::read(&owned).map_err(|error| error.to_string())?
    {
        return Err(
            "refusing to overwrite a Start Menu shortcut no longer owned by Agent Bridge"
                .to_owned(),
        );
    }
    if shortcut.is_file() && !owned.is_file() && !backup.exists() {
        if let Some(parent) = backup.parent() {
            fs::create_dir_all(parent).map_err(|error| error.to_string())?;
        }
        fs::copy(shortcut, backup).map_err(|error| error.to_string())?;
    }
    Ok(())
}

#[cfg(windows)]
fn restore_registration(classes: &RegKey) -> Result<(), String> {
    let root = RegKey::predef(HKEY_CURRENT_USER);
    let owner = match root.open_subkey_with_flags(OWNERSHIP_KEY, KEY_READ | KEY_WRITE) {
        Ok(value) => value,
        Err(_) => return Ok(()),
    };
    let raw: String = match owner.get_value("PreviousProtocolJson") {
        Ok(value) => value,
        Err(_) => return Ok(()),
    };
    let prior: PriorProtocol = serde_json::from_str(&raw).map_err(|error| error.to_string())?;
    if prior.existed {
        let (protocol, _) = classes
            .create_subkey(PROTOCOL)
            .map_err(|error| error.to_string())?;
        if let Some(value) = prior.description {
            protocol
                .set_value("", &value)
                .map_err(|error| error.to_string())?;
        }
        if let Some(value) = prior.url_protocol {
            protocol
                .set_value("URL Protocol", &value)
                .map_err(|error| error.to_string())?;
        }
        if let Some(value) = prior.activation {
            protocol
                .set_value("AgentBridgeActivationArgvJson", &value)
                .map_err(|error| error.to_string())?;
        }
        if let Some(value) = prior.command {
            protocol
                .create_subkey("shell\\open\\command")
                .map_err(|error| error.to_string())?
                .0
                .set_value("", &value)
                .map_err(|error| error.to_string())?;
        }
    }
    let _ = root.delete_subkey_all(OWNERSHIP_KEY);
    Ok(())
}

#[cfg(windows)]
fn create_start_menu_shortcut(executable: &str) -> Result<(), String> {
    let shortcut = shortcut_path()?;
    let parent = shortcut
        .parent()
        .ok_or_else(|| "invalid Start Menu shortcut path".to_owned())?;
    fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    unsafe {
        CoInitializeEx(None, COINIT_APARTMENTTHREADED)
            .ok()
            .map_err(|error| error.to_string())?;
        let result = (|| -> windows::core::Result<()> {
            let link: IShellLinkW = CoCreateInstance(&ShellLink, None, CLSCTX_INPROC_SERVER)?;
            let executable = HSTRING::from(executable);
            link.SetPath(PCWSTR(executable.as_ptr()))?;
            let properties: IPropertyStore = link.cast()?;
            let aumid = PROPVARIANT::from(AUMID);
            properties.SetValue(&PKEY_AppUserModel_ID, &aumid)?;
            properties.Commit()?;
            let persist: IPersistFile = link.cast()?;
            let shortcut = HSTRING::from(shortcut.to_string_lossy().as_ref());
            persist.Save(PCWSTR(shortcut.as_ptr()), true)?;
            Ok(())
        })();
        CoUninitialize();
        result.map_err(|error| error.to_string())
    }
}

#[cfg(windows)]
fn restore_start_menu_shortcut() -> Result<(), String> {
    let shortcut = shortcut_path()?;
    let backup = shortcut_backup_path()?;
    let owned = shortcut_owned_path()?;
    if backup.is_file() {
        fs::copy(&backup, &shortcut).map_err(|error| error.to_string())?;
        fs::remove_file(backup).map_err(|error| error.to_string())?;
        let _ = fs::remove_file(owned);
        return Ok(());
    }
    match fs::remove_file(shortcut) {
        Ok(()) => {
            let _ = fs::remove_file(owned);
            Ok(())
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            let _ = fs::remove_file(owned);
            Ok(())
        }
        Err(error) => Err(error.to_string()),
    }
}

#[cfg(not(windows))]
pub fn register() -> Result<String, String> {
    Err("Windows registration is unavailable on this platform".to_owned())
}
#[cfg(not(windows))]
pub fn unregister() -> Result<String, String> {
    Err("Windows registration is unavailable on this platform".to_owned())
}
#[cfg(not(windows))]
pub fn launch_activation(_: &str) -> Result<(), String> {
    Err("Windows registration is unavailable on this platform".to_owned())
}
#[cfg(not(windows))]
pub fn status() -> Result<String, String> {
    Err("Windows registration is unavailable on this platform".to_owned())
}
