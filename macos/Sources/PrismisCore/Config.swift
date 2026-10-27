import Foundation

public enum ConfigError: Error, Equatable, CustomStringConvertible {
  case missingFile(String)
  /// The key the section in use lacks, as `[remote] key` or `[api] key`.
  case missingKey(String)
  case invalidURL(String)
  case plaintextURL(String)
  /// The file's path, its permission bits, and the uid that owns it.
  case exposed(path: String, mode: UInt16, owner: UInt32)

  public var description: String {
    switch self {
    case .missingFile(let p): return "config not found: \(p)"
    case .missingKey(let k): return "config has no \(k)"
    case .invalidURL(let v): return "[remote] url is not an http(s) URL: \(v)"
    case .plaintextURL(let v):
      return
        "[remote] url must be https unless the daemon is on this machine; http would send the key in the clear: \(v)"
    case .exposed(let path, let mode, let owner):
      return
        "config \(path) is mode \(String(format: "%04o", Int(mode))) owned by uid \(owner); it holds the API key, so it must be owned by you and readable by no one else: chmod 600 \(path)"
    }
  }
}

/// Where the daemon is and the key it takes, from `~/.config/prismis/config.toml`: the `[remote]` url and key
/// when a url is set there, otherwise the local daemon and the `[api]` key.
public struct PrismisConfig: Sendable, Equatable {
  /// The daemon's fixed port, as the CLI and TUI also hold it.
  public static let localURL = "http://127.0.0.1:8989"

  public let baseURL: URL
  public let key: String

  public init(baseURL: URL, key: String) {
    self.baseURL = baseURL
    self.key = key
  }

  public static func path(home: URL) -> URL {
    home.appendingPathComponent(".config/prismis/config.toml")
  }

  /// Refuses, before reading a byte of it, a file any group or other permission bit is set on or that a uid
  /// other than `effectiveUID` owns. It never changes the file.
  public static func load(
    home: URL = FileManager.default.homeDirectoryForCurrentUser, effectiveUID: uid_t = geteuid()
  ) throws -> PrismisConfig {
    let path = path(home: home)
    // Open once: the descriptor that is checked is the one that is read, so a swap of the path between the
    // check and the read cannot slip an unchecked file through.
    let fd = open(path.path, O_RDONLY)
    if fd < 0 { throw ConfigError.missingFile(path.path) }
    let handle = FileHandle(fileDescriptor: fd, closeOnDealloc: true)
    var info = stat()
    guard fstat(fd, &info) == 0 else { throw ConfigError.missingFile(path.path) }
    let mode = UInt16(info.st_mode & 0o777)
    if mode & 0o077 != 0 || info.st_uid != effectiveUID {
      throw ConfigError.exposed(path: path.path, mode: mode, owner: info.st_uid)
    }
    // readToEnd returns nil, not empty Data, for an empty file.
    guard let data = try? (handle.readToEnd() ?? Data()),
      let text = String(data: data, encoding: .utf8)
    else { throw ConfigError.missingFile(path.path) }
    return try parse(text)
  }

  public static func parse(_ text: String) throws -> PrismisConfig {
    let values = sections(text)
    if let url = values["remote"]?["url"], !url.isEmpty {
      guard let key = values["remote"]?["key"], !key.isEmpty else {
        throw ConfigError.missingKey("[remote] key")
      }
      return PrismisConfig(baseURL: try remoteURL(url), key: key)
    }
    guard let key = values["api"]?["key"], !key.isEmpty else {
      throw ConfigError.missingKey("[api] key")
    }
    // The daemon's [api] host is the address it binds (0.0.0.0 for every interface), not one to dial.
    guard let local = URL(string: localURL) else { throw ConfigError.invalidURL(localURL) }
    return PrismisConfig(baseURL: local, key: key)
  }

  private static func remoteURL(_ url: String) throws -> URL {
    guard let parsed = URL(string: url), let scheme = parsed.scheme,
      ["http", "https"].contains(scheme), parsed.host != nil
    else { throw ConfigError.invalidURL(url) }
    if scheme == "http", !["127.0.0.1", "localhost", "::1", "[::1]"].contains(parsed.host ?? "") {
      throw ConfigError.plaintextURL(url)
    }
    return parsed
  }

  /// The subset of TOML the two sections use: `[table]` headers, `key = "string"` lines and `#` comments,
  /// each string kept under the table it appears in, since `key` appears under both [api] and [remote].
  /// Lines that are not a quoted string value (numbers, booleans, arrays) are skipped.
  static func sections(_ text: String) -> [String: [String: String]] {
    var tables: [String: [String: String]] = [:]
    var table = ""
    for raw in text.split(whereSeparator: \.isNewline) {
      let line = raw.trimmingCharacters(in: .whitespaces)
      if line.isEmpty || line.hasPrefix("#") { continue }
      if line.hasPrefix("[") {
        let name = line.drop { $0 == "[" }.prefix { $0 != "]" }
        table = name.trimmingCharacters(in: .whitespaces)
        continue
      }
      guard let eq = line.firstIndex(of: "=") else { continue }
      let key = line[..<eq].trimmingCharacters(in: .whitespaces)
      let rest = line[line.index(after: eq)...].trimmingCharacters(in: .whitespaces)
      if let v = quoted(rest) { tables[table, default: [:]][key] = v }
    }
    return tables
  }

  private static func quoted(_ s: String) -> String? {
    guard let q = s.first, q == "\"" || q == "'" else { return nil }
    let body = s.dropFirst()
    guard let end = body.firstIndex(of: q) else { return nil }
    return String(body[..<end])
  }
}
