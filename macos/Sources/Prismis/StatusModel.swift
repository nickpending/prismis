import AppKit
import Foundation
import Observation
import PrismisCore
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

/// What the icon and the panel show, and the loop that feeds them: a poll at launch, every five minutes
/// after, and on demand from the panel.
@MainActor
@Observable
final class StatusModel {
  private static let interval: Duration = .seconds(300)

  /// The latest poll's rows, new count and failure.
  private(set) var snapshot = PollSnapshot()
  /// Why the config could not be used; nothing is polled until it can.
  private(set) var configProblem: String?

  /// Whether the app is a login item, as the system reports it; only the operator's toggle changes it.
  private(set) var launchAtLogin = false
  private(set) var loginNote: String?
  private let loginItem = LaunchAtLogin(service: LoginItem())

  /// The row the pointer is over, whose summary the panel reveals. It lives here because the Command Line
  /// Tools toolchain has no SwiftUI macro plugin, so a view cannot hold `@State`.
  private(set) var hoveredRow: String?

  private var poller: Poller?
  private var opening: ItemOpening?
  private var started = false

  var icon: IconState { IconState(snapshot: snapshot, configRefused: configProblem != nil) }

  func start() {
    guard !started else { return }
    started = true
    refreshLoginItem()
    configure()
    Task { await loop() }
  }

  /// Poll now, from the panel. A config that failed to load is read again first.
  func refresh() {
    if poller == nil { configure() }
    Task { await pollOnce() }
  }

  func open(_ item: Item) { opening?.openItem(item) }

  func openPrismis() { opening?.openPrismis() }

  /// The pointer entered or left the row `id`. Leaving one row never clears the row it entered next.
  func hover(_ id: String, inside: Bool) {
    if inside {
      hoveredRow = id
    } else if hoveredRow == id {
      hoveredRow = nil
    }
  }

  /// The panel is showing `snapshot`: its rows are on screen with their dots, so the last-looked time can
  /// move to the newest of them for the next poll.
  func panelShown() {
    guard let poller else { return }
    let shown = snapshot
    Task { await poller.panelOpened(showing: shown) }
  }

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
    opening = ItemOpening(base: config.baseURL, opener: BrowserOpener())
    poller = Poller(
      client: APIClient(config: config), store: LastLookedStore(),
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
    snapshot = await poller.poll()
  }
}
