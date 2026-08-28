from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PLUGIN_ROOT / "skills" / "shortdrama-storyboard"
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import run_golden_tests  # noqa: E402
import validate_storyboard  # noqa: E402


def compact_fixture() -> dict[str, Any]:
    suite = json.loads((SKILL_ROOT / "references" / "golden_tests.json").read_text(encoding="utf-8"))
    obj = copy.deepcopy(suite["fixtures"]["valid_dialogue"])
    run_golden_tests.inject_pg1_fixture_evidence(obj)
    run_golden_tests.upgrade_fixture_to_compact(obj)
    return obj


def validate(obj: dict[str, Any]) -> dict[str, Any]:
    return validate_storyboard.validate_project(
        obj,
        mode="audit",
        rules_path=str(SKILL_ROOT / "references" / "storyboard_runtime_rules.json"),
    )


def has_code(report: dict[str, Any], code: str) -> bool:
    return any(issue.get("error_code") == code for issue in report.get("issues", []))


def shot_schema() -> dict[str, Any]:
    schema = json.loads((SKILL_ROOT / "references" / "storyboard_schema.json").read_text(encoding="utf-8"))
    return schema["$defs"]["shot"]


def root_schema() -> dict[str, Any]:
    return json.loads((SKILL_ROOT / "references" / "storyboard_schema.json").read_text(encoding="utf-8"))


def schema_allows_shot(shot: dict[str, Any]) -> bool:
    contract = shot_schema()
    declared = set(contract["properties"])
    return contract.get("additionalProperties") is not False or set(shot).issubset(declared)


def schema_allows_root(obj: dict[str, Any]) -> bool:
    contract = root_schema()
    declared = set(contract["properties"])
    return contract.get("additionalProperties") is not False or set(obj).issubset(declared)


def error_catalog_map(filename: str) -> dict[str, dict[str, Any]]:
    value = json.loads((SKILL_ROOT / "references" / filename).read_text(encoding="utf-8"))
    rows = value["error_catalog"] if isinstance(value, dict) else value
    return {row["error_code"]: row for row in rows}


