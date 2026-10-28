import Foundation

/// When the next check runs. macOS checks an app's Local Network permission on its first connections to a
/// private address and fails them while it does: every launch-time check of a LAN daemon failed "offline"
/// or timed out (2026-10-27) while a check minutes later in the same process succeeded. So an unreachable
/// daemon is retried soon, a fixed number of times, before the normal interval resumes.
public enum PollSchedule {
  public static let interval: TimeInterval = 300
  public static let retryDelays: [TimeInterval] = [10, 30, 60]

  /// The wait before the next check, given the last check's failure and how many unreachable checks in a
  /// row have already been retried.
  public static func delay(after failure: PollFailure?, retriesSoFar: Int) -> TimeInterval {
    guard case .unreachable = failure, retriesSoFar < retryDelays.count else { return interval }
    return retryDelays[retriesSoFar]
  }
}
