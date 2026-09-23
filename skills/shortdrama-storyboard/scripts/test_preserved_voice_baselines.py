"""Bundled original-voice controls; no private files, server or repo-test imports."""
import copy
import json
import unittest

from authoring_contract_blocks import ContractBlockError, voice_block
from field_content import field_policy, preserved_voice_baseline, voice_content_findings
from validate_storyboard import VOICE_TEMPLATE_LABELS, _voice_template_errors
from voice_sequence import build_voice_sequence
from rule_registry import (REFERENCE_ROOT, _historical_migration_matches,
                           check_registry_definition, load_registry, registry_sha256)


BASELINES = (
    "青年女声，中音区，清亮偏薄，气息有稳定支撑。",
    "年长女声，中低音区，偏厚偏暗，气息稳重。",
    "中年女声，中低音区，偏厚偏暗，字头清楚、气息稳定。",
    "中年女声，中低音区，偏厚偏暗、微沙，沉稳直接。",
    "青年男声，中音区，略厚而明亮，平常吐字清楚、声线端正。",
    "中年男声，中低音区，厚实偏沉，声线平稳。",
)
INHERITED = "保持角色自然声线，不额外改变音高、厚薄或明暗。"
ROWS = [("甲", "明日再商议。")]


def spec(baseline):
    return {"baseline": baseline, "speech_act": "陈述延后商议的安排。",
            "current_expression": "平静，意在确认商议时间。", "volume": "近距离交谈音量。",
            "rhythm": "中速，逐字清楚。", "breath": "发声前自然吸气，句末轻呼气。",
            "pauses": "句中不停顿。", "emphasis": "“明日”。", "tail_direction": "落稳",
            "tail_execution": "句末自然收住。", "prohibited_tones": ["喊叫", "哭腔"]}


class PreservedVoiceBaselineTests(unittest.TestCase):
    def test_registered_originals_preserve_bytes_and_fixed_labels_through_author_gate(self):
        policy = field_policy()["voice_subfields"]["基础声线"]
        self.assertEqual(policy["preserved_complete_baselines"], list(BASELINES))
        self.assertEqual(policy["preserved_inheritance_baseline"], INHERITED)
        for baseline in (*BASELINES, INHERITED):
            with self.subTest(baseline=baseline):
                value = voice_block(mode="spoken", dialogue_rows=ROWS, **spec(baseline))
                self.assertEqual(value.splitlines()[0], "基础声线：" + baseline)
                self.assertEqual([line.split("：", 1)[0] for line in value.splitlines()], list(VOICE_TEMPLATE_LABELS))
                self.assertEqual(voice_content_findings(value, dialogue_rows=ROWS), [])
                self.assertEqual(_voice_template_errors(value, ROWS[0][1], ROWS), [])

    def test_multiple_speakers_keep_their_exact_baselines(self):
        rows = ROWS + [("乙", "明日再商议。")]
        value = build_voice_sequence([(rows[0][0], rows[0][1], spec(BASELINES[0])),
                                      (rows[1][0], rows[1][1], spec(INHERITED))])
        self.assertIn("〔1:甲〕" + BASELINES[0] + "〔2:乙〕" + INHERITED, value)
        self.assertEqual(voice_content_findings(value, dialogue_rows=rows), [])
        self.assertEqual(_voice_template_errors(value, "".join(t for _, t in rows), rows), [])
        bad_second = INHERITED + "乙抬手。"
        with self.assertRaises(ContractBlockError):
            build_voice_sequence([(rows[0][0], rows[0][1], spec(BASELINES[0])),
                                  (rows[1][0], rows[1][1], spec(bad_second))])
        bad = value.replace("〔2:乙〕" + INHERITED, "〔2:乙〕" + bad_second)
        self.assertTrue(voice_content_findings(bad, dialogue_rows=rows))
        self.assertTrue(any("第2句乙" in error for error in
                            _voice_template_errors(bad, "".join(t for _, t in rows), rows)))

    def test_append_prepend_and_partial_values_do_not_gain_compatibility(self):
        for baseline in (*BASELINES, INHERITED):
            for extra in ("甲抬手。", "愤怒，意在威胁。", "摄影机位于窗旁。", "音量提高。", "基础声线：中年男声。"):
                for bad in (baseline + extra, extra + baseline):
                    with self.subTest(bad=bad):
                        self.assertFalse(preserved_voice_baseline(bad))
                        with self.assertRaises(ContractBlockError):
                            voice_block(mode="spoken", dialogue_rows=ROWS, **spec(bad))
        for bad in ("保持自然声线。", "青年女声，中音区。", "清亮偏薄，气息有稳定支撑。", "保持角色自然声线。"):
            with self.subTest(bad=bad):
                self.assertFalse(preserved_voice_baseline(bad))
                with self.assertRaises(ContractBlockError):
                    voice_block(mode="spoken", dialogue_rows=ROWS, **spec(bad))

    def test_compatibility_does_not_apply_to_other_fields_or_disable_other_voice_checks(self):
        for key, value in (("current_expression", "甲抬手。"),
                           ("speech_act", "摄影机位于窗旁。"),
                           ("emphasis", "“没说过”。"),
                           ("tail_direction", "任意"),
                           ("current_expression", BASELINES[0])):
            invalid = spec(INHERITED)
            invalid[key] = value
            with self.subTest(key=key), self.assertRaises(ContractBlockError):
                voice_block(mode="spoken", dialogue_rows=ROWS, **invalid)
        valid = voice_block(mode="spoken", dialogue_rows=ROWS, **spec(INHERITED))
        self.assertTrue(_voice_template_errors(valid.replace("言语行为：", "当前表达："), ROWS[0][1], ROWS))
        with self.assertRaises(ContractBlockError):
            voice_block(mode="silent", dialogue_rows=[], baseline=INHERITED)


