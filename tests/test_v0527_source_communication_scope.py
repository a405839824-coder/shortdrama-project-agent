"""Given content stays oral; precise event-only source remains pending."""
from pathlib import Path
import copy
import sys
import unittest
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "server"), str(ROOT / "skills/shortdrama-storyboard/scripts")]
from source_content_limits import (_summary_shape, oral_communication_candidate_text,
    inspect_source_content_limits, text_sha256, AFFORDANCE, CONTRACT, ENTAILMENT, TREATMENT)
from validate_storyboard import (_catalog_map, _stable_sha256,
    _validate_adapted_dialogue_audit, load_runtime_rules)

EVENT_CASES = [['apology', '甲向乙道歉时，', 'apology_contents_unstated', True], ['instruction_receipt', '甲守在隔扇外听医嘱，', 'instruction_receipt_contents_unstated', True], ['given_apology', '甲向乙道歉，说自己会补偿。', 'apology_contents_unstated', False], ['quoted_apology', '甲向乙道歉：“我会补偿。”', 'apology_contents_unstated', False], ['given_instruction', '甲守在隔扇外听医嘱，须先复查。', 'instruction_receipt_contents_unstated', False], ['quoted_instruction', '甲守在隔扇外听医嘱：“先复查。”', 'instruction_receipt_contents_unstated', False]]
CUE_CASES = [['written_ack', '甲在书面说明上落名，承认已领取物件。', False], ['written_then_oral', '甲在书面说明上落名，承认已领取物件。甲说道：“我已领取。”', True], ['oral_ack', '甲向乙承认已领取物件。', True], ['gift_note', '甲送物时附了一张简短礼单，只说用于查验。', False], ['gift_then_oral', '甲送物时附了一张简短礼单，只说用于查验。甲说道：“请查验。”', True], ['future_condition', '是否交付物件，等乙愿意再说。', False], ['ordered_deferral', '甲要求等乙愿意再说。', True], ['actual_reading', '甲朗读书面说明。', True]]

def fixture(source, *, event_kind=None, remainder=""):
    pieces = [source, remainder] if event_kind else [source]
    parent = "".join(pieces)
    shot = {"镜头编号": "EP90-001", "画面内容": "甲保持倾听，乙停在原位；无可辨对白。",
            "心理音轨": "客观旁白：“" + parent + "”无角色口型。"}
    obj = {"source_lock": {"adapted_dialogue_contract": "source-fact-dialogue-v1"},
           "shots": [shot], "prevention_evidence": {"source_ledger": []}}
    entries, segments, cursor = [], [], 0
    for index, text in enumerate(pieces, 1):
        limited = bool(event_kind and index == 1)
        aid, uid = f"ADP-{index}", f"UNIT-{index}"
        obj["prevention_evidence"]["source_ledger"].append({
            "source_id": uid, "source_text": text, "source_type": "原文叙述",
            "speaker": "内容未给" if limited else "客观叙述", "target_shots": ["EP90-001"]})
        entry = {"adaptation_id": aid, "source_unit_id": uid, "source_ledger_index": index,
                 "source_text": text, "target_shots": ["EP90-001"], "speaker": "", "adapted_text": "",
                 "dialogue_affordance": AFFORDANCE if limited else "不宜转对白",
                 "treatment": TREATMENT if limited else "可视动作",
                 "scene_semantics": "叙述性会话场景" if limited else "非会话叙述",
                 "communication_functions": ["其他"] if limited else [],
                 "exchange_participants": ["甲", "原文接收方"] if limited else [],
                 "entailment_status": ENTAILMENT if limited else "entailed_by_source",
                 "plot_change": False, "new_plot_facts": [], "fact_atoms": [text],
                 "speaker_basis": "没有补造未给内容或新说话人。", "authorization": "合成控制",
                 "adaptation_reason": "明确已给内容和仅给事件的边界。",
                 "speech_necessity_basis": "仅事件内容未给；书面或未来条件不视为已经开口。"}
        if limited:
            entry["source_content_limit"] = {"contract": CONTRACT, "partition_id": "PARENT",
                "source_text_sha256": text_sha256(text), "unprovided_content_kind": event_kind,
                "narration": {"shot_id": "EP90-001", "field": "心理音轨", "text": parent,
                              "field_sha256": text_sha256(shot["心理音轨"])},
                "action": {"shot_id": "EP90-001", "field": "画面内容",
                           "field_sha256": text_sha256(shot["画面内容"])}}
        entries.append(entry)
        segments.append({"source_ledger_index": index, "source_unit_id": uid,
                         "char_range": [cursor, cursor + len(text)],
                         "kind": "event_only" if limited else "non_dialogue", "adaptation_id": aid})
        cursor += len(text)
    audit = {"contract": "source-fact-dialogue-v1", "scope_episode_ids": ["EP90"],
             "candidate_unit_count": len(entries), "reviewed_unit_count": len(entries),
             "scene_classification_count": len(entries), "adapted_dialogue_count": 0,
             "conversation_scene_count": 1 if event_kind else 0, "coverage_percent": 100,
             "ordered_entry_sha256": _stable_sha256([e["adaptation_id"] for e in entries]), "entries": entries}
    if event_kind:
        audit["source_content_partitions"] = [{"partition_id": "PARENT", "source_text": parent,
            "source_text_sha256": text_sha256(parent), "segments": segments}]
    obj["prevention_evidence"]["dialogue_adaptation_audit"] = audit
    return obj


