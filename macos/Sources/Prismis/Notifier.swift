import Foundation
import PrismisCore
import UserNotifications

/// Posts each new item as a macOS notification. The permission prompt comes the first time one is due, not
/// at launch.
struct Notifier: ItemNotifier {
  struct Refused: Error, CustomStringConvertible {
    var description: String { "notifications are not allowed for Prismis in System Settings" }
  }

  func post(_ notice: ItemNotice) async throws {
    let center = UNUserNotificationCenter.current()
    switch await center.notificationSettings().authorizationStatus {
    case .notDetermined:
      guard try await center.requestAuthorization(options: [.alert, .sound]) else {
        throw Refused()
      }
    case .denied:
      throw Refused()
    default:
      break
    }
    let content = UNMutableNotificationContent()
    content.title = notice.title
    content.body = notice.body
    content.sound = .default
    content.userInfo = notice.userInfo
    try await center.add(
      UNNotificationRequest(identifier: notice.identifier, content: content, trigger: nil))
  }
}

/// The notification centre's delegate: a click on an item's notification opens it the way the dropdown does,
/// and a notification that arrives while the menu is open still shows.
final class NotificationClicks: NSObject, UNUserNotificationCenterDelegate, @unchecked Sendable {
  private let opening: ItemOpening

  init(opening: ItemOpening) { self.opening = opening }

  func userNotificationCenter(
    _ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse
  ) async {
    opening.openNotification(response.notification.request.content.userInfo)
  }

  func userNotificationCenter(
    _ center: UNUserNotificationCenter, willPresent notification: UNNotification
  ) async -> UNNotificationPresentationOptions {
    [.banner, .list, .sound]
  }
}
