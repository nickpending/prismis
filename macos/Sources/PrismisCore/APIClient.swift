import Foundation

/// The daemon's clock as the API writes it: ISO 8601 with an offset, with or without fractional seconds.
public enum DaemonTime {
  /// Nil for text that is not an ISO 8601 time with an offset.
  public static func parse(_ text: String) -> Date? {
    let fractional = ISO8601DateFormatter()
    fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    if let date = fractional.date(from: text) { return date }
    return ISO8601DateFormatter().date(from: text)
  }

  /// UTC with milliseconds and a `Z`, which a query string carries unescaped, where `+00:00` would read
  /// back as a space.
  public static func format(_ date: Date) -> String {
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    return formatter.string(from: date)
  }

  /// Whether `time` falls after `mark` the way the daemon's `since` filter decides it: SQLite's
  /// `datetime()` drops fractional seconds on both sides, so a time in the same second as the mark is not
  /// after it.
  public static func isAfter(_ time: Date, _ mark: Date) -> Bool {
    time.timeIntervalSince1970.rounded(.down) > mark.timeIntervalSince1970.rounded(.down)
  }
}

/// One unread HIGH item, as the list view of `GET /api/entries` gives it.
public struct Item: Sendable, Equatable, Identifiable, Decodable {
  public let id: String
  public let title: String
  public let sourceName: String?
  /// The daemon's kind, mirrored from the analysis; nil when the item has none.
  public let kind: String?
  public let summary: String?
  /// When the daemon fetched it, on the daemon's clock: what newness and the last-looked time are read
  /// from.
  public let fetchedAt: Date?
  public let publishedAt: Date?

  public init(
    id: String, title: String, sourceName: String?, kind: String? = nil, summary: String? = nil,
    fetchedAt: Date? = nil, publishedAt: Date? = nil
  ) {
    self.id = id
    self.title = title
    self.sourceName = sourceName
    self.kind = kind
    self.summary = summary
    self.fetchedAt = fetchedAt
    self.publishedAt = publishedAt
  }

  enum CodingKeys: String, CodingKey {
    case id, title, kind, summary, analysis
    case sourceName = "source_name"
    case fetchedAt = "fetched_at"
    case publishedAt = "published_at"
  }

  private struct Analysis: Decodable { let kind: String? }

  /// Only the id and the title are required. Every other field that is missing, null or of the wrong type
  /// is nil, so one odd item never fails the poll.
  public init(from decoder: Decoder) throws {
    let c = try decoder.container(keyedBy: CodingKeys.self)
    id = try c.decode(String.self, forKey: .id)
    title = try c.decode(String.self, forKey: .title)
    sourceName = try? c.decodeIfPresent(String.self, forKey: .sourceName)
    summary = try? c.decodeIfPresent(String.self, forKey: .summary)
    let analysis = try? c.decodeIfPresent(Analysis.self, forKey: .analysis)
    let named = (try? c.decodeIfPresent(String.self, forKey: .kind)) ?? analysis?.kind
    kind = named?.isEmpty == false ? named : nil
    fetchedAt = Self.time(c, .fetchedAt)
    publishedAt = Self.time(c, .publishedAt)
  }

  private static func time(_ c: KeyedDecodingContainer<CodingKeys>, _ key: CodingKeys) -> Date? {
    (try? c.decodeIfPresent(String.self, forKey: key)).flatMap(DaemonTime.parse)
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

  /// The request a poll makes: the four filters, at most `limit` items, only those fetched after `since`
  /// when it is set, and the key in `X-API-Key`.
  public func request(since: Date? = nil, limit: Int) -> URLRequest {
    var root = config.baseURL.absoluteString
    while root.hasSuffix("/") { root.removeLast() }
    var components = URLComponents(string: "\(root)/api/entries")
    var query = [
      URLQueryItem(name: "priority", value: "high"),
      URLQueryItem(name: "unread_only", value: "true"),
      URLQueryItem(name: "view", value: "list"),
      URLQueryItem(name: "sort_by", value: "date"),
      URLQueryItem(name: "limit", value: String(limit)),
    ]
    if let since { query.append(URLQueryItem(name: "since", value: DaemonTime.format(since))) }
    components?.queryItems = query
    var request = URLRequest(url: components?.url ?? config.baseURL, timeoutInterval: Self.timeout)
    request.httpMethod = "GET"
    request.setValue(config.key, forHTTPHeaderField: "X-API-Key")
    request.setValue("application/json", forHTTPHeaderField: "Accept")
    return request
  }

  /// The listed items, or a `PollFailure` saying why there are none to show.
  public func highUnread(since: Date? = nil, limit: Int) async throws(PollFailure) -> [Item] {
    let data: Data
    let response: URLResponse
    do {
      (data, response) = try await session.data(
        for: request(since: since, limit: limit), delegate: RefuseRedirects())
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
