import Foundation
import Testing

@testable import PrismisCore

@Suite struct PanelPresentationTests {
  /// The ten kinds the daemon's classifier names.
  static let daemonKinds = [
    "release", "experience", "question", "analysis", "news", "incident", "research",
    "vulnerability",
    "humor", "tutorial",
  ]

  // MARK: C6 — the header's count, 99+ past 99

  @Test(arguments: [(100, "99+"), (7, "7")])
  func theHeaderCountIsRenderedFromThePoll(_ listed: Int, _ label: String) async throws {
    let t = wholeSecond(Date())
    let fresh = (1...listed).map { n in
      item(itemID(n), "Item \(n)", fetched: t.addingTimeInterval(Double(n)))
    }
    let server = try await FakeServer()
    defer { server.stop() }
    server.list(fresh)

    let snapshot = await poller(server, store: try lastLookedFile(t)).poll()

    #expect(snapshot.newCount == listed)
    #expect(PanelPresentation.countLabel(snapshot.newCount) == label)
  }

  @Test func theCountIsTheNumberThroughNinetyNine() {
    #expect(PanelPresentation.countLabel(0) == "0")
    #expect(PanelPresentation.countLabel(99) == "99")
    #expect(PanelPresentation.countLabel(101) == "99+")
  }

  // MARK: C7 — tolerant items and their kind chips

  /// The list view as the daemon writes it, with a known kind, an unknown kind, no kind and no summary.
  static let listView = """
    {"success": true, "message": "Retrieved 4 content items", "data": {"total": 4, "query": null,
     "items": [
      {"id": "00000000-0000-4000-8000-000000000001", "title": "Known", "source_name": "r/netsec",
       "kind": "vulnerability", "analysis": {"kind": "vulnerability", "kind_confidence": 0.93},
       "summary": "A summary.", "fetched_at": "2026-10-27T10:00:00.123456+00:00",
       "published_at": "2026-10-27T09:00:00+00:00", "read": false, "priority": "high"},
      {"id": "00000000-0000-4000-8000-000000000002", "title": "Unknown", "source_name": "Feed",
       "kind": "zeitgeist", "analysis": {"kind": "zeitgeist"}, "summary": "Another.",
       "fetched_at": "2026-10-27T10:00:01+00:00", "published_at": null},
      {"id": "00000000-0000-4000-8000-000000000003", "title": "No kind", "source_name": null,
       "kind": null, "analysis": {}, "summary": "Third."},
      {"id": "00000000-0000-4000-8000-000000000004", "title": "No summary", "source_name": "Feed",
       "analysis": {"metrics": {}}, "fetched_at": "2026-10-27T10:00:02+00:00"}
     ]}}
    """

  @Test func everyItemDecodesAndEachKindGetsItsChip() async throws {
    let server = try await FakeServer()
    defer { server.stop() }
    let body = Data(Self.listView.utf8)
    server.reply = { _ in (200, body) }

    let snapshot = await poller(server, store: try lastLookedFile(nil)).poll()

    #expect(snapshot.failure == nil, "\(String(describing: snapshot.failure))")
    let items = snapshot.rows.map(\.item)
    #expect(items.map(\.title) == ["Known", "Unknown", "No kind", "No summary"])
    #expect(items.map(\.kind) == ["vulnerability", "zeitgeist", nil, nil])
    #expect(items.map(\.summary) == ["A summary.", "Another.", "Third.", nil])
    #expect(items.map(\.sourceName) == ["r/netsec", "Feed", nil, "Feed"])
    // Six fractional digits are read, to the millisecond.
    #expect(sameInstant(items[0].fetchedAt, Date(timeIntervalSince1970: 1_793_095_200.123)))
    #expect(sameInstant(items[0].publishedAt, Date(timeIntervalSince1970: 1_793_091_600)))
    #expect(items[1].publishedAt == nil)
    #expect(items[2].fetchedAt == nil)

    let chips = items.map { PanelPresentation.chip(for: $0.kind) }
    #expect(
      chips[0] == KindChip(label: "vulnerability", style: .tint(text: 0xFF6FA3, base: 0xFF0066)))
    #expect(chips[1] == KindChip(label: "zeitgeist", style: .neutral))
    #expect(chips[2] == nil)
    #expect(chips[3] == nil)
  }

  @Test func eachKindTheDaemonNamesHasItsOwnTint() {
    let styles = Self.daemonKinds.map { PanelPresentation.chip(for: $0)?.style }
    for (kind, style) in zip(Self.daemonKinds, styles) {
      guard case .tint = style else {
        Issue.record("\(kind) gets \(String(describing: style)), not its own tint")
        continue
      }
    }
    let distinct = Set(styles.map { String(describing: $0) })
    #expect(distinct.count == Self.daemonKinds.count, "\(styles)")
  }

  @Test func anEmptyKindGetsNoChip() {
    #expect(PanelPresentation.chip(for: "") == nil)
  }

  // MARK: Ages

  @Test func agesReadInTheLargestWholeUnit() {
    let now = Date(timeIntervalSince1970: 1_793_095_200)
    let cases: [(TimeInterval, String)] = [
      (0, "now"), (59, "now"), (60, "1m"), (4 * 60 + 59, "4m"), (3600, "1h"),
      (2 * 3600 + 1800, "2h"),
      (86400, "1d"), (3 * 86400 + 7200, "3d"),
      // A daemon clock ahead of the Mac's gives a time in the future: it reads as now.
      (-3600, "now"),
    ]
    for (back, label) in cases {
      #expect(PanelPresentation.ago(now.addingTimeInterval(-back), now: now) == label, "\(back)s")
    }
    #expect(
      PanelPresentation.checkedLabel(now.addingTimeInterval(-30), now: now) == "checked just now")
    #expect(
      PanelPresentation.checkedLabel(now.addingTimeInterval(-240), now: now) == "checked 4m ago")
  }

  @Test func aRowsAgeIsFromItsPublishedTimeElseItsFetchedTime() {
    let now = Date(timeIntervalSince1970: 1_793_095_200)
    let both = item(
      itemID(1), "Both", fetched: now.addingTimeInterval(-240),
      published: now.addingTimeInterval(-7200))
    let fetchedOnly = item(itemID(2), "Fetched", fetched: now.addingTimeInterval(-240))
    #expect(PanelPresentation.age(of: both, now: now) == "2h")
    #expect(PanelPresentation.age(of: fetchedOnly, now: now) == "4m")
    #expect(PanelPresentation.age(of: item(itemID(3), "Neither"), now: now) == nil)
  }

  // MARK: The icon

  @Test func aRefusedConfigDimsTheIconWhateverTheCount() {
    var snapshot = PollSnapshot()
    snapshot.newCount = 3
    #expect(IconState(snapshot: snapshot) == .new)
    #expect(IconState(snapshot: snapshot, configRefused: true) == .failed)
    #expect(IconState(snapshot: PollSnapshot(), configRefused: true) == .failed)
  }
}
