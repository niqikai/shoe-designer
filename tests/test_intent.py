"""Intent parsing (#16). No network anywhere: the Claude backend runs against a fake client."""

import json
import types

import pytest

from shoe_designer import intent as it  # noqa: E402

CFG = it.load_config()
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32


def base(**kw):
    d = {"template": "dlp_solid", "pattern": None, "style_tags": [], "instep_ease_mm": None, "notes": "", "unsupported_requests": []}
    d.update(kw)
    return d


class Scripted:
    """Backend returning queued answers; records the requests it got."""

    def __init__(self, *answers):
        self.answers, self.requests = list(answers), []

    def complete(self, request):
        self.requests.append(request)
        a = self.answers.pop(0)
        return a if isinstance(a, (str, dict)) else json.dumps(a)


def run(text="x", **kw):
    return it.parse_intent(text, it.RuleBackend(), **kw)


@pytest.fixture
def png(tmp_path):
    p = tmp_path / "pat.png"
    p.write_bytes(PNG)
    return str(p)


# ---------------------------------------------------------------- schema / config consistency


def test_schema_is_valid_strict_and_matches_config_limits():
    import jsonschema

    schema = it.load_schema()
    jsonschema.Draft202012Validator.check_schema(schema)
    assert schema["additionalProperties"] is False and schema["$defs"]["pattern"]["additionalProperties"] is False
    props = schema["$defs"]["pattern"]["properties"]
    clamp = lambda p: [b for b in p["anyOf"] if "x-clamp" in b][0]["x-clamp"]  # noqa: E731
    lim = CFG["limits"]
    for k in ("scale_mm", "open_ratio", "depth_mm", "seed"):
        assert clamp(props[k]) == lim[k]
    assert clamp(schema["properties"]["instep_ease_mm"]) == lim["instep_ease_mm"]
    assert set(schema["properties"]["style_tags"]["items"]["enum"]) == set(CFG["style"])
    for name, preset in CFG["style"].items():
        assert preset["kind"] in props["kind"]["enum"], name


def test_api_schema_drops_unsupported_keywords():
    s = json.dumps(it.api_schema())
    for bad in ("x-clamp", "maxLength", "uniqueItems", "minItems", "$schema"):
        assert f'"{bad}":' not in s
    assert "additionalProperties" in s


# ---------------------------------------------------------------- valid / rejected input


def test_valid_minimal_intent_passes_and_is_complete():
    r = it.parse_intent("x", Scripted(base()))
    assert r.ok and r.attempts == 1 and r.changes == []
    assert r.intent["template"] == "dlp_solid"
    assert r.intent["pattern"] == {"kind": "none", "zones": ["side", "top"], "seed": 0}


@pytest.mark.parametrize(
    "answer,needle",
    [
        (base(heel_height_mm=40), "heel_height_mm"),  # extra top-level field
        (base(pattern={"kind": "none", "color": "red"}), "color"),  # extra nested field
        (base(template="dlp_boot"), "template"),  # outside the enum
        (base(style_tags=["sporty", "gothic"]), "style_tags"),
        (base(pattern={"kind": "hex_perforation", "scale_mm": "big"}), "scale_mm"),  # wrong type
        (base(pattern={"kind": "hex_perforation", "seed": 1.5}), "seed"),
        (base(pattern={"kind": "hex_perforation", "zones": ["sole"]}), "zones"),
        (base(pattern={"kind": "hex_perforation", "zones": []}), "zones"),
        (base(style_tags="sporty"), "style_tags"),
        (base(notes=5), "notes"),
        ({k: v for k, v in base().items() if k != "template"}, "template"),  # missing required
        ("not json", "not valid JSON"),
        ("[1, 2]", "JSON object"),
        ('{"template": "dlp_solid", "pattern": {"kind": "none", "scale_mm": NaN}}', "non-finite"),
    ],
)
def test_invalid_output_is_rejected_with_clear_error(answer, needle):
    backend = Scripted(answer, answer)
    r = it.parse_intent("x", backend)
    assert not r.ok and r.intent is None
    assert any(needle in e for e in r.errors), r.errors


