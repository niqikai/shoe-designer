# Development notes — Milestone 1

## Environment

The project targets Python 3.11 and build123d 0.10.0. The intended setup uses `uv` and an editable project install:

```sh
uv sync --locked --extra test --python 3.11
```

The test extra supplies pytest. The `real_last` marker identifies tests that require the locally licensed shoe-last asset. Do not treat a mocked fixture or a primitive test shape as real-last acceptance.

## CLI scope

Milestone 1 commands are `inspect-last`, `build-base`, `run`, and `validate`. They write to `output/m1` by default only where the implementation defines that behavior. Explicit `--output` paths are shown in README. `run` means the same Milestone 1 operation as `build-base`; no later milestone may be invoked implicitly.

## Status reporting

Keep implementation, execution, and acceptance distinct. Record actual commands, exit status, and real output paths. Mark a check `NOT_TESTED` if it was not run. A Dockerfile is not evidence of a successful image build or container run. The current orchestration environment reports Docker as unavailable; Docker validation is therefore **NOT_TESTED** unless a later run produces evidence.

## Model allocation

For this collaboration, geometry implementation used `gpt-6-astra` with `high` reasoning, ShoeSpec/schema/tests/independent review and final parameter verification used `gpt-6.1-sol` with `high`, and initial documentation used `gpt-6-luna` with `medium`. The root agent handled integration, rendered inspection, runtime corrections and final acceptance evidence. Astra reached its usage limit after the corrected real build; Sol completed the remaining parameter comparison. These are actual task assignments, not a universal benchmark of model quality.

The official [model selection guide](https://developers.openai.com/api/docs/guides/model-selection) informed the complexity/cost allocation. No API key was needed for these built-in Codex subagents. Milestone 1 remains the authorized scope until the user accepts its report under `docs/PROMPT.md`.
