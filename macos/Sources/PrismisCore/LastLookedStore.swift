import Foundation

public enum LastLookedStoreError: Error, CustomStringConvertible {
  case write(path: String, errno: Int32)

  public var description: String {
    switch self {
    case .write(let path, let code):
      return "could not write \(path): \(String(cString: strerror(code)))"
    }
  }
}

/// When the operator last looked, as one daemon `fetched_at` in one JSON file:
/// `{"last_looked": "<ISO 8601 time>"}`. It is always a time the daemon wrote, never the Mac's clock, so
/// the two machines' clocks never meet. A file that is absent or cannot be read holds no time.
public struct LastLookedStore: Sendable {
  public static var defaultFile: URL {
    FileManager.default.homeDirectoryForCurrentUser
      .appendingPathComponent("Library/Application Support/Prismis/last-looked.json")
  }

  public let file: URL
  public private(set) var lastLooked: Date?

  private struct Contents: Codable {
    var lastLooked: String

    enum CodingKeys: String, CodingKey { case lastLooked = "last_looked" }
  }

  public init(file: URL = defaultFile) {
    self.file = file
    lastLooked = (try? Data(contentsOf: file))
      .flatMap { try? JSONDecoder().decode(Contents.self, from: $0) }
      .flatMap { DaemonTime.parse($0.lastLooked) }
  }

  /// Takes `time` as the last-looked time and writes it. The time is held in memory even when the write
  /// throws, so this process goes on counting from it.
  public mutating func store(_ time: Date) throws {
    lastLooked = time
    try write(Contents(lastLooked: DaemonTime.format(time)))
  }

  /// Written to a temporary file in the same directory, flushed, and renamed over the file, so a write that
  /// stops partway leaves the previous time whole.
  private func write(_ contents: Contents) throws {
    let dir = file.deletingLastPathComponent()
    try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    let data = try JSONEncoder().encode(contents)

    let tmp = dir.appendingPathComponent(".\(file.lastPathComponent).\(UUID().uuidString).tmp")
    let fd = open(tmp.path, O_WRONLY | O_CREAT | O_EXCL, 0o600)
    if fd < 0 { throw LastLookedStoreError.write(path: tmp.path, errno: errno) }
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
      throw LastLookedStoreError.write(path: file.path, errno: code)
    }
  }
}
