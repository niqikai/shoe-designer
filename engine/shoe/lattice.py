"""Continuous periodic sheet/truss fields clipped inside the existing midsole.

Gyroid and diamond use trigonometric TPMS approximations. Octet is the union of
the twelve periodic FCC nearest-neighbour line families, with circular rods.
Density is calibrated in a unit cell; the actual cropped core is measured too.
"""
from functools import lru_cache
import math

import numpy as np

from engine.shoe.volume import geometry_dependencies

KINDS = ("gyroid", "diamond", "octet")


def periodic_metric(kind, x, y, z, period):
    """Return |TPMS F| (dimensionless) or exact octet centreline distance / L."""
    if kind not in KINDS:
        raise ValueError("未知晶格类型。")
    if kind in ("gyroid", "diamond"):
        u, v, w = [np.asarray(axis, dtype=np.float32) * (2 * np.pi / period) for axis in (x, y, z)]
        sx, sy, sz = np.sin(u), np.sin(v), np.sin(w)
        cx, cy, cz = np.cos(u), np.cos(v), np.cos(w)
        if kind == "gyroid":
            return np.abs(sx * cy + sy * cz + sz * cx).astype(np.float32)
        return np.abs(sx * sy * sz + sx * cy * cz + cx * sy * cz + cx * cy * sz).astype(np.float32)
    # FCC nodes lie at all integer lattice sites and all face centres. Each
    # family consists of complete diagonals; crossings provide 12 neighbours.
    axes = [np.asarray(axis, dtype=np.float32) / period for axis in (x, y, z)]
    best = np.full(np.broadcast_shapes(*(a.shape for a in axes)), np.inf, dtype=np.float32)
    wrap = lambda value: (value + .5) % 1.0 - .5
    for a, b, c in ((0, 1, 2), (0, 2, 1), (1, 2, 0)):
        for sign in (-1, 1):
            for phase in (0.0, .5):
                distance2 = wrap(axes[a] + sign * axes[b] - phase) ** 2 * .5 + wrap(axes[c] - phase) ** 2
                np.minimum(best, distance2, out=best)
    return np.sqrt(best)


@lru_cache(maxsize=3)
def unit_samples(kind):
    axis = (np.arange(64, dtype=np.float32) + .5) / 64
    values = periodic_metric(kind, axis[:, None, None], axis[None, :, None], axis[None, None, :], 1.0)
    return np.sort(values.ravel())


def threshold_for_density(kind, density):
    values = unit_samples(kind)
    rank = np.asarray(density) * (len(values) - 1)
    return np.interp(rank, np.arange(len(values)), values)


def density_profile(y, length, params):
    """Smooth transition over 8% of length around both engineering boundaries."""
    t = np.asarray(y, dtype=np.float64) / length
    smooth = lambda value: np.clip(value, 0, 1) ** 2 * (3 - 2 * np.clip(value, 0, 1))
    heel, arch, front = [params["lattice_density_" + zone] for zone in ("heel", "arch", "forefoot")]
    return heel + (arch - heel) * smooth((t - .26) / .08) + (front - arch) * smooth((t - .56) / .08)


def lattice_spec(params):
    kind = params["lattice_type"]
    targets = {zone: params["lattice_density_" + zone] for zone in ("heel", "arch", "forefoot")}
    thresholds = {zone: float(threshold_for_density(kind, rho)) for zone, rho in targets.items()}
    smallest = min(thresholds.values())
    diameter = params["lattice_rod_mm"]
    # For either TPMS each partial derivative is bounded by sqrt(2), hence
    # |grad(F)| <= 2*pi*sqrt(6)/L. The uniform minimum threshold encloses a
    # ball of diameter c_min*L/(pi*sqrt(6)) at every zero-surface point.
    # This conservative interior bound excludes clipped ends and voxel error.
    period = diameter / (2 * smallest) if kind == "octet" else diameter * math.pi * math.sqrt(6) / smallest
    thickness = {zone: (2 * threshold * period if kind == "octet" else threshold * period / (math.pi * math.sqrt(6)))
                 for zone, threshold in thresholds.items()}
    return {"type": kind, "period_mm": period, "unit_cell_target_density": targets,
            "zone_thresholds": thresholds, "requested_rod_or_sheet_mm": diameter,
            "interior_design_thickness_mm": thickness,
            "thickness_definition": "octet rod diameter" if kind == "octet" else "conservative uncut analytic sheet bound; not a measured printed wall",
            "thickness_zone_note": "zone plateau values; transitions vary; requested scale is the global analytic minimum before clipping and sampling",
            "density_calibration": "64^3 midpoint unit-cell quantiles; cropped core fractions measured separately",
            "zone_transition_fraction": .08, "phase_continuous": True,
            "period_note": "one global period; rod/nominal sheet scale and density are coupled, not independent stiffness controls"}