class ExistingVoiceMigrationTests(unittest.TestCase):
    def setUp(self):
        self.registry = load_registry()
        self.baseline = json.loads((REFERENCE_ROOT / "historical_rule_baseline.json").read_text(encoding="utf-8"))
        prior = self.baseline["atomic_semantic_definitions"]["AR-R10-VOICE-001"]
        row = next(r for r in self.registry["atomic_rules"] if r["rule_id"] == "AR-R10-VOICE-001")
        self.pairs = [
            ("critical_runtime_policies", "field_content_policy",
             self.baseline["critical_runtime_policies"]["field_content_policy"],
             self.registry["runtime"]["field_content_policy"]),
            ("atomic_semantic_definitions", "AR-R10-VOICE-001", prior,
             {k: row.get(k) for k in prior}),
        ]

    def test_named_voice_migration_is_required_and_does_not_reuse_0552_authority(self):
        for kind, identity, previous, current in self.pairs:
            self.assertTrue(_historical_migration_matches(self.registry, kind, identity, previous, current))
            without = copy.deepcopy(self.registry)
            del without["historical_rule_migrations"]["existing-voice-0553"]
            self.assertFalse(_historical_migration_matches(without, kind, identity, previous, current))
        faults = check_registry_definition(without)
        self.assertTrue(any("field_content_policy" in f for f in faults))
        self.assertTrue(any("AR-R10-VOICE-001" in f for f in faults))

    def test_wrong_authorization_or_either_hash_cannot_authorize_changes(self):
        for kind, identity, previous, current in self.pairs:
            for key, value in (("basis", "user-authorized-unified-root-cause-fix-2026-09-22"),
                               ("authorization_work_order", "WO-wrong"),
                               ("legacy_applies_to_current_generation", True),
                               ("baseline_sha256", "0" * 64), ("current_sha256", "0" * 64)):
                altered = copy.deepcopy(self.registry)
                migration = altered["historical_rule_migrations"]["existing-voice-0553"]
                target = migration[kind][identity] if key.endswith("sha256") else migration
                target[key] = value
                with self.subTest(kind=kind, key=key):
                    self.assertFalse(_historical_migration_matches(altered, kind, identity, previous, current))
            self.assertFalse(_historical_migration_matches(self.registry, kind, identity, previous,
                                                          {**current, "unapproved_extra": True}))
            self.assertFalse(_historical_migration_matches(self.registry, kind, identity,
                                                          {**previous, "unapproved_extra": True}, current))

    def test_unrelated_rules_cannot_borrow_the_voice_authorization_even_with_claimed_hashes(self):
        identity = next(key for key in self.baseline["atomic_semantic_definitions"]
                        if key != "AR-R10-VOICE-001")
        previous = self.baseline["atomic_semantic_definitions"][identity]
        row = next(r for r in self.registry["atomic_rules"] if r["rule_id"] == identity)
        row["rule_full_text"] += " unauthorized change"
        current = {k: row.get(k) for k in previous}
        self.registry["historical_rule_migrations"]["existing-voice-0553"]["atomic_semantic_definitions"][identity] = {
            "baseline_sha256": registry_sha256(previous), "current_sha256": registry_sha256(current)}
        self.assertFalse(_historical_migration_matches(self.registry, "atomic_semantic_definitions",
                                                      identity, previous, current))
        self.assertTrue(any(identity in f for f in check_registry_definition(self.registry)))


if __name__ == "__main__":
    unittest.main()
