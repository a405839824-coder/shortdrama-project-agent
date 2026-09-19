#!/usr/bin/env python3
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

from shortdrama_mcp.models import SEMANTIC_AUDIT_LAYER_NAMES  # noqa: E402
from shortdrama_mcp.delta import _build_adjacent_continuity_audit  # noqa: E402
from shortdrama_mcp.service import (  # noqa: E402
    SEMANTIC_AUDIT_CONTRACT_VERSION,
    StoryboardService,
    _stable_json_bytes,
    canonical_storyboard_sha256,
)
from shortdrama_mcp.storage import sha256_bytes  # noqa: E402
from shortdrama_mcp.audit_coverage import build_obligations, coverage_context  # noqa: E402
from semantic_audit_helpers import synthetic_coverage  # noqa: E402


class AdjacentContinuitySemanticAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.storyboard: dict[str, Any] = {
            "schema_version": "R10.1",
            "shots": [
                {
                    "镜头编号": "EP01-001", "场景连续性ID": "EP01-SC01",
                    "光影": "窗光柔和照亮面部", "光源来源": "左侧窗外晨光",
                    "光源方向": "画面左侧平视高度照向主体", "色温倾向": "中性微暖",
                },
                {
                    "镜头编号": "EP01-002", "场景连续性ID": "EP01-SC01",
                    "光影": "窗光柔和照亮面部", "光源来源": "左侧窗外晨光",
                    "光源方向": "画面左侧平视高度照向主体", "色温倾向": "中性微暖",
                },
                {
                    "镜头编号": "EP01-003", "场景连续性ID": "EP01-SC02",
                    "光影": "窗光柔和照亮面部", "光源来源": "左侧窗外晨光",
                    "光源方向": "画面左侧平视高度照向主体", "色温倾向": "中性微暖",
                },
            ],
        }
        self.pair_ids = ["EP01-001->EP01-002", "EP01-002->EP01-003"]
        self.pair_hash = sha256_bytes(_stable_json_bytes(self.pair_ids))
        canonical_hash = canonical_storyboard_sha256(self.storyboard)
        self.lighting_evidence_ids = [
            f"field:{shot['镜头编号']}.{field_name}"
            for shot in self.storyboard["shots"]
            for field_name in ("光影", "光源来源", "光源方向", "色温倾向")
        ]
        self.report: dict[str, Any] = {
            "status": "pass",
            "issues": [],
            "quality_metrics": {
                "adjacent_continuity": {
                    "expected_pair_count": 2,
                    "reviewed_pair_count": 2,
                    "coverage_percent": 100,
                    "ordered_pair_sha256": self.pair_hash,
                    "pair_ids": copy.deepcopy(self.pair_ids),
                    "failed_pair_count": 0,
                    "proof_verified": True,
                }
            },
        }
        self.submission: dict[str, Any] = {
            "canonical_sha256": canonical_hash,
            "reviewed_shot_count": 3,
            "reviewed_batch_count": 0,
            "reviewed_adjacent_pair_count": 2,
            "adjacent_pair_order_sha256": self.pair_hash,
            "layers": [
                {
                    "layer": layer,
                    "status": "pass",
                    "summary": f"{layer}已逐项复核",
                    "evidence_ids": (
                        [f"pair:{pair_id}" for pair_id in self.pair_ids]
                        if layer == "空间连续"
                        else [f"canonical:{canonical_hash}"]
                        + self.lighting_evidence_ids
                        if layer == "机位可见性"
                        else [f"canonical:{canonical_hash}"]
                    ),
                }
                for layer in SEMANTIC_AUDIT_LAYER_NAMES
            ],
            "issues": [],
            "conclusion": {"status": "pass", "summary": "八层复核通过"},
        }
        self.service = object.__new__(StoryboardService)
        self.service._validator_fingerprint = "a" * 64
        self.service._ruleset_fingerprint = "b" * 64
        self.service._runtime_fingerprint = "c" * 64
        self.service._schema_fingerprint = "d" * 64

    def attach(self, submission: dict[str, Any] | None = None) -> dict[str, Any]:
        # Synthetic declarations only: these tests isolate the existing pair,
        # lighting and voice checks while honoring the current coverage input.
        submitted = copy.deepcopy(submission or self.submission)
        submitted["coverage"] = synthetic_coverage(
            build_obligations(self.storyboard, coverage_context(self.service))
        )
        return self.service._attach_independent_evidence_matrix(
            copy.deepcopy(self.report),
            copy.deepcopy(self.storyboard),
            submitted,
        )

    def spatial_layer(self, submission: dict[str, Any]) -> dict[str, Any]:
        return next(row for row in submission["layers"] if row["layer"] == "空间连续")

    def test_exact_n_minus_one_ordered_pair_set_passes(self) -> None:
        enriched = self.attach()
        proof = enriched["independent_evidence_matrix"]["adjacent_continuity"]
        self.assertEqual(
            SEMANTIC_AUDIT_CONTRACT_VERSION,
            "semantic-audit-v9-server-owned-obligation-coverage",
        )
        self.assertEqual(proof["expected_pair_count"], 2)
        self.assertEqual(proof["reviewed_pair_count"], 2)
        self.assertEqual(proof["coverage_percent"], 100.0)
        self.assertEqual(proof["pair_ids"], self.pair_ids)
        self.assertEqual(proof["ordered_pair_sha256"], self.pair_hash)
        self.assertEqual(enriched["status"], "pass")
        lighting = enriched["independent_evidence_matrix"][
            "t2i_lighting_field_coverage"
        ]
        self.assertEqual(lighting["contract"], "t2i-static-observable-lighting-v2")
        self.assertEqual(lighting["expected_shot_count"], 3)
        self.assertEqual(lighting["expected_field_count"], 12)
        self.assertEqual(lighting["reviewed_field_count"], 12)
        self.assertEqual(lighting["evidence_ids"], self.lighting_evidence_ids)

    def test_missing_lighting_field_evidence_is_rejected(self) -> None:
        submission = copy.deepcopy(self.submission)
        layer = next(
            row for row in submission["layers"] if row["layer"] == "机位可见性"
        )
        layer["evidence_ids"].remove("field:EP01-002.光源方向")
        with self.assertRaisesRegex(ValueError, "t2i_lighting_coverage_incomplete"):
            self.attach(submission)

    def test_misordered_lighting_field_evidence_is_rejected(self) -> None:
        submission = copy.deepcopy(self.submission)
        layer = next(
            row for row in submission["layers"] if row["layer"] == "机位可见性"
        )
        first = layer["evidence_ids"].index("field:EP01-001.光影")
        layer["evidence_ids"][first], layer["evidence_ids"][first + 1] = (
            layer["evidence_ids"][first + 1], layer["evidence_ids"][first]
        )
        with self.assertRaisesRegex(ValueError, "t2i_lighting_coverage_incomplete"):
            self.attach(submission)

    def test_missing_pair_is_rejected(self) -> None:
        submission = copy.deepcopy(self.submission)
        self.spatial_layer(submission)["evidence_ids"] = [f"pair:{self.pair_ids[0]}"]
        with self.assertRaisesRegex(ValueError, "adjacent_pair_coverage_incomplete"):
            self.attach(submission)

    def test_reversed_pair_is_rejected(self) -> None:
        submission = copy.deepcopy(self.submission)
        self.spatial_layer(submission)["evidence_ids"] = [
            f"pair:{self.pair_ids[0]}",
            "pair:EP01-003->EP01-002",
        ]
        with self.assertRaisesRegex(ValueError, "pair evidence is not one canonical adjacent pair"):
            self.attach(submission)

    def test_declared_pair_count_mismatch_is_rejected(self) -> None:
        submission = copy.deepcopy(self.submission)
        submission["reviewed_adjacent_pair_count"] = 1
        with self.assertRaisesRegex(ValueError, "adjacent_pair_count_mismatch"):
            self.attach(submission)

    def test_order_hash_drift_is_rejected(self) -> None:
        submission = copy.deepcopy(self.submission)
        submission["adjacent_pair_order_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "adjacent_pair_hash_mismatch"):
            self.attach(submission)

    def test_machine_pair_manifest_is_mandatory(self) -> None:
        self.report["quality_metrics"].pop("adjacent_continuity")
        with self.assertRaisesRegex(ValueError, "adjacent_pair_machine_proof_missing"):
            self.attach()

    def test_dialogue_voice_field_evidence_is_mandatory_and_recorded(self) -> None:
        self.storyboard["shots"][0].update({
            "画面内容": "江氏说道：“好。”",
            "音色": "基础声线：青年女声。",
        })
        canonical_hash = canonical_storyboard_sha256(self.storyboard)
        self.submission["canonical_sha256"] = canonical_hash
        for layer in self.submission["layers"]:
            if layer["layer"] == "机位可见性":
                layer["evidence_ids"] = [f"canonical:{canonical_hash}"] + self.lighting_evidence_ids
            elif layer["layer"] != "空间连续":
                layer["evidence_ids"] = [f"canonical:{canonical_hash}"]
            if layer["layer"] == "禁项与声音":
                layer["evidence_ids"].append("field:EP01-001.音色")
        enriched = self.attach()
        proof = enriched["independent_evidence_matrix"]["voice_field_coverage"]
        self.assertEqual(proof["contract"], "fixed-voice-template-v1")
        self.assertEqual(proof["expected_dialogue_shot_count"], 1)
        self.assertEqual(proof["reviewed_voice_field_count"], 1)
        self.assertEqual(proof["coverage_percent"], 100.0)
        self.assertEqual(proof["evidence_ids"], ["field:EP01-001.音色"])

    def test_missing_dialogue_voice_field_evidence_is_rejected(self) -> None:
        self.storyboard["shots"][0].update({
            "画面内容": "江氏说道：“好。”",
            "音色": "基础声线：青年女声。",
        })
        canonical_hash = canonical_storyboard_sha256(self.storyboard)
        self.submission["canonical_sha256"] = canonical_hash
        for layer in self.submission["layers"]:
            if layer["layer"] == "机位可见性":
                layer["evidence_ids"] = [f"canonical:{canonical_hash}"] + self.lighting_evidence_ids
            elif layer["layer"] != "空间连续":
                layer["evidence_ids"] = [f"canonical:{canonical_hash}"]
        with self.assertRaisesRegex(ValueError, "voice_field_coverage_incomplete"):
            self.attach()

    def test_delta_rebuilds_complete_global_pair_proof(self) -> None:
        state = {
            "人物位置": "门内",
            "身体朝向": "朝桌面",
            "视线落点": "信件",
            "手持道具": "无",
            "门窗状态": "门开窗闭",
            "动作进度": "停稳",
            "光源方向": "画面左至右",
        }
        scene_rows = [
            {
                "shot_id": shot["镜头编号"],
                "scene_id": shot["场景连续性ID"],
                "start_state": copy.deepcopy(state),
                "end_state": copy.deepcopy(state),
                "transition_explanation": (
                    "切入同一时间的新房间" if index == 2 else "不适用：同场连续"
                ),
            }
            for index, shot in enumerate(self.storyboard["shots"])
        ]
        proof = _build_adjacent_continuity_audit(
            self.storyboard["shots"], scene_rows
        )
        self.assertEqual(proof["expected_pair_count"], 2)
        self.assertEqual(proof["reviewed_pair_count"], 2)
        self.assertEqual(proof["coverage_percent"], 100)
        self.assertEqual([row["pair_id"] for row in proof["pairs"]], self.pair_ids)
        self.assertEqual(proof["ordered_pair_sha256"], self.pair_hash)
        self.assertEqual(proof["pairs"][0]["boundary_type"], "same_scene")
        self.assertEqual(proof["pairs"][1]["boundary_type"], "scene_cut")


if __name__ == "__main__":
    unittest.main()
