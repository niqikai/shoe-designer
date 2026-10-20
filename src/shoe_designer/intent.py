"""Intent parsing (#16): text / images -> a strict, pipeline-realisable parameter set.

The parser never produces geometry and never invents values. A backend (Claude API or the rule-based mock)
returns a JSON object; this module

* validates it against ``schemas/intent.schema.json`` (extra fields, wrong types, values outside an enum are
  rejected with a readable error; one retry hands the errors back to the backend),
* clamps out-of-range numbers into the design space and reports every change,
* fills pattern parameters from the style-tag presets (``config/intent.toml``),
* reports requests the design space cannot realise (heel height, toe shape ...) in ``unsupported_requests``.

jsonschema is in the dev dependency group; the Claude backend needs the optional ``intent`` extra (anthropic). See docs/design/issue-16-intent.md.
"""

from __future__ import annotations

import base64
import copy
import json
import math
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

try:
    import jsonschema
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "shoe_designer.intent needs jsonschema: run `uv sync` (it is in the dev group) or `pip install jsonschema`"
    ) from e

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config" / "intent.toml"
SCHEMA_PATH = ROOT / "schemas" / "intent.schema.json"

MEDIA_TYPES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}
_MAGIC = {"png": (b"\x89PNG\r\n\x1a\n",), "jpg": (b"\xff\xd8\xff",), "jpeg": (b"\xff\xd8\xff",), "webp": (b"RIFF",)}

# PatternSpec parameters each kind actually uses (anything else the user gave is reported as dropped).
_KIND_PARAMS = {
    "none": set(),
    "hex_perforation": {"scale_mm", "open_ratio"},
    "voronoi_perforation": {"scale_mm", "open_ratio"},
    "wave_displacement": {"scale_mm", "depth_mm"},
    "rib_displacement": {"scale_mm", "depth_mm"},
    "image": {"scale_mm", "image", "image_use"},
}
_NUMERIC_PARAMS = ("scale_mm", "open_ratio", "depth_mm", "seed")


class IntentError(Exception):
    pass


class ImageInputError(IntentError):
    """An input image is missing, not an accepted format, or too large."""


class BackendError(IntentError):
    """The backend could not produce an answer (network, refusal, truncated output ...). Not retried."""


def load_config(path: str | Path = CONFIG_PATH) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def load_schema(path: str | Path = SCHEMA_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def strict_schema(schema: dict | None = None) -> dict:
    """Schema where each ``x-clamp: [lo, hi]`` becomes ``minimum`` / ``maximum`` (what a final intent must satisfy)."""

    def walk(node):
        if isinstance(node, dict):
            out = {k: walk(v) for k, v in node.items()}
            if "x-clamp" in out:
                lo, hi = out.pop("x-clamp")
                out["minimum"], out["maximum"] = lo, hi
            return out
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(copy.deepcopy(schema or load_schema()))


_UNSUPPORTED_KEYWORDS = {"uniqueItems", "minItems", "maxItems", "minLength", "maxLength", "$schema", "$id"}


def api_schema(schema: dict | None = None) -> dict:
    """The schema for Claude's structured output: only the keywords it accepts (no ``x-*``, no size limits).

    The dropped constraints are re-checked by :func:`validate_intent` on the response."""

    def walk(node):
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items() if not k.startswith("x-") and k not in _UNSUPPORTED_KEYWORDS}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(copy.deepcopy(schema or load_schema()))


# ---------------------------------------------------------------- images


@dataclass(frozen=True)
class IntentImage:
    """``reference``: a shoe photo to read style from. ``pattern``: a pattern picture used as PatternSpec.image."""

    path: str
    role: str  # "reference" | "pattern"
    media_type: str = ""

    def read_b64(self) -> str:
        return base64.standard_b64encode(Path(self.path).read_bytes()).decode("ascii")


