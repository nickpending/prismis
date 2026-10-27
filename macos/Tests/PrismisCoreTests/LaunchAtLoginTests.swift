import Foundation
import Testing

@testable import PrismisCore

/// The login-item service's stand-in: records every call and holds the registration.
final class FakeLoginItem: LoginItemService, @unchecked Sendable {
  struct Refused: Error, LocalizedError {
    var errorDescription: String? { "refused by the system" }
  }

  private let lock = NSLock()
  private var _status: LoginItemStatus
  private var _calls: [String] = []
  let registerThrows: Bool

  init(_ status: LoginItemStatus = .notRegistered, registerThrows: Bool = false) {
    _status = status
    self.registerThrows = registerThrows
  }

  var status: LoginItemStatus { lock.withLock { _status } }
  var calls: [String] { lock.withLock { _calls } }

  func register() throws {
    lock.withLock { _calls.append("register") }
    if registerThrows { throw Refused() }
    lock.withLock { _status = .enabled }
  }

  func unregister() throws {
    lock.withLock {
      _calls.append("unregister")
      _status = .notRegistered
    }
  }
}

/// C8: launch at login starts off, reading it never changes it, and only the toggle does.
@Suite struct LaunchAtLoginTests {
  @Test func aFreshInstallStartsOffAndReadingItRegistersNothing() {
    let fake = FakeLoginItem()
    let login = LaunchAtLogin(service: fake)
    #expect(login.state == LaunchAtLogin.State(isOn: false, note: nil))
    _ = login.state
    #expect(fake.calls.isEmpty, "reading the state called \(fake.calls)")
    #expect(fake.status == .notRegistered)
  }

  @Test func readingTheStateNeverChangesTheRegistration() {
    let expected: [(LoginItemStatus, Bool, String?)] = [
      (.notRegistered, false, nil), (.enabled, true, nil),
      (.requiresApproval, true, "Allow Prismis in System Settings > General > Login Items"),
    ]
    for (status, on, note) in expected {
      let fake = FakeLoginItem(status)
      let state = LaunchAtLogin(service: fake).state
      #expect(state.isOn == on)
      #expect(state.note == note)
      #expect(fake.calls.isEmpty, "reading \(status) called \(fake.calls)")
    }
  }

  @Test func theToggleTurnsItOnAndOffOnce() {
    let fake = FakeLoginItem()
    let login = LaunchAtLogin(service: fake)
    #expect(login.set(true) == nil)
    #expect(login.state.isOn)
    #expect(login.set(false) == nil)
    #expect(!login.state.isOn)
    #expect(fake.calls == ["register", "unregister"])
  }

  @Test func aRefusedRegistrationReturnsANoteAndNeverUnregisters() {
    let fake = FakeLoginItem(registerThrows: true)
    let note = LaunchAtLogin(service: fake).set(true)
    #expect(note?.contains("refused by the system") == true)
    #expect(fake.calls == ["register"])
  }
}
