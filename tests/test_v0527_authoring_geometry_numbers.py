"""Synthetic record/CLI interoperability, never a manuscript quality fixture."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

TESTS = Path(__file__).resolve().parent
SCRIPTS = TESTS.parent / "skills/shortdrama-storyboard/scripts"
sys.path[:0] = [str(SCRIPTS), str(TESTS)]
import author_storyboard as author
from staging_text_bindings import bind_fields
from staging_geometry import GeometryInputError, _number
from test_v0527_authoring_serializer import decisions
from test_v0527_staging_text_bindings import bound_decisions


def decimal_decisions():
    """Reuse only the synthetic structural fixture, with decimal geometry."""
    data = bound_decisions()
    data["shots"][0]["staging"]["geometry"]["camera"] = [0, 4.5]
    return data


def raw_json(data):
    return (json.dumps(data, ensure_ascii=False) + "\n").encode("utf-8")


class AuthoringGeometryNumberTests(unittest.TestCase):
    def record(self, raw, accepted, *, cli=False):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = root / "synthetic.json"
            source.write_bytes(raw)
            if cli:
                proc = subprocess.run([sys.executable, "-X", "utf8", str(SCRIPTS / "author_storyboard.py"),
                                       "--input", str(source), "--attempts-root", str(root / "attempts"),
                                       "--attempt-id", "synthetic"], capture_output=True, text=True, encoding="utf-8")
                self.assertEqual(proc.returncode, 0 if accepted else 2, proc.stdout + proc.stderr)
                report = json.loads(proc.stdout)
            else:
                report = author.record_attempt(source, root / "attempts", attempt_id="synthetic")
            self.assertEqual(report["status"], "rendered_pending_preflight" if accepted else "rejected", report)
            attempt = Path(report["report_path"]).parent
            self.assertEqual((attempt / "input.original.json").read_bytes(), raw)
            self.assertEqual(source.read_bytes(), raw)
            self.assertEqual(report["input_sha256"], hashlib.sha256(raw).hexdigest())
            self.assertTrue(report["include_in_attempt_accounting"])
            self.assertFalse(report["commit_eligible"])
            self.assertIsNone(report["semantic_accuracy_percent"])
            canonical = json.loads((attempt / "canonical.json").read_text("utf-8")) if accepted else None
            diagnostic_path = attempt / "initial_staging_bindings.json"
            diagnostic = json.loads(diagnostic_path.read_text("utf-8")) if diagnostic_path.exists() else None
            if not accepted:
                self.assertFalse((attempt / "canonical.json").exists())
                self.assertIsNone(report["canonical_path"])
            return report, canonical, diagnostic

    def test_real_cli_decimal_coordinate_freezes_input_and_writes_matching_diagnostics(self):
        data = decimal_decisions(); raw = raw_json(data)
        report, canonical, diagnostic = self.record(raw, True, cli=True)
        expected_fields, expected = bind_fields(data["shots"][0]["fields"], data["shots"][0]["staging"])
        self.assertEqual(canonical["shots"][0]["机位"], expected_fields["机位"])
        self.assertEqual(diagnostic[0]["geometry"], expected["geometry"])
        self.assertEqual(canonical["prevention_evidence"]["repair_history"], [])
        self.assertNotIn("dialogue_adaptation_audit", canonical["prevention_evidence"])

    def test_decimal_negative_positions_targets_and_angles_round_trip_record(self):
        data = decimal_decisions(); g = data["shots"][0]["staging"]["geometry"]
        g["camera"] = [-1.25, 4.5]; g["camera_target"] = [0.25, -0.75]
        for a, x in zip(g["actors"], [-0.5, 1.75]):
            a.update(actor_position=[x, -0.25], body_yaw=180.5, head_yaw=90.25, gaze_target=[0.25, -1.5])
        _, _, diagnostic = self.record(raw_json(data), True)
        self.assertEqual(diagnostic[0]["geometry"], bind_fields(data["shots"][0]["fields"], data["shots"][0]["staging"])[1]["geometry"])

    def test_scientific_geometry_tokens_are_valid_in_record_and_cli(self):
        data = decimal_decisions(); g = data["shots"][0]["staging"]["geometry"]
        g["camera"][1] = "CAMERA_NUMBER"; g["actors"][0]["head_yaw"] = "YAW_NUMBER"
        raw = raw_json(data).replace(b'"CAMERA_NUMBER"', b'4.5e0').replace(b'"YAW_NUMBER"', b'9.025e1')
        self.record(raw, True)
        self.record(raw, True, cli=True)

    def test_geometry_rejects_boolean_string_null_nonfinite_and_overflow_raw_tokens(self):
        data = decimal_decisions(); data["shots"][0]["staging"]["geometry"]["camera"][1] = "NUMBER"
        for token in [b'true', b'false', b'"4.5"', b'null', b'NaN', b'Infinity', b'-Infinity',
                      b'1e309', b'-1e309', b'9' * 400]:
            with self.subTest(token=token[:24]):
                self.record(raw_json(data).replace(b'"NUMBER"', token), False)

    def test_geometry_rejects_out_of_range_angles_and_arithmetic_overflow(self):
        for angle in [-0.1, 360.0, True, "90.5", None]:
            with self.subTest(angle=angle):
                data = decimal_decisions(); data["shots"][0]["staging"]["geometry"]["actors"][0]["head_yaw"] = angle
                self.record(raw_json(data), False)
        data = decimal_decisions(); g = data["shots"][0]["staging"]["geometry"]
        g["camera"] = [-1.7e308, 0]; g["camera_target"] = [1.7e308, 0]
        self.record(raw_json(data), False)

    def test_cli_rejections_preserve_raw_boolean_and_overprecision_attempts(self):
        data = decimal_decisions(); data["shots"][0]["staging"]["geometry"]["camera"][1] = True
        self.record(raw_json(data), False, cli=True)
        raw = raw_json(decimal_decisions()).replace(
            b'"duration": 4.0', b'"duration": 4.0000000000000000000000000000000001')
        report, _, _ = self.record(raw, False, cli=True)
        self.assertIn("0.1-second", report["findings"][0]["message"])

    def test_exact_tenths_still_work_and_overprecision_tokens_are_not_rounded(self):
        for version in [decisions, decimal_decisions]:
            data = version(); data["shots"][0]["phases"][0]["end"] = 0.1
            data["shots"][0]["phases"][1]["start"] = 0.1
            self.record(raw_json(data), True)
            self.record(raw_json(data).replace(b': 0.1', b': 1e-1'), True)
            for old, new in [(b'"duration": 4.0', b'"duration": 4.0000000000000000000000000000000001'),
                             (b'"end": 0.1', b'"end": 0.1000000000000000000000000000000001'),
                             (b'"start": 0.7', b'"start": 0.7000000000000000000000000000000001')]:
                with self.subTest(contract=data["contract"], token=old):
                    raw = raw_json(data); self.assertIn(old, raw)
                    report, _, _ = self.record(raw.replace(old, new, 1), False)
                    self.assertIn("0.1-second", report["findings"][0]["message"])

    def test_v1_output_unchanged_and_has_no_staging_diagnostics(self):
        data = decisions(); original = copy.deepcopy(data)
        _, canonical, diagnostics = self.record(raw_json(data), True)
        self.assertEqual(canonical, author._render_decisions(original))
        self.assertIsNone(diagnostics)
        self.assertEqual(data, original)

    def test_staging_adapter_used_twice_after_raw_freeze_without_mutating_time_tokens(self):
        raw = raw_json(decimal_decisions())
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); path = root / "synthetic.json"; path.write_bytes(raw)
            renderer = author._render_decisions
            def observe(parsed):
                self.assertEqual((root / "attempts/synthetic/input.original.json").read_bytes(), raw)
                self.assertIs(type(parsed["shots"][0]["duration"]), author._JSONFloat)
                self.assertEqual(parsed["shots"][0]["duration"].literal, "4.0")
                self.assertIs(type(parsed["shots"][0]["staging"]["geometry"]["camera"][1]), author._JSONFloat)
                before = copy.deepcopy(parsed); output = renderer(parsed)
                self.assertEqual(parsed, before)
                self.assertIs(type(parsed["shots"][0]["staging"]["geometry"]["camera"][1]), author._JSONFloat)
                return output
            with mock.patch.object(author, "_render_decisions", side_effect=observe), mock.patch.object(author, "bind_fields", wraps=bind_fields) as calls:
                report = author.record_attempt(path, root / "attempts", attempt_id="synthetic")
            self.assertEqual(report["status"], "rendered_pending_preflight", report)
            # Independent preflight, canonical assembly, and persisted binding
            # diagnostics all use the same raw-frozen numeric adapter.
            self.assertEqual(calls.call_count, 3)
            for call in calls.call_args_list:
                self.assertIs(type(call.args[1]["geometry"]["camera"][1]), float)

    def test_external_geometry_subclass_policy_is_unchanged(self):
        class ExternalFloat(float):
            pass
        with self.assertRaises(GeometryInputError):
            _number(ExternalFloat(4.5), "coordinate")
        data = decimal_decisions(); data["shots"][0]["staging"]["geometry"]["camera"][1] = ExternalFloat(4.5)
        with self.assertRaises(author.RejectedDecisions):
            author._render_decisions(data)


if __name__ == "__main__":
    unittest.main()
