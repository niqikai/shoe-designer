from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .audit import audit_asset, infer_last_size
from .assets import DEFAULT_MANIFEST, AssetError, assets_root, load_asset, load_catalog


def _list(catalog, root) -> None:
    for a in catalog.values():
        present = "ok " if root is not None and (root / a.path).is_file() else "-- "
        commercial = {True: "yes", False: "NO", None: "?"}[a.commercial_use]
        print(f"{present}{a.id:28} {a.role:7} commercial={commercial:3} {a.license}")


def _inspect(catalog, asset_id, root) -> None:
    loaded = load_asset(asset_id, catalog, root)
    m = loaded.mesh
    ext = m.extents
    print(f"{asset_id}: {len(m.faces)} faces, watertight={m.is_watertight}")
    print(f"canonical extents (L x W x H mm): {ext[0]:.1f} x {ext[1]:.1f} x {ext[2]:.1f}")
    print(f"canonical bounds min: {m.bounds[0].round(1).tolist()}")
    print(f"source -> canonical transform:\n{loaded.transform.round(4)}")


def _audit(catalog, ids, root, last_id, out) -> None:
    ids = ids or list(catalog)
    unknown = [i for i in ids if i not in catalog]
    if unknown:
        raise AssetError(f"unknown asset(s) {unknown}; known: {sorted(catalog)}")
    cache: dict = {}

    def get(asset_id):  # each mesh is loaded at most once, and only when needed
        if asset_id not in cache:
            cache[asset_id] = load_asset(asset_id, catalog, root)
        return cache[asset_id]

    last_mesh, last_error = None, None
    if any(catalog[i].role == "shoe" for i in ids):
        try:
            last_mesh = get(last_id).mesh
        except AssetError as e:
            last_error = str(e)
    results = []
    for asset_id in ids:
        print(f"auditing {asset_id} ...", file=sys.stderr)
        try:
            if catalog[asset_id].role == "shoe" and last_mesh is None:
                raise AssetError(f"cannot fit against last {last_id!r}: {last_error}")
            results.append(audit_asset(get(asset_id), last_mesh))
        except AssetError as e:  # one bad asset must not lose the others
            results.append({"id": asset_id, "role": catalog[asset_id].role, "error": str(e)})
    sizing = infer_last_size(last_mesh, results) if last_mesh is not None else None
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"assets": results, "size_inference": sizing}, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"{'id':32}{'L x W x H mm':20}{'wt':>3}{'bod':>4}  {'wall med/p1 mm':16}{'thin':>6}  {'overhang open/encl':20}fit vs {last_id}")
    for r in results:
        if "error" in r:
            print(f"{r['id']:32}ERROR: {r['error']}")
            continue
        g, th, ov, fit = r["geometry"], r.get("thickness"), r.get("overhangs"), r.get("fit_to_last")
        dims = " x ".join(f"{x:.0f}" for x in g["extents_mm"])
        has_wall = th and th["median_mm"] is not None
        wall = f"{th['median_mm']:.1f}/{th['p1']:.1f}" if has_wall else "-"
        thin = f"{th['frac_thin']:.1%}" if has_wall else "-"
        over = f"{ov['open_frac']:.1%}/{ov['enclosed_frac']:.1%}" if ov else "-"
        if not fit:
            fits = "-"
        elif fit["seats"] is None:
            fits = f"not fitted: {fit['reason']}"
        elif fit["seats"]:
            fits = f"seats at dz={fit['dz_mm']} mm, signed clearance min {fit['clearance_min_mm']} mm"
        else:
            low = fit["best_low_seat"]
            fits = (f"no seat; length {fit['length_delta_mm']:+.1f} mm, width x{fit['width_ratio']:.2f}, "
                    f"{max(low['last_in_shoe_frac'], low['volume_overlap_frac']):.0%} collides at best low seat")
        print(f"{r['id']:32}{dims:20}{'y' if g['watertight'] else 'N':>3}{g['bodies']:>4}  {wall:16}{thin:>6}  {over:20}{fits}")
    if sizing and sizing["range_eu"]:
        lo, hi = sizing["range_eu"]
        print(f"\nlast length {sizing['last_length_mm']} mm -> about EU {lo}-{hi} by length (unconfirmed)")
        for row in sizing["series"]:
            print(f"  {row['series']}: EU {row['estimate_eu']}{' (extrapolated)' if row['extrapolated'] else ''}")
    print(f"\nwrote {out}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="shoe-assets")
    p.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    p.add_argument("--root", type=Path, help="mesh folder (default: $SHOE_ASSETS_ROOT)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list catalogued assets and whether their files are present")
    ins = sub.add_parser("inspect", help="load one asset into the canonical frame and report")
    ins.add_argument("asset_id")
    aud = sub.add_parser("audit", help="measure every (or the given) asset and write a JSON report")
    aud.add_argument("ids", nargs="*")
    aud.add_argument("--last", default="sneaker_last", help="last the shoes are fitted against")
    aud.add_argument("--out", type=Path, default=Path("reports/audit.json"))
    args = p.parse_args(argv)
    try:
        catalog = load_catalog(args.manifest)
        if args.cmd == "list":
            try:
                root = assets_root(args.root)
            except AssetError:
                root = None
            _list(catalog, root)
        elif args.cmd == "audit":
            _audit(catalog, args.ids, args.root, args.last, args.out)
        else:
            _inspect(catalog, args.asset_id, args.root)
    except AssetError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
