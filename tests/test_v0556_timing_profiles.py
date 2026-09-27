"""Source-bound provisional timing. Synthetic controls are not calibration."""
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "skills/shortdrama-storyboard/scripts"), str(ROOT / "tests")]
import production_decisions
from author_storyboard import _render_decisions
from timing_model import _profile, resolve_profile, estimate_utterance, validate_timing, chapter_budget
from test_v0527_production_decisions import (
    action, production_fixture, performance_fixture, voice_spec, source_fixture, compile_source)

RULES = json.loads((ROOT / "skills/shortdrama-storyboard/references/rule_registry.json").read_text(encoding="utf-8"))["runtime"]
FAST = "provisional-zh-continuous-explanation-v1"
ORDINARY = "provisional-zh-ordinary-v1"


def selection(**changes):
    return {"speaker_category": "adult_supported", "delivery_class": "continuous_explanation",
            "source_use_id": "U1", "basis_fact_ids": ["F1"],
            "rationale": "合成有源成年人物解释当前安排；语义待独立复核。", **changes}


def use(text):
    return {"id": "U1", "actor": "甲", "mode": "spoken", "fact_ids": ["F1"], "text": text}


def project(text="甲" * 30, seconds=4.8):
    row = {"镜头编号": "EP90-001", "时长": seconds, "可见主体": "甲",
           "画面内容": f"甲说道：“{text}”", "音色": "自然连续解释。", "心理音轨": "无。"}
    timing = {"contract": "shot-timing-v1", "events": [], "utterances": [{
        "id": "speech", "kind": "dialogue", "speaker": "甲", "profile_id": FAST,
        "selection_evidence": selection(), "source": {"shot_id": row["镜头编号"], "dialogue_index": 0},
        "segments": [{"shot_id": row["镜头编号"], "start": 0, "end": seconds, "char_range": [0, len(text)]}]}]}
    return {"source_lock": {"output_contract": "R10-SPC-1", "target_model": "Seedance 2.5"},
            "prevention_evidence": {"audio_workflow": {"timing": timing},
                                    "source_decisions": {"uses": [use(text)]}},
            "shots": [row]}


def compiler_input(text="明日再议。", *, fast=True, restraint=False):
    graph, decisions, texts = source_fixture()
    source_text = "成年甲解释道：" + text
    texts["DOC"] = source_text
    import hashlib
    graph["sources"]["DOC"] = hashlib.sha256(source_text.encode()).hexdigest()
    graph["facts"][0]["content_text"] = text
    graph["facts"][0]["supports"] = [{"source_id": "DOC", "start": 0, "end": len(source_text), "text": source_text}]
    decisions["uses"][0]["text"] = text
    data = performance_fixture()
    data["project"]["prevention_evidence"]["source_decisions"] = copy.deepcopy(decisions)
    data["project"]["prevention_evidence"]["source_ledger"] = [{"source_id": "DOC", "source_text": source_text}]
    data["shots"][0]["source_unit_ids"] = ["DOC"]
    data["shots"][0]["editorial"]["原文支撑句"] = source_text
    spec = voice_spec()
    spec["emphasis"] = "“" + text.rstrip("。") + "”。"
    speech = action("speak", id="speech", source_use_id="U1", voice=spec, start=10, end=30)
    if fast:
        speech["timing"] = {"profile_id": FAST, "selection_evidence": selection()}
    data["shots"][0]["actions"].append(speech)
    if restraint:
        data["shots"][0]["actions"].append(action(
            "blink_restraint", id="restraint", start=10, end=30,
            reason="emphasis", utterance_id="speech", char_range=[0, len(text) - 1]))
    return data, compile_source(graph, decisions, texts)


