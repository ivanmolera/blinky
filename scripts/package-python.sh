#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ $# -ne 1 || -z "$1" ]]; then
  echo "Usage: package-python.sh DESTINATION" >&2
  exit 1
fi

PYTHON_VERSION=3.13.18
PYTHON_RELEASE=20261124
UPSTREAM_SOURCE=48d61fcd7d85d7013b95576f62f779008c316a63
UPSTREAM_ARCHIVE_SHA256=ca344c08ea7717d1b81fc915f4545098e0c60483137dbc47ce3c3588389e53dc
ARCH="${BLINKY_TARGET_ARCH:-$(uname -m)}"
# Digests from the immutable upstream release's asset metadata.
case "$ARCH" in
  arm64)
    TARGET=aarch64-apple-darwin
    SHA256=296f0903cccda15a5c9d3d8393cd2204718aeffd9b095a00f812d75c7b1c0967
    ;;
  x86_64)
    TARGET=x86_64-apple-darwin
    SHA256=bb16e167ec9e38368118ce3e43ba2c7852fea2cdba16e1852555a8997747f78a
    ;;
  *)
    echo "Blinky supports arm64 and x86_64 Python runtimes on macOS." >&2
    exit 1
    ;;
esac

PARENT="$(dirname "$1")"
mkdir -p "$PARENT"
DESTINATION="$(cd "$PARENT" && pwd)/$(basename "$1")"
if [[ -e "$DESTINATION" || -L "$DESTINATION" ]]; then
  echo "Python destination already exists: $DESTINATION" >&2
  exit 1
fi

STAGING="$(mktemp -d "$(dirname "$DESTINATION")/.blinky-python.XXXXXX")"
trap 'rm -rf "$STAGING"' EXIT
ARCHIVE="$STAGING/cpython.tar.gz"
URL="https://github.com/astral-sh/python-build-standalone/releases/download/$PYTHON_RELEASE/cpython-$PYTHON_VERSION%2B$PYTHON_RELEASE-$TARGET-install_only_stripped.tar.gz"
curl --fail --silent --show-error --location --retry 2 --connect-timeout 20 --max-time 180 \
  --proto '=https' --proto-redir '=https' --output "$ARCHIVE" "$URL"
printf '%s  %s\n' "$SHA256" "$ARCHIVE" | shasum -a 256 -c -
tar -xzf "$ARCHIVE" -C "$STAGING"

RUNTIME="$STAGING/python"
SOURCE_ARCHIVE="$STAGING/upstream-source.tar.gz"
curl --fail --silent --show-error --location --retry 2 --connect-timeout 20 --max-time 120 \
  --proto '=https' --proto-redir '=https' --output "$SOURCE_ARCHIVE" \
  "https://codeload.github.com/astral-sh/python-build-standalone/tar.gz/$UPSTREAM_SOURCE"
printf '%s  %s\n' "$UPSTREAM_ARCHIVE_SHA256" "$SOURCE_ARCHIVE" | shasum -a 256 -c -
tar -xzf "$SOURCE_ARCHIVE" -C "$STAGING"
mkdir "$RUNTIME/licenses"
cp "$STAGING/python-build-standalone-$UPSTREAM_SOURCE"/LICENSE* "$RUNTIME/licenses/"

PYTHON="$RUNTIME/bin/python3"
PIP_CERTIFICATES="$("$PYTHON" -I -B -c 'from pip._vendor import certifi; print(certifi.where())')"
REQUIREMENTS="$ROOT/Sources/Blinky/Bridge/requirements.txt"
WHEELS="$STAGING/wheels"
PIP=("$PYTHON" -I -B -m pip --isolated --no-input --disable-pip-version-check)
export PIP_CONFIG_FILE=/dev/null
# Resolve wheels against Blinky's minimum system version, independent of the build Mac.
"${PIP[@]}" --timeout 20 --retries 2 --cert "$PIP_CERTIFICATES" download \
  --only-binary=:all: --platform "macosx_14_0_$ARCH" --no-cache-dir --index-url https://pypi.org/simple \
  --dest "$WHEELS" -r "$REQUIREMENTS"
"${PIP[@]}" install --only-binary=:all: --no-compile --no-index --find-links "$WHEELS" -r "$REQUIREMENTS"
"${PIP[@]}" check

SITE_PACKAGES="$RUNTIME/lib/python3.13/site-packages"
ln -s lib/python3.13/site-packages/certifi/cacert.pem "$RUNTIME/cacert.pem"
# The installed dependencies retain their wheel metadata and licenses.
rm -rf "$SITE_PACKAGES/pip" "$SITE_PACKAGES"/pip-*.dist-info "$RUNTIME/lib/python3.13/ensurepip"
# pip's generated console scripts can contain absolute build paths.
find "$RUNTIME/bin" -mindepth 1 -maxdepth 1 \
  ! -name python ! -name python3 ! -name python3.13 -delete

RELOCATED="$STAGING/Relocated Python"
mv "$RUNTIME" "$RELOCATED"
"$RELOCATED/bin/python3" -I -B - "$RELOCATED" "$PYTHON_VERSION" "$PYTHON_RELEASE" "$UPSTREAM_SOURCE" <<'PYTHON'
from importlib.metadata import distributions, version
from pathlib import Path
import ssl
import sys

import aiohttp.web
import blinkpy.auth
import blinkpy.blinkpy
import certifi

root = Path(sys.argv[1]).resolve()
if Path(sys.prefix).resolve() != root:
    raise SystemExit("Embedded Python cannot locate its relocated standard library.")
if ".".join(map(str, sys.version_info[:3])) != sys.argv[2] or version("blinkpy") != "0.25.9":
    raise SystemExit("Unexpected Python or blinkpy version.")
for path in root.rglob("*"):
    if path.is_symlink() and not path.resolve(strict=True).is_relative_to(root):
        raise SystemExit(f"Embedded Python symlink escapes its runtime: {path.relative_to(root)}")
if (root / "cacert.pem").resolve(strict=True) != Path(certifi.where()).resolve(strict=True):
    raise SystemExit("Embedded Python cannot locate its certificate bundle.")
ssl.create_default_context(cafile=str(root / "cacert.pem"))
packages = sorted((item.metadata["Name"], item.version) for item in distributions())
sources = [
    f"CPython {sys.argv[2]} release: https://www.python.org/downloads/release/python-{sys.argv[2].replace('.', '')}/",
    f"Python build: https://github.com/astral-sh/python-build-standalone/releases/tag/{sys.argv[3]}",
    f"Build source and licenses: https://github.com/astral-sh/python-build-standalone/tree/{sys.argv[4]}",
    "",
    "Python package source downloads:",
    *(f"{name} {release}: https://pypi.org/project/{name}/{release}/#files" for name, release in packages),
]
(root / "licenses" / "SOURCES.txt").write_text("\n".join(sources) + "\n", encoding="utf-8")
print(f"Verified relocated Python {sys.argv[2]} and blinkpy {version('blinkpy')}.")
PYTHON
mv "$RELOCATED" "$DESTINATION"
