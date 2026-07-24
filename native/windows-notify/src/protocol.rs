use serde::{Deserialize, Serialize};

pub const MAX_INPUT_BYTES: usize = 16 * 1024;
pub const MAX_TITLE_CHARS: usize = 256;
pub const MAX_BODY_CHARS: usize = 2048;
pub const MAX_IDENTIFIER_CHARS: usize = 256;
pub const MAX_DETAIL_CHARS: usize = 1024;

#[derive(Debug, Deserialize, PartialEq)]
#[serde(deny_unknown_fields, tag = "operation", rename_all = "snake_case")]
pub enum Request {
    Post {
        title: String,
        body: String,
        task_id: String,
        actions: Vec<Action>,
        expires_in_seconds: u32,
    },
    Register,
    Unregister,
    Status,
    Action {
        action: Action,
        notification_id: String,
        task_id: String,
    },
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum Action { View, Claim, Snooze }

#[derive(Debug, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Response {
    pub ok: bool,
    pub notification_id: String,
    pub status: String,
    pub detail: String,
}

impl Response {
    pub fn posted(notification_id: String, detail: impl Into<String>) -> Self {
        Self { ok: true, notification_id, status: "os_posted".to_owned(), detail: bounded_detail(detail.into()) }
    }

    pub fn registered(detail: impl Into<String>) -> Self {
        Self { ok: true, notification_id: "registration".to_owned(), status: "os_posted".to_owned(), detail: bounded_detail(detail.into()) }
    }

    pub fn failure(detail: impl Into<String>) -> Self {
        Self { ok: false, notification_id: String::new(), status: "failed".to_owned(), detail: bounded_detail(detail.into()) }
    }
}

pub fn parse_request(input: &[u8]) -> Result<Request, String> {
    if input.len() > MAX_INPUT_BYTES { return Err("request exceeds 16384 bytes".to_owned()); }
    let value: serde_json::Value = serde_json::from_slice(input).map_err(|_| "malformed request JSON".to_owned())?;
    enforce_exact_fields(&value)?;
    let request: Request = serde_json::from_value(value).map_err(|_| "malformed request JSON".to_owned())?;
    validate(&request)?;
    Ok(request)
}

fn enforce_exact_fields(value: &serde_json::Value) -> Result<(), String> {
    let object = value.as_object().ok_or_else(|| "request must be an object".to_owned())?;
    let operation = object.get("operation").and_then(serde_json::Value::as_str).ok_or_else(|| "request operation is missing".to_owned())?;
    let allowed: &[&str] = match operation {
        "post" => &["operation", "title", "body", "task_id", "actions", "expires_in_seconds"],
        "register" | "unregister" | "status" => &["operation"],
        "action" => &["operation", "action", "notification_id", "task_id"],
        _ => return Err("unknown request operation".to_owned()),
    };
    if object.len() != allowed.len() || object.keys().any(|key| !allowed.contains(&key.as_str())) {
        return Err("request has unknown or missing fields".to_owned());
    }
    Ok(())
}

fn validate(request: &Request) -> Result<(), String> {
    match request {
        Request::Post { title, body, task_id, actions, expires_in_seconds } => {
            text(title, "title", MAX_TITLE_CHARS)?;
            text(body, "body", MAX_BODY_CHARS)?;
            opaque(task_id, "task_id")?;
            if actions.as_slice() != [Action::View, Action::Claim, Action::Snooze] { return Err("actions must be view, claim, snooze".to_owned()); }
            if *expires_in_seconds == 0 || *expires_in_seconds > 86_400 { return Err("invalid expires_in_seconds".to_owned()); }
        }
        Request::Action { action: _, notification_id, task_id } => {
            opaque(notification_id, "notification_id")?;
            opaque(task_id, "task_id")?;
        }
        Request::Register | Request::Unregister | Request::Status => {}
    }
    Ok(())
}

fn text(value: &str, field: &str, maximum: usize) -> Result<(), String> {
    if value.is_empty() || value.chars().count() > maximum { Err(format!("invalid {}", field)) } else { Ok(()) }
}

fn opaque(value: &str, field: &str) -> Result<(), String> {
    if value.is_empty() || value.len() > MAX_IDENTIFIER_CHARS || !value.bytes().all(|b| b.is_ascii_alphanumeric() || matches!(b, b'.' | b'_' | b'-')) { Err(format!("invalid {}", field)) } else { Ok(()) }
}

fn bounded_detail(mut detail: String) -> String {
    if detail.chars().count() > MAX_DETAIL_CHARS { detail = detail.chars().take(MAX_DETAIL_CHARS).collect(); }
    detail
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn accepts_only_the_fixed_post_shape() {
        let request = parse_request(br#"{"operation":"post","title":"T","body":"B","task_id":"opaque-1","actions":["view","claim","snooze"],"expires_in_seconds":30}"#).unwrap();
        assert!(matches!(request, Request::Post { .. }));
    }

    #[test]
    fn rejects_unknown_fields_and_executable_action_data() {
        assert!(parse_request(br#"{"operation":"register","command":"calc.exe"}"#).is_err());
        assert!(parse_request(br#"{"operation":"post","title":"T","body":"B","task_id":"opaque-1","actions":["view"],"expires_in_seconds":30}"#).is_err());
    }

    #[test]
    fn rejects_oversized_input() {
        assert!(parse_request(&vec![b' '; MAX_INPUT_BYTES + 1]).is_err());
    }
}
