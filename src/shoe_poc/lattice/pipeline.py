"""M2 orchestration: accepted CAD is an immutable input, never rebuilt here."""
from __future__ import annotations

import json
from pathlib import Path
import time
import traceback
from uuid import uuid4

import numpy as np

from shoe_poc.reporting import artifact_manifest, environment, sha256, utc_now, write_json
from shoe_poc.schemas import ShoeSpec


GENERATED = ("lattice.stl", "lattice_metadata.json", "gyroid_field.npz", "domain_field.npz",
             "domain_metadata.json", "domain_provenance.json", "shoe_spec.json", "execution_report.json", "failure_report.json",
             "validation_report.json", "preview.png", "artifact_manifest.json", "test_report.json",
             "acceptance_report.json", "parameter_change_report.json", "parameter_sections.png",
             "m1_preservation_report.json", "performance_report.json")


def validate_output_paths(output: Path, base_directory: Path) -> None:
    """Run before diagnostics/RSS threads can write through an output alias."""
    output, base_directory = output.resolve(), base_directory.resolve()
    if output.is_relative_to(base_directory) or base_directory.is_relative_to(output):
        raise ValueError("M2 output and frozen M1 directory must be separate, non-nested directories")
    for name in GENERATED:
        if (output/name).is_symlink():
            raise ValueError(f"M2 generated output must not be a symbolic link: {name}")


def accepted_baseline(directory: Path, spec: ShoeSpec, acceptance_path: Path) -> dict:
    """Require the actual approved run and the same geometric design sections."""
    directory = directory.resolve()
    approval = json.loads(acceptance_path.read_text())
    if approval.get("status") != "ACCEPTED" or approval.get("scope") != "M1_base_shape":
        raise ValueError("M2 requires explicit M1 base-shape acceptance")
    execution = json.loads((directory/"execution_report.json").read_text())
    if execution.get("status") != "PASS" or execution.get("run_id") != approval.get("run_id"):
        raise ValueError("M1 directory does not identify the accepted passing run")
    if not {"base_shoe.step", "base_shoe.stl", "shoe_spec.json", "preview.png", "toe_comparison.png"} <= approval.get("files", {}).keys():
        raise ValueError("M1 approval does not bind all required geometry and preview files")
    for name, record in approval["files"].items():
        path = directory/name
        if not path.is_file() or sha256(path) != record["sha256"] or path.stat().st_size != record["bytes"]:
            raise ValueError(f"Frozen M1 artifact changed: {name}")
    # The historical shape approval is immutable. A separate, versioned M2
    # input lock binds its supporting CAD and execution evidence without
    # retroactively changing the user's M1 decision or generated M1 files.
    lock_path = acceptance_path.with_suffix(".m2_inputs.json")
    lock = json.loads(lock_path.read_text())
    support = {"execution_report.json", "cavity.step", "last_normalized.step",
               "last_metadata.json", "geometry_report.json"}
    if (lock.get("scope") != "M2_immutable_CAD_inputs"
            or lock.get("accepted_run_id") != approval["run_id"]
            or lock.get("acceptance_sha256") != sha256(acceptance_path)
            or not support <= lock.get("files", {}).keys()):
        raise ValueError("M2 supporting CAD input lock does not bind the accepted M1 run")
    for name in support:
        record, path = lock["files"][name], directory/name
        if not path.is_file() or sha256(path) != record["sha256"] or path.stat().st_size != record["bytes"]:
            raise ValueError(f"Frozen M1 supporting input changed: {name}")
    for name in ("cavity.step", "last_normalized.step", "last_metadata.json", "geometry_report.json"):
        record = execution.get("outputs", {}).get(name)
        if not record or not (directory/name).is_file() or sha256(directory/name) != record["sha256"] or (directory/name).stat().st_size != record["bytes"]:
            raise ValueError(f"M1 supporting CAD evidence changed or unbound: {name}")
    frozen_spec = ShoeSpec.load(directory/"shoe_spec.json")
    for section in ("last", "shoe", "upper", "sole", "manufacturing"):
        if getattr(frozen_spec, section) != getattr(spec, section):
            raise ValueError(f"M2 cannot change frozen M1 {section} parameters")
    source = sha256(Path(spec.last.file))
    if source != approval.get("source_sha256") or source != execution.get("input_sha256"):
        raise ValueError("M2 last does not match the accepted source geometry")
    return {"directory": str(directory), "accepted_run_id": approval["run_id"],
            "acceptance_file": str(acceptance_path.resolve()), "acceptance_sha256": sha256(acceptance_path),
            "support_lock_file": str(lock_path.resolve()), "support_lock_sha256": sha256(lock_path),
            "source_sha256": source,
            "files": {name: {"sha256": sha256(directory/name), "bytes": (directory/name).stat().st_size}
                      for name in ("base_shoe.step", "base_shoe.stl", "cavity.step", "last_normalized.step", "shoe_spec.json")}}


