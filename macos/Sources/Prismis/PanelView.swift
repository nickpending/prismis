import AppKit
import PrismisCore
import SwiftUI

/// The CleanCyber colours of the approved panel mockup.
private enum Palette {
  static let panel = Color(rgb: 0x10141B)
  static let border = Color(rgb: 0x00D9FF, opacity: 0.18)
  static let divider = Color(rgb: 0x1F2630)
  static let hover = Color(rgb: 0x18202B)
  static let text = Color(rgb: 0xEEEEEE)
  static let textDim = Color(rgb: 0xB7BFCA)
  static let muted = Color(rgb: 0x9AA4B2)
  static let accent = Color(rgb: 0x00D9FF)
  static let amber = Color(rgb: 0xFF8800)
  static let neutralChip: UInt32 = 0x9AA4B2
}

extension Color {
  /// A colour from `0xRRGGBB`.
  init(rgb: UInt32, opacity: Double = 1) {
    self.init(
      .sRGB, red: Double((rgb >> 16) & 0xFF) / 255, green: Double((rgb >> 8) & 0xFF) / 255,
      blue: Double(rgb & 0xFF) / 255, opacity: opacity)
  }
}

/// The menu-bar glyph of the icon mockup, drawn for each `IconState`. The quiet glyph is a template, so
/// the menu bar tints it; the other two carry their own colours.
enum MenuBarIcon {
  static func image(_ state: IconState) -> NSImage {
    let image = NSImage(size: NSSize(width: 18, height: 18), flipped: true) { _ in
      let glyph = NSBezierPath()
      glyph.move(to: NSPoint(x: 9, y: 3))
      glyph.line(to: NSPoint(x: 15, y: 14))
      glyph.line(to: NSPoint(x: 3, y: 14))
      glyph.close()
      glyph.move(to: NSPoint(x: 9, y: 7))
      glyph.line(to: NSPoint(x: 9, y: 14))
      glyph.lineWidth = 1.4
      glyph.lineJoinStyle = .round
      (state == .failed ? NSColor.tertiaryLabelColor : NSColor.labelColor).setStroke()
      glyph.stroke()
      switch state {
      case .quiet:
        break
      case .new:
        NSColor(srgbRed: 0, green: 0xD9 / 255, blue: 1, alpha: 1).setFill()
        NSBezierPath(ovalIn: NSRect(x: 12, y: 1, width: 6, height: 6)).fill()
      case .failed:
        NSColor(srgbRed: 1, green: 0x88 / 255, blue: 0, alpha: 1).setFill()
        NSBezierPath(ovalIn: NSRect(x: 11, y: 10, width: 7, height: 7)).fill()
      }
      return true
    }
    image.isTemplate = state == .quiet
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

/// The window-style panel: the header's count and check time, the newest rows, and the footer.
struct PanelView: View {
  let model: StatusModel

  var body: some View {
    VStack(alignment: .leading, spacing: 0) {
      header
      Palette.divider.frame(height: 1).padding(.horizontal, 18)
      rows
      Palette.divider.frame(height: 1).padding(.horizontal, 18)
      footer
    }
    .frame(width: 380)
    .background(Palette.panel)
    .overlay(RoundedRectangle(cornerRadius: 14).stroke(Palette.border))
    .environment(\.colorScheme, .dark)
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
    HStack(alignment: .firstTextBaseline, spacing: 8) {
      if let problem = model.configProblem {
        reason("Config: \(problem)")
      } else if let failure = snapshot.failure {
        reason(failure.description)
      } else {
        Text(PanelPresentation.countLabel(snapshot.newCount))
          .font(.system(size: 22, weight: .semibold, design: .monospaced))
          .foregroundStyle(snapshot.newCount > 0 ? Palette.accent : Palette.muted)
        Text("new since you last looked")
          .font(.system(size: 14, weight: .medium))
          .foregroundStyle(Palette.text)
      }
      Spacer(minLength: 8)
      if let checked = snapshot.checkedAt {
        TimelineView(.periodic(from: .now, by: 30)) { context in
          Text(PanelPresentation.checkedLabel(checked, now: context.date))
            .font(.system(size: 11, design: .monospaced))
            .foregroundStyle(Palette.muted)
        }
      }
    }
    .padding(EdgeInsets(top: 16, leading: 18, bottom: 10, trailing: 18))
  }

  private func reason(_ text: String) -> some View {
    Text(text)
      .font(.system(size: 13, weight: .medium))
      .foregroundStyle(Palette.amber)
      .fixedSize(horizontal: false, vertical: true)
  }

  @ViewBuilder private var rows: some View {
    VStack(alignment: .leading, spacing: 2) {
      if snapshot.rows.isEmpty {
        Text(snapshot.checkedAt == nil ? "Checking…" : "No unread HIGH items")
          .font(.system(size: 13))
          .foregroundStyle(Palette.muted)
          .padding(14)
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
    .padding(.vertical, 8)
    .padding(.horizontal, 6)
  }

  private var footer: some View {
    HStack(spacing: 2) {
      Button {
        model.openPrismis()
      } label: {
        HStack(spacing: 8) {
          Text("Open prismis")
          Image(systemName: "arrow.up.right")
        }
        .font(.system(size: 13, weight: .medium))
        .foregroundStyle(Palette.accent)
        .padding(.horizontal, 16)
        .frame(height: 44)
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(Palette.accent))
        .contentShape(Rectangle())
      }
      .buttonStyle(.plain)
      Spacer()
      Button {
        model.refresh()
      } label: {
        Image(systemName: "arrow.clockwise")
          .frame(width: 44, height: 44)
          .contentShape(Rectangle())
      }
      .buttonStyle(.plain)
      .foregroundStyle(Palette.muted)
      .keyboardShortcut("r")
      .help("Refresh now")
      Menu {
        Toggle(
          "Launch at Login",
          isOn: Binding(get: { model.launchAtLogin }, set: { model.setLaunchAtLogin($0) }))
        if let note = model.loginNote { Text(note) }
        Divider()
        Button("Quit Prismis") { NSApplication.shared.terminate(nil) }
          .keyboardShortcut("q")
      } label: {
        Image(systemName: "gearshape")
      }
      .menuStyle(.borderlessButton)
      .menuIndicator(.hidden)
      .fixedSize()
      .frame(width: 44, height: 44)
      .help("Settings")
    }
    .padding(EdgeInsets(top: 10, leading: 18, bottom: 12, trailing: 12))
  }
}

/// One row: the new dot, the title on at most two lines, the meta line, and the summary on hover.
private struct RowView: View {
  let row: PollSnapshot.Row
  let hovered: Bool

  var body: some View {
    HStack(alignment: .top, spacing: 12) {
      Circle()
        .fill(row.isNew ? Palette.accent : .clear)
        .frame(width: 7, height: 7)
        .padding(.top, 6)
        .frame(width: 8)
      VStack(alignment: .leading, spacing: 6) {
        Text(row.item.title)
          .font(.system(size: 13.5, weight: row.isNew ? .medium : .regular))
          .foregroundStyle(row.isNew ? Palette.text : Palette.textDim)
          .lineLimit(2)
          .multilineTextAlignment(.leading)
        meta
        if hovered, let summary = row.item.summary, !summary.isEmpty {
          Text(summary)
            .font(.system(size: 12))
            .foregroundStyle(Palette.textDim)
            .fixedSize(horizontal: false, vertical: true)
        }
      }
      .frame(maxWidth: .infinity, alignment: .leading)
    }
    .padding(EdgeInsets(top: 12, leading: 14, bottom: 12, trailing: 14))
    .background(hovered ? Palette.hover : .clear, in: RoundedRectangle(cornerRadius: 10))
    .contentShape(Rectangle())
  }

  private var meta: some View {
    HStack(spacing: 8) {
      if let source = row.item.sourceName { Text(source).lineLimit(1) }
      if let chip = PanelPresentation.chip(for: row.item.kind) { ChipView(chip: chip) }
      Spacer(minLength: 4)
      TimelineView(.periodic(from: .now, by: 60)) { context in
        Text(PanelPresentation.age(of: row.item, now: context.date) ?? "")
      }
    }
    .font(.system(size: 11, design: .monospaced))
    .foregroundStyle(Palette.muted)
  }
}

private struct ChipView: View {
  let chip: KindChip

  var body: some View {
    let (text, base): (UInt32, UInt32) =
      switch chip.style {
      case .tint(let text, let base): (text, base)
      case .neutral: (Palette.neutralChip, Palette.neutralChip)
      }
    Text(chip.label)
      .font(.system(size: 10.5, design: .monospaced))
      .foregroundStyle(Color(rgb: text))
      .padding(.horizontal, 7)
      .padding(.vertical, 1)
      .background(Capsule().fill(Color(rgb: base, opacity: 0.12)))
      .overlay(Capsule().stroke(Color(rgb: base, opacity: 0.35)))
  }
}
