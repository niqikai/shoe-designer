#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
BLENDER_BIN="$(python3 "$PROJECT_DIR/tools/find_blender.py")"
cd "$PROJECT_DIR"
exec "$BLENDER_BIN" -b --factory-startup --disable-autoexec --python-exit-code 1 \
  --python "$PROJECT_DIR/engine/inspect_last.py" -- "$@"
