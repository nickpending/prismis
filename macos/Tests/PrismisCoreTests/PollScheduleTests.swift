import PrismisCore
import Testing

@Suite struct PollScheduleTests {
  @Test func anUnreachableDaemonIsRetriedSoonThenTheIntervalResumes() {
    let unreachable = PollFailure.unreachable("The Internet connection appears to be offline.")
    #expect(PollSchedule.delay(after: unreachable, retriesSoFar: 0) == 10)
    #expect(PollSchedule.delay(after: unreachable, retriesSoFar: 1) == 30)
    #expect(PollSchedule.delay(after: unreachable, retriesSoFar: 2) == 60)
    #expect(PollSchedule.delay(after: unreachable, retriesSoFar: 3) == 300)
  }

  @Test func otherOutcomesWaitTheNormalInterval() {
    #expect(PollSchedule.delay(after: nil, retriesSoFar: 0) == 300)
    #expect(PollSchedule.delay(after: .refusedKey(403), retriesSoFar: 0) == 300)
    #expect(PollSchedule.delay(after: .serverError(500), retriesSoFar: 0) == 300)
  }
}
