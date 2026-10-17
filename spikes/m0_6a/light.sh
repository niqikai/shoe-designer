#!/usr/bin/env bash
# Light runs (small shapes). Boxscale is the only one that needs the heavy lock (up to 3 GB).
#   bash spikes/m0_6a/light.sh
set -uo pipefail
cd "$(dirname "$0")/../.."
PY=${PY:-.venv/bin/python}
OUT=.scratch/m0_6a
mkdir -p $OUT
echo "== callback overhead";            $PY spikes/m0_6a/b_callback_overhead.py | tee $OUT/callback_overhead.json
echo "== real kernel sample (lattice)"; $PY spikes/m0_6a/b_realkernel_sample.py | tee $OUT/realkernel.json
echo "== GIL probe";                    $PY spikes/m0_6a/b_gil_probe.py | tee $OUT/gil_probe.json
echo "== zero values";                  $PY spikes/m0_6a/b_zero.py | tee $OUT/zero.json
for s in sphere capsules; do
  echo "== determinism $s edge 1.0"; bash spikes/m0_6a/b_determinism.sh $s 1.0 | tee $OUT/determinism_$s.txt
done
echo "== cross-block h=1.0 e=1.0";     $PY spikes/m0_6a/b_crossblock.py --voxel 1.0 --edge 1.0 > $OUT/crossblock_h100.json
echo "== cross-block h=0.3 e=0.3";     $PY spikes/m0_6a/b_crossblock.py --voxel 0.3 --edge 0.3 > $OUT/crossblock_h030.json
echo "== lattice truth";               $PY spikes/m0_6a/lattice_truth.py | tee $OUT/lattice_truth.json
until mkdir .scratch/heavy.lock 2>/dev/null; do echo "heavy.lock held, waiting 30 s ..."; sleep 30; done
trap 'rmdir .scratch/heavy.lock' EXIT
echo "== box scale (heavy lock)";       $PY spikes/m0_6a/guarded.py --limit-gb 6 --out $OUT/guard3.jsonl -- $PY spikes/m0_6a/b_boxscale.py | tee $OUT/boxscale.json
