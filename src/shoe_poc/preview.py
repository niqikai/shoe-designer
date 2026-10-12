"""Deterministic headless raster preview of the actual exported STL."""
from pathlib import Path
import os

os.environ.setdefault("MPLCONFIGDIR", "/private/tmp/shoe-poc-matplotlib" if os.path.exists("/private/tmp") else "/tmp/shoe-poc-matplotlib")

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import trimesh


def rasterize(mesh, elevation, azimuth, width=900, height=520, frame_bounds=None):
    """Orthographic depth buffer avoids matplotlib's triangle-sort artifacts."""
    elev, azim = np.radians([elevation, azimuth])
    view = np.array([np.cos(elev)*np.cos(azim), np.cos(elev)*np.sin(azim), np.sin(elev)])
    right = np.array([-np.sin(azim), np.cos(azim), 0])
    up = np.cross(view, right)
    basis = np.stack([right, up, view]).T
    bounds = mesh.bounds if frame_bounds is None else np.asarray(frame_bounds)
    coordinates = (mesh.vertices - bounds.mean(axis=0)) @ basis
    if frame_bounds is None:
        span = np.ptp(coordinates[:, :2], axis=0)
    else:
        corners = np.asarray([[x, y, z] for x in bounds[:, 0]
                              for y in bounds[:, 1] for z in bounds[:, 2]])
        span = np.ptp((corners - bounds.mean(axis=0)) @ basis, axis=0)[:2]
    scale = min((width-60)/span[0], (height-60)/span[1])
    screen = coordinates.copy()
    screen[:, 0] = width/2 + coordinates[:, 0]*scale
    screen[:, 1] = height/2 - coordinates[:, 1]*scale
    depth = np.full((height, width), -np.inf)
    pixels = np.full((height, width, 3), 0.97)
    light = view + np.array([0, 0, 1.2]); light /= np.linalg.norm(light)
    diffuse = np.clip(mesh.face_normals @ light, 0, 1)
    colors = (0.4 + 0.6*diffuse[:, None]) * np.array([0.15, 0.64, 0.73])
    for triangle, color in zip(screen[mesh.faces], colors):
        x0, y0 = np.maximum(np.floor(triangle[:, :2].min(axis=0)), [0, 0]).astype(int)
        x1, y1 = np.minimum(np.ceil(triangle[:, :2].max(axis=0)), [width-1, height-1]).astype(int)
        if x1 < x0 or y1 < y0:
            continue
        a, b, c = triangle
        denominator = (b[1]-c[1])*(a[0]-c[0]) + (c[0]-b[0])*(a[1]-c[1])
        if abs(denominator) < 1e-9:
            continue
        ys, xs = np.mgrid[y0:y1+1, x0:x1+1]; xs = xs+0.5; ys = ys+0.5
        wa = ((b[1]-c[1])*(xs-c[0]) + (c[0]-b[0])*(ys-c[1]))/denominator
        wb = ((c[1]-a[1])*(xs-c[0]) + (a[0]-c[0])*(ys-c[1]))/denominator
        wc = 1-wa-wb
        z = wa*a[2]+wb*b[2]+wc*c[2]
        region = depth[y0:y1+1, x0:x1+1]
        visible = (wa >= -1e-9) & (wb >= -1e-9) & (wc >= -1e-9) & (z > region)
        region[visible] = z[visible]
        pixels[y0:y1+1, x0:x1+1][visible] = color
    return pixels


def render_preview(model: Path, output: Path) -> None:
    mesh = trimesh.load_mesh(model)
    fig = plt.figure(figsize=(16, 10), facecolor="#f3f5f7")
    angles = [(28, -55, "Actual base shoe · perspective"),
              (90, -90, "Top view · entry opening"), (0, -90, "Side view · solid sole")]
    for i, (elev, azim, title) in enumerate(angles, 1):
        ax = fig.add_subplot(2, 2, i)
        ax.imshow(rasterize(mesh, elev, azim))
        ax.set_title(title, fontsize=12)
        ax.set_axis_off()
    ax = fig.add_subplot(2, 2, 4)
    y = float(mesh.bounds[:, 1].mean())
    section = mesh.section(plane_origin=[0, y, 0], plane_normal=[0, 1, 0])
    if section is not None:
        for points in section.discrete:
            ax.plot(points[:, 0], points[:, 2], color="#148eaa", linewidth=1.4)
    ax.set_aspect("equal"); ax.set_xlabel("X (mm)"); ax.set_ylabel("Z (mm)")
    ax.set_title("Longitudinal section · cavity and wall", fontsize=12)
    ax.grid(alpha=0.2)
    fig.suptitle("Milestone 1 · real STEP last → parametric CAD → STL", fontsize=18)
    dimensions = " × ".join(f"{value:.1f}" for value in mesh.extents)
    fig.text(0.5, 0.015, f"{dimensions} mm · Engineering preview. Orientation/fit require review; no print qualification.", ha="center", fontsize=11)
    fig.tight_layout(rect=(0, 0.035, 1, 0.95))
    fig.savefig(output, dpi=140)
    plt.close(fig)


