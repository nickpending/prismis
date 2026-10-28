import Foundation
import Testing

@testable import PrismisCore

/// C1: the app posts no notifications. Nothing under Sources reaches the notification centre, Info.plist
/// declares no notification usage, and the Poller has no notifier to hand anything to.
@Suite struct NoNotificationsTests {
  /// The package root: this file is Tests/PrismisCoreTests/NoNotificationsTests.swift.
  static let package = URL(fileURLWithPath: #filePath)
    .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()

  /// What any use of the notification centre names: its framework, its centre and delegate, and the
  /// AppKit API it replaced.
  static let needles = ["UserNotifications", "UNUserNotificationCenter", "NSUserNotification"]

  @Test func noSourceUsesTheNotificationCentre() throws {
    let sources = Self.package.appendingPathComponent("Sources")
    let walker = try #require(
      FileManager.default.enumerator(at: sources, includingPropertiesForKeys: nil))
    var files: [URL] = []
    for case let file as URL in walker where file.pathExtension == "swift" { files.append(file) }
    #expect(files.count >= 10, "found only \(files.map(\.lastPathComponent)) under \(sources.path)")

    var hits: [String] = []
    for file in files {
      let text = try String(contentsOf: file, encoding: .utf8)
      for (n, line) in text.components(separatedBy: "\n").enumerated()
      where Self.needles.contains(where: line.contains) {
        hits.append("\(file.lastPathComponent):\(n + 1): \(line)")
      }
    }
    #expect(hits.isEmpty, "\(hits)")
  }

  @Test func infoPlistDeclaresNoNotificationUsage() throws {
    let data = try Data(contentsOf: Self.package.appendingPathComponent("Resources/Info.plist"))
    let plist = try #require(
      try PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any])
    #expect(plist["CFBundleIdentifier"] as? String == "info.voidwire.Prismis")
    let keys = Self.keys(plist)
    #expect(!keys.contains { $0.localizedCaseInsensitiveContains("notification") }, "\(keys)")
  }

  /// Every key in `plist`, nested dictionaries included.
  private static func keys(_ plist: [String: Any]) -> [String] {
    plist.flatMap { key, value in [key] + ((value as? [String: Any]).map(keys) ?? []) }
  }

  @Test func aPollThatFindsNewItemsReturnsItsSnapshot() async throws {
    let fixture = LookFixture()
    let server = try await FakeServer()
    defer { server.stop() }
    server.list(fixture.rows)
    let p = poller(server, store: try lastLookedFile(fixture.t))

    let snapshot = await p.poll()

    #expect(snapshot.newCount == 2)
    #expect(await p.snapshot == snapshot)
  }
}
