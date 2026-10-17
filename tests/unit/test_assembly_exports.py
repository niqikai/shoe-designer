import json

import trimesh

from shoe_poc.assembly.exports import export_assembly, read_3mf64, read_ascii_stl64


def test_float64_manufacturing_exports_and_glb_preview_roundtrip(tmp_path):
    mesh = trimesh.creation.box(extents=(12., 8., 4.))
    result = export_assembly(mesh, tmp_path, self_intersection_pair_budget=10_000)

    assert result['manufacturing_status'] == 'NOT_VERIFIED'
    assert result['artifacts']['final_shoe.stl']['status'] == 'PASS'
    assert result['artifacts']['final_shoe.3mf']['status'] == 'PASS'
    assert result['artifacts']['preview.glb']['status'] in ('PASS', 'PARTIAL')
    for filename, reader in [('final_shoe.stl', read_ascii_stl64), ('final_shoe.3mf', read_3mf64)]:
        restored = reader(tmp_path/filename)
        assert len(restored.faces) == len(mesh.faces)
        assert (restored.bounds == mesh.bounds).all()
        assert abs(restored.volume-mesh.volume) < 1e-10
    for filename in ('final_shoe.stl.reimport.json', 'final_shoe.3mf.reimport.json', 'preview.glb.reimport.json'):
        report = json.loads((tmp_path/filename).read_text())
        assert report['repair_applied'] is False
        assert report['components_removed'] == 0


def test_multi_body_core_shell_is_exported_as_partial_prototype(tmp_path):
    first = trimesh.creation.box(extents=(4., 4., 4.))
    second = first.copy()
    second.apply_translation((10., 0., 0.))
    mesh = trimesh.util.concatenate((first, second))

    result = export_assembly(mesh, tmp_path, require_single_body=False,
                             self_intersection_pair_budget=10_000)

    assert result['status'] == 'PARTIAL'
    assert result['artifacts']['m3_core_shell_prototype.stl']['role'] == 'experimental_prototype'
    assert result['artifacts']['m3_core_shell_prototype.3mf']['single_body_required'] is False