def check_images(images, config: dict | None = None) -> list[IntentImage]:
    cfg = (config or load_config())["images"]
    out: list[IntentImage] = []
    if len(images) > cfg["max_count"]:
        raise ImageInputError(f"too many images: {len(images)} > {cfg['max_count']}")
    for item in images:
        path, role = (item.path, item.role) if isinstance(item, IntentImage) else item
        if role not in ("reference", "pattern"):
            raise ImageInputError(f"{path}: image role must be 'reference' or 'pattern', got {role!r}")
        p = Path(path).expanduser()
        if not p.is_file():
            raise ImageInputError(f"{path}: file does not exist")
        ext = p.suffix.lower().lstrip(".")
        if ext not in cfg["formats"]:
            raise ImageInputError(f"{path}: format .{ext} not accepted (allowed: {', '.join(cfg['formats'])})")
        size = p.stat().st_size
        if size == 0 or size > cfg["max_bytes"]:
            raise ImageInputError(f"{path}: size {size} bytes outside (0, {cfg['max_bytes']}]")
        with open(p, "rb") as f:
            head = f.read(12)
        if not head.startswith(_MAGIC[ext]) or (ext == "webp" and head[8:12] != b"WEBP"):
            raise ImageInputError(f"{path}: content is not a valid .{ext} file")
        out.append(IntentImage(str(p.resolve()), role, MEDIA_TYPES[ext]))
    if sum(i.role == "pattern" for i in out) > 1:
        raise ImageInputError("at most one pattern image is allowed")
    return out


# ---------------------------------------------------------------- unsupported requests


def detect_unsupported(text: str, config: dict | None = None) -> list[str]:
    """Labels of the unsupported requests mentioned in ``text`` (deterministic keyword scan)."""
    found = []
    for entry in (config or load_config())["unsupported"]:
        if any(re.search(p, text, re.IGNORECASE) for p in entry["patterns"]):
            found.append(entry["label"])
    return found


def _mentions(item: str, entry: dict) -> bool:
    return any(re.search(p, item, re.IGNORECASE) for p in entry["patterns"]) or item.strip().lower() == entry["label"].lower()


# ---------------------------------------------------------------- request / result


@dataclass(frozen=True)
class IntentRequest:
    text: str
    images: tuple[IntentImage, ...] = ()
    feedback: str | None = None  # validation errors of the previous attempt (retry only)


@dataclass(frozen=True)
class Change:
    field: str
    original: Any
    new: Any
    reason: str


@dataclass
class IntentResult:
    ok: bool
    intent: dict | None = None
    changes: list[Change] = field(default_factory=list)  # clamps, preset fills of irrelevant params, overrides
    warnings: list[str] = field(default_factory=list)  # e.g. unsupported mention the backend forgot to list
    errors: list[str] = field(default_factory=list)  # when ok is False
    attempts: int = 0

    @property
    def unsupported_requests(self) -> list[str]:
        return list(self.intent["unsupported_requests"]) if self.intent else []

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "intent": self.intent,
            "changes": [vars(c) for c in self.changes],
            "warnings": self.warnings,
            "errors": self.errors,
            "attempts": self.attempts,
        }


class IntentBackend(Protocol):
    def complete(self, request: IntentRequest) -> str | dict:
        """Return the intent as a JSON string or dict. May raise :class:`BackendError`."""


# ---------------------------------------------------------------- validation


def _loads(raw: str | dict) -> tuple[dict | None, list[str]]:
    if isinstance(raw, dict):
        return raw, []

    def bad_const(name):
        raise ValueError(f"non-finite number {name} is not valid JSON")

    try:
        obj = json.loads(raw, parse_constant=bad_const)
    except ValueError as e:
        return None, [f"output is not valid JSON: {e}"]
    if not isinstance(obj, dict):
        return None, [f"top level must be a JSON object, got {type(obj).__name__}"]
    return obj, []


def _path(err) -> str:
    return ".".join(str(p) for p in err.absolute_path) or "(root)"


def _schema_errors(obj: dict, schema: dict) -> list[str]:
    v = jsonschema.Draft202012Validator(schema)
    msgs = []
    for err in sorted(v.iter_errors(obj), key=lambda e: list(map(str, e.absolute_path))):
        # anyOf failures say "x is not valid under any of the given schemas"; descend to the useful leaves.
        if err.validator == "anyOf" and err.context:
            leaves = [c for c in err.context if c.validator != "type" or c.instance is not None]
            err = (leaves or err.context)[0] if len(err.context) else err
        msgs.append(f"{_path(err)}: {err.message}")
    return msgs


