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

func run() -> Response {
    do {
        let request = try parseRequest(readRequest())
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

let response = run()
let encoder = JSONEncoder()
encoder.outputFormatting = [.withoutEscapingSlashes]
FileHandle.standardOutput.write((try! encoder.encode(response)) + Data([0x0a]))
