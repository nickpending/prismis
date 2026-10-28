import Foundation

/// The web view's links: `<daemon url>/`, the web view itself, and `<daemon url>/#item=<id>`, which opens
/// that item's detail panel.
public enum ItemLink {
  /// Nil only for an id that cannot be put in a URL fragment.
  public static func url(base: URL, id: String) -> URL? {
    guard let fragment = id.addingPercentEncoding(withAllowedCharacters: .urlFragmentAllowed) else {
      return nil
    }
    return URL(string: "\(root(base))/#item=\(fragment)")
  }

  public static func webView(base: URL) -> URL? {
    URL(string: "\(root(base))/")
  }

  /// The base without its trailing slashes, so either spelling of it gives one link.
  private static func root(_ base: URL) -> String {
    var root = base.absoluteString
    while root.hasSuffix("/") { root.removeLast() }
    return root
  }
}

/// The panel's two ways into the web view, both through one opener: a row opens its item, and the footer's
/// Open prismis opens the web view itself.
public struct ItemOpening: Sendable {
  private let base: URL
  private let opener: ItemOpener

  public init(base: URL, opener: ItemOpener) {
    self.base = base
    self.opener = opener
  }

  /// A row's action for `item`.
  public func openItem(_ item: Item) {
    if let url = ItemLink.url(base: base, id: item.id) { opener.open(url) }
  }

  /// The footer's Open prismis: the daemon's web view, at its root.
  public func openPrismis() {
    if let url = ItemLink.webView(base: base) { opener.open(url) }
  }
}