class SourceCommunicationScopeTests(unittest.TestCase):
    def faults(self, obj):
        return _validate_adapted_dialogue_audit(obj, obj["shots"], _catalog_map(load_runtime_rules()))

    def assert_pending(self, source, kind, remainder):
        obj = fixture(source, event_kind=kind, remainder=remainder)
        self.assertEqual(self.faults(obj), [])
        inspected = inspect_source_content_limits(obj, obj["shots"])
        self.assertEqual(inspected["issues"], [])
        self.assertEqual(len(inspected["obligations"]), 1)
        self.assertEqual(inspected["obligations"][0]["source_state"], "pending_independent")
        return obj

    def test_apology_public_binding_remains_pending(self):
        self.assert_pending("甲向乙道歉时，", "apology_contents_unstated", "乙保持沉默。")

    def test_instruction_receipt_public_binding_remains_pending(self):
        self.assert_pending("甲守在隔扇外听医嘱，", "instruction_receipt_contents_unstated", "包裹仍在门外。")

    def test_actual_fields_cannot_be_reused_after_edit(self):
        obj = self.assert_pending("甲向乙道歉时，", "apology_contents_unstated", "乙保持沉默。")
        obj["shots"][0]["心理音轨"] += "新文字"
        self.assertTrue(any("actual output hash mismatch" in e["evidence"] for e in self.faults(obj)))

    def test_given_content_cannot_be_hidden_by_updated_hashes(self):
        obj = fixture("甲向乙道歉，说自己会补偿。", event_kind="apology_contents_unstated", remainder="乙保持沉默。")
        self.assertTrue(any("unsupported event syntax" in e["evidence"] for e in self.faults(obj)))

    def test_author_flag_does_not_supply_independent_evidence(self):
        obj = fixture("甲向乙道歉时，", event_kind="apology_contents_unstated", remainder="乙保持沉默。")
        obj["prevention_evidence"]["dialogue_adaptation_audit"]["entries"][0]["source_content_limit"]["approved"] = True
        self.assertTrue(any("no author approval flags" in e["evidence"] for e in self.faults(obj)))

    def test_mask_only_proved_tokens_preserves_length_and_neighbors(self):
        text = "甲在书面说明上落名，承认已领取物件。甲说道：“请核对。”"
        residual = oral_communication_candidate_text(text)
        self.assertEqual(len(residual), len(text))
        self.assertIn('甲说道：“请核对。”', residual)
        self.assertIn('已领取物件。', residual)


def _event_test(text, kind, expected):
    def run(self):
        self.assertEqual(_summary_shape(text, kind), expected)
    return run


def _cue_test(text, expected):
    def run(self):
        faults = self.faults(fixture(text))
        speech_fault = any("原文已发生言语行为" in row["evidence"] for row in faults)
        self.assertEqual(speech_fault, expected, faults)
        if not expected:
            self.assertEqual(faults, [])
    return run

for name, text, kind, expected in EVENT_CASES:
    setattr(SourceCommunicationScopeTests, "test_event_" + name, _event_test(text, kind, expected))
for name, text, expected in CUE_CASES:
    setattr(SourceCommunicationScopeTests, "test_cue_" + name, _cue_test(text, expected))

if __name__ == "__main__":
    unittest.main()
