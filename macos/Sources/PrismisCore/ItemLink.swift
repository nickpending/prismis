import Foundation

/// The web view's link to one item: `<daemon url>/#item=<id>`, which opens that item's detail panel.
public enum ItemLink {
  /// Nil only for an id that cannot be put in a URL fragment.
  public static func url(base: URL, id: String) -> URL? {
    guard let fragment = id.addingPercentEncoding(withAllowedCharacters: .urlFragmentAllowed) else {
      return nil
    }
    var root = base.absoluteString
    while root.hasSuffix("/") { root.removeLast() }
    return URL(string: "\(root)/#item=\(fragment)")
  }
}

/// The two ways the app opens an item, the dropdown and a click on its notification, both through one opener
/// and one link.
public struct ItemOpening: Sendable {
  /// The `userInfo` key an item's notification carries its id under.
  public static let itemIDKey = "prismis.item.id"

  private let base: URL
  private let opener: ItemOpener

  public init(base: URL, opener: ItemOpener) {
    self.base = base
    self.opener = opener
  }

  /// The dropdown's action for `item`.
  public func openItem(_ item: Item) {
    if let url = ItemLink.url(base: base, id: item.id) { opener.open(url) }
  }

  /// The notification-click handler: opens the item the clicked notification names. False when it names none.
  @discardableResult
  public func openNotification(_ userInfo: [AnyHashable: Any]) -> Bool {
    guard let id = userInfo[Self.itemIDKey] as? String, let url = ItemLink.url(base: base, id: id)
    else { return false }
    opener.open(url)
    return true
  }
}
