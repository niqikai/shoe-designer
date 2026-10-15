"""Binary serialization checks protect genuine triangles, including tiny features."""
from pathlib import Path
import struct

import pytest

from shoe_poc.exporting import export_mesh, remove_exact_zero_area_records


def _record(points, normal=(0, 0, 1), attribute=0):
    return struct.pack('<12fH', *normal, *(c for point in points for c in point), attribute)


def _binary(records, count=None):
    # A binary header can begin with 'solid'; it must still remain byte-identical.
    header = b'solid binary CAD test'.ljust(80, b'\x9f')
    return header + struct.pack('<I', len(records) if count is None else count) + b''.join(records)


def test_duplicate_vertex_and_collinear_records_removed_only(tmp_path):
    positive = _record([(0, 0, 0), (1, 0, 0), (0, 1, 0)], normal=(0, 0, -2), attribute=65535)
    duplicate = _record([(0, 0, 0), (1, 0, 0), (1, 0, 0)])
    collinear = _record([(1, 2, 3), (2, 4, 6), (3, 6, 9)])
    second_positive = _record([(0, 0, 2), (3, 0, 2), (0, 5, 2)], normal=(4, 5, 6), attribute=123)
    path = tmp_path / 'shoe.stl'
    path.write_bytes(_binary([positive, duplicate, collinear, second_positive]))
    result = remove_exact_zero_area_records(path)
    assert path.read_bytes() == _binary([positive, second_positive])
    assert result['native_triangle_count'] == 4
    assert result['discarded_exact_zero_area_records'] == 2
    assert result['retained_triangle_count'] == 2
    assert result['positive_area_triangles_preserved_byte_for_byte'] is True
    assert result['area_tolerance'] == 0
    assert result['hole_filling'] is False
    assert result['vertex_or_normal_changes'] is False


def test_very_tiny_positive_area_survives_byte_for_byte(tmp_path):
    tiny = _record([(0, 0, 0), (1e-30, 0, 0), (0, 1e-30, 0)], attribute=29)
    degenerate = _record([(0, 0, 0)] * 3)
    path = tmp_path / 'tiny.stl'
    path.write_bytes(_binary([degenerate, tiny]))
    result = remove_exact_zero_area_records(path)
    assert result['discarded_exact_zero_area_records'] == 1
    assert path.read_bytes() == _binary([tiny])


def test_valid_binary_without_degeneracy_is_unchanged_and_idempotent(tmp_path):
    positive = _record([(0, 0, 0), (1, 0, 0), (0, 1, 0)], attribute=47)
    payload = _binary([positive])
    path = tmp_path / 'valid.stl'
    path.write_bytes(payload)
    for _ in range(2):
        result = remove_exact_zero_area_records(path)
        assert result['discarded_exact_zero_area_records'] == 0
        assert path.read_bytes() == payload


@pytest.mark.parametrize('payload', [
    b'', b'\0' * 83,
    _binary([], count=1),
    _binary([_record([(0, 0, 0), (1, 0, 0), (0, 1, 0)])])[:-1],
    _binary([_record([(0, 0, 0), (1, 0, 0), (0, 1, 0)])]) + b'X',
    b'solid ascii\nfacet normal 0 0 1\nendsolid\n',
])
def test_malformed_binary_rejected_without_modifying_input(tmp_path, payload):
    path = tmp_path / 'bad.stl'
    path.write_bytes(payload)
    with pytest.raises(ValueError, match='binary STL'):
        remove_exact_zero_area_records(path)
    assert path.read_bytes() == payload


def test_native_cad_export_is_real_binary_and_keeps_closed_box(tmp_path):
    from build123d import Solid
    import trimesh
    path = tmp_path / 'cad.stl'
    report = export_mesh(Solid.make_box(12, 8, 5), path)
    payload = path.read_bytes()
    count = struct.unpack_from('<I', payload, 80)[0]
    assert len(payload) == 84+50*count
    assert report['retained_triangle_count'] == count > 0
    mesh = trimesh.load_mesh(Path(path), process=False)
    # Native STL repeats per-triangle vertices, so weld exact duplicates for the
    # independent connectivity check without moving any vertex positions.
    mesh.merge_vertices()
    assert mesh.is_watertight
    assert mesh.volume == pytest.approx(12*8*5, abs=1e-5)


def test_native_absolute_deflection_and_cache_reset_on_curved_cad(tmp_path):
    from build123d import Solid
    import numpy as np
    import trimesh
    sphere = Solid.make_sphere(50)
    original_volume = sphere.volume
    measurements = {}
    # Fine first: without clearing cached triangulations, the second coarser
    # export can incorrectly reuse the fine mesh. A generous angular tolerance
    # ensures the linear deflection determines this geometric comparison.
    for tolerance in (0.15, 3.0):
        path = tmp_path / f'sphere-{tolerance}.stl'
        report = export_mesh(sphere, path, tolerance=tolerance, angular_tolerance=1.0)
        assert report['linear_deflection_mm'] == tolerance
        assert report['relative_deflection'] is False
        assert report['cached_triangulations_cleared'] is True
        mesh = trimesh.load_mesh(path, process=False)
        radial_sag = 50-np.linalg.norm(mesh.triangles_center, axis=1)
        measurements[tolerance] = (len(mesh.faces), float(radial_sag.max()))
    assert measurements[0.15][1] <= 0.15
    assert measurements[3.0][1] > 1.0
    assert measurements[3.0][0] < measurements[0.15][0]/5
    assert sphere.volume == pytest.approx(original_volume, rel=1e-12)