def validate_intent(raw: str | dict, images: tuple[IntentImage, ...] = ()) -> tuple[dict | None, list[str]]:
    """Parse and validate one backend answer. Returns (intent, errors); intent is None when errors is non-empty.

    Out-of-range numbers are NOT errors here (they are clamped later); everything else in the schema is."""
    obj, errors = _loads(raw)
    if obj is None:
        return None, errors
    errors = _schema_errors(obj, load_schema())
    pat = obj.get("pattern") if isinstance(obj.get("pattern"), dict) else None
    if pat and not errors:
        pattern_images = [i.path for i in images if i.role == "pattern"]
        given = pat.get("image")
        if given is not None and given not in pattern_images and str(Path(given).expanduser().resolve()) not in pattern_images:
            errors.append("pattern.image: only the pattern image the user supplied may be used; leave it null")
        if pat.get("kind") == "image" and not pattern_images:
            errors.append("pattern.kind: 'image' requires a pattern image supplied by the user; none was given")
        for k in _NUMERIC_PARAMS:
            v = pat.get(k)
            if isinstance(v, float) and not math.isfinite(v):
                errors.append(f"pattern.{k}: not finite")
    return (obj if not errors else None), errors


# ---------------------------------------------------------------- normalisation


def _clamp(value, lo, hi):
    return min(max(value, lo), hi)


def normalise(obj: dict, images: tuple[IntentImage, ...] = (), config: dict | None = None) -> tuple[dict, list[Change]]:
    """Clamp, apply presets and defaults. Input must have passed :func:`validate_intent`."""
    cfg = config or load_config()
    limits, defaults, styles = cfg["limits"], cfg["defaults"], cfg["style"]
    changes: list[Change] = []
    out = copy.deepcopy(obj)

    def clamp_field(name: str, value, key: str):
        lo, hi = limits[key]
        if key == "seed":
            new = int(_clamp(value, lo, hi))
        else:
            new = float(_clamp(value, lo, hi))
        if new != value:
            side = "below min" if value < lo else "above max"
            changes.append(Change(name, value, new, f"{side} {lo if value < lo else hi} (design space [{lo}, {hi}])"))
        return new

    # instep ease
    if out["instep_ease_mm"] is not None:
        out["instep_ease_mm"] = clamp_field("instep_ease_mm", out["instep_ease_mm"], "instep_ease_mm")

    # pattern: image override, then preset / defaults
    pattern_image = next((i for i in images if i.role == "pattern"), None)
    raw = out["pattern"]
    first_preset = styles[out["style_tags"][0]] if out["style_tags"] else None
    if pattern_image is not None:
        if raw is None or raw["kind"] != "image":
            changes.append(
                Change("pattern.kind", None if raw is None else raw["kind"], "image", "a pattern image was supplied by the user")
            )
        raw = dict(raw or {})
        raw["kind"] = "image"
        raw["image"] = pattern_image.path
    elif raw is None:
        raw = dict(first_preset) if first_preset else {"kind": "none"}
    else:
        raw = dict(raw)
        if first_preset and first_preset["kind"] == raw["kind"]:
            for k, v in first_preset.items():
                if raw.get(k) is None:
                    raw[k] = v
    kind = raw["kind"]
    used = _KIND_PARAMS[kind]
    if kind == "image":
        use = raw.get("image_use") or defaults["image_use"]
        raw["image_use"] = use
        used = used | ({"open_ratio"} if use == "perforation" else {"depth_mm"})
    pat: dict = {"kind": kind}
    for k in ("scale_mm", "open_ratio", "depth_mm", "image", "image_use"):
        given = raw.get(k)
        if k not in used:
            if given is not None and obj["pattern"] is not None and obj["pattern"].get(k) is not None:
                changes.append(Change(f"pattern.{k}", given, None, f"not used by pattern kind {kind!r}"))
            continue
        if given is None:
            given = defaults.get(k)
        pat[k] = given
    for k in ("scale_mm", "open_ratio", "depth_mm"):
        if k in pat:
            pat[k] = clamp_field(f"pattern.{k}", pat[k], k)
    pat["zones"] = list(raw.get("zones") or defaults["zones"])
    seed = raw.get("seed")
    pat["seed"] = defaults["seed"] if seed is None else clamp_field("pattern.seed", seed, "seed")
    out["pattern"] = pat
    return out, changes


