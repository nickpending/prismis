import AppKit
import Foundation
import Observation
import PrismisCore
import UserNotifications
import os

/// Unified log (`log show --predicate 'subsystem == "info.voidwire.Prismis"'`): every unattended decision, at
/// .notice or above, since macOS does not persist .info or .debug. Never log the key.
private let log = Logger(subsystem: "info.voidwire.Prismis", category: "app")

/// The default browser behind PrismisCore's `ItemOpener`.
struct BrowserOpener: ItemOpener {
  func open(_ url: URL) {
    Task { @MainActor in NSWorkspace.shared.open(url) }
  }
}

/// What the menu bar shows, and the loop that feeds it: a poll at launch, every five minutes after, and on
/// demand from the menu.
@MainActor
@Observable
final class StatusModel {
  private static let interval: Duration = .seconds(300)

  /// The last good list, newest first.
  private(set) var items: [Item] = []
  /// Why the latest poll failed, shown as the daemon being unreachable.
  private(set) var failure: String?
  /// Why the config could not be used; nothing is polled until it can.
  private(set) var configProblem: String?

  /// Whether the app is a login item, as the system reports it; only the operator's toggle changes it.
  private(set) var launchAtLogin = false
  private(set) var loginNote: String?
  private let loginItem = LaunchAtLogin(service: LoginItem())

  private var poller: Poller?
  private var opening: ItemOpening?
  private var clicks: NotificationClicks?
  private var started = false

  var countLabel: String { configProblem == nil ? "\(items.count)" : "!" }

  var symbol: String {
    if configProblem != nil || failure != nil { return "exclamationmark.triangle" }
    return items.isEmpty ? "tray" : "tray.full"
  }

  func start() {
    guard !started else { return }
    started = true
    refreshLoginItem()
    configure()
    Task { await loop() }
  }

  /// Poll now, from the menu. A config that failed to load is read again first.
  func refresh() {
    if poller == nil { configure() }
    Task { await pollOnce() }
  }

  func open(_ item: Item) { opening?.openItem(item) }

  // MARK: Launch at login

  /// The operator's toggle: register or unregister, then show what the system says.
  func setLaunchAtLogin(_ on: Bool) {
    let failure = loginItem.set(on)
    log.notice("launch at login \(on ? "on" : "off", privacy: .public)")
    refreshLoginItem()
    if let failure { loginNote = failure }
  }

  private func refreshLoginItem() {
    let state = loginItem.state
    launchAtLogin = state.isOn
    loginNote = state.note
  }

  // MARK: Polling

  private func configure() {
    let config: PrismisConfig
    do {
      config = try PrismisConfig.load()
    } catch {
      configProblem = String(describing: error)
      log.error("config refused: \(String(describing: error), privacy: .public)")
      return
    }
    configProblem = nil
    let opening = ItemOpening(base: config.baseURL, opener: BrowserOpener())
    self.opening = opening
    let clicks = NotificationClicks(opening: opening)
    self.clicks = clicks
    UNUserNotificationCenter.current().delegate = clicks
    poller = Poller(
      client: APIClient(config: config), store: AlertedStore(), notifier: Notifier(),
      log: { log.notice("\($0, privacy: .public)") })
    log.notice("polling \(config.baseURL.absoluteString, privacy: .public)")
  }

  private func loop() async {
    while !Task.isCancelled {
      await pollOnce()
      try? await Task.sleep(for: Self.interval)
    }
  }

  private func pollOnce() async {
    guard let poller else { return }
    let snapshot = await poller.poll()
    items = snapshot.items
    failure = snapshot.failure?.description
  }
}
