#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
BLENDER_BIN="$(python3 "$PROJECT_DIR/tools/find_blender.py")"
cd "$PROJECT_DIR"
PARAMS="designs/current.json"
if [[ $# -gt 0 && "$1" != --* ]]; then PARAMS="$1"; shift; fi
mkdir -p "$PROJECT_DIR/out/logs"
LOG_PATH="$PROJECT_DIR/out/logs/build-$(date +%Y%m%d-%H%M%S)-$$.log"
"$BLENDER_BIN" -b --factory-startup --disable-autoexec --python-exit-code 1 \
  --python "$PROJECT_DIR/engine/build.py" -- --params "$PARAMS" "$@" 2>&1 | tee "$LOG_PATH"
