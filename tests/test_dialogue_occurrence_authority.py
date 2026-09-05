"""Source parser and service-bound occurrence identity regression tests."""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

from shortdrama_mcp.source_dialogue import (
    authority_claims_match_exactly,
    candidate_protected_dialogue_claims,
    parse_source_dialogue_authority,
)

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "shortdrama-storyboard" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
from dialogue_occurrences import align_storyboard_dialogue  # noqa: E402


class SourceOccurrenceIdentityTests(unittest.TestCase):
    def source(self, text="江氏：好。\n江氏：好。"):
        return parse_source_dialogue_authority(text, source_role="script")

    def test_parser_identity_is_stable_and_distinguishes_repetition(self):
        first = self.source()
        self.assertEqual(first, self.source())
        self.assertEqual([r.source_ordinal for r in first], [0, 1])
        self.assertNotEqual(first[0].occurrence_id, first[1].occurrence_id)
        self.assertEqual(first[0].source_text_sha256, first[1].source_text_sha256)
        self.assertEqual(first[0].as_dict()["occurrence_id"], first[0].occurrence_id)

    def test_source_revision_changes_identity(self):
        first = self.source()[0]
        second = self.source("江氏：好。\n江氏：走。")[0]
        self.assertNotEqual(first.occurrence_id, second.occurrence_id)

    def test_parser_normalized_line_endings_have_same_identity(self):
        self.assertEqual(self.source(), self.source("江氏：好。\r\n江氏：好。"))

    def test_authority_inventory_cannot_be_split_or_invented(self):
        rows = self.source("江氏：大嫂，没来晚吧？")
        self.assertFalse(authority_claims_match_exactly(rows, [("江氏", "大嫂，"), ("江氏", "没来晚吧？")]))
        self.assertTrue(authority_claims_match_exactly(rows, [("江氏", "大嫂，没来晚吧？")]))

    def test_authoritative_binding_accepts_legal_cross_shot_split(self):
        rows = self.source("江氏：大嫂，没来晚吧？")
        claim = {"speaker": rows[0].speaker, "text": rows[0].text,
                 "occurrence_id": rows[0].occurrence_id}
        storyboard = {"protected_dialogue": [claim]}
        shots = [{"镜头编号": "EP01-001", "画面内容": ("江氏", "大嫂，")},
                 {"镜头编号": "EP01-002", "画面内容": ("江氏", "没来晚吧？")}]
        result = align_storyboard_dialogue(storyboard, shots, lambda text: [text],
                                          authoritative_occurrences=[r.as_dict() for r in rows])
        self.assertEqual(result["status"], "pass", result)
        self.assertTrue(all(r["occurrence_id"] == rows[0].occurrence_id for r in result["fragments"]))
        self.assertTrue(authority_claims_match_exactly(rows, candidate_protected_dialogue_claims(storyboard)))

    def test_authoritative_binding_rejects_forged_candidate_id(self):
        rows = self.source("江氏：好。")
        storyboard = {"protected_dialogue": [{"speaker": "江氏", "text": "好。", "occurrence_id": "forged"}]}
        result = align_storyboard_dialogue(storyboard, [{"镜头编号": "EP01-001", "画面内容": ("江氏", "好。")}],
                                          lambda text: [text], authoritative_occurrences=[r.as_dict() for r in rows])
        self.assertEqual(result["status"], "fail_current")
        self.assertIn("authority_occurrence_identity_mismatch", {r["code"] for r in result["issues"]})

    def test_authoritative_binding_rejects_forged_fragment_id(self):
        rows = self.source("江氏：好。")
        storyboard = {"protected_dialogue": [{"speaker": "江氏", "text": "好。"}],
                      "prevention_evidence": {"protected_dialogue_fragments": [
                          {"occurrence_id": "forged", "shot_id": "EP01-001", "dialogue_index": 0, "char_range": [0, 2]},
                      ]}}
        result = align_storyboard_dialogue(storyboard, [{"镜头编号": "EP01-001", "画面内容": ("江氏", "好。")}],
                                          lambda text: [text], authoritative_occurrences=[r.as_dict() for r in rows])
        self.assertEqual(result["status"], "fail_current")
        self.assertIn("unknown_occurrence", {r["code"] for r in result["issues"]})

    def test_authoritative_binding_rejects_inventory_repetition_loss(self):
        rows = self.source()
        storyboard = {"protected_dialogue": [{"speaker": "江氏", "text": "好。"}]}
        result = align_storyboard_dialogue(storyboard, [{"镜头编号": "EP01-001", "画面内容": ("江氏", "好。")}],
                                          lambda text: [text], authoritative_occurrences=[r.as_dict() for r in rows])
        self.assertEqual(result["status"], "fail_current")
        self.assertIn("authority_inventory_mismatch", {r["code"] for r in result["issues"]})


