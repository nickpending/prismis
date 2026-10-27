import Foundation

/// What the menu shows after a poll: the last list a poll got, and why the latest poll failed, if it did.
public struct PollSnapshot: Sendable, Equatable {
  /// The last good list, newest first. A failed poll leaves it as it was.
  public var items: [Item] = []
  /// Set when the latest poll failed; nil after a poll that got a list.
  public var failure: PollFailure?
  public var lastSuccess: Date?

  public init() {}
}

/// One poll: fetch the list, alert each listed item the store does not hold yet, and record it. The first
/// poll against a store with no file only seeds it, so installing does not flood. A failed poll alerts
/// nothing and keeps the last list. Every decision goes to `log`.
public actor Poller {
  private let client: APIClient
  private var store: AlertedStore
  private let notifier: ItemNotifier
  private let log: @Sendable (String) -> Void
  public private(set) var snapshot = PollSnapshot()

  public init(
    client: APIClient, store: AlertedStore, notifier: ItemNotifier,
    log: @escaping @Sendable (String) -> Void
  ) {
    self.client = client
    self.store = store
    self.notifier = notifier
    self.log = log
  }

  @discardableResult
  public func poll(now: Date = Date()) async -> PollSnapshot {
    let items: [Item]
    do {
      items = try await client.highUnread()
    } catch {
      snapshot.failure = error
      log("poll failed: \(error.description); keeping the last \(snapshot.items.count) items")
      return snapshot
    }
    snapshot.items = items
    snapshot.failure = nil
    snapshot.lastSuccess = now

    var ids: [String] = []
    for item in items where !ids.contains(item.id) { ids.append(item.id) }

    if !store.seeded {
      do {
        try store.record(ids, now: now)
        log("seeded the alerted store with \(ids.count) items; alerting none")
      } catch {
        log("could not seed the alerted store: \(error); alerting none")
      }
      return snapshot
    }

    let fresh = items.filter { !store.contains($0.id) }
    do {
      try store.record(fresh.map(\.id), now: now)
    } catch {
      log("could not write the alerted store: \(error)")
    }
    var posted = Set<String>()
    for item in fresh where posted.insert(item.id).inserted {
      do {
        try await notifier.post(ItemNotice(item: item))
        log("alerted \(item.id)")
      } catch {
        log("could not post an alert for \(item.id): \(error)")
      }
    }
    log("polled \(items.count) items, \(posted.count) new")
    return snapshot
  }
}
