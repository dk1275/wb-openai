// make-icon.swift — 生成 WBOpenAI 的 App 图标（1024×1024 PNG）
//
// 深蓝渐变圆角方块 + 白色闪电，和菜单栏的 ⚡ 呼应。
// 用代码画而不是用图片，好处是尺寸/配色随时可调，也能进构建流程。

import AppKit
import Foundation

let S: CGFloat = 1024
let out = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "icon-1024.png"

// 精确控制像素尺寸（避免 Retina 下 tiffRepresentation 翻倍）
guard let rep = NSBitmapImageRep(bitmapDataPlanes: nil,
                                 pixelsWide: Int(S), pixelsHigh: Int(S),
                                 bitsPerSample: 8, samplesPerPixel: 4,
                                 hasAlpha: true, isPlanar: false,
                                 colorSpaceName: .deviceRGB,
                                 bytesPerRow: 0, bitsPerPixel: 0),
      let gctx = NSGraphicsContext(bitmapImageRep: rep) else {
    FileHandle.standardError.write("无法创建绘图上下文\n".data(using: .utf8)!)
    exit(1)
}

NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = gctx
guard let ctx = gctx.cgContext as CGContext? else { exit(1) }

// ---- 圆角方块（macOS 图标比例 ≈ 22.6%）----
let radius: CGFloat = S * 0.2266
let body = CGRect(x: 0, y: 0, width: S, height: S)
let rounded = CGPath(roundedRect: body, cornerWidth: radius, cornerHeight: radius, transform: nil)

// 外投影
ctx.saveGState()
ctx.setShadow(offset: CGSize(width: 0, height: -S * 0.010),
              blur: S * 0.028,
              color: NSColor.black.withAlphaComponent(0.32).cgColor)
ctx.addPath(rounded)
ctx.setFillColor(NSColor.black.cgColor)
ctx.fillPath()
ctx.restoreGState()

// ---- 渐变底 ----
ctx.saveGState()
ctx.addPath(rounded)
ctx.clip()

let top    = NSColor(srgbRed: 0.24, green: 0.42, blue: 0.96, alpha: 1).cgColor  // #3D6BF5
let bottom = NSColor(srgbRed: 0.03, green: 0.40, blue: 0.78, alpha: 1).cgColor  // #0866C7
let grad = CGGradient(colorsSpace: CGColorSpaceCreateDeviceRGB(),
                      colors: [top, bottom] as CFArray,
                      locations: [0, 1])!
ctx.drawLinearGradient(grad,
                       start: CGPoint(x: 0, y: S),
                       end: CGPoint(x: S, y: 0),
                       options: [])

// 左上柔光会引入大面积低对比度渐变，缩图后容易出现环状条纹、PNG 也压不下去，
// 所以这里只用一条线性渐变，保持干净。
ctx.restoreGState()

// ---- 居中闪电 ----
let symbolSize = S * 0.54
let cfg = NSImage.SymbolConfiguration(paletteColors: [.white])
    .applying(NSImage.SymbolConfiguration(pointSize: symbolSize, weight: .bold))

if let sym = NSImage(systemSymbolName: "bolt.fill", accessibilityDescription: nil)?
        .withSymbolConfiguration(cfg) {
    let w = sym.size.width
    let h = sym.size.height
    // 闪电视觉重心偏上一点，稍微下移让它看起来居中
    let rect = CGRect(x: (S - w) / 2,
                      y: (S - h) / 2 - S * 0.012,
                      width: w, height: h)
    ctx.saveGState()
    ctx.setShadow(offset: CGSize(width: 0, height: -S * 0.004),
                  blur: S * 0.02,
                  color: NSColor.black.withAlphaComponent(0.25).cgColor)
    sym.draw(in: rect)
    ctx.restoreGState()
} else {
    FileHandle.standardError.write("SF Symbol 渲染失败，退化为纯色块\n".data(using: .utf8)!)
}

NSGraphicsContext.restoreGraphicsState()

guard let png = rep.representation(using: .png, properties: [:]) else { exit(1) }
try png.write(to: URL(fileURLWithPath: out))
print("已生成 \(out)  \(Int(S))×\(Int(S))  \(png.count / 1024) KB")
