"""Bounded local-language edits, driven by the registry in semantic_map.md."""
from dataclasses import dataclass
import json
import re
import unicodedata

from engine.params import normalize_params, read_schema


class EditError(ValueError):
    """A user-facing request error; no geometry or design should be changed."""


@dataclass
class Interpretation:
    params: dict
    notes: list
    clamps: list
    matched_rules: list


def number(value):
    """Arabic or simple Chinese numbers, including 十/百/千 and decimal 点."""
    try:
        result = float(value)
    except ValueError:
        digits = dict(zip("零〇一二两三四五六七八九", (0, 0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9)))
        integer, _, fraction = value.partition("点")
        if any(char not in digits and char not in "十百千" for char in integer):
            raise EditError("无法理解数值：" + value)
        if any(char not in digits for char in fraction):
            raise EditError("无法理解数值：" + value)
        if any(char in "十百千" for char in integer):
            result, part = 0, 0
            for char in integer:
                if char in digits:
                    part = digits[char]
                else:
                    result += (part or 1) * {"十": 10, "百": 100, "千": 1000}[char]
                    part = 0
            result += part
        else:
            result = int("".join(str(digits[char]) for char in integer) or "0")
        if fraction:
            result += float("0." + "".join(str(digits[char]) for char in fraction))
    return int(result) if float(result).is_integer() else result


def read_rules(path):
    source = path.read_text(encoding="utf-8")
    match = re.search(r"<!-- M4_RULES_START -->\s*```json\s*(.*?)\s*```\s*<!-- M4_RULES_END -->", source, re.S)
    if not match:
        raise EditError("语义表缺少 M4 规则区。")
    registry = json.loads(match.group(1))
    fields = set(read_schema()["properties"]) - {"revision", "schema_version"}
    for rule in registry["rules"]:
        re.compile(rule["pattern"], re.I)
        for action in rule.get("actions", []):
            field = action["field"]
            names = [field] if isinstance(field, str) else field["map"].values()
            if not set(names) <= fields or action.get("op", "set") not in ("set", "add", "step"):
                raise EditError("语义规则包含不支持的参数或操作：" + rule["id"])
    return registry


def _value(spec, match):
    if not isinstance(spec, dict):
        return spec
    captured = match.group(spec["group"])
    if captured is None and spec.get("fallback_group"):
        captured = match.group(spec["fallback_group"])
    if "map" in spec:
        return spec["map"][captured]
    result = number(captured)
    if spec.get("percent_group") and match.group(spec["percent_group"]):
        result /= 100
    return result * spec.get("scale", 1)


def interpret(text, current, semantic_path):
    """Resolve every clause before applying it; unknown clauses fail closed."""
    registry = read_rules(semantic_path)
    text = unicodedata.normalize("NFKC", text).strip()
    candidates = []
    for order, rule in enumerate(registry["rules"]):
        for match in re.finditer(rule["pattern"], text, re.I):
            candidates.append((match, rule, order))
    # Explicit long expressions outrank a contained short expression, such as
    # TPU 90A versus TPU, or a relative toe edit versus a generic round toe.
    covered = [False] * len(text)
    chosen = []
    for match, rule, order in sorted(candidates, key=lambda item: (-(item[0].end() - item[0].start()), item[2])):
        if any(covered[match.start():match.end()]):
            continue
        covered[match.start():match.end()] = [True] * (match.end() - match.start())
        chosen.append((match, rule))
    remainder = "".join(" " if used else char for char, used in zip(text, covered))
    remainder = re.sub(registry["filler_pattern"], "", remainder, flags=re.I)
    remainder = re.sub(r"[\s，,。.!！?？、；;：:/]", "", remainder)
    if remainder or not chosen:
        raise EditError(f"尚未识别这段要求：{remainder or text}。本次未修改设计；可使用语义表中的表达或 --set 指定已支持参数。")

    params, _ = normalize_params(current)
    notes, matched = [], []
    for match, rule in sorted(chosen, key=lambda item: item[0].start()):
        matched.append(rule["id"])
        if rule.get("when") == "lattice" and params["midsole_structure"] != "lattice":
            notes.append("当前是实心中底；“" + match.group() + "”只记录意图，未启用晶格或改变几何。")
            continue
        for action in rule.get("actions", []):
            field = _value(action["field"], match)
            value = _value(action["value"], match)
            operation = action.get("op", "set")
            if operation == "add":
                value = round(params[field] + value, 9)
            elif operation == "step":
                choices = action["choices"]
                index = choices.index(params[field]) + value
                limited = min(len(choices) - 1, max(0, index))
                if limited != index:
                    notes.append("脚宽已到当前档位边界，保持该档位。")
                value = choices[limited]
            params[field] = value
        if rule.get("note"):
            notes.append(rule["note"])
    params, clamps = normalize_params(params)
    return Interpretation(params, list(dict.fromkeys(notes)), clamps, matched)


def apply_sets(current, assignments):
    """Typed JSON primitives, with bare enum strings permitted for shell use."""
    values = dict(current)
    for assignment in assignments:
        key, separator, raw = assignment.partition("=")
        if not separator or not key or not raw:
            raise EditError("--set 格式为 参数=值，例如 heel_sole_mm=27。")
        if key in ("schema_version", "revision"):
            raise EditError("版本号由工具管理，不能用 --set 修改。")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            value = raw
        values[key] = value
    effective, clamps = normalize_params(values)
    return effective, clamps
