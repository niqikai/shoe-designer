#!/usr/bin/env bash
# Candidate B, criterion 5: same input, repeated runs, TBB thread count 1 / 2 / 4 / default.
#   bash spikes/m0_6a/b_determinism.sh sphere 1.0 [extra b_levelset.py args]
# The thread count is fixed with the DYLD shim in onecpu.c: manifold3d's macOS wheel has TBB
# linked in statically, and neither TBB_NUM_THREADS nor any Python API changes it.
set -euo pipefail
SHAPE=$1; EDGE=$2; shift 2
cd "$(dirname "$0")/../.."
mkdir -p .scratch/interpose
[ -f .scratch/interpose/libonecpu.dylib ] || clang -dynamiclib -o .scratch/interpose/libonecpu.dylib spikes/m0_6a/onecpu.c
PY=${PY:-.venv/bin/python}
SUMMARY="import sys,json; d=json.loads(sys.stdin.read().strip().splitlines()[-1]); print(sys.argv[1], 'tris', d['mesh']['triangles'], 'canonical_sha256', d['hash'], 'raw_order_sha256', d['raw_order_hash'][:16], 'levelset_s', round(d['levelset_seconds'],3))"
for label in default-run1 default-run2 threads1 threads2 threads4; do
  case $label in
    threads*) n=${label#threads}; envs=(DYLD_INSERT_LIBRARIES=.scratch/interpose/libonecpu.dylib ONECPU_N=$n) ;;
    *) envs=() ;;
  esac
  env ${envs[@]+"${envs[@]}"} "$PY" spikes/m0_6a/b_levelset.py --shape "$SHAPE" --edge "$EDGE" --callback lookup "$@" \
    | python3 -c "$SUMMARY" "$label"
done