def render_upper_gate(upper_model: Path, cavity_model: Path, last_model: Path, output: Path) -> None:
    """Reimported component meshes and shared-plane overlays before assembly."""
    upper, cavity, last = [trimesh.load_mesh(path) for path in
                           (upper_model, cavity_model, last_model)]
    frame = np.vstack((np.minimum(upper.bounds[0], cavity.bounds[0]),
                       np.maximum(upper.bounds[1], cavity.bounds[1])))
    fig, axes = plt.subplots(2, 2, figsize=(14, 10), facecolor="#f3f5f7")
    for axis, mesh, label in [(axes[0, 0], upper, "Repaired upper · actual STL"),
                              (axes[0, 1], cavity, "Cavity · cutaway ankle continuation included")]:
        axis.imshow(rasterize(mesh, 28, -55, frame_bounds=frame))
        axis.set_title(label)
        axis.set_axis_off()
    y = float(last.bounds[:, 1].mean())
    x = float(last.bounds[0, 0] + .7 * last.extents[0])
    for axis, origin, normal, coordinates, title in [
            (axes[1, 0], [0, y, 0], [0, 1, 0], [0, 2], "Longitudinal overlay"),
            (axes[1, 1], [x, 0, 0], [1, 0, 0], [1, 2], "Forefoot overlay")]:
        for mesh, color, label in [(last, "#444444", "Real last"),
                                    (cavity, "#4a9c53", "Cavity"),
                                    (upper, "#148eaa", "Upper material")]:
            section = mesh.section(plane_origin=origin, plane_normal=normal)
            if section is not None:
                for index, points in enumerate(section.discrete):
                    axis.plot(points[:, coordinates[0]], points[:, coordinates[1]],
                              color=color, linewidth=1.2, label=label if index == 0 else None)
        axis.set_aspect("equal")
        axis.set_xlabel("X (mm)" if coordinates[0] == 0 else "Y (mm)")
        axis.set_ylabel("Z (mm)")
        axis.set_title(title)
        axis.legend()
        axis.grid(alpha=.2)
    fig.suptitle("Upper/cavity geometry gate · flat-face shading, no smoothing", fontsize=16)
    fig.tight_layout(rect=(0, 0, 1, .96))
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=130)
    plt.close(fig)


def render_comparison(previous_model: Path, model: Path, last_model: Path,
                      output: Path) -> None:
    """Compare reimported STL geometry with identical cameras and scale.

    Sections overlay the source last, so changes to the exterior cannot hide
    loss of the cavity. Face normals are used as exported, without smoothing.
    """
    previous, current, last = [trimesh.load_mesh(path, process=True)
                               for path in (previous_model, model, last_model)]
    frame = np.asarray([np.minimum(previous.bounds[0], current.bounds[0]),
                        np.maximum(previous.bounds[1], current.bounds[1])])
    fig, axes = plt.subplots(4, 2, figsize=(15, 18), facecolor="#f3f5f7")
    for row, (elev, azim, title) in enumerate([
            (28, -55, "Perspective"), (0, -90, "Side"), (90, -90, "Top")]):
        for column, (mesh, label) in enumerate([(previous, "Original M1"),
                                                (current, "M1 repair")]):
            axes[row, column].imshow(rasterize(mesh, elev, azim, frame_bounds=frame))
            axes[row, column].set_title(f"{label} · {title}")
            axes[row, column].set_axis_off()
    y = float(last.bounds[:, 1].mean())
    x = float(last.bounds[0, 0] + 0.7 * last.extents[0])
    for axis, origin, normal, indices, title in [
            (axes[3, 0], [0, y, 0], [0, 1, 0], [0, 2], "Longitudinal section"),
            (axes[3, 1], [x, 0, 0], [1, 0, 0], [1, 2], "Forefoot section")]:
        for mesh, color, label in [(previous, "#d88a2f", "Original M1"),
                                    (current, "#148eaa", "M1 repair"),
                                    (last, "#444444", "Real last")]:
            section = mesh.section(plane_origin=origin, plane_normal=normal)
            if section is not None:
                for i, points in enumerate(section.discrete):
                    axis.plot(points[:, indices[0]], points[:, indices[1]],
                              color=color, label=label if i == 0 else None,
                              linewidth=1.3)
        axis.set_aspect("equal")
        axis.set_title(title)
        axis.set_xlabel("X (mm)" if indices[0] == 0 else "Y (mm)")
        axis.set_ylabel("Z (mm)")
        axis.legend()
        axis.grid(alpha=0.2)
    fig.suptitle("Actual STL comparison · identical camera and scale", fontsize=17)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=130)
    plt.close(fig)
