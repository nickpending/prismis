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

  /// Answers 200 with `items` as the daemon's list view.
  func list(_ items: [Item]) {
    let body = Self.entries(items)
    reply = { _ in (200, body) }
  }

  /// The daemon's `GET /api/entries` envelope around `items`, with the list view's other fields present so
  /// the client is shown to ignore them.
  static func entries(_ items: [Item]) -> Data {
    let rows: [[String: Any]] = items.map { item in
      [
        "id": item.id, "title": item.title, "source_name": item.sourceName as Any,
        "source_type": "rss", "url": "https://example.com/\(item.id)", "priority": "high",
        "read": false, "summary": "s", "analysis": ["kind": "analysis"],
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

/// The notification centre's stand-in: records every notice it is handed.
final class RecordingNotifier: ItemNotifier, @unchecked Sendable {
  private let lock = NSLock()
  private var _posted: [ItemNotice] = []
  var posted: [ItemNotice] { lock.withLock { _posted } }
  func post(_ notice: ItemNotice) async throws { lock.withLock { _posted.append(notice) } }
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

func item(_ id: String, _ title: String, source: String? = "Feed") -> Item {
  Item(id: id, title: title, sourceName: source)
}
