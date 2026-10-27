import Foundation

public enum LoginItemStatus: Sendable, Equatable {
  case notRegistered
  case enabled
  case requiresApproval
}

/// The operating system's login-item registry. The app target adapts the system service to it; tests fake it.
public protocol LoginItemService: Sendable {
  var status: LoginItemStatus { get }
  func register() throws
  func unregister() throws
}

/// Launch at login is the operator's choice: this reads the registration and changes it only in `set(_:)`,
/// which only the menu's toggle calls. Installing never registers anything, so it starts off.
public struct LaunchAtLogin: Sendable {
  public static let approvalNote = "Allow Prismis in System Settings > General > Login Items"

  public struct State: Sendable, Equatable {
    public let isOn: Bool
    public let note: String?

    public init(isOn: Bool, note: String?) {
      self.isOn = isOn
      self.note = note
    }
  }

  private let service: LoginItemService

  public init(service: LoginItemService) { self.service = service }

  public var state: State {
    switch service.status {
    case .notRegistered: return State(isOn: false, note: nil)
    case .enabled: return State(isOn: true, note: nil)
    case .requiresApproval: return State(isOn: true, note: Self.approvalNote)
    }
  }

  /// Register or unregister to match `on`. Returns a note for the menu when the system refused.
  @discardableResult
  public func set(_ on: Bool) -> String? {
    do {
      if on {
        if service.status == .notRegistered { try service.register() }
      } else if service.status != .notRegistered {
        try service.unregister()
      }
      return nil
    } catch {
      return "Launch at Login failed: \(error.localizedDescription)"
    }
  }
}
