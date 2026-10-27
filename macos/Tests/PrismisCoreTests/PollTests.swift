import Foundation
import Testing

@testable import PrismisCore

/// The ids the store file at `file` holds, read from the file itself.
func storedIDs(_ file: URL) throws -> Set<String> {
  let data = try Data(contentsOf: file)
  let json = try #require(try JSONSerialization.jsonObject(with: data) as? [String: Any])
  let alerted = try #require(json["alerted"] as? [String: Any])
  return Set(alerted.keys)
}

func poller(
  _ server: FakeServer, key: String = "K", store file: URL, notifier: ItemNotifier,
  log: RecordingLog = RecordingLog()
) -> Poller {
  let config = PrismisConfig(baseURL: server.url, key: key)
  return Poller(
    client: APIClient(config: config), store: AlertedStore(file: file), notifier: notifier,
    log: log.sink)
}

@Suite struct PollTests {
  let x = item("5d937428-3c20-4b87-b342-c7f7a7f615cd", "X", source: "Feed A")
  let y = item("0b6e2a8c-7f43-4d36-9c4e-2f1a8d9e6b10", "Y", source: nil)
  let z = item("c1d2e3f4-a5b6-4c7d-8e9f-0a1b2c3d4e5f", "Z", source: "Feed C")

  // MARK: C2 — one request, the high unread list view, the key in X-API-Key

  @Test func onePollMakesOneListRequestWithTheKeyAndReturnsTheItems() async throws {
    let server = try await FakeServer()
    defer { server.stop() }
    server.list([x, y])
    let dir = try tempDirectory("poll-c2")
    let p = poller(
      server, key: "K", store: dir.appendingPathComponent("alerted.json"),
      notifier: RecordingNotifier())

    let snapshot = await p.poll()

    #expect(server.requests.count == 1)
    let request = try #require(server.requests.first)
    #expect(request.method == "GET")
    #expect(request.path == "/api/entries")
    #expect(
      request.query == [
        "priority": "high", "unread_only": "true", "view": "list", "sort_by": "date",
      ])
    #expect(request.headers["x-api-key"] == "K")
    #expect(snapshot.failure == nil)
    #expect(snapshot.items == [x, y])
    #expect(snapshot.items.map(\.sourceName) == ["Feed A", nil])
  }

  // MARK: C3 — no alert on the first poll, one per new item, none again after a restart

  @Test func alertsOncePerNewItemAcrossPollsAndRestarts() async throws {
    let server = try await FakeServer()
    defer { server.stop() }
    let dir = try tempDirectory("poll-c3")
    let file = dir.appendingPathComponent("alerted.json")
    #expect(!FileManager.default.fileExists(atPath: file.path))
    let notifier = RecordingNotifier()
    let first = poller(server, store: file, notifier: notifier)

    server.list([x, y])
    await first.poll()
    #expect(notifier.posted.isEmpty, "the first poll posted \(notifier.posted.map(\.title))")
    // try?: a missing file fails this expectation and the test goes on to the restart.
    #expect((try? storedIDs(file)) == [x.id, y.id])

    server.list([x, y, z])
    await first.poll()
    #expect(notifier.posted == [ItemNotice(item: z)])

    // A restart: a new store opened on the same file.
    let restarted = RecordingNotifier()
    let second = poller(server, store: file, notifier: restarted)
    await second.poll()
    #expect(restarted.posted.isEmpty, "after a restart it posted \(restarted.posted.map(\.title))")
    #expect(notifier.posted.count == 1)
  }
}
