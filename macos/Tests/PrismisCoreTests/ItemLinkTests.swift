import Foundation
import Testing

@testable import PrismisCore

/// C6: the item's link, and the two ways the app opens it.
@Suite struct ItemLinkTests {
  static let id = "5d937428-3c20-4b87-b342-c7f7a7f615cd"
  static let expected = "https://example.test/#item=5d937428-3c20-4b87-b342-c7f7a7f615cd"

  @Test(arguments: ["https://example.test", "https://example.test/"])
  func eitherBaseGivesTheSameLink(_ base: String) throws {
    let baseURL = try #require(URL(string: base))
    let url = try #require(ItemLink.url(base: baseURL, id: Self.id))
    #expect(url.absoluteString == Self.expected)
  }

  @Test func theDropdownActionOpensTheLink() throws {
    let opener = RecordingOpener()
    let opening = ItemOpening(
      base: try #require(URL(string: "https://example.test/")), opener: opener)
    opening.openItem(item(Self.id, "X"))
    #expect(opener.opened.map(\.absoluteString) == [Self.expected])
  }

  /// The notice a real poll posts, clicked: its userInfo is what the notification centre hands back.
  @Test func clickingAPostedNotificationOpensTheLink() async throws {
    let server = try await FakeServer()
    defer { server.stop() }
    let notifier = RecordingNotifier()
    let p = poller(
      server, store: try tempDirectory("link").appendingPathComponent("alerted.json"),
      notifier: notifier)
    await p.poll()
    server.list([item(Self.id, "X")])
    await p.poll()
    let notice = try #require(notifier.posted.first)

    let opener = RecordingOpener()
    let opening = ItemOpening(
      base: try #require(URL(string: "https://example.test")), opener: opener)
    let userInfo: [AnyHashable: Any] = notice.userInfo
    #expect(opening.openNotification(userInfo))
    #expect(opener.opened.map(\.absoluteString) == [Self.expected])
  }

  @Test func aNotificationWithoutAnItemOpensNothing() throws {
    let opener = RecordingOpener()
    let opening = ItemOpening(
      base: try #require(URL(string: "https://example.test")), opener: opener)
    #expect(!opening.openNotification(["other": "x"]))
    #expect(opener.opened.isEmpty)
  }
}