def test_bool_is_not_a_number():
    r = it.parse_intent("x", Scripted(*[base(instep_ease_mm=True)] * 2))
    assert not r.ok


# ---------------------------------------------------------------- clamp


def test_out_of_range_numbers_are_clamped_and_reported():
    ans = base(
        pattern={"kind": "wave_displacement", "scale_mm": 50, "depth_mm": -1, "seed": -3},
        instep_ease_mm=5,
    )
    r = it.parse_intent("x", Scripted(ans))
    assert r.ok
    p = r.intent["pattern"]
    assert (p["scale_mm"], p["depth_mm"], p["seed"], r.intent["instep_ease_mm"]) == (30.0, 0.0, 0, 3.0)
    ch = {c.field: c for c in r.changes}
    assert set(ch) == {"pattern.scale_mm", "pattern.depth_mm", "pattern.seed", "instep_ease_mm"}
    assert ch["pattern.scale_mm"].original == 50 and ch["pattern.scale_mm"].new == 30.0
    assert "above max" in ch["pattern.scale_mm"].reason and "below min" in ch["pattern.depth_mm"].reason


def test_in_range_values_are_untouched():
    r = it.parse_intent("x", Scripted(base(pattern={"kind": "hex_perforation", "scale_mm": 4, "open_ratio": 0.6}, instep_ease_mm=2.5)))
    assert r.ok and r.changes == []
    assert r.intent["pattern"]["scale_mm"] == 4 and r.intent["pattern"]["open_ratio"] == 0.6


def test_final_intent_satisfies_strict_schema():
    import jsonschema

    r = it.parse_intent("x", Scripted(base(pattern={"kind": "rib_displacement", "depth_mm": 99}, instep_ease_mm=0)))
    jsonschema.Draft202012Validator(it.strict_schema()).validate(r.intent)


def test_parameters_unused_by_kind_are_reported_not_silent():
    r = it.parse_intent("x", Scripted(base(pattern={"kind": "hex_perforation", "depth_mm": 1.0})))
    assert "depth_mm" not in r.intent["pattern"]
    assert [(c.field, c.original, c.new) for c in r.changes] == [("pattern.depth_mm", 1.0, None)]


# ---------------------------------------------------------------- style presets


@pytest.mark.parametrize("tag", sorted(CFG["style"]))
def test_every_style_tag_maps_to_its_preset(tag):
    r = it.parse_intent("x", Scripted(base(style_tags=[tag])))
    preset = CFG["style"][tag]
    p = r.intent["pattern"]
    assert p["kind"] == preset["kind"]
    for k, v in preset.items():
        assert p[k] == v
    assert r.intent["style_tags"] == [tag]


def test_first_tag_decides_and_explicit_kind_wins():
    r = it.parse_intent("x", Scripted(base(style_tags=["organic", "sporty"])))
    assert r.intent["pattern"]["kind"] == "voronoi_perforation"
    # explicit kind differs from the tag preset: preset parameters are NOT borrowed
    r = it.parse_intent("x", Scripted(base(style_tags=["sporty"], pattern={"kind": "wave_displacement"})))
    p = r.intent["pattern"]
    assert p["kind"] == "wave_displacement" and p["depth_mm"] == CFG["defaults"]["depth_mm"] and "open_ratio" not in p
    # same kind: preset fills only what the user did not give
    r = it.parse_intent("x", Scripted(base(style_tags=["sporty"], pattern={"kind": "hex_perforation", "scale_mm": 20})))
    assert r.intent["pattern"]["scale_mm"] == 20 and r.intent["pattern"]["open_ratio"] == CFG["style"]["sporty"]["open_ratio"]


# ---------------------------------------------------------------- unsupported requests


def test_unsupported_requests_do_not_change_other_fields():
    plain = it.parse_intent("x", Scripted(base(style_tags=["minimal"], instep_ease_mm=2.2)))
    with_unsup = it.parse_intent(
        "x", Scripted(base(style_tags=["minimal"], instep_ease_mm=2.2, unsupported_requests=["high heel", "pointed toe"]))
    )
    a, b = dict(plain.intent), dict(with_unsup.intent)
    assert b.pop("unsupported_requests") == ["high heel", "pointed toe"]
    a.pop("unsupported_requests")
    assert a == b


