import AppKit
import PrismisCore
import SwiftUI

/// The menu-bar glyph for each `IconState`: always a template image, so the menu bar paints it in the same
/// colour as every other menu-bar icon in light and dark bars alike. A template carries no colour of its own,
/// so new is the glyph with a dot cut beside it and failed is the warning variant of the symbol.
enum MenuBarIcon {
  static func image(_ state: IconState) -> NSImage {
    let config = NSImage.SymbolConfiguration(pointSize: 14, weight: .regular)
    let name = state == .failed ? "exclamationmark.triangle" : "triangle"
    let glyph = NSImage(systemSymbolName: name, accessibilityDescription: nil)?
      .withSymbolConfiguration(config)
    let image = NSImage(size: NSSize(width: 20, height: 18), flipped: false) { rect in
      if let glyph {
        let size = glyph.size
        glyph.draw(
          in: NSRect(
            x: (rect.height - size.width) / 2, y: (rect.height - size.height) / 2,
            width: size.width,
            height: size.height))
      }
      if state == .new {
        NSColor.black.setFill()
        NSBezierPath(ovalIn: NSRect(x: 14, y: 11, width: 6, height: 6)).fill()
      }
      return true
    }
    image.isTemplate = true
    return image
  }

  static func label(_ state: IconState) -> String {
    switch state {
    case .quiet: return "Prismis: nothing new"
    case .new: return "Prismis: new HIGH items"
    case .failed: return "Prismis: the last check failed"
    }
  }
}

/// The window-style panel, drawn as the system draws its own menu-bar panels: the window's material, system
/// type and label colours, SF Symbols, and the user's accent colour for what is new.
struct PanelView: View {
  let model: StatusModel

  var body: some View {
    VStack(alignment: .leading, spacing: 0) {
      header
      Divider().padding(.horizontal, 12)
      rows
      Divider().padding(.horizontal, 12)
      footer
    }
    .frame(width: 340)
    // The rows are on screen with their dots before the last-looked time moves. A window-style panel is
    // ordered out between looks rather than rebuilt, so each later look arrives as its window becoming
    // key; this accessory app has no other window.
    .onAppear { model.panelShown() }
    .onReceive(NotificationCenter.default.publisher(for: NSWindow.didBecomeKeyNotification)) { _ in
      model.panelShown()
    }
  }

  private var snapshot: PollSnapshot { model.snapshot }

  @ViewBuilder private var header: some View {
    HStack(alignment: .firstTextBaseline, spacing: 4) {
      if let problem = model.configProblem {
        reason("Config: \(problem)")
      } else if let failure = snapshot.failure {
        reason(failure.description)
      } else {
        Text("\(PanelPresentation.countLabel(snapshot.newCount)) new")
          .font(.headline)
          .foregroundStyle(snapshot.newCount > 0 ? Color.accentColor : Color.secondary)
        Text("since you last looked")
          .font(.subheadline)
          .foregroundStyle(.secondary)
      }
      Spacer(minLength: 8)
      if let checked = snapshot.checkedAt {
        TimelineView(.periodic(from: .now, by: 30)) { context in
          Text(PanelPresentation.checkedLabel(checked, now: context.date))
            .font(.caption)
            .foregroundStyle(.tertiary)
        }
      }
    }
    .padding(EdgeInsets(top: 12, leading: 14, bottom: 10, trailing: 14))
  }

  private func reason(_ text: String) -> some View {
    Label(text, systemImage: "exclamationmark.triangle.fill")
      .font(.subheadline)
      .foregroundStyle(.orange)
      .fixedSize(horizontal: false, vertical: true)
  }

  @ViewBuilder private var rows: some View {
    VStack(alignment: .leading, spacing: 0) {
      if snapshot.rows.isEmpty {
        Text(snapshot.checkedAt == nil ? "Checking…" : "No unread HIGH items")
          .font(.subheadline)
          .foregroundStyle(.secondary)
          .padding(.horizontal, 8)
          .padding(.vertical, 10)
      }
      ForEach(snapshot.rows) { row in
        Button {
          model.open(row.item)
        } label: {
          RowView(row: row, hovered: model.hoveredRow == row.id)
        }
        .buttonStyle(.plain)
        .onHover { inside in model.hover(row.id, inside: inside) }
      }
    }
    .padding(6)
  }

