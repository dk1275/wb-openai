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
