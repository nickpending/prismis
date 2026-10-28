import Foundation
import Network

@testable import PrismisCore

/// A local stand-in for the daemon's `GET /api/entries`, over real loopback HTTP. It records every request
/// and answers with whatever `reply` returns: by default 200 and an empty list. Tests set `reply` to a list
/// of items, an error status, or anything else.
final class FakeServer: @unchecked Sendable {
  struct Received: Sendable {
    let method: String
    let path: String
    let query: [String: String]
    let headers: [String: String]
  }

  private let listener: NWListener
  private let queue = DispatchQueue(label: "fake-prismis-daemon")
  private let lock = NSLock()
  private var received: [Received] = []
  private var _reply: @Sendable (Received) -> (Int, Data) = { _ in (200, FakeServer.entries([])) }
  private(set) var port: UInt16 = 0

  var reply: @Sendable (Received) -> (Int, Data) {
    get { lock.withLock { _reply } }
    set { lock.withLock { _reply = newValue } }
  }

  var requests: [Received] { lock.withLock { received } }
  var url: URL { URL(string: "http://127.0.0.1:\(port)")! }

  init() async throws {
    let params = NWParameters.tcp
    params.requiredLocalEndpoint = .hostPort(host: "127.0.0.1", port: .any)
    listener = try NWListener(using: params)
    listener.newConnectionHandler = { [unowned self] conn in self.serve(conn) }
    let ready: UInt16 = try await withCheckedThrowingContinuation { cont in
      let done = LockedBox(false)
      listener.stateUpdateHandler = { [unowned self] state in
        switch state {
        case .ready:
          if !done.value {
            done.mutate { $0 = true }
            cont.resume(returning: self.listener.port?.rawValue ?? 0)
          }
        case .failed(let e):
          if !done.value {
            done.mutate { $0 = true }
            cont.resume(throwing: e)
          }
        default: break
        }
      }
      listener.start(queue: queue)
    }
    port = ready
  }

  func stop() { listener.cancel() }

  /// Always answers `status` with an empty body.
  func answer(_ status: Int) { reply = { _ in (status, Data()) } }

  /// Answers 200 with `items`, in the order given, as the daemon's list view answers its query: with
  /// `since`, only the items whose `fetched_at` is after it, compared to the whole second as SQLite's
  /// `datetime()` compares them; then the first `limit`.
  func list(_ items: [Item]) {
    reply = { request in
      var listed = items
      if let since = request.query["since"] {
        guard let mark = Self.daemonTime(since) else { return (422, Data()) }
        listed = listed.filter { item in
          guard let fetched = item.fetchedAt else { return false }
          return fetched.timeIntervalSince1970.rounded(.down)
            > mark.timeIntervalSince1970.rounded(.down)
        }
      }
      if let limit = request.query["limit"].flatMap(Int.init) {
        listed = Array(listed.prefix(limit))
      }
      return (200, Self.entries(listed))
    }
  }

  /// The daemon's own reading of an ISO 8601 query parameter.
  private static func daemonTime(_ text: String) -> Date? {
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    return formatter.date(from: text) ?? ISO8601DateFormatter().date(from: text)
  }

  /// A time as the daemon writes it: Python's `isoformat()`, six fractional digits and `+00:00`.
  static func pythonTime(_ date: Date) -> String {
    let formatter = DateFormatter()
    formatter.locale = Locale(identifier: "en_US_POSIX")
    formatter.timeZone = TimeZone(identifier: "UTC")
    formatter.dateFormat = "yyyy-MM-dd'T'HH:mm:ss.SSSSSS"
    return formatter.string(from: date) + "+00:00"
  }

  /// The daemon's `GET /api/entries` envelope around `items`, with the list view's other fields present so
  /// the client is shown to ignore them. A nil kind, summary or time is written as null.
  static func entries(_ items: [Item]) -> Data {
    let rows: [[String: Any]] = items.map { item in
      [
        "id": item.id, "title": item.title, "source_name": item.sourceName as Any,
        "source_type": "rss", "url": "https://example.com/\(item.id)", "priority": "high",
        "read": false, "summary": item.summary as Any, "kind": item.kind as Any,
        "analysis": ["kind": item.kind as Any, "kind_confidence": 0.9],
        "fetched_at": item.fetchedAt.map(pythonTime) as Any,
        "published_at": item.publishedAt.map(pythonTime) as Any,
      ]
    }
    let envelope: [String: Any] = [
      "success": true, "message": "Retrieved \(items.count) content items",
      "data": ["items": rows, "total": items.count, "query": NSNull()],
    ]
    return (try? JSONSerialization.data(withJSONObject: envelope)) ?? Data()
  }

  // MARK: HTTP

  private func serve(_ conn: NWConnection) {
    conn.start(queue: queue)
    read(conn, Data())
  }

