#!/bin/bash
# build-app.sh — 编译并组装 WBOpenAI.app（macOS 菜单栏应用）
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"          # wb-openai/menubar
SRC="$(dirname "$HERE")"                        # wb-openai
BUILD="${HERE}/build"
APP="${BUILD}/WBOpenAI.app"
BIN="${APP}/Contents/MacOS/WBOpenAI"
RES="${APP}/Contents/Resources/wb-openai"

echo "== 清理 =="
rm -rf "${BUILD}"
mkdir -p "${APP}/Contents/MacOS" "${RES}"

echo "== 编译（swiftc）=="
swiftc -O -o "${BIN}" "${HERE}/main.swift" -framework AppKit
echo "   二进制: $(du -h "${BIN}" | cut -f1)"

echo "== 生成图标 =="
swiftc -O -o "${BUILD}/make-icon" "${HERE}/make-icon.swift" -framework AppKit
"${BUILD}/make-icon" "${BUILD}/icon-1024.png"

# 渐变图 PNG 体积很大（近 1 MB）。有 Pillow 就量化到 256 色，能压到 ~15 KB 且肉眼无差；
# 没有就原样用，只是包大一点，不影响功能。
if python3 -c "import PIL" >/dev/null 2>&1; then
  python3 - "${BUILD}/icon-1024.png" <<'PY'
import os, sys
from PIL import Image
p = sys.argv[1]
im = Image.open(p).convert("RGBA")
before = os.path.getsize(p)
im.quantize(colors=256, method=Image.FASTOCTREE,
            dither=Image.FLOYDSTEINBERG).save(p, optimize=True)
print(f"   量化: {before//1024} KB -> {os.path.getsize(p)//1024} KB")
PY
else
  echo "   （未装 Pillow，跳过量化）"
fi

ICONSET="${BUILD}/AppIcon.iconset"
mkdir -p "${ICONSET}"
gen() { sips -z "$1" "$1" "${BUILD}/icon-1024.png" --out "${ICONSET}/$2" >/dev/null 2>&1; }
gen 16   icon_16x16.png
gen 32   icon_16x16@2x.png
gen 32   icon_32x32.png
gen 64   icon_32x32@2x.png
gen 128  icon_128x128.png
gen 256  icon_128x128@2x.png
gen 256  icon_256x256.png
gen 512  icon_256x256@2x.png
gen 512  icon_512x512.png
gen 1024 icon_512x512@2x.png
iconutil -c icns "${ICONSET}" -o "${APP}/Contents/Resources/AppIcon.icns"
echo "   icns: $(du -h "${APP}/Contents/Resources/AppIcon.icns" | cut -f1)"

echo "== 拷贝脚本到 Resources =="
for f in converter.py wb_decode.py wb_panel.py bootstrap.py desensitize.py \
         responses_adapter.py turing_helper.js setup-dsh.py check-dsh.py \
         start.sh start-panel.bat .gitignore LICENSE README.md "启动面板.command"; do
  if [ -f "${SRC}/${f}" ]; then
    cp "${SRC}/${f}" "${RES}/"
  else
    echo "   ! 缺少 ${f}，跳过"
  fi
done
chmod +x "${RES}/启动面板.command" "${RES}/start.sh" 2>/dev/null || true
echo "   已放入 $(ls -1 "${RES}" | wc -l | tr -d ' ') 个文件"

echo "== 写 Info.plist =="
cat > "${APP}/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>WBOpenAI</string>
  <key>CFBundleDisplayName</key><string>WB-OpenAI 网关</string>
  <key>CFBundleIdentifier</key><string>cn.wbopenai.menubar</string>
  <key>CFBundleExecutable</key><string>WBOpenAI</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>LSUIElement</key><true/>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSSupportsAutomaticGraphicsSwitching</key><true/>
</dict>
</plist>
PLIST

printf 'APPL????' > "${APP}/Contents/PkgInfo"

echo "== Ad-hoc 签名 =="
codesign --force --deep --sign - "${APP}" 2>&1 | sed 's/^/   /' || echo "   签名失败（不影响本机运行）"

echo "== 校验 =="
codesign -dv "${APP}" 2>&1 | head -3 | sed 's/^/   /'
echo "   体积: $(du -sh "${APP}" | cut -f1)"
echo "   路径: ${APP}"
echo
echo "完成。双击打开：open \"${APP}\""
