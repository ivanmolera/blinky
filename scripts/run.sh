#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -z "${BLINKY_PYTHON:-}" && -x "$ROOT/.venv/bin/python3" ]]; then
  export BLINKY_PYTHON="$ROOT/.venv/bin/python3"
fi
export BLINKY_BRIDGE_PATH="${BLINKY_BRIDGE_PATH:-$ROOT/Sources/Blinky/Bridge/bridge.py}"

exec swift run Blinky "$@"
