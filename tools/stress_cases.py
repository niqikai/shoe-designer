"""Reproducible, schema-bounded M5 cases; this module never generates geometry.

The plan is deliberately independent of manufacturing acceptance. For example,
a legal 100 mm build volume is retained even when a whole shoe cannot fit it.
No design, asset, output, or clock state is read when constructing a plan.
"""
import argparse
import hashlib
import json
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.params import normalize_params, read_schema

GENERATOR = "schema-random-m5-v1"
METADATA_FIELDS = {"schema_version", "revision"}


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _rng(seed, *parts):
    # Separate streams make prefixes stable when count or sharding changes.
    return random.Random(int(fingerprint([seed, *parts]), 16))


def _limits(schema, values):
    limits = {key: dict(spec) for key, spec in schema["properties"].items()}
    for clause in schema.get("allOf", []):
        conditions = clause["if"].get("properties", {})
        if all(values.get(key) == rule["const"] for key, rule in conditions.items()):
            for key, constraint in clause["then"].get("properties", {}).items():
                limits[key].update(constraint)
    return limits


def _enum_value(seed, key, options, index):
    # Every block contains every enum value, with a fresh independent order.
    # A single cyclic order would correlate all two-valued fields forever.
    block, offset = divmod(index, len(options))
    order = list(options)
    _rng(seed, "enum", key, block).shuffle(order)
    return order[offset]


def _random_request(schema, seed, index):
    properties = schema["properties"]
    requested = {key: spec["default"] for key, spec in properties.items()}
    for key, spec in properties.items():
        if "enum" in spec:
            requested[key] = _enum_value(seed, key, spec["enum"], index)
    limits = _limits(schema, requested)
    rng = _rng(seed, "numeric", index)
    for key, spec in limits.items():
        if key in METADATA_FIELDS or spec["type"] not in ("number", "integer"):
            continue
        lower, upper = spec["minimum"], spec["maximum"]
        if key == "boundary_rounding_mm":
            # normalize_params owns the associated wall-thickness cap.
            probe = dict(requested, boundary_rounding_mm=upper)
            upper = normalize_params(probe)[0][key]
            if index % 4 == 0:
                requested[key] = lower
                continue
        if spec["type"] == "integer":
            requested[key] = rng.randint(int(lower), int(upper))
        else:
            requested[key] = min(upper, max(lower, round(rng.uniform(lower, upper), 6)))
    return requested


def _case(kind, index, label, requested):
    effective, clamps = normalize_params(requested)
    content = {"requested": requested, "effective": effective, "clamps": clamps}
    digest = fingerprint(content)
    return {"case_id": f"{kind}-{index + 1:04d}-{digest[:12]}",
            "kind": kind, "index": index, "label": label,
            **content, "fingerprint": digest}


def _boundary_requests(schema):
    properties = schema["properties"]
    seen = set()

    def emit(label, patch):
        # Fully effective legal values are then recorded as the request. This
        # repairs context defaults (mid collar) without hiding any rejection.
        requested = normalize_params(patch)[0]
        digest = fingerprint(requested)
        if digest not in seen:
            seen.add(digest)
            return label, requested
        return None

    yield "schema-defaults", normalize_params({})[0]
    seen.add(fingerprint(normalize_params({})[0]))
    for key, spec in properties.items():
        if "enum" in spec:
            for value in spec["enum"]:
                result = emit(f"{key}={value}", {key: value})
                if result:
                    yield result
        if key in METADATA_FIELDS or spec["type"] not in ("number", "integer"):
            continue
        for edge in ("minimum", "maximum"):
            patch = {key: spec[edge]}
            if key == "boundary_rounding_mm" and edge == "maximum":
                # Reach the schema maximum with a legal associated wall.
                patch["upper_thickness_mm"] = properties["upper_thickness_mm"]["maximum"]
            if key == "collar_height_mm" and edge == "maximum":
                # Find the style whose conditional range admits the maximum.
                for style in properties["collar_style"]["enum"]:
                    context = normalize_params({"collar_style": style})[0]
                    if _limits(schema, context)[key]["maximum"] >= spec[edge]:
                        patch["collar_style"] = style
                        break
            result = emit(f"{key}:{edge}", patch)
            if result:
                yield result
    for clause in schema.get("allOf", []):
        context = {key: rule["const"] for key, rule in clause["if"]["properties"].items()}
        for key, constraint in clause["then"]["properties"].items():
            for edge in ("minimum", "maximum"):
                if edge in constraint:
                    result = emit(f"{context}:{key}:{edge}", {**context, key: constraint[edge]})
                    if result:
                        yield result
    # The radius/wall association is implemented by normalize_params rather
    # than JSON Schema allOf, so exercise both ends of that wall explicitly.
    for edge in ("minimum", "maximum"):
        wall = properties["upper_thickness_mm"][edge]
        result = emit(f"wall-{edge}:rounding-cap", {
            "upper_thickness_mm": wall,
            "boundary_rounding_mm": properties["boundary_rounding_mm"]["maximum"],
        })
        if result:
            yield result
    # A lattice density/rod endpoint on a solid shoe would be a no-op. Retain
    # the ordinary schema boundaries above, and also activate every topology
    # while exercising its numerical limits and free-edge finishing states.
    lattice_numeric = [key for key, spec in properties.items()
                       if key.startswith("lattice_") and spec["type"] in ("number", "integer")]
    for topology in properties["lattice_type"]["enum"]:
        context = {"midsole_structure": "lattice", "lattice_type": topology}
        result = emit(f"active-lattice:{topology}", context)
        if result:
            yield result
        for key in lattice_numeric + ["boundary_rounding_mm"]:
            for edge in ("minimum", "maximum"):
                patch = {**context, key: properties[key][edge]}
                if key == "boundary_rounding_mm" and edge == "maximum":
                    patch["upper_thickness_mm"] = properties["upper_thickness_mm"]["maximum"]
                result = emit(f"active-{topology}:{key}:{edge}", patch)
                if result:
                    yield result
        result = emit(f"active-{topology}:thin-wall-rounding-cap", {
            **context, "upper_thickness_mm": properties["upper_thickness_mm"]["minimum"],
            "boundary_rounding_mm": properties["boundary_rounding_mm"]["maximum"],
        })
        if result:
            yield result
    for label, patch in _combination_corners(schema):
        result = emit(label, patch)
        if result:
            yield result


