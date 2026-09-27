"""Historical workbook bytes remain verifiable without invoking new guards."""
import hashlib
import copy
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "server"), str(ROOT / "skills/shortdrama-storyboard/scripts")]
from openpyxl import load_workbook
from shortdrama_mcp import frozen_projector_0555 as frozen
from shortdrama_mcp.file_io import extract_xlsx, storyboard_to_xlsx, CURRENT_PROJECTION_CONTRACT
from shortdrama_mcp.materializer import formal_mother_template_rows
from validate_storyboard import validate_project, validate_saved_projection


class FrozenProjectionV0556Tests(unittest.TestCase):
    def fixture(self):
        path = ROOT / "tests/fixtures/v0555_four_visible_original.xlsx"
        raw = path.read_bytes()
        provenance = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
        self.assertEqual(hashlib.sha256(raw).hexdigest(), provenance["sha256"])
        return raw

    def test_snapshot_is_original_git_blob_bytes(self):
        manifest = json.loads((frozen.SNAPSHOT_ROOT / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["source_commit"], frozen.SOURCE_COMMIT)
        for name, record in manifest["files"].items():
            raw = (frozen.SNAPSHOT_ROOT / name).read_bytes()
            with self.subTest(name=name):
                self.assertEqual(hashlib.sha256(raw).hexdigest(), record["sha256"])
                git_blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
                self.assertEqual(git_blob, record["git_blob"])

    def test_old_four_subject_bytes_import_but_new_generation_keeps_guard(self):
        raw = self.fixture()
        with patch("shortdrama_mcp.field_content.r5_keyframe_sections", side_effect=AssertionError("live projector")):
            for audit_only in (False, True):
                parsed = extract_xlsx(raw, audit_only=audit_only)
                canonical = parsed["canonical_storyboard"]
                self.assertIn("门房", canonical["shots"][0]["可见主体"])
                self.assertNotIn("audit_workbook_evidence", canonical)
        self.assertEqual(CURRENT_PROJECTION_CONTRACT, "shortdrama-projector/0.5.56")
        with self.assertRaisesRegex(ValueError, "keyframe_subject_capacity"):
            formal_mother_template_rows(canonical)
        with self.assertRaisesRegex(ValueError, "keyframe_subject_capacity"):
            storyboard_to_xlsx(canonical, {"status": "fail", "issues": []}, assume_compact=True)

    def test_changed_old_F_is_rejected_even_for_audit_only(self):
        raw = self.fixture()
        workbook = load_workbook(io.BytesIO(raw))
        workbook["正式分镜母版"]["F2"] = str(workbook["正式分镜母版"]["F2"].value) + "\n所有角色睁眼。"
        output = io.BytesIO()
        workbook.save(output)
        for audit_only in (False, True):
            with self.subTest(audit_only=audit_only), self.assertRaisesRegex(ValueError, "row 2.*col(?:umn)? 6|projection.*mismatch"):
                extract_xlsx(output.getvalue(), audit_only=audit_only)
        self.assertEqual(raw, self.fixture())

    def test_only_separate_verified_evidence_closes_historical_binding(self):
        parsed = extract_xlsx(self.fixture(), audit_only=True)
        canonical = parsed["canonical_storyboard"]
        evidence = parsed["audit_workbook_evidence"]
        original, original_evidence = copy.deepcopy(canonical), copy.deepcopy(evidence)
        checked = validate_project(canonical, saved_workbook_evidence=evidence)
        checks = {row["check_id"]: row for row in checked["quality_metrics"]["semantic_checks"]}
        self.assertEqual(checks["saved_projection_binding"]["status"], "pass")
        self.assertEqual(checks["saved_visible_capacity"]["status"], "fail")
        self.assertEqual(checked["quality_metrics"]["saved_workbook_evidence"], evidence)
        self.assertEqual(canonical, original)
        self.assertEqual(evidence, original_evidence)

        # Putting the same attestation in author-controlled JSON cannot grant
        # original-sidecar integrity, even if every flag is copied verbatim.
        forged = copy.deepcopy(canonical)
        forged["audit_workbook_evidence"] = copy.deepcopy(evidence)
        checks = {row["check_id"]: row for row in validate_project(forged)["quality_metrics"]["semantic_checks"]}
        self.assertEqual(checks["saved_projection_binding"]["status"], "unknown")
        fields = {name: cell["value"] for name, cell in evidence["rows"][0]["cells"].items()}
        with patch("validate_storyboard.project_gpt_keyframe", side_effect=AssertionError("new projector must not certify old cells")):
            checks = {row["check_id"]: row for row in validate_saved_projection(canonical["shots"][0], fields,
                projection_contract_verified=evidence["projection_contract"])}
        self.assertEqual(checks["saved_projection_binding"]["status"], "pass")
        self.assertEqual(checks["saved_visible_capacity"]["status"], "fail")


if __name__ == "__main__":
    unittest.main()
