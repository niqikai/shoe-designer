"""S7: one command from user input to a printable STL and an audit report (docs/design/end-to-end.md).

    make-shoe "波浪纹的镂空运动鞋" --form A --size 44 --out-dir reports/make_shoe/demo
    make-shoe --form B --style k1_honeycomb
    make-shoe --form B-knit --variant v7_hex

Steps: intent (text / images -> structured intent, clamp report) -> build (A: template + cavity rebuild S2-S6, pattern layer #13,
optional sole lattice #14; B: flow-aligned lattice shoe #46) -> process-profile checks (config/process_sls_tpu.toml) -> audit
(Gate / D8 table, watertight, one solid, no closed cavity, weight at the placeholder density) -> ``shoe.stl``, ``report.md``,
``params.json`` and ``audit.json`` in the output directory. The stages are the existing modules; this file only wires them.

Output derives from CC BY-NC-ND / unknown-source assets: keep it out of git (reports/ is ignored) and do not distribute it.
Keys for the intent backends are read from the environment (ANTHROPIC_API_KEY / DEEPSEEK_API_KEY) and never printed.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import resource
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import audit, ease
from . import intent as it
from . import lattice as L
from . import outline as O
from . import pose as pz
from .assets import AssetError, load_asset, load_catalog
from .compose import compose, gate_verdict
from .envelope import envelope
from .lattice_conformal import build_whole, load_specs
from .lattice_variants import build_variant, load_variants, prepare
from .pattern import PatternConfig, PatternMasks, PatternSpec, apply_pattern
from .process import ProcessConfig, build_volume, channel_width, mesh_inner_shells
from .sdf import Grid
from .support_closure import closure

SIZES = (43, 44, 45)  # the DLP template family
TEMPLATES = ("solid", "hollow")
STYLE_FILES = ("lattice_conformal_styles.toml", "lattice_conformal.toml")
DEFAULT_STYLE = "k1_honeycomb"
DEFAULT_VARIANT = "v1_base"
DEFAULT_PROCESS = "config/process_sls_tpu.toml"
HEADROOM_MM = 45.0
CLOSING_MM = 12.0
BAND_MM = 14.0
SEALED_FAIL_MM3 = 1000.0  # sealed air above this is a design error even when the safety fill removes it
THIN_FRAC_WARN = 0.02  # provisional: share of surface samples / material thinner than the process minimum


class MakeShoeError(Exception):
    """User-facing failure (bad input, missing asset or key, refused build)."""


def rss_mb() -> float:
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r / (1024 * 1024 if sys.platform == "darwin" else 1024)


@dataclass
class Check:
    name: str
    status: str  # pass | warn | fail | na
    value: str
    detail: str = ""

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class Result:
    mesh: object
    checks: list[Check]
    audit: dict
    build: dict
    seconds: dict = field(default_factory=dict)

    @property
    def failed(self) -> bool:
        return any(c.status == "fail" for c in self.checks)


# --------------------------------------------------------------------------------------------------- 1. intent
def make_backend(name: str, model: str | None = None):
    if name == "rules":
        return it.RuleBackend()
    key, cls = {"claude": ("ANTHROPIC_API_KEY", it.ClaudeBackend), "deepseek": ("DEEPSEEK_API_KEY", it.DeepSeekBackend)}[name]
    if not os.environ.get(key):
        raise MakeShoeError(f"--backend {name} needs {key} in the environment (or use --backend rules, offline)")
    return cls(model=model)


def resolve_intent(text: str, images, backend) -> it.IntentResult:
    if not text.strip() and not images:  # nothing to parse: the defaults of the design space
        return it.IntentResult(True, intent={"template": None, "pattern": None, "style_tags": [], "instep_ease_mm": None, "notes": "",
                                             "unsupported_requests": []})
    res = it.parse_intent(text, backend, images=images)
    if not res.ok:
        raise MakeShoeError("intent parsing failed: " + "; ".join(res.errors))
    return res


def intent_pattern(intent: dict, override: str | None, pattern_image: str | None) -> PatternSpec:
    """The PatternSpec of the request. ``intent['pattern']`` wins; style tags fill in a preset when the intent has none."""
    raw = intent.get("pattern")
    if raw is None and intent.get("style_tags"):
        raw = dict(it.load_config()["style"][intent["style_tags"][0]])
    raw = {k: v for k, v in (raw or {"kind": "none"}).items() if v is not None}
    if override:
        raw = {**raw, "kind": override}
    if pattern_image:
        raw.update(kind="image", image=pattern_image)
    return PatternSpec.from_dict(raw)


def clamp_lines(res: it.IntentResult) -> list[str]:
    out = [f"{c.field}: {c.original!r} -> {c.new!r} ({c.reason})" for c in res.changes]
    out += [f"warning: {w}" for w in res.warnings]
    out += [f"unsupported: {u}" for u in res.unsupported_requests]
    return out


# --------------------------------------------------------------------------------------------------- 2. build
@dataclass
class Placed:
    """Template pipeline up to the cavity: shared by form A and form B."""

    proc: ProcessConfig
    ease_cfg: object
    env: object
    grid: Grid
    cav: object
    phi_e: np.ndarray
    pose: dict
    floor_ease: float


def place_cavity(shoe_id: str, proc: ProcessConfig, h: float = 1.0, instep_mm: float | None = None) -> Placed:
    """Envelope of the template -> pose of the last -> cavity (last + ease) on one grid (the common part of scripts/build_real.py)."""
    cat = load_catalog()
    last = load_asset("sneaker_last", cat).mesh
    shoe = load_asset(shoe_id, cat).mesh
    b = shoe.bounds
    step = max(h, Grid.dz_for(h, proc.alpha_b_deg))
    grid0 = Grid.from_bounds(b[0], b[1] + [0, 0, HEADROOM_MM], h, proc.alpha_b_deg, margin=3 * step)
    env = envelope(shoe, h=h, grid=grid0, closing_radius=CLOSING_MM, band=BAND_MM)
    cfg = dataclasses.replace(ease.EaseConfig.from_toml(None), fdm_shrink=proc.shrink_mm)
    if instep_mm is not None:
        cfg = dataclasses.replace(cfg, zones=tuple(dataclasses.replace(z, value=instep_mm) if z.name == "instep" else z for z in cfg.zones))
    field_ = ease.EaseField.for_last(last, cfg)
    floor_ease = float(field_(np.array([[last.bounds.mean(axis=0)[0], 0.0, last.bounds[0, 2]]]), np.array([[0.0, 0.0, -1.0]]))[0])
    res = pz.fit_pose(env.phi, env.grid, env.mask_below(), last, ease=field_, wall=proc.wall, s_min=proc.sole_min + floor_ease)
    lb = last.bounds
    corners = np.array([[x, y, z] for x in lb[:, 0] for y in lb[:, 1] for z in lb[:, 2]])
    pc = corners @ res.transform[:3, :3].T + res.transform[:3, 3]
    zt_max = float(env.collar.rim[:, 2].max())
    grid = ease.extend_grid(env.grid, pc.min(axis=0) - 5, np.maximum(pc.max(axis=0), [0, 0, zt_max + HEADROOM_MM]) + 5)
    cav = ease.build_cavity(last, res.transform, env.collar, grid, cfg)
    off = np.rint((np.asarray(env.grid.origin) - np.asarray(grid.origin)) / np.asarray(grid.spacing)).astype(int)
    phi_e = np.full(grid.shape, BAND_MM, np.float32)
    phi_e[tuple(slice(o, o + n) for o, n in zip(off, env.grid.shape))] = env.phi
    return Placed(proc, cfg, env, grid, cav, phi_e, res.to_dict()["pose"], floor_ease)


def build_form_a(pl: Placed, spec: PatternSpec, lattice_sole: bool, lattice_cfg: L.LatticeConfig, log=print):
    """Compose phi_shoe (S6), apply the pattern (#13), optionally the sole lattice (#14). Returns (mesh, build report)."""
    proc, grid, cav = pl.proc, pl.grid, pl.cav
    t0 = time.perf_counter()
    cl = closure(cav.phi, grid, t=proc.wall, phi_e=pl.phi_e, region=pl.env.collar.mask_below(grid)) if proc.closure else None
    comp = compose(pl.phi_e, cav.phi, cl.phi_a if cl else None, cl.phi_c_star if cl else None, grid, pl.env.collar, wall=proc.wall,
                   t_fill=proc.t_fill, sole_min=proc.sole_min, band_mm=BAND_MM, bed_cut=proc.bed_cut)
    phi_cav = cl.phi_c_star if cl else cav.phi
    log(f"[compose] {time.perf_counter() - t0:.0f} s")
    rep = {"form": "A", "compose": comp.report, "gate_metrics": comp.gate, "pose": pl.pose, "cavity": pl.cav.report,
           "closure_on": proc.closure, "bed_cut": proc.bed_cut}
    phi, g = comp.phi, grid
    rep["_comp"] = comp  # for the audit; stripped before it is written
    rep["_core"] = ((phi_cav < 0) & comp.below, grid)
    if spec.kind != "none":
        t0 = time.perf_counter()
        masks = PatternMasks.from_composed(comp, phi_cav, pl.env.collar, proc)
        phi, prep_rep = apply_pattern(phi, g, spec, masks, proc, PatternConfig.from_toml())
        rep["pattern"] = prep_rep
        log(f"[pattern {spec.kind}] {time.perf_counter() - t0:.0f} s")
    if lattice_sole:
        t0 = time.perf_counter()
        phi, g, rep["sole_lattice"] = L.sole_lattice(phi, phi_cav, g, lattice_cfg, proc)
        log(f"[sole lattice] {time.perf_counter() - t0:.0f} s")
    rep["_field"] = (phi, g)
    return rep


def load_style(name: str, outline_json: str | None):
    specs = {s.name: s for f in STYLE_FILES for s in load_specs(f)}
    if name not in specs:
        raise MakeShoeError(f"unknown style {name!r}; available: {', '.join(sorted(specs))}")
    v = specs[name]
    clamps: list[str] = []
    if outline_json:
        raw = json.loads(Path(outline_json).read_text() if Path(outline_json).exists() else outline_json)
        try:
            shapes, rep = O.clamp(raw)
        except O.OutlineError as e:
            raise MakeShoeError(f"outline: {e}") from None
        clamps = [f"{r['field']}: {r['requested']} -> {r['used']} (range {r['range']})" for r in rep]
        v = dataclasses.replace(v, outline=[dict(s) for s in shapes])
    return v, clamps


def coarse_prep(pl: Placed):
    """The in-process equivalent of scripts/lattice_variants.py --prep: unopened phi_C and the collar height on the template grid."""
    from .ease import _column_xy

    c0 = (pl.cav.phi_last - pl.cav.ease_grid).astype(np.float32)  # phi_C before the collar is opened
    zt = pl.env.collar.z_t(_column_xy(pl.grid)).reshape(pl.grid.shape[:2])
    return c0, prepare(c0, zt, pl.grid)


def build_form_b(pl: Placed, v, lattice_cfg: L.LatticeConfig, log=print):
    """Flow-aligned lattice shoe (#46) on the cavity of the placed last."""
    t0 = time.perf_counter()
    c0, prep = coarse_prep(pl)
    fine, fg, rep, _aux = build_whole(prep, v, lattice_cfg, pl.proc)
    log(f"[lattice shoe {v.name}] {time.perf_counter() - t0:.0f} s, route {rep['route']}")
    return {"form": "B", "style": v.name, "lattice": rep, "printable": rep["printable"], "pose": pl.pose, "_field": (fine, fg),
            "_core": (c0 < 0, pl.grid)}


def variant_names() -> list[str]:
    return [v.name for v in load_variants()]


def load_variant(name: str, image: str | None):
    """A B-knit variant (#47) by name. ``image`` replaces the built-in test picture of an image-driven variant (v6_image)."""
    specs = {v.name: v for v in load_variants()}
    if name not in specs:
        raise MakeShoeError(f"unknown variant {name!r}; available: {', '.join(specs)}")
    v, note = specs[name], None
    if image:
        if v.image:
            v, note = dataclasses.replace(v, image=str(image)), f"variant {name}: image {image} drives the yarn diameter (instead of the built-in test picture)"
        else:
            note = f"image {image} is not used: variant {name} is not image-driven (v6_image is)"
    return v, note


def build_form_knit(pl: Placed, v, lattice_cfg: L.LatticeConfig, log=print):
    """B-knit variant (#47: lattice_variants.build_variant) on the cavity of the placed last."""
    t0 = time.perf_counter()
    c0, prep = coarse_prep(pl)
    fine, fg, rep, _aux = build_variant(prep, v, lattice_cfg, pl.proc)
    log(f"[knit variant {v.name}] {time.perf_counter() - t0:.0f} s, {'printable' if rep['printable'] else 'preview (not printable)'}")
    return {"form": "B-knit", "variant": v.name, "lattice": rep, "printable": rep["printable"], "pose": pl.pose, "_field": (fine, fg),
            "_core": (c0 < 0, pl.grid)}


# --------------------------------------------------------------------------------------------------- 3. checks + audit
def strided(phi: np.ndarray, g: Grid, step_mm: float = 1.0):
    s = max(round(step_mm / g.h), 1)
    return phi[::s, ::s, ::s], Grid(g.origin, g.h * s, g.dz * s, tuple(np.ceil(np.array(phi.shape) / s).astype(int)))


def check_and_audit(rep: dict, proc: ProcessConfig, lcfg: L.LatticeConfig, lattice_like: bool, samples: int = 10000, log=print) -> Result:
    phi, g = rep.pop("_field")
    comp = rep.pop("_comp", None)
    core = rep.pop("_core")
    secs = {}
    t0 = time.perf_counter()
    sealed_raw = L.sealed(phi, g)
    safety = {"filled_mm3": 0.0}
    if sealed_raw["count"]:  # same safety net as the lattice scripts; the raw count is reported (and warned about)
        safety = L.fill_sealed(phi, g)
    mesh, erep = L.to_mesh(phi, g, 400.0)
    secs["mesh"] = round(time.perf_counter() - t0, 1)
    geo = audit.geometry(mesh)
    inner = mesh_inner_shells(mesh)
    checks: list[Check] = []
    add = lambda *a: checks.append(Check(*a))

    add("watertight", "pass" if geo["watertight"] else "fail", str(geo["watertight"]), "导出网格必须是闭合流形")
    add("single_solid", "pass" if geo["bodies"] == 1 else "fail", f"{geo['bodies']} body", f"丢弃碎块 {erep.dropped_volume_mm3:.1f} mm³")
    after = L.sealed(phi, g) if sealed_raw["count"] else sealed_raw
    n_closed = inner["count"] + after["count"]
    patched = f"设计本身留有 {sealed_raw['count']} 个封闭气泡({sealed_raw['volume_mm3']} mm³),已由保险填充补实" if sealed_raw["count"] else ""
    big = sealed_raw["volume_mm3"] > SEALED_FAIL_MM3  # a pocket this size is a design error, not a voxel artefact
    add("no_closed_cavity", "fail" if (n_closed or big) else ("warn" if sealed_raw["count"] else "pass"),
        f"封闭空腔 {after['count']} 个,内壳 {inner['count']} 个", "封闭空腔会困住粉末" + (";" + patched if patched else ""))

    t0 = time.perf_counter()
    if lattice_like:
        thin = L.thin_material(phi, g, L.strut_min_mm(lcfg, proc))
        frac = thin["thin_fraction"]
        what = f"薄于 {2 * thin['test_radius_mm'] + g.h:.1f} mm 的材料占比"
    else:
        thin = audit.wall_thickness(mesh, samples)
        frac = thin["frac_thin"] or 0.0
        what = f"壁厚小于 {audit.THIN_MM} mm 的表面采样占比"
    add("min_rib", "pass" if frac <= THIN_FRAC_WARN else "warn", f"{frac:.4f}",
        f"{what};上限 {THIN_FRAC_WARN} 为暂定值(工艺下限 {proc.wall_min} mm)")
    secs["thin"] = round(time.perf_counter() - t0, 1)

    t0 = time.perf_counter()
    core_arr, cg = core  # cavity core on the coarse (template) grid
    if core_arr.shape == phi.shape:
        ch = channel_width(phi, g, core_arr, proc.min_channel_mm)
    else:  # lattice field: measure on a ~1 mm copy, the core resampled (nearest) onto it
        ps, gs = strided(phi, g)
        ix = [np.clip(np.rint((gs.origin[i] + np.arange(gs.shape[i]) * gs.spacing[i] - cg.origin[i]) / cg.spacing[i]).astype(int), 0, cg.shape[i] - 1)
              for i in range(3)]
        ch = channel_width(ps, gs, core_arr[np.ix_(*ix)], proc.min_channel_mm)
    secs["channel"] = round(time.perf_counter() - t0, 1)
    add("openings_drain", "pass" if ch["ok"] and ch["unreachable_mm3"] == 0 else "warn", f"通道宽 {ch['width_mm']} mm(要求 ≥ {proc.min_channel_mm})",
        f"粉末够不到的内腔 {ch['unreachable_mm3']} mm³;鞋口兼作排粉口")

    bv = build_volume(mesh.vertices, proc.machines, proc.part_gap_mm, proc.min_xy_mm)
    fits = [n for n, m in bv["machines"].items() if (m["aligned"] or m["rotated"]) and m["meets_min_xy"]]
    add("build_volume", "pass" if fits else "fail", "可放入:" + (", ".join(fits) or "无"), f"外形 {bv['bbox_mm']} mm,留边 {proc.part_gap_mm} mm")

    if comp is not None:
        gv = gate_verdict(comp.gate)
        st = {"usable": "pass", "reshape": "warn", "not_applicable": "warn"}[gv["verdict"]]
        rows = ", ".join(f"{k} {v['value']:.3g} ({v['status']})" for k, v in gv["rows"].items())
        add("gate_d8", st, gv["verdict"], rows + ";阈值为暂定值,need_fit 最大值判'不适用'的口径待定(D8)")
        rep["gate"] = gv
    else:
        add("gate_d8", "na", "n/a", "B 路线不推移模板外皮,模板只用于摆放鞋楦")

    printable = rep.pop("printable", None)
    if printable is not None:
        add("variant_printable", "pass" if printable else "warn", "可打印" if printable else "预览(不可打印)",
            "check_variant:体素 ≤ 最细特征 / 4 且各线径 ≥ 工艺下限;预览体素偏粗,只用于看形")

    dens = lcfg.tpu_density_g_cm3
    vol = float(mesh.volume)
    weight = round(vol / 1000 * dens, 1)
    add("weight", "pass", f"{weight} g", f"{vol / 1000:.1f} cm³,占位密度 {dens} g/cm³")

    rep.pop("compose", None)
    au = {"geometry": geo, "inner_shells": inner, "sealed_field": sealed_raw, "safety_fill": safety, "thin": thin, "channel": ch, "build_volume": bv,
          "volume_cm3": round(vol / 1000, 2), "weight_g": weight, "density_g_cm3": dens, "export": {"dropped_mm3": erep.dropped_volume_mm3,
          "components": erep.n_components}}
    return Result(mesh, checks, au, rep, secs)


# --------------------------------------------------------------------------------------------------- 4. report
ICON = {"pass": "通过", "warn": "注意", "fail": "未通过", "na": "不适用"}
CHECK_ZH = {
    "watertight": "水密", "single_solid": "单一实体", "no_closed_cavity": "无封闭空腔", "min_rib": "最小壁厚 / 筋宽",
    "openings_drain": "开口与排粉", "build_volume": "成型尺寸", "gate_d8": "Gate / D8 模板适用性", "variant_printable": "变体可打印性(check_variant)", "weight": "重量(占位密度)",
}


def report_md(p: dict, res: Result) -> str:
    form = p["form"]
    desc = "A 模板重建(模板外皮 + 鞋楦内腔 + 图案层" + (" + 鞋底点阵)" if p["lattice_sole"] else ")") if form == "A" else f"B-knit 针织变体 {p['variant']}(#47)" if form == "B-knit" else f"B 点阵鞋(样式 {p['style']})"
    lines = ["# 鞋款生成报告", "", f"- 路线:{desc}", f"- 码数:EU {p['size']}({p['template_id']})", f"- 工艺:{p['process']}",
             f"- 输入:{p['text'] or '(无文字)'}" + (f";图片 {len(p['images'])} 张" if p["images"] else ""), f"- 意图后端:{p['backend']}", ""]
    lines += ["## 意图与限幅", ""]
    lines += [f"- {s}" for s in p["clamp_report"]] or ["- 无调整,无不支持的请求"]
    if p.get("ignored"):
        lines += [f"- 未应用:{s}" for s in p["ignored"]]
    n_bad = sum(c.status == "fail" for c in res.checks)
    n_warn = sum(c.status == "warn" for c in res.checks)
    lines += ["", "## 审计结论", "", f"**{'存在未通过项' if n_bad else '全部硬性检查通过'}**(未通过 {n_bad},注意 {n_warn})。", "",
              "| 项目 | 结果 | 数值 | 说明 |", "|---|---|---|---|"]
    lines += [f"| {CHECK_ZH.get(c.name, c.name)} | {ICON[c.status]} | {c.value} | {c.detail} |" for c in res.checks]
    au = res.audit
    lines += ["", "## 成品", "", f"- STL:`shoe.stl`,{au['geometry']['faces']} 面,外形 {au['geometry']['extents_mm']} mm",
              f"- 体积 {au['volume_cm3']} cm³,重量约 {au['weight_g']} g(密度 {au['density_g_cm3']} g/cm³ 为占位值,待厂家数据)",
              f"- 峰值内存 {p['peak_rss_mb']} MB,总用时 {p['total_seconds']} s", "",
              "## 说明", "", "- 重量按占位密度估算;壁厚 / 筋宽的 2% 阈值与 Gate 面积阈值为暂定值(D8)。",
              "- 输出由 CC BY-NC-ND / 来源不明的素材衍生,仅供内部评估,不得分发。"]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------------------------------- 5. driver
def run(a, log=None) -> int:
    log = log or (lambda s: print(s, flush=True))
    t_all = time.perf_counter()
    proc = ProcessConfig.from_toml(a.process)
    lcfg = L.LatticeConfig.from_toml()
    if a.size not in SIZES:
        raise MakeShoeError(f"size {a.size}: only the template sizes {SIZES} exist")
    images = [(p, "reference") for p in a.image] + ([(a.pattern_image, "pattern")] if a.pattern_image else [])
    text = " ".join(a.text)
    res_i = resolve_intent(text, images, make_backend(a.backend, a.model) if (text.strip() or images) else None)
    intent = res_i.intent
    clamp_report = clamp_lines(res_i)
    log("意图 / intent clamp report:")
    for s in clamp_report or ["(no changes)"]:
        log("  " + s)

    template = (a.template or intent.get("template") or "solid").removeprefix("dlp_")  # intent: dlp_solid / dlp_hollow
    shoe_id = f"dlp_{template}_{a.size}_right"
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ignored: list[str] = []
    spec = None
    style = variant = None
    if a.form == "A":
        spec = intent_pattern(intent, a.pattern, a.pattern_image)
        clamp_report += [f"pattern.{k}: {v['from']} -> {v['to']} (design space)" for k, v in spec.clamped.items()]
        ignored += list(spec.notes)
    elif a.form == "B-knit":
        variant, note = load_variant(a.variant, a.pattern_image or (a.image[0] if a.image else None))
        ignored.append("B-knit takes its look from --variant; the intent's pattern / style tags are not applied")
        if note:
            ignored.append(note)
        if a.lattice_sole or a.outline:
            ignored.append("--lattice-sole / --outline do not apply to B-knit (the variant defines sole and outline)")
    else:
        style, outline_clamps = load_style(a.style, a.outline)
        clamp_report += [f"outline: {s}" for s in outline_clamps]
        if intent.get("pattern") or intent.get("style_tags"):
            ignored.append("form B takes its look from --style; the intent's pattern / style tags are not applied")
        if a.lattice_sole:
            ignored.append("--lattice-sole is form A only (form B has its own net sole)")
    t0 = time.perf_counter()
    log(f"[template] {shoe_id}, process {proc.name}")
    pl = place_cavity(shoe_id, proc, a.h, intent.get("instep_ease_mm"))
    log(f"[envelope + pose + cavity] {time.perf_counter() - t0:.0f} s, peak RSS {rss_mb():.0f} MB")
    if a.form == "A":
        rep = build_form_a(pl, spec, a.lattice_sole, lcfg, log)
    else:
        try:
            rep = build_form_knit(pl, variant, lcfg, log) if a.form == "B-knit" else build_form_b(pl, style, lcfg, log)
        except L.LatticeError as e:
            raise MakeShoeError(f"lattice shoe refused: {e}") from None
    lattice_like = a.form != "A" or a.lattice_sole
    res = check_and_audit(rep, proc, lcfg, lattice_like, a.samples, log)
    res.mesh.export(out / "shoe.stl")
    params = {"form": a.form, "size": a.size, "template": template, "template_id": shoe_id, "process": proc.name, "process_file": a.process,
              "backend": a.backend if (text.strip() or images) else "none", "text": text, "images": [p for p, _ in images],
              "intent": intent, "clamp_report": clamp_report, "ignored": ignored, "lattice_sole": a.lattice_sole, "style": a.style if a.form == "B" else None, "variant": a.variant if a.form == "B-knit" else None,
              "pattern": spec.to_dict() if spec else None, "h_mm": a.h, "seconds": res.seconds, "peak_rss_mb": round(rss_mb()),
              "total_seconds": round(time.perf_counter() - t_all, 1), "stl": str(out / "shoe.stl")}
    (out / "params.json").write_text(json.dumps({**params, "build": res.build}, indent=1, ensure_ascii=False, default=float))
    (out / "audit.json").write_text(json.dumps({"checks": [c.to_dict() for c in res.checks], **res.audit}, indent=1, ensure_ascii=False, default=float))
    (out / "report.md").write_text(report_md(params, res), encoding="utf-8")
    for c in res.checks:
        log(f"  [{c.status:4}] {c.name}: {c.value}")
    size_mb = (out / "shoe.stl").stat().st_size / 1e6
    log(f"-> {out}/ shoe.stl {size_mb:.0f} MB, report.md, params.json, audit.json; {params['total_seconds']} s, peak RSS {params['peak_rss_mb']} MB")
    return 1 if res.failed else 0


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="make-shoe", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("text", nargs="*", help="request text (Chinese or English); may be empty when --image / --pattern-image is given")
    ap.add_argument("--image", action="append", default=[], help="reference shoe image (repeatable)")
    ap.add_argument("--pattern-image", help="pattern image (form A: image pattern)")
    ap.add_argument("--backend", choices=["rules", "claude", "deepseek"], default="rules", help="intent parser (default rules, offline)")
    ap.add_argument("--model", help="override the backend model id")
    ap.add_argument("--form", choices=["A", "B", "B-knit"], default="A",
                    help="A template rebuild (default), B conformal lattice shoe (#46), B-knit modeling-freedom variants (#47)")
    ap.add_argument("--size", type=int, default=44, help=f"EU size, one of {SIZES} (default 44)")
    ap.add_argument("--template", choices=TEMPLATES, help="solid / hollow (default: the intent's, else solid)")
    ap.add_argument("--process", default=DEFAULT_PROCESS)
    ap.add_argument("--pattern", choices=["none", "hex_perforation", "voronoi_perforation", "wave_displacement", "rib_displacement", "image"],
                    help="form A: force the pattern kind")
    ap.add_argument("--lattice-sole", action="store_true", help="form A: lattice sole (#14)")
    ap.add_argument("--style", default=DEFAULT_STYLE, help=f"form B: variant name (default {DEFAULT_STYLE}); see config/lattice_conformal*.toml")
    ap.add_argument("--variant", default=DEFAULT_VARIANT, help=f"form B-knit: {', '.join(variant_names())} (default {DEFAULT_VARIANT})")
    ap.add_argument("--outline", help="form B: outline functions as a JSON list or a JSON file (see shoe_designer.outline)")
    ap.add_argument("--h", type=float, default=1.0, help="template grid spacing in mm")
    ap.add_argument("--samples", type=int, default=10000, help="wall-thickness ray samples (solid forms)")
    ap.add_argument("--out-dir", default="reports/make_shoe/out")
    return ap


def main(argv=None) -> int:
    a = parser().parse_args(argv)
    try:
        return run(a)
    except MakeShoeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # missing assets etc.: name the cause, keep the traceback for real bugs
        if isinstance(e, AssetError):
            print(f"error: {e} (set SHOE_ASSETS_ROOT, see README)", file=sys.stderr)
            return 2
        raise


if __name__ == "__main__":
    sys.exit(main())