class SourceOccurrenceManifestIntegrationTests(unittest.TestCase):
    """Exercise the real service manifest without issuing production output."""

    def setUp(self):
        # Reuse the established isolated storage/import fixture, not its
        # tests or its costly per-project full-runtime certificate operation.
        import test_v056_source_dialogue_authority as source_fixture
        self.fixture = source_fixture.SourceDialoguePreflightTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.service = self.fixture.service
        self.namespace = self.fixture.namespace
        self.project_id = str(self.service.create_project(
            self.namespace, "occurrence-range-integration"
        ).project_id)

    def manifest(self, source, claims, texts, fragments=None):
        self.fixture._attach_source(self.project_id, source, role="script")
        candidate = {
            "protected_dialogue": claims,
            "shots": [{"镜头编号": f"EP01-{index + 1:03d}",
                       "画面内容": f"江氏说道：“{text}”"}
                      for index, text in enumerate(texts)],
        }
        if fragments is not None:
            candidate["prevention_evidence"] = {"protected_dialogue_fragments": fragments}
        before = self.service.store.list_artifacts(self.namespace, self.project_id)
        manifest, issues = self.service._source_dialogue_authority_manifest(
            self.namespace, self.project_id, candidate,
        )
        self.assertEqual(self.service.store.list_artifacts(self.namespace, self.project_id), before)
        return manifest, issues

    def test_service_manifest_accepts_split_and_seals_ranges(self):
        manifest, issues = self.manifest(
            "江氏：大嫂，没来晚吧？",
            [{"speaker": "江氏", "text": "大嫂，没来晚吧？"}],
            ["大嫂，", "没来晚吧？"],
        )
        self.assertEqual(issues, [])
        self.assertEqual(manifest["status"], "pass")
        coverage = manifest["dialogue_fragment_coverage"]
        self.assertEqual(coverage["status"], "pass")
        self.assertEqual([r["char_range"] for r in coverage["fragments"]], [[0, 3], [3, 8]])

    def test_service_manifest_rejects_forged_candidate_occurrence(self):
        manifest, issues = self.manifest(
            "江氏：好。",
            [{"speaker": "江氏", "text": "好。", "occurrence_id": "forged"}],
            ["好。"],
        )
        self.assertEqual(manifest["status"], "fail_current")
        self.assertTrue(issues)

    def test_service_manifest_rejects_single_actual_for_two_occurrences(self):
        manifest, issues = self.manifest(
            "江氏：好。\n江氏：好。",
            [{"speaker": "江氏", "text": "好。"}] * 2,
            ["好。"],
        )
        self.assertEqual(manifest["status"], "fail_current")
        self.assertTrue(issues)

    def test_service_manifest_rejects_missing_fragment_character(self):
        manifest, issues = self.manifest(
            "江氏：大嫂，没来晚吧？",
            [{"speaker": "江氏", "text": "大嫂，没来晚吧？"}],
            ["大嫂，", "来晚吧？"],
        )
        self.assertEqual(manifest["status"], "fail_current")
        self.assertTrue(issues)


if __name__ == "__main__":
    unittest.main(verbosity=2)