  private var footer: some View {
    HStack(spacing: 12) {
      Button {
        model.openPrismis()
      } label: {
        Label("Open Prismis", systemImage: "arrow.up.forward.app")
      }
      .buttonStyle(.borderless)
      Spacer()
      Button {
        model.refresh()
      } label: {
        Image(systemName: "arrow.clockwise")
      }
      .buttonStyle(.borderless)
      .keyboardShortcut("r")
      .help("Refresh now")
      .accessibilityLabel("Refresh now")
      Menu {
        Toggle(
          "Launch at Login",
          isOn: Binding(get: { model.launchAtLogin }, set: { model.setLaunchAtLogin($0) }))
        if let note = model.loginNote { Text(note) }
        Divider()
        Button("Quit Prismis") { NSApplication.shared.terminate(nil) }
          .keyboardShortcut("q")
      } label: {
        Image(systemName: "ellipsis.circle")
      }
      .menuStyle(.borderlessButton)
      .menuIndicator(.hidden)
      .fixedSize()
      .help("Settings")
      .accessibilityLabel("Settings")
    }
    .font(.subheadline)
    .foregroundStyle(.secondary)
    .padding(EdgeInsets(top: 8, leading: 14, bottom: 10, trailing: 14))
  }
}

/// One row: the new dot, the title on at most two lines, and the meta line.
private struct RowView: View {
  let row: PollSnapshot.Row
  let hovered: Bool

  var body: some View {
    HStack(alignment: .firstTextBaseline, spacing: 8) {
      Circle()
        .fill(row.isNew ? Color.accentColor : .clear)
        .frame(width: 6, height: 6)
        .alignmentGuide(.firstTextBaseline) { $0[.bottom] }
      VStack(alignment: .leading, spacing: 3) {
        Text(row.item.title)
          .font(.body.weight(row.isNew ? .semibold : .regular))
          .foregroundStyle(row.isNew ? .primary : .secondary)
          .lineLimit(2)
          .multilineTextAlignment(.leading)
        meta
      }
      .frame(maxWidth: .infinity, alignment: .leading)
    }
    .padding(.horizontal, 8)
    .padding(.vertical, 6)
    .background(
      hovered ? Color.primary.opacity(0.08) : .clear, in: RoundedRectangle(cornerRadius: 6)
    )
    .contentShape(Rectangle())
  }

  private var meta: some View {
    HStack(spacing: 6) {
      if let source = row.item.sourceName { Text(source).lineLimit(1) }
      if let chip = PanelPresentation.chip(for: row.item.kind) {
        Text("·")
        ChipView(chip: chip)
      }
      Spacer(minLength: 4)
      TimelineView(.periodic(from: .now, by: 60)) { context in
        Text(PanelPresentation.age(of: row.item, now: context.date) ?? "")
      }
    }
    .font(.caption)
    .foregroundStyle(.secondary)
  }
}

/// The kind, in the system's small caption type with a faint wash of its tint; the label stays a label
/// colour so it reads in light and dark mode alike.
private struct ChipView: View {
  let chip: KindChip

  var body: some View {
    let tint: Color =
      switch chip.style {
      case .tint(_, let base): Color(rgb: base)
      case .neutral: .secondary
      }
    Text(chip.label)
      .font(.caption2)
      .foregroundStyle(.secondary)
      .padding(.horizontal, 5)
      .padding(.vertical, 1)
      .background(Capsule().fill(tint.opacity(0.18)))
  }
}

extension Color {
  /// A colour from `0xRRGGBB`, used only for a kind's tint wash.
  init(rgb: UInt32) {
    self.init(
      .sRGB, red: Double((rgb >> 16) & 0xFF) / 255, green: Double((rgb >> 8) & 0xFF) / 255,
      blue: Double(rgb & 0xFF) / 255)
  }
}
