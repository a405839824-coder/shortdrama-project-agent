from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT / "server", ROOT / "skills/shortdrama-storyboard/scripts", ROOT / "tests"):
    sys.path.insert(0, str(folder))

from source_content_limits import AFFORDANCE, CONTRACT, ENTAILMENT, TREATMENT, inspect_source_content_limits, text_sha256, _summary_shape
from validate_storyboard import _catalog_map, _stable_sha256, _validate_adapted_dialogue_audit, _protected_dialogue_alignment, load_runtime_rules
import test_v0519_adapted_dialogue as legacy_dialogue_tests
from shortdrama_mcp.service import canonical_storyboard_sha256, _stable_json_bytes
from shortdrama_mcp.storage import sha256_bytes


def example(chapter: int = 46) -> dict:
    if chapter == 46:
        segments = [
            ("府医在湖边先确认孩子能自主呼吸，又询问得昌救人后做过哪些处置。", "given_dialogue", "府医", "救人后做过哪些处置？"),
            ("得知没有采用倒悬、强行喂水之类的偏法，", "given_dialogue", "得昌", "没有倒悬，也没有强行喂水。"),
            ("他才让人缓慢移入暖轿，", "given_dialogue", "府医", "慢慢移入暖轿。"),
            ("并把途中需要观察的反应逐项交代清楚。", "event_only", "", "府医把途中需要观察的反应逐项交代清楚。"),
        ]
        unprovided = "instruction_contents_unstated"
    else:
        segments = [
            ("府医复诊时特意问孩子是否头晕、胸闷，又听了呼吸。", "given_dialogue", "府医", "你头晕、胸闷吗？"),
            ("孟子平能完整回答，", "event_only", "", "孟子平能完整回答。"),
            ("体温也尚正常。医嘱和孩子状态写进小册，照料者每隔一段时间核对一次。", "non_dialogue", "", ""),
        ]
        unprovided = "answer_contents_unstated"
    ep = f"EP{chapter - 13:02d}"
    result = {"schema_version": "R10.1", "source_lock": {"adapted_dialogue_contract": "source-fact-dialogue-v1"},
              "prevention_evidence": {"source_ledger": []}, "shots": []}
    entries, partition, cursor = [], [], 0
    full = "".join(s[0] for s in segments)
    pid = f"CH{chapter}-P04" if chapter == 46 else "CH47-P02"
    for index, (text, kind, speaker, dialogue) in enumerate(segments, 1):
        sid, aid, uid = f"{ep}-{index:03d}", f"ADP-{chapter}-{index}", f"{pid}-S{index}"
        shot = {"镜头编号": sid, "场景连续性ID": "test-scene", "音色": "基础声线：中年男声。" if speaker else "无对白，不适用。",
                "画面内容": f"{speaker}说道：“{dialogue}”" if speaker else "交代者面部在画外，听者保持倾听；不示范未给的医嘱步骤。",
                "心理音轨": f"叙述旁白：“{dialogue}”起止0.5—5.0秒；无口型，不生成字幕。" if kind == "event_only" else "无。",
                "光影": "人物与纸面清楚", "光源来源": "窗外漫射日光", "光源方向": "左前方", "色温倾向": "自然微暖"}
        if chapter == 47 and kind == "event_only":
            shot["画面内容"] = "孩子应答时面部在画外，府医听完后停笔；不呈现具体答语。"
        if kind == "non_dialogue":
            shot["画面内容"] = "记录者将状态写进小册，照料者核对记录；没有可辨对白。"
        result["shots"].append(shot)
        result["prevention_evidence"]["source_ledger"].append({"source_id": uid, "source_type": "原文叙述", "source_text": text, "speaker": speaker or "未给可听内容", "treatment": "保留", "target_shots": [sid], "authorization": "源文忠实改编"})
        entry = {"adaptation_id": aid, "source_ledger_index": index, "source_unit_id": uid, "source_text": text,
                 "dialogue_affordance": "必须转对白" if speaker else "不宜转对白", "treatment": "原文事实转化对白" if speaker else "可视动作",
                 "speaker": speaker, "adapted_text": dialogue if speaker else "", "target_shots": [sid], "fact_atoms": [text],
                 "speaker_basis": "仅使用原文明确的问答参与者；未知内容不分配新说话人。", "authorization": "源文忠实改编", "plot_change": False,
                 "new_plot_facts": [], "entailment_status": "entailed_by_source", "adaptation_reason": "保留已给事实，未给内容保持边界。",
                 "scene_semantics": "叙述性会话场景" if kind != "non_dialogue" else "非会话叙述",
                 "communication_functions": ["问答"] if kind != "non_dialogue" else [],
                 "exchange_participants": ["府医", "接收或回应者"] if kind != "non_dialogue" else [],
                 "speech_necessity_basis": "已给交流内容还原；未给答语只呈现应答事件，不虚构。"}
        if kind == "event_only":
            entry.update(dialogue_affordance=AFFORDANCE, treatment=TREATMENT, entailment_status=ENTAILMENT)
            entry["source_content_limit"] = {"contract": CONTRACT, "partition_id": pid, "source_text_sha256": text_sha256(text),
                "unprovided_content_kind": unprovided,
                "narration": {"shot_id": sid, "field": "心理音轨", "text": dialogue, "field_sha256": text_sha256(shot["心理音轨"])},
                "action": {"shot_id": sid, "field": "画面内容", "field_sha256": text_sha256(shot["画面内容"])}}
        entries.append(entry)
        partition.append({"source_ledger_index": index, "source_unit_id": uid, "char_range": [cursor, cursor + len(text)], "kind": kind, "adaptation_id": aid})
        cursor += len(text)
    result["prevention_evidence"]["dialogue_adaptation_audit"] = {
        "contract": "source-fact-dialogue-v1", "scope_episode_ids": [ep], "candidate_unit_count": len(entries), "reviewed_unit_count": len(entries),
        "adapted_dialogue_count": sum(bool(e["speaker"]) for e in entries),
        "conversation_scene_count": sum(e["scene_semantics"] != "非会话叙述" for e in entries), "scene_classification_count": len(entries),
        "coverage_percent": 100, "ordered_entry_sha256": _stable_sha256([e["adaptation_id"] for e in entries]), "entries": entries,
        "source_content_partitions": [{"partition_id": pid, "source_text": full, "source_text_sha256": text_sha256(full), "segments": partition}]}
    return result


