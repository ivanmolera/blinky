#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

BLINKY_BOOTSTRAP_PYTHON="${BLINKY_BOOTSTRAP_PYTHON:-}"
if [[ -z "$BLINKY_BOOTSTRAP_PYTHON" ]]; then
  for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      BLINKY_BOOTSTRAP_PYTHON="$(command -v "$candidate")"
      break
    fi
  done
fi

if [[ -z "$BLINKY_BOOTSTRAP_PYTHON" || ! -x "$BLINKY_BOOTSTRAP_PYTHON" ]]; then
  echo "Necesitas Python 3.10 o posterior para conectar con Blink." >&2
  exit 1
fi

if ! "$BLINKY_BOOTSTRAP_PYTHON" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
  echo "Python 3.10 o posterior es necesario. El comando python3 usa una versión anterior." >&2
  exit 1
fi

if [[ ! -x "$ROOT/.venv/bin/python3" ]]; then
  "$BLINKY_BOOTSTRAP_PYTHON" -m venv "$ROOT/.venv"
fi

"$ROOT/.venv/bin/python3" -m pip --isolated install --index-url https://pypi.org/simple --disable-pip-version-check \
  -r "$ROOT/Sources/Blinky/Bridge/requirements.txt"

echo "Entorno preparado. Ejecuta ./scripts/run.sh para abrir Blinky."
