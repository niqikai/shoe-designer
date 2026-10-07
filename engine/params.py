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
    limits = dict(properties["collar_height_mm"])
    for conditional in schema["allOf"]:
        if conditional["if"]["properties"]["collar_style"]["const"] == style:
            limits.update(conditional["then"]["properties"]["collar_height_mm"])
    original = result["collar_height_mm"]
    effective = min(limits["maximum"], max(limits["minimum"], original))
    result["collar_height_mm"] = float(effective)
    if original != effective:
        warnings.append(f"鞋口高度 {original:g} mm 超出{('低帮' if style == 'low' else '中帮')}裁切范围，已截断为 {effective:g} mm。")
    return result, warnings


def load_params(path):
    return normalize_params(json.loads(Path(path).read_text(encoding="utf-8")))
