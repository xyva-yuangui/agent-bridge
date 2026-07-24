mod protocol;
mod registration;
mod toast;

use std::io::{self, Read};
use std::path::PathBuf;
use std::process::Command;
use protocol::{parse_request, Request, Response};

fn main() {
    let response = run().unwrap_or_else(Response::failure);
    // The Python client accepts exactly one bounded JSON object on stdout.
    println!("{}", serde_json::to_string(&response).expect("response is serializable"));
}

fn run() -> Result<Response, String> {
    let args: Vec<String> = std::env::args().collect();
    if args.len() == 3 && args[1] == "action-uri" {
        return handle_activation_uri(&args[2]);
    }
    if args.len() != 1 { return Err("helper accepts only one JSON request on stdin".to_owned()); }
    let mut input = Vec::new();
    io::stdin().take((protocol::MAX_INPUT_BYTES + 1) as u64).read_to_end(&mut input).map_err(|error| error.to_string())?;
    match parse_request(&input)? {
        Request::Register => registration::register().map(Response::registered),
        Request::Unregister => registration::unregister().map(Response::registered),
        request @ Request::Post { .. } => toast::post(&request).map(|id| Response::posted(id, "WinRT toast accepted by the operating system")),
        Request::Action { action, notification_id, task_id } => handle_action(action, notification_id, task_id),
    }
}

fn handle_activation_uri(uri: &str) -> Result<Response, String> {
    let prefix = "agent-bridge://action/";
    if !uri.starts_with(prefix) || uri.len() > 1024 || uri.chars().any(char::is_whitespace) { return Err("invalid Agent Bridge activation URI".to_owned()); }
    let (action, query) = uri[prefix.len()..].split_once('?').ok_or_else(|| "activation URI has no query".to_owned())?;
    if !matches!(action, "view" | "claim" | "snooze") { return Err("invalid activation action".to_owned()); }
    let notification_id = query.split('&').find_map(|part| part.strip_prefix("notification_id=")).ok_or_else(|| "activation URI has no notification ID".to_owned())?;
    if notification_id.is_empty() || notification_id.len() > 256 || !notification_id.bytes().all(|b| b.is_ascii_alphanumeric() || matches!(b, b'.' | b'_' | b'-')) { return Err("invalid activation notification ID".to_owned()); }
    // The target executable is a fixed sibling installed with the helper. User-controlled URI data is supplied only as argv.
    bridge_command()?.args(["open-action", "--notification-id", notification_id, "--action", action]).status().map_err(|error| error.to_string())?;
    Ok(Response::registered("forwarded constrained Agent Bridge activation"))
}

fn handle_action(action: protocol::Action, notification_id: String, task_id: String) -> Result<Response, String> {
    let name = match action { protocol::Action::View => "view", protocol::Action::Claim => "claim", protocol::Action::Snooze => "snooze" };
    let _ = task_id; // Native action resolution uses the durable notification mapping, never a caller-supplied task ID.
    bridge_command()?.args(["open-action", "--notification-id", &notification_id, "--action", name]).status().map_err(|error| error.to_string())?;
    Ok(Response::posted(notification_id, format!("forwarded opaque {} action", name)))
}

fn bridge_command() -> Result<PathBuf, String> {
    let helper = std::env::current_exe().map_err(|error| error.to_string())?;
    let bridge = helper.parent().ok_or_else(|| "helper has no installation directory".to_owned())?.join("bridge.exe");
    if !bridge.is_file() { return Err("installed bridge.exe action handler is unavailable".to_owned()); }
    Ok(bridge)
}