# ---------------------------------------------------------------- orchestration


def parse_intent(
    text: str,
    backend: IntentBackend,
    images=(),
    *,
    max_retries: int = 1,
    config: dict | None = None,
) -> IntentResult:
    """Text (+ optional images) -> :class:`IntentResult`. Never raises for bad model output; never invents values."""
    cfg = config or load_config()
    try:
        checked = tuple(check_images(images, cfg))
    except ImageInputError as e:
        return IntentResult(False, errors=[f"image input: {e}"])
    if not text.strip() and not checked:
        return IntentResult(False, errors=["empty request: give a text description or an image"])

    feedback = None
    errors: list[str] = []
    result = IntentResult(False)
    for attempt in range(1, max_retries + 2):
        result.attempts = attempt
        try:
            raw = backend.complete(IntentRequest(text, checked, feedback))
        except BackendError as e:
            result.errors = [f"backend: {e}"]
            return result
        obj, errors = validate_intent(raw, checked)
        if obj is not None:
            break
        feedback = "Your previous answer was rejected:\n" + "\n".join(f"- {e}" for e in errors)
    else:
        result.errors = errors
        return result

    intent, changes = normalise(obj, checked, cfg)
    # Safety net: unsupported mentions in the request text that the backend did not list.
    listed = intent["unsupported_requests"]
    for entry in cfg["unsupported"]:
        if any(re.search(p, text, re.IGNORECASE) for p in entry["patterns"]) and not any(_mentions(i, entry) for i in listed):
            listed.append(entry["label"])
            result.warnings.append(f"unsupported request not listed by the backend, added: {entry['label']}")
    final_errors = _schema_errors(intent, strict_schema())
    if final_errors:  # cannot happen unless config and schema drift apart
        result.errors = ["internal: normalised intent violates the strict schema: " + "; ".join(final_errors)]
        return result
    result.ok, result.intent, result.changes = True, intent, changes
    return result


# ---------------------------------------------------------------- rule-based backend (tests, offline)


class RuleBackend:
    """Keyword rules over the vocabulary in ``config/intent.toml``. Deterministic; for tests and offline use."""

    def __init__(self, config: dict | None = None):
        self.cfg = config or load_config()

    def _hits(self, text: str, table: dict) -> list[tuple[int, str]]:
        low = text.lower()
        hits = []
        for name, words in table.items():
            pos = [low.find(w.lower()) for w in words if w.lower() in low]
            if pos:
                hits.append((min(pos), name))
        return sorted(hits)

    def complete(self, request: IntentRequest) -> str:
        text, kw = request.text, self.cfg["keywords"]
        notes = []
        tmpl = {n for _, n in self._hits(text, kw["template"])}
        if tmpl == {"dlp_hollow", "dlp_solid"}:
            template = "dlp_solid"
            notes.append("both solid and hollow were mentioned; kept the default solid template")
        else:
            template = next(iter(tmpl), "dlp_solid")
        kinds = self._hits(text, kw["pattern"])
        pattern = None
        if kinds:
            pattern = {"kind": kinds[0][1]}
            for key, regex in (
                ("scale_mm", r"(?:孔径|scale|尺度|图案大小)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*mm"),
                ("depth_mm", r"(?:depth|深度)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*mm"),
            ):
                m = re.search(regex, text, re.IGNORECASE)
                if m:
                    pattern[key] = float(m.group(1))
            m = re.search(r"(?:open ratio|镂空率|开孔率)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*%", text, re.IGNORECASE)
            if m:
                pattern["open_ratio"] = float(m.group(1)) / 100
        tags = [n for _, n in self._hits(text, kw["style"])]
        instep = None
        m = re.search(r"(?:脚背松量|instep ease)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(?:mm|毫米)?", text, re.IGNORECASE)
        if m:
            instep = float(m.group(1))
        elif self._hits(text, {"loose": kw["instep"]["loose"]}):
            instep = self.cfg["limits"]["instep_ease_mm"][1]
        elif self._hits(text, {"tight": kw["instep"]["tight"]}):
            instep = self.cfg["limits"]["instep_ease_mm"][0]
        if any(i.role == "reference" for i in request.images):
            notes.append("a reference image was given but the rule backend cannot read images")
        return json.dumps(
            {
                "template": template,
                "pattern": pattern,
                "style_tags": tags,
                "instep_ease_mm": instep,
                "notes": "; ".join(notes),
                "unsupported_requests": detect_unsupported(text, self.cfg),
            }
        )


