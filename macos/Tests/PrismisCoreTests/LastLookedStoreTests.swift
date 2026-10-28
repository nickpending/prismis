import Foundation
import Testing

@testable import PrismisCore

/// The last-looked time survives a restart and is written atomically.
@Suite struct LastLookedStoreTests {
  let t = Date(timeIntervalSince1970: 1_793_095_200.123)

  private func inode(_ file: URL) throws -> UInt64 {
    let attrs = try FileManager.default.attributesOfItem(atPath: file.path)
    return try #require(attrs[.systemFileNumber] as? UInt64)
  }

  @Test func aStoredTimeIsReadBackByANewStore() throws {
    let file = try tempDirectory("looked").appendingPathComponent("last-looked.json")
    var store = LastLookedStore(file: file)
    #expect(store.lastLooked == nil)
    try store.store(t)
    #expect(sameInstant(store.lastLooked, t))
    #expect(sameInstant(LastLookedStore(file: file).lastLooked, t))
    // The file is the documented shape, the time as ISO 8601 text.
    let json = try #require(
      try JSONSerialization.jsonObject(with: Data(contentsOf: file)) as? [String: String])
    #expect(json.keys.sorted() == ["last_looked"])
    #expect(sameInstant(json["last_looked"].flatMap(DaemonTime.parse), t))
  }

  @Test func aWriteMakesTheDirectoryAndRenamesTheFileIntoPlace() throws {
    let dir = try tempDirectory("looked-rename").appendingPathComponent("Prismis")
    let file = dir.appendingPathComponent("last-looked.json")
    var store = LastLookedStore(file: file)
    try store.store(t)
    let before = try inode(file)
    try store.store(t.addingTimeInterval(60))
    // A rename puts a new inode at the path; a write in place keeps the old one.
    #expect(try inode(file) != before)
    let left = try FileManager.default.contentsOfDirectory(atPath: dir.path)
    #expect(left == ["last-looked.json"], "the directory holds \(left)")
    #expect(sameInstant(storedTime(file), t.addingTimeInterval(60)))
  }

  @Test func aWriteThatCannotCompleteLeavesTheOldFileWhole() throws {
    let file = try lastLookedFile(t)
    let dir = file.deletingLastPathComponent()
    let bytes = try Data(contentsOf: file)
    var store = LastLookedStore(file: file)
    // The directory refuses a new file; the file itself stays writable, so only a writer that goes
    // through a temporary file in the directory is stopped here.
    try FileManager.default.setAttributes([.posixPermissions: 0o500], ofItemAtPath: dir.path)
    defer {
      try? FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: dir.path)
    }

    #expect(throws: LastLookedStoreError.self) { try store.store(t.addingTimeInterval(60)) }
    #expect(try Data(contentsOf: file) == bytes)
    // This process goes on from the new time.
    #expect(sameInstant(store.lastLooked, t.addingTimeInterval(60)))
  }

  @Test func anAbsentOrDamagedFileHoldsNoTime() throws {
    let dir = try tempDirectory("looked-damaged")
    #expect(LastLookedStore(file: dir.appendingPathComponent("absent.json")).lastLooked == nil)
    for body in ["{", "{\"last_looked\": \"yesterday\"}", "{\"last_looked\": 5}", "[]"] {
      let file = dir.appendingPathComponent("damaged.json")
      try Data(body.utf8).write(to: file)
      #expect(LastLookedStore(file: file).lastLooked == nil, "\(body) gave a time")
    }
  }
}
