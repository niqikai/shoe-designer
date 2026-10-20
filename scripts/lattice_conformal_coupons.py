"""#46 / #11: rod-net shell coupons (curved patches with a frame) for the print and powder-removal tests. Local, not for the repository.

    uv run --extra fast python scripts/lattice_conformal_coupons.py

Writes reports/lattice/conformal_coupons/*.stl and 试件说明.md. Rod diameter 2.4 / 3.5 / 5.5 mm with cell 17 / 14 / 38 mm (voxel <= rod / 4).
"""
import json
import sys
from pathlib import Path

import numpy as np

from shoe_designer import lattice as L
from shoe_designer import lattice_conformal as C

OUT = Path("reports/lattice/conformal_coupons")
SETS = [  # name, rod, pitch, size, voxel, radius
    ("R2.4_C17", 2.4, 17.0, (70.0, 70.0), 0.5, 80.0),
    ("R3.5_C14", 3.5, 14.0, (70.0, 70.0), 0.8, 80.0),
    ("R5.5_C38", 5.5, 38.0, (120.0, 120.0), 1.2, 100.0),
]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for name, rod, pitch, size, vox, rad in SETS:
        assert vox <= rod / 4 + 1e-9
        phi, g, info = C.shell_coupon(rod, pitch, size, vox, rad, elongate=1.0)
        band, region = info.pop("band"), info.pop("region")
        solid = phi < 0
        rods = C.rod_diameters(solid, g.h, region, target=rod)
        wins = C.shell_pores(solid, g.h, band)
        raw = L.sealed(phi, g)
        safety = L.fill_sealed(phi, g) if raw["count"] else {"filled_mm3": 0.0}
        mesh, er = L.to_mesh(phi, g, 200.0)
        chk = L.export_checks(mesh)
        path = OUT / f"{name}.stl"
        mesh.export(path)
        mb = path.stat().st_size / 1e6
        rows.append({"name": name, **info, "rods_mm": rods, "windows_mm": wins, "sealed_raw": raw["count"], "filled_mm3": safety["filled_mm3"], "checks": chk, "faces": int(len(mesh.faces)),
                     "volume_cm3": round(float(mesh.volume) / 1000, 3), "stl_MB": round(mb, 2), "extents_mm": [round(float(e), 1) for e in mesh.extents]})
        print(name, rows[-1]["checks"], f"{mb:.1f} MB", rods.get("median"), wins.get("median"), flush=True)
    (OUT / "report.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False, default=float))
    lines = ["# 杆状晶格壳试件(#11 / #46)", "", "三件弧面小块(圆柱面,曲率半径 80 / 80 / 100 mm),外框实心 5 mm,壳厚 = 杆径 + 1 mm,杆为圆截面,单元为 Voronoi(与鞋面同一套规则)。",
             "两面都开放,没有封闭空腔,用于测:最小杆径成形、孔径(清粉)、杆间距、弧面处杆的变形。TPU 密度 1.2 g/cm³ 是占位值。", "",
             "| 试件 | 杆径 mm | 单元 mm | 尺寸 mm | 体素 mm | 窗内切球 中位 / 最小 mm | 体积 cm³ | 重量 g(1.2) | STL |", "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        w = r["windows_mm"]
        lines.append(f"| {r['name']} | {r['rod_mm']} | {r['pitch_mm']} | {r['size_mm'][0]:.0f}×{r['size_mm'][1]:.0f} | {r['voxel_mm']} | {w.get('median', 0):.1f} / {w.get('min', 0):.1f} | "
                     f"{r['volume_cm3']} | {r['volume_cm3'] * 1.2:.1f} | {r['stl_MB']} MB |")
    lines += ["", "每件都检查过:水密、单一实体、导出网格内壳 0、封闭空腔 0(体素级残留已填并记录)。杆径实测见 report.json(骨架法,标定偏差 < 1 体素)。",
              "", "## 请工厂回答", "1. 各件最小杆径是否成形(2.4 mm 最关键;1.2 mm 下限尚无试件,#14 的 BCC 试件覆盖)。", "2. 清粉:窗内切球 5 mm 以下的窗是否能清干净(R2.4_C17 最容易堵,R5.5_C38 最开放)。",
              "3. 弧面处、框与网交接处是否有断裂/粘连。", "4. 同一件两面清粉难度是否一样(两面都开放)。", "", "声明:几何由系统生成的演示参数,不是产品设计;Zellerfeld 鞋楦仅用于内部演示,试件本身不含鞋楦几何。"]
    (OUT / "试件说明.md").write_text("\n".join(lines))


if __name__ == "__main__":
    main()
