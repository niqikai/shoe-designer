"""Cached CAD fields need the same immutable inputs and exact sampling grid."""
import copy
import json
import math
from types import SimpleNamespace

import numpy as np
import pytest

from shoe_poc.lattice.cache import load_certified_domain
from shoe_poc.lattice.domain import plan_grid
from shoe_poc.reporting import sha256
from shoe_poc.schemas.spec import LatticeDomainParameters


class Shape:
    def bounding_box(self):
        return SimpleNamespace(min=SimpleNamespace(X=0., Y=0.), max=SimpleNamespace(X=8., Y=6.))


def write_json(path, value):
    path.write_text(json.dumps(value))


def save_cache(directory, field, metadata, provenance, *, npz_metadata=None, origin=None):
    np.savez_compressed(directory / "domain_field.npz", field_mm=field,
        origin_mm=metadata["origin_mm"] if origin is None else origin,
        spacing_mm=metadata["spacing_mm"],
        metadata_json=np.asarray(json.dumps(metadata if npz_metadata is None else npz_metadata)))
    write_json(directory / "domain_metadata.json", metadata)
    provenance.update(domain_field_sha256=sha256(directory / "domain_field.npz"),
                      domain_metadata_sha256=sha256(directory / "domain_metadata.json"))
    write_json(directory / "domain_provenance.json", provenance)


@pytest.fixture
def cache(tmp_path):
    settings = LatticeDomainParameters()
    plan = plan_grid([[0, 0, -4], [8, 6, 0]], .4, max_memory_mb=64)
    field = np.full(plan.shape, .4, dtype=np.float32)
    field[5:10, 5:10, 4:7] = -.5
    metadata = {"axis_order": "XYZ", "shape_xyz": list(plan.shape), "origin_mm": list(plan.origin_mm),
        "spacing_mm": .4, "clearance_mm": 2., "computation_bounds_mm": [[0, 0, -4], [8, 6, 0]],
        "sole_z_bounds_mm": [-4, 0], "voxel_count": plan.voxel_count,
        "estimated_peak_bytes": plan.estimated_peak_bytes,
        "certified_negative_node_count": int(np.count_nonzero(field < 0)),
        "independent_native_samples": {"count": 1, "failed_count": 0, "measurement_errors": [], "points_mm": [[2, 2, -2]]},
        "native_scanlines": {"line_count": 1, "measurement_errors": []},
        "certificate": {"query_count": 1, "anchor_classification_errors": [],
                        "cell_diagonal_reserve_mm": math.sqrt(3)*.4, "numeric_reserve_mm": 8e-6}}
    baseline = {"accepted_run_id": "accepted-m1", "source_sha256": "source-hash", "files": {"base_shoe.step": {"sha256": "cad-hash"}}}
    provenance = {"run_id": "independent-domain-run", "baseline": baseline,
                  "parameters": {"spacing_mm": .4, "clearance_mm": 2., "sole_z_bounds_mm": [-4, 0], "bounds_mm": None}}
    save_cache(tmp_path, field, metadata, provenance)
    kwargs = {"baseline": baseline, "shape": Shape(), "z_min_mm": -4., "z_max_mm": 0.,
              "settings": settings, "spacing_mm": .4, "max_voxels": 100_000, "max_memory_mb": 64}
    return tmp_path, field, metadata, provenance, kwargs


def test_replays_exact_cached_field_without_new_sampling(cache):
    directory, field, metadata, _, kwargs = cache
    result = load_certified_domain(directory, **kwargs)
    assert result.occupancy is None
    assert np.array_equal(result.field_mm, field)
    assert result.metadata == metadata
    assert result.plan.shape == field.shape


def test_planar_cache_cannot_be_replayed_as_curved_domain(cache, monkeypatch):
    from shoe_poc.lattice.envelope import SoleEnvelope
    directory, _, _, _, kwargs = cache
    kwargs['settings'] = LatticeDomainParameters(mode='curved_lower_foot')
    kwargs['envelope'] = SoleEnvelope({'version': 'lower-foot-roof-polyline-v1',
        'lower_datum_z_mm': -4., 'upper_points_xz_mm': [[0., -1.], [8., 0.]]})
    monkeypatch.setattr(np, 'load', lambda *a, **k: pytest.fail('stale curved contract must reject before field load'))
    with pytest.raises(ValueError, match='envelope'):
        load_certified_domain(directory, **kwargs)


@pytest.mark.parametrize('changed', ['roof', 'guide_hash'])
def test_curved_cache_requires_identical_roof_and_frozen_guide_identity(cache, monkeypatch, changed):
    from shoe_poc.lattice.envelope import SoleEnvelope
    directory, field, metadata, provenance, kwargs = cache
    identity = {'version': 'lower-foot-roof-polyline-v1', 'lower_datum_z_mm': -4.,
        'upper_points_xz_mm': [[0., -1.], [8., 0.]], 'source_geometry_report_sha256': 'frozen-guide'}
    metadata['envelope'] = copy.deepcopy(identity)
    provenance['parameters']['envelope'] = copy.deepcopy(identity)
    save_cache(directory, field, metadata, provenance)
    kwargs['settings'] = LatticeDomainParameters(mode='curved_lower_foot')
    kwargs['envelope'] = SoleEnvelope(identity)
    assert np.array_equal(load_certified_domain(directory, **kwargs).field_mm, field)
    if changed == 'roof':
        identity['upper_points_xz_mm'][0][1] = -.5
    else:
        identity['source_geometry_report_sha256'] = 'different-guide'
    monkeypatch.setattr(np, 'load', lambda *a, **k: pytest.fail('stale envelope must reject before field load'))
    with pytest.raises(ValueError, match='envelope'):
        load_certified_domain(directory, **kwargs)


