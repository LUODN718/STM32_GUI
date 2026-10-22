#!/bin/bash
# 生成 macOS 应用包「MCUNet 图片推理.app」
# 用法：bash scripts/make_app.sh [输出目录]   （默认 ~/Applications）
#      PYTHON_BIN=/path/to/python bash scripts/make_app.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_NAME="${APP_NAME:-MCUNet 图片推理.app}"
DEST="${1:-$HOME/Applications}"
APP="$DEST/$APP_NAME"
PYTHON_BIN="${PYTHON_BIN:-/opt/miniconda3/envs/STM32-GUI/bin/python}"
VERSION="2.1"

echo "==> 源目录：$ROOT"
echo "==> 输出：$APP"
echo "==> 解释器：$PYTHON_BIN"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

# 图标：由 assets/MCUNet.iconset 生成 icns
if [ -d "$ROOT/assets/MCUNet.iconset" ]; then
    iconutil -c icns "$ROOT/assets/MCUNet.iconset" \
        -o "$APP/Contents/Resources/MCUNetStudio.icns"
else
    echo "!! 未找到 assets/MCUNet.iconset，跳过图标" >&2
fi

cp "$ROOT/main.py" "$APP/Contents/Resources/main.py"

cat > "$APP/Contents/MacOS/MCUNet" <<LAUNCHER
#!/bin/zsh
APP_PYTHON="$PYTHON_BIN"
APP_SOURCE="\${0:A:h:h}/Resources"
APP_LOG_DIR="\$HOME/Library/Logs/MCUNet"
if [[ ! -x "\$APP_PYTHON" || ! -f "\$APP_SOURCE/main.py" ]]; then
    /usr/bin/osascript -e 'display alert "MCUNet 图片推理无法启动" message "找不到 Python 解释器或 main.py，请用 PYTHON_BIN 指定解释器后重新打包。" as critical'
    exit 1
fi
mkdir -p "\$APP_LOG_DIR"
cd "\$APP_SOURCE" || exit 1
exec "\$APP_PYTHON" "\$APP_SOURCE/main.py" >> "\$APP_LOG_DIR/launcher.log" 2>&1
LAUNCHER
chmod +x "$APP/Contents/MacOS/MCUNet"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleDevelopmentRegion</key><string>zh_CN</string>
    <key>CFBundleDisplayName</key><string>MCUNet 图片推理</string>
    <key>CFBundleExecutable</key><string>MCUNet</string>
    <key>CFBundleIconFile</key><string>MCUNetStudio.icns</string>
    <key>CFBundleIdentifier</key><string>local.lljk.mcunet-image-inference</string>
    <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
    <key>CFBundleName</key><string>MCUNet 图片推理</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>$VERSION</string>
    <key>CFBundleVersion</key><string>$VERSION</string>
    <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

plutil -lint "$APP/Contents/Info.plist"
echo "==> 完成：$APP"
