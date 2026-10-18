from __future__ import annotations

import argparse
import sys
from pathlib import Path

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


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="shoe-assets")
    p.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    p.add_argument("--root", type=Path, help="mesh folder (default: $SHOE_ASSETS_ROOT)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list catalogued assets and whether their files are present")
    ins = sub.add_parser("inspect", help="load one asset into the canonical frame and report")
    ins.add_argument("asset_id")
    args = p.parse_args(argv)
    try:
        catalog = load_catalog(args.manifest)
        if args.cmd == "list":
            try:
                root = assets_root(args.root)
            except AssetError:
                root = None
            _list(catalog, root)
        else:
            _inspect(catalog, args.asset_id, args.root)
    except AssetError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
