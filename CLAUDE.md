# shoe-designer

Text/image to 3D-printable (FDM + TPU) shoe STL system. See README.md for usage and `docs/design` for design plans.

## Commands

- Install: `uv sync` (optional `uv sync --extra fast` for embreex)
- Test: `uv run pytest`
- CLI: `uv run shoe-assets list|inspect|audit`

## Layout

- `src/shoe_designer/`: `assets` (catalogue + canonical-frame loader), `audit`, `envelope`, `sdf`, `cli`
- `tests/`: pytest, one `test_<module>.py` per module, shared helpers in `tests/helpers.py`
- `assets/manifest.toml`: asset catalogue; the mesh files themselves live outside git (`$SHOE_ASSETS_ROOT`)

## Conventions

- Canonical frame: millimetres, +X heel→toe, +Z up, +Y = up×toe. Keep all geometry code in this frame.
- Python >=3.11, dependencies managed with uv (`pyproject.toml`, `uv.lock`); do not hand-edit `uv.lock`.
- Never commit third-party meshes (large, licence-restricted: Zellerfeld files are CC BY-NC-ND 4.0).
- Add or update tests with every behaviour change; run `uv run pytest` before finishing.
- Roadmap lives in GitHub issues; reference the issue number in commits and PRs (e.g. `(#10)`).

## Reviewing

When asked to review a PR: focus on correctness (units, axis conventions, mesh edge cases), missing tests and regressions. Skip pure style nits.
