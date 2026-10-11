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

## Configuration and outputs

ShoeSpec is JSON with explicit millimetre units for geometry. Manufacturing rules in [config/manufacturing_rules.json](config/manufacturing_rules.json) are experimental and informational for this milestone; they do not certify printability. Milestone 1 outputs belong under `output/m1/`, including the inspected last metadata/normalized STEP and base shoe STEP/STL where those operations succeed. Generated files are not source-controlled.

See [architecture](docs/architecture.md) for the milestone boundary, and [development notes](docs/development.md) for environment and implementation status conventions.

The Dockerfile uses the hashed `requirements.lock` exported from `uv.lock`. Build and test with `docker build -t shoe-poc .` and `docker run --rm --entrypoint python shoe-poc -m pytest -m 'not real_last'`. Mount licensed inputs read-only and output separately for real geometry runs. Docker/Linux execution has not been verified on the current macOS host.
