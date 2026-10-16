# Core engine architecture — M1 and M2

## Scope

The frozen M1 vertical slice can: inspect a locally supplied STEP shoe last, normalize it using an explicit configured transform, validate a versioned ShoeSpec, generate a parametric base shoe, and export STEP and STL when the geometry operations succeed. The CLI is headless. A command's presence in documentation is not evidence that it is implemented or has run successfully.

Milestone 2 is explicitly authorized after acceptance of the frozen 16 mm M1 silhouette. `build-lattice` reads that accepted CAD and generates a bounded gyroid field and closed lattice STL. Milestone 3 shell fusion, whole-shoe manufacturing checks and additional export formats, and Milestone 4 Meshy/design mapping remain outside the current runtime scope. `run` remains equivalent to `build-base`; it does not silently invoke M2.

## Component boundaries

- **CLI**: parses milestone commands, paths, and options; returns meaningful process status and diagnostics.
- **Last handling**: imports STEP, inspects available geometry properties, applies the configured coordinate transform, and writes metadata plus normalized STEP. Shoe-tip direction, side, and ground reference must be explicit when geometry alone cannot determine them reliably.
- **ShoeSpec**: versioned JSON contract. Lengths use millimetres and are range-checked before geometry construction.
- **Geometry**: separate upper, sole, and assembly responsibilities. The shoe last is an input constraint, never a hard-coded production asset. The base shoe must preserve a cavity and opening; an overall scaled last is not a finished shoe.
- **Export and validation**: write base STEP/STL and inspect exported files only where implemented. Report each geometry assertion and export status from actual results.
- **Manufacturing rules**: explicitly experimental configuration. It is not a validated process window or a printability certification.

## Data flow

```text
local STEP + ShoeSpec JSON
          ↓
  inspect and normalize last
          ↓
  construct base upper and sole
          ↓
  assemble and export STEP / STL
          ↓
  report actual checks and artifacts
```

The original STEP remains local. Neither the original asset nor geometry derived from it may be uploaded to Meshy or another third party. Meshy credentials are not needed for Milestone 1.

## Current CLI contract

The implemented interface is `inspect-last --input ... --output output/m1`, `build-base --spec ... --output output/m1`, `run --spec ... --output output/m1`, and `validate --model ...`. The latter validates the supplied model only to the extent of checks actually implemented. `run` is Milestone 1 only. Consult CLI help and implementation before relying on options or behavior.

## M2 boundaries

`build-lattice --spec input/specs/shoe_spec_m2.json --base-dir output/m1_toe_refine --output output/m2` is a separate entry point. The shape approval binds M1 STEP/STL, spec and previews; its `.m2_inputs.json` companion locks the supporting cavity, normalized last, metadata and original execution report. Both are checked before use and after computation. The command refuses nested M1/M2 outputs and generated-file symbolic links before starting any diagnostic writer or RSS guard.

The real shoe CAD material is intersected with an explicit sole design envelope, minus the actual cavity. Legacy `planar` mode keeps the window from the last datum minus `sole.height_mm` up to the last datum; that horizontal ceiling truncates raised toe material. The repair selects `curved_lower_foot`: its ruled roof is the lower of the accepted rocker plus nominal sole height and the frozen lower-foot connection guide minus the transition half-band. Recorded linear X segments define the exact envelope contract, independently of voxel spacing. The original geometry-report SHA and all roof coordinates are bound into domain metadata and cache provenance. Optional bounds only reduce the region. Neither mode reconstructs or changes the accepted M1 geometry.

For the curved mode, certificate radii also use exact Euclidean distance to every XZ roof segment and its endpoint rays. Vertical roof separation alone would overestimate clearance on a slope. Independent native vertical sections measure intended underfoot material, thin reserves, and actual exported STL intervals over forefoot/toe bins; raised material above the last datum is checked separately. This catches missing coverage even when containment passes. Finite probes remain distinct from global coverage and wall-thickness proofs.

A sparse three-station measurement can alias a gyroid phase in a narrow toe bin. A coverage-only failure therefore gets at most one deterministic seven-by-seven X/Y refinement. Support thresholds do not change, the initial failing measurement is retained, and the new lines must intersect actual CAD and STL material. Intrusion failures and unresolved native CAD measurements do not qualify for this retry. A genuinely absent toe or absent certified domain still fails the refined check.

The later terminal-completion gate addresses a different failure of that sparse
coverage test: a small fraction of surviving toe material can satisfy a bin
while the main body ends early. In the explicit refinement corridor, adaptive
blocks may be skipped only with a valid boundary-distance upper bound, an
outside-region proof, or complete existing negative-node coverage. Actual
trimmed-CAD nearest points, global box faces and the explicit roof supply safe
upper bounds; supporting planes of trimmed faces do not. Separate exact-query
journals bind the accepted inputs, original field and refinement policy, and
are resumed only through a mandatory complete traversal. They never satisfy
the completed-domain cache contract on their own.

Independent native CAD nodes determine the expected toe sites. Six-neighbor
domain connectivity and the exported STL's largest positive-volume connected
surface are checked separately. Every terminal grid plane with independently
recomputed analytic material must have actual crossing triangles in that
principal surface. Tangent caps and distant isolated fragments cannot establish
connected reach. The checks remain finite sampled evidence and make no global
manufacturing or continuous coverage claim.

Native CAD scanlines select candidate material. Exact CAD boundary distances and a full cell-diagonal reserve certify interior balls. Ambiguous classifier membership is resolved with independent three-axis full-bounding-box CAD Boolean lines; unresolved disagreement stops generation. Their union provides a conservative field; unproved regions are explicitly omitted and counted. Candidate raster EDT is not a containment certificate. Numerical assertions remain conditional on CAD kernel tolerances.

The gyroid field is `abs(G)-threshold`; `threshold` is dimensionless. NumPy samples XYZ slabs in millimetres with an explicit origin and phase. The field is intersected with the certified domain before [scikit-image Lewiner Marching Cubes](https://scikit-image.org/docs/stable/api/skimage.measure.html#skimage.measure.marching_cubes), including positive boundary padding and a documented inward cap inset. No mesh smoothing, hole filling or component deletion is performed. All components are retained and measured.

Independent validation reimports the STL, checks arbitrary-genus manifold topology, shell nesting orientation, cross-shell separation, positive net material volume and serialization error, samples actual CAD membership/Z bounds, compares native CAD and all-triangle line intervals, and evaluates the analytic gyroid residual separately from domain caps. Finite sampling does not establish global minimum wall thickness or global containment. M2 engineering status, user acceptance, M3 shell fusion and manufacturing/physical tests are distinct fields.

Voxel/allocation estimates reject excessive work before large arrays or extraction. The CLI also samples process RSS; this does not replace an OS hard limit. Reports contain actual parameters, voxel counts, native CAD diagnostics, omitted regions, timings, peak RSS and file hashes. Reusing the frozen M1 model does not modify its artifacts.


Validated domain replay requires a provenance sidecar bound to the exact accepted inputs, original domain hash, parameters and grid. Header/resource checks precede array decompression. Cached raw scanline occupancy is unavailable (`None`); the certified signed field and original metadata are preserved. Reports distinguish original CAD sampling from replay. Variant checks use at most two independent spawned CAD processes with individual RSS guards.
