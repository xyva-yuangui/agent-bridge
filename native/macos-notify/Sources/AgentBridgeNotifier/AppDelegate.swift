import Foundation
import UserNotifications

final class AppDelegate: NSObject, UNUserNotificationCenterDelegate {
    private let center = UNUserNotificationCenter.current()
    private let activationArgvKey = "agentBridgeActivationArgv"
    private let categoryIdentifier = "agent-bridge-task"
    private let expiryKey = "agentBridgeNotificationExpiries"
    var onActionHandled: (() -> Void)?

    func configure() {
        center.delegate = self
        let actions = [
            UNNotificationAction(identifier: "view", title: "View", options: []),
            UNNotificationAction(identifier: "claim", title: "Claim", options: []),
            UNNotificationAction(identifier: "snooze", title: "Snooze", options: []),
        ]
        let category = UNNotificationCategory(identifier: categoryIdentifier, actions: actions, intentIdentifiers: [], options: [])
        center.setNotificationCategories([category])
        cleanupExpiredNotifications()
    }

    func register(_ argv: [String]) throws -> Response {
        try validActivationArgv(argv)
        UserDefaults.standard.set(argv, forKey: activationArgvKey)
        return waitForAuthorization(detail: "authorization requested and fixed activation argv registered")
    }

    func unregister() -> Response {
        UserDefaults.standard.removeObject(forKey: activationArgvKey)
        center.removeAllPendingNotificationRequests()
        center.removeAllDeliveredNotifications()
        UserDefaults.standard.removeObject(forKey: expiryKey)
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
        guard UserDefaults.standard.array(forKey: activationArgvKey) as? [String] != nil else {
            return .failure("fixed activation argv is not registered")
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
        // macOS has no native expiration for immediately delivered local requests.
        // Reusing this identifier replaces both pending and delivered task notices.
        center.removePendingNotificationRequests(withIdentifiers: [notificationID])
        center.removeDeliveredNotifications(withIdentifiers: [notificationID])
        let request = UNNotificationRequest(identifier: notificationID, content: content, trigger: nil)
        let semaphore = DispatchSemaphore(value: 0)
        var failure: Error?
        center.add(request) { error in failure = error; semaphore.signal() }
        guard semaphore.wait(timeout: .now() + 1.0) == .success else { return .failure("timed out posting UserNotifications request") }
        guard failure == nil else { return .failure("UserNotifications rejected request: \(failure!.localizedDescription)") }
        recordExpiry(notificationID: notificationID, after: expiresInSeconds)
        if expiresInSeconds <= 30, scheduleExpiryCleanup(notificationID: notificationID, after: expiresInSeconds) {
            return .posted(notificationID, "UserNotifications accepted request; replacement is supported and expiration scheduled for \(expiresInSeconds) seconds")
        }
        return .posted(notificationID, "UserNotifications accepted request; replacement is supported, expiration unsupported beyond 30 seconds because macOS has no native expiration; cleanup runs on the next invocation")
    }

    func forward(action: NotificationAction, notificationID: String) -> Response {
        do {
            try opaqueID(notificationID, "notification_id")
            try launchFixedHandler(action: action, notificationID: notificationID)
            return .posted(notificationID, "forwarded opaque \(action.rawValue) action")
        } catch { return .failure(error.localizedDescription) }
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse, withCompletionHandler completionHandler: @escaping () -> Void) {
        defer { completionHandler(); onActionHandled?() }
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
        let activationURI = "agent-bridge://action/\(action.rawValue)/\(notificationID)"
        process.arguments = Array(argv.dropFirst()) + ["open-action", "--activation-uri", activationURI]
        try process.run()
    }

    func cleanupExpiredNotification(_ notificationID: String, expectedAt: TimeInterval) {
        guard Date().timeIntervalSince1970 >= expectedAt else { return }
        center.removePendingNotificationRequests(withIdentifiers: [notificationID])
        center.removeDeliveredNotifications(withIdentifiers: [notificationID])
        var expiries = notificationExpiries()
        expiries.removeValue(forKey: notificationID)
        UserDefaults.standard.set(expiries, forKey: expiryKey)
    }

    private func cleanupExpiredNotifications() {
        let now = Date().timeIntervalSince1970
        for (notificationID, expiry) in notificationExpiries() where expiry <= now {
            cleanupExpiredNotification(notificationID, expectedAt: expiry)
        }
    }

    private func recordExpiry(notificationID: String, after seconds: Int) {
        var expiries = notificationExpiries()
        expiries[notificationID] = Date().timeIntervalSince1970 + TimeInterval(seconds)
        UserDefaults.standard.set(expiries, forKey: expiryKey)
    }

    private func notificationExpiries() -> [String: TimeInterval] {
        let raw = UserDefaults.standard.dictionary(forKey: expiryKey) ?? [:]
        return raw.reduce(into: [:]) { values, pair in
            if let expiry = pair.value as? TimeInterval { values[pair.key] = expiry }
        }
    }

    private func scheduleExpiryCleanup(notificationID: String, after seconds: Int) -> Bool {
        guard (1...30).contains(seconds), let executable = Bundle.main.executableURL else { return false }
        let child = Process()
        child.executableURL = executable
        child.arguments = ["--cleanup-expired", notificationID, String(Date().timeIntervalSince1970 + TimeInterval(seconds))]
        child.standardInput = FileHandle.nullDevice
        child.standardOutput = FileHandle.nullDevice
        child.standardError = FileHandle.nullDevice
        do { try child.run(); return true } catch { return false }
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
