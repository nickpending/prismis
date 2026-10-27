import Foundation

/// One unread HIGH item, as the list view of `GET /api/entries` gives it.
public struct Item: Sendable, Equatable, Identifiable, Decodable {
  public let id: String
  public let title: String
  public let sourceName: String?

  public init(id: String, title: String, sourceName: String?) {
    self.id = id
    self.title = title
    self.sourceName = sourceName
  }

  enum CodingKeys: String, CodingKey {
    case id, title
    case sourceName = "source_name"
  }
}

/// Why a poll got no list. A refused key, a daemon that cannot be reached and a daemon that failed are told
/// apart, since each has a different fix.
public enum PollFailure: Error, Equatable, CustomStringConvertible {
  /// 401 or 403: the daemon is there and does not take the key.
  case refusedKey(Int)
  /// No HTTP answer at all: refused connection, timeout, DNS, TLS.
  case unreachable(String)
  /// 5xx.
  case serverError(Int)
  /// Any other non-2xx, a redirect included: the key is never sent anywhere but the configured daemon.
  case unexpectedStatus(Int)
  /// A 2xx whose body is not the list.
  case badResponse(String)

  public var description: String {
    switch self {
    case .refusedKey(let s):
      return "the daemon refused the API key (HTTP \(s)); check key in config.toml"
    case .unreachable(let why): return "daemon unreachable: \(why)"
    case .serverError(let s): return "the daemon failed (HTTP \(s))"
    case .unexpectedStatus(let s): return "the daemon answered HTTP \(s)"
    case .badResponse(let why): return "the daemon's answer is not a list: \(why)"
    }
  }
}

/// Answers every redirect with nil, so the 3xx is the response and the key goes to the configured daemon only.
final class RefuseRedirects: NSObject, URLSessionTaskDelegate {
  func urlSession(
    _ session: URLSession, task: URLSessionTask,
    willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest,
    completionHandler: @escaping (URLRequest?) -> Void
  ) {
    completionHandler(nil)
  }
}

/// `GET /api/entries` for the unread HIGH items, newest first, in the list view.
public struct APIClient: Sendable {
  public static let timeout: TimeInterval = 30

  public let config: PrismisConfig
  private let session: URLSession

  public init(config: PrismisConfig, session: URLSession = URLSession(configuration: .ephemeral)) {
    self.config = config
    self.session = session
  }

  private struct Envelope: Decodable {
    struct Payload: Decodable { let items: [Item] }
    let data: Payload
  }

  /// The request a poll makes: the four filters and the key in `X-API-Key`.
  public var request: URLRequest {
    var root = config.baseURL.absoluteString
    while root.hasSuffix("/") { root.removeLast() }
    var components = URLComponents(string: "\(root)/api/entries")
    components?.queryItems = [
      URLQueryItem(name: "priority", value: "high"),
      URLQueryItem(name: "unread_only", value: "true"),
      URLQueryItem(name: "view", value: "list"),
      URLQueryItem(name: "sort_by", value: "date"),
    ]
    var request = URLRequest(url: components?.url ?? config.baseURL, timeoutInterval: Self.timeout)
    request.httpMethod = "GET"
    request.setValue(config.key, forHTTPHeaderField: "X-API-Key")
    request.setValue("application/json", forHTTPHeaderField: "Accept")
    return request
  }

  /// The listed items, or a `PollFailure` saying why there are none to show.
  public func highUnread() async throws(PollFailure) -> [Item] {
    let data: Data
    let response: URLResponse
    do {
      (data, response) = try await session.data(for: request, delegate: RefuseRedirects())
    } catch {
      throw .unreachable(error.localizedDescription)
    }
    guard let http = response as? HTTPURLResponse else { throw .badResponse("not HTTP") }
    switch http.statusCode {
    case 200..<300: break
    case 401, 403: throw .refusedKey(http.statusCode)
    case 500..<600: throw .serverError(http.statusCode)
    default: throw .unexpectedStatus(http.statusCode)
    }
    do {
      return try JSONDecoder().decode(Envelope.self, from: data).data.items
    } catch {
      throw .badResponse(String(describing: error))
    }
  }
}
