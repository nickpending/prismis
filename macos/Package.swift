// swift-tools-version: 6.0
import PackageDescription

let package = Package(
  name: "Prismis",
  platforms: [.macOS(.v14)],
  products: [
    .executable(name: "Prismis", targets: ["Prismis"])
  ],
  targets: [
    // Everything testable: config, API client, last-looked store, poll decision, panel presentation,
    // item link, login toggle. No UI and no ServiceManagement import, so all of it runs under `swift test`.
    .target(name: "PrismisCore"),
    // Menu-bar app: SwiftUI MenuBarExtra, its window-style panel, the login item.
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
