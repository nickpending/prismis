import PrismisCore
import ServiceManagement

/// SMAppService behind PrismisCore's `LoginItemService`: the only place the app names it.
struct LoginItem: LoginItemService {
  var status: LoginItemStatus {
    switch SMAppService.mainApp.status {
    case .enabled: return .enabled
    case .requiresApproval: return .requiresApproval
    default: return .notRegistered
    }
  }

  func register() throws { try SMAppService.mainApp.register() }
  func unregister() throws { try SMAppService.mainApp.unregister() }
}
