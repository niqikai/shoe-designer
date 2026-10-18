"""Asset catalog: where each last / template shoe lives, what it is, and how to bring it
into the canonical frame.

Canonical frame (millimetres, right-handed):
    +X  heel -> toe (length)
    +Y  up x toe (lateral)
    +Z  up
    origin: x = min of the frame's anchor mesh, y = its bbox centre, z = its lowest point

Source files use inconsistent axes, so each asset declares which source axes are
"toe" and "up". Assets that share a coordinate system with a last (thickness maps,
sole regions, ...) declare ``frame_of`` and reuse that last's transform, so they stay
registered with it.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import trimesh

ROOT_ENV = "SHOE_ASSETS_ROOT"
DEFAULT_MANIFEST = Path(__file__).resolve().parents[2] / "assets" / "manifest.toml"
ROLES = {"last", "shoe", "region"}
# Plausible adult-shoe length in mm; outside this, units are probably wrong.
LENGTH_RANGE_MM = (150.0, 400.0)

_AXES = {
    "+X": (1.0, 0.0, 0.0), "-X": (-1.0, 0.0, 0.0),
    "+Y": (0.0, 1.0, 0.0), "-Y": (0.0, -1.0, 0.0),
    "+Z": (0.0, 0.0, 1.0), "-Z": (0.0, 0.0, -1.0),
}


class AssetError(Exception):
    pass


@dataclass(frozen=True)
class Asset:
    id: str
    role: str
    path: str
    source: str
    license: str
    toe: str = "+X"
    up: str = "+Z"
    frame_of: str | None = None
    commercial_use: bool | None = None  # None = not yet determined
    side: str | None = None
    size_eu: float | None = None
    notes: str = ""


@dataclass(frozen=True)
class Loaded:
    asset: Asset
    mesh: trimesh.Trimesh  # in canonical frame
    transform: np.ndarray  # 4x4, source -> canonical


def _axis(name: str, what: str, asset_id: str) -> np.ndarray:
    try:
        return np.array(_AXES[name])
    except KeyError:
        raise AssetError(f"{asset_id}: {what}={name!r} must be one of {sorted(_AXES)}") from None


def load_catalog(manifest: Path = DEFAULT_MANIFEST) -> dict[str, Asset]:
    data = tomllib.loads(Path(manifest).read_text(encoding="utf-8"))
    catalog: dict[str, Asset] = {}
    for row in data.get("asset", []):
        try:
            asset = Asset(**row)
        except TypeError as e:
            raise AssetError(f"bad manifest row {row.get('id', '?')}: {e}") from None
        if asset.id in catalog:
            raise AssetError(f"duplicate asset id {asset.id!r}")
        if asset.role not in ROLES:
            raise AssetError(f"{asset.id}: role {asset.role!r} not in {sorted(ROLES)}")
        _axis(asset.toe, "toe", asset.id)
        _axis(asset.up, "up", asset.id)
        catalog[asset.id] = asset
    for asset in catalog.values():
        if asset.frame_of is not None and catalog.get(asset.frame_of) is None:
            raise AssetError(f"{asset.id}: frame_of {asset.frame_of!r} is not in the catalog")
        if asset.frame_of is not None and catalog[asset.frame_of].frame_of is not None:
            raise AssetError(f"{asset.id}: frame_of must point at an anchor, not at {asset.frame_of!r}")
    return catalog


def assets_root(root: Path | str | None = None) -> Path:
    value = root if root is not None else os.environ.get(ROOT_ENV)
    if not value:
        raise AssetError(f"set {ROOT_ENV} to the folder holding the mesh files")
    path = Path(value).expanduser()
    if not path.is_dir():
        raise AssetError(f"assets root {path} is not a directory")
    return path


def rotation(toe: str, up: str, asset_id: str = "?") -> np.ndarray:
    """Rows are the canonical X, Y, Z axes expressed in source coordinates."""
    t, u = _axis(toe, "toe", asset_id), _axis(up, "up", asset_id)
    if abs(float(t @ u)) > 0.5:
        raise AssetError(f"{asset_id}: toe={toe} and up={up} must be different axes")
    return np.stack([t, np.cross(u, t), u])


def _anchor_transform(mesh: trimesh.Trimesh, asset: Asset) -> np.ndarray:
    rot = rotation(asset.toe, asset.up, asset.id)
    v = mesh.vertices @ rot.T
    lo, hi = v.min(axis=0), v.max(axis=0)
    shift = np.array([lo[0], (lo[1] + hi[1]) / 2.0, lo[2]])
    transform = np.eye(4)
    transform[:3, :3] = rot
    transform[:3, 3] = -shift
    return transform


def _read(asset: Asset, root: Path) -> trimesh.Trimesh:
    path = root / asset.path
    if not path.is_file():
        raise AssetError(f"{asset.id}: file not found: {path}")
    mesh = trimesh.load(path, force="mesh")  # default processing merges duplicate vertices
    if len(mesh.faces) == 0:
        raise AssetError(f"{asset.id}: {path} contains no faces")
    return mesh


def load_asset(asset_id: str, catalog: dict[str, Asset], root: Path | str | None = None) -> Loaded:
    if asset_id not in catalog:
        raise AssetError(f"unknown asset {asset_id!r}; known: {sorted(catalog)}")
    base = assets_root(root)
    asset = catalog[asset_id]
    mesh = _read(asset, base)
    if asset.frame_of is None:
        transform = _anchor_transform(mesh, asset)
    else:
        anchor = catalog[asset.frame_of]
        transform = _anchor_transform(_read(anchor, base), anchor)
    mesh.apply_transform(transform)
    if asset.role in ("last", "shoe"):
        length = float(mesh.extents[0])
        if not LENGTH_RANGE_MM[0] <= length <= LENGTH_RANGE_MM[1]:
            raise AssetError(
                f"{asset.id}: length {length:.1f} mm outside {LENGTH_RANGE_MM}; wrong units or toe axis?"
            )
    return Loaded(asset=asset, mesh=mesh, transform=transform)
