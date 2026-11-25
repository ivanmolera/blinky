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

func periwinkle(red: CGFloat, green: CGFloat, blue: CGFloat) -> NSColor {
    NSColor(displayP3Red: red / 255, green: green / 255, blue: blue / 255, alpha: 1)
}

func drawIcon() {
    let tile = NSBezierPath(roundedRect: NSRect(x: 72, y: 72, width: 880, height: 880),
                            xRadius: 248, yRadius: 248)
    let light = periwinkle(red: 152, green: 162, blue: 255)
    let deep = periwinkle(red: 112, green: 124, blue: 247)
    NSGradient(colorsAndLocations: (deep, 0), (deep, 0.28), (light, 1))?.draw(in: tile, angle: 90)

    // A one-piece camera with a circular cutout makes a distinct mark at Dock sizes.
    let camera = NSBezierPath()
    camera.move(to: NSPoint(x: 320, y: 342))
    camera.line(to: NSPoint(x: 557, y: 342))
    camera.curve(to: NSPoint(x: 623, y: 407),
                 controlPoint1: NSPoint(x: 598, y: 342), controlPoint2: NSPoint(x: 623, y: 367))
    camera.line(to: NSPoint(x: 623, y: 445))
    camera.line(to: NSPoint(x: 731, y: 389))
    camera.curve(to: NSPoint(x: 772, y: 414),
                 controlPoint1: NSPoint(x: 749, y: 380), controlPoint2: NSPoint(x: 772, y: 392))
    camera.line(to: NSPoint(x: 772, y: 610))
    camera.curve(to: NSPoint(x: 731, y: 635),
                 controlPoint1: NSPoint(x: 772, y: 632), controlPoint2: NSPoint(x: 749, y: 644))
    camera.line(to: NSPoint(x: 623, y: 579))
    camera.line(to: NSPoint(x: 623, y: 617))
    camera.curve(to: NSPoint(x: 557, y: 682),
                 controlPoint1: NSPoint(x: 623, y: 657), controlPoint2: NSPoint(x: 598, y: 682))
    camera.line(to: NSPoint(x: 320, y: 682))
    camera.curve(to: NSPoint(x: 252, y: 614),
                 controlPoint1: NSPoint(x: 279, y: 682), controlPoint2: NSPoint(x: 252, y: 655))
    camera.line(to: NSPoint(x: 252, y: 410))
    camera.curve(to: NSPoint(x: 320, y: 342),
                 controlPoint1: NSPoint(x: 252, y: 369), controlPoint2: NSPoint(x: 279, y: 342))
    camera.close()
    camera.appendOval(in: NSRect(x: 378, y: 452, width: 120, height: 120))
    camera.windingRule = .evenOdd
    NSColor.white.setFill()
    camera.fill()
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
