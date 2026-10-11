"""Deterministic headless raster preview of the actual exported STL."""
from pathlib import Path
import os

os.environ.setdefault("MPLCONFIGDIR", "/private/tmp/shoe-poc-matplotlib" if os.path.exists("/private/tmp") else "/tmp/shoe-poc-matplotlib")

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import trimesh


def rasterize(mesh, elevation, azimuth, width=900, height=520):
    """Orthographic depth buffer avoids matplotlib's triangle-sort artifacts."""
    elev, azim = np.radians([elevation, azimuth])
    view = np.array([np.cos(elev)*np.cos(azim), np.cos(elev)*np.sin(azim), np.sin(elev)])
    right = np.array([-np.sin(azim), np.cos(azim), 0])
    up = np.cross(view, right)
    coordinates = (mesh.vertices - mesh.bounds.mean(axis=0)) @ np.stack([right, up, view]).T
    span = np.ptp(coordinates[:, :2], axis=0)
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