  private func read(_ conn: NWConnection, _ buffer: Data) {
    conn.receive(minimumIncompleteLength: 1, maximumLength: 1 << 20) {
      [self] data, _, done, error in
      var buf = buffer
      if let data { buf.append(data) }
      if let request = parse(buf) {
        lock.withLock { received.append(request) }
        let (status, body) = reply(request)
        let head =
          "HTTP/1.1 \(status) X\r\nContent-Type: application/json\r\nContent-Length: \(body.count)\r\nConnection: close\r\n\r\n"
        conn.send(
          content: Data(head.utf8) + body, completion: .contentProcessed { _ in conn.cancel() })
      } else if error != nil || done {
        conn.cancel()
      } else {
        read(conn, buf)
      }
    }
  }

  /// A GET carries no body, so the request is complete at the blank line.
  private func parse(_ buf: Data) -> Received? {
    guard let split = buf.range(of: Data("\r\n\r\n".utf8)) else { return nil }
    // Lossy on purpose: a test fake reading an HTTP head.
    // swiftlint:disable:next optional_data_string_conversion
    let head = String(decoding: buf[..<split.lowerBound], as: UTF8.self)
    let lines = head.components(separatedBy: "\r\n")
    var headers: [String: String] = [:]
    for l in lines.dropFirst() {
      if let c = l.firstIndex(of: ":") {
        headers[l[..<c].lowercased()] = l[l.index(after: c)...].trimmingCharacters(in: .whitespaces)
      }
    }
    let parts = lines[0].split(separator: " ")
    let method = parts.first.map(String.init) ?? ""
    let target = parts.dropFirst().first.map(String.init) ?? ""
    let components = URLComponents(string: target)
    var query: [String: String] = [:]
    for item in components?.queryItems ?? [] { query[item.name] = item.value ?? "" }
    return Received(
      method: method, path: components?.path ?? target, query: query, headers: headers)
  }
}

/// A port nothing listens on: one a listener was given and then gave back.
func closedPort() async throws -> URL {
  let server = try await FakeServer()
  let url = server.url
  server.stop()
  try await Task.sleep(for: .milliseconds(50))
  return url
}

final class LockedBox<T>: @unchecked Sendable {
  private let lock = NSLock()
  private var v: T
  init(_ v: T) { self.v = v }
  var value: T { lock.withLock { v } }
  func mutate(_ f: (inout T) -> Void) { lock.withLock { f(&v) } }
}

/// The browser's stand-in: records every URL it is asked to open.
final class RecordingOpener: ItemOpener, @unchecked Sendable {
  private let lock = NSLock()
  private var _opened: [URL] = []
  var opened: [URL] { lock.withLock { _opened } }
  func open(_ url: URL) { lock.withLock { _opened.append(url) } }
}

/// The log's stand-in: keeps every line.
final class RecordingLog: @unchecked Sendable {
  private let lock = NSLock()
  private var _lines: [String] = []
  var lines: [String] { lock.withLock { _lines } }
  var sink: @Sendable (String) -> Void { { [self] line in lock.withLock { _lines.append(line) } } }
}

func tempDirectory(_ name: String) throws -> URL {
  let dir = FileManager.default.temporaryDirectory
    .appendingPathComponent("prismis-tests-\(name)-\(UUID().uuidString)")
  try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
  return dir
}

func item(
  _ id: String, _ title: String, source: String? = "Feed", kind: String? = nil,
  summary: String? = nil, fetched: Date? = nil, published: Date? = nil
) -> Item {
  Item(
    id: id, title: title, sourceName: source, kind: kind, summary: summary, fetchedAt: fetched,
    publishedAt: published)
}

/// A UUID-shaped item id, distinct for each `n`.
func itemID(_ n: Int) -> String {
  String(format: "00000000-0000-4000-8000-%012d", n)
}

/// A last-looked file holding `time`, in a new directory.
func lastLookedFile(_ time: Date?) throws -> URL {
  let file = try tempDirectory("last-looked").appendingPathComponent("last-looked.json")
  if let time {
    var store = LastLookedStore(file: file)
    try store.store(time)
  }
  return file
}

/// The last-looked time the file at `file` holds, read by a new store, as a restart reads it.
func storedTime(_ file: URL) -> Date? {
  LastLookedStore(file: file).lastLooked
}

func poller(
  _ server: FakeServer, key: String = "K", store file: URL, log: RecordingLog = RecordingLog()
) -> Poller {
  let config = PrismisConfig(baseURL: server.url, key: key)
  return Poller(
    client: APIClient(config: config), store: LastLookedStore(file: file), log: log.sink)
}

/// Whether `a` and `b` are the same instant to the millisecond, the precision the store keeps.
func sameInstant(_ a: Date?, _ b: Date?) -> Bool {
  guard let a, let b else { return a == nil && b == nil }
  return abs(a.timeIntervalSince(b)) < 0.001
}