# ---------------------------------------------------------------- Claude backend


def _system_prompt(cfg: dict) -> str:
    lim = cfg["limits"]
    tags = ", ".join(cfg["style"])
    unsup = "; ".join(e["label"] for e in cfg["unsupported"])
    return f"""You translate a shoe request (any language) into a JSON object for a fixed 3D-printing pipeline.
You do not design anything; you only pick values the pipeline can realise. Output exactly one JSON object that matches the schema.

What the pipeline can change:
- template: "dlp_solid" or "dlp_hollow" (default dlp_solid; choose dlp_hollow only if the user wants an open / perforated / hollow look).
- pattern: kind none | hex_perforation | voronoi_perforation | wave_displacement | rib_displacement | image. Parameters
  scale_mm {lim['scale_mm']}, open_ratio {lim['open_ratio']} (perforation kinds), depth_mm {lim['depth_mm']} (displacement kinds),
  zones subset of side/top/toe/heel, seed integer. Give a parameter only if the user stated it; use null otherwise.
  If the user gave no pattern wish at all, set pattern to null.
- style_tags: only from [{tags}], only when the request clearly matches.
- instep_ease_mm {lim['instep_ease_mm']} or null.
- notes: free text for humans, never used by the pipeline.
- unsupported_requests: everything the user asked for that the pipeline cannot do ({unsup}, and anything else outside the list above),
  quoted briefly in the user's words. Never turn an unsupported request into some other field, and never drop it silently.

Pattern images: when an image is marked "pattern image", set pattern.kind to "image", pattern.image to null, and image_use to
perforation or displacement. Images marked "reference" are shoe photos: read style from them (style_tags, pattern kind); do not
describe colours as design values. Never invent numbers the user did not give. Do not add fields the schema lacks."""


class ClaudeBackend:
    """Claude API backend. Text and images go in one request; structured output enforces the schema shape.

    The API key is read by the SDK from the environment (ANTHROPIC_API_KEY or ``ant auth login``); it is never
    stored, printed or logged here. ``client`` is injectable so tests use a mock transport (no network)."""

    def __init__(self, client=None, *, model: str | None = None, config: dict | None = None):
        self.cfg = config or load_config()
        self.model = model or os.environ.get("SHOE_INTENT_MODEL") or self.cfg["claude"]["model"]
        if client is None:
            import anthropic  # optional dependency

            client = anthropic.Anthropic()
        self.client = client
        self._schema = api_schema()
        self._system = _system_prompt(self.cfg)

    def _content(self, request: IntentRequest) -> list[dict]:
        blocks: list[dict] = []
        for i, img in enumerate(request.images, 1):
            label = "pattern image" if img.role == "pattern" else "reference shoe image"
            blocks.append({"type": "text", "text": f"Image {i}: {label}"})
            blocks.append(
                {"type": "image", "source": {"type": "base64", "media_type": img.media_type, "data": img.read_b64()}}
            )
        text = request.text.strip() or "(no text; use the images)"
        if request.feedback:
            text += "\n\n" + request.feedback + "\nAnswer again with a corrected JSON object."
        blocks.append({"type": "text", "text": text})
        return blocks

    def complete(self, request: IntentRequest) -> str:
        c = self.cfg["claude"]
        try:
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=c["max_tokens"],
                system=self._system,
                output_config={"effort": c["effort"], "format": {"type": "json_schema", "schema": self._schema}},
                messages=[{"role": "user", "content": self._content(request)}],
            )
        except Exception as e:  # SDK errors: surface the class and message, never request headers
            raise BackendError(f"{type(e).__name__}: {e}") from e
        if resp.stop_reason == "refusal":
            raise BackendError("the model refused the request")
        if resp.stop_reason == "max_tokens":
            raise BackendError("output truncated (max_tokens)")
        text = next((b.text for b in resp.content if b.type == "text"), None)
        if text is None:
            raise BackendError("response contained no text block")
        return text
