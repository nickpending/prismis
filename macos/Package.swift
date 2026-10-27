// swift-tools-version: 6.0
import PackageDescription

let package = Package(
  name: "Prismis",
  platforms: [.macOS(.v14)],
  products: [
    .executable(name: "Prismis", targets: ["Prismis"])
  ],
  targets: [
    // Everything testable: config, API client, alerted store, poll decision, item link, login toggle.
    // No UI and no UserNotifications or ServiceManagement import, so all of it runs under `swift test`.
    .target(name: "PrismisCore"),
    // Menu-bar app: SwiftUI MenuBarExtra, the notifier, the login item.
    .executableTarget(
      name: "Prismis",
      dependencies: ["PrismisCore"]
    ),
    .testTarget(
      name: "PrismisCoreTests",
      dependencies: ["PrismisCore"]
    ),
  ]
)
