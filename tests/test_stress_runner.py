"""Driver evidence contracts, with synthetic records and no Blender process."""
from contextlib import redirect_stdout
import copy
import hashlib
import importlib.util
import io
import json
import math
from pathlib import Path
import sys
import tempfile
import threading
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import run_stress
from tools.stress_cases import make_manifest
from tools.stress_inputs import NORMALIZED_LAST, normalized_last_signature, valid_last_signature


def input_signature():
    return {"file": NORMALIZED_LAST, "bytes": 128, "sha256": "a" * 64}


def health():
    return {"status": "pass", "closed": True, "finite_coordinates": True,
            "connected_components": 1, "normal_orientation": "outward",
            "signed_volume_mm3": 1234.5,
            **{key: 0 for key in ("boundary_edges", "non_manifold_edges", "non_manifold_vertices",
                                 "degenerate_faces", "duplicate_vertices", "near_duplicate_vertices",
                                 "self_intersection_pairs", "inconsistent_winding_edges")}}


def result_record(case, status="pass", directory=None):
    record = {"case_id": case["case_id"], "fingerprint": case["fingerprint"],
              "requested": copy.deepcopy(case["requested"]),
              "effective_params": copy.deepcopy(case["effective"]), "status": status,
              "mesh_health": health(), "deformed_last_health": health(),
              "error": None if status == "pass" else "合成几何失败"}
    if directory is not None:
        record["batch_directory"] = str(directory)
    return record


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False) + "\n", encoding="utf-8")


def write_batch(directory, plan, records, *, driver_patch=None, report_patch=None):
    ids = [record["case_id"] for record in records]
    fail_count = sum(record["status"] == "fail" for record in records)
    code = 2 if fail_count else 0
    metadata = {"engine_sha256": plan["engine_sha256"], "plan_sha256": plan["plan_sha256"],
                "normalized_last": copy.deepcopy(plan["normalized_last"]),
                "case_ids": ids, "started_at": "start", "ended_at": "end", "exit_code": code,
                "driver_error": None}
    metadata.update(driver_patch or {})
    report = {"stage": "M5_geometry_batch", "status": "fail" if fail_count else "pass", "sources_unchanged": True,
              "designs_unchanged": True, "case_ids": ids, "requested_count": len(records), "completed_count": len(records),
              "pass_count": sum(record["status"] == "pass" for record in records),
              "fail_count": fail_count, "exit_code": code}
    report.update({key: copy.deepcopy(plan["normalized_last"]) for key in
                   ("normalized_last_expected", "normalized_last_before", "normalized_last_after")})
    report["normalized_last_unchanged"] = True
    report.update(report_patch or {})
    write_json(directory / "driver.json", metadata)
    write_json(directory / "report.json", report)
    (directory / "results.jsonl").write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records), encoding="utf-8")


class StressRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.addCleanup(self.temporary.cleanup)
        self.plan = make_manifest(count=3, seed=8, include_boundaries=False)
        self.plan["engine_sha256"] = "synthetic-engine-hash"
        self.plan["normalized_last"] = input_signature()
        self.cases = self.plan["cases"]
        self.batch = self.root / "batch"

    def verify(self, records, **kwargs):
        write_batch(self.batch, self.plan, records, **kwargs)
        return run_stress.verified_batch(self.batch, self.plan, self.cases)

    def test_valid_batch_preserves_records_and_checks_both_meshes(self):
        records = [result_record(case) for case in self.cases]
        accepted = self.verify(records)
        self.assertEqual(set(accepted), {case["case_id"] for case in self.cases})
        self.assertEqual(accepted[self.cases[0]["case_id"]]["batch_directory"], str(self.batch))
        for field in ("mesh_health", "deformed_last_health"):
            bad = result_record(self.cases[0])
            bad[field]["boundary_edges"] = 2
            self.assertEqual(self.verify([bad]), {})

    def test_parameters_id_and_fingerprint_must_match_the_selected_plan(self):
        base = result_record(self.cases[0])
        variants = [{**base, "case_id": "case-from-another-plan"},
                    {**base, "fingerprint": "another-fingerprint"},
                    {**base, "effective_params": {**base["effective_params"], "size_eu": 34}},
                    {**base, "effective_params": None}]
        for record in variants:
            with self.subTest(record=record):
                self.assertEqual(self.verify([record]), {})
        record = result_record(self.cases[2])
        write_batch(self.batch, self.plan, [record])
        self.assertEqual(run_stress.verified_batch(self.batch, self.plan, self.cases[:2]), {})

    def test_health_rejects_missing_each_gate_and_recorded_geometry_defects(self):
        base = health()
        self.assertTrue(run_stress.healthy(base))
        for key in base:
            missing = {name: value for name, value in base.items() if name != key}
            with self.subTest(missing=key):
                self.assertFalse(run_stress.healthy(missing))
        defects = {"closed": False, "finite_coordinates": False, "connected_components": 2,
                   "normal_orientation": "inward", "status": "warning"}
        defects.update({key: 1 for key in base if key.endswith("edges") or key.endswith("vertices")
                        or key in ("degenerate_faces", "self_intersection_pairs")})
        for key, value in defects.items():
            with self.subTest(gate=key):
                self.assertFalse(run_stress.healthy({**base, key: value}))
        for volume in (0, -1, math.nan, math.inf, -math.inf, True, "1234", None):
            with self.subTest(volume=volume):
                self.assertFalse(run_stress.healthy({**base, "signed_volume_mm3": volume}))
        self.assertFalse(run_stress.healthy(None))

    def test_source_and_design_provenance_and_both_plan_hashes_are_required(self):
        records = [result_record(self.cases[0])]
        for field in ("sources_unchanged", "designs_unchanged"):
            for value in (False, None, 1, "true"):
                with self.subTest(field=field, value=value):
                    self.assertEqual(self.verify(records, report_patch={field: value}), {})
        for field in ("engine_sha256", "plan_sha256"):
            with self.subTest(hash=field):
                self.assertEqual(self.verify(records, driver_patch={field: "old-run"}), {})
        for status in ("running", "error", None):
            self.assertEqual(self.verify(records, report_patch={"status": status}), {})

    def test_geometry_failures_are_available_for_summary_but_never_accepted_as_success(self):
        record = result_record(self.cases[0], "fail")
        record["mesh_health"] = None
        record["deformed_last_health"] = None
        accepted = self.verify([record], report_patch={"status": "fail", "exit_code": 2})
        self.assertEqual(accepted[self.cases[0]["case_id"]]["status"], "fail")
        for status in ("running", "error", "partial_pass", None):
            self.assertEqual(self.verify([{**record, "status": status}]), {})

    def test_read_lines_keeps_durable_records_when_last_write_is_interrupted(self):
        path = self.root / "interrupted.jsonl"
        valid = {"case_id": "durable-case", "status": "pass"}
        path.write_bytes((json.dumps(valid) + "\n42\n[]\n").encode() + b'{"case_id":"partial')
        self.assertEqual(run_stress.read_lines(path), [valid])
        path.write_bytes(b'\xff\xfe\n' + (json.dumps(valid) + "\n").encode())
        self.assertEqual(run_stress.read_lines(path), [valid])
        self.assertEqual(run_stress.read_lines(self.root / "missing.jsonl"), [])

    def test_missing_or_corrupt_batch_metadata_cannot_supply_results(self):
        self.assertEqual(run_stress.verified_batch(self.batch, self.plan, self.cases), {})
        records = [result_record(self.cases[0])]
        for filename, value in (("driver.json", []), ("report.json", []),
                                ("driver.json", None), ("report.json", "not-an-object")):
            write_batch(self.batch, self.plan, records)
            write_json(self.batch / filename, value)
            with self.subTest(filename=filename, value=value):
                self.assertEqual(run_stress.verified_batch(self.batch, self.plan, self.cases), {})
        (self.batch / "report.json").write_bytes(b'{"status":')
        self.assertEqual(run_stress.verified_batch(self.batch, self.plan, self.cases), {})

    def test_partial_and_running_summaries_cannot_claim_full_milestone(self):
        output = self.root / "summary"
        output.mkdir()
        selected = self.cases[:2]
        records = {case["case_id"]: result_record(case, directory=self.batch) for case in selected}
        partial = run_stress.write_summary(output, self.plan, selected, records, "start", running=False)
        self.assertEqual(partial["status"], "partial_pass")
        self.assertFalse(partial["full_plan"])
        self.assertEqual(partial["planned"]["random"], 3)
        self.assertEqual((partial["selected"], partial["completed"], partial["missing"]), (2, 2, 0))
        self.assertTrue(partial["geometry_only"])
        self.assertFalse(partial["manufacturing_certified"])
        running = run_stress.write_summary(output, self.plan, selected, records, "start", running=True)
        self.assertEqual(running["status"], "running")
        full_records = {case["case_id"]: result_record(case, directory=self.batch) for case in self.cases}
        full = run_stress.write_summary(output, self.plan, self.cases, full_records, "start", running=False)
        self.assertEqual(full["status"], "pass")
        self.assertTrue(full["full_plan"])

    def test_summary_counts_missing_failures_and_ordered_replay_records(self):
        output = self.root / "summary"
        output.mkdir()
        records = {self.cases[1]["case_id"]: result_record(self.cases[1], "fail", self.batch),
                   self.cases[0]["case_id"]: result_record(self.cases[0], directory=self.batch)}
        summary = run_stress.write_summary(output, self.plan, self.cases, records, "start", running=False)
        self.assertEqual(summary["status"], "fail")
        self.assertEqual(summary["counts"], {"pass": 1, "fail": 1, "error": 0})
        self.assertEqual(summary["missing"], 1)
        self.assertEqual(summary["random_pass"], 1)
        self.assertEqual(summary["boundary_pass"], 0)
        self.assertEqual(summary["failures"][0]["case_id"], self.cases[1]["case_id"])
        self.assertIn("合成几何失败", (output / "summary.md").read_text())
        self.assertEqual([record["case_id"] for record in run_stress.read_lines(output / "results.jsonl")],
                         [case["case_id"] for case in self.cases[:2]])
        records[self.cases[2]["case_id"]] = result_record(self.cases[2], "error", self.batch)
        summary = run_stress.write_summary(output, self.plan, self.cases, records, "start", running=False)
        self.assertEqual(summary["counts"], {"pass": 1, "fail": 1, "error": 1})
        self.assertEqual(summary["missing"], 0)
        self.assertEqual(len(summary["failures"]), 2)

    def invoke_main(self, argv, fake_batch):
        with patch.object(run_stress, "ROOT", self.root), \
                patch.object(run_stress, "make_manifest", return_value=copy.deepcopy(self.plan)), \
                patch.object(run_stress, "engine_fingerprint", return_value=self.plan["engine_sha256"]), \
                patch.object(run_stress, "normalized_last_signature", return_value=self.plan["normalized_last"]), \
                patch.object(run_stress, "find_blender", return_value="/synthetic/blender"), \
                patch.object(run_stress, "run_batch", side_effect=fake_batch), \
                redirect_stdout(io.StringIO()):
            return run_stress.main(argv)

    def run_directory(self):
        return (self.root / "out/m5-test/runs" /
                (self.plan["plan_sha256"][:12] + "-" + self.plan["engine_sha256"][:12]))

    def test_resume_reuses_only_valid_success_and_reruns_failure_or_changed_parameters(self):
        previous = self.run_directory() / "batches/0000"
        records = [result_record(self.cases[0]), result_record(self.cases[1], "fail"),
                   result_record(self.cases[2])]
        records[2]["effective_params"]["size_eu"] = 34
        write_batch(previous, self.plan, records, report_patch={"status": "fail", "exit_code": 2})
        launched = []

        def fake_batch(root, executable, manifest_path, plan, cases, directory, timeout, stop_event=None):
            launched.extend(case["case_id"] for case in cases)
            results = [result_record(case) for case in cases]
            write_batch(directory, plan, results)
            return {record["case_id"]: {**record, "batch_directory": str(directory)} for record in results}

        code = self.invoke_main(["--out", "out/m5-test", "--resume", "--jobs", "1"], fake_batch)
        self.assertEqual(code, 0)
        self.assertEqual(set(launched), {case["case_id"] for case in self.cases[1:]})
        summary = run_stress.read_json(self.run_directory() / "summary.json")
        self.assertEqual(summary["status"], "pass")
        merged = run_stress.read_lines(self.run_directory() / "results.jsonl")
        self.assertEqual(merged[0]["batch_directory"], str(previous))
        self.assertEqual(summary["counts"], {"pass": 3, "fail": 0, "error": 0})

    def test_replay_only_failed_case_is_explicitly_a_partial_run(self):
        previous = self.run_directory() / "batches/0000"
        write_batch(previous, self.plan, [result_record(self.cases[0], "fail")],
                    report_patch={"status": "fail", "exit_code": 2})
        launched = []

        def fake_batch(root, executable, manifest_path, plan, cases, directory, timeout, stop_event=None):
            launched.extend(case["case_id"] for case in cases)
            record = result_record(cases[0], directory=directory)
            return {record["case_id"]: record}

        code = self.invoke_main(["--out", "out/m5-test", "--resume", "--only", self.cases[0]["case_id"]], fake_batch)
        self.assertEqual(code, 0)
        self.assertEqual(launched, [self.cases[0]["case_id"]])
        summary = run_stress.read_json(self.run_directory() / "summary.json")
        self.assertEqual(summary["status"], "partial_pass")
        self.assertEqual(summary["selected"], 1)
        self.assertFalse(summary["full_plan"])

    def test_invalid_only_case_is_rejected_before_any_worker_launch(self):
        def must_not_run(*args, **kwargs):
            self.fail("参数错误不能启动工作进程")

        with self.assertRaisesRegex(ValueError, "不存在"):
            self.invoke_main(["--out", "out/m5-test", "--only", "unknown-case"], must_not_run)

    def test_boundary_first_run_is_partial_and_empty_selection_is_rejected(self):
        def must_not_run(*args, **kwargs):
            self.fail("空选择不能启动工作进程")
        with self.assertRaisesRegex(ValueError, "没有案例"):
            self.invoke_main(["--out", "out/m5-empty", "--kind", "boundary"], must_not_run)
        corner = copy.deepcopy(self.cases[0])
        corner.update(case_id="boundary-synthetic", kind="boundary")
        self.plan["cases"].append(corner)
        self.plan["counts"].update(boundary=1, total=4)
        launched = []
        def fake_batch(root, executable, manifest_path, plan, cases, directory, timeout, stop_event=None):
            launched.extend(case["case_id"] for case in cases)
            record = result_record(cases[0], directory=directory)
            return {record["case_id"]: record}
        self.assertEqual(self.invoke_main(["--out", "out/m5-test", "--kind", "boundary"], fake_batch), 0)
        self.assertEqual(launched, ["boundary-synthetic"])
        summary = run_stress.read_json(self.run_directory() / "summary.json")
        self.assertEqual(summary["status"], "partial_pass")
        self.assertEqual(summary["boundary_pass"], 1)
        self.assertEqual(summary["random_pass"], 0)

    def test_resume_rejects_driver_invalidated_evidence(self):
        self.assertEqual(self.verify([result_record(self.cases[0])],
                                     driver_patch={"driver_error": "运行期间引擎代码变化，本批证据作废。"}), {})

    def test_resume_requires_a_completed_driver_and_matching_case_provenance(self):
        records = [result_record(self.cases[0])]
        for kwargs in ({"driver_patch": {"case_ids": ["unrelated-case"]}},
                       {"report_patch": {"case_ids": ["unrelated-case"]}},
                       {"driver_patch": {"ended_at": None}},
                       {"driver_patch": {"exit_code": None}},
                       {"report_patch": {"completed_count": 0}}):
            self.assertEqual(self.verify(records, **kwargs), {})

    def test_mesh_count_booleans_cannot_stand_in_for_integer_counts(self):
        self.assertFalse(run_stress.healthy({**health(), "connected_components": True}))
        for key in health():
            if key.endswith("edges") or key.endswith("vertices") or key in ("degenerate_faces", "self_intersection_pairs"):
                self.assertFalse(run_stress.healthy({**health(), key: False}))

    def test_verified_batch_rejects_inconsistent_counts_codes_duplicate_or_missing_records(self):
        records = [result_record(case) for case in self.cases]
        for field in ("requested_count", "completed_count", "pass_count", "fail_count", "exit_code"):
            for value in (True, None, 2.5, "0", -1):
                with self.subTest(field=field, value=value):
                    self.assertEqual(self.verify(records, report_patch={field: value}), {})
        for code in (True, None, 1, 2, 9):
            self.assertEqual(self.verify(records, driver_patch={"exit_code": code}), {})
        self.assertEqual(self.verify(records, report_patch={"status": "fail", "exit_code": 2}), {})
        write_batch(self.batch, self.plan, records)
        (self.batch / "results.jsonl").write_text(json.dumps(records[0]) + "\n")
        self.assertEqual(run_stress.verified_batch(self.batch, self.plan, self.cases), {})
        duplicates = [records[0], records[0], records[2]]
        self.assertEqual(self.verify(duplicates), {})
        write_batch(self.batch, self.plan, records)
        (self.batch / "results.jsonl").write_text(
            "".join(json.dumps(record) + "\n" for record in reversed(records)))
        self.assertEqual(run_stress.verified_batch(self.batch, self.plan, self.cases), {})

    def test_blender_fail_exit_mapping_retains_healthy_successes_in_the_same_valid_batch(self):
        records = [result_record(self.cases[0], "fail"), result_record(self.cases[1])]
        for driver_code in (1, 2):
            with self.subTest(driver_code=driver_code):
                accepted = self.verify(records, driver_patch={"exit_code": driver_code})
                self.assertEqual(accepted[self.cases[0]["case_id"]]["status"], "fail")
                self.assertEqual(accepted[self.cases[1]["case_id"]]["status"], "pass")
        self.assertEqual(self.verify(records, driver_patch={"exit_code": 0}), {})

    def test_one_parameter_mismatch_does_not_discard_other_healthy_records(self):
        records = [result_record(case) for case in self.cases]
        records[1]["effective_params"]["size_eu"] = 34
        accepted = self.verify(records)
        self.assertEqual(set(accepted), {self.cases[0]["case_id"], self.cases[2]["case_id"]})

    def test_actual_normalized_input_digest_binds_runtime_without_loading_geometry(self):
        path = self.root / NORMALIZED_LAST
        path.parent.mkdir(parents=True)
        raw = b"synthetic normalized last, never a Blender mesh"
        path.write_bytes(raw)
        for name in ("schema/shoe_params.schema.json", "requirements-geometry.txt", "tools/stress_cases.py",
                     "tools/stress_inputs.py", "tools/run_stress.py", "tests/blender_stress_worker.py"):
            source = self.root / name
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(b"synthetic source")
        signature = normalized_last_signature(self.root)
        self.assertEqual(signature, {"file": NORMALIZED_LAST, "bytes": len(raw),
                                     "sha256": hashlib.sha256(raw).hexdigest()})
        old_fingerprint = run_stress.engine_fingerprint(self.root)
        self.assertEqual(path.read_bytes(), raw)
        path.write_bytes(raw + b" modified")
        self.assertNotEqual(normalized_last_signature(self.root), signature)
        self.assertNotEqual(run_stress.engine_fingerprint(self.root), old_fingerprint)

    def test_input_signature_rejects_missing_or_noncanonical_byte_proof(self):
        self.assertTrue(valid_last_signature(input_signature()))
        for value in (None, {}, [], {**input_signature(), "bytes": True}, {**input_signature(), "bytes": 0},
                      {**input_signature(), "sha256": "not-a-hash"}, {**input_signature(), "file": "other.blend"}):
            with self.subTest(value=value):
                self.assertFalse(valid_last_signature(value))

    def test_resume_requires_expected_and_unchanged_actual_normalized_input(self):
        records = [result_record(self.cases[0])]
        changed = {**input_signature(), "sha256": "b" * 64}
        self.assertEqual(self.verify(records, driver_patch={"normalized_last": changed}), {})
        for field in ("normalized_last_expected", "normalized_last_before", "normalized_last_after"):
            for value in (None, changed):
                with self.subTest(field=field, value=value):
                    self.assertEqual(self.verify(records, report_patch={field: value}), {})
        for value in (False, None, 1, "true"):
            self.assertEqual(self.verify(records, report_patch={"normalized_last_unchanged": value}), {})
        write_batch(self.batch, self.plan, records)
        old_plan = {key: value for key, value in self.plan.items() if key != "normalized_last"}
        self.assertEqual(run_stress.verified_batch(self.batch, old_plan, self.cases), {})

    def test_stop_event_terminates_worker_and_invalidates_even_its_completed_records(self):
        stop_event = threading.Event()
        calls = []

        class SimulatedProcess:
            returncode = None

            def poll(self):
                return self.returncode

            def terminate(self):
                calls.append("terminate")

            def wait(self, timeout=None):
                if timeout is not None:
                    raise run_stress.subprocess.TimeoutExpired("synthetic worker", timeout)
                return self.returncode

            def kill(self):
                calls.append("kill")
                self.returncode = -9

        def launch(*args, **kwargs):
            # Even an apparently complete response cannot survive an
            # interrupted driver, which lacks a normal worker exit.
            write_batch(self.batch, self.plan, [result_record(case) for case in self.cases])
            return SimulatedProcess()

        with patch.object(run_stress, "engine_fingerprint", return_value=self.plan["engine_sha256"]), \
                patch.object(run_stress.subprocess, "Popen", side_effect=launch), \
                patch.object(run_stress.time, "sleep", side_effect=lambda _: stop_event.set()):
            records = run_stress.run_batch(self.root, "/synthetic/blender", self.root / "manifest.json",
                                          self.plan, self.cases, self.batch, 300, stop_event)
        self.assertEqual(calls, ["terminate", "kill"])
        self.assertTrue(all(record["status"] == "error" for record in records.values()))
        metadata = run_stress.read_json(self.batch / "driver.json")
        self.assertIn("用户中断", metadata["driver_error"])
        self.assertEqual(metadata["exit_code"], -9)
        self.assertEqual(run_stress.verified_batch(self.batch, self.plan, self.cases), {})

    def test_ctrl_c_cancels_queued_batches_and_returns_interrupted_summary(self):
        started = threading.Event()
        launched = []

        def active_batch(root, executable, manifest_path, plan, cases, directory, timeout, stop_event=None):
            launched.append(cases[0]["case_id"])
            started.set()
            self.assertTrue(stop_event.wait(timeout=2), "driver 未通知活动工作进程停止")
            record = result_record(cases[0], "error", directory)
            record["error"] = "用户中断，批次未完成"
            return {record["case_id"]: record}

        def interrupt(futures):
            self.assertTrue(started.wait(timeout=2))
            raise KeyboardInterrupt()

        with patch.object(run_stress, "as_completed", side_effect=interrupt):
            code = self.invoke_main(["--out", "out/m5-test", "--jobs", "1", "--batch-size", "1"], active_batch)
        self.assertEqual(code, 130)
        self.assertEqual(launched, [self.cases[0]["case_id"]])
        summary = run_stress.read_json(self.run_directory() / "summary.json")
        self.assertEqual(summary["status"], "interrupted")
        self.assertTrue(summary["interrupted"])
        self.assertEqual(summary["counts"], {"pass": 0, "fail": 0, "error": 1})
        self.assertEqual((summary["completed"], summary["missing"]), (1, 2))

    def test_unreadable_runtime_input_is_a_durable_batch_error_without_launch(self):
        with patch.object(run_stress, "engine_fingerprint", side_effect=FileNotFoundError("normalized input missing")), \
                patch.object(run_stress.subprocess, "Popen") as launch:
            records = run_stress.run_batch(self.root, "/synthetic/blender", self.root / "manifest.json",
                                          self.plan, self.cases, self.batch, 300)
        launch.assert_not_called()
        self.assertTrue(all(record["status"] == "error" for record in records.values()))
        metadata = run_stress.read_json(self.batch / "driver.json")
        self.assertTrue(metadata["ended_at"])
        self.assertIn("摘要", metadata["driver_error"])
        self.assertEqual(run_stress.verified_batch(self.batch, self.plan, self.cases), {})

    def test_worker_checks_expected_input_before_loading_and_actual_bytes_after_cases(self):
        fake_bpy = ModuleType("bpy")
        fake_bpy.app = SimpleNamespace(version_string="synthetic Blender; no geometry")
        fake_build, fake_last = ModuleType("engine.build"), ModuleType("engine.shoe.last")
        fake_build.build_shoe = Mock()
        fake_last.load_last = Mock(return_value=object())
        fake_last.source_manifest = Mock(return_value={"synthetic_raw": True})
        specification = importlib.util.spec_from_file_location("synthetic_stress_worker", ROOT / "tests/blender_stress_worker.py")
        worker = importlib.util.module_from_spec(specification)
        with patch.dict(sys.modules, {"bpy": fake_bpy, "engine.build": fake_build, "engine.shoe.last": fake_last}):
            specification.loader.exec_module(worker)
        worker.ROOT = self.root
        worker.data_snapshot = lambda: {}
        path = self.root / NORMALIZED_LAST
        path.parent.mkdir(parents=True)
        path.write_bytes(b"synthetic actual normalized input")
        expected = normalized_last_signature(self.root)
        plan_path = self.root / "manifest.json"
        write_json(plan_path, self.plan)  # Intentionally differs from actual input.
        ids = ",".join(case["case_id"] for case in self.cases)
        with redirect_stdout(io.StringIO()):
            code = worker.run(plan_path, ids, self.root / "out/before-mismatch")
        self.assertEqual(code, 1)
        fake_last.load_last.assert_not_called()
        report = run_stress.read_json(self.root / "out/before-mismatch/report.json")
        self.assertEqual(report["normalized_last_before"], expected)
        self.assertFalse(report["normalized_last_unchanged"])

        def change_input(*args):
            path.write_bytes(b"different actual normalized input")
            return "pass"

        worker.run_case = change_input
        write_json(plan_path, {**self.plan, "normalized_last": expected})
        with redirect_stdout(io.StringIO()):
            code = worker.run(plan_path, ids, self.root / "out/after-mismatch")
        self.assertEqual(code, 1)
        fake_last.load_last.assert_called_once()
        report = run_stress.read_json(self.root / "out/after-mismatch/report.json")
        self.assertEqual(report["status"], "error")
        self.assertEqual(report["normalized_last_expected"], expected)
        self.assertEqual(report["normalized_last_before"], expected)
        self.assertEqual(report["normalized_last_after"], normalized_last_signature(self.root))
        self.assertFalse(report["normalized_last_unchanged"])
        self.assertEqual(report["completed_count"], len(self.cases))
        fake_build.build_shoe.assert_not_called()

    def test_worker_case_ledger_keeps_actual_finishing_and_extraction_evidence(self):
        fake_bpy = ModuleType("bpy")
        fake_bpy.app = SimpleNamespace(version_string="synthetic Blender; no geometry")
        fake_build, fake_last = ModuleType("engine.build"), ModuleType("engine.shoe.last")
        fake_build.build_shoe = Mock()
        fake_last.load_last = Mock()
        fake_last.source_manifest = Mock()
        specification = importlib.util.spec_from_file_location("synthetic_ledger_worker", ROOT / "tests/blender_stress_worker.py")
        worker = importlib.util.module_from_spec(specification)
        with patch.dict(sys.modules, {"bpy": fake_bpy, "engine.build": fake_build, "engine.shoe.last": fake_last}):
            specification.loader.exec_module(worker)
        worker.clean_generated_data = Mock()
        case = self.cases[0]
        finish = {"enabled": True, "applied_strength": .25, "protected_field_unchanged": True,
                  "surface_smoothing": {"factor": .125, "iterations": 6,
                                        "max_surface_displacement_mm": .34}}
        extraction = {"method": "synthetic bounded extraction", "fallback_used": True,
                      "attempts": [{"level": 0.0, "status": "fail"}, {"level": .0001, "status": "pass"}]}
        fake_build.build_shoe.return_value = SimpleNamespace(report={
            "effective_params": case["effective"], "mesh_health": health(), "deformed_last_health": health(),
            "voxel_mm": .5, "grid_shape": [10, 12, 14], "geometry_elapsed_seconds": .2,
            "clamp_messages": [], "edge_finish": finish, "mesh_extraction": extraction})
        output = self.root / "out/ledger"
        output.mkdir(parents=True)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(worker.run_case(case, object(), {}, output), "pass")
        recorded = run_stress.read_lines(output / "results.jsonl")[0]
        self.assertEqual(recorded["edge_finish"], finish)
        self.assertEqual(recorded["mesh_extraction"], extraction)
        # Metadata absence before a build returns is explicit, never invented.
        fake_build.build_shoe.side_effect = ValueError("synthetic early geometry failure")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(worker.run_case(case, object(), {}, output), "fail")
        failed = run_stress.read_lines(output / "results.jsonl")[-1]
        self.assertIsNone(failed["edge_finish"])
        self.assertIsNone(failed["mesh_extraction"])
        self.assertIn("early geometry failure", failed["error"])


if __name__ == "__main__":
    unittest.main()
