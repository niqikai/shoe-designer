"""Schema-driven defaults and clamps, without an extra Python dependency."""
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_schema():
    return json.loads((ROOT / "schema/shoe_params.schema.json").read_text(encoding="utf-8"))


def normalize_params(values):
    """Return effective parameters and user-facing clamp messages; never write design files."""
    if not isinstance(values, dict):
        raise ValueError("设计参数必须是 JSON 对象。")
    schema = read_schema()
    properties = schema["properties"]
    unknown = set(values) - set(properties)
    if unknown:
        raise ValueError("当前阶段不支持这些参数：" + ", ".join(sorted(unknown)))
    result = {key: spec["default"] for key, spec in properties.items()}
    result.update(values)
    warnings = []
    for key, spec in properties.items():
        item = result[key]
        expected = spec["type"]
        if expected == "string" and not isinstance(item, str):
            raise ValueError(key + " 必须是文字。")
        if expected in ("number", "integer"):
            if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item):
                raise ValueError(key + " 必须是有限数值。")
            if expected == "integer" and not isinstance(item, int):
                raise ValueError(key + " 必须是整数。")
        if "const" in spec and item != spec["const"]:
            raise ValueError(key + " 版本不受支持。")
        if "enum" in spec and item not in spec["enum"]:
            raise ValueError(key + " 可选值为：" + ", ".join(spec["enum"]))
        if key == "revision" and item < 0:
            raise ValueError("设计版本不能为负数。")
    style = result["collar_style"]
    if "collar_height_mm" not in values:
        result["collar_height_mm"] = schema["x-style-defaults"][style]
    limits = {key: dict(spec) for key, spec in properties.items()}
    for conditional in schema["allOf"]:
        conditions = conditional["if"]["properties"]
        if all(result.get(key) == condition["const"] for key, condition in conditions.items()):
            for key, constraint in conditional["then"]["properties"].items():
                limits[key].update(constraint)
    for key, spec in limits.items():
        if spec["type"] not in ("number", "integer") or key in ("schema_version", "revision"):
            continue
        original = result[key]
        effective = min(spec.get("maximum", math.inf), max(spec.get("minimum", -math.inf), original))
        result[key] = int(effective) if spec["type"] == "integer" else float(effective)
        if original == effective:
            continue
        if key == "collar_height_mm":
            warnings.append(f"鞋口高度 {original:g} mm 超出{('低帮' if style == 'low' else '中帮')}裁切范围，已截断为 {effective:g} mm。")
        elif key == "toe_roundness" and result["foot_width"] != "standard":
            warnings.append(f"脚宽与鞋头圆度叠加超过局部 ±15% 范围，鞋头圆度已截断为 {effective:g}。")
        else:
            unit = " " + spec["x-unit"] if "x-unit" in spec else ""
            warnings.append(f"{spec.get('title', key)} {original:g}{unit} 超出允许范围，已截断为 {effective:g}{unit}。")
    return result, warnings


def load_params(path):
    return normalize_params(json.loads(Path(path).read_text(encoding="utf-8")))
