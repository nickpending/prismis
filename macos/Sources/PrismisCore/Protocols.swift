import Foundation

/// Whatever opens a URL in the default browser. The app target adapts NSWorkspace to it; tests record
/// the URL.
public protocol ItemOpener: Sendable {
  func open(_ url: URL)
}
