// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "AgentBridgeNotifier",
    platforms: [.macOS(.v12)],
    products: [.executable(name: "AgentBridgeNotifier", targets: ["AgentBridgeNotifier"])],
    targets: [.executableTarget(name: "AgentBridgeNotifier", path: "Sources/AgentBridgeNotifier")]
)
