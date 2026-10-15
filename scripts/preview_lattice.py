"""Render actual lattice/base STL views without changing either mesh."""
from argparse import ArgumentParser
from pathlib import Path

from shoe_poc.lattice_preview import render_lattice_preview


if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--lattice", type=Path, default=Path("output/m2/lattice.stl"))
    parser.add_argument("--base", type=Path, default=Path("output/m1_toe_refine/base_shoe.stl"))
    parser.add_argument("--output", type=Path, default=Path("output/m2/preview.png"))
    args = parser.parse_args()
    render_lattice_preview(args.lattice, args.base, args.output)