def _combination_corners(schema):
    """Coupled geometric extremes, beyond independent one-field boundaries."""
    properties = schema["properties"]

    def edge(key, name):
        return properties[key][name]

    def collar(style, name):
        context = normalize_params({"collar_style": style})[0]
        return _limits(schema, context)["collar_height_mm"][name]

    minimum, maximum = "minimum", "maximum"
    # These are the four historical M1 shape corners. emit() normalizes their
    # narrow/wide toe limits before recording the fully legal request.
    corners = [
        {"size_eu": edge("size_eu", minimum), "foot_width": "narrow", "collar_style": "low",
         "collar_height_mm": collar("low", minimum), "upper_thickness_mm": edge("upper_thickness_mm", minimum),
         "heel_sole_mm": edge("heel_sole_mm", minimum), "forefoot_sole_mm": edge("forefoot_sole_mm", minimum),
         "toe_roundness": edge("toe_roundness", minimum), "toe_height_scale": edge("toe_height_scale", minimum),
         "edge_radius_mm": edge("edge_radius_mm", minimum), "outsole_flare_mm": edge("outsole_flare_mm", minimum)},
        {"size_eu": edge("size_eu", maximum), "foot_width": "wide", "collar_style": "mid",
         "collar_height_mm": collar("mid", maximum), "upper_thickness_mm": edge("upper_thickness_mm", minimum),
         "heel_sole_mm": edge("heel_sole_mm", maximum), "forefoot_sole_mm": edge("forefoot_sole_mm", maximum),
         "toe_roundness": edge("toe_roundness", maximum), "toe_height_scale": edge("toe_height_scale", maximum),
         "edge_radius_mm": edge("edge_radius_mm", maximum), "outsole_flare_mm": edge("outsole_flare_mm", maximum)},
        {"size_eu": edge("size_eu", minimum), "foot_width": "wide", "collar_style": "mid",
         "collar_height_mm": collar("mid", maximum), "upper_thickness_mm": edge("upper_thickness_mm", maximum),
         "heel_sole_mm": edge("heel_sole_mm", maximum), "forefoot_sole_mm": edge("forefoot_sole_mm", minimum),
         "toe_roundness": edge("toe_roundness", maximum), "toe_height_scale": edge("toe_height_scale", minimum),
         "edge_radius_mm": edge("edge_radius_mm", maximum), "outsole_flare_mm": edge("outsole_flare_mm", minimum)},
        {"size_eu": edge("size_eu", maximum), "foot_width": "narrow", "foot_side": "left", "collar_style": "low",
         "collar_height_mm": collar("low", maximum), "upper_thickness_mm": edge("upper_thickness_mm", minimum),
         "heel_sole_mm": edge("heel_sole_mm", minimum), "forefoot_sole_mm": edge("forefoot_sole_mm", maximum),
         "toe_roundness": edge("toe_roundness", minimum), "toe_height_scale": edge("toe_height_scale", maximum),
         "edge_radius_mm": edge("edge_radius_mm", maximum), "outsole_flare_mm": edge("outsole_flare_mm", maximum)},
    ]
    for index, corner in enumerate(corners):
        yield f"corner-m1-{index + 1:02d}", {**corner, "midsole_structure": "solid", "boundary_rounding_mm": 0}

    fragile = {"heel_sole_mm": edge("heel_sole_mm", minimum),
               "forefoot_sole_mm": edge("forefoot_sole_mm", minimum),
               "upper_thickness_mm": edge("upper_thickness_mm", maximum),
               "edge_radius_mm": edge("edge_radius_mm", maximum),
               "outsole_flare_mm": edge("outsole_flare_mm", minimum),
               "lattice_rod_mm": edge("lattice_rod_mm", minimum),
               "lattice_density_heel": edge("lattice_density_heel", minimum),
               "lattice_density_arch": edge("lattice_density_arch", maximum),
               "lattice_density_forefoot": edge("lattice_density_forefoot", minimum),
               "boundary_rounding_mm": edge("boundary_rounding_mm", maximum)}
    for topology in properties["lattice_type"]["enum"]:
        for style, size_edge, collar_edge in (("low", minimum, minimum), ("mid", maximum, maximum)):
            yield f"corner-thin-gradient-{topology}-{style}", {
                **fragile, "midsole_structure": "lattice", "lattice_type": topology,
                "size_eu": edge("size_eu", size_edge), "collar_style": style,
                "collar_height_mm": collar(style, collar_edge),
                "resolution": "preview" if style == "low" else "export",
            }

    contexts = [("solid", {"midsole_structure": "solid"})] + [
        (topology, {"midsole_structure": "lattice", "lattice_type": topology})
        for topology in properties["lattice_type"]["enum"]]
    for name, context in contexts:
        for slope, heel_edge, front_edge in (("heel-low-front-high", minimum, maximum),
                                            ("heel-high-front-low", maximum, minimum)):
            yield f"corner-slope-{name}-{slope}", {
                **fragile, **context,
                "heel_sole_mm": edge("heel_sole_mm", heel_edge),
                "forefoot_sole_mm": edge("forefoot_sole_mm", front_edge),
            }
        yield f"corner-high-collar-small-{name}", {
            **context, "size_eu": edge("size_eu", minimum), "collar_style": "mid",
            "collar_height_mm": collar("mid", maximum), "foot_width": "wide",
            "toe_roundness": edge("toe_roundness", maximum),
            "toe_height_scale": edge("toe_height_scale", minimum),
            "boundary_rounding_mm": edge("boundary_rounding_mm", maximum),
        }


