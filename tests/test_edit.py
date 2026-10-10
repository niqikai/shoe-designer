"""M4 language, history and backend contracts, without Blender or source assets.

The fake backend tests orchestration and failure paths only. Actual geometry and
export verification are covered by the Blender integration tests and M4 demo.
"""
import base64
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine.params import normalize_params
from tools.apply_edit import main
from tools.design_store import DesignStore, atomic_write, changes, dump
from tools.edit_outputs import cache_previews, PRINT_FILES, PREVIEW_FILES
from tools.semantic import EditError, apply_sets, interpret, number


def baseline():
    return normalize_params({"revision": 4, "midsole_structure": "lattice", "boundary_rounding_mm": 1.2})[0]


class LanguageTests(unittest.TestCase):
    def interpret(self, text, params=None):
        return interpret(text, params or baseline(), ROOT / "docs/semantic_map.md")

    def test_initial_round_thick_skate_request_records_rebound_without_claims(self):
        result = self.interpret("设计一双圆头厚底板鞋，回弹好")
        self.assertEqual(result.params["toe_roundness"], .1)
        self.assertEqual(result.params["toe_height_scale"], 1.08)
        self.assertEqual(result.params["heel_sole_mm"], 32)
        self.assertEqual(result.params["forefoot_sole_mm"], 24)
        self.assertEqual(result.params["lattice_density_heel"], .32)
        self.assertTrue(any("试样" in note for note in result.notes))

    def test_relative_toe_and_heel_only_change_the_requested_region(self):
        result = self.interpret("鞋头再圆一点，脚跟再软一点")
        expected = {"toe_roundness": .03, "toe_height_scale": 1.02, "lattice_density_heel": .27}
        self.assertEqual({x["parameter"]: x["after"] for x in changes(baseline(), result.params)}, expected)

    def test_material_softness_does_not_also_change_lattice(self):
        result = self.interpret("换更软的 TPU")
        self.assertEqual([x["parameter"] for x in changes(baseline(), result.params)], ["tpu_shore_a"])
        self.assertEqual(result.params["tpu_shore_a"], 85)

    def test_lightness_maps_to_heel_and_forefoot_design_intent(self):
        result = self.interpret("更轻一点")
        self.assertEqual(result.params["lattice_density_heel"], .27)
        self.assertEqual(result.params["lattice_density_forefoot"], .3)
        self.assertEqual(result.params["lattice_density_arch"], .45)

    def test_joint_width_toe_clamp_is_reported(self):
        result = self.interpret("宽脚，圆头")
        self.assertEqual(result.params["toe_roundness"], .05)
        self.assertEqual(result.params["foot_width"], "wide")
        self.assertTrue(any("已截断" in note for note in result.clamps))

    def test_oversized_collar_is_clamped_to_the_selected_style(self):
        result = self.interpret("低帮，鞋口高度一百毫米")
        self.assertEqual(result.params["collar_height_mm"], 85)
        self.assertTrue(result.clamps)

    def test_chinese_numbers_percent_and_build_dimensions(self):
        result = self.interpret("四十二码，脚跟密度二十七%，前掌加厚三毫米，打印空间300×250×200mm")
        self.assertEqual(result.params["size_eu"], 42)
        self.assertEqual(result.params["lattice_density_heel"], .27)
        self.assertEqual(result.params["forefoot_sole_mm"], 19)
        self.assertEqual([result.params["build_size_" + axis + "_mm"] for axis in "xyz"], [300, 250, 200])
        self.assertEqual(number("零点三二"), .32)

    def test_explicit_shore_process_and_orientation(self):
        for phrase in ("TPU 85A", "邵氏 A 硬度八十五"):
            with self.subTest(phrase=phrase):
                result = self.interpret("用MJF打印，" + phrase + "，保持原方向")
                self.assertEqual(result.params["tpu_shore_a"], 85)
                self.assertEqual(result.params["print_process"], "MJF")
                self.assertEqual(result.params["build_orientation"], "as_designed")

    def test_order_of_absolute_and_relative_edits_is_preserved(self):
        self.assertEqual(self.interpret("圆头，然后鞋头再圆一点").params["toe_roundness"], .13)
        self.assertEqual(self.interpret("鞋头再圆一点，然后圆头").params["toe_roundness"], .1)

    def test_softness_on_solid_records_intent_without_enabling_lattice(self):
        params = baseline()
        params["midsole_structure"] = "solid"
        result = self.interpret("脚跟更软一点", params)
        self.assertEqual(result.params, params)
        self.assertTrue(any("实心中底" in note for note in result.notes))

    def test_structure_then_softness_uses_the_new_structure(self):
        result = self.interpret("实心中底，然后脚跟更软一点")
        self.assertEqual(result.params["lattice_density_heel"], .32)
        self.assertEqual(self.interpret("实心中底，然后Gyroid，脚跟更软一点").params["lattice_density_heel"], .27)

    def test_width_stops_at_edge_with_visible_note(self):
        result = self.interpret("宽脚，再宽一点")
        self.assertEqual(result.params["foot_width"], "wide")
        self.assertTrue(any("档位边界" in note for note in result.notes))

    def test_unknown_or_negated_clause_rejects_the_whole_request(self):
        current = baseline()
        for text in ("不要更圆一点", "鞋头更圆一点，变红", "更厚一点，导出3MF", "加外底花纹", ""):
            with self.subTest(text=text), self.assertRaises(EditError):
                self.interpret(text, current)
        self.assertEqual(current, baseline())

    def test_explicit_parameters_reject_metadata_unknown_types_and_nonfinite(self):
        for value in ("revision=10", "schema_version=1", "texture=stars", "heel_sole_mm=true", "heel_sole_mm=NaN", "foot_side=other"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                apply_sets(baseline(), [value])

    def test_explicit_parameters_clamp_and_respect_joint_constraints(self):
        result, clamps = apply_sets(baseline(), ["foot_width=wide", "toe_roundness=1", "upper_thickness_mm=1.8"])
        self.assertEqual(result["toe_roundness"], .05)
        self.assertEqual(result["boundary_rounding_mm"], .9)
        self.assertGreaterEqual(len(clamps), 2)


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "designs/history").mkdir(parents=True)
        (self.root / "docs").mkdir()
        shutil.copyfile(ROOT / "docs/semantic_map.md", self.root / "docs/semantic_map.md")
        self.store = DesignStore(self.root)
        self.original = (json.dumps(baseline(), ensure_ascii=False, separators=(", ", ": ")) + "\n\n").encode()
        self.store.current.write_bytes(self.original)
        self.store.changelog.write_text("# 历史\n", encoding="utf-8")
        self.calls = []
        self.output = self.root / "out/m3"

    def invoke(self, *arguments, runner=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([*arguments, "--json"], root=self.root, runner=runner or self.backend)
        return code, json.loads(stdout.getvalue())

    def backend(self, root, output, params):
        self.calls.append(dict(params))
        png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/S7sAAAAASUVORK5CYII=")
        for name in PREVIEW_FILES:
            path = output / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(png)
        exports = {}
        for key in ("stl", "glb", "print_stl", "print_glb"):
            extension = key.rsplit("_", 1)[-1]
            pose = "_print" if key.startswith("print_") else ""
            path = output / f"shoe_{params['foot_side']}{pose}.{extension}"
            path.write_bytes(b"test backend contract, not a geometry fixture")
            exports[key] = str(path)
            exports[key + "_coordinate_unit"] = "mm" if extension == "stl" else "m"
        report = {"status": "warning", "effective_params": dict(params), "voxel_mm": .8,
                  "original_sources_unchanged": True, "mesh_health": {"status": "pass"},
                  "manufacturing": {"status": "warning", "export_allowed": True, "blockers": [], "warnings": ["设备待验证"],
                                    "thickness": {"status": "pass"}, "powder_removal": {"status": "warning"},
                                    "build_volume": {"status": "warning"}, "overhang": {"status": "not_applicable"}},
                  "exports": exports, "previews": {"iso": str(output / "previews/shoe_iso.png")}}
        (output / "report.json").write_bytes(dump(report))
        return {"exit_code": 0, "report": report}

    def snapshot(self):
        return {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}

    def test_one_command_backs_up_exact_bytes_and_runs_normalized_parameters(self):
        code, report = self.invoke("鞋头再圆一点，脚跟再软一点")
        self.assertEqual(code, 0)
        self.assertEqual(report["revision"], 5)
        self.assertEqual((self.store.history / "v004.json").read_bytes(), self.original)
        self.assertEqual(self.calls, [report["effective_params"]])
        self.assertEqual(self.store.read_current()[1], report["effective_params"])
        self.assertEqual(self.store.read_operations()[0]["previous_revision"], 4)
        self.assertIn("鞋头再圆一点", self.store.changelog.read_text())
        self.assertTrue((self.output / "edit_report.md").is_file())

    def test_dry_run_never_creates_lock_history_outputs_or_calls_backend(self):
        original = self.snapshot()
        code, report = self.invoke("鞋口高度999毫米", "--dry-run")
        self.assertEqual(code, 0)
        self.assertEqual(report["effective_params"]["collar_height_mm"], 85)
        self.assertTrue(report["clamps"])
        self.assertEqual(self.snapshot(), original)
        self.assertEqual(self.calls, [])

    def test_no_parameter_change_rebuilds_without_new_history(self):
        code, report = self.invoke("重新生成")
        self.assertEqual(code, 0)
        self.assertFalse(report["saved"])
        self.assertEqual(report["revision"], 4)
        self.assertEqual(self.store.current.read_bytes(), self.original)
        self.assertEqual(list(self.store.history.iterdir()), [])
        self.assertEqual(len(self.calls), 1)

    def test_consecutive_undos_walk_edit_parents_instead_of_toggling(self):
        self.invoke("脚跟加厚三毫米")
        self.invoke("鞋头更圆一点")
        code, first = self.invoke("撤销")
        self.assertEqual((code, first["revision"], first["restored_from"]), (0, 7, 5))
        self.assertEqual(first["effective_params"]["heel_sole_mm"], 27)
        self.assertEqual(first["effective_params"]["toe_roundness"], 0)
        code, second = self.invoke("撤销上一次修改")
        self.assertEqual((code, second["revision"], second["restored_from"]), (0, 8, 4))
        self.assertEqual(changes(baseline(), second["effective_params"]), [])
        self.assertEqual(self.store.revisions(), [4, 5, 6, 7, 8])

    def test_restore_creates_new_version_and_undo_restores_previous_active_design(self):
        self.invoke("更厚一点")
        code, restored = self.invoke("回到第四版")
        self.assertEqual((code, restored["revision"], restored["restored_from"]), (0, 6, 4))
        self.assertEqual(changes(baseline(), restored["effective_params"]), [])
        code, undone = self.invoke("--undo")
        self.assertEqual((code, undone["revision"], undone["restored_from"]), (0, 7, 5))
        self.assertEqual(undone["effective_params"]["heel_sole_mm"], 27)

    def test_spaced_controls_and_chinese_compare_versions(self):
        self.invoke("更厚一点")
        self.assertEqual(self.invoke("请回到第 四 版", "--dry-run")[1]["restored_from"], 4)
        code, report = self.invoke("对比第 四 版和第 五 版", "--dry-run")
        self.assertEqual((code, report["left_revision"], report["right_revision"]), (0, 4, 5))

    def test_legacy_undo_normalizes_missing_defaults(self):
        (self.store.history / "v003.json").write_bytes(dump({"revision": 3, "collar_style": "low", "collar_height_mm": 75}))
        code, report = self.invoke("--undo")
        self.assertEqual((code, report["revision"], report["restored_from"]), (0, 5, 3))
        self.assertEqual(report["effective_params"]["midsole_structure"], "solid")

    def test_backup_collision_refuses_to_overwrite_history_or_current(self):
        backup = self.store.history / "v004.json"
        backup.write_bytes(b'{"revision": 4}')
        code, report = self.invoke("更厚一点")
        self.assertEqual(code, 1)
        self.assertIn("拒绝覆盖", report["summary"])
        self.assertEqual(self.store.current.read_bytes(), self.original)
        self.assertEqual(backup.read_bytes(), b'{"revision": 4}')
        self.assertEqual(self.calls, [])

    def test_unrecorded_current_mutation_is_not_backed_up_over_existing_ledger(self):
        self.invoke("更厚一点")
        mutated = self.store.read_current()[1]
        mutated["heel_sole_mm"] = 35
        raw = dump(mutated)
        self.store.current.write_bytes(raw)
        code, report = self.invoke("鞋头更圆一点")
        self.assertEqual(code, 1)
        self.assertIn("手工改写", report["summary"])
        self.assertEqual(self.store.current.read_bytes(), raw)
        self.assertFalse((self.store.history / "v005.json").exists())

    def test_metadata_write_failure_rolls_back_active_design_and_logs(self):
        original_log = self.store.changelog.read_bytes()
        failed = False
        def fail_once(path, raw):
            nonlocal failed
            if path.name == "operations.jsonl" and not failed:
                failed = True
                raise OSError("simulated storage failure")
            atomic_write(path, raw)
        with patch("tools.design_store.atomic_write", side_effect=fail_once):
            code, report = self.invoke("更厚一点")
        self.assertEqual(code, 1)
        self.assertEqual(self.store.current.read_bytes(), self.original)
        self.assertEqual(self.store.changelog.read_bytes(), original_log)
        self.assertFalse(self.store.operations.exists())
        self.assertEqual((self.store.history / "v004.json").read_bytes(), self.original)
        self.assertEqual(self.calls, [])

    def test_new_version_advances_past_all_existing_history(self):
        later = dict(baseline(), revision=10, heel_sole_mm=30)
        (self.store.history / "v010.json").write_bytes(dump(later))
        code, report = self.invoke("更厚一点")
        self.assertEqual((code, report["revision"]), (0, 11))

    def test_compare_creates_offline_preview_page_without_edit_or_generation(self):
        self.backend(self.root, self.output, baseline())
        self.calls.clear()
        self.invoke("更厚一点")
        old_raw = self.store.current.read_bytes()
        old_ledger = self.store.operations.read_bytes()
        calls = len(self.calls)
        code, report = self.invoke("对比上一版")
        self.assertEqual(code, 0)
        document = Path(report["comparison_html"]).read_text()
        self.assertEqual(document.count("data:image/png;base64,"), 2)
        self.assertIn("v004", document)
        self.assertIn("v005", document)
        self.assertIn("24.0 → 27.0", document)
        self.assertEqual(self.store.current.read_bytes(), old_raw)
        self.assertEqual(self.store.operations.read_bytes(), old_ledger)
        self.assertEqual(len(self.calls), calls)

    def test_compare_without_cached_previews_is_explicitly_parameters_only(self):
        (self.store.history / "v003.json").write_bytes(dump(dict(baseline(), revision=3, heel_sole_mm=20)))
        code, report = self.invoke("--compare", "3", "4")
        self.assertEqual(code, 0)
        self.assertIn("只比较参数", Path(report["comparison_html"]).read_text())
        self.assertEqual(self.calls, [])

    def test_incomplete_preview_cache_still_produces_parameter_comparison(self):
        self.backend(self.root, self.output, baseline())
        self.invoke("更厚一点")
        cache = self.root / "out/m4/versions/v004/report.json"
        cache.unlink()
        code, report = self.invoke("对比上一版")
        self.assertEqual(code, 0)
        self.assertIn("只比较参数", Path(report["comparison_html"]).read_text())
        for raw in (b"{partial", b"null", b'{"voxel_mm": null}'):
            with self.subTest(raw=raw):
                cache.write_bytes(raw)
                code, report = self.invoke("对比上一版")
                self.assertEqual(code, 0)
                self.assertIn("只比较参数", Path(report["comparison_html"]).read_text())

    def test_compare_dry_run_and_list_do_not_write_anything(self):
        (self.store.history / "v003.json").write_bytes(dump(dict(baseline(), revision=3, heel_sole_mm=20)))
        original = self.snapshot()
        self.assertEqual(self.invoke("--compare", "--dry-run")[0], 0)
        self.assertEqual(self.invoke("--list")[1]["revisions"], [3, 4])
        self.assertEqual(self.snapshot(), original)

    def test_unknown_request_does_not_partially_edit_or_call_backend(self):
        code, report = self.invoke("更厚一点，变红")
        self.assertEqual(code, 1)
        self.assertIn("变红", report["summary"])
        self.assertEqual(self.store.current.read_bytes(), self.original)
        self.assertEqual(list(self.store.history.iterdir()), [])
        self.assertEqual(self.calls, [])

    def test_patch_uses_typed_values_and_automatic_clamps(self):
        path = self.root / "patch.json"
        path.write_bytes(dump({"heel_sole_mm": 999, "foot_side": "left"}))
        code, report = self.invoke("--patch", str(path))
        self.assertEqual(code, 0)
        self.assertEqual(report["effective_params"]["heel_sole_mm"], 40)
        self.assertEqual(report["effective_params"]["foot_side"], "left")
        self.assertTrue(report["clamps"])
        self.assertTrue((self.output / "shoe_left_print.stl").is_file())

    def test_invalid_patch_or_control_combination_keeps_current(self):
        path = self.root / "patch.json"
        for values in ({"revision": 9}, [], {"heel_sole_mm": "27"}):
            with self.subTest(values=values):
                path.write_bytes(dump(values))
                self.assertEqual(self.invoke("--patch", str(path))[0], 1)
        self.assertEqual(self.invoke("撤销", "--set", "heel_sole_mm=27")[0], 1)
        self.assertEqual(self.invoke("--restore", "999")[0], 1)
        self.assertEqual(self.store.current.read_bytes(), self.original)
        self.assertEqual(self.calls, [])

    def test_manufacturing_failure_keeps_edit_undoable_and_removes_exports(self):
        def failing(root, output, params):
            result = self.backend(root, output, params)
            result["exit_code"] = 2
            result["report"]["status"] = "fail"
            result["report"]["manufacturing"].update(status="fail", export_allowed=False, blockers=["thin"])
            (output / "report.json").write_bytes(dump(result["report"]))
            return result
        code, report = self.invoke("更厚一点", runner=failing)
        self.assertEqual((code, report["revision"]), (2, 5))
        self.assertEqual(report["exports"], {})
        self.assertFalse(any((self.output / name).exists() for name in PRINT_FILES))
        self.assertTrue((self.output / "four_views.png").is_file())
        self.assertEqual(self.invoke("撤销")[0], 0)
        self.assertEqual(changes(baseline(), self.store.read_current()[1]), [])

    def test_invalid_successes_delete_prints_and_report_failure(self):
        mutations = {
            "missing_preview": lambda r, out: (out / "four_views.png").unlink(),
            "wrong_revision": lambda r, out: r["effective_params"].update(revision=999),
            "wrong_units": lambda r, out: r["exports"].update(stl_coordinate_unit="m"),
            "null_path": lambda r, out: r["exports"].update(stl=None),
            "numeric_path": lambda r, out: r["exports"].update(stl=42),
            "list_path": lambda r, out: r["exports"].update(stl=["shoe_right.stl"]),
            "source_missing": lambda r, out: r.pop("original_sources_unchanged"),
            "overall_fail": lambda r, out: r.update(status="fail"),
            "blocked": lambda r, out: r["manufacturing"].update(blockers=["thin"]),
            "missing_thickness": lambda r, out: r["manufacturing"].pop("thickness"),
            "bad_mesh": lambda r, out: r.update(mesh_health=None),
            "bad_manufacturing": lambda r, out: r.update(manufacturing=None),
            "bad_thickness": lambda r, out: r["manufacturing"].update(thickness=None),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                def broken(root, output, params):
                    result = self.backend(root, output, params)
                    mutate(result["report"], output)
                    return result
                code, report = self.invoke("重新生成", runner=broken)
                self.assertEqual(code, 1)
                self.assertEqual(report["exports"], {})
                self.assertFalse(any((self.output / item).exists() for item in PRINT_FILES))

    def test_engine_launch_or_report_parse_error_retains_edit_but_cleans_files(self):
        for error in (OSError("Blender unavailable"), ValueError("malformed report")):
            with self.subTest(error=error):
                def broken(root, output, params):
                    self.backend(root, output, params)
                    raise error
                code, report = self.invoke("更厚一点", runner=broken)
                self.assertEqual(code, 1)
                self.assertTrue(report["saved"])
                self.assertFalse(any((self.output / name).exists() for name in PRINT_FILES))

    def test_old_output_is_invalidated_before_backend_runs(self):
        self.backend(self.root, self.output, baseline())
        def inspect_old(root, output, params):
            self.assertFalse((output / "report.json").exists())
            self.assertFalse(any((output / name).exists() for name in PRINT_FILES + PREVIEW_FILES))
            return self.backend(root, output, params)
        self.assertEqual(self.invoke("更厚一点", runner=inspect_old)[0], 0)
        self.assertTrue((self.root / "out/m4/versions/v004/previews/shoe_iso.png").is_file())

    def test_external_design_change_during_generation_is_preserved_and_export_blocked(self):
        def changed(root, output, params):
            result = self.backend(root, output, params)
            self.store.current.write_bytes(dump(dict(params, heel_sole_mm=39)))
            return result
        code, report = self.invoke("更厚一点", runner=changed)
        self.assertEqual(code, 1)
        self.assertIn("外部修改", report["summary"])
        self.assertEqual(self.store.read_current()[1]["heel_sole_mm"], 39)
        self.assertFalse(any((self.output / name).exists() for name in PRINT_FILES))

    def test_invalid_or_reserved_output_paths_never_change_design(self):
        for path in ("assets/last/raw", "out", "out/../designs", "out/m4/versions/v004", "out/logs"):
            with self.subTest(path=path):
                code, _ = self.invoke("更厚一点", "--out", path)
                self.assertEqual(code, 1)
        self.assertEqual(self.store.current.read_bytes(), self.original)
        self.assertEqual(self.calls, [])

    def test_exclusive_lock_prevents_concurrent_edits(self):
        with self.store.locked():
            code, report = self.invoke("更厚一点")
        self.assertEqual(code, 1)
        self.assertIn("正在运行", report["summary"])
        self.assertEqual(self.store.current.read_bytes(), self.original)

    def test_malformed_historical_output_does_not_block_a_new_edit(self):
        self.output.mkdir(parents=True)
        (self.output / "report.json").write_bytes(b"{partial report")
        self.assertIsNone(cache_previews(self.root, self.output, baseline()))
        self.assertEqual(self.invoke("更厚一点")[0], 0)

    def test_backend_adaptive_warnings_reach_json_markdown_and_cli_without_duplicates(self):
        warnings = ["收口较强候选会切断材料连接，已回退到 0.25 强度。",
                    "附加表面平滑强度已从 0.5 回退到 0.125。",
                    "网格提取改用有界数值回退，场量不是物理位移或壁厚保证。"]

        def adaptive_backend(root, output, params):
            result = self.backend(root, output, params)
            result["report"]["clamp_messages"] = warnings + [warnings[0]]
            return result

        code, report = self.invoke("--set", "heel_sole_mm=999", runner=adaptive_backend)
        self.assertEqual(code, 0)
        self.assertTrue(any("已截断" in message for message in report["clamps"]))
        written_json = json.loads((self.output / "edit_report.json").read_bytes())
        markdown = (self.output / "edit_report.md").read_text()
        for message in warnings:
            self.assertEqual(report["clamps"].count(message), 1)
            self.assertEqual(written_json["clamps"].count(message), 1)
            self.assertEqual(markdown.count(message), 1)
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["重新生成"], root=self.root, runner=adaptive_backend)
        self.assertEqual(code, 0)
        for message in warnings:
            self.assertEqual(stdout.getvalue().count(message), 1)

    def test_optional_backend_warning_metadata_rejects_bad_types_without_inventing_messages(self):
        missing = object()
        valid = "已从同一材料场生成较弱收口，仍须制造校验。"
        values = (missing, None, 42, "非列表，不能作为逐条警告", {"warning": valid},
                  [None, 42, True, {}, [], "", "   ", valid])
        for value in values:
            with self.subTest(value=value):
                def backend_with_metadata(root, output, params):
                    result = self.backend(root, output, params)
                    if value is not missing:
                        result["report"]["clamp_messages"] = value
                    return result
                code, report = self.invoke("重新生成", runner=backend_with_metadata)
                self.assertEqual(code, 0)
                self.assertEqual(report["clamps"], [valid] if isinstance(value, list) else [])


if __name__ == "__main__":
    unittest.main()
