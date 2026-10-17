"""Lossless float64 manufacturing exports and explicitly quantized GLB previews.

No repair, tolerance welding, face removal, smoothing or shell reorientation is
performed. STL is triangle soup: exact coordinate indexing is lossless and is
checked against the original oriented triangle coordinates on readback.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import time
import xml.etree.ElementTree as ET
import zipfile

import numpy as np
import trimesh

CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CONTENT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
MODEL_PATH = "3D/3dmodel.model"
IDENTITY_3MF = "1 0 0 0 1 0 0 0 1 0 0 0"
PREVIEW_TRANSFORM = "(x,y,z)_mm -> (x,z,-y)*0.001_m; right-handed Z-up to Y-up"


def _arrays(mesh: trimesh.Trimesh) -> tuple[np.ndarray, np.ndarray]:
    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.faces)
    if vertices.ndim != 2 or vertices.shape[1:] != (3,) or not len(vertices):
        raise ValueError("Expected nonempty (n,3) float64 vertices")
    if faces.ndim != 2 or faces.shape[1:] != (3,) or not len(faces) or faces.dtype.kind not in "iu":
        raise ValueError("Expected nonempty (n,3) integer triangle indices")
    if not np.isfinite(vertices).all() or faces.min() < 0 or faces.max() >= len(vertices):
        raise ValueError("Nonfinite vertices or out-of-range triangle indices")
    return vertices, faces.astype(np.int64, copy=False)


def _mesh(vertices, faces) -> trimesh.Trimesh:
    return trimesh.Trimesh(vertices=np.asarray(vertices, dtype=np.float64),
                           faces=np.asarray(faces, dtype=np.int64), process=False,
                           validate=False)


def _index_exact(triangles: np.ndarray) -> trimesh.Trimesh:
    vertices, inverse = np.unique(triangles.reshape(-1, 3), axis=0, return_inverse=True)
    mesh = _mesh(vertices, inverse.reshape(-1, 3))
    if not np.array_equal(np.asarray(mesh.triangles), triangles):
        raise ValueError("Exact indexing altered triangle coordinates")
    return mesh


def write_ascii_stl64(mesh: trimesh.Trimesh, path: str | Path) -> None:
    """Explicit 17-significant-digit ASCII STL; normals never drive topology."""
    vertices, faces = _arrays(mesh)
    with Path(path).open("w", encoding="ascii", newline="\n") as stream:
        stream.write("solid shoe_m3_float64\n")
        for face in faces:
            triangle = vertices[face]
            normal = np.cross(triangle[1] - triangle[0], triangle[2] - triangle[0])
            length = np.linalg.norm(normal)
            if not length > 0 or not np.isfinite(length):
                raise ValueError("Cannot export degenerate or nonfinite triangle")
            normal /= length
            stream.write("  facet normal " + " ".join(format(x, ".17g") for x in normal) + "\n    outer loop\n")
            for point in triangle:
                stream.write("      vertex " + " ".join(format(x, ".17g") for x in point) + "\n")
            stream.write("    endloop\n  endfacet\n")
        stream.write("endsolid shoe_m3_float64\n")


def read_ascii_stl64(path: str | Path) -> trimesh.Trimesh:
    """Strict complete-facet grammar and Python binary64 decimal conversion."""
    triangles = []
    with Path(path).open(encoding="ascii") as stream:
        lines = iter(line.strip().split() for line in stream if line.strip())
        start = next(lines, [])
        if start != ["solid", "shoe_m3_float64"]:
            raise ValueError("Expected M3 ASCII STL solid header")
        for tokens in lines:
            if tokens == ["endsolid", "shoe_m3_float64"]:
                if next(lines, None) is not None:
                    raise ValueError("Trailing ASCII STL data")
                break
            if len(tokens) != 5 or tokens[:2] != ["facet", "normal"]:
                raise ValueError("Expected ASCII STL facet normal")
            try:
                normal = np.asarray([float(x) for x in tokens[2:]])
                if not np.isfinite(normal).all() or next(lines) != ["outer", "loop"]:
                    raise ValueError("Invalid ASCII STL normal or outer loop")
                triangle = []
                for _ in range(3):
                    vertex = next(lines)
                    if len(vertex) != 4 or vertex[0] != "vertex":
                        raise ValueError("Expected three ASCII STL vertices")
                    triangle.append([float(x) for x in vertex[1:]])
                if next(lines) != ["endloop"] or next(lines) != ["endfacet"]:
                    raise ValueError("Incomplete ASCII STL facet")
                triangles.append(triangle)
            except (StopIteration, OverflowError) as exc:
                raise ValueError("Truncated or invalid ASCII STL facet") from exc
        else:
            raise ValueError("Missing ASCII STL endsolid")
    points = np.asarray(triangles, dtype=np.float64)
    if not len(points) or not np.isfinite(points).all():
        raise ValueError("Empty or nonfinite ASCII STL vertices")
    return _index_exact(points)


def _independent_stl_triangles(path: Path) -> np.ndarray:
    """Independent NumPy float64 parser of the serialized vertex lines.

    The strict reader checks grammar separately. This numerical reader does not
    call it or trimesh's STL parser, whose installed implementation may cast to
    float32. It is deliberately reported as a limited numeric cross-check.
    """
    with path.open(encoding="ascii") as stream:
        rows = (line.split("vertex", 1)[1] for line in stream
                if line.lstrip().startswith("vertex "))
        points = np.loadtxt(rows, dtype=np.float64, ndmin=2)
    if points.shape[1:] != (3,) or len(points) % 3:
        raise ValueError("Independent NumPy STL reader found invalid vertex data")
    return points.reshape(-1, 3, 3)


def write_3mf64(mesh: trimesh.Trimesh, path: str | Path) -> None:
    """Standard core 3MF package with decimal17g, millimetres and identity build."""
    vertices, faces = _arrays(mesh)
    # Stream model XML so a large manufacturing mesh needs no XML DOM copy.
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr("[Content_Types].xml", f'<?xml version="1.0" encoding="UTF-8"?><Types xmlns="{CONTENT_NS}"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>')
        archive.writestr("_rels/.rels", f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{REL_NS}"><Relationship Target="/{MODEL_PATH}" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
        with archive.open(MODEL_PATH, "w", force_zip64=True) as stream:
            stream.write((f'<?xml version="1.0" encoding="UTF-8"?><model xmlns="{CORE_NS}" unit="millimeter" xml:lang="en-US"><resources><object id="1" type="model" name="final_shoe"><mesh><vertices>\n').encode())
            for x, y, z in vertices:
                stream.write(f'<vertex x="{x:.17g}" y="{y:.17g}" z="{z:.17g}"/>\n'.encode())
            stream.write(b"</vertices><triangles>\n")
            for a, b, c in faces:
                stream.write(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>\n'.encode())
            stream.write((f'</triangles></mesh></object></resources><build><item objectid="1" transform="{IDENTITY_3MF}"/></build></model>').encode())


def read_3mf64(path: str | Path) -> trimesh.Trimesh:
    """Strict reader of the deliberately small manufacturing package protocol."""
    tag = lambda name: f"{{{CORE_NS}}}{name}"
    with zipfile.ZipFile(path) as archive:
        if set(archive.namelist()) != {"[Content_Types].xml", "_rels/.rels", MODEL_PATH}:
            raise ValueError("Unexpected 3MF package contents")
        rels = ET.fromstring(archive.read("_rels/.rels"))
        relationships = list(rels)
        if len(relationships) != 1 or relationships[0].get("Target") != f"/{MODEL_PATH}":
            raise ValueError("Invalid 3MF model relationship")
        root = ET.fromstring(archive.read(MODEL_PATH))
    if root.tag != tag("model") or root.get("unit") != "millimeter":
        raise ValueError("Expected standard 3MF model with millimeter units")
    resources, build = root.find(tag("resources")), root.find(tag("build"))
    objects = [] if resources is None else list(resources)
    items = [] if build is None else list(build)
    if (len(objects) != 1 or objects[0].tag != tag("object") or objects[0].get("id") != "1"
            or objects[0].get("type") != "model" or len(items) != 1
            or items[0].tag != tag("item") or items[0].get("objectid") != "1"
            or items[0].get("transform") != IDENTITY_3MF):
        raise ValueError("Expected one 3MF mesh object and identity build item")
    obj = objects[0]
    if len(obj) != 1 or obj[0].tag != tag("mesh"):
        raise ValueError("Expected one 3MF mesh")
    mesh = obj[0]
    if len(mesh) != 2 or mesh[0].tag != tag("vertices") or mesh[1].tag != tag("triangles"):
        raise ValueError("Invalid 3MF vertices/triangles protocol")
    if any(v.tag != tag("vertex") or set(v.attrib) != {"x", "y", "z"} for v in mesh[0]):
        raise ValueError("Invalid 3MF vertex")
    if any(f.tag != tag("triangle") or set(f.attrib) != {"v1", "v2", "v3"} for f in mesh[1]):
        raise ValueError("Invalid 3MF triangle")
    result = _mesh([[float(v.get(axis)) for axis in ("x", "y", "z")] for v in mesh[0]],
                   [[int(f.get(axis)) for axis in ("v1", "v2", "v3")] for f in mesh[1]])
    _arrays(result)
    return result


def _read_independent_scene(path: Path) -> tuple[trimesh.Trimesh, dict]:
    scene = trimesh.load_scene(path, process=False, maintain_order=True)
    if len(scene.geometry) != 1 or len(scene.graph.nodes_geometry) != 1:
        raise ValueError("Independent reader expected exactly one mesh instance")
    node = scene.graph.nodes_geometry[0]
    transform, geometry = scene.graph[node]
    mesh = scene.geometry[geometry].copy()
    # Apply the actual independently parsed scene transform, never an assumed one.
    mesh.apply_transform(transform)
    return mesh, {"reader": f"trimesh {trimesh.__version__}",
                  "process": False, "scene_transform": transform.tolist(),
                  "units": scene.metadata.get("units", mesh.metadata.get("units"))}


def _comparison(reference: trimesh.Trimesh, readback: trimesh.Trimesh) -> dict:
    original, faces = _arrays(reference)
    actual, read_faces = _arrays(readback)
    triangles_equal_shape = faces.shape == read_faces.shape
    # STL necessarily changes indexing; oriented triangle identity is authoritative.
    delta = (np.abs(original[faces] - actual[read_faces])
             if triangles_equal_shape else np.asarray([np.inf]))
    return {"triangle_coordinates_exact": bool(triangles_equal_shape and np.all(delta == 0)),
            "vertex_coordinates_exact": bool(original.shape == actual.shape and np.array_equal(original, actual)),
            "face_indices_exact": bool(np.array_equal(faces, read_faces)),
            "maximum_coordinate_error_mm": float(np.max(delta)),
            "reference_bounds_mm": reference.bounds.tolist(),
            "readback_bounds_mm": readback.bounds.tolist(),
            "reference_volume_mm3": float(reference.volume),
            "readback_volume_mm3": float(readback.volume)}


def _validation(mesh, *, area_floor_mm2, volume_floor_mm3, self_intersection_pair_budget,
                require_single_body=True):
    from .geometry import validate_native_mesh
    return validate_native_mesh(mesh, require_single_body=require_single_body,
                                area_floor_mm2=area_floor_mm2,
                                volume_floor_mm3=volume_floor_mm3,
                                self_intersection_pair_budget=self_intersection_pair_budget)


def quantization_preflight(mesh: trimesh.Trimesh, *, preview=False,
                           area_floor_mm2=1e-12, volume_floor_mm3=1e-7,
                           self_intersection_pair_budget=2_000_000,
                           require_single_body=True) -> tuple[trimesh.Trimesh, dict]:
    vertices, faces = _arrays(mesh)
    encoded = vertices[:, [0, 2, 1]] * [0.001, 0.001, -0.001] if preview else vertices
    quantized = encoded.astype(np.float32).astype(np.float64)
    decoded = quantized[:, [0, 2, 1]] * [1000., -1000., 1000.] if preview else quantized
    result = _mesh(decoded, faces.copy())
    old_triangles, new_triangles = vertices[faces], decoded[faces]
    old_normals = np.cross(old_triangles[:, 1] - old_triangles[:, 0], old_triangles[:, 2] - old_triangles[:, 0])
    new_normals = np.cross(new_triangles[:, 1] - new_triangles[:, 0], new_triangles[:, 2] - new_triangles[:, 0])
    area = np.linalg.norm(new_normals, axis=1) / 2
    report = {"encoding": "float32 POSITION in metres" if preview else "binary STL float32 in millimetres",
              "role": "preview_only" if preview else "preflight_only_not_written",
              "transform": PREVIEW_TRANSFORM if preview else "identity; mm; X/Y/Z-up",
              "vertex_identities_retained": True, "face_indices_retained": True,
              "new_coordinate_collisions": int(len(np.unique(vertices, axis=0)) - len(np.unique(decoded, axis=0))),
              "zero_area_triangle_count": int(np.count_nonzero(area == 0)),
              "below_area_floor_triangle_count": int(np.count_nonzero(area < area_floor_mm2)),
              "orientation_reversal_or_collapse_count": int(np.count_nonzero(np.einsum("ij,ij->i", old_normals, new_normals) <= 0)),
              "minimum_triangle_area_mm2": float(area.min()),
              "maximum_coordinate_error_mm": float(np.max(np.abs(decoded - vertices))),
              "repair_applied": False, "components_removed": 0}
    report["validation"] = _validation(result, area_floor_mm2=area_floor_mm2,
                                       volume_floor_mm3=volume_floor_mm3,
                                       self_intersection_pair_budget=self_intersection_pair_budget,
                                       require_single_body=require_single_body)
    validation = report['validation']
    invalid = (report['new_coordinate_collisions'] or report['below_area_floor_triangle_count']
               or report['orientation_reversal_or_collapse_count']
               or validation.get('topology_status', validation.get('geometry_status', validation.get('status'))) != 'PASS'
               or validation.get('self_intersections', {}).get('status') == 'FAIL')
    report['status'] = ('FAIL' if invalid else 'PARTIAL'
                        if validation.get('self_intersections', {}).get('status') == 'NOT_VERIFIED' else 'PASS')
    return result, report


def export_assembly(mesh: trimesh.Trimesh, output_directory: str | Path, *,
                    assembled_mesh: trimesh.Trimesh | None = None,
                    area_floor_mm2=1e-12, volume_floor_mm3=1e-7,
                    self_intersection_pair_budget=2_000_000,
                    require_single_body=True) -> dict:
    """Write and independently validate required manufacturing and preview files.

    Raises on authoritative input/readback failure and retains all diagnostics.
    A preview failure is explicit in the returned status; manufacturing mesh
    precision or acceptance is never reduced to make the preview succeed.
    """
    from .geometry import require_valid_mesh
    directory = Path(output_directory)
    directory.mkdir(parents=True, exist_ok=True)
    options = {"area_floor_mm2": area_floor_mm2, "volume_floor_mm3": volume_floor_mm3,
               "self_intersection_pair_budget": self_intersection_pair_budget}
    require_valid_mesh(mesh, require_single_body=require_single_body, **options)
    artifacts = {}
    file_rows = ([('assembled_shoe.stl', assembled_mesh), ('final_shoe.stl', mesh), ('final_shoe.3mf', mesh)]
                 if require_single_body else
                 [('m3_core_shell_prototype.stl', mesh), ('m3_core_shell_prototype.3mf', mesh)])
    for filename, source in file_rows:
        if source is None:
            continue
        require_valid_mesh(source, require_single_body=require_single_body, **options)
        started = time.perf_counter()
        path = directory / filename
        is_stl = path.suffix == ".stl"
        (write_ascii_stl64 if is_stl else write_3mf64)(source, path)
        readback = (read_ascii_stl64 if is_stl else read_3mf64)(path)
        comparison = _comparison(source, readback)
        validation = require_valid_mesh(readback, require_single_body=require_single_body, **options)
        if not comparison["triangle_coordinates_exact"]:
            raise ValueError(f"Authoritative {filename} altered oriented triangle coordinates")
        if is_stl:
            independent_triangles = _independent_stl_triangles(path)
            independent_exact = np.array_equal(np.asarray(source.triangles), independent_triangles)
            independent_mesh = _index_exact(independent_triangles)
            independent = {"reader": "NumPy loadtxt float64 (independent numeric parser)",
                           "scope": "decimal vertex records; complete grammar checked by strict reader",
                           "triangle_coordinates_exact": bool(independent_exact),
                           "units": "mm by documented STL external convention; STL has no unit field",
                           "validation": require_valid_mesh(independent_mesh, require_single_body=require_single_body, **options)}
        else:
            independent_mesh, independent = _read_independent_scene(path)
            independent.update(_comparison(source, independent_mesh))
            independent_exact = independent["triangle_coordinates_exact"]
            if independent["units"] != "millimeter":
                raise ValueError("Independent 3MF reader did not retain millimetre units")
            independent["validation"] = require_valid_mesh(independent_mesh, require_single_body=require_single_body, **options)
        if not independent_exact:
            raise ValueError(f"Independent {filename} readback altered triangle coordinates")
        validation_partial = any(row.get('self_intersections', {}).get('status') == 'NOT_VERIFIED'
                                 or row.get('shell_nesting_status') == 'NOT_VERIFIED'
                                 for row in (validation, independent.get('validation', {})))
        report = {"status": "PARTIAL" if validation_partial or not require_single_body else "PASS",
                  "encoding": "ASCII STL decimal17g" if is_stl else "standard core 3MF decimal17g",
                  "coordinate_precision": "float64 round-trip, 17 significant decimal digits",
                  "role": "experimental_prototype" if not require_single_body else "manufacturing",
                  "single_body_required": bool(require_single_body),
                  "unit": "mm", "transform": "identity; X heel-to-toe, Y width, Z up",
                  "repair_applied": False, "components_removed": 0,
                  "comparison": comparison, "validation": validation, "independent_reader": independent}
        report_path = directory / f"{filename}.reimport.json"
        report_path.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
        artifacts[filename] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                               "bytes": path.stat().st_size, "seconds": time.perf_counter() - started,
                               "report_path": str(report_path), **report}

    vertices, faces = _arrays(mesh)
    native_path = directory / "native_mesh_float64.npz"
    np.savez_compressed(native_path, protocol_version=np.asarray(1, dtype=np.int64),
                        vertices=vertices, faces=faces)
    with np.load(native_path, allow_pickle=False) as native:
        if not np.array_equal(native["vertices"], vertices) or not np.array_equal(native["faces"], faces):
            raise ValueError("Native diagnostic NPZ readback mismatch")
    _, binary_preflight = quantization_preflight(mesh, require_single_body=require_single_body, **options)
    preview_mesh, preview_preflight = quantization_preflight(mesh, preview=True,
        require_single_body=require_single_body, **options)
    preview_path = directory / "preview.glb"
    preview_positions = vertices[:, [0, 2, 1]] * [0.001, 0.001, -0.001]
    preview = _mesh(preview_positions, faces)
    preview.metadata["units"] = "meters"
    preview_path.write_bytes(trimesh.exchange.gltf.export_glb(trimesh.Scene(preview)))
    imported, reader = _read_independent_scene(preview_path)
    imported_vertices = np.asarray(imported.vertices)[:, [0, 2, 1]] * [1000., -1000., 1000.]
    restored = _mesh(imported_vertices, imported.faces)
    preview_comparison = _comparison(preview_mesh, restored)
    preview_validation = _validation(restored, require_single_body=require_single_body, **options)
    preview_topology = preview_validation.get('topology_status', preview_validation.get('geometry_status', preview_validation.get('status')))
    preview_status = ("PASS" if preview_validation.get('self_intersections', {}).get('status') == 'PASS' else "PARTIAL") if (
        preview_preflight["status"] in {"PASS", "PARTIAL"} and preview_comparison["triangle_coordinates_exact"]
        and preview_topology == "PASS") else "FAIL"
    preview_report = {"status": preview_status, "role": "preview_only", "manufacturing_output": False,
                      "encoding": "GLB/glTF POSITION float32 componentType=5126", "unit": "m",
                      "transform": PREVIEW_TRANSFORM, "quantization": preview_preflight,
                      "comparison_to_quantized_reference": preview_comparison,
                      "independent_reader": reader, "readback_validation": preview_validation,
                      "repair_applied": False, "components_removed": 0}
    preview_report_path = directory / "preview.glb.reimport.json"
    preview_report_path.write_text(json.dumps(preview_report, indent=2, allow_nan=False) + "\n")
    artifacts["preview.glb"] = {"path": str(preview_path), "sha256": hashlib.sha256(preview_path.read_bytes()).hexdigest(),
                                "bytes": preview_path.stat().st_size,
                                "report_path": str(preview_report_path), **preview_report}
    artifacts["native_mesh_float64.npz"] = {"path": str(native_path), "role": "diagnostic_numeric_reference",
                                            "sha256": hashlib.sha256(native_path.read_bytes()).hexdigest(),
                                            "bytes": native_path.stat().st_size, "protocol_version": 1,
                                            "allow_pickle": False, "roundtrip_exact": True}
    authoritative_failure = any(row.get('role') in ('manufacturing', 'experimental_prototype')
                                and row.get('status') == 'FAIL' for row in artifacts.values())
    overall = "FAIL" if authoritative_failure else "PARTIAL" if preview_status != "PASS" or any(
        row.get('status') == 'PARTIAL' for row in artifacts.values()) else "PASS"
    return {"status": overall, "manufacturing_status": "NOT_VERIFIED", "preview_status": preview_status,
            "repair_applied": False, "components_removed": 0,
            "binary_stl_float32_preflight": binary_preflight, "artifacts": artifacts}
