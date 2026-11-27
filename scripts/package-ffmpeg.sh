#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  printf 'Usage: %s <FFmpeg bundle directory> [corresponding source directory]\n' "$0" >&2
  exit 1
fi

PACKAGE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DESTINATION="$1"
SOURCE_DESTINATION="${2:-}"
CACHE="${BLINKY_PACKAGE_CACHE:-$PACKAGE_ROOT/.build/package-downloads}"
VERSION=9.1
ARCHIVE="ffmpeg-$VERSION.tar.xz"
SOURCE_URL="https://ffmpeg.org/releases/$ARCHIVE"
# Verified against FFmpeg release signing key FCF986EA15E6E293A5644F10B4322F04D67658D8.
SHA256=1a4d394ae63d190a526cd052eb4415c156b5c5e2df37efab41a7de0b8fde2433
ARCH="$(uname -m)"
case "$ARCH" in
  arm64|x86_64) ;;
  *) printf 'Unsupported FFmpeg build architecture: %s\n' "$ARCH" >&2; exit 1 ;;
esac

mkdir -p "$CACHE"
CACHE="$(cd "$CACHE" && pwd)"
WORK="$(mktemp -d "$CACHE/ffmpeg-build.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

valid_archive() {
  [[ -f "$1" ]] && printf '%s  %s\n' "$SHA256" "$1" | shasum -a 256 --check --status
}

if ! valid_archive "$CACHE/$ARCHIVE"; then
  curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
    --retry 2 --connect-timeout 15 --max-time 180 "$SOURCE_URL" --output "$WORK/$ARCHIVE"
  if ! valid_archive "$WORK/$ARCHIVE"; then
    printf 'FFmpeg source checksum verification failed.\n' >&2
    exit 1
  fi
  mv -f "$WORK/$ARCHIVE" "$CACHE/$ARCHIVE"
fi

tar -xJf "$CACHE/$ARCHIVE" -C "$WORK"
SOURCE="$WORK/ffmpeg-$VERSION"
mkdir "$WORK/build"

# Blinky feeds MPEG-TS over stdin and serves the resulting local HLS files.
# FFmpeg runs as a separate process with no network protocols or external libraries.
CONFIGURE=(
  --cc=/usr/bin/clang
  --arch="$ARCH"
  --target-os=darwin
  --extra-cflags=-mmacosx-version-min=14.0
  --extra-ldflags=-mmacosx-version-min=14.0
  --disable-autodetect --disable-everything --disable-debug --disable-doc --disable-network
  --disable-shared --enable-static --disable-x86asm --disable-ffprobe --disable-ffplay --disable-avdevice
  --enable-demuxer=mpegts,aac,ac3,eac3,loas,mp3,h264,hevc,mpegvideo --enable-muxer=hls --enable-protocol=file,pipe
  --enable-bsf=extract_extradata
  --enable-encoder=aac --enable-decoder=h264,hevc,mpeg2video,aac,aac_latm,ac3,eac3,mp2,mp3,opus,pcm_alaw,pcm_mulaw
  --enable-parser=h264,hevc,mpegvideo,aac,aac_latm,ac3,mpegaudio,opus --enable-filter=aresample
)
(
  cd "$WORK/build"
  "$SOURCE/configure" "${CONFIGURE[@]}" "--sysroot=$(xcrun --sdk macosx --show-sdk-path)"
  make -j"$(sysctl -n hw.ncpu 2>/dev/null || printf '4')" ffmpeg
)
BINARY="$WORK/build/ffmpeg"
LOAD_COMMANDS="$(otool -L "$BINARY")"
NON_SYSTEM_LIBS="$(printf '%s\n' "$LOAD_COMMANDS" | awk 'NR > 1 && $1 !~ /^\/(usr\/lib|System\/Library)\// { print $1 }')"
if [[ "$(lipo -archs "$BINARY")" != "$ARCH" || -n "$NON_SYSTEM_LIBS" ]]; then
  printf 'FFmpeg has an unexpected architecture or non-system libraries.\n%s\n' "$LOAD_COMMANDS" >&2
  exit 1
fi

mkdir -p "$DESTINATION"
install -m 755 "$BINARY" "$DESTINATION/ffmpeg"
install -m 644 "$SOURCE/COPYING.LGPLv2.1" "$DESTINATION/COPYING.LGPLv2.1"
install -m 644 "$SOURCE/LICENSE.md" "$DESTINATION/LICENSE.md"
{
  printf 'FFmpeg %s\n\n' "$VERSION"
  printf 'Blinky runs FFmpeg as a separate program under the GNU Lesser General Public License, version 2.1 or later.\n\n'
  printf 'Corresponding unmodified source: %s\n' "$SOURCE_URL"
  printf 'SHA-256: %s\n\n' "$SHA256"
  printf 'To rebuild, extract the source archive on macOS and run:\n\n'
  printf './configure'
  printf ' %q' "${CONFIGURE[@]}"
  printf ' --sysroot="$(xcrun --sdk macosx --show-sdk-path)"\nmake ffmpeg\n'
} > "$DESTINATION/BUILD.txt"

if [[ -n "$SOURCE_DESTINATION" ]]; then
  mkdir -p "$SOURCE_DESTINATION"
  install -m 644 "$CACHE/$ARCHIVE" "$SOURCE_DESTINATION/$ARCHIVE"
  install -m 644 "$DESTINATION/BUILD.txt" "$SOURCE_DESTINATION/ffmpeg-$VERSION-build.txt"
fi
