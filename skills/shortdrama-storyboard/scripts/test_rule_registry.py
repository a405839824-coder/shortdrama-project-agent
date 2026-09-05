#!/usr/bin/env python3
"""Positive and negative registry tests; fixture mutation is isolated."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from rule_registry import (REFERENCE_ROOT, check_documentation, check_registry_definition,
                           load_registry, render_views, validate_registry,
                           write_views)


class RuleRegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = load_registry()

    def test_current_views_match_entire_registry(self):
        self.assertEqual(validate_registry(), [])

    def test_registry_is_complete_not_count_only(self):
        registry = copy.deepcopy(self.registry)
        registry["errors"][0]["error_code"] = "E-REMOVED-999"
        self.assertTrue(any("dangling error" in error for error in check_registry_definition(registry)))

    def test_duplicate_identity_rejected(self):
        registry = copy.deepcopy(self.registry)
        registry["errors"].append(copy.deepcopy(registry["errors"][0]))
        self.assertTrue(any("duplicate" in error for error in check_registry_definition(registry)))

    def test_stale_active_revision_rejected(self):
        registry = copy.deepcopy(self.registry)
        registry["validators"][0]["rule_revision"] = "0.5.23"
        self.assertTrue(any("stale active" in error for error in check_registry_definition(registry)))

    def test_missing_applicability_rejected(self):
        registry = copy.deepcopy(self.registry)
        registry["atomic_rules"][0]["trigger_condition"] = ""
        self.assertTrue(any("applicability" in error for error in check_registry_definition(registry)))

    def test_dangling_conflict_rejected(self):
        registry = copy.deepcopy(self.registry)
        row = next(row for row in registry["atomic_rules"] if "conflict_ref" in row)
        row["conflict_ref"] = "C-999"
        self.assertTrue(any("dangling conflict" in error for error in check_registry_definition(registry)))

    def test_dangling_task_module_rejected(self):
        registry = copy.deepcopy(self.registry)
        registry["task_cards"][0]["must_call_modules"].append("UNKNOWN_MODULE")
        self.assertTrue(any("dangling module" in error for error in check_registry_definition(registry)))

    def test_same_count_wrong_gate_identity_rejected(self):
        registry = copy.deepcopy(self.registry)
        registry["runtime"]["gates"]["template_hard_gates"][-1]["gate_id"] = "THG-99"
        self.assertTrue(any("complete THG" in error for error in check_registry_definition(registry)))

    def test_timing_shadow_rate_rejected(self):
        registry = copy.deepcopy(self.registry)
        registry["runtime"]["dialogue_policy"]["default_cjk_chars_per_second"] = 2.8
        self.assertTrue(any("shadow rate" in error for error in check_registry_definition(registry)))

    def test_chapter_cannot_restore_separate_rate(self):
        registry = copy.deepcopy(self.registry)
        registry["runtime"]["modules"]["ADAPTATION_BUDGET"]["dialogue_rate_cjk_per_second"] = 2.8
        self.assertTrue(any("chapter budget" in error for error in check_registry_definition(registry)))

    def test_provisional_speed_never_measured_articulation(self):
        registry = copy.deepcopy(self.registry)
        registry["runtime"]["timing_policy"]["profiles"][0]["rate_basis"] = "articulation"
        self.assertTrue(any("unmeasured articulation" in error for error in check_registry_definition(registry)))

    def test_unverified_speed_cannot_be_labelled_measured(self):
        registry = copy.deepcopy(self.registry)
        registry["runtime"]["timing_policy"]["profiles"][0]["evidence_status"] = "measured"
        self.assertTrue(any("measured label lacks" in error for error in check_registry_definition(registry)))

    def test_capability_cannot_reintroduce_window_roles(self):
        registry = copy.deepcopy(self.registry)
        registry["capabilities"]["storyboard_window_architecture"]["window_role_restrictions"] = True
        self.assertTrue(any("window-neutral" in error for error in check_registry_definition(registry)))

    def test_capability_cannot_create_different_ledger_authority(self):
        registry = copy.deepcopy(self.registry)
        registry["capabilities"]["project_ledger_policy"]["operational_authority"] = "global_ledger"
        self.assertTrue(any("project ledger policy differs" in error for error in check_registry_definition(registry)))

    def test_conflict_atomic_text_generated_from_same_definition(self):
        views = render_views(self.registry)
        atoms = {row["rule_id"]: row for row in views["atomic_rule_registry.jsonl"]}
        for conflict in views["conflict_crosswalk.json"]:
            identity = "AR-R10-" + conflict["conflict_id"]
            if identity in atoms:
                self.assertIn(conflict["decision"], atoms[identity]["rule_full_text"])
                self.assertEqual(conflict["linked_error_codes"], atoms[identity]["linked_error_codes"])

    def test_runtime_error_index_is_full_exact_catalog(self):
        views = render_views(self.registry)
        self.assertEqual(views["storyboard_runtime_rules.json"]["error_catalog"], views["error_catalog.json"])
        self.assertIn("E-T2I-001", {row["error_code"] for row in views["error_catalog.json"]})

    def test_task_gate_references_are_full_ordered_set(self):
        views = render_views(self.registry)
        expected = [row["gate_id"] for row in views["storyboard_runtime_rules.json"]["gates"]["template_hard_gates"]]
        for row in views["task_package_cards.json"]:
            if "required_template_gate_ids" in row:
                self.assertEqual(row["required_template_gate_ids"], expected)

    def test_same_identity_content_drift_is_rejected_without_writes(self):
        with tempfile.TemporaryDirectory(prefix="r10-registry-test-") as directory:
            root = Path(directory)
            (root / "rule_registry.json").write_text(json.dumps(self.registry, ensure_ascii=False), encoding="utf-8")
            write_views(root)
            target = root / "validator_ruleset.json"
            rows = json.loads(target.read_text(encoding="utf-8"))
            rows[0]["applies_when"] = "silently disabled"
            target.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
            before = {path.name: path.read_bytes() for path in root.iterdir()}
            failures = validate_registry(root)
            self.assertTrue(any("content/order/applicability/parameter drift" in error for error in failures))
            self.assertEqual(before, {path.name: path.read_bytes() for path in root.iterdir()})

    def test_missing_generated_view_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="r10-registry-missing-") as directory:
            root = Path(directory)
            (root / "rule_registry.json").write_text(json.dumps(self.registry, ensure_ascii=False), encoding="utf-8")
            self.assertTrue(any("missing generated view" in error for error in validate_registry(root)))

    def test_generation_idempotence(self):
        self.assertEqual(render_views(self.registry), render_views(copy.deepcopy(self.registry)))

    def test_any_window_and_project_ledger_invariants(self):
        runtime = self.registry["runtime"]
        self.assertFalse(runtime["windowed_execution_policy"]["activation"]["window_role_restrictions"])
        self.assertTrue(runtime["windowed_execution_policy"]["activation"]["same_window_may_complete_entire_workflow"])
        self.assertEqual(runtime["project_ledger_policy"]["operational_authority"], "exact_authenticated_project_id_ledger")

    def contracted_documents(self):
        return {name: (REFERENCE_ROOT / name).read_text(encoding="utf-8")
                for name in self.registry["documentation_contracts"]}

    def test_current_entry_templates_match_registry(self):
        self.assertEqual(check_documentation(self.registry, self.contracted_documents()), [])

    def test_old_rate_reintroduced_into_intake_is_rejected(self):
        documents = self.contracted_documents()
        documents["novel_to_storyboard_intake.md"] += "\n对白按2.8个汉字/秒计算。"
        self.assertTrue(any("retired active declaration" in error for error in check_documentation(self.registry, documents)))

    def test_old_dialogue_template_rate_rejected(self):
        documents = self.contracted_documents()
        documents["template_library.md"] += "\n字数｜2.8最小时长｜停顿"
        self.assertTrue(any("retired active declaration" in error for error in check_documentation(self.registry, documents)))

    def test_optical_aperture_not_confused_with_speech_rate(self):
        documents = self.contracted_documents()
        documents["template_library.md"] += "\n光学示例：f/2.8，适用景深另行确定。"
        self.assertEqual(check_documentation(self.registry, documents), [])

    def test_same_count_wrong_template_identity_rejected(self):
        documents = self.contracted_documents()
        documents["template_library.md"] = documents["template_library.md"].replace("## TPL-R10-50 ", "## TPL-R10-51 ")
        self.assertTrue(any("complete template ID set" in error for error in check_documentation(self.registry, documents)))

    def test_duplicate_template_identity_rejected(self):
        documents = self.contracted_documents()
        documents["template_library.md"] = documents["template_library.md"].replace("## TPL-R10-50 ", "## TPL-R10-49 ")
        self.assertTrue(any("duplicate template identity" in error for error in check_documentation(self.registry, documents)))

    def test_retired_map_generated_from_runtime_deprecations(self):
        views = render_views(self.registry)
        self.assertEqual(views["deprecated_rule_map.json"]["items"], self.registry["runtime"]["deprecated_rules"])
        self.assertTrue(any("2.8" in row["deprecated"] for row in views["deprecated_rule_map.json"]["items"]))
        self.assertFalse(any(row["replacement"] == "R10.DIALOGUE_RATE_2_8" for row in views["deprecated_rule_map.json"]["items"]))

    def test_retired_map_cannot_restore_old_rate_target(self):
        registry = copy.deepcopy(self.registry)
        registry["runtime"]["deprecated_rules"][1]["replacement"] = "R10.DIALOGUE_RATE_2_8"
        self.assertTrue(any("stale or missing replacement" in error for error in check_registry_definition(registry)))

    def test_current_release_sentence_is_generated_not_shadow_definition(self):
        views = render_views(self.registry)
        card = next(row for row in views["task_package_cards.json"] if row["task_package_id"] == "TP-PATCH-ABSORB")
        sentence = next(text for text in card["forbidden_skip_items"] if text.startswith("插件当前发布版本"))
        self.assertTrue(sentence.startswith("插件当前发布版本、清单与活跃发布报告统一标记" + self.registry["plugin_version"] + "；"))
        self.assertIn("0.5.22新增", sentence)  # Historical attribution remains intact.

    def test_old_current_release_sentence_rejected_even_with_current_rule_revision(self):
        registry = copy.deepcopy(self.registry)
        card = next(row for row in registry["task_cards"] if row["task_package_id"] == "TP-PATCH-ABSORB")
        card["forbidden_skip_items"].append("插件当前发布版本、清单与活跃发布报告统一标记0.5.22；")
        self.assertEqual(card["rule_revision"], registry["plugin_version"])
        self.assertTrue(any("current release natural-language" in error for error in check_registry_definition(registry)))

    def test_old_current_version_in_capability_description_rejected(self):
        registry = copy.deepcopy(self.registry)
        registry["capabilities"]["known_limits"].append({"id": "example", "description": "当前0.5.12增量路线"})
        self.assertTrue(any("current-version claim" in error for error in check_registry_definition(registry)))


if __name__ == "__main__":
    unittest.main()
