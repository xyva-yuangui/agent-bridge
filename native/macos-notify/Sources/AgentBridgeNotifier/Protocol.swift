import Foundation

let maxInputBytes = 16 * 1024
let maxTitleCharacters = 256
let maxBodyCharacters = 2048
let maxIdentifierCharacters = 256
let maxDetailCharacters = 1024

enum NotificationAction: String, Codable, CaseIterable {
    case view, claim, snooze
}

enum Request {
    case post(title: String, body: String, taskID: String, actions: [NotificationAction], expiresInSeconds: Int)
    case register(activationArgv: [String])
    case unregister
    case status
    case action(action: NotificationAction, notificationID: String, taskID: String)
}

private struct PostPayload: Decodable {
    let title: String
    let body: String
    let taskID: String
    let actions: [String]
    let expiresInSeconds: Int

    enum CodingKeys: String, CodingKey {
        case title, body, actions
        case taskID = "task_id"
        case expiresInSeconds = "expires_in_seconds"
    }
}

private struct RegisterPayload: Decodable {
    let activationArgv: [String]
    enum CodingKeys: String, CodingKey { case activationArgv = "activation_argv" }
}

private struct ActionPayload: Decodable {
    let action: String
    let notificationID: String
    let taskID: String
    enum CodingKeys: String, CodingKey {
        case action
        case notificationID = "notification_id"
        case taskID = "task_id"
    }
}

struct Response: Codable {
    let ok: Bool
    let notification_id: String
    let status: String
    let detail: String

    static func posted(_ notificationID: String, _ detail: String) -> Response {
        Response(ok: true, notification_id: notificationID, status: "os_posted", detail: boundedDetail(detail))
    }

    static func registered(_ detail: String) -> Response {
        Response(ok: true, notification_id: "registration", status: "os_posted", detail: boundedDetail(detail))
    }

    static func failure(_ detail: String) -> Response {
        Response(ok: false, notification_id: "", status: "failed", detail: boundedDetail(detail))
    }
}

func parseRequest(_ data: Data) throws -> Request {
    guard data.count <= maxInputBytes else { throw ProtocolError("request exceeds 16384 bytes") }
    guard let object = try JSONSerialization.jsonObject(with: data) as? [String: Any],
          let operation = object["operation"] as? String else {
        throw ProtocolError("malformed request JSON")
    }
    func requireExact(_ fields: Set<String>) throws {
        guard Set(object.keys) == fields else { throw ProtocolError("request has unknown or missing fields") }
    }
    let decoder = JSONDecoder()
    switch operation {
    case "post":
        try requireExact(["operation", "title", "body", "task_id", "actions", "expires_in_seconds"])
        let payload: PostPayload
        do { payload = try decoder.decode(PostPayload.self, from: data) } catch { throw ProtocolError("malformed request JSON") }
        let actions = try payload.actions.map { value -> NotificationAction in
            guard let action = NotificationAction(rawValue: value) else { throw ProtocolError("invalid action") }
            return action
        }
        try boundedText(payload.title, "title", maxTitleCharacters)
        try boundedText(payload.body, "body", maxBodyCharacters)
        try opaqueID(payload.taskID, "task_id")
        guard actions == NotificationAction.allCases else { throw ProtocolError("actions must be view, claim, snooze") }
        guard (1...86_400).contains(payload.expiresInSeconds) else { throw ProtocolError("invalid expires_in_seconds") }
        return .post(title: payload.title, body: payload.body, taskID: payload.taskID, actions: actions, expiresInSeconds: payload.expiresInSeconds)
    case "register":
        try requireExact(["operation", "activation_argv"])
        let payload: RegisterPayload
        do { payload = try decoder.decode(RegisterPayload.self, from: data) } catch { throw ProtocolError("malformed request JSON") }
        try validActivationArgv(payload.activationArgv)
        return .register(activationArgv: payload.activationArgv)
    case "unregister":
        try requireExact(["operation"])
        return .unregister
    case "status":
        try requireExact(["operation"])
        return .status
    case "action":
        try requireExact(["operation", "action", "notification_id", "task_id"])
        let payload: ActionPayload
        do { payload = try decoder.decode(ActionPayload.self, from: data) } catch { throw ProtocolError("malformed request JSON") }
        guard let action = NotificationAction(rawValue: payload.action) else { throw ProtocolError("invalid action") }
        try opaqueID(payload.notificationID, "notification_id")
        try opaqueID(payload.taskID, "task_id")
        return .action(action: action, notificationID: payload.notificationID, taskID: payload.taskID)
    default:
        throw ProtocolError("unknown request operation")
    }
}

struct ProtocolError: Error, LocalizedError {
    let message: String
    init(_ message: String) { self.message = message }
    var errorDescription: String? { message }
}

func boundedText(_ value: String, _ field: String, _ maximum: Int) throws {
    guard !value.isEmpty, value.count <= maximum else { throw ProtocolError("invalid \(field)") }
}

func opaqueID(_ value: String, _ field: String) throws {
    let allowed = CharacterSet(charactersIn: "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-")
    guard !value.isEmpty, value.utf8.count <= maxIdentifierCharacters,
          value.unicodeScalars.allSatisfy({ allowed.contains($0) }) else { throw ProtocolError("invalid \(field)") }
}

func validActivationArgv(_ argv: [String]) throws {
    guard (1...16).contains(argv.count), argv.allSatisfy({ !$0.isEmpty && $0.count <= 1024 }),
          argv[0].hasPrefix("/"), !argv.contains(where: { $0.contains("\0") || $0.contains("\n") || $0.contains("\r") }) else {
        throw ProtocolError("invalid activation_argv")
    }
}

func boundedDetail(_ value: String) -> String { String(value.prefix(maxDetailCharacters)) }
