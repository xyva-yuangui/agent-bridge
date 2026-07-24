use crate::protocol::{Action, Request};

pub fn stable_notification_id(task_id: &str) -> String {
    // Stable opaque identifier: content is never treated as a command or URI component without escaping.
    let mut hash: u64 = 0xcbf29ce484222325;
    for byte in task_id.as_bytes() { hash ^= u64::from(*byte); hash = hash.wrapping_mul(0x100000001b3); }
    format!("toast-{:016x}", hash)
}

#[cfg(windows)]
pub fn post(request: &Request) -> Result<String, String> {
    use windows::core::{HSTRING, Interface};
    use windows::Foundation::{DateTime, IReference, PropertyValue};
    use std::time::{SystemTime, UNIX_EPOCH};
    use windows::Data::Xml::Dom::XmlDocument;
    use windows::UI::Notifications::{ToastNotification, ToastNotificationManager};
    use crate::registration::{AUMID, PROTOCOL};

    let Request::Post { title, body, task_id, actions, expires_in_seconds } = request else { return Err("post request expected".to_owned()); };
    let notification_id = stable_notification_id(task_id);
    let action_xml = actions.iter().map(|action| {
        let name = match action { Action::View => "view", Action::Claim => "claim", Action::Snooze => "snooze" };
        let label = match action { Action::View => "View task", Action::Claim => "Claim", Action::Snooze => "Snooze" };
        format!("<action content=\"{}\" arguments=\"{}://action/{}/{}\" activationType=\"protocol\"/>", label, PROTOCOL, name, xml_escape(&notification_id))
    }).collect::<String>();
    // XmlDocument is the WinRT input format. Every untrusted text/attribute is escaped before insertion.
    let xml = format!("<toast launch=\"{}://action/view/{}\"><visual><binding template=\"ToastGeneric\"><text>{}</text><text>{}</text></binding></visual><actions>{}</actions></toast>", PROTOCOL, xml_escape(&notification_id), xml_escape(title), xml_escape(body), action_xml);
    let document = XmlDocument::new().map_err(|error| error.to_string())?;
    document.LoadXml(&HSTRING::from(xml)).map_err(|error| error.to_string())?;
    let toast = ToastNotification::CreateToastNotification(&document).map_err(|error| error.to_string())?;
    // Tag/group make retries for the same logical task replace the native notification instead of duplicating it.
    toast.SetTag(&HSTRING::from(&notification_id[6..22])).map_err(|error| error.to_string())?;
    toast.SetGroup(&HSTRING::from("agent-bridge")).map_err(|error| error.to_string())?;
    let unix_ticks = SystemTime::now().duration_since(UNIX_EPOCH).map_err(|error| error.to_string())?.as_secs() as i64 * 10_000_000;
    let expiry = DateTime { UniversalTime: 116_444_736_000_000_000i64 + unix_ticks + i64::from(*expires_in_seconds) * 10_000_000 };
    let expiry_ref: IReference<DateTime> = PropertyValue::CreateDateTime(expiry).map_err(|error| error.to_string())?.cast().map_err(|error| error.to_string())?;
    toast.SetExpirationTime(&expiry_ref).map_err(|error| error.to_string())?;
    let notifier = ToastNotificationManager::CreateToastNotifierWithId(&HSTRING::from(AUMID)).map_err(|error| error.to_string())?;
    notifier.Show(&toast).map_err(|error| error.to_string())?;
    Ok(notification_id)
}

#[cfg(windows)]
fn xml_escape(value: &str) -> String {
    value.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;").replace('"', "&quot;").replace('\'', "&apos;")
}

#[cfg(not(windows))]
pub fn post(_: &Request) -> Result<String, String> { Err("Windows WinRT toast APIs are unavailable on this platform".to_owned()) }

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn notification_identifier_is_stable_and_opaque() {
        assert_eq!(stable_notification_id("task-1"), stable_notification_id("task-1"));
        assert_ne!(stable_notification_id("task-1"), stable_notification_id("task-2"));
        assert!(stable_notification_id("task-1").starts_with("toast-"));
    }
}
