"""Candidate B, criterion 4: can manifold3d level_set be applied per block and stitched?

    uv run python spikes/m0_6a/b_crossblock.py --voxel 1.0 --edge 1.0
    uv run python spikes/m0_6a/b_crossblock.py --voxel 0.3 --edge 0.3

Sphere R = 20 mm cut by the plane x = y = z = SPLIT in every axis (2 x 2 x 2 sub-boxes).

  whole      one level_set over the whole box (reference)
  abutting   8 level_set calls on boxes that just touch; concatenate + weld by coordinate
  overlap    8 calls on boxes overlapping by ``--overlap`` mm; each triangle is kept only by
             the box that owns it (its lexicographically smallest vertex lies in the box's
             core part); concatenate + weld by coordinate

Reports closedness / manifoldness of the stitched meshes and how many triangles of the
reference are reproduced bit-for-bit (as coordinate triples).
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import common
import manifold3d as m3d

from shoegen.evaluate import plan_blocks


def extract(f, bounds, edge):
    M = m3d.Manifold.level_set(f, list(map(float, bounds)), edge)
    mesh = M.to_mesh64()
    return (
        np.asarray(mesh.vert_properties, np.float64)[:, :3],
        np.asarray(mesh.tri_verts, np.int64),
    )


def tri_coords(v, t):
    """(N, 9) float64 rows: triangle vertex coordinates, rotated to start at the
    lexicographically smallest vertex (orientation kept); and that vertex (N, 3)."""
    P = v[t]  # N,3,3
    n = np.arange(len(t))
    best = np.zeros(len(t), np.int64)
    for k in (1, 2):
        p, q = P[n, best], P[:, k]
        less = (q[:, 0] < p[:, 0]) | (
            (q[:, 0] == p[:, 0])
            & ((q[:, 1] < p[:, 1]) | ((q[:, 1] == p[:, 1]) & (q[:, 2] < p[:, 2])))
        )
        best = np.where(less, k, best)
    idx = (best[:, None] + np.arange(3)[None, :]) % 3
    R = np.take_along_axis(P, idx[:, :, None], axis=1).reshape(len(t), 9)
    return R, P[n, best]


def rows_set(R):
    return np.unique(R, axis=0)


def compare(W, S):
    """How many rows of the reference W (N,9) also appear bitwise in S."""
    Wu, Su = rows_set(W), rows_set(S)
    both = np.concatenate([Wu, Su])
    _, c = np.unique(both, axis=0, return_counts=True)
    common_rows = int(np.count_nonzero(c == 2))
    return {
        "ref_triangles": len(Wu),
        "stitched_triangles": len(Su),
        "identical_bitwise": common_rows,
        "only_in_ref": len(Wu) - common_rows,
        "only_in_stitched": len(Su) - common_rows,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--voxel", type=float, default=1.0)
    ap.add_argument("--edge", type=float, default=1.0)
    ap.add_argument("--overlap", type=float, default=3.0, help="in units of --edge")
    a = ap.parse_args()
    h, e = a.voxel, a.edge
    sh = common.sphere_shape(h)
    plan = plan_blocks(sh.graph, None, sh.region_min, sh.region_max, h)
    store, _ = common.build_store(plan, workers=1)
    f = common.make_lookup(store)

    # box on the lattice: indices k0..k1 (multiples of the cell edge e = n * h)
    n = round(e / h)
    c = np.asarray(common.SPHERE_CENTRE)
    k0 = int(np.floor((c[0] - 22) / h))
    k1 = k0 + int(np.ceil(44 / h / n)) * n
    lo, hi = k0 * h, k1 * h
    split_idx = k0 + (k1 - k0) // 2 // n * n
    split = split_idx * h
    out = {"voxel": h, "edge": e, "box": [lo, hi], "split": split}

    # whole
    vw, tw = extract(f, [lo] * 3 + [hi] * 3, e)
    out["whole"] = {
        k: v
        for k, v in common.mesh_report(vw, tw).items()
        if k
        in (
            "triangles",
            "closed_oriented_manifold",
            "nonmanifold_vertices",
            "euler_characteristic",
            "volume_mm3",
        )
    }
    Rw, _ = tri_coords(vw, tw)

    def staged(overlap_cells, trim):
        ov = overlap_cells * e
        verts, tris, off = [], [], 0
        caps = 0
        for ix, iy, iz in itertools.product((0, 1), repeat=3):
            idx = (ix, iy, iz)
            b0 = [lo if i == 0 else split - ov for i in idx]
            b1 = [split + ov if i == 0 else hi for i in idx]
            v, t = extract(f, b0 + b1, e)
            # triangles lying completely in one bounds plane of this sub-box = synthetic caps
            on_plane = np.zeros(len(t), bool)
            for ax in range(3):
                for plane in (b0[ax], b1[ax]):
                    on_plane |= np.all(v[t][:, :, ax] == plane, axis=1)
            if trim:
                _, v0 = tri_coords(v, t)
                keep = np.ones(len(t), bool)
                for ax in range(3):
                    keep &= (v0[:, ax] >= split) == bool(idx[ax])
                caps += int(np.count_nonzero(on_plane & keep))
                t = t[keep]
            else:
                caps += int(np.count_nonzero(on_plane))
            verts.append(v)
            tris.append(t + off)
            off += len(v)
        V, T = np.concatenate(verts), np.concatenate(tris)
        Vm, Tm = common.merge_by_coordinate(V, T)
        rep = common.mesh_report(Vm, Tm)
        used = np.unique(Tm)
        res = {
            k: rep[k]
            for k in (
                "triangles",
                "closed_oriented_manifold",
                "directed_edge_duplicates",
                "edges_without_twin",
                "nonmanifold_vertices",
                "euler_characteristic",
                "volume_mm3",
            )
        }
        res["cap_triangles_seen"] = caps
        R, _ = tri_coords(Vm, Tm)
        res["vs_whole"] = compare(Rw, R)
        res["canonical_hash_equal_to_whole"] = common.mesh_hash(Vm, Tm) == common.mesh_hash(
            *common.merge_by_coordinate(vw, tw)
        )
        # nearest-coordinate deviation of stitched triangle vertices from the reference ones
        if res["vs_whole"]["only_in_stitched"]:
            from scipy.spatial import cKDTree

            tree = cKDTree(vw)
            dist, _ = tree.query(Vm[used])
            res["stitched_vertex_to_nearest_ref_vertex_max_mm"] = float(dist.max())
            res["stitched_vertices_not_bitwise_in_ref"] = int(np.count_nonzero(dist > 0))
        return res

    out["abutting_no_trim"] = staged(0, trim=False)
    out["overlap_trim"] = staged(a.overlap, trim=True)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
