"""Render an identical-camera toe zoom from the actual exported STL files."""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import trimesh

from shoe_poc.preview import plt, rasterize


def render_toe_comparison(baseline_dir, candidate_dir, output):
    baseline_dir, candidate_dir, output = map(Path, (baseline_dir, candidate_dir, output))
    if output.resolve().is_relative_to(baseline_dir.resolve()):
        raise ValueError("Toe inspection must not write inside the preserved baseline directory")
    previous, current, last = [trimesh.load_mesh(path, process=True) for path in
        (baseline_dir/"base_shoe.stl", candidate_dir/"base_shoe.stl", candidate_dir/"last_normalized.stl")]
    tip = float(max(previous.bounds[1, 0], current.bounds[1, 0]))
    floor = float(min(previous.bounds[0, 2], current.bounds[0, 2]))
    roof = float(last.bounds[0, 2]+42.)
    frame = np.asarray([[tip-65., min(previous.bounds[0, 1], current.bounds[0, 1]), floor-3.],
                        [tip+3., max(previous.bounds[1, 1], current.bounds[1, 1]), roof]])
    fig, axes = plt.subplots(2, 2, figsize=(14, 11), facecolor="#f3f5f7")
    for axis, mesh, label in ((axes[0, 0], previous, "Preserved design baseline"),
                               (axes[0, 1], current, "Toe refinement")):
        axis.imshow(rasterize(mesh, 0, -90, width=1000, height=620, frame_bounds=frame))
        axis.set_title(f"{label} · actual STL toe side zoom")
        axis.set_axis_off()
    y_center = float(last.bounds[:, 1].mean())
    for axis, y, title in ((axes[1, 0], y_center, "Center longitudinal section"),
                           (axes[1, 1], y_center+5., "Longitudinal section · Y center +5 mm")):
        for mesh, color, label in ((previous, "#d88a2f", "Preserved baseline"),
                                    (current, "#148eaa", "Toe refinement"),
                                    (last, "#444444", "Unchanged source last")):
            section = mesh.section(plane_origin=(0, y, 0), plane_normal=(0, 1, 0))
            if section is not None:
                for index, points in enumerate(section.discrete):
                    axis.plot(points[:, 0], points[:, 2], color=color, linewidth=1.3,
                              label=label if index == 0 else None)
        axis.set_xlim(frame[0, 0], frame[1, 0]); axis.set_ylim(frame[0, 2], frame[1, 2])
        axis.set_aspect("equal"); axis.grid(alpha=.2)
        axis.set_xlabel("X (mm)"); axis.set_ylabel("Z (mm)")
        axis.set_title(title); axis.legend(fontsize=9)
    fig.suptitle("Toe comparison · identical camera and scale · raw exported geometry", fontsize=16)
    fig.text(.5, .015, "Flat triangle shading and actual mesh sections; no vertex displacement or normal smoothing. Appearance approval remains pending.",
             ha="center", fontsize=9)
    fig.tight_layout(rect=(0, .035, 1, .96))
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=140)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-dir", type=Path, default=Path("output/m1_design_refine"))
    parser.add_argument("--candidate-dir", type=Path, default=Path("output/m1_toe_refine"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or args.candidate_dir/"toe_comparison.png"
    render_toe_comparison(args.baseline_dir, args.candidate_dir, output)
    print(output.resolve())


if __name__ == "__main__":
    main()
