import AppKit
import Foundation

func readRequest() throws -> Data {
    var input = Data()
    while true {
        let chunk = FileHandle.standardInput.readData(ofLength: 4096)
        if chunk.isEmpty { break }
        input.append(chunk)
        guard input.count <= maxInputBytes else { throw ProtocolError("request exceeds 16384 bytes") }
    }
    return input
}

func run(_ input: Data) -> Response {
    do {
        let request = try parseRequest(input)
        let delegate = AppDelegate()
        delegate.configure()
        switch request {
        case let .post(title, body, taskID, _, expires): return delegate.post(title: title, body: body, taskID: taskID, expiresInSeconds: expires)
        case let .register(argv): return try delegate.register(argv)
        case .unregister: return delegate.unregister()
        case .status: return delegate.status()
        case let .action(action, notificationID, _): return delegate.forward(action: action, notificationID: notificationID)
        }
    } catch { return .failure(error.localizedDescription) }
}

func writeResponse(_ response: Response) {
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.withoutEscapingSlashes]
    FileHandle.standardOutput.write((try! encoder.encode(response)) + Data([0x0a]))
}

final class ApplicationLifecycleDelegate: NSObject, NSApplicationDelegate {
    private let notifier = AppDelegate()
    private var timeout: Timer?

    func applicationDidFinishLaunching(_ notification: Notification) {
        notifier.configure()
        notifier.onActionHandled = { [weak self] in self?.finish() }
        timeout = Timer.scheduledTimer(withTimeInterval: 30, repeats: false) { [weak self] _ in self?.finish() }
    }

    private func finish() {
        timeout?.invalidate()
        NSApp.terminate(nil)
    }
}

func runApplicationMode() {
    let application = NSApplication.shared
    application.setActivationPolicy(.accessory)
    let lifecycle = ApplicationLifecycleDelegate()
    application.delegate = lifecycle
    application.run()
}

func runExpiryCleanupChild(arguments: [String]) -> Bool {
    guard arguments.count == 5, arguments[1] == "--cleanup-expired",
          let expiry = TimeInterval(arguments[3]) else { return false }
    do { try opaqueID(arguments[2], "notification_id") } catch { return false }
    do { try opaqueID(arguments[4], "expiry_generation") } catch { return false }
    let delay = expiry - Date().timeIntervalSince1970
    guard (0.0...30.0).contains(delay) else { return false }
    if delay > 0 { Thread.sleep(forTimeInterval: delay) }
    let notifier = AppDelegate()
    notifier.configure()
    notifier.cleanupExpiredNotification(arguments[2], expectedAt: expiry, expectedGeneration: arguments[4])
    return true
}

let arguments = CommandLine.arguments
if runExpiryCleanupChild(arguments: arguments) {
    // The bounded cleanup child deliberately emits no protocol output.
} else if arguments == [CommandLine.arguments[0], "--protocol"] {
    do { writeResponse(run(try readRequest())) }
    catch { writeResponse(.failure(error.localizedDescription)) }
} else if arguments.count == 1 {
    runApplicationMode()
} else {
    writeResponse(.failure("helper arguments are invalid"))
}