class V050CompactValidatorTests(unittest.TestCase):
    def test_compact_dialogue_and_three_stage_timeline_pass(self) -> None:
        obj = compact_fixture()
        obj["shots"][0]["画面内容"] = (
            "0.0–0.8秒，女主站在桌前，目光落在来人身上，右手压住袖口；"
            "0.8–4.3秒，女主嘴角收住、肩颈微紧，呼吸克制后开口："
            "【女主】你终于来了？声音压低，语速放慢，开口前呼吸稳定，在‘终于’前停顿，"
            "‘来了’加重，尾音克制上扬，问号表现试探；"
            "4.3–5.0秒，她保持视线和站位，听者延后反应，动作收束。"
        )
        report = validate(obj)
        self.assertEqual(report["status"], "pass", report["issues"])
        self.assertEqual(report["schema_version"], "R10.1")
        self.assertEqual(report["batch_count"], 0)

    def test_deprecated_standalone_field_fails(self) -> None:
        obj = compact_fixture()
        obj["shots"][0]["台词"] = "你终于来了？"
        report = validate(obj)
        self.assertTrue(has_code(report, "E-DEPRECATED-FIELD-001"), report["issues"])
        self.assertFalse(schema_allows_shot(obj["shots"][0]))

    def test_schema_rejects_unknown_shot_field(self) -> None:
        obj = compact_fixture()
        obj["shots"][0]["未登记扩展字段"] = "不得进入R10.1 canonical"
        self.assertIs(shot_schema()["additionalProperties"], False)
        self.assertFalse(schema_allows_shot(obj["shots"][0]))

    def test_non_contiguous_timeline_fails(self) -> None:
        obj = compact_fixture()
        obj["shots"][0]["画面内容"] = "0–1秒女主停住；1.5–5秒女主抬眼。"
        report = validate(obj)
        self.assertTrue(has_code(report, "E-TIMELINE-001"), report["issues"])

    def test_duplicate_batch_fails(self) -> None:
        obj = compact_fixture()
        obj["batches"] = [{"批次编号": "B01"}]
        report = validate(obj)
        self.assertTrue(has_code(report, "E-DUPLICATE-GENERATION-001"), report["issues"])

    def test_even_empty_batches_key_is_rejected(self) -> None:
        obj = compact_fixture()
        obj["batches"] = []
        report = validate(obj)
        self.assertTrue(has_code(report, "E-DUPLICATE-GENERATION-001"), report["issues"])
        self.assertNotIn("batches", root_schema()["properties"])
        self.assertFalse(schema_allows_root(obj))

    def test_seedance_prompt_cache_root_is_rejected(self) -> None:
        obj = compact_fixture()
        obj["seedance_prompt_cache"] = {}
        report = validate(obj)
        self.assertTrue(has_code(report, "E-DUPLICATE-GENERATION-001"), report["issues"])
        self.assertFalse(schema_allows_root(obj))

    def test_registered_evidence_and_anchor_roots_remain_legal(self) -> None:
        obj = compact_fixture()
        obj["anchors"] = {"character": {"女主": "青色窄袖衣"}}
        obj["canonical_extension"] = {"studio_continuity": {"version": 1}}
        report = validate(obj)
        self.assertEqual(report["status"], "pass", report["issues"])
        self.assertTrue(schema_allows_root(obj))
        self.assertIs(root_schema()["additionalProperties"], False)
        self.assertEqual(
            set(root_schema()["x-top-level-allowlist"]),
            validate_storyboard.COMPACT_TOP_LEVEL_FIELDS,
        )

    def test_nested_derived_cache_in_canonical_extension_is_rejected(self) -> None:
        obj = compact_fixture()
        obj["canonical_extension"] = {"studio": {"seedance_prompt_cache": {}}}
        report = validate(obj)
        self.assertTrue(has_code(report, "E-DUPLICATE-GENERATION-001"), report["issues"])

    def test_mother_template_columns_are_export_only_and_rejected(self) -> None:
        obj = compact_fixture()
        obj["mother_template_columns"] = list(validate_storyboard.COMPACT_MOTHER_COLUMNS)
        report = validate(obj)
        self.assertTrue(has_code(report, "E-DUPLICATE-GENERATION-001"), report["issues"])
        self.assertFalse(schema_allows_root(obj))

    def test_natural_motion_sentence_replaces_retired_sublabels(self) -> None:
        obj = compact_fixture()
        obj["shots"][0]["运镜方式"] = "推镜头，摄影机极缓慢靠近女主，用于聚焦她的克制反应"
        report = validate(obj)
        codes = {issue["error_code"] for issue in report["issues"]}
        self.assertNotIn("E-MOTION-002", codes, report["issues"])
        self.assertNotIn("E-MOTION-003", codes, report["issues"])
        self.assertNotIn("E-DEPRECATED-FIELD-001", codes, report["issues"])

    def test_retired_motion_sublabel_fails_in_display_field(self) -> None:
        obj = compact_fixture()
        obj["shots"][0]["运镜方式"] = "推镜头｜运镜主体：女主｜叙事功能：聚焦注意力"
        report = validate(obj)
        self.assertTrue(has_code(report, "E-DEPRECATED-FIELD-001"), report["issues"])

    def test_verbatim_source_evidence_may_contain_deprecated_label_text(self) -> None:
        obj = compact_fixture()
        quoted = "原文逐字记录：台词：你终于来了？随后人物抬眼。"
        for field in ("原文支撑句", "覆盖原文", "原文依据类型", "参考图锚定"):
            obj["shots"][0][field] = quoted
        report = validate(obj)
        self.assertFalse(has_code(report, "E-DEPRECATED-FIELD-001"), report["issues"])

    def test_compact_error_semantics_match_runtime_catalog(self) -> None:
        catalog = error_catalog_map("error_catalog.json")
        runtime = error_catalog_map("storyboard_runtime_rules.json")
        codes = set(catalog).intersection(runtime)
        self.assertGreaterEqual(len(codes), 60)
        fields = ("severity", "trigger_condition", "required_evidence", "repair_action")
        for code in codes:
            self.assertIn(code, catalog)
            self.assertIn(code, runtime)
            for field in fields:
                self.assertEqual(catalog[code][field], runtime[code][field], f"{code}.{field}")
        forbidden_repairs = ("补齐生成批次", "Seedance独立列")
        for code, row in catalog.items():
            repair = str(row.get("repair_action", ""))
            self.assertFalse(any(term in repair for term in forbidden_repairs), f"{code}: {repair}")

    def test_all_four_light_fields_are_required(self) -> None:
        obj = compact_fixture()
        obj["shots"][0]["光源方向"] = ""
        report = validate(obj)
        self.assertTrue(has_code(report, "E-LIGHT-001"), report["issues"])

    def test_exact_27_field_contract(self) -> None:
        obj = compact_fixture()
        self.assertEqual(obj["field_order"], validate_storyboard.COMPACT_SHOT_FIELDS)
        self.assertEqual(len(obj["field_order"]), 27)
        self.assertIn("切镜/转场提示词", obj["field_order"])
        self.assertNotIn("切镜转场提示词", obj["field_order"])


if __name__ == "__main__":
    unittest.main()
