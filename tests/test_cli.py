"""M0.10: ``shoegen build`` end to end (synthetic last, in-process ``main``).

The synthetic STEP is exported once per module: a STEP export carries a timestamp, so a
second export would have another sha256 and miss the field cache.
"""

from __future__ import annotations

import hashlib
import json
import os
import resource
import sys
import time
from pathlib import Path

import numpy as np
import pytest
import trimesh
from build123d import export_step
from fixtures.synthetic_lasts import rounded_last

from shoegen.cli import main

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "m0.json"
PROFILE = "pbf-tpu-generic@0"
ROOT = Path(__file__).resolve().parents[1]
# ``SHOEGEN_REAL_LAST`` points at a last read in place (it is never copied);
# ``SHOEGEN_REAL_LAST_NORMALIZATION`` at its --normalization JSON (plan H1).
REAL_LASTS = (
    [Path(os.environ["SHOEGEN_REAL_LAST"])]
    if os.environ.get("SHOEGEN_REAL_LAST")
    else sorted(p for pat in ("*.stp", "*.step") for p in (ROOT / "input" / "lasts").glob(pat))
)
REAL_LAST_NORMALIZATION = os.environ.get("SHOEGEN_REAL_LAST_NORMALIZATION")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_params(path: Path, *, radius=2.0, cell=12.0, collar=30.0) -> Path:
    obj = json.loads(EXAMPLE.read_text())
    obj["collar_height_mm"] = collar
    obj["lattice"].update({"radius_mm": radius, "cell_mm": cell})
    path.write_text(json.dumps(obj))
    return path