def make_manifest(count=1000, seed=20261009, include_boundaries=True):
    """Return a pure JSON-compatible plan with exactly count random cases."""
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise ValueError("随机案例数必须是正整数。")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("随机种子必须是整数。")
    if not isinstance(include_boundaries, bool):
        raise ValueError("include_boundaries 必须是布尔值。")
    schema = read_schema()
    cases = [_case("random", index, f"seed={seed};index={index}",
                   _random_request(schema, seed, index)) for index in range(count)]
    if include_boundaries:
        for index, (label, requested) in enumerate(_boundary_requests(schema)):
            cases.append(_case("boundary", index, label, requested))
    coverage = {}
    for key, spec in schema["properties"].items():
        values = [case["effective"][key] for case in cases]
        if "enum" in spec:
            coverage[key] = {value: values.count(value) for value in spec["enum"]}
        elif key not in METADATA_FIELDS and spec["type"] in ("number", "integer"):
            coverage[key] = {"minimum": min(values), "maximum": max(values)}
    manifest = {"format_version": 1, "generator": GENERATOR, "seed": seed,
                "schema_sha256": fingerprint(schema),
                "counts": {"random": count, "boundary": len(cases) - count,
                           "total": len(cases)}, "coverage": coverage, "cases": cases}
    manifest["plan_sha256"] = fingerprint(manifest)
    return manifest


def select_shard(manifest, shard_index, shard_count):
    """Partition in plan order; all shards are disjoint and merge losslessly."""
    if (isinstance(shard_count, bool) or not isinstance(shard_count, int) or shard_count < 1
            or isinstance(shard_index, bool) or not isinstance(shard_index, int)
            or not 0 <= shard_index < shard_count):
        raise ValueError("分片需满足 0 <= shard_index < shard_count，且 shard_count 为正整数。")
    return manifest["cases"][shard_index::shard_count]


def main(argv=None):
    parser = argparse.ArgumentParser(description="只生成 M5 固定种子参数计划，不读取鞋楦或生成网格。")
    parser.add_argument("--count", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=20261009)
    parser.add_argument("--no-boundaries", action="store_true")
    options = parser.parse_args(argv)
    print(json.dumps(make_manifest(options.count, options.seed, not options.no_boundaries),
                     ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
