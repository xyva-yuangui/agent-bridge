#[cfg(windows)]
use std::env;
#[cfg(windows)]
use std::fs;
#[cfg(windows)]
use std::path::PathBuf;
#[cfg(windows)]
use winreg::enums::{HKEY_CURRENT_USER, KEY_WRITE};
#[cfg(windows)]
use winreg::RegKey;
#[cfg(windows)]
use windows::core::{HSTRING, Interface, PCWSTR, PROPVARIANT};
#[cfg(windows)]
use windows::Win32::Storage::EnhancedStorage::PKEY_AppUserModel_ID;
#[cfg(windows)]
use windows::Win32::System::Com::{CoCreateInstance, CoInitializeEx, CoUninitialize, IPersistFile, CLSCTX_INPROC_SERVER, COINIT_APARTMENTTHREADED};
#[cfg(windows)]
#[cfg(windows)]
use windows::Win32::UI::Shell::{IShellLinkW, ShellLink};
#[cfg(windows)]
use windows::Win32::UI::Shell::PropertiesSystem::IPropertyStore;

pub const AUMID: &str = "OpenAI.AgentBridge.WindowsNotify";
pub const PROTOCOL: &str = "agent-bridge";

#[cfg(windows)]
pub fn register(activation_argv: &[String]) -> Result<String, String> {
    if activation_argv.first().map(|value| std::path::Path::new(value).is_absolute()) != Some(true) { return Err("activation argv must begin with an absolute executable path".to_owned()); }
    let executable = env::current_exe().map_err(|error| error.to_string())?;
    let executable = executable.to_string_lossy();
    if executable.contains('"') { return Err("helper executable path contains an unsupported quote".to_owned()); }
    let classes = RegKey::predef(HKEY_CURRENT_USER).open_subkey_with_flags("Software\\Classes", KEY_WRITE)
        .or_else(|_| RegKey::predef(HKEY_CURRENT_USER).create_subkey("Software\\Classes").map(|pair| pair.0))
        .map_err(|error| error.to_string())?;
    let (protocol, _) = classes.create_subkey(PROTOCOL).map_err(|error| error.to_string())?;
    protocol.set_value("", &"URL:Agent Bridge action").map_err(|error| error.to_string())?;
    protocol.set_value("URL Protocol", &"").map_err(|error| error.to_string())?;
    let (command, _) = protocol.create_subkey("shell\\open\\command").map_err(|error| error.to_string())?;
    // Only the helper's own resolved path and the fixed Windows protocol placeholder are registered.
    command.set_value("", &format!("\"{}\" action-uri \"%1\"", executable)).map_err(|error| error.to_string())?;
    protocol.set_value("AgentBridgeActivationArgvJson", &serde_json::to_string(activation_argv).map_err(|error| error.to_string())?).map_err(|error| error.to_string())?;
    create_start_menu_shortcut(&executable)?;
    Ok(format!("registered per-user {} protocol for {}", PROTOCOL, AUMID))
}

#[cfg(windows)]
pub fn launch_activation(uri: &str) -> Result<(), String> {
    let classes = RegKey::predef(HKEY_CURRENT_USER).open_subkey("Software\\Classes").map_err(|error| error.to_string())?;
    let protocol = classes.open_subkey(PROTOCOL).map_err(|_| "Agent Bridge activation is not registered".to_owned())?;
    let raw: String = protocol.get_value("AgentBridgeActivationArgvJson").map_err(|_| "Agent Bridge activation argv is missing".to_owned())?;
    let argv: Vec<String> = serde_json::from_str(&raw).map_err(|_| "Agent Bridge activation argv is invalid".to_owned())?;
    if argv.first().map(|value| std::path::Path::new(value).is_absolute()) != Some(true) { return Err("Agent Bridge activation argv is unsafe".to_owned()); }
    std::process::Command::new(&argv[0]).args(&argv[1..]).arg("open-action").arg("--activation-uri").arg(uri).status().map_err(|error| error.to_string())?;
    Ok(())
}

#[cfg(windows)]
pub fn unregister() -> Result<String, String> {
    use windows::UI::Notifications::ToastNotificationManager;
    ToastNotificationManager::History().and_then(|history| history.ClearWithId(&HSTRING::from(AUMID))).map_err(|error| error.to_string())?;
    let classes = RegKey::predef(HKEY_CURRENT_USER).open_subkey_with_flags("Software\\Classes", KEY_WRITE).map_err(|error| error.to_string())?;
    match classes.delete_subkey_all(PROTOCOL) {
        Ok(()) => { remove_start_menu_shortcut()?; Ok(format!("unregistered per-user {} protocol", PROTOCOL)) },
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => { remove_start_menu_shortcut()?; Ok("protocol was not registered".to_owned()) },
        Err(error) => Err(error.to_string()),
    }
}

#[cfg(windows)]
pub fn status() -> Result<String, String> {
    let classes = RegKey::predef(HKEY_CURRENT_USER).open_subkey_with_flags("Software\\Classes", KEY_WRITE).map_err(|error| error.to_string())?;
    let command = classes.open_subkey(format!("{}\\shell\\open\\command", PROTOCOL)).map_err(|_| "Agent Bridge protocol registration is missing".to_owned())?;
    let value: String = command.get_value("").map_err(|_| "Agent Bridge protocol command is missing".to_owned())?;
    if !value.contains("action-uri") { return Err("Agent Bridge protocol command is invalid".to_owned()); }
    if !shortcut_path()?.is_file() { return Err("Agent Bridge AUMID shortcut is missing".to_owned()); }
    Ok(format!("per-user {} protocol and {} shortcut are registered", PROTOCOL, AUMID))
}

#[cfg(windows)]
fn shortcut_path() -> Result<PathBuf, String> {
    let app_data = env::var_os("APPDATA").ok_or_else(|| "APPDATA is unavailable".to_owned())?;
    Ok(PathBuf::from(app_data).join("Microsoft").join("Windows").join("Start Menu").join("Programs").join("Agent Bridge.lnk"))
}

#[cfg(windows)]
fn create_start_menu_shortcut(executable: &str) -> Result<(), String> {
    let shortcut = shortcut_path()?;
    let parent = shortcut.parent().ok_or_else(|| "invalid Start Menu shortcut path".to_owned())?;
    fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    unsafe {
        CoInitializeEx(None, COINIT_APARTMENTTHREADED).ok().map_err(|error| error.to_string())?;
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
fn remove_start_menu_shortcut() -> Result<(), String> {
    let shortcut = shortcut_path()?;
    match fs::remove_file(shortcut) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error.to_string()),
    }
}

#[cfg(not(windows))]
pub fn register() -> Result<String, String> { Err("Windows registration is unavailable on this platform".to_owned()) }
#[cfg(not(windows))]
pub fn unregister() -> Result<String, String> { Err("Windows registration is unavailable on this platform".to_owned()) }
#[cfg(not(windows))]
pub fn launch_activation(_: &str) -> Result<(), String> { Err("Windows registration is unavailable on this platform".to_owned()) }
#[cfg(not(windows))]
pub fn status() -> Result<String, String> { Err("Windows registration is unavailable on this platform".to_owned()) }
