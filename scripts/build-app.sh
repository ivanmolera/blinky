#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

swift build -c release
BIN_DIR="$(swift build -c release --show-bin-path)"
APP_PATH="$ROOT/dist/Blinky.app"
CONTENTS="$APP_PATH/Contents"

if [[ ! -d "$BIN_DIR/Blinky_Blinky.bundle" ]]; then
  echo "No se ha generado el paquete de recursos Blinky_Blinky.bundle." >&2
  exit 1
fi

mkdir -p "$ROOT/dist"
rm -rf "$APP_PATH"
mkdir -p "$CONTENTS/MacOS" "$CONTENTS/Resources"
cp "$BIN_DIR/Blinky" "$CONTENTS/MacOS/Blinky"
chmod 755 "$CONTENTS/MacOS/Blinky"
cp -R "$BIN_DIR/Blinky_Blinky.bundle" "$CONTENTS/Resources/"

cat > "$CONTENTS/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key><string>es</string>
  <key>CFBundleExecutable</key><string>Blinky</string>
  <key>CFBundleIdentifier</key><string>com.ivanmolera.blinky</string>
  <key>CFBundleName</key><string>Blinky</string>
  <key>CFBundleDisplayName</key><string>Blinky</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>0.1.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

# La app de desarrollo reutiliza el entorno local; no distribuye Python.
if [[ -x "$ROOT/.venv/bin/python3" ]]; then
  plutil -insert BlinkyPythonPath -string "$ROOT/.venv/bin/python3" \
    "$CONTENTS/Info.plist"
fi

plutil -lint "$CONTENTS/Info.plist"
if command -v codesign >/dev/null 2>&1; then
  codesign --force --sign - "$APP_PATH"
fi

echo "App creada: $APP_PATH"
echo "Abre con: open \"$APP_PATH\""