class TimingProfileV0556Tests(unittest.TestCase):
    def resolve(self, evidence=None, **kw):
        text = kw.pop("text", "甲" * 31)
        args = dict(selection=selection() if evidence is None else evidence, kind="dialogue",
                    speaker="甲", text=text, uses={"U1": use(text)})
        args.update(kw)
        return resolve_profile(RULES, FAST, **args)

    def test_source_bound_adult_and_inference_use_central_not_fast_edge(self):
        for category in ("adult_supported", "adult_inferred"):
            p = self.resolve(selection(speaker_category=category))
            self.assertEqual(p["units_per_second"], 6.3)
            self.assertEqual(p["evidence_status"], "provisional")
            self.assertEqual(p["voice"], "unmatched")
            self.assertEqual(p["target_model"], "unmatched")
        self.assertEqual(resolve_profile(RULES)["units_per_second"], 5)

    def test_unknown_child_question_special_and_psychological_cannot_borrow(self):
        for fields in ({"speaker_category": "child"}, {"speaker_category": "unknown"},
                       {"delivery_class": "ordinary_question_or_reply"}, {"delivery_class": "special_delivery"}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.resolve(selection(**fields))
        with self.assertRaises(ValueError):
            self.resolve(kind="psychological")
        with self.assertRaises(ValueError):
            resolve_profile(RULES, FAST, default=True)
        with self.assertRaises(ValueError):
            resolve_profile(RULES, FAST)

    def test_selection_is_source_bound_not_a_label_or_number(self):
        for evidence in (selection(source_use_id="missing"), selection(basis_fact_ids=["unrelated"]),
                         selection(basis_fact_ids=[]), {**selection(), "rate": 6.9},
                         selection(source_use_id=[])):
            with self.subTest(evidence=evidence), self.assertRaises(ValueError):
                self.resolve(evidence)
        with self.assertRaises(ValueError):
            self.resolve(speaker="乙")
        with self.assertRaises(ValueError):
            self.resolve(uses={"U1": use("另一个原句")})
        self.assertEqual(self.resolve(text="简短解释")["units_per_second"], 6.3)
        self.assertEqual(self.resolve(text="长" * 80)["units_per_second"], 6.3)

    def test_affirmative_crying_and_child_baseline_conflict_but_prohibitions_do_not(self):
        for voice in ({"current_expression": "哭诉当前遭遇。"}, {"current_expression": "哭喊。"},
                      {"volume": "低吼。"}, {"breath": "喘息。"}, {"baseline": "儿童童声。"}):
            with self.subTest(voice=voice), self.assertRaises(ValueError):
                self.resolve(voice=voice)
        self.assertEqual(self.resolve(voice={"prohibited_tones": ["哭腔", "喊叫"],
                                            "current_expression": "不带哭腔，自然解释。"})["id"], FAST)

    def test_asr_is_not_measured_even_with_self_reported_labels(self):
        for changes in ({"evidence_status": "measured"}, {"rate_basis": "articulation"}):
            rules = copy.deepcopy(RULES)
            p = next(p for p in rules["timing_policy"]["profiles"] if p["id"] == FAST)
            p.update(changes)
            p["evidence"].update(reviewed=True, alignment_verified=True, model_version="made-up", voice="made-up")
            with self.assertRaises(ValueError):
                _profile(rules, FAST)

    def test_normal_pauses_not_added_and_extra_pause_counted_once_in_both_estimates(self):
        p = self.resolve()
        pause = {"id": "p1", "offset": 2, "seconds": .5, "extra": True}
        plain = estimate_utterance("甲" * 30, p)
        punctuation = estimate_utterance("甲，" * 30, p)
        self.assertEqual(plain["seconds"], punctuation["seconds"])
        extra = estimate_utterance("甲" * 30, p, [pause, copy.deepcopy(pause)])
        self.assertAlmostEqual(extra["seconds"], 30 / 6.3 + .5)
        self.assertEqual(extra["reference_seconds"], [30 / 6.9 + .5, 30 / 5.8 + .5])

    def test_central_fit_with_slow_sample_risk_is_not_actual_audio_pass(self):
        data = project()
        result = validate_timing(data, data["shots"], RULES)
        self.assertEqual(result["faults"], [], result)
        risk = next(row for row in result["advisories"] if row["kind"] == "provisional_capacity_risk")
        self.assertAlmostEqual(risk["central_seconds"], 30 / 6.3)
        self.assertGreater(risk["reference_seconds"][1], 4.8)
        self.assertFalse(risk["actual_audio_verified"])
        self.assertEqual(result["metrics"]["speech_calibration_status"], "not_measured")

    def test_ordinary_same_text_keeps_five_and_each_segment_keeps_its_own_capacity(self):
        data = project("甲" * 31, 5.0)
        item = data["prevention_evidence"]["audio_workflow"]["timing"]["utterances"][0]
        self.assertEqual(validate_timing(data, data["shots"], RULES)["faults"], [])
        item["profile_id"] = ORDINARY
        item.pop("selection_evidence")
        self.assertTrue(any(f["code"] == "E-DIALOGUE-003" for f in validate_timing(data, data["shots"], RULES)["faults"]))
        item["profile_id"] = FAST
        item["selection_evidence"] = selection()
        item["segments"] = [{"shot_id": "EP90-001", "start": 0, "end": .5, "char_range": [0, 10]},
                            {"shot_id": "EP90-001", "start": .5, "end": 5, "char_range": [10, 31]}]
        self.assertTrue(any(f["code"] == "E-DIALOGUE-003" for f in validate_timing(data, data["shots"], RULES)["faults"]))

    def test_chapter_uses_bound_profile_and_retains_capacity_risk(self):
        data = project()
        result = chapter_budget([{"speaker": "甲", "text": "甲" * 30}], RULES, data)
        self.assertEqual(result["faults"], [], result)
        self.assertAlmostEqual(result["required_dialogue_seconds"], 30 / 6.3)
        self.assertIn(FAST, result["profile_ids"])
        self.assertTrue(any(row["kind"] == "provisional_capacity_risk" for row in result["advisories"]))

    def test_compiler_emits_same_profile_and_restraint_binding_without_changing_voice(self):
        data, source = compiler_input(restraint=True)
        before = copy.deepcopy(data)
        compiled = production_decisions.lower_production_decisions(data, source_compilation=source)
        canonical = _render_decisions(compiled["typed_decisions"])
        item = canonical["prevention_evidence"]["audio_workflow"]["timing"]["utterances"][0]
        self.assertEqual(item["profile_id"], FAST)
        self.assertEqual(item["selection_evidence"], selection())
        result = validate_timing(canonical, canonical["shots"], RULES)
        self.assertEqual(result["faults"], [], result)
        self.assertEqual(result["metrics"]["utterances"][0]["profile_id"], FAST)
        self.assertIn(before["shots"][0]["actions"][-2]["voice"]["baseline"], canonical["shots"][0]["音色"])
        self.assertEqual(data, before)
        encoded = json.dumps(compiled["derivation"], ensure_ascii=False)
        self.assertIn(FAST, encoded)
        bindings = [a["performance_binding"] for s in compiled["derivation"]["shots"]
                    for a in s["actions"] if a.get("performance_binding")]
        self.assertTrue(bindings)
        self.assertTrue(all(b["profile_id"] == FAST for b in bindings))

    def test_compiler_new_profile_is_actually_used_for_capacity_and_legacy_keeps_default(self):
        # 11 units in 2 seconds fit 6.3 but cannot fit the old 5 profile.
        text = "甲乙丙丁戊己庚辛壬癸甲。"
        data, source = compiler_input(text)
        compiled = production_decisions.lower_production_decisions(data, source_compilation=source)
        self.assertEqual(compiled["typed_decisions"]["project"]["prevention_evidence"]["audio_workflow"]["timing"]["utterances"][0]["profile_id"], FAST)
        data["shots"][0]["actions"][-1].pop("timing")
        with self.assertRaisesRegex(production_decisions.ProductionDecisionError, "audio alignment"):
            production_decisions.lower_production_decisions(data, source_compilation=source)
        old, old_source = compiler_input(fast=False)
        old_compiled = production_decisions.lower_production_decisions(old, source_compilation=old_source)
        self.assertEqual(old_compiled["typed_decisions"]["project"]["prevention_evidence"]["audio_workflow"]["timing"]["utterances"][0]["profile_id"], ORDINARY)

    def test_same_sample_backcalculation_is_not_holdout_validation(self):
        totals = sum(n / rate for n, rate in ((47, 6.3), (5, 5), (70, 6.3), (89, 6.3)))
        self.assertAlmostEqual(totals, 33.698412698412696)
        self.assertLess(47 / 6.3, 7.62)
        p = self.resolve()
        self.assertFalse(p["evidence"]["reviewed"])
        self.assertFalse(p["evidence"]["alignment_verified"])

    def test_compiler_rejects_ineligible_and_unbound_selection_at_actual_entry(self):
        for change in ({"speaker_category": "unknown"}, {"speaker_category": "child"},
                       {"delivery_class": "ordinary_question_or_reply"},
                       {"delivery_class": "special_delivery"}, {"basis_fact_ids": ["not-this-source"]}):
            data, source = compiler_input()
            data["shots"][0]["actions"][-1]["timing"]["selection_evidence"].update(change)
            with self.subTest(change=change), self.assertRaises(production_decisions.ProductionDecisionError):
                production_decisions.lower_production_decisions(data, source_compilation=source)
        for choice in (None, {"profile_id": FAST}, {"profile_id": FAST, "selection_evidence": None},
                       {"profile_id": FAST, "selection_evidence": selection(), "rate": 6.9}):
            data, source = compiler_input()
            data["shots"][0]["actions"][-1]["timing"] = choice
            with self.subTest(choice=choice), self.assertRaises(production_decisions.ProductionDecisionError):
                production_decisions.lower_production_decisions(data, source_compilation=source)

    def test_compiler_rejects_global_default_and_nondialogue_borrowing(self):
        data, source = compiler_input(fast=False)
        data["project"]["prevention_evidence"]["audio_workflow"]["timing"]["default_profile_id"] = FAST
        with self.assertRaisesRegex(production_decisions.ProductionDecisionError, "逐发言"):
            production_decisions.lower_production_decisions(data, source_compilation=source)
        for kind in ("narrate", "inner_voice"):
            data, source = compiler_input()
            data["shots"][0]["actions"][-1]["kind"] = kind
            with self.subTest(kind=kind), self.assertRaises(production_decisions.ProductionDecisionError):
                production_decisions.lower_production_decisions(data, source_compilation=source)

    def test_actual_and_canonical_voice_cannot_contradict_faster_selection(self):
        data, source = compiler_input()
        data["shots"][0]["actions"][-1]["voice"]["current_expression"] = "哭诉，意在确认商议时间。"
        with self.assertRaisesRegex(production_decisions.ProductionDecisionError, "发声条件"):
            production_decisions.lower_production_decisions(data, source_compilation=source)
        data, source = compiler_input()
        canonical = _render_decisions(production_decisions.lower_production_decisions(data, source_compilation=source)["typed_decisions"])
        canonical["shots"][0]["音色"] = canonical["shots"][0]["音色"].replace(
            "当前表达：平静", "当前表达：哭诉")
        checked = validate_timing(canonical, canonical["shots"], RULES)
        self.assertTrue(any("发声条件" in fault["message"] for fault in checked["faults"]), checked)
        budget = chapter_budget([{"speaker": "甲", "text": "明日再议。"}], RULES, canonical)
        self.assertTrue(any("发声条件" in fault["message"] for fault in budget["faults"]), budget)

    def test_cross_shot_continuation_cannot_borrow_fast_source_voice(self):
        from voice_sequence import build_voice_sequence
        data = project(seconds=2.4)
        first = data["shots"][0]
        spec = voice_spec()
        spec["emphasis"] = "“甲”。"
        first["音色"] = build_voice_sequence([("甲", "甲" * 30, spec)])
        second = copy.deepcopy(first)
        second.update(镜头编号="EP90-002", 画面内容="甲继续上一句，口型与承接对白一致。")
        data["shots"].append(second)
        item = data["prevention_evidence"]["audio_workflow"]["timing"]["utterances"][0]
        item["segments"] = [{"shot_id": "EP90-001", "start": 0, "end": 2.4, "char_range": [0, 15]},
                            {"shot_id": "EP90-002", "start": 0, "end": 2.4, "char_range": [15, 30]}]
        self.assertEqual(validate_timing(data, data["shots"], RULES)["faults"], [])
        for voice in (first["音色"].replace("自然", "哭泣"), "沿用上一镜音色。", "", "无对白，不适用。"):
            second["音色"] = voice
            with self.subTest(voice=voice):
                self.assertTrue(validate_timing(data, data["shots"], RULES)["faults"])
                self.assertTrue(chapter_budget([{"speaker": "甲", "text": "甲" * 30}], RULES, data)["faults"])


if __name__ == "__main__":
    unittest.main()
