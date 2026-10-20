"""#14 -> #11: lattice coupons for the factory (cells 4 / 6 / 8 mm x strut diameters 1.2 / 1.6 / 2.0 mm = 9 blocks).

    uv run python scripts/lattice_coupons.py [--out-dir reports/lattice/coupons] [--kind bcc] [--size 48 48 20] \\
        [--process config/process_sls_tpu.toml] [--lattice config/lattice.toml]

Each block: lattice core between a bottom and a top skin plate (2 mm), the four sides open lattice, round powder holes through
both plates. Writes one STL per coupon, coupon_report.json (watertight / bodies / inner shells / sealed cavities / pore sizes /
volume / mass at the placeholder density) and 试件说明.md (one Chinese page for the factory). Nothing here depends on the shoe assets.
"""
import argparse
import json
from pathlib import Path

from shoe_designer import lattice as L
from shoe_designer.process import ProcessConfig


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="reports/lattice/coupons")
    ap.add_argument("--process", default="config/process_sls_tpu.toml")
    ap.add_argument("--lattice", default=None)
    ap.add_argument("--kind", default=None, help="bcc or cubic (default: config sole.kind)")
    ap.add_argument("--size", type=float, nargs=3, default=(48.0, 48.0, 20.0), help="x y z in mm (48 divides 4, 6 and 8)")
    a = ap.parse_args()
    proc = ProcessConfig.from_toml(a.process)
    cfg = L.LatticeConfig.from_toml(a.lattice)
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for cell in L.COUPON_CELLS:
        for d in L.COUPON_DIAMETERS:
            phi, grid, rep = L.coupon(cell, d, cfg, proc, a.kind, tuple(a.size))
            raw = L.sealed(phi, grid)
            mesh, er = L.to_mesh(phi, grid, 5.0)
            name = f"coupon_c{cell:g}_d{d:g}.stl"
            mesh.export(out / name)
            thin = L.thin_material(phi, grid, L.strut_min_mm(cfg, proc))
            rep.update({"file": name, "faces": int(len(mesh.faces)), "mesh_volume_cm3": round(float(mesh.volume) / 1000, 3),
                        "checks": L.export_checks(mesh), "sealed_cavities": raw, "dropped_mm3": round(er.dropped_volume_mm3, 2),
                        "thin_fraction": thin["thin_fraction"]})
            ok = rep["checks"]["watertight"] and rep["checks"]["bodies"] == 1 and raw["count"] == 0 and rep["checks"]["inner_shells"]["count"] == 0
            rep["ok"] = bool(ok)
            rows.append(rep)
            print(f"{name}: watertight={rep['checks']['watertight']} bodies={rep['checks']['bodies']} sealed={raw['count']} "
                  f"fill={rep['fill_vs_solid']} through={rep['pores']['through_mm']} mm  {rep['mesh_volume_cm3']} cm3  ok={ok}", flush=True)
    (out / "coupon_report.json").write_text(json.dumps({"process": proc.name, "coupons": rows}, indent=1, ensure_ascii=False, default=float))
    (out / "试件说明.md").write_text(note(rows, cfg, proc, a.size), encoding="utf-8")
    print(f"wrote {len(rows)} coupons to {out}")


KIND_ZH = {"bcc": "体心立方(BCC)", "cubic": "简单立方(杆沿胞元棱)"}


def note(rows, cfg, proc, size) -> str:
    t = ["# 晶格试件说明(#11 / #14)", "",
         f"工艺:{proc.name}(粉末床 TPU)。共 9 件:胞元 4 / 6 / 8 mm × 杆径 1.2 / 1.6 / 2.0 mm,尺寸 {size[0]:g}×{size[1]:g}×{size[2]:g} mm。",
         f"结构:{KIND_ZH[rows[0]['kind']]}杆状晶格,上下各 {rows[0]['skin_mm']:g} mm 实体蒙皮板,四个侧面是开放的晶格(不封口);"
         f"底板 4 个、顶板 1 个直径 {rows[0]['hole_mm']:g} mm 的圆孔作出粉孔。STL 单位毫米,每件水密、单体、无封闭空腔。", "",
         "## 请工厂帮忙看三件事", "",
         "1. **出粉**:打印后用你们常规的清粉流程(气枪/吹砂/超声),记录每件能否清干净、各用了什么方式和时间。"
         "通用资料说开孔 ≥ 6–8 mm 可自动清粉、4–6 mm 需要高压气加超声、< 4 mm 易积粉;下表的「贯通孔径」是晶格内部能穿过的最大球直径,请对照实际清粉情况告诉我们哪个尺寸可行。",
         "2. **强度与回弹**:压缩(或手压)看回弹和永久变形,拉扯杆看撕裂,杆径 1.2 mm 的是否稳定成形(有无断杆、粘连)。",
         "3. **实际杆径与重量**:称重(用于校准密度),量杆径(与设计值的偏差,决定是否要加补偿)。", "",
         "## 清单", "",
         "| 文件 | 胞元 mm | 杆径 mm | 体积占比(对实心块) | 单件体积 cm³ | 按 1.2 g/cm³ 估重 g | 贯通孔径 mm | 最大空洞直径 mm |",
         "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        t.append(f"| {r['file']} | {r['cell_mm']:g} | {r['strut_diameter_mm']:g} | {r['fill_vs_solid']:.2f} | {r['mesh_volume_cm3']:.2f} | "
                 f"{r['mesh_volume_cm3'] * cfg.tpu_density_g_cm3:.1f} | {r['pores']['through_mm']:.1f} | {r['pores']['inscribed_mm']:.1f} |")
    t += ["", "密度 1.2 g/cm³ 是占位值,没有核实,请以工厂材料数据表为准;重量只用来对照实测。",
          "孔径是由几何算出的理想值(未计打印收缩和粉末粘连),实际可用孔径通常更小。", ""]
    return "\n".join(t)


if __name__ == "__main__":
    main()
