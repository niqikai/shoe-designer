#!/usr/bin/env bash
# Heavy: save the lattice meshes (level_set edge 0.5 / 0.30) and time Manifold(Mesh64) on them.
set -uo pipefail
cd "$(dirname "$0")/../.."
PY=${PY:-.venv/bin/python}
OUT=.scratch/m0_6a
until mkdir .scratch/heavy.lock 2>/dev/null; do echo "heavy.lock held, waiting 30 s ..."; sleep 30; done
trap 'rmdir .scratch/heavy.lock' EXIT
G="$PY spikes/m0_6a/guarded.py --limit-gb 6 --out $OUT/guard2.jsonl --"
for e in 0.50 0.30; do
  tag=${e/./}
  $G $PY spikes/m0_6a/b_levelset.py --shape lattice --edge $e --callback lookup --watchdog-gb 6 --no-vertex-check --save $OUT/lattice_e$tag.npz > /dev/null
done
ls -la $OUT/*.npz
for tag in 050 030; do $G $PY spikes/m0_6a/b_validate.py $OUT/lattice_e$tag.npz | tee -a $OUT/validate.json; done
