import Foundation

/// The operating system's notification centre. The app target adapts UserNotifications to it; tests
/// record what it is handed.
public protocol ItemNotifier: Sendable {
  func post(_ notice: ItemNotice) async throws
}

/// Whatever opens a URL in the default browser. The app target adapts NSWorkspace to it; tests record
/// the URL.
public protocol ItemOpener: Sendable {
  func open(_ url: URL)
}

/// One notification for one item: its title, its source, and what a click on it hands back.
public struct ItemNotice: Sendable, Equatable {
  /// One identifier per item, so a repeat would replace the banner instead of stacking.
  public let identifier: String
  public let title: String
  public let body: String
  /// What the notification carries back to `ItemOpening.openNotification(_:)` when it is clicked.
  public let userInfo: [String: String]

  public init(item: Item) {
    identifier = "prismis.item.\(item.id)"
    title = item.title
    body = item.sourceName ?? ""
    userInfo = [ItemOpening.itemIDKey: item.id]
  }
}