def lattice_midsole(sole, maps, x, y, z, length, params):
    """Replace only the sole core; keep the same curved roof and 3 mm outsole."""
    spec = lattice_spec(params)
    top_skin = max(2.0, params["upper_thickness_mm"])
    bottom_skin = 3.0
    bottom = maps["bottom"][:, :, None]
    top = maps["top"][:, :, None]
    height = z[None, None, :]
    core = np.maximum(sole, bottom + bottom_skin - height).astype(np.float32)
    np.maximum(core, height - top + top_skin, out=core)
    density_y = density_profile(y, length, params)
    threshold = threshold_for_density(spec["type"], density_y).astype(np.float32)[None, :, None]
    # Outside the core a positive lattice value has no effect. Limit expensive
    # periodic calculations to the sole height band, not the whole upper.
    active = np.flatnonzero((z >= float(maps["bottom"].min()) + bottom_skin - 2)
                           & (z <= float(maps["top"].max()) - top_skin + 2))
    periodic = np.full(sole.shape, 1e3, dtype=np.float32)
    metric = periodic_metric(spec["type"], x[:, None, None], y[None, :, None], z[None, None, active], spec["period_mm"])
    scale = spec["period_mm"] if spec["type"] == "octet" else spec["period_mm"] / (2 * math.pi * math.sqrt(6))
    periodic[:, :, active] = (metric - threshold) * scale
    # Complement of the carved core gives the protective skins; the periodic
    # solid overlaps them wherever it continues, with no coincident mesh seam.
    result = np.minimum(np.maximum(sole, -core), np.maximum(sole, periodic)).astype(np.float32)
    core_mask = core < -.01
    zone_stats = {}
    for zone, lo, hi in (("heel", -np.inf, .30), ("arch", .30, .60), ("forefoot", .60, np.inf)):
        mask = core_mask & ((y >= lo * length) & (y < hi * length))[None, :, None]
        count = int(mask.sum())
        zone_stats[zone] = {"core_sample_voxels": count,
                            "cropped_core_material_fraction": float(np.count_nonzero((periodic < 0) & mask) / count) if count else None}
    spec.update({"top_skin_mm": top_skin, "outsole_skin_mm": bottom_skin,
                 "sidewall": "open cell ends clipped to original sole envelope; no sealed perimeter jacket",
                 "zone_core_sampling": zone_stats, "core_voxels": int(core_mask.sum())})
    return result, core_mask, spec


def inspect_voids(field, core_mask, spacing):
    """A sampled geometry diagnostic, not a powder-removal certification."""
    geometry_dependencies()
    from scipy.ndimage import binary_propagation, generate_binary_structure
    void = field > 0
    seed = np.zeros(field.shape, dtype=bool)
    for axis in range(3):
        slices = [slice(None)] * 3
        for side in (0, -1):
            slices[axis] = side
            seed[tuple(slices)] = void[tuple(slices)]
    exterior = binary_propagation(seed, structure=generate_binary_structure(3, 1), mask=void)
    core_void = void & core_mask
    total = int(core_void.sum())
    trapped = int(np.count_nonzero(core_void & ~exterior))
    return {"core_void_voxels": total, "trapped_core_void_voxels": trapped,
            "trapped_core_void_mm3": trapped * spacing ** 3,
            "sampled_open_core_void_fraction": (total - trapped) / total if total else None,
            "method": "6-neighbour positive-field flood fill from the padded exterior",
            "limitation": "voxel-resolution connectivity only; aperture diameter and powder removal are not validated until M3"}


def clean_lattice_field(field, spacing):
    """Remove tiny clipped fragments and fill closed, unvented voxel pockets.

    These actions operate on generated material only. Significant disconnected
    material is an error, rather than something silently discarded.
    """
    geometry_dependencies()
    from scipy.ndimage import binary_fill_holes, generate_binary_structure, label
    material = field < 0
    labels, count = label(material, structure=generate_binary_structure(3, 1))
    sizes = np.bincount(labels.ravel())
    sizes[0] = 0
    largest = int(sizes.argmax())
    removed = material & (labels != largest)
    removed_count = int(removed.sum())
    if removed_count > max(8, int(material.sum() * .01)):
        raise ValueError("晶格裁切产生超过 1% 的分离材料，拒绝静默丢弃。")
    field[removed] = np.abs(field[removed]) + spacing * .05
    material[removed] = False
    filled = binary_fill_holes(material, structure=generate_binary_structure(3, 1)) & ~material
    filled_count = int(filled.sum())
    field[filled] = -np.abs(field[filled]) - spacing * .05
    return {"removed_fragment_components": int(count - 1), "removed_material_voxels": removed_count,
            "removed_material_mm3": removed_count * spacing ** 3,
            "filled_closed_void_voxels": filled_count, "filled_closed_void_mm3": filled_count * spacing ** 3,
            "note": "generated-field cleanup; sealed sampled pockets are made solid, not presented as drainable lattice"}
