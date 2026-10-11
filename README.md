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
uv venv --python 3.11
uv pip install --python .venv/bin/python -e '.[test]'
```

Put the licensed test asset at `input/lasts/sneaker_last.stp`. The input and generated output are ignored by Git. Read [asset provenance](docs/asset_provenance.md) before copying or processing the asset. In particular, do not upload the original last or derived geometry to third-party services.

## Milestone 1 CLI

The intended command interface is:

```sh
uv run python -m shoe_poc inspect-last --input input/lasts/sneaker_last.stp --output output/m1
uv run python -m shoe_poc build-base --spec input/specs/shoe_spec.json --output output/m1
uv run python -m shoe_poc run --spec input/specs/shoe_spec.json --output output/m1
uv run python -m shoe_poc validate --model output/m1/base_shoe.stl
```

`run` is Milestone 1 only and is equivalent to `build-base`; it must not start future lattice, assembly, AI, or manufacturing workflows. Use `--help` to see the implemented interface. These commands describe the planned interface; their availability and execution results must be confirmed from the current checkout and reported accurately.

Run the tests with `uv run pytest`. Tests marked `real_last` require the local asset; do not substitute an unrelated primitive for real-last acceptance. No test or geometry result is asserted by this documentation.

## Configuration and outputs

ShoeSpec is JSON with explicit millimetre units for geometry. Manufacturing rules in [config/manufacturing_rules.json](config/manufacturing_rules.json) are experimental and informational for this milestone; they do not certify printability. Milestone 1 outputs belong under `output/m1/`, including the inspected last metadata/normalized STEP and base shoe STEP/STL where those operations succeed. Generated files are not source-controlled.

See [architecture](docs/architecture.md) for the milestone boundary, and [development notes](docs/development.md) for environment and implementation status conventions.
