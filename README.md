# shoe-designer

Text/image to 3D-printable (FDM + TPU) shoe STL system. Roadmap: GitHub issues and project board.

## Assets (issue #6)

Lasts and template shoes are third-party and large, so they are **not in git**. `assets/manifest.toml`
catalogues them (source, licence, commercial-use flag, which source axes are toe/up); the files are read
from `$SHOE_ASSETS_ROOT`.

```bash
export SHOE_ASSETS_ROOT=~/Downloads
uv run shoe-assets list                    # catalogue + which files are present
uv run shoe-assets inspect sneaker_last    # load into the canonical frame and report
uv run pytest
```

Canonical frame: millimetres, +X heel→toe, +Z up, +Y = up×toe, origin at the toe-axis start / lateral
centre / lowest point of the asset. Region files (`frame_of`) reuse their last's transform so they stay
registered with it. Loading rejects lengths outside 150–400 mm (wrong units or toe axis).

The Zellerfeld files are CC BY-NC-ND 4.0: non-commercial, and modified versions may not be distributed.
That is tracked in issue #8.
