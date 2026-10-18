import json

import trimesh

from shoe_designer import cli
from helpers import hollow


def manifest(tmp_path, rows):
    out = []
    for r in rows:
        out.append("[[asset]]\n" + "\n".join(
            f'{k} = {v!r}'.replace("'", '"') if isinstance(v, str) else f"{k} = {v}" for k, v in r.items()
        ))
    p = tmp_path / "m.toml"
    p.write_text("\n".join(out), encoding="utf-8")
    return p


def base(id, role, path, **kw):
    return {"id": id, "role": role, "path": path, "source": "t", "license": "t", **kw}


def run(tmp_path, rows, *args):
    m = manifest(tmp_path, rows)
    out = tmp_path / "r" / "audit.json"
    code = cli.main(["--manifest", str(m), "--root", str(tmp_path), "audit", *args, "--out", str(out)])
    return code, (json.loads(out.read_text()) if out.exists() else None)


def write_last(tmp_path):
    last = trimesh.creation.box(extents=(200, 80, 100))
    last.export(tmp_path / "last.stl")


def test_one_missing_file_does_not_lose_other_results(tmp_path, capsys):
    write_last(tmp_path)
    code, rep = run(tmp_path, [base("last", "last", "last.stl"), base("gone", "last", "missing.stl")])
    assert code == 0
    by_id = {r["id"]: r for r in rep["assets"]}
    assert "geometry" in by_id["last"] and "not found" in by_id["gone"]["error"]
    assert "ERROR" in capsys.readouterr().out


def test_geometry_only_audit_does_not_need_the_last(tmp_path):
    write_last(tmp_path)
    rows = [base("a", "region", "last.stl"), base("sneaker_last", "last", "nope.stl")]
    code, rep = run(tmp_path, rows, "a", "--last", "typo")
    assert code == 0 and "geometry" in rep["assets"][0]


def test_shoe_rows_report_the_last_error_but_audit_continues(tmp_path):
    hollow((290, 110, 80), 2.6).export(tmp_path / "s.stl")
    rows = [base("s", "shoe", "s.stl", toe="+X"), base("last", "last", "nope.stl")]
    code, rep = run(tmp_path, rows, "--last", "last")
    assert code == 0 and "cannot fit" in rep["assets"][0]["error"]
    assert rep["size_inference"] is None
