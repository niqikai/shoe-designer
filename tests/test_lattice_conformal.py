"""#46 checkpoint 1: the independent rod-diameter measurement and the conformal spec loader."""
import numpy as np
import pytest

from shoe_designer import lattice_conformal as C
from shoe_designer.lattice import LatticeError


def tube(dia, direction, h=0.5, n=80):
    g = np.arange(n) * h
    X, Y, Z = np.meshgrid(g, g, g, indexing="ij")
    d = np.asarray(direction, float)
    d /= np.linalg.norm(d)
    V = np.stack([X - 5.0, Y - 15.0, Z - 20.0], -1)
    r = np.linalg.norm(V - (V @ d)[..., None] * d, axis=-1)
    return r < dia / 2


@pytest.mark.parametrize("direction", [(1, 0, 0), (1, 0.6, 0.3), (1, 1, 1)])
@pytest.mark.parametrize("dia", [2.4, 3.5, 5.5])
def test_rod_measurement_within_one_voxel(dia, direction):
    h = 0.5
    r = C.rod_diameters(tube(dia, direction, h), h)
    assert r["n"] > 20
    assert abs(r["median"] - dia) <= h  # skeleton estimator is calibrated to better than 1 voxel on straight tubes
    assert r["p05"] >= dia - h - 1e-6


def test_specs_load_and_reject_unknown_keys():
    specs = C.load_specs("config/lattice_conformal.toml")
    assert [s.name for s in specs][:3] == ["c0_direct", "c1_chart", "c2_flow"]
    assert not specs[0].conformal and specs[2].flow_amp_deg > 0
    with pytest.raises(LatticeError):
        C.ConformalSpec.from_dict({"name": "x", "bogus": 1})
