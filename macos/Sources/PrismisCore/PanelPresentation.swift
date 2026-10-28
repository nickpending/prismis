import Foundation

/// The menu-bar icon: quiet with nothing new, a dot when something is new, dimmed with an amber mark when
/// the last check failed or the config was refused.
public enum IconState: Sendable, Equatable {
  case quiet
  case new
  case failed

  public init(snapshot: PollSnapshot, configRefused: Bool = false) {
    if configRefused || snapshot.failure != nil {
      self = .failed
    } else {
      self = snapshot.newCount > 0 ? .new : .quiet
    }
  }
}

/// How a kind chip is drawn: its own tint for a kind the app knows, the neutral chip for one it does not.
/// Colours are `0xRRGGBB`; the app turns them into its own colour type.
public enum ChipStyle: Sendable, Equatable {
  /// `text` for the label; `base` for the fill and the border, each at its own opacity.
  case tint(text: UInt32, base: UInt32)
  case neutral
}

public struct KindChip: Sendable, Equatable {
  public let label: String
  public let style: ChipStyle
}

/// What the panel's header and rows say, decided here so `swift test` can check it.
public enum PanelPresentation {
  /// The tint of each kind the daemon's classifier names: the mockup's five, and five more from the same
  /// palette.
  public static let kindTints: [String: ChipStyle] = [
    "vulnerability": .tint(text: 0xFF6FA3, base: 0xFF0066),
    "experience": .tint(text: 0xE6CCFF, base: 0x9F4DFF),
    "research": .tint(text: 0x5CFFB0, base: 0x00FF88),
    "release": .tint(text: 0x7FEBFF, base: 0x00D9FF),
    "news": .tint(text: 0xFFB566, base: 0xFF8800),
    "incident": .tint(text: 0xFF8A80, base: 0xFF3B30),
    "question": .tint(text: 0xFFE680, base: 0xFFD400),
    "analysis": .tint(text: 0xA8C1FF, base: 0x4D7CFF),
    "humor": .tint(text: 0xFF99E6, base: 0xFF33CC),
    "tutorial": .tint(text: 0x80FFEA, base: 0x00E5C0),
  ]

  /// The header's new count: the number, or 99+ past 99.
  public static func countLabel(_ count: Int) -> String {
    count > 99 ? "99+" : String(count)
  }

  /// The chip a row shows for `kind`, or nil when the item has no kind.
  public static func chip(for kind: String?) -> KindChip? {
    guard let kind, !kind.isEmpty else { return nil }
    return KindChip(label: kind, style: kindTints[kind] ?? .neutral)
  }

  /// How long ago `time` was, at `now`, in its largest whole unit: "now", "4m", "2h", "3d". A time after
  /// `now`, from a daemon whose clock runs ahead, reads as now.
  public static func ago(_ time: Date, now: Date) -> String {
    let minutes = Int(now.timeIntervalSince(time) / 60)
    if minutes < 1 { return "now" }
    if minutes < 60 { return "\(minutes)m" }
    if minutes < 24 * 60 { return "\(minutes / 60)h" }
    return "\(minutes / (24 * 60))d"
  }

  /// The header's check time: "checked just now", "checked 4m ago".
  public static func checkedLabel(_ checked: Date, now: Date) -> String {
    let ago = ago(checked, now: now)
    return ago == "now" ? "checked just now" : "checked \(ago) ago"
  }

  /// A row's age: from its published time, else its fetched time; nil with neither.
  public static func age(of item: Item, now: Date) -> String? {
    (item.publishedAt ?? item.fetchedAt).map { ago($0, now: now) }
  }
}