def protected_parent_example() -> dict:
    obj = example(47)
    evidence = obj["prevention_evidence"]
    audit = evidence["dialogue_adaptation_audit"]
    text = "头晕吗？胸口觉得闷吗？"
    source = f"府医问：“{text}”"
    evidence["source_ledger"][0]["source_text"] = source
    obj["shots"][0]["画面内容"] = f"府医说道：“{text}”"
    obj["protected_dialogue"] = [{"occurrence_id": "CH47-Q01", "speaker": "府医", "text": text, "target_shots": ["EP34-001"]}]
    audit["entries"].pop(0)
    audit.update(candidate_unit_count=2, reviewed_unit_count=2, adapted_dialogue_count=0, conversation_scene_count=1, scene_classification_count=2)
    audit["ordered_entry_sha256"] = _stable_sha256([e["adaptation_id"] for e in audit["entries"]])
    parent = audit["source_content_partitions"][0]
    parent["segments"][0].update(kind="protected_dialogue", protected_occurrence_id="CH47-Q01")
    parent["segments"][0].pop("adaptation_id")
    cursor = 0
    for segment, row in zip(parent["segments"], evidence["source_ledger"]):
        segment["char_range"] = [cursor, cursor + len(row["source_text"])]
        cursor += len(row["source_text"])
    parent["source_text"] = "".join(row["source_text"] for row in evidence["source_ledger"])
    parent["source_text_sha256"] = text_sha256(parent["source_text"])
    return obj


