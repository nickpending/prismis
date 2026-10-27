import Foundation
import Testing

@testable import PrismisCore

/// C4: a failed poll says why, keeps the last good list, posts nothing, and logs its reason.
@Suite struct FailedPollTests {
  let x = item("5d937428-3c20-4b87-b342-c7f7a7f615cd", "X")
  let y = item("0b6e2a8c-7f43-4d36-9c4e-2f1a8d9e6b10", "Y")

  enum Fault: CaseIterable { case serverError, refusedKey, closedPort }

  /// A poller that has already polled X and Y successfully, then `fault` broke the daemon: the failed
  /// poll's snapshot, the notices posted by the failed poll alone, and the log.
  private func failedPoll(_ fault: Fault) async throws -> (
    PollSnapshot, [ItemNotice], RecordingLog
  ) {
    let server = try await FakeServer()
    defer { server.stop() }
    let dir = try tempDirectory("failed-poll")
    let notifier = RecordingNotifier()
    let log = RecordingLog()
    let p = poller(
      server, store: dir.appendingPathComponent("alerted.json"), notifier: notifier, log: log)
    server.list([x, y])
    let good = await p.poll()
    #expect(good.failure == nil)
    #expect(good.items == [x, y])
    let postedBefore = notifier.posted.count

    switch fault {
    case .serverError: server.answer(500)
    case .refusedKey: server.answer(403)
    case .closedPort:
      server.stop()
      try await Task.sleep(for: .milliseconds(50))
    }
    let failed = await p.poll()
    return (failed, Array(notifier.posted.dropFirst(postedBefore)), log)
  }

  @Test(arguments: Fault.allCases) func aFailedPollKeepsTheListAndPostsNothing(_ fault: Fault)
    async throws
  {
    let (snapshot, posted, log) = try await failedPoll(fault)
    let failure = try #require(snapshot.failure, "the poll after \(fault) did not fail")

    switch (fault, failure) {
    case (.serverError, .serverError(500)), (.refusedKey, .refusedKey(403)),
      (.closedPort, .unreachable):
      break
    default:
      Issue.record("\(fault) ended as \(failure)")
    }
    #expect(snapshot.items == [x, y])
    #expect(posted.isEmpty, "the failed poll posted \(posted.map(\.title))")
    #expect(
      log.lines.contains { $0.contains(failure.description) },
      "no log line carries \"\(failure.description)\": \(log.lines)")
  }

  @Test func theThreeReasonsReadDifferently() async throws {
    var reasons: [String] = []
    for fault in Fault.allCases {
      let (snapshot, _, _) = try await failedPoll(fault)
      reasons.append(try #require(snapshot.failure).description)
    }
    #expect(Set(reasons).count == 3, "\(reasons)")
    #expect(reasons[1].contains("key"))
    #expect(reasons[2].contains("unreachable"))
  }
}
