import Foundation
import Testing

@testable import PrismisCore

/// C8: the item's link, and the panel's two ways into the web view through the opener.
@Suite struct ItemLinkTests {
  static let id = "5d937428-3c20-4b87-b342-c7f7a7f615cd"
  static let expected = "https://example.test/#item=5d937428-3c20-4b87-b342-c7f7a7f615cd"

  @Test(arguments: ["https://example.test", "https://example.test/"])
  func eitherBaseGivesTheSameLink(_ base: String) throws {
    let baseURL = try #require(URL(string: base))
    let url = try #require(ItemLink.url(base: baseURL, id: Self.id))
    #expect(url.absoluteString == Self.expected)
  }

  /// A row a real poll listed, clicked, then the footer's Open prismis.
  @Test(arguments: ["https://example.test", "https://example.test/"])
  func aRowOpensItsItemAndOpenPrismisOpensTheWebView(_ base: String) async throws {
    let server = try await FakeServer()
    defer { server.stop() }
    server.list([item(Self.id, "X")])
    let row = try #require(await poller(server, store: try lastLookedFile(nil)).poll().rows.first)

    let opener = RecordingOpener()
    let opening = ItemOpening(base: try #require(URL(string: base)), opener: opener)
    opening.openItem(row.item)
    opening.openPrismis()

    #expect(opener.opened.map(\.absoluteString) == [Self.expected, "https://example.test/"])
  }
}