class SourceContentLimitTests(unittest.TestCase):
    def setUp(self):
        self.obj = example()
        self.catalog = _catalog_map(load_runtime_rules())

    def faults(self):
        return _validate_adapted_dialogue_audit(self.obj, self.obj["shots"], self.catalog)

    def entry(self):
        return next(e for e in self.obj["prevention_evidence"]["dialogue_adaptation_audit"]["entries"] if e["treatment"] == TREATMENT)

    def test_real_ch46_event_structure_passes_but_entailment_stays_pending(self):
        self.assertEqual(self.faults(), [])
        proof = inspect_source_content_limits(self.obj, self.obj["shots"])
        self.assertEqual(proof["obligations"][0]["source_state"], "pending_independent")
        self.assertEqual(self.entry()["entailment_status"], ENTAILMENT)

    def test_real_ch47_answer_and_given_medical_question(self):
        self.obj = example(47)
        self.assertEqual(self.faults(), [])
        self.obj["shots"][0]["画面内容"] = "府医看着孩子，没有开口。"
        self.assertTrue(self.faults())

    def test_given_question_cannot_be_relabelled_unknown(self):
        self.obj = example(47)
        audit = self.obj["prevention_evidence"]["dialogue_adaptation_audit"]
        audit["entries"][0]["treatment"] = TREATMENT
        audit["entries"][0]["source_content_limit"] = copy.deepcopy(self.entry()["source_content_limit"]) if "source_content_limit" in self.entry() else {}
        self.assertTrue(self.faults())

    def test_unknown_boolean_is_not_the_interface(self):
        self.entry()["source_content_limit"] = {"unknown": True}
        self.assertTrue(self.faults())

    def test_author_approval_flags_rejected(self):
        self.entry()["source_content_limit"]["independently_approved"] = True
        self.assertTrue(self.faults())

    def test_entailed_status_cannot_fake_unknown_detail_proof(self):
        self.entry()["entailment_status"] = "entailed_by_source"
        self.assertTrue(self.faults())

    def test_invented_speaker_rejected(self):
        self.entry()["speaker"] = "苏清柳"
        self.assertTrue(self.faults())

    def test_character_inner_speech_rejected(self):
        sh = self.obj["shots"][-1]
        sh["心理音轨"] = sh["心理音轨"].replace("叙述旁白", "苏清柳心理内声")
        self.entry()["source_content_limit"]["narration"]["field_sha256"] = text_sha256(sh["心理音轨"])
        self.assertTrue(self.faults())

    def test_source_text_tampering_rejected(self):
        self.entry()["source_text"] += "必须每刻钟看一次。"
        self.assertTrue(self.faults())

    def test_parent_source_tampering_rejected(self):
        self.obj["prevention_evidence"]["dialogue_adaptation_audit"]["source_content_partitions"][0]["source_text"] += "另外一个条件。"
        self.assertTrue(self.faults())

    def test_partition_cannot_omit_given_span(self):
        self.obj["prevention_evidence"]["dialogue_adaptation_audit"]["source_content_partitions"][0]["segments"].pop(0)
        self.assertTrue(self.faults())

    def test_partition_overlap_rejected(self):
        self.obj["prevention_evidence"]["dialogue_adaptation_audit"]["source_content_partitions"][0]["segments"][1]["char_range"][0] -= 1
        self.assertTrue(self.faults())

    def test_scope_tampering_rejected(self):
        self.obj["prevention_evidence"]["dialogue_adaptation_audit"]["scope_episode_ids"] = ["EP99"]
        self.assertTrue(self.faults())

    def test_actual_action_tampering_rejected(self):
        self.obj["shots"][-1]["画面内容"] += "府医示范额外治疗。"
        self.assertTrue(self.faults())

    def test_actual_narration_tampering_rejected(self):
        self.obj["shots"][-1]["心理音轨"] = "叙述旁白：“要每一刻钟观察。”无口型。"
        self.assertTrue(self.faults())

    def test_unbound_direct_quote_cannot_use_exception(self):
        part = self.obj["prevention_evidence"]["dialogue_adaptation_audit"]["source_content_partitions"][0]
        part["source_text"] = '府医说：“每刻钟观察。”' + part["source_text"]
        part["source_text_sha256"] = text_sha256(part["source_text"])
        self.assertTrue(self.faults())

    def test_protected_quote_parent_and_event_child_are_compatible(self):
        self.obj = protected_parent_example()
        self.assertEqual(self.faults(), [])

    def test_protected_quote_text_cannot_be_changed_in_compatible_parent(self):
        self.obj = protected_parent_example()
        self.obj["shots"][0]["画面内容"] = "府医说道：“都不头晕吧？”"
        self.assertTrue(self.faults())

    def test_protected_quote_speaker_cannot_be_changed(self):
        self.obj = protected_parent_example()
        self.obj["shots"][0]["画面内容"] = self.obj["shots"][0]["画面内容"].replace("府医说道", "苏清柳说道")
        self.assertTrue(self.faults())

    def test_summary_support_is_explicitly_narrow_not_a_chapter_whitelist(self):
        self.assertTrue(_summary_shape("先生把应知事项逐项说明完毕。", "instruction_contents_unstated"))
        self.assertTrue(_summary_shape("另一名孩子能够清楚回答。", "answer_contents_unstated"))
        self.assertFalse(_summary_shape("孩子答完全部问题。", "answer_contents_unstated"))
        self.assertFalse(_summary_shape("府医询问是否头晕。", "answer_contents_unstated"))
        self.assertFalse(_summary_shape("府医把必须每刻钟观察逐项交代清楚。", "instruction_contents_unstated"))

    def test_schema_accepts_normal_and_protected_parent_examples(self):
        from jsonschema import Draft202012Validator
        schema = json.loads((ROOT / "skills/shortdrama-storyboard/references/storyboard_schema.json").read_text(encoding="utf-8"))
        fragment_schema = schema["properties"]["prevention_evidence"]["properties"]["dialogue_adaptation_audit"]
        fragment_schema = {**fragment_schema, "$defs": schema["$defs"]}
        validator = Draft202012Validator(fragment_schema)
        for obj in [example(46), example(47), protected_parent_example()]:
            self.assertEqual(list(validator.iter_errors(obj["prevention_evidence"]["dialogue_adaptation_audit"])), [])
        changed = example(46)
        changed["prevention_evidence"]["dialogue_adaptation_audit"]["entries"][-1]["source_content_limit"]["unknown"] = True
        self.assertTrue(list(validator.iter_errors(changed["prevention_evidence"]["dialogue_adaptation_audit"])))

    def test_malformed_bindings_fail_closed(self):
        for invalid in [None, 1, [], "unknown"]:
            with self.subTest(invalid=invalid):
                changed = copy.deepcopy(self.obj)
                changed["prevention_evidence"]["dialogue_adaptation_audit"]["source_content_partitions"] = invalid
                self.assertTrue(inspect_source_content_limits(changed, changed["shots"])["issues"])


