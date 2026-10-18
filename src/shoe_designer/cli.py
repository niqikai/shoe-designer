from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .audit import audit_asset
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
    last = load_asset(last_id, catalog, root).mesh
    results = []
    for asset_id in ids or list(catalog):
        print(f"auditing {asset_id} ...", file=sys.stderr)
        results.append(audit_asset(load_asset(asset_id, catalog, root), last))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"{'id':32}{'L x W x H mm':20}{'wt':>3}{'bod':>4}  {'wall med/p1 mm':16}{'<1.2':>6}  {'overhang open/encl':20}fit vs {last_id}")
    for r in results:
        g, th, ov, fit = r["geometry"], r.get("thickness"), r.get("overhangs"), r.get("fit_to_last")
        dims = " x ".join(f"{x:.0f}" for x in g["extents_mm"])
        wall = f"{th['median_mm']:.1f}/{th['p1']:.1f}" if th else "-"
        thin = f"{th['frac_below_1.2mm']:.1%}" if th else "-"
        over = f"{ov['open_frac']:.1%}/{ov['enclosed_frac']:.1%}" if ov else "-"
        if not fit:
            fits = "-"
        elif fit["seats"]:
            fits = f"seats at dz={fit['seat_dz_mm']} mm, clearance min {fit['clearance_min_mm']} mm"
        else:
            low = next(v for k, v in fit.items() if k.startswith("best_seat"))
            fits = f"no seat; length {fit['length_delta_mm']:+.1f} mm, {low['collision_frac']:.0%} of last inside shoe at best low seat"
        print(f"{r['id']:32}{dims:20}{'y' if g['watertight'] else 'N':>3}{g['bodies']:>4}  {wall:16}{thin:>6}  {over:20}{fits}")
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