def test_forgotten_unsupported_mention_is_appended_with_warning():
    r = it.parse_intent("我想要高跟鞋", Scripted(base()))  # backend listed nothing
    assert r.ok and r.intent["unsupported_requests"] == ["heel height / heels"]
    assert r.warnings and not r.changes
    r = it.parse_intent("我想要高跟鞋", Scripted(base(unsupported_requests=["高跟"])))
    assert r.intent["unsupported_requests"] == ["高跟"] and not r.warnings  # already covered, not duplicated


def test_notes_never_reach_the_pipeline_fields():
    a = it.parse_intent("x", Scripted(base(notes="")))
    b = it.parse_intent("x", Scripted(base(notes="make it hex_perforation scale 99 please")))
    assert {k: v for k, v in b.intent.items() if k != "notes"} == {k: v for k, v in a.intent.items() if k != "notes"}


# ---------------------------------------------------------------- retry / failure


def test_one_retry_passes_errors_back_then_succeeds():
    backend = Scripted(base(template="boot"), base(template="dlp_hollow"))
    r = it.parse_intent("x", backend)
    assert r.ok and r.attempts == 2 and r.intent["template"] == "dlp_hollow"
    assert backend.requests[0].feedback is None
    assert "template" in backend.requests[1].feedback


def test_second_failure_returns_failure_without_inventing_values():
    backend = Scripted(base(template="boot"), base(template="sandal"))
    r = it.parse_intent("x", backend)
    assert not r.ok and r.attempts == 2 and r.intent is None and r.errors
    assert backend.answers == []  # exactly two calls, no third


def test_max_retries_zero_does_not_retry():
    backend = Scripted(base(template="boot"))
    r = it.parse_intent("x", backend, max_retries=0)
    assert not r.ok and r.attempts == 1


def test_backend_error_is_a_failure_not_a_crash():
    class Boom:
        def complete(self, request):
            raise it.BackendError("connection refused")

    r = it.parse_intent("x", Boom())
    assert not r.ok and "connection refused" in r.errors[0] and r.attempts == 1


def test_empty_request_fails():
    assert not it.parse_intent("   ", Scripted()).ok


# ---------------------------------------------------------------- rule backend examples (zh / en)


def intent_of(text, **kw):
    r = it.parse_intent(text, it.RuleBackend(), **kw)
    assert r.ok, r.errors
    return r


def test_example_wave_hollow_sporty():
    i = intent_of("波浪纹的镂空运动鞋").intent
    assert i["template"] == "dlp_hollow" and i["style_tags"] == ["sporty"]
    assert i["pattern"]["kind"] == "wave_displacement" and i["unsupported_requests"] == []


def test_example_minimal():
    i = intent_of("极简风格").intent
    assert i["template"] == "dlp_solid" and i["style_tags"] == ["minimal"] and i["pattern"]["kind"] == "none"


def test_example_high_heel_pointed_toe_is_all_unsupported():
    r = intent_of("我想要高跟尖头")
    i = r.intent
    assert set(i["unsupported_requests"]) == {"heel height / heels", "toe shape"}
    assert i["template"] == "dlp_solid" and i["pattern"]["kind"] == "none" and i["style_tags"] == []


def test_example_english_hex_perforated_sporty():
    i = intent_of("a sporty perforated sneaker with a honeycomb pattern").intent
    assert i["template"] == "dlp_hollow" and i["pattern"]["kind"] == "hex_perforation" and i["style_tags"] == ["sporty"]
    assert i["pattern"]["open_ratio"] == CFG["style"]["sporty"]["open_ratio"]


def test_example_organic_voronoi():
    i = intent_of("organic voronoi holes").intent
    assert i["pattern"]["kind"] == "voronoi_perforation" and i["style_tags"] == ["organic"]


def test_example_knit_look():
    i = intent_of("针织质感,休闲").intent
    assert i["style_tags"] == ["knit", "casual"] or i["style_tags"] == ["casual", "knit"]
    assert i["pattern"]["kind"] in ("rib_displacement", "wave_displacement")


