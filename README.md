# AI 3D Printed Footwear Platform — Phase 0

This repository is a headless Python proof of concept for engineering geometry from a locally supplied shoe last. The active scope is **Milestone 1: inspect and normalize a STEP last, validate a ShoeSpec, and build/export a parametric base shoe**. Geometry must be evaluated against the real locally supplied last; a successful import, test, export, or Docker run is reported only after it has actually happened.

Milestone 1 does not include lattice generation, full manufacturing validation, Meshy integration, a web service, or a production-ready shoe. Later milestone features must not be silently included in `run`.

## Requirements

- Python 3.11
- `uv`
- build123d 0.10.0 (installed from the pinned project dependency)
- For container use: Docker or a compatible runtime

Create the environment and install this project in editable mode:

```sh
uv sync --locked --extra test --python 3.11
```

Put the licensed test asset at `input/lasts/sneaker_last.stp`. The input and generated output are ignored by Git. Read [asset provenance](docs/asset_provenance.md) before copying or processing the asset. In particular, do not upload the original last or derived geometry to third-party services.

## Milestone 1 CLI

The command interface is:

```sh
.venv/bin/python -m shoe_poc inspect-last --input input/lasts/sneaker_last.stp --output output/inspection
.venv/bin/python -m shoe_poc build-base --spec input/specs/shoe_spec.json --output output/m1
.venv/bin/python -m shoe_poc run --spec input/specs/shoe_spec.json --output output/m1
.venv/bin/python -m shoe_poc validate --model output/m1/base_shoe.stl --output output/mesh-validation
```

`run` is Milestone 1 only and is equivalent to `build-base`; it does not start future lattice, assembly, AI, or manufacturing workflows. Use `--help` to see the implemented interface. Exit codes: 0 for executed geometry checks passing, 2 for input/configuration errors, 3 for geometry/runtime/check failures. A zero exit code does not certify manufacturing or fit.

Run the tests with `.venv/bin/python -m pytest --report-json output/m1/test_report.json`. Tests marked `real_last` require the local asset and are explicitly skipped when it is unavailable. Primitive fixtures exercise algorithms only. See the milestone acceptance report for actual results.

To reproduce the complete sole-height comparison, run `.venv/bin/python scripts/verify_parameters.py --spec input/specs/shoe_spec.json --baseline output/m1 --output output/m1/variant --height-mm 25`. This generates a second complete model and compares actual CAD/mesh coordinates and volumes.

Each CLI command accepts `--max-rss-mb` (default 8192 MiB). The guard samples process RSS every 100 ms and exits with code 3 plus a saved failure report when an observed sample exceeds the budget. It is a sampled safeguard, not an OS hard cap; brief spikes may occur between samples. Native CAD jobs recorded multi-GiB peak RSS and require sufficient memory. For production use, additionally enforce container/OS limits.

## M1 shape repair and acceptance

The first M1 result passed the implemented topology/export checks but had a faceted upper, abrupt collar cut, flat end caps and slab-like sole. That result is a pipeline prototype, not an accepted shoe template. Preserve `output/m1/` and generate repair evidence separately:

```sh
.venv/bin/python -m shoe_poc run --spec input/specs/shoe_spec.json --output output/m1_repair --compare-with output/m1/base_shoe.stl
.venv/bin/python scripts/verify_parameters.py --spec input/specs/shoe_spec.json --baseline output/m1_repair --output output/m1_repair/variant
.venv/bin/python -m pytest -q --report-json output/m1_repair/test_report.json
.venv/bin/python scripts/report_m1.py --output output/m1_repair --document docs/milestone1_repair_report.md
```

`--compare-with` is optional. It reads the previous STL and renders both exported meshes with identical cameras and scale, plus sections overlaid with the real last. It does not rewrite the previous model or smooth the geometry. `surface_report.json` records independent sampled clearance/thickness, exclusions and unmeasurable locations; these samples do not certify a global minimum.

The execution report separates pipeline status from milestone status. M1 remains `PARTIAL` while appearance/user acceptance is pending, even if topology, export and numerical tests pass. A failed numerical check produces a failed execution. Manufacturing, physical printing and wear remain separate qualification levels. M2 starts only after M1 user acceptance.

The selected repair retains the imported CAD foot surfaces through local normal offsets, uses a curved collar and a lower-foot periodic sole with an actual exterior transition band. The CLI requires the sampled exterior junction and bottom round to qualify; complete positions, exclusions and errors remain in `geometry_report.json`. See [repair acceptance](docs/milestone1_repair_report.md) for the recorded results and [geometry](docs/geometry.md) for the selected algorithm and unsuccessful reconstruction trials.

Each generation clears only known pipeline files from its output directory, preserving diagnostics and unrelated files. Tests and sole-height comparison evidence are associated with the current run; rerun them after rebuilding the model. The parameter comparison also accepts `--max-rss-mb` with the same 8192 MiB default.

