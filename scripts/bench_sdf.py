"""Round-trip benchmark: mesh -> SDF -> mesh on real assets (needs SHOE_ASSETS_ROOT)."""
import resource
import sys
import time

import numpy as np
import trimesh

from shoe_designer import sdf
from shoe_designer.assets import DEFAULT_MANIFEST, load_asset, load_catalog


def rss_mb():
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r / 2**20 if sys.platform == "darwin" else r / 2**10


def err(a, b, n=20000):
    pts = trimesh.sample.sample_surface(a, n, seed=0)[0]
    d = np.abs(sdf.signed_distance(b, pts))
    return d.mean(), np.percentile(d, 99), d.max()


def main(asset_id, h, band):
    mesh = load_asset(asset_id, load_catalog(DEFAULT_MANIFEST)).mesh
    grid = sdf.Grid.for_mesh(mesh, h)
    t0 = time.perf_counter()
    phi = sdf.sdf_from_mesh(mesh, grid, band=band)
    t1 = time.perf_counter()
    out, rep = sdf.sdf_to_mesh(phi, grid)
    t2 = time.perf_counter()
    e_out = err(out, mesh)
    e_in = err(mesh, out)
    print(f"{asset_id} h={h} dz={grid.dz:.3f} shape={grid.shape} voxels={grid.n_voxels/1e6:.1f}M faces_in={len(mesh.faces)}")
    print(f"  sdf {t1-t0:.1f}s  mc {t2-t1:.1f}s  peak RSS {rss_mb():.0f} MB  faces_out={rep.n_faces} watertight={out.is_watertight}")
    print(f"  dropped {rep.dropped_volume_mm3:.2f} mm3 in {rep.n_components} comps; volume in {mesh.volume:.0f} out {out.volume:.0f} mm3")
    print(f"  out->in mean/p99/max {e_out[0]:.3f}/{e_out[1]:.3f}/{e_out[2]:.3f} mm; in->out {e_in[0]:.3f}/{e_in[1]:.3f}/{e_in[2]:.3f} mm; half-voxel {0.5*max(grid.spacing):.3f}")


if __name__ == "__main__":
    main(sys.argv[1], float(sys.argv[2]), float(sys.argv[3]) if len(sys.argv) > 3 else None)