def test_example_ribbed_solid():
    i = intent_of("solid shoe with ribbed texture, depth 3mm").intent
    assert i["template"] == "dlp_solid" and i["pattern"]["kind"] == "rib_displacement" and i["pattern"]["depth_mm"] == 2.0


def test_example_instep_numeric_clamped():
    r = intent_of("脚背松量5mm")
    assert r.intent["instep_ease_mm"] == 3.0 and r.changes[0].field == "instep_ease_mm" and r.changes[0].original == 5.0


def test_example_instep_loose_and_tight_words():
    assert intent_of("脚背宽松一点").intent["instep_ease_mm"] == 3.0
    assert intent_of("tight instep please").intent["instep_ease_mm"] == 2.0


def test_example_scale_too_big_clamped():
    r = intent_of("六边形镂空,孔径 50mm")
    assert r.intent["pattern"]["scale_mm"] == 30.0 and [c.field for c in r.changes] == ["pattern.scale_mm"]


def test_example_english_mixed_supported_and_unsupported():
    r = intent_of("minimal white boots with laces")
    assert set(r.intent["unsupported_requests"]) == {
        "colour",
        "laces / closures",
        "shoe type outside the two templates (boots, sandals ...)",
    }
    assert r.intent["style_tags"] == ["minimal"]


def test_example_conflicting_template_words_noted():
    i = intent_of("solid but also hollow").intent
    assert i["template"] == "dlp_solid" and "both" in i["notes"]


def test_example_no_pattern_words_means_none():
    assert intent_of("just a normal shoe").intent["pattern"]["kind"] == "none"


# ---------------------------------------------------------------- images


def test_image_checks(tmp_path, png):
    imgs = it.check_images([(png, "pattern")])
    assert imgs[0].media_type == "image/png"
    with pytest.raises(it.ImageInputError, match="does not exist"):
        it.check_images([(str(tmp_path / "nope.png"), "pattern")])
    gif = tmp_path / "a.gif"
    gif.write_bytes(b"GIF89a")
    with pytest.raises(it.ImageInputError, match="not accepted"):
        it.check_images([(str(gif), "reference")])
    fake = tmp_path / "fake.png"
    fake.write_bytes(b"not a png at all")
    with pytest.raises(it.ImageInputError, match="not a valid"):
        it.check_images([(str(fake), "reference")])
    empty = tmp_path / "e.png"
    empty.write_bytes(b"")
    with pytest.raises(it.ImageInputError, match="size"):
        it.check_images([(str(empty), "reference")])
    big = tmp_path / "big.png"
    big.write_bytes(PNG + b"\0" * CFG["images"]["max_bytes"])
    with pytest.raises(it.ImageInputError, match="size"):
        it.check_images([(str(big), "reference")])
    with pytest.raises(it.ImageInputError, match="role"):
        it.check_images([(png, "style")])
    with pytest.raises(it.ImageInputError, match="at most one pattern"):
        it.check_images([(png, "pattern"), (png, "pattern")])


def test_bad_image_fails_before_calling_the_backend(tmp_path):
    backend = Scripted(base())
    r = it.parse_intent("x", backend, images=[(str(tmp_path / "missing.png"), "pattern")])
    assert not r.ok and "image input" in r.errors[0] and backend.requests == []


def test_pattern_image_becomes_image_kind(png):
    r = it.parse_intent("x", Scripted(base(pattern={"kind": "image", "image_use": "displacement", "depth_mm": 1.2})), images=[(png, "pattern")])
    p = r.intent["pattern"]
    assert r.ok and p["kind"] == "image" and p["image"].endswith("pat.png") and p["image_use"] == "displacement"
    assert p["depth_mm"] == 1.2 and "open_ratio" not in p and r.changes == []


def test_pattern_image_overrides_other_kind_and_reports(png):
    r = it.parse_intent("x", Scripted(base(pattern={"kind": "hex_perforation"})), images=[(png, "pattern")])
    assert r.intent["pattern"]["kind"] == "image" and r.intent["pattern"]["image_use"] == "perforation"
    assert [(c.field, c.original, c.new) for c in r.changes] == [("pattern.kind", "hex_perforation", "image")]


