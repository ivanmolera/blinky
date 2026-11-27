#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -f "$ROOT/Assets/AppIcon.icns" ]]; then
  swift "$ROOT/scripts/generate-icon.swift"
fi

swift build -c release
BIN_DIR="$(swift build -c release --show-bin-path)"
OUTPUT_DIR="${BLINKY_APP_OUTPUT_DIR:-$ROOT/dist}"
APP_PATH="$OUTPUT_DIR/Blinky.app"
CONTENTS="$APP_PATH/Contents"

if [[ ! -d "$BIN_DIR/Blinky_Blinky.bundle" ]]; then
  echo "No se ha generado el paquete de recursos Blinky_Blinky.bundle." >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR"
rm -rf "$APP_PATH"
mkdir -p "$CONTENTS/MacOS" "$CONTENTS/Resources"
cp "$BIN_DIR/Blinky" "$CONTENTS/MacOS/Blinky"
chmod 755 "$CONTENTS/MacOS/Blinky"
cp -R "$BIN_DIR/Blinky_Blinky.bundle" "$CONTENTS/Resources/"
cp -R "$BIN_DIR/Blinky_BlinkyCore.bundle" "$CONTENTS/Resources/"
cp "$ROOT/Assets/AppIcon.icns" "$CONTENTS/Resources/AppIcon.icns"

cat > "$CONTENTS/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key><string>en</string>
  <key>CFBundleLocalizations</key><array><string>en</string><string>es</string><string>ca</string></array>
  <key>CFBundleExecutable</key><string>Blinky</string>
  <key>CFBundleIdentifier</key><string>com.ivanmolera.blinky</string>
  <key>CFBundleName</key><string>Blinky</string>
  <key>CFBundleDisplayName</key><string>Blinky</string>
  <key>CFBundleIconFile</key><string>AppIcon.icns</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>0.4.0</string>
  <key>CFBundleVersion</key><string>9</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSAppTransportSecurity</key>
  <dict><key>NSAllowsLocalNetworking</key><true/></dict>
</dict>
</plist>
PLIST

SIGN_ARGS=(--force --sign "${BLINKY_SIGN_IDENTITY:--}")
if [[ -n "${BLINKY_SIGN_IDENTITY:-}" ]]; then
  SIGN_ARGS+=(--options runtime --timestamp)
fi

if [[ "${BLINKY_BUNDLE_RUNTIME:-0}" == 1 ]]; then
  ARCH="$(lipo -archs "$CONTENTS/MacOS/Blinky")"
  if [[ "$ARCH" != "$(uname -m)" ]]; then
    echo "Build the bundled runtime on a Mac running the same architecture as Blinky." >&2
    exit 1
  fi
  PYTHON="$CONTENTS/Resources/Python"
  BLINKY_TARGET_ARCH="$ARCH" "$ROOT/scripts/package-python.sh" "$PYTHON"
  "$PYTHON/bin/python3" -I -B "$ROOT/scripts/layout-python-runtime.py" "$CONTENTS"
  FFMPEG="$CONTENTS/Resources/Licenses/FFmpeg"
  "$ROOT/scripts/package-ffmpeg.sh" "$FFMPEG" "${BLINKY_FFMPEG_SOURCE_DIR:-}"
  mv "$FFMPEG/ffmpeg" "$CONTENTS/Helpers/ffmpeg"
  while IFS= read -r -d '' executable; do
    codesign "${SIGN_ARGS[@]}" "$executable"
  done < <(find "$CONTENTS/Helpers" "$CONTENTS/Frameworks" -type f -print0)
  plutil -insert BlinkyBundlesRuntime -bool YES "$CONTENTS/Info.plist"
elif [[ "${BLINKY_DISTRIBUTION:-0}" == 1 ]]; then
  plutil -insert BlinkyManagesPython -bool YES "$CONTENTS/Info.plist"
elif [[ -x "$ROOT/.venv/bin/python3" ]]; then
  plutil -insert BlinkyPythonPath -string "$ROOT/.venv/bin/python3" \
    "$CONTENTS/Info.plist"
fi

plutil -lint "$CONTENTS/Info.plist"
codesign "${SIGN_ARGS[@]}" "$APP_PATH"

echo "App creada: $APP_PATH"
echo "Abre con: open \"$APP_PATH\""