def build_lattice(spec_path: Path, output: Path, *, base_directory: Path,
                  acceptance_path: Path, max_voxels: int = 10_000_000,
                  max_field_memory_mb: float = 4096., run_id: str | None = None,
                  domain_cache: Path | None = None) -> dict:
    """Build M2 only and preserve diagnostics on every failed attempt."""
    output, base_directory = output.resolve(), base_directory.resolve()
    validate_output_paths(output, base_directory)
    if domain_cache is not None and domain_cache.resolve() == output:
        raise ValueError("Domain cache and output must differ; preserve the source field")
    if max_voxels <= 0 or not np.isfinite(max_field_memory_mb) or max_field_memory_mb <= 0:
        raise ValueError("M2 voxel and allocation budgets must be positive")
    run_id = run_id or str(uuid4())
    started, timings = time.perf_counter(), {}
    prepared = False
    output.mkdir(parents=True, exist_ok=True)

    def stage(name, action):
        import logging
        logging.getLogger("shoe_poc").info("M2 %s", name)
        before = time.perf_counter()
        try:
            return action()
        finally:
            timings[name] = time.perf_counter()-before
            write_json(output/"execution_report.json", {"milestone": 2, "run_id": run_id,
                "status": "RUNNING", "last_stage": name, "generated_at": utc_now(),
                "elapsed_seconds": time.perf_counter()-started, "timings_seconds": timings})

    try:
        spec = ShoeSpec.load(spec_path)
        baseline = accepted_baseline(base_directory, spec, acceptance_path)
        from .gyroid import GyroidParameters
        parameters = GyroidParameters(spec.lattice.period_mm, spec.lattice.threshold,
            spec.lattice.resolution_mm, tuple(spec.lattice.phase_origin_mm))
        if not parameters.resolution_diagnostics()["feasible"]:
            raise ValueError("Underresolved gyroid: reduce resolution_mm; nominal transition needs >=2 samples and period >=12 samples")
        if spec_path.resolve() in {(output/name).resolve() for name in GENERATED} and spec_path.resolve() != output/"shoe_spec.json":
            raise ValueError("M2 spec conflicts with a generated output path")
        for name in GENERATED:
            path = output/name
            if path.is_file() and path.resolve() != spec_path.resolve():
                path.unlink()
        prepared = True
        write_json(output/"shoe_spec.json", spec.model_dump(mode="json"))
        from build123d import import_step
        from scipy.interpolate import RegularGridInterpolator
        from .domain import sample_sole_domain, NativeDomainProbe
        from .gyroid import GyroidParameters, sample_gyroid, save_field_npz
        from .meshing import extract_lattice_mesh, export_lattice
        from shoe_poc.validation.lattice import (validate_lattice_mesh, validate_lattice_domain,
            validate_native_line_containment, validate_gyroid_residual)
        from shoe_poc.lattice_preview import render_lattice_preview

        shoe = stage("import_accepted_shoe", lambda: import_step(base_directory/"base_shoe.step"))
        cavity = stage("import_accepted_cavity", lambda: import_step(base_directory/"cavity.step"))
        last = stage("import_accepted_last", lambda: import_step(base_directory/"last_normalized.step"))
        source_base = last.bounding_box().min.Z
        settings = spec.lattice
        from .envelope import build_sole_envelope
        envelope = build_sole_envelope(shoe, last, spec,
            json.loads((base_directory/'geometry_report.json').read_text()),
            source_sha256=sha256(base_directory/'geometry_report.json'))
        z_min, z_max = (source_base-spec.sole.height_mm, source_base+settings.domain.max_z_offset_mm) if envelope is None else envelope.z_bounds
        memory = int(max_field_memory_mb*1024**2)
        if domain_cache is None:
            domain = stage("sample_certified_sole_domain", lambda: sample_sole_domain(shoe, cavity,
                z_min_mm=z_min, z_max_mm=z_max, envelope=envelope,
                spacing_mm=settings.resolution_mm, clearance_mm=settings.domain.clearance_mm,
                bounds_mm=settings.domain.bounds_mm, max_voxels=max_voxels,
                max_memory_mb=max_field_memory_mb))
            domain_source = {"mode": "fresh_CAD_sampling", "run_id": run_id}
        else:
            from .cache import load_certified_domain
            domain = stage("load_bound_certified_domain", lambda: load_certified_domain(domain_cache,
                baseline=baseline, shape=shoe, z_min_mm=z_min,
                z_max_mm=z_max, settings=settings.domain, envelope=envelope,
                spacing_mm=settings.resolution_mm, max_voxels=max_voxels, max_memory_mb=max_field_memory_mb))
            domain_source = {"mode": "validated_cached_CAD_certificates", "directory": str(domain_cache.resolve()),
                "provenance_sha256": sha256(domain_cache/"domain_provenance.json"),
                "provenance": json.loads((domain_cache/"domain_provenance.json").read_text()),
                "note": "Original CAD sampling reused; this run independently rechecks the extracted STL against CAD."}
        write_json(output/"domain_metadata.json", domain.metadata)
        stage("save_domain_field", lambda: np.savez_compressed(output/"domain_field.npz",
            field_mm=domain.field_mm, origin_mm=domain.plan.origin_mm,
            spacing_mm=domain.plan.spacing_mm, metadata_json=np.asarray(json.dumps(domain.metadata))))
        write_json(output/'domain_provenance.json', {'run_id': run_id, 'baseline': baseline,
            'parameters': {'spacing_mm': settings.resolution_mm, 'clearance_mm': settings.domain.clearance_mm,
                'sole_z_bounds_mm': [z_min, z_max], 'bounds_mm': settings.domain.bounds_mm,
                'envelope': None if envelope is None else envelope.metadata},
            'domain_field_sha256': sha256(output/'domain_field.npz'),
            'domain_metadata_sha256': sha256(output/'domain_metadata.json'),
            'domain_source': domain_source})
        field = stage("sample_gyroid", lambda: sample_gyroid(parameters, domain.plan,
            domain_signed_field=domain.field_mm, domain_metadata=domain.metadata,
            max_voxels=max_voxels, max_memory_bytes=memory))
        stage("save_gyroid_field", lambda: save_field_npz(field, output/"gyroid_field.npz"))
        result = stage("extract_closed_lattice", lambda: extract_lattice_mesh(field, max_memory_bytes=memory))
        stage("export_lattice_stl", lambda: export_lattice(result, output/"lattice.stl"))
        mesh_report = stage("reimport_validate_stl", lambda: validate_lattice_mesh(output/"lattice.stl", reference_mesh=result.mesh))
        probe = NativeDomainProbe(shoe, cavity,
            z_min_mm=z_min, z_max_mm=z_max, envelope=envelope)
        containment = stage("independent_cad_containment", lambda: validate_lattice_domain(output/"lattice.stl",
            classify_points=probe.classify_points, max_samples=4096,
            native_line_probe=lambda mesh: validate_native_line_containment(mesh, scanline=probe.scanline, maximum_lines=32)))
        containment["native_probe_diagnostics"] = probe.diagnostics
        interpolator = RegularGridInterpolator(domain.plan.axes, domain.field_mm, bounds_error=False, fill_value=1.)
        residual = stage("independent_implicit_residual", lambda: validate_gyroid_residual(output/"lattice.stl",
            period_mm=settings.period_mm, threshold=settings.threshold,
            phase_origin_mm=settings.phase_origin_mm, domain_field=interpolator,
            cap_tolerance_mm=np.sqrt(3)*settings.resolution_mm, max_samples=2_000_000,
            residual_tolerance=(2*np.pi/settings.period_mm*settings.resolution_mm)**2/4+1e-4))
        residual["cap_classification_basis"] = "One full MC voxel diagonal: mixed domain/gyroid cells may contain Lewiner interior fan vertices; their boundary is not a pure gyroid level set. Analytic residual tolerance is unchanged."
        coverage = None
        if envelope is not None:
            from shoe_poc.validation.lattice_coverage import NativeVerticalSoleSections, validate_lattice_coverage
            sections = NativeVerticalSoleSections(shoe, cavity,
                upper_z=envelope.upper_z, z_min_mm=envelope.z_bounds[0])
            coverage = stage('independent_sole_coverage', lambda: validate_lattice_coverage(
                output/'lattice.stl', native_sections=sections, bounds_mm=sections.bounds_mm,
                period_mm=settings.period_mm, resolution_mm=settings.resolution_mm,
                clearance_mm=settings.domain.clearance_mm, domain_field=interpolator,
                reference_datum_mm=source_base))
        geometry_reports = [mesh_report, containment, residual]+([] if coverage is None else [coverage])
        validation = {"run_id": run_id, "mesh": mesh_report, "native_domain_samples": containment,
                      "gyroid_residual": residual, "geometry_status": "PASS" if all(
                          r["geometry_status"] == "PASS" for r in geometry_reports) else "FAIL",
                      "manufacturing_status": "NOT_VERIFIED", "M3_shell_fusion": "NOT_IMPLEMENTED"}
        if coverage is not None:
            validation['sole_coverage'] = coverage
        write_json(output/"validation_report.json", validation)
        stage("render_actual_stl", lambda: render_lattice_preview(output/"lattice.stl", base_directory/"base_shoe.stl", output/"preview.png"))
        # Recheck inputs after all computation; the accepted base is immutable.
        accepted_baseline(base_directory, spec, acceptance_path)
        metadata = {"milestone": 2, "algorithm_version": "m2-certified-curved-gyroid-v2" if envelope is not None else "m2-certified-gyroid-v1", "run_id": run_id, "baseline": baseline,
                    "field": field.metadata, "meshing": result.metadata, "domain_source": domain_source,
                    "actual_lattice_parameters": settings.model_dump(mode="json"),
                    "validation": validation, "manufacturing_status": "NOT_VERIFIED"}
        write_json(output/"lattice_metadata.json", metadata)
        execution = {"milestone": 2, "run_id": run_id, "status": validation["geometry_status"],
            "milestone_status": "PARTIAL", "user_acceptance": "PENDING", "generated_at": utc_now(),
            "spec_source": str(spec_path.resolve()), "spec_sha256": sha256(spec_path), "baseline": baseline,
            "domain_source": domain_source,
            "elapsed_seconds": time.perf_counter()-started, "timings_seconds": timings,
            "resource_limits": {"max_voxels": max_voxels, "max_field_memory_mb": max_field_memory_mb},
            "environment": environment(), "outputs": artifact_manifest(output)}
        execution["outputs"].pop("execution_report.json", None)
        write_json(output/"execution_report.json", execution)
        write_json(output/"artifact_manifest.json", artifact_manifest(output))
        if validation["geometry_status"] != "PASS":
            raise RuntimeError("M2 geometry checks failed; inspect saved validation and field diagnostics")
        return execution
    except Exception as error:
        write_json(output/"failure_report.json", {"milestone": 2, "run_id": run_id, "status": "FAIL",
            "error_type": type(error).__name__, "error": str(error), "output_prepared": prepared,
            "diagnostics": getattr(error, "metadata", {}), "traceback": traceback.format_exc(),
            "elapsed_seconds": time.perf_counter()-started})
        write_json(output/"execution_report.json", {"milestone": 2, "run_id": run_id, "status": "FAIL",
            "milestone_status": "PARTIAL", "error": str(error), "timings_seconds": timings,
            "elapsed_seconds": time.perf_counter()-started, "outputs": {}, "environment": environment()})
        raise
