#!/usr/bin/env bash
# All heavy runs of the M0.6a spike (80 x 80 x 40 mm BCC lattice block at 0.25 mm voxels, shoe-box
# level_set scaling, 0.25 mm per-block stitching test). Held under .scratch/heavy.lock (shared
# with other agents; waits/retries if taken). Every run is under the in-process RssWatchdog
# (6 GB, parent + workers) where the script has one, and under the out-of-process guard
# guarded.py (6 GB over the whole process tree, which also works while level_set holds the GIL).
#   bash spikes/m0_6a/heavy.sh
set -uo pipefail
cd "$(dirname "$0")/../.."
mkdir -p .scratch/m0_6a .scratch/interpose
PY=${PY:-.venv/bin/python}
OUT=.scratch/m0_6a
until mkdir .scratch/heavy.lock 2>/dev/null; do echo "heavy.lock held, waiting 30 s ..."; sleep 30; done
trap 'rmdir .scratch/heavy.lock' EXIT
echo "lock acquired $(date +%T)"
SHIM=DYLD_INSERT_LIBRARIES=.scratch/interpose/libonecpu.dylib
[ -f .scratch/interpose/libonecpu.dylib ] || clang -dynamiclib -o .scratch/interpose/libonecpu.dylib spikes/m0_6a/onecpu.c
G="$PY spikes/m0_6a/guarded.py --limit-gb 6 --out $OUT/guard.jsonl --"
L="spikes/m0_6a/b_levelset.py --shape lattice --callback lookup --watchdog-gb 6 --no-vertex-check"

echo "== ref: scikit-image marching_cubes per block"
$G uv run --with scikit-image python spikes/m0_6a/ref_skimage.py --watchdog-gb 6 | tee $OUT/ref_skimage.json
echo "== B lattice lookup edge 0.25 run1 (default threads)"
$G $PY $L --edge 0.25 | tee $OUT/lattice_run1.json
echo "== B lattice lookup edge 0.25 run2 (default threads)"
$G $PY $L --edge 0.25 | tee $OUT/lattice_run2.json
echo "== B lattice lookup edge 0.25, 1 TBB thread"
$G env $SHIM ONECPU_N=1 $PY $L --edge 0.25 | tee $OUT/lattice_threads1.json
echo "== B lattice lookup edge 0.25, 4 TBB threads"
$G env $SHIM ONECPU_N=4 $PY $L --edge 0.25 | tee $OUT/lattice_threads4.json
echo "== B lattice lookup edge 0.30"
$G $PY $L --edge 0.30 | tee $OUT/lattice_e030.json
echo "== B lattice lookup edge 0.50"
$G $PY $L --edge 0.50 | tee $OUT/lattice_e050.json
echo "== B lattice, real graph kernel as callback, edge 0.25"
$G $PY spikes/m0_6a/b_levelset.py --shape lattice --callback program --watchdog-gb 6 --no-vertex-check --edge 0.25 | tee $OUT/lattice_program.json
echo "== B shoe-sized box"
for e in 0.5 0.35 0.25; do $G $PY spikes/m0_6a/b_shoebox.py --edge $e | tee -a $OUT/shoebox.json; done
echo "== B cross-block, sphere voxel 0.25 edge 0.25 (dyadic)"
$G $PY spikes/m0_6a/b_crossblock.py --voxel 0.25 --edge 0.25 | tee $OUT/crossblock_h025.json
echo "done $(date +%T)"