@pytest.fixture(scope="module")
def work(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("cli")


@pytest.fixture(scope="module")
def last_step(work) -> Path:
    path = work / "rounded.step"
    export_step(rounded_last().shape, path)
    return path


@pytest.fixture(scope="module")
def params_file(work) -> Path:
    return write_params(work / "params.json")


def run(last, params, out, work, *extra) -> int:
    return main(
        [
            "build",
            "--graph", str(params),
            "--last", str(last),
            "--profile", PROFILE,
            "-o", str(out),
            "--voxel-mm", "1.0",
            "--workers", "2",
            "--cache-dir", str(work / "cache"),
            *extra,
        ]
    )  # fmt: skip


@pytest.fixture(scope="module")
def smoke(work, last_step, params_file):
    out = work / "smoke"
    t0 = time.perf_counter()
    code = run(last_step, params_file, out, work)
    return code, out, time.perf_counter() - t0


def test_smoke_outputs_and_mesh(smoke, eval_report):
    code, out, seconds = smoke
    assert code == 0
    for name in ("shoe.stl", "graph.json", "report.json", "manifest.json"):
        assert (out / name).is_file(), name
    mesh = trimesh.load(out / "shoe.stl")
    assert mesh.is_watertight
    parts = mesh.split(only_watertight=False)
    bodies = [p for p in parts if p.volume > 0]
    assert len(bodies) == 1  # the other shells are sealed lattice pores (negative volume)
    report = json.loads((out / "report.json").read_text())
    assert report["manufacturing_verdict"] == "NOT_VERIFIED"
    assert report["exit_code"] == 0
    assert report["summary"]["prebuild"] == "pass" and report["summary"]["hard"] == "pass"
    assert report["run"]["normalization"]["orientation_status"] == (
        "PROVISIONAL_REQUIRES_USER_CONFIRMATION"
    )
    assert report["run"]["last"]["usage_policy"] == "DEVELOPMENT_ONLY_LOCAL_ONLY"
    # The skin and footbed seal the lattice pores (ADR 0002 open issue, M0 review): the
    # enclosed-cavity soft check must report it as a finding, the hard checks still pass.
    soft = {r["id"]: r for r in report["checks"]["soft"]}
    cav = soft["enclosed_cavities"]
    assert cav["result"] == "report_only" and cav["finding"] is True
    assert cav["measured"]["cavity_count"] >= 1
    assert cav["measured"]["largest_cavity_volumes_mm3"][0] > 1000.0
    assert "enclosed_cavities" in report["summary"]["soft_findings"]
    assert report["rusage_peaks"]["parent_max_rss_bytes"] > 0
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["files"]["shoe.stl"]["sha256"] == sha(out / "shoe.stl")
    assert manifest["files"]["graph.json"]["sha256"] == sha(out / "graph.json")
    assert manifest["run_id"] and manifest["peak_rss_bytes"] > 0
    assert manifest["environment"]["packages"]["numpy"]
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak_gb = (peak if sys.platform == "darwin" else peak * 1024) / 1024**3
    eval_report(
        f"smoke {seconds:.1f}s, {len(mesh.faces)} triangles, watchdog peak RSS "
        f"{manifest['peak_rss_bytes'] / 1024**3:.2f} GB (process high-water {peak_gb:.2f} GB), "
        f"timings { ({k: round(v, 1) for k, v in report['timings_s'].items()}) }"
    )
    assert seconds < 90


def test_deterministic(smoke, work, last_step, params_file, eval_report):
    _, out1, _ = smoke
    out2 = work / "smoke2"
    assert run(last_step, params_file, out2, work) == 0
    for name in ("shoe.stl", "graph.json"):
        assert sha(out1 / name) == sha(out2 / name), name
    eval_report(f"sha256 shoe.stl {sha(out1 / 'shoe.stl')} graph.json {sha(out1 / 'graph.json')}")


def test_strut_below_profile_minimum_exit_3(work, last_step):
    params = write_params(work / "thin.json", radius=0.2)
    out = work / "thin"
    out.mkdir()
    (out / "shoe.stl").write_bytes(b"stale")  # an earlier run's file must not survive
    assert run(last_step, params, out, work) == 3
    assert not (out / "shoe.stl").exists()
    report = json.loads((out / "report.json").read_text())
    assert report["exit_code"] == 3 and report["summary"]["prebuild"] == "fail"
    assert report["summary"]["hard"] == "not_run"
    assert json.loads((out / "manifest.json").read_text())["shoe_stl"] is None


def test_bad_params_exit_3(work, last_step):
    bad = work / "bad.json"
    bad.write_text('{"nope": 1}')
    assert run(last_step, bad, work / "bad", work) == 3
    assert not (work / "bad" / "shoe.stl").exists()


def test_broken_last_exit_4(work, params_file):
    broken = work / "broken.step"
    broken.write_text("this is not a STEP file")
    out = work / "broken"
    assert run(broken, params_file, out, work) == 4
    assert not (out / "shoe.stl").exists()
    assert json.loads((out / "report.json").read_text())["failed_stage"] == "last_import"
    assert run(work / "missing.step", params_file, work / "missing", work) == 4


def test_usage_errors_exit_2(work, last_step, params_file):
    with pytest.raises(SystemExit) as e:
        main([])
    assert e.value.code == 2
    with pytest.raises(SystemExit) as e:
        main(["build", "--graph", str(params_file)])
    assert e.value.code == 2
    with pytest.raises(SystemExit) as e:
        run(last_step, params_file, work / "x", work, "--voxel-mm", "-1")
    assert e.value.code == 2


def test_hard_failure_exit_5_writes_no_stl(work, last_step, params_file, monkeypatch):
    """Force a hard failure (build volume far too small) via a patched profile."""
    from shoegen import profile

    real = profile.load_profile

    def small(pid):
        p = real(pid)
        return p.model_copy(update={"build_volume_mm": (10.0, 10.0, 10.0)})

    monkeypatch.setattr(profile, "load_profile", small)
    out = work / "hard"
    assert run(last_step, params_file, out, work) == 5
    assert not (out / "shoe.stl").exists()
    report = json.loads((out / "report.json").read_text())
    assert report["summary"]["hard"] == "fail" and report["exit_code"] == 5
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["status"] == "hard_check_failed" and manifest["shoe_stl"] is None
    assert (out / "graph.json").is_file()


def test_typed_errors_keep_their_exit_code():
    """Errors raised after the graph stage carry their own exit code (M0 review): the
    far-field check of the block planner -> 3, a last mesh error -> 4, the assembly
    memory estimate -> 6 (same as the watchdog); anything else -> 1."""
    from shoegen.cli import _code_of
    from shoegen.graph import GraphValidationError
    from shoegen.last import LastMeshError
    from shoegen.mesh import MeshMemoryError

    assert _code_of(GraphValidationError("x")) == (3, "input_failed")
    assert _code_of(LastMeshError("x")) == (4, "last_import_failed")
    assert _code_of(MeshMemoryError("x")) == (6, "resource_limit")
    assert _code_of(RuntimeError("x")) == (1, "internal_error")


@pytest.mark.real_last
@pytest.mark.skipif(not REAL_LASTS, reason="no input/lasts/*.stp|*.step")
def test_real_last(eval_report):
    # Derived geometry of a licensed last stays under output/ (gitignored; plan 1.4),
    # not in the system temp directory.
    out = ROOT / "output" / "pytest_real_last"
    out.mkdir(parents=True, exist_ok=True)
    params = write_params(out / "params.json", radius=1.0, cell=10.0, collar=75.0)
    norm = ["--normalization", REAL_LAST_NORMALIZATION] if REAL_LAST_NORMALIZATION else []
    t0 = time.perf_counter()
    code = main(
        [
            "build",
            "--graph", str(params),
            "--last", str(REAL_LASTS[0]),
            "--profile", PROFILE,
            "-o", str(out / "build"),
            "--voxel-mm", "0.5",
            "--max-rss-gb", "6",
            "--cache-dir", str(ROOT / "output" / "cache" / "fields"),
            *norm,
        ]
    )  # fmt: skip
    seconds = time.perf_counter() - t0
    manifest = json.loads((out / "build" / "manifest.json").read_text())
    eval_report(
        f"real last {REAL_LASTS[0].name}: exit {code}, {seconds:.0f}s, watchdog peak RSS "
        f"{manifest['peak_rss_bytes'] / 1024**3:.2f} GB, triangles {manifest.get('triangles')}"
    )
    assert code == 0
    assert np.isfinite(seconds)
