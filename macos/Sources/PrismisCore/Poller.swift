import Foundation

/// What the panel and the icon show after a poll: the newest rows, how many items are new since the
/// operator last looked, and why the latest poll failed, if it did.
public struct PollSnapshot: Sendable, Equatable {
  /// One listed item, and whether it was fetched after the last-looked time.
  public struct Row: Sendable, Equatable, Identifiable {
    public let item: Item
    public let isNew: Bool

    public var id: String { item.id }

    public init(item: Item, isNew: Bool) {
      self.item = item
      self.isNew = isNew
    }
  }

  /// The last good rows, newest first. A failed poll leaves them as they were.
  public var rows: [Row] = []
  /// How many items the daemon listed as fetched after the last-looked time, at most `Poller.newLimit`.
  /// A failed poll leaves it as it was.
  public var newCount = 0
  /// The newest `fetched_at` among the rows and the new items: what opening the panel stores.
  public var newestFetched: Date?
  /// Set when the latest poll failed; nil after a poll that got both lists.
  public var failure: PollFailure?
  /// When the latest poll ran, on the Mac's clock, whether it succeeded or not.
  public var checkedAt: Date?

  public init() {}
}

/// One poll: the newest rows and the items fetched since the operator last looked, two requests to the
/// daemon. The first poll with no last-looked time stores the newest `fetched_at` it saw and counts
/// nothing new, so a fresh install starts quiet. A failed poll keeps the last rows and count. Every
/// decision goes to `log`.
public actor Poller {
  /// The rows the panel lists.
  public static let rowLimit = 5
  /// The most new items one poll counts; the panel shows more as 99+.
  public static let newLimit = 100

  private let client: APIClient
  private var store: LastLookedStore
  private let log: @Sendable (String) -> Void
  public private(set) var snapshot = PollSnapshot()

  public init(
    client: APIClient, store: LastLookedStore, log: @escaping @Sendable (String) -> Void
  ) {
    self.client = client
    self.store = store
    self.log = log
  }

  @discardableResult
  public func poll(now: Date = Date()) async -> PollSnapshot {
    snapshot.checkedAt = now
    let lastLooked = store.lastLooked
    let rows: [Item]
    var fresh: [Item] = []
    do {
      rows = try await client.highUnread(limit: Self.rowLimit)
      if let lastLooked {
        fresh = try await client.highUnread(since: lastLooked, limit: Self.newLimit)
      }
    } catch {
      snapshot.failure = error
      log(
        "poll failed: \(error.description); keeping the last \(snapshot.rows.count) rows and "
          + "\(snapshot.newCount) new")
      return snapshot
    }
    let newest = (rows + fresh).compactMap(\.fetchedAt).max()
    snapshot.failure = nil
    snapshot.newestFetched = newest

    guard let lastLooked else {
      snapshot.rows = rows.map { PollSnapshot.Row(item: $0, isNew: false) }
      snapshot.newCount = 0
      seed(newest, listed: rows.count)
      return snapshot
    }
    snapshot.rows = rows.map { item in
      PollSnapshot.Row(
        item: item, isNew: item.fetchedAt.map { DaemonTime.isAfter($0, lastLooked) } ?? false)
    }
    snapshot.newCount = fresh.count
    log(
      "polled \(rows.count) rows, \(fresh.count) new since \(DaemonTime.format(lastLooked))"
        + "\(fresh.count >= Self.newLimit ? " (the most one poll counts)" : "")")
    return snapshot
  }

  /// No last look yet: the newest fetch time listed becomes it, so nothing listed now counts as new.
  private func seed(_ newest: Date?, listed: Int) {
    guard let newest else {
      log("no last-looked time, and no fetch time in \(listed) rows; starting quiet")
      return
    }
    do {
      try store.store(newest)
      log("no last-looked time; starting quiet from \(DaemonTime.format(newest))")
    } catch {
      log("could not store the last-looked time: \(error); starting quiet from it in memory")
    }
  }

  /// The panel-open action, run once the panel shows `shown`: stores the newest `fetched_at` among its
  /// rows and new items, so the next poll counts only what was fetched after this look. A time no later
  /// than the stored one leaves the store as it is.
  public func panelOpened(showing shown: PollSnapshot) {
    guard let newest = shown.newestFetched else { return }
    if let current = store.lastLooked, newest <= current { return }
    do {
      try store.store(newest)
      log("looked at the panel; last looked is now \(DaemonTime.format(newest))")
    } catch {
      log("could not store last looked \(DaemonTime.format(newest)): \(error); held in memory")
    }
  }
}