@pytest.mark.parametrize("change", ["source", "accepted_run", "support_hash"])
def test_stale_baseline_is_rejected_before_numpy_load(cache, monkeypatch, change):
    directory, _, _, _, kwargs = cache
    kwargs["baseline"] = copy.deepcopy(kwargs["baseline"])
    if change == "source":
        kwargs["baseline"]["source_sha256"] = "other-source"
    elif change == "accepted_run":
        kwargs["baseline"]["accepted_run_id"] = "other-m1-run"
    else:
        kwargs["baseline"]["files"]["base_shoe.step"]["sha256"] = "other-cad"
    monkeypatch.setattr(np, "load", lambda *a, **k: pytest.fail("NPZ must not load before provenance validation"))
    with pytest.raises(ValueError, match="baseline"):
        load_certified_domain(directory, **kwargs)


@pytest.mark.parametrize("name", ["domain_field.npz", "domain_metadata.json"])
def test_changed_cache_hash_is_rejected(cache, name):
    directory, _, _, _, kwargs = cache
    with (directory / name).open("ab") as stream:
        stream.write(b"modified")
    with pytest.raises(ValueError, match="hash"):
        load_certified_domain(directory, **kwargs)


@pytest.mark.parametrize("change", ["spacing", "clearance", "bounds", "z"])
def test_requested_sampling_parameters_cannot_reuse_other_grid(cache, monkeypatch, change):
    directory, _, _, _, kwargs = cache
    if change == "spacing":
        kwargs["spacing_mm"] = .6
    elif change == "clearance":
        kwargs["settings"] = LatticeDomainParameters(clearance_mm=3.)
    elif change == "bounds":
        kwargs["settings"] = LatticeDomainParameters(bounds_mm=[[1., 1., -3.], [7., 5., -1.]])
    else:
        kwargs["z_min_mm"] = -3.
    monkeypatch.setattr(np, "load", lambda *a, **k: pytest.fail("stale parameters must reject before NPZ load"))
    with pytest.raises(ValueError, match="provenance"):
        load_certified_domain(directory, **kwargs)


@pytest.mark.parametrize("limit", ["max_voxels", "max_memory_mb"])
def test_resource_preflight_rejects_before_numpy_load(cache, monkeypatch, limit):
    directory, _, _, _, kwargs = cache
    kwargs[limit] = 1
    monkeypatch.setattr(np, "load", lambda *a, **k: pytest.fail("resource preflight must precede NPZ load"))
    with pytest.raises(ValueError, match="resource"):
        load_certified_domain(directory, **kwargs)


@pytest.mark.parametrize("change", ["metadata_mismatch", "npz_origin", "field_shape", "field_nan", "boundary", "negative_count"])
def test_invalid_field_or_npz_metadata_is_rejected_even_with_fresh_hash(cache, change):
    directory, field, metadata, provenance, kwargs = cache
    extra = {}
    if change == "metadata_mismatch":
        extra["npz_metadata"] = dict(metadata, clearance_mm=3.)
    elif change == "npz_origin":
        extra["origin"] = [0., 0., -4.]
    elif change == "field_shape":
        field = field[:-1]
    elif change == "field_nan":
        field[6, 6, 5] = np.nan
    elif change == "boundary":
        field[0, 0, 0] = -1
        metadata["certified_negative_node_count"] += 1
    else:
        metadata["certified_negative_node_count"] += 1
    save_cache(directory, field, metadata, provenance, **extra)
    with pytest.raises(ValueError):
        load_certified_domain(directory, **kwargs)


@pytest.mark.parametrize("change", ["sample_zero", "failed", "sample_errors", "line_errors", "anchor_errors", "no_run"])
def test_incomplete_or_failed_cad_evidence_never_qualifies_cache(cache, change):
    directory, field, metadata, provenance, kwargs = cache
    if change == "sample_zero":
        metadata["independent_native_samples"]["count"] = 0
    elif change == "failed":
        metadata["independent_native_samples"]["failed_count"] = 1
    elif change == "sample_errors":
        metadata["independent_native_samples"]["measurement_errors"] = ["unknown native result"]
    elif change == "line_errors":
        metadata["native_scanlines"]["measurement_errors"] = ["native scanline failed"]
    elif change == "anchor_errors":
        metadata["certificate"]["anchor_classification_errors"] = [[2, 2, -2]]
    else:
        provenance["run_id"] = ""
    save_cache(directory, field, metadata, provenance)
    with pytest.raises(ValueError):
        load_certified_domain(directory, **kwargs)
