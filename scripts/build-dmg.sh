#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -n "${BLINKY_SIGN_IDENTITY:-}" || -n "${BLINKY_NOTARY_PROFILE:-}" ]]; then
  if [[ -z "${BLINKY_SIGN_IDENTITY:-}" || -z "${BLINKY_NOTARY_PROFILE:-}" ]]; then
    echo "Provide both BLINKY_SIGN_IDENTITY and BLINKY_NOTARY_PROFILE for a distributable disk image." >&2
    exit 1
  fi
  SUFFIX=""
else
  SUFFIX="-local"
fi

STAGING="$(mktemp -d "${TMPDIR:-/tmp}/blinky-dmg.XXXXXX")"
trap 'rm -rf "$STAGING"' EXIT

BLINKY_DISTRIBUTION=1 BLINKY_APP_OUTPUT_DIR="$STAGING/image" "$ROOT/scripts/build-app.sh"
APP="$STAGING/image/Blinky.app"
codesign --verify --deep --strict "$APP"
VERSION="$(plutil -extract CFBundleShortVersionString raw "$APP/Contents/Info.plist")"
ARCHS="$(lipo -archs "$APP/Contents/MacOS/Blinky")"
FILENAME="Blinky-$VERSION-${ARCHS// /-}$SUFFIX.dmg"
IMAGE="$STAGING/$FILENAME"

ln -s /Applications "$STAGING/image/Applications"
cat > "$STAGING/image/Read Me.txt" <<'INSTALLATION'
Blinky for Mac

Drag Blinky to Applications, then open it from Applications.

Requirements

- macOS 14 or later.
- Python 3.10 or later to connect a Blink account.
  https://www.python.org/downloads/macos/
- Internet access. Blinky downloads its Python dependencies the first time
  you connect your account.
- FFmpeg for Live View.
INSTALLATION
hdiutil create -volname Blinky -srcfolder "$STAGING/image" -format UDZO "$IMAGE"
hdiutil verify "$IMAGE"

if [[ -n "${BLINKY_SIGN_IDENTITY:-}" ]]; then
  codesign --sign "$BLINKY_SIGN_IDENTITY" --timestamp --identifier com.ivanmolera.blinky.dmg "$IMAGE"
  codesign --verify --strict "$IMAGE"
  xcrun notarytool submit "$IMAGE" --keychain-profile "$BLINKY_NOTARY_PROFILE" --wait --timeout 30m
  xcrun stapler staple "$IMAGE"
  xcrun stapler validate "$IMAGE"
else
  echo "Local test disk image. A public download requires a Developer ID signature and notarization."
fi

mkdir -p "$ROOT/dist"
mv "$IMAGE" "$ROOT/dist/$FILENAME"
echo "Disk image ready at $ROOT/dist/$FILENAME"
