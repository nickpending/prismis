import PrismisCore
import SwiftUI

@main
struct PrismisApp: App {
  // A plain stored property, not @State: the App is built once per process, and @Observable tracks reads
  // without it.
  private let model: StatusModel

  init() {
    // The poll loop and the notification delegate start with the app, not when the menu first opens, so a
    // click on a notification that launched the app is handled.
    model = StatusModel()
    model.start()
  }

  var body: some Scene {
    MenuBarExtra {
      if let problem = model.configProblem {
        Text("Config: \(problem)")
      } else {
        if let failure = model.failure {
          Text("Daemon unreachable: \(failure)")
          Divider()
        }
        if model.items.isEmpty {
          Text("No unread HIGH items")
        } else {
          ForEach(model.items) { item in
            Button(label(item)) { model.open(item) }
          }
        }
      }
      Divider()
      Button("Refresh Now") { model.refresh() }
        .keyboardShortcut("r")
      Toggle(
        "Launch at Login",
        isOn: Binding(get: { model.launchAtLogin }, set: { model.setLaunchAtLogin($0) }))
      if let note = model.loginNote { Text(note) }
      Divider()
      Button("Quit Prismis") { NSApplication.shared.terminate(nil) }
        .keyboardShortcut("q")
    } label: {
      Image(systemName: model.symbol)
      Text(model.countLabel)
    }
  }

  private func label(_ item: Item) -> String {
    guard let source = item.sourceName, !source.isEmpty else { return item.title }
    return "\(item.title) — \(source)"
  }
}
