import PrismisCore
import SwiftUI

@main
struct PrismisApp: App {
  // A plain stored property, not @State: the App is built once per process, and @Observable tracks reads
  // without it.
  private let model: StatusModel

  init() {
    // The poll loop starts with the app, not when the panel first opens, so the icon is current before
    // the operator looks.
    model = StatusModel()
    model.start()
  }

  var body: some Scene {
    MenuBarExtra {
      PanelView(model: model)
    } label: {
      Image(nsImage: MenuBarIcon.image(model.icon))
        .accessibilityLabel(MenuBarIcon.label(model.icon))
    }
    .menuBarExtraStyle(.window)
  }
}
