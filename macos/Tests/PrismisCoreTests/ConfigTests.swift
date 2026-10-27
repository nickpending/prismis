import Foundation
import Testing

@testable import PrismisCore

/// C1: the config is read from `<home>/.config/prismis/config.toml`, by section.
@Suite struct ConfigTests {
  /// A home directory holding `.config/prismis/config.toml` with `text` at `mode`.
  private func home(_ text: String, mode: Int = 0o600) throws -> URL {
    let home = try tempDirectory("config")
    let file = PrismisConfig.path(home: home)
    try FileManager.default.createDirectory(
      at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
    try text.write(to: file, atomically: true, encoding: .utf8)
    try FileManager.default.setAttributes([.posixPermissions: mode], ofItemAtPath: file.path)
    return home
  }

  /// The error `load` throws for `home`, or nil when it loads.
  private func refusal(_ home: URL) -> ConfigError? {
    do {
      _ = try PrismisConfig.load(home: home)
      return nil
    } catch let error as ConfigError {
      return error
    } catch {
      Issue.record("threw \(error), not a ConfigError")
      return nil
    }
  }

  @Test func aRemoteURLUsesTheRemoteKeyNotTheAPIKey() throws {
    // [remote] before [api]: a reader that ignores sections keeps the last `key`, A.
    let home = try home(
      """
      [remote]
      url = "https://example.test"  # the daemon on another machine
      key = "R"

      [api]
      key = "A"
      host = "127.0.0.1"
      """)
    let config = try PrismisConfig.load(home: home)
    #expect(config.baseURL.absoluteString == "https://example.test")
    #expect(config.key == "R")
  }

  @Test func withoutARemoteURLItIsTheLocalDaemonWithTheAPIKey() throws {
    let home = try home(
      """
      [api]
      key = "A"
      host = "0.0.0.0"
      """)
    let config = try PrismisConfig.load(home: home)
    #expect(config.baseURL.absoluteString == "http://127.0.0.1:8989")
    #expect(config.key == "A")
  }

  @Test func aRemoteKeyWithoutAURLStillMeansTheLocalDaemon() throws {
    // [remote] after [api], as the daemon's default config orders them: a reader that ignores
    // sections keeps the last `key`, R.
    let home = try home(
      """
      [api]
      key = "A"
      host = "0.0.0.0"

      [remote]
      # url = "https://prismis.example.com"
      key = "R"
      """)
    let config = try PrismisConfig.load(home: home)
    #expect(config.baseURL.absoluteString == "http://127.0.0.1:8989")
    #expect(config.key == "A")
  }

  @Test func aFileOthersCanReadIsRefusedNamingItsMode() throws {
    for (mode, digits) in [(0o644, "0644"), (0o640, "0640"), (0o604, "0604"), (0o620, "0620")] {
      let home = try home("[api]\nkey = \"A\"\n", mode: mode)
      let error = refusal(home)
      guard case .exposed(let path, let found, _) = error else {
        Issue.record("mode \(digits) gave \(String(describing: error)), not exposed")
        continue
      }
      #expect(path == PrismisConfig.path(home: home).path)
      #expect(found == UInt16(mode))
      #expect(error?.description.contains(digits) == true)
      #expect(error?.description.contains("chmod 600") == true)
    }
  }

  @Test func plainHTTPOffLoopbackIsRefusedNamingTheURL() throws {
    for url in ["http://prismis.example.lan:8989", "http://192.168.1.20:8989"] {
      let home = try home("[remote]\nurl = \"\(url)\"\nkey = \"R\"\n[api]\nkey = \"A\"\n")
      let error = refusal(home)
      #expect(error == .plaintextURL(url))
      #expect(error?.description.contains(url) == true)
    }
    for url in ["http://127.0.0.1:8989", "http://localhost:8989", "http://[::1]:8989"] {
      let home = try home("[remote]\nurl = \"\(url)\"\nkey = \"R\"\n")
      #expect(try PrismisConfig.load(home: home).baseURL.absoluteString == url)
    }
  }

  @Test func aMissingKeyInTheSectionInUseIsNamed() throws {
    // The remote is in use, so the [api] key does not stand in for its missing key.
    let remote = try home("[remote]\nurl = \"https://example.test\"\n[api]\nkey = \"A\"\n")
    #expect(refusal(remote) == .missingKey("[remote] key"))
    #expect(refusal(remote)?.description.contains("[remote] key") == true)
    let local = try home("[api]\nhost = \"0.0.0.0\"\n[remote]\nkey = \"R\"\n")
    #expect(refusal(local) == .missingKey("[api] key"))
  }

  @Test func aMissingFileIsItsOwnError() throws {
    let home = try tempDirectory("config-none")
    #expect(refusal(home) == .missingFile(PrismisConfig.path(home: home).path))
  }

  /// A file that is there but cannot be read is not reported as absent: the fix differs.
  @Test func aFileThatCannotBeOpenedIsUnreadableNotMissing() throws {
    let home = try home("[api]\nkey = \"A\"\n", mode: 0o000)
    let path = PrismisConfig.path(home: home).path
    defer { try? FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: path) }
    let error = refusal(home)
    guard case .unreadable(let named, let reason) = error else {
      Issue.record("an unopenable file gave \(String(describing: error)), not unreadable")
      return
    }
    #expect(named == path)
    #expect(reason.contains("Permission denied"))
    #expect(error?.description.contains("not found") == false)
  }

  @Test func aFileThatIsNotUTF8IsUnreadableNotMissing() throws {
    let home = try home("", mode: 0o600)
    let path = PrismisConfig.path(home: home).path
    try Data([0x5B, 0xFF, 0xFE, 0x5D]).write(to: URL(fileURLWithPath: path))
    let error = refusal(home)
    guard case .unreadable(let named, let reason) = error else {
      Issue.record("a non-UTF-8 file gave \(String(describing: error)), not unreadable")
      return
    }
    #expect(named == path)
    #expect(reason.contains("UTF-8"))
  }

  @Test func refusingAFileLeavesItsModeAndBytesAlone() throws {
    let home = try home("[api]\nkey = \"A\"\n", mode: 0o644)
    let file = PrismisConfig.path(home: home)
    let before = try Data(contentsOf: file)
    #expect(refusal(home) != nil)
    let attrs = try FileManager.default.attributesOfItem(atPath: file.path)
    #expect(attrs[.posixPermissions] as? Int == 0o644)
    #expect(try Data(contentsOf: file) == before)
  }
}