The subsequent toe repair preserves `output/m1_design_refine/` and writes to `output/m1_toe_refine/`:

```sh
.venv/bin/python -m shoe_poc run --spec input/specs/shoe_spec_toe_refine.json --output output/m1_toe_refine --compare-with output/m1_design_refine/base_shoe.stl --design-baseline output/m1_design_refine
.venv/bin/python scripts/inspect_toe.py
.venv/bin/python scripts/report_toe.py
```

Its optional `toe_roll_radius_mm` defaults to zero for legacy geometry. A positive value replaces the terminal flat rocker with a continuous quintic roll. The CLI measures actual CAD toe sections and exports STL from bounded, repaired native CAD. STEP geometric roundtrip does not qualify direct STEP remeshing: the recorded offset-face meshing issue requires reapplying the native representation repair after STEP import. See [toe repair evidence and limits](docs/milestone1_toe_refine_report.md). M1 fixes conspicuous geometry defects; styling can be frozen once the base silhouette is accepted.

The user has confirmed the 16 mm base silhouette. Its frozen run and artifact hashes are recorded in [M1 shape acceptance](docs/milestone1_shape_acceptance.json). The current M1 acceptance is PASS and the M2 gate is READY; M2 is now available through the explicit `build-lattice` command. The report keeps the user's decision separate from the original build-time PENDING fields, and refuses to transfer approval to a different run or changed model. Manufacturing, printing and wear remain unqualified.

## Configuration and outputs

ShoeSpec is JSON with explicit millimetre units for geometry. Manufacturing rules in [config/manufacturing_rules.json](config/manufacturing_rules.json) are experimental and informational for this milestone; they do not certify printability. Milestone 1 outputs belong under `output/m1/`, including the inspected last metadata/normalized STEP and base shoe STEP/STL where those operations succeed. Generated files are not source-controlled.

See [architecture](docs/architecture.md) for the milestone boundary, and [development notes](docs/development.md) for environment and implementation status conventions.

The Dockerfile uses the hashed `requirements.lock` exported from `uv.lock`. Build and test with `docker build -t shoe-poc .` and `docker run --rm --entrypoint python shoe-poc -m pytest -m 'not real_last'`. Mount licensed inputs read-only and output separately for real geometry runs. Docker/Linux execution has not been verified on the current macOS host.

## M2 lattice engine

The approved M1 shape is a read-only input. Generate the actual closed gyroid STL with:

```sh
.venv/bin/python -m shoe_poc build-lattice --spec input/specs/shoe_spec_m2.json --base-dir output/m1_toe_refine --output output/m2
.venv/bin/python scripts/verify_lattice.py --output output/m2
.venv/bin/python scripts/preview_lattice.py --help
```

Outputs include `lattice.stl`, `gyroid_field.npz`, `domain_field.npz`, metadata, independent geometry validation, execution records and an actual-STL preview. The optional domain controls are `max_z_offset_mm` (default 0), `clearance_mm` (default 2), and reducing `bounds_mm` (default null). Optional `phase_origin_mm` defaults to `[0, 0, 0]`. The M2 example uses a 12 mm period, 0.6 dimensionless threshold and 0.4 mm grid; the old 0.3/1 mm placeholder is rejected by the engine's resolution screen. Threshold is not measured wall thickness.

The default design domain is the actual sole material below the normalized last datum with a conservative experimental reserve. It omits the upturned toe above that level and any uncertified narrow areas. It does not change the approved shoe silhouette. M2 produces a separate lattice; combining it with an outer shell and qualifying a complete shoe belongs to M3. `run` continues to build M1 only. See [M2 report](docs/milestone2_report.md) for actual results and limitations.


M2 can reuse completed CAD domain certificates with `build-lattice --domain-cache <directory>` only when the separate `domain_provenance.json`, source hashes, frozen baseline, actual parameters, grid headers and saved CAD evidence all agree. The loader checks array budgets before decompression. Such execution records identify cache reuse and retain the original sampling run and time; they do not claim new CAD sampling. Independent checks still run on the newly exported STL.

`verify_lattice.py --jobs 2` uses two isolated spawned processes by default (`--jobs 1` is available). Each worker imports its own CAD and enforces a sampled 4096 MiB RSS guard plus allocation preflight; the 8192 MiB parent guard covers the parent process only. These are per-process safeguards, not a combined OS hard limit. Closed internal void boundaries are validated by nesting orientation rather than incorrectly requiring every connected boundary surface to have positive signed volume. Cross-shell contact/intersection checks are independent; global self-intersection within one shell remains unverified.


A zero contour is always attempted first. If only strict native/STL triangle-area checks fail, the extractor can retry at two bounded, conservative negative levels based on float32 epsilon. It records each attempt, actual level, effective gyroid threshold and extra domain inset; the saved requested scalar field stays unchanged. Other topology failures stop immediately. The real 0.45 threshold case required this numerical approximation; its effective value and failure evidence are included in the M2 report.
