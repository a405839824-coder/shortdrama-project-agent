#!/usr/bin/env python3
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server"
VALIDATOR_DIR = ROOT / "skills" / "shortdrama-storyboard" / "scripts"
for path in (SERVER, VALIDATOR_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from shortdrama_mcp.models import SEMANTIC_AUDIT_LAYER_NAMES  # noqa: E402
from shortdrama_mcp.service import (  # noqa: E402
    StoryboardService,
    _stable_json_bytes,
    canonical_storyboard_sha256,
)
from shortdrama_mcp.storage import sha256_bytes  # noqa: E402
from validate_storyboard import (  # noqa: E402
    _catalog_map,
    _stable_sha256,
    _validate_adapted_dialogue_audit,
    load_runtime_rules,
)


class AdaptedDialogueGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.shots: list[dict[str, Any]] = [
            {
                "镜头编号": "EP12-001",
                "画面内容": "苏氏翻开名册，没有开口。",
            },
            {
                "镜头编号": "EP13-001",
                "画面内容": "老管事代表说道：“夫人，要不先开始？”",
            },
        ]
        self.entry = {
            "adaptation_id": "ADP-EP13-001",
            "source_ledger_index": 2,
            "source_unit_id": "CH18-U01",
            "source_text": "有人劝她先开始。",
            "dialogue_affordance": "必须转对白",
            "treatment": "原文事实转化对白",
            "speaker": "老管事代表",
            "adapted_text": "夫人，要不先开始？",
            "target_shots": ["EP13-001"],
            "fact_atoms": ["有人劝苏清柳先开始"],
            "speaker_basis": "原文明确为在场管事中的劝说者；采用已登记代表身份，不新增人物关系。",
            "authorization": "用户明确要求本段应增加对白并同步更新模板规则。",
            "plot_change": False,
            "new_plot_facts": [],
            "entailment_status": "entailed_by_source",
            "adaptation_reason": "把可观察的劝说行为恢复为可听对白，避免机械无声手势。",
            "scene_semantics": "叙述性会话场景",
            "communication_functions": ["劝决"],
            "exchange_participants": ["老管事代表", "苏清柳"],
            "speech_necessity_basis": "劝说必须由管事向苏清柳传递理由并等待她决定，单靠动作无法完成信息交换。",
        }
        self.storyboard = {
            "source_lock": {"adapted_dialogue_contract": "source-fact-dialogue-v1"},
            "prevention_evidence": {
                "source_ledger": [
                    {
                        "source_id": "CH17-U01",
                        "source_type": "D级叙述",
                        "source_text": "苏氏翻开名册。",
                        "target_shots": ["EP12-001"],
                    },
                    {
                        "source_id": "CH18-U01",
                        "source_type": "D级叙述性言语行为",
                        "source_text": "有人劝她先开始。",
                        "target_shots": ["EP13-001"],
                    },
                ],
                "dialogue_adaptation_audit": {
                    "contract": "source-fact-dialogue-v1",
                    "scope_episode_ids": ["EP13"],
                    "candidate_unit_count": 1,
                    "reviewed_unit_count": 1,
                    "adapted_dialogue_count": 1,
                    "conversation_scene_count": 1,
                    "scene_classification_count": 1,
                    "coverage_percent": 100,
                    "ordered_entry_sha256": _stable_sha256(["ADP-EP13-001"]),
                    "entries": [copy.deepcopy(self.entry)],
                },
            },
        }
        self.catalog = _catalog_map(load_runtime_rules())

    def validate(self) -> list[dict[str, Any]]:
        return _validate_adapted_dialogue_audit(
            copy.deepcopy(self.storyboard),
            copy.deepcopy(self.shots),
            self.catalog,
        )

    def test_scope_bound_complete_mapping_passes(self) -> None:
        self.assertEqual(self.validate(), [])

    def test_mandatory_speech_cannot_be_silent_action(self) -> None:
        self.shots[1]["画面内容"] = "老管事代表抬手示意，没有开口。"
        issues = self.validate()
        self.assertTrue(issues)
        self.assertTrue(all(row["template_gate_id"] == "THG-16" for row in issues))

    def test_source_text_must_match_ledger_exactly(self) -> None:
        self.storyboard["prevention_evidence"]["dialogue_adaptation_audit"]["entries"][0][
            "source_text"
        ] = "有人建议她开始。"
        self.assertTrue(self.validate())

    def test_out_of_scope_episode_does_not_create_candidate(self) -> None:
        self.storyboard["prevention_evidence"]["source_ledger"][0].update({
            "source_text": "有人劝苏氏开始。",
            "source_type": "D级叙述性言语行为",
        })
        self.assertEqual(self.validate(), [])

    def test_negated_speech_act_does_not_force_invented_dialogue(self) -> None:
        self.shots[1]["画面内容"] = "田婆子垂下视线，没有开口。"
        self.storyboard["prevention_evidence"]["source_ledger"][1].update({
            "source_id": "CH18-U13",
            "source_text": "田婆子见状，不敢继续仗着老资格叫嚷。",
            "source_type": "D级人物反应",
        })
        audit = self.storyboard["prevention_evidence"]["dialogue_adaptation_audit"]
        non_dialogue_entry = copy.deepcopy(self.entry)
        non_dialogue_entry.update({
            "adaptation_id": "ADP-EP13-013",
            "source_ledger_index": 2,
            "source_unit_id": "CH18-U13",
            "source_text": "田婆子见状，不敢继续仗着老资格叫嚷。",
            "dialogue_affordance": "不宜转对白",
            "treatment": "可视动作",
            "speaker": "",
            "adapted_text": "",
            "fact_atoms": ["田婆子没有继续叫嚷"],
            "adaptation_reason": "原文明确否定开口，以收声后的反应表现。",
            "scene_semantics": "非会话叙述",
            "communication_functions": [],
            "exchange_participants": [],
            "speech_necessity_basis": "本单元描述的是停止说话，不应反向新增台词。",
        })
        audit.update({
            "candidate_unit_count": 1,
            "reviewed_unit_count": 1,
            "adapted_dialogue_count": 0,
            "conversation_scene_count": 0,
            "scene_classification_count": 1,
            "ordered_entry_sha256": _stable_sha256(["ADP-EP13-013"]),
            "entries": [non_dialogue_entry],
        })
        self.assertEqual(self.validate(), [])

    def test_implicit_conversation_scene_without_speech_verb_must_be_dialogue(self) -> None:
        self.storyboard["prevention_evidence"]["source_ledger"][1].update({
            "source_id": "CH18-U09",
            "source_text": "苏清柳只先确认究竟是消息没到，还是有人故意试探新主母的底线。",
            "source_type": "D级叙述",
        })
        entry = self.storyboard["prevention_evidence"]["dialogue_adaptation_audit"]["entries"][0]
        entry.update({
            "source_unit_id": "CH18-U09",
            "source_text": "苏清柳只先确认究竟是消息没到，还是有人故意试探新主母的底线。",
            "speaker": "苏清柳",
            "adapted_text": "先查清消息究竟有没有送到，再论迟到之责。",
            "fact_atoms": ["先核实消息是否送达", "暂不因一次迟到直接定罪"],
            "communication_functions": ["令应", "证判"],
            "exchange_participants": ["苏清柳", "门房与副管事"],
            "speaker_basis": "原文明确由苏清柳作出先核验再判断的处理决定。",
            "speech_necessity_basis": "苏清柳需要把核验命令传达给执行者，事件无法靠静默动作完成。",
        })
        self.shots[1]["画面内容"] = "苏清柳说道：“先查清消息究竟有没有送到，再论迟到之责。”"
        self.assertEqual(self.validate(), [])

        self.shots[1]["画面内容"] = "苏清柳翻看签名簿，没有开口。"
        self.assertTrue(self.validate())

    def test_every_in_scope_narrative_unit_requires_scene_semantic_decision(self) -> None:
        self.storyboard["prevention_evidence"]["source_ledger"].append({
            "source_id": "CH18-U10",
            "source_type": "D级叙述",
            "source_text": "召集帖昨日下午便送到各房。",
            "target_shots": ["EP13-001"],
        })
        self.assertTrue(self.validate())


class AdaptedDialogueIndependentAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.storyboard: dict[str, Any] = {
            "schema_version": "R10.1",
            "prevention_evidence": {
                "dialogue_adaptation_audit": {
                    "contract": "source-fact-dialogue-v1",
                    "ordered_entry_sha256": "b" * 64,
                    "entries": [
                        {
                            "source_unit_id": "CH18-U01",
                            "scene_semantics": "叙述性会话场景",
                            "treatment": "原文事实转化对白",
                            "target_shots": ["EP13-001"],
                        }
                    ],
                }
            },
            "shots": [
                {
                    "镜头编号": "EP13-001",
                    "场景连续性ID": "E13-S01",
                    "画面内容": "老管事代表说道：“夫人，要不先开始？”",
                    "音色": "基础声线：中年男声。",
                },
                {
                    "镜头编号": "EP13-002",
                    "场景连续性ID": "E13-S01",
                    "画面内容": "苏清柳点头。",
                    "音色": "无对白，不适用。",
                },
            ],
        }
        self.pair_ids = ["EP13-001->EP13-002"]
        self.pair_hash = sha256_bytes(_stable_json_bytes(self.pair_ids))
        canonical_hash = canonical_storyboard_sha256(self.storyboard)
        self.report = {
            "status": "pass",
            "issues": [],
            "quality_metrics": {
                "adjacent_continuity": {
                    "expected_pair_count": 1,
                    "reviewed_pair_count": 1,
                    "coverage_percent": 100,
                    "ordered_pair_sha256": self.pair_hash,
                    "pair_ids": copy.deepcopy(self.pair_ids),
                    "failed_pair_count": 0,
                    "proof_verified": True,
                }
            },
        }
        self.submission = {
            "canonical_sha256": canonical_hash,
            "reviewed_shot_count": 2,
            "reviewed_batch_count": 0,
            "reviewed_adjacent_pair_count": 1,
            "adjacent_pair_order_sha256": self.pair_hash,
            "layers": [],
            "issues": [],
            "conclusion": {"status": "pass", "summary": "八层复核通过"},
        }
        for layer in SEMANTIC_AUDIT_LAYER_NAMES:
            evidence = [f"canonical:{canonical_hash}"]
            if layer == "空间连续":
                evidence = ["pair:EP13-001->EP13-002"]
            elif layer == "禁项与声音":
                evidence.append("field:EP13-001.音色")
            elif layer == "源文对白":
                evidence.append("field:EP13-001.画面内容")
                evidence.append("source-unit:CH18-U01")
            self.submission["layers"].append({
                "layer": layer,
                "status": "pass",
                "summary": f"{layer}已复核",
                "evidence_ids": evidence,
            })
        self.service = object.__new__(StoryboardService)
        self.service._validator_fingerprint = "a" * 64

    def attach(self, submission: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.service._attach_independent_evidence_matrix(
            copy.deepcopy(self.report),
            copy.deepcopy(self.storyboard),
            copy.deepcopy(submission or self.submission),
        )

    def test_adapted_dialogue_field_evidence_is_recorded(self) -> None:
        enriched = self.attach()
        proof = enriched["independent_evidence_matrix"]["adapted_dialogue_coverage"]
        self.assertEqual(proof["expected_adapted_dialogue_count"], 1)
        self.assertEqual(proof["expected_target_shot_count"], 1)
        self.assertEqual(proof["reviewed_target_shot_count"], 1)
        self.assertEqual(proof["evidence_ids"], ["field:EP13-001.画面内容"])
        scene_proof = enriched["independent_evidence_matrix"][
            "conversation_scene_classification_coverage"
        ]
        self.assertEqual(scene_proof["expected_source_unit_count"], 1)
        self.assertEqual(scene_proof["reviewed_source_unit_count"], 1)
        self.assertEqual(scene_proof["evidence_ids"], ["source-unit:CH18-U01"])

    def test_missing_adapted_dialogue_field_evidence_is_rejected(self) -> None:
        submission = copy.deepcopy(self.submission)
        source_layer = next(
            row for row in submission["layers"] if row["layer"] == "源文对白"
        )
        source_layer["evidence_ids"] = [submission["layers"][0]["evidence_ids"][0]]
        with self.assertRaisesRegex(ValueError, "adapted_dialogue_coverage_incomplete"):
            self.attach(submission)

    def test_missing_scene_classification_evidence_is_rejected(self) -> None:
        submission = copy.deepcopy(self.submission)
        source_layer = next(
            row for row in submission["layers"] if row["layer"] == "源文对白"
        )
        source_layer["evidence_ids"] = [
            value for value in source_layer["evidence_ids"]
            if not value.startswith("source-unit:")
        ]
        with self.assertRaisesRegex(ValueError, "conversation_scene_coverage_incomplete"):
            self.attach(submission)


if __name__ == "__main__":
    unittest.main()
