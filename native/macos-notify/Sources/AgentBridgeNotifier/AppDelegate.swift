import Foundation
import UserNotifications

final class AppDelegate: NSObject, UNUserNotificationCenterDelegate {
    private let center = UNUserNotificationCenter.current()
    private let activationArgvKey = "agentBridgeActivationArgv"
    private let categoryIdentifier = "agent-bridge-task"

    func configure() {
        center.delegate = self
        let actions = [
            UNNotificationAction(identifier: "view", title: "View", options: []),
            UNNotificationAction(identifier: "claim", title: "Claim", options: []),
            UNNotificationAction(identifier: "snooze", title: "Snooze", options: []),
        ]
        let category = UNNotificationCategory(identifier: categoryIdentifier, actions: actions, intentIdentifiers: [], options: [])
        center.setNotificationCategories([category])
    }

    func register(_ argv: [String]) throws -> Response {
        try validActivationArgv(argv)
        UserDefaults.standard.set(argv, forKey: activationArgvKey)
        return waitForAuthorization(detail: "authorization requested and fixed activation argv registered")
    }

    func unregister() -> Response {
        UserDefaults.standard.removeObject(forKey: activationArgvKey)
        center.removeAllPendingNotificationRequests()
        return .registered("activation argv removed and pending Agent Bridge notifications cleared")
    }

    func status() -> Response {
        let semaphore = DispatchSemaphore(value: 0)
        var settings: UNNotificationSettings?
        center.getNotificationSettings { value in settings = value; semaphore.signal() }
        guard semaphore.wait(timeout: .now() + 1.0) == .success, let current = settings else {
            return .failure("timed out reading UserNotifications authorization status")
        }
        guard current.authorizationStatus == .authorized || current.authorizationStatus == .provisional || current.authorizationStatus == .ephemeral else {
            return .failure("UserNotifications authorization is \(authorizationName(current.authorizationStatus))")
        }
        return .registered("UserNotifications authorization is \(authorizationName(current.authorizationStatus))")
    }

    func post(title: String, body: String, taskID: String, expiresInSeconds: Int) -> Response {
        let authorization = waitForAuthorization(detail: "UserNotifications authorization granted")
        guard authorization.ok else { return authorization }
        let notificationID = "mac-\(taskID)"
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        content.categoryIdentifier = categoryIdentifier
        content.userInfo = ["notification_id": notificationID, "task_id": taskID]
        // UserNotifications has no expiration API for immediately delivered local requests.
        // Reusing this identifier replaces a pending request for the same opaque task ID.
        center.removePendingNotificationRequests(withIdentifiers: [notificationID])
        let request = UNNotificationRequest(identifier: notificationID, content: content, trigger: nil)
        let semaphore = DispatchSemaphore(value: 0)
        var failure: Error?
        center.add(request) { error in failure = error; semaphore.signal() }
        guard semaphore.wait(timeout: .now() + 1.0) == .success else { return .failure("timed out posting UserNotifications request") }
        guard failure == nil else { return .failure("UserNotifications rejected request: \(failure!.localizedDescription)") }
        return .posted(notificationID, "UserNotifications accepted request; replacement is supported, expiration \(expiresInSeconds)s is not supported by macOS")
    }

    func forward(action: NotificationAction, notificationID: String) -> Response {
        do {
            try opaqueID(notificationID, "notification_id")
            try launchFixedHandler(action: action, notificationID: notificationID)
            return .posted(notificationID, "forwarded opaque \(action.rawValue) action")
        } catch { return .failure(error.localizedDescription) }
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse, withCompletionHandler completionHandler: @escaping () -> Void) {
        defer { completionHandler() }
        guard let action = NotificationAction(rawValue: response.actionIdentifier),
              let notificationID = response.notification.request.content.userInfo["notification_id"] as? String else { return }
        try? launchFixedHandler(action: action, notificationID: notificationID)
    }

    private func waitForAuthorization(detail: String) -> Response {
        let semaphore = DispatchSemaphore(value: 0)
        var granted = false
        var failure: Error?
        center.requestAuthorization(options: [.alert, .badge, .sound]) { allowed, error in granted = allowed; failure = error; semaphore.signal() }
        guard semaphore.wait(timeout: .now() + 2.0) == .success else { return .failure("timed out requesting UserNotifications authorization") }
        guard failure == nil, granted else { return .failure("UserNotifications authorization was not granted") }
        return .registered(detail)
    }

    private func launchFixedHandler(action: NotificationAction, notificationID: String) throws {
        guard let argv = UserDefaults.standard.array(forKey: activationArgvKey) as? [String] else { throw ProtocolError("fixed activation argv is not registered") }
        try validActivationArgv(argv)
        let process = Process()
        process.executableURL = URL(fileURLWithPath: argv[0])
        process.arguments = Array(argv.dropFirst()) + ["open-action", "--notification-id", notificationID, "--action", action.rawValue]
        try process.run()
    }

    private func authorizationName(_ value: UNAuthorizationStatus) -> String {
        switch value {
        case .authorized: return "authorized"
        case .denied: return "denied"
        case .notDetermined: return "not_determined"
        case .provisional: return "provisional"
        case .ephemeral: return "ephemeral"
        @unknown default: return "unknown"
        }
    }
}