class SourceLimitIndependentTests(legacy_dialogue_tests.AdaptedDialogueIndependentAuditTests):
    def limited_submission(self, obj=None):
        self.storyboard = obj if obj is not None else example(47)
        canonical = canonical_storyboard_sha256(self.storyboard)
        shots = self.storyboard["shots"]
        pair_ids = [f'{a["镜头编号"]}->{b["镜头编号"]}' for a, b in zip(shots, shots[1:])]
        pair_hash = sha256_bytes(_stable_json_bytes(pair_ids))
        self.submission.update(canonical_sha256=canonical, reviewed_shot_count=len(shots), reviewed_adjacent_pair_count=len(pair_ids), adjacent_pair_order_sha256=pair_hash)
        self.report["quality_metrics"]["adjacent_continuity"].update(expected_pair_count=len(pair_ids), reviewed_pair_count=len(pair_ids), ordered_pair_sha256=pair_hash, pair_ids=pair_ids)
        audit = self.storyboard["prevention_evidence"]["dialogue_adaptation_audit"]
        requirements = inspect_source_content_limits(
            self.storyboard, shots, protected_alignment=_protected_dialogue_alignment(self.storyboard, shots)
        )["obligations"]
        for row in self.submission["layers"]:
            ev = [f"canonical:{canonical}"]
            if row["layer"] == "空间连续": ev = [f"pair:{x}" for x in pair_ids]
            if row["layer"] == "禁项与声音": ev += [f'field:{shots[0]["镜头编号"]}.音色']
            if row["layer"] == "机位可见性": ev += [f'field:{s["镜头编号"]}.{f}' for s in shots for f in ("光影", "光源来源", "光源方向", "色温倾向")]
            if row["layer"] == "源文对白":
                ev += [f'field:{shots[0]["镜头编号"]}.画面内容']
                ev += [f'source-unit:{e["source_unit_id"]}' for e in audit["entries"]]
                ev += [x["evidence_id"] for x in requirements]
                ev += [ref for x in requirements for ref in x["field_evidence_ids"]]
            row["evidence_ids"] = ev
        return next(row for row in self.submission["layers"] if row["layer"] == "源文对白")

    def test_exact_independent_obligation_closes_event_only_not_detail(self):
        self.limited_submission()
        report = self.attach()
        coverage = report["independent_evidence_matrix"]["source_content_limit_coverage"]
        self.assertEqual(coverage["state"], "verified_event_only")
        self.assertFalse(coverage["unprovided_content_certified"])

    def test_protected_parent_independent_evidence_remains_compatible(self):
        self.limited_submission(protected_parent_example())
        self.assertEqual(self.attach()["independent_evidence_matrix"]["source_content_limit_coverage"]["state"], "verified_event_only")

    def test_given_question_output_change_invalidates_limit_binding(self):
        self.limited_submission()
        self.storyboard["shots"][0]["画面内容"] = "府医没有询问，直接点头。"
        newhash = canonical_storyboard_sha256(self.storyboard)
        self.submission["canonical_sha256"] = newhash
        for layer in self.submission["layers"]:
            layer["evidence_ids"] = [f"canonical:{newhash}" if v.startswith("canonical:") else v for v in layer["evidence_ids"]]
        with self.assertRaisesRegex(ValueError, "source_content_limit_binding_mismatch"):
            self.attach()

    def test_protected_ledger_metadata_change_invalidates_local_binding(self):
        self.limited_submission(protected_parent_example())
        self.storyboard["prevention_evidence"]["source_ledger"][0]["authorization"] = "changed source authorization claim"
        newhash = canonical_storyboard_sha256(self.storyboard)
        self.submission["canonical_sha256"] = newhash
        for layer in self.submission["layers"]:
            layer["evidence_ids"] = [f"canonical:{newhash}" if v.startswith("canonical:") else v for v in layer["evidence_ids"]]
        with self.assertRaisesRegex(ValueError, "source_content_limit_binding_mismatch"):
            self.attach()

    def test_missing_independent_limit_reference_remains_pending(self):
        layer = self.limited_submission()
        layer["evidence_ids"] = [v for v in layer["evidence_ids"] if not v.startswith("source-limit:")]
        with self.assertRaisesRegex(ValueError, "source_content_limit_pending"):
            self.attach()

    def test_missing_actual_narration_field_rejected(self):
        layer = self.limited_submission()
        layer["evidence_ids"] = [v for v in layer["evidence_ids"] if not v.endswith(".心理音轨")]
        with self.assertRaisesRegex(ValueError, "source_content_limit_pending"):
            self.attach()

    def test_stale_binding_rejected_even_with_new_canonical_hash(self):
        self.limited_submission()
        sh = self.storyboard["shots"][1]
        sh["画面内容"] += "记录人倾听。"
        entry = self.storyboard["prevention_evidence"]["dialogue_adaptation_audit"]["entries"][1]
        entry["source_content_limit"]["action"]["field_sha256"] = text_sha256(sh["画面内容"])
        newhash = canonical_storyboard_sha256(self.storyboard)
        self.submission["canonical_sha256"] = newhash
        for layer in self.submission["layers"]:
            layer["evidence_ids"] = [f"canonical:{newhash}" if v.startswith("canonical:") else v for v in layer["evidence_ids"]]
        with self.assertRaisesRegex(ValueError, "source_content_limit_binding_mismatch"):
            self.attach()


if __name__ == "__main__":
    unittest.main()
