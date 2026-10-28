import Foundation
import Testing

@testable import PrismisCore

/// A time on whole seconds, so it survives the daemon's text and the store's untouched.
func wholeSecond(_ date: Date) -> Date {
  Date(timeIntervalSince1970: date.timeIntervalSince1970.rounded(.down))
}

/// C3's daemon, whose clock runs an hour ahead of the Mac's. The operator last looked at `t`. Of the newest
/// five, in the daemon's published-first order, `a` and `b` were fetched after `t` and the other three
/// before; `a` and `b` were published long before `t`, so only their fetch times make them new. `b` holds
/// the newest fetch time.
struct LookFixture {
  let t: Date
  let a: Item
  let b: Item
  let rows: [Item]

  init(macNow: Date = Date()) {
    let t = wholeSecond(macNow).addingTimeInterval(3600 - 1800)
    self.t = t
    a = item(
      itemID(1), "A", kind: "vulnerability", fetched: t.addingTimeInterval(600),
      published: t.addingTimeInterval(-7200))
    b = item(
      itemID(2), "B", fetched: t.addingTimeInterval(1200), published: t.addingTimeInterval(-3600))
    let c = item(
      itemID(3), "C", fetched: t.addingTimeInterval(-600), published: t.addingTimeInterval(-650))
    let d = item(
      itemID(4), "D", fetched: t.addingTimeInterval(-1200), published: t.addingTimeInterval(-1250))
    let e = item(
      itemID(5), "E", fetched: t.addingTimeInterval(-1800), published: t.addingTimeInterval(-5000))
    rows = [c, d, b, e, a]
  }
}

@Suite struct PollTests {
  static let filters = [
    "priority": "high", "unread_only": "true", "view": "list", "sort_by": "date",
  ]

  // MARK: C2 — a fresh install's first poll starts quiet

  @Test func theFirstPollWithNoFileStartsQuietAndStoresTheNewestFetchTime() async throws {
    let server = try await FakeServer()
    defer { server.stop() }
    let f = wholeSecond(Date())
    // The newest fetch time is the third row's: the rows run newest published first.
    let offsets: [TimeInterval] = [-1200, -1800, 0, -600, -2400]
    let five = offsets.enumerated().map { n, offset in
      item(
        itemID(n + 1), "Item \(n + 1)", fetched: f.addingTimeInterval(offset),
        published: f.addingTimeInterval(-3600 - Double(n) * 60))
    }
    server.list(five)
    let file = try lastLookedFile(nil)

    let snapshot = await poller(server, store: file).poll()

    #expect(snapshot.failure == nil)
    #expect(snapshot.rows.map(\.item) == five)
    #expect(
      snapshot.rows.allSatisfy { !$0.isNew }, "rows marked new: \(snapshot.rows.filter(\.isNew))")
    #expect(snapshot.newCount == 0)
    #expect(IconState(snapshot: snapshot) == .quiet)
    #expect(
      sameInstant(storedTime(file), f), "the file holds \(String(describing: storedTime(file)))")
    // With no last look there is nothing to ask `since` about.
    #expect(server.requests.map { $0.query["since"] } == [nil])
  }

  @Test func aFirstPollThatListsNothingLeavesTheFileAbsent() async throws {
    let server = try await FakeServer()
    defer { server.stop() }
    server.list([])
    let file = try lastLookedFile(nil)

    let snapshot = await poller(server, store: file).poll()

    #expect(snapshot.failure == nil)
    #expect(snapshot.checkedAt != nil)
    #expect(IconState(snapshot: snapshot) == .quiet)
    #expect(!FileManager.default.fileExists(atPath: file.path))
  }

  // MARK: C3 — the new count and the new rows come from fetch times after the last look

  @Test func aPollCountsTheItemsFetchedAfterTheLastLookAndMarksExactlyThoseRows() async throws {
    let fixture = LookFixture()
    let server = try await FakeServer()
    defer { server.stop() }
    server.list(fixture.rows)
    let file = try lastLookedFile(fixture.t)

    let snapshot = await poller(server, key: "K", store: file).poll()

    #expect(server.requests.count == 2)
    for request in server.requests {
      #expect(request.method == "GET")
      #expect(request.path == "/api/entries")
      #expect(request.headers["x-api-key"] == "K")
    }
    let newest = server.requests.filter { $0.query["since"] == nil }
    let since = server.requests.filter { $0.query["since"] != nil }
    #expect(newest.map(\.query) == [Self.filters.merging(["limit": "5"]) { $1 }])
    #expect(since.count == 1)
    var sinceQuery = try #require(since.first).query
    let mark = try #require(sinceQuery["since"])
    sinceQuery["since"] = nil
    #expect(sinceQuery == Self.filters.merging(["limit": "100"]) { $1 })
    #expect(sameInstant(DaemonTime.parse(mark), fixture.t), "since=\(mark)")

    #expect(snapshot.failure == nil)
    #expect(snapshot.newCount == 2)
    #expect(snapshot.rows.map(\.item) == fixture.rows)
    #expect(snapshot.rows.filter(\.isNew).map(\.id) == [fixture.b.id, fixture.a.id])
    #expect(IconState(snapshot: snapshot) == .new)
    // A poll never moves the last look; only opening the panel does.
    #expect(sameInstant(storedTime(file), fixture.t))
  }

  /// The daemon compares fetch times to `since` to the whole second, so a row fetched later in the second
  /// of the last look is neither counted nor marked.
  @Test func aRowFetchedInTheSameSecondAsTheLastLookIsNotNew() async throws {
    let server = try await FakeServer()
    defer { server.stop() }
    let t = wholeSecond(Date())
    let same = item(itemID(1), "Same second", fetched: t.addingTimeInterval(0.5))
    let next = item(itemID(2), "Next second", fetched: t.addingTimeInterval(1))
    server.list([same, next])
    let file = try lastLookedFile(t)

    let snapshot = await poller(server, store: file).poll()

    #expect(snapshot.newCount == 1)
    #expect(snapshot.rows.map(\.isNew) == [false, true])
  }
}
