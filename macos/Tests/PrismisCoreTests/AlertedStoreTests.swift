import Foundation
import Testing

@testable import PrismisCore

/// C5: a write drops entries first alerted more than 30 days ago, and replaces the file by rename.
@Suite struct AlertedStoreTests {
  let old = "11111111-1111-4111-8111-111111111111"
  let today = item("22222222-2222-4222-8222-222222222222", "Today")
  let new = item("33333333-3333-4333-8333-333333333333", "New")

  /// A store file holding `entries`, each id with its first-alerted time.
  private func storeFile(_ entries: [String: Date]) throws -> URL {
    let dir = try tempDirectory("alerted")
    let file = dir.appendingPathComponent("alerted.json")
    let iso = ISO8601DateFormatter()
    let alerted = entries.mapValues { iso.string(from: $0) }
    let data = try JSONSerialization.data(withJSONObject: ["alerted": alerted])
    try data.write(to: file)
    return file
  }

  private func inode(_ file: URL) throws -> UInt64 {
    let attrs = try FileManager.default.attributesOfItem(atPath: file.path)
    return try #require(attrs[.systemFileNumber] as? UInt64)
  }

  @Test func aPollDropsEntriesOlderThanThirtyDaysAndRenamesTheFileIntoPlace() async throws {
    let now = Date()
    let todayAt = now.addingTimeInterval(-3600)
    let file = try storeFile([old: now.addingTimeInterval(-31 * 86400), today.id: todayAt])
    let before = try inode(file)
    let server = try await FakeServer()
    defer { server.stop() }
    server.list([today, new])
    let notifier = RecordingNotifier()

    await poller(server, store: file, notifier: notifier).poll(now: now)

    #expect(try storedIDs(file) == [today.id, new.id])
    #expect(notifier.posted == [ItemNotice(item: new)])
    // Today's entry keeps the time it was first alerted.
    let reopened = AlertedStore(file: file)
    let kept = try #require(reopened.alertedAt(today.id))
    #expect(abs(kept.timeIntervalSince(todayAt)) < 1)
    // A rename puts a new inode at the path; a write in place keeps the old one.
    #expect(try inode(file) != before)
    let left = try FileManager.default.contentsOfDirectory(
      atPath: file.deletingLastPathComponent().path)
    #expect(left == ["alerted.json"], "the directory holds \(left)")
  }

  @Test func aWriteThatCannotCompleteLeavesTheOldStoreWhole() throws {
    let file = try storeFile([today.id: Date()])
    let dir = file.deletingLastPathComponent()
    let bytes = try Data(contentsOf: file)
    var store = AlertedStore(file: file)
    // The directory refuses a new file; the store file itself stays writable, so only a writer that
    // goes through a temporary file in the directory is stopped here.
    try FileManager.default.setAttributes([.posixPermissions: 0o500], ofItemAtPath: dir.path)
    defer {
      try? FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: dir.path)
    }

    #expect(throws: (any Error).self) { try store.record([new.id], now: Date()) }
    #expect(try Data(contentsOf: file) == bytes)
  }

  @Test func aStoreWithNoFileIsUnseededAndOneWithAFileIsSeeded() throws {
    let dir = try tempDirectory("alerted-seed")
    let file = dir.appendingPathComponent("alerted.json")
    var store = AlertedStore(file: file)
    #expect(!store.seeded)
    try store.record([], now: Date())
    #expect(store.seeded)
    #expect(AlertedStore(file: file).seeded)
    #expect(try storedIDs(file).isEmpty)
  }
}
