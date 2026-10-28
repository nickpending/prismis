import Foundation
import Testing

@testable import PrismisCore

/// C5: a failed poll says why, keeps the last good rows and new count, dims the icon, and logs its reason.
@Suite struct FailedPollTests {
  enum Fault: CaseIterable { case serverError, refusedKey, closedPort }

  @Test func eachFailedPollKeepsTheRowsAndCountAndSaysWhy() async throws {
    let fixture = LookFixture()
    let server = try await FakeServer()
    defer { server.stop() }
    server.list(fixture.rows)
    let log = RecordingLog()
    let p = poller(server, store: try lastLookedFile(fixture.t), log: log)
    let good = await p.poll()
    #expect(good.failure == nil)
    #expect(good.rows.count == 5)
    #expect(good.newCount == 2)

    var reasons: [String] = []
    for fault in Fault.allCases {
      switch fault {
      case .serverError: server.answer(500)
      case .refusedKey: server.answer(403)
      case .closedPort:
        server.stop()
        try await Task.sleep(for: .milliseconds(50))
      }
      let failed = await p.poll()
      let failure = try #require(failed.failure, "the poll after \(fault) did not fail")

      switch (fault, failure) {
      case (.serverError, .serverError(500)), (.refusedKey, .refusedKey(403)),
        (.closedPort, .unreachable):
        break
      default:
        Issue.record("\(fault) ended as \(failure)")
      }
      #expect(failed.rows == good.rows)
      #expect(failed.newCount == good.newCount)
      #expect(failed.newestFetched == good.newestFetched)
      #expect(IconState(snapshot: failed) == .failed)
      #expect(
        log.lines.contains { $0.contains(failure.description) },
        "no log line carries \"\(failure.description)\": \(log.lines)")
      reasons.append(failure.description)
    }
    #expect(Set(reasons).count == 3, "\(reasons)")
    #expect(reasons[1].contains("key"))
    #expect(reasons[2].contains("unreachable"))
  }

  /// The two requests are one check: a since listing that fails fails the poll, and the newest rows it did
  /// get are not shown with a count from before.
  @Test func aFailedSinceListingFailsThePollAndKeepsTheLastGoodRows() async throws {
    let fixture = LookFixture()
    let server = try await FakeServer()
    defer { server.stop() }
    server.list(fixture.rows)
    let p = poller(server, store: try lastLookedFile(fixture.t))
    let good = await p.poll()
    #expect(good.newCount == 2)

    let fresh = item(itemID(7), "Listed only after the first poll", fetched: fixture.t)
    let rows = FakeServer.entries([fresh])
    server.reply = { request in request.query["since"] == nil ? (200, rows) : (500, Data()) }
    let failed = await p.poll()

    #expect(failed.failure == .serverError(500))
    #expect(failed.rows == good.rows)
    #expect(failed.newCount == 2)
    #expect(IconState(snapshot: failed) == .failed)
  }
}
