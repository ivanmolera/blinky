#!/usr/bin/env swift
import AppKit
import Foundation

// All artwork is drawn from vectors so every icon size stays reproducible.
let fileManager = FileManager.default
let projectRoot = URL(fileURLWithPath: #filePath)
    .deletingLastPathComponent().deletingLastPathComponent()
let assets = projectRoot.appendingPathComponent("Assets", isDirectory: true)
let temporary = fileManager.temporaryDirectory
    .appendingPathComponent("blinky-icon-\(UUID().uuidString)", isDirectory: true)
let iconset = temporary.appendingPathComponent("AppIcon.iconset", isDirectory: true)
try fileManager.createDirectory(at: assets, withIntermediateDirectories: true)
try fileManager.createDirectory(at: iconset, withIntermediateDirectories: true)
defer { try? fileManager.removeItem(at: temporary) }

func color(_ hex: UInt32, alpha: CGFloat = 1) -> NSColor {
    NSColor(calibratedRed: CGFloat((hex >> 16) & 255) / 255,
            green: CGFloat((hex >> 8) & 255) / 255,
            blue: CGFloat(hex & 255) / 255, alpha: alpha)
}

func ellipse(_ rect: NSRect, fill: NSColor) {
    fill.setFill()
    NSBezierPath(ovalIn: rect).fill()
}

func gradient(_ colors: [NSColor], in path: NSBezierPath, angle: CGFloat) {
    NSGradient(colors: colors)!.draw(in: path, angle: angle)
}

func drawIcon() {
    let tile = NSBezierPath(roundedRect: NSRect(x: 72, y: 72, width: 880, height: 880),
                            xRadius: 202, yRadius: 202)

    NSGraphicsContext.saveGraphicsState()
    let tileShadow = NSShadow()
    tileShadow.shadowColor = color(0x171D55, alpha: 0.22)
    tileShadow.shadowBlurRadius = 24
    tileShadow.shadowOffset = NSSize(width: 0, height: -12)
    tileShadow.set()
    color(0x3C40BE).setFill()
    tile.fill()
    NSGraphicsContext.restoreGraphicsState()

    gradient([color(0x3036A6), color(0x535BE0), color(0x777CFF)], in: tile, angle: 115)

    // A restrained top edge gives the tile depth without reducing contrast.
    NSGraphicsContext.saveGraphicsState()
    tile.addClip()
    let glow = NSBezierPath(ovalIn: NSRect(x: -90, y: 660, width: 1120, height: 640))
    gradient([color(0xFFFFFF, alpha: 0), color(0xFFFFFF, alpha: 0.14)], in: glow, angle: 90)
    NSGraphicsContext.restoreGraphicsState()
    color(0xFFFFFF, alpha: 0.14).setStroke()
    tile.lineWidth = 2
    tile.stroke()

    let body = NSBezierPath(roundedRect: NSRect(x: 232, y: 300, width: 560, height: 390),
                            xRadius: 82, yRadius: 82)
    NSGraphicsContext.saveGraphicsState()
    let cameraShadow = NSShadow()
    cameraShadow.shadowColor = color(0x10164F, alpha: 0.28)
    cameraShadow.shadowBlurRadius = 28
    cameraShadow.shadowOffset = NSSize(width: 0, height: -14)
    cameraShadow.set()
    color(0xFFFFFF).setFill()
    body.fill()
    NSGraphicsContext.restoreGraphicsState()
    gradient([color(0xEDF0FF), color(0xFFFFFF)], in: body, angle: 90)

    // The roof connects the camera silhouette to the home it watches over.
    let roof = NSBezierPath()
    roof.move(to: NSPoint(x: 394, y: 718))
    roof.line(to: NSPoint(x: 512, y: 808))
    roof.line(to: NSPoint(x: 630, y: 718))
    roof.lineWidth = 36
    roof.lineCapStyle = .round
    roof.lineJoinStyle = .round
    color(0xFFFFFF).setStroke()
    roof.stroke()
    let walls = NSBezierPath()
    walls.move(to: NSPoint(x: 418, y: 730))
    walls.line(to: NSPoint(x: 418, y: 689))
    walls.move(to: NSPoint(x: 606, y: 730))
    walls.line(to: NSPoint(x: 606, y: 689))
    walls.lineWidth = 30
    walls.lineCapStyle = .round
    walls.stroke()

    // The central lens remains the dominant feature at small Dock sizes.
    let lens = NSBezierPath(ovalIn: NSRect(x: 368, y: 351, width: 288, height: 288))
    gradient([color(0x292F8E), color(0x525CD7)], in: lens, angle: 100)
    let innerRing = NSBezierPath(ovalIn: NSRect(x: 397, y: 380, width: 230, height: 230))
    color(0xAAB8FF, alpha: 0.36).setStroke()
    innerRing.lineWidth = 10
    innerRing.stroke()
    let iris = NSBezierPath(ovalIn: NSRect(x: 432, y: 415, width: 160, height: 160))
    gradient([color(0x252D7D), color(0x414DB7)], in: iris, angle: 110)
    let reflection = NSBezierPath()
    reflection.appendArc(withCenter: NSPoint(x: 512, y: 495), radius: 97,
                         startAngle: 55, endAngle: 132, clockwise: false)
    reflection.lineWidth = 15
    reflection.lineCapStyle = .round
    color(0xFFFFFF, alpha: 0.64).setStroke()
    reflection.stroke()
    ellipse(NSRect(x: 461, y: 526, width: 29, height: 29), fill: color(0xFFFFFF, alpha: 0.84))

    let viewfinder = NSBezierPath(roundedRect: NSRect(x: 275, y: 619, width: 62, height: 20),
                                  xRadius: 10, yRadius: 10)
    color(0x5863BE, alpha: 0.5).setFill()
    viewfinder.fill()
    ellipse(NSRect(x: 716, y: 612, width: 32, height: 32), fill: color(0x52CBC3))
}

func png(pixels: Int) throws -> Data {
    guard let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: pixels, pixelsHigh: pixels,
                                        bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true,
                                        isPlanar: false, colorSpaceName: .deviceRGB,
                                        bytesPerRow: 0, bitsPerPixel: 0),
          let context = NSGraphicsContext(bitmapImageRep: bitmap) else {
        throw NSError(domain: "BlinkyIcon", code: 1,
                      userInfo: [NSLocalizedDescriptionKey: "Could not create the icon drawing context."])
    }
    bitmap.size = NSSize(width: pixels, height: pixels)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = context
    context.shouldAntialias = true
    context.imageInterpolation = .high
    let scale = CGFloat(pixels) / 1024
    context.cgContext.scaleBy(x: scale, y: scale)
    drawIcon()
    context.flushGraphics()
    NSGraphicsContext.restoreGraphicsState()
    guard let data = bitmap.representation(using: .png, properties: [:]) else {
        throw NSError(domain: "BlinkyIcon", code: 2,
                      userInfo: [NSLocalizedDescriptionKey: "Could not encode the icon PNG."])
    }
    return data
}

for size in [16, 32, 128, 256, 512] {
    try png(pixels: size).write(to: iconset.appendingPathComponent("icon_\(size)x\(size).png"))
    try png(pixels: size * 2).write(to: iconset.appendingPathComponent("icon_\(size)x\(size)@2x.png"))
}
try png(pixels: 1024).write(to: assets.appendingPathComponent("AppIcon-preview.png"))

let conversion = Process()
conversion.executableURL = URL(fileURLWithPath: "/usr/bin/iconutil")
conversion.arguments = ["-c", "icns", iconset.path, "-o", assets.appendingPathComponent("AppIcon.icns").path]
try conversion.run()
conversion.waitUntilExit()
guard conversion.terminationStatus == 0 else {
    throw NSError(domain: "BlinkyIcon", code: 3,
                  userInfo: [NSLocalizedDescriptionKey: "iconutil could not create AppIcon.icns."])
}
print("Generated Assets/AppIcon.icns and Assets/AppIcon-preview.png")
