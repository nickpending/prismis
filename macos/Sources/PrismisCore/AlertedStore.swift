import Foundation

public enum AlertedStoreError: Error, CustomStringConvertible {
  case write(path: String, errno: Int32)

  public var description: String {
    switch self {
    case .write(let path, let code):
      return "could not write \(path): \(String(cString: strerror(code)))"
    }
  }
}

/// The items already alerted, each with the time it was first alerted, in one JSON file:
/// `{"alerted": {"<item id>": "<ISO 8601 time>"}}`. A store whose file does not exist yet is unseeded: the
/// first poll seeds it with every listed item and alerts none. A file that cannot be read counts as absent,
/// so a damaged store reseeds rather than alerting everything again.
public struct AlertedStore: Sendable {
  /// Entries first alerted longer ago than this are dropped on the next write.
  public static let retention: TimeInterval = 30 * 86400

  public static var defaultFile: URL {
    FileManager.default.homeDirectoryForCurrentUser
      .appendingPathComponent("Library/Application Support/Prismis/alerted.json")
  }

  public let file: URL
  public private(set) var seeded: Bool
  private var entries: [String: Date]

  private struct Contents: Codable {
    var alerted: [String: Date]
  }

  public init(file: URL = defaultFile) {
    self.file = file
    let decoder = JSONDecoder()
    decoder.dateDecodingStrategy = .iso8601
    if let data = try? Data(contentsOf: file),
      let contents = try? decoder.decode(Contents.self, from: data)
    {
      entries = contents.alerted
      seeded = true
    } else {
      entries = [:]
      seeded = false
    }
  }

  public func contains(_ id: String) -> Bool { entries[id] != nil }

  public func alertedAt(_ id: String) -> Date? { entries[id] }

  /// Adds every id not already held as first alerted at `now`, drops entries older than `retention`, and
  /// writes the file when it changed or does not exist yet. The ids are held in memory even when the
  /// write throws, so this process does not alert them again.
  public mutating func record(_ ids: [String], now: Date) throws {
    var next = entries
    for id in ids where next[id] == nil { next[id] = now }
    next = next.filter { now.timeIntervalSince($0.value) <= Self.retention }
    let changed = next != entries
    entries = next
    guard changed || !seeded else { return }
    try write()
    seeded = true
  }

  /// Written to a temporary file in the same directory, flushed, and renamed over the store, so a write that
  /// stops partway leaves the previous store whole.
  private func write() throws {
    let dir = file.deletingLastPathComponent()
    try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    let encoder = JSONEncoder()
    encoder.dateEncodingStrategy = .iso8601
    encoder.outputFormatting = [.sortedKeys]
    let data = try encoder.encode(Contents(alerted: entries))

    let tmp = dir.appendingPathComponent(".\(file.lastPathComponent).\(UUID().uuidString).tmp")
    let fd = open(tmp.path, O_WRONLY | O_CREAT | O_EXCL, 0o600)
    if fd < 0 { throw AlertedStoreError.write(path: tmp.path, errno: errno) }
    let handle = FileHandle(fileDescriptor: fd, closeOnDealloc: true)
    do {
      try handle.write(contentsOf: data)
      try handle.synchronize()
      try handle.close()
    } catch {
      unlink(tmp.path)
      throw error
    }
    if rename(tmp.path, file.path) != 0 {
      let code = errno
      unlink(tmp.path)
      throw AlertedStoreError.write(path: file.path, errno: code)
    }
  }
}
