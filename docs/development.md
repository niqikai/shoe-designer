# Development notes — Milestone 1

## Environment

The project targets Python 3.11 and build123d 0.10.0. The intended setup uses `uv` and an editable project install:

```sh
uv venv --python 3.11
uv pip install --python .venv/bin/python -e '.[test]'
```

The test extra supplies pytest. The `real_last` marker identifies tests that require the locally licensed shoe-last asset. Do not treat a mocked fixture or a primitive test shape as real-last acceptance.

## CLI scope

Milestone 1 commands are `inspect-last`, `build-base`, `run`, and `validate`. They write to `output/m1` by default only where the implementation defines that behavior. Explicit `--output` paths are shown in README. `run` means the same Milestone 1 operation as `build-base`; no later milestone may be invoked implicitly.

## Status reporting

Keep implementation, execution, and acceptance distinct. Record actual commands, exit status, and real output paths. Mark a check `NOT_TESTED` if it was not run. A Dockerfile is not evidence of a successful image build or container run. The current orchestration environment reports Docker as unavailable; Docker validation is therefore **NOT_TESTED** unless a later run produces evidence.

## Model allocation

For this collaboration, geometry implementation is allocated to Astra, ShoeSpec/schema work to Sol, documentation to Luna, and overall orchestration to the root agent. These allocations describe responsibility only; they do not imply implementation or verification status.
