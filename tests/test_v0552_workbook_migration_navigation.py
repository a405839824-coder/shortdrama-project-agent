"""Reject impossible lossless migrations before creating another work order."""
import copy
from contextlib import nullcontext
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "server"), str(ROOT / "tests")]
from shortdrama_mcp.canonical import stable_json_hash
from shortdrama_mcp.workbook_migration import shot_atoms
from shortdrama_mcp.workbook_migration_service import WorkbookMigrationMixin, _migration_field_precheck
from test_efficiency import valid_storyboard


class NavigationOnlyService(WorkbookMigrationMixin):
    def __init__(self, candidate, *, source=None):
        self.candidate = candidate
        self.hub = {"project_state":{"authoritative_sources":[source] if source else []}}
        self.plan = {"source_artifact_id":"ART-old", "source_raw_sha256":"a" * 64,
                     "proof":{"episode_ids":["EP01"], "atom_sha256":stable_json_hash(shot_atoms(candidate))},
                     "prepared_atom_sha256":stable_json_hash(shot_atoms(candidate)),
                     "field_content_precheck":_migration_field_precheck(candidate)}
        # Any attempted storage or authority creation would fail this test.
        self.store = SimpleNamespace(lock=lambda key: nullcontext())

    def _workbook_migration_plan(self, *args):
        return self.plan, self.candidate, self.hub, {}


class WorkbookMigrationNavigationTests(unittest.TestCase):
    def test_spatial_failure_reports_real_requirements_without_writes_for_plan_and_execute(self):
        candidate = valid_storyboard()
        candidate["shots"][0]["空间站位描述"] = "甲在木案北侧。"
        before = copy.deepcopy(candidate)
        service = NavigationOnlyService(candidate)
        for action in ("plan", "execute"):
            result = service.migrate_workbook_canonical("test", project_id="SBP-test", work_order_id="WO-plan",
                action=action, expected_plan_sha256=None, migration_id=None, request_session_id="session")
            self.assertEqual(result.status, "workbook_migration_requires_reconstruction")
            self.assertFalse(result.data["production_work_order_created"])
            self.assertFalse(result.data["artifact_created"])
            self.assertFalse(result.data["certificate_created"])
            guidance = result.data["reconstruction_guidance"]
            self.assertFalse(guidance["authorization_granted"])
            self.assertIsNone(guidance["dialogue_authority_artifact_id"])
            self.assertEqual(guidance["begin_project_task_template"]["targets"],
                             ["ART-old", "source_sha256:" + "a" * 64, "EP01"])
            self.assertNotIn("source_link_mode", guidance["commit_storyboard_template"])
            self.assertTrue(any("world_position_in_screen_field" == row["role"]
                                for row in service.plan["field_content_precheck"]["findings"]))
        self.assertEqual(candidate, before)

    def test_existing_source_is_reused_but_a_storyboard_is_not_promoted_to_dialogue_authority(self):
        candidate = valid_storyboard()
        candidate["shots"][0]["起始状态"] = "甲身体朝东北。"
        for role, expected in (("storyboard", None), ("script", "ART-source"), ("novel", "ART-source")):
            service = NavigationOnlyService(candidate, source={"artifact_id":"ART-source", "source_role":role})
            result = service.migrate_workbook_canonical("test", project_id="SBP-test", work_order_id="WO-plan",
                action="plan", expected_plan_sha256=None, migration_id=None, request_session_id="session")
            self.assertEqual(result.data["reconstruction_guidance"]["dialogue_authority_artifact_id"], expected)

    def test_clean_field_candidate_and_lossless_hash_protection_are_unchanged(self):
        candidate = valid_storyboard()
        self.assertEqual(_migration_field_precheck(candidate)["status"], "pass")
        record = {"plan":NavigationOnlyService(candidate).plan}
        WorkbookMigrationMixin._assert_workbook_migration_atoms(record, candidate)
        altered = copy.deepcopy(candidate)
        altered["shots"][0]["空间站位描述"] = "甲在画面左侧。"
        with self.assertRaisesRegex(ValueError, "semantic_change_forbidden"):
            WorkbookMigrationMixin._assert_workbook_migration_atoms(record, altered)


if __name__ == "__main__":
    unittest.main()