def test_pattern_image_without_pattern_object(png):
    r = it.parse_intent("x", Scripted(base()), images=[(png, "pattern")])
    assert r.intent["pattern"]["kind"] == "image" and r.intent["pattern"]["open_ratio"] == CFG["defaults"]["open_ratio"]


def test_model_cannot_invent_image_path_or_kind_image_without_image(png):
    r = it.parse_intent("x", Scripted(*[base(pattern={"kind": "image", "image": "/etc/passwd"})] * 2), images=[(png, "pattern")])
    assert not r.ok and any("pattern.image" in e for e in r.errors)
    r = it.parse_intent("x", Scripted(*[base(pattern={"kind": "image"})] * 2))
    assert not r.ok and any("requires a pattern image" in e for e in r.errors)


def test_reference_image_only_leaves_pattern_to_style(png):
    r = it.parse_intent("x", Scripted(base(style_tags=["geometric"])), images=[(png, "reference")])
    assert r.ok and r.intent["pattern"]["kind"] == "hex_perforation"


# ---------------------------------------------------------------- Claude backend with a fake transport


class FakeClient:
    def __init__(self, *texts, stop_reason="end_turn", raises=None):
        self.texts, self.calls, self.stop_reason, self.raises = list(texts), [], stop_reason, raises
        self.messages = types.SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        if self.raises:
            raise self.raises
        block = types.SimpleNamespace(type="text", text=self.texts.pop(0))
        return types.SimpleNamespace(content=[block], stop_reason=self.stop_reason)


def test_claude_backend_request_shape_and_result(png):
    ref = png
    client = FakeClient(json.dumps(base(template="dlp_hollow", style_tags=["sporty"])))
    backend = it.ClaudeBackend(client)
    r = it.parse_intent("镂空运动鞋", backend, images=[(ref, "reference")])
    assert r.ok and r.intent["template"] == "dlp_hollow"
    call = client.calls[0]
    assert call["model"] == CFG["claude"]["model"]
    assert "tool_choice" not in call and "temperature" not in call and "thinking" not in call
    fmt = call["output_config"]["format"]
    assert fmt["type"] == "json_schema" and fmt["schema"] == it.api_schema()
    blocks = call["messages"][0]["content"]
    assert [b["type"] for b in blocks] == ["text", "image", "text"]
    assert blocks[1]["source"]["media_type"] == "image/png" and blocks[2]["text"] == "镂空运动鞋"
    assert "reference shoe image" in blocks[0]["text"]


def test_claude_backend_model_is_configurable(monkeypatch):
    assert it.ClaudeBackend(FakeClient(), model="m-x").model == "m-x"
    monkeypatch.setenv("SHOE_INTENT_MODEL", "from-env")
    assert it.ClaudeBackend(FakeClient()).model == "from-env"


def test_claude_backend_retry_sends_feedback():
    client = FakeClient(json.dumps(base(template="boot")), json.dumps(base()))
    r = it.parse_intent("x", it.ClaudeBackend(client))
    assert r.ok and r.attempts == 2
    assert "rejected" in client.calls[1]["messages"][0]["content"][-1]["text"]


@pytest.mark.parametrize("stop", ["refusal", "max_tokens"])
def test_claude_backend_refusal_and_truncation_fail_cleanly(stop):
    r = it.parse_intent("x", it.ClaudeBackend(FakeClient("{}", stop_reason=stop)))
    assert not r.ok and r.attempts == 1 and r.intent is None


def test_claude_backend_api_error_is_reported_without_secrets():
    r = it.parse_intent("x", it.ClaudeBackend(FakeClient(raises=RuntimeError("boom"))))
    assert not r.ok and "RuntimeError: boom" in r.errors[0]


def test_no_api_key_in_repo_sources():
    import pathlib
    import re

    root = pathlib.Path(it.__file__).resolve().parents[2]
    for f in [*root.glob("src/**/*.py"), *root.glob("scripts/*.py"), *root.glob("config/*.toml"), *root.glob("schemas/*.json")]:
        assert not re.search(r"sk-ant-[A-Za-z0-9_-]{8,}", f.read_text(encoding="utf-8")), f
