from __future__ import annotations

import copy
import unittest

from shortdrama_mcp.canonical import (
    COMPACT_SHOT_FIELDS,
    COMPACT_TOP_LEVEL_FIELDS,
    compact_storyboard_canonical,
)


def _shot() -> dict:
    shot = {field: f"{field}-事实" for field in COMPACT_SHOT_FIELDS}
    shot.update(
        {
            "镜头编号": "EP06-004",
            "时长": 3.6,
            "累计时长": 3.6,
            "空间站位描述": "苏清柳在中央偏左，邹氏在右前景。",
            "起始状态": "苏清柳微欠身，双手已经离开茶盏。",
            "画面内容": "苏清柳整理袖口后说【苏清柳】母亲，请见谅。",
            "音色": "平静克制、温和带锋",
            "光影": "左侧晨光向右衰减",
            "光源来源": "左侧木格窗自然晨光",
            "光源方向": "左向右",
            "色温倾向": "中性微暖",
        }
    )
    return shot


def _compact() -> dict:
    return {
        "schema_version": "R10.1",
        "schema_profile": "compact_master_v1",
        "field_order": list(COMPACT_SHOT_FIELDS),
        "shots": [_shot()],
        "total_duration": 3.6,
    }


class CompactTopLevelPersistenceTests(unittest.TestCase):
    def test_registered_semantic_blocks_survive_the_persistence_boundary(self) -> None:
        storyboard = _compact()
        semantic_blocks = {
            "project_version": "V16.3-148-R10-StableRuntime",
            "workflow_profile": "R10-PG1",
            "episode": "第06集",
            "episode_id": "EP06",
            "分集": "第06集",
            "source_lock": {"本集起点": "清晨赔礼"},
            "source_ingestion": {"mode": "chapter_to_storyboard"},
            "chapter_intake": {"chapter_boundary": "CH06"},
            "chapter_map": {"chapter_id": "CH06"},
            "source_mapping": [{"source_unit_id": "SRC-1"}],
            "protected_dialogue": [{"text": "母亲，请见谅。"}],
            "cognition_matrix": [{"character": "苏清柳"}],
            "scene_state": {"scene-1": {"start": "赔礼前"}},
            "coverage": {"core_events": {"covered": 1, "total": 1}},
            "prevention_evidence": {"shot_plan": [{"shot_id": "EP06-004"}]},
            "stage_evidence": {"STAGE-A": {"status": "pass"}},
            "human_review": {"最终归档": "approved"},
            "anchors": {"character": {"苏清柳": "青衣"}},
            "character_anchors": [{"anchor_id": "CHAR-SQL"}],
            "scene_anchors": [{"anchor_id": "SC-XIANGYA"}],
            "prop_anchors": [{"anchor_id": "PROP-CUP"}],
            "costume_anchors": [{"anchor_id": "COSTUME-SQL"}],
            "project_anchors": {"aspect_ratio": "9:16"},
            "canonical_extension": {"semantic_fact": {"door": "open"}},
        }
        storyboard.update(copy.deepcopy(semantic_blocks))

        successor = compact_storyboard_canonical(storyboard)

        self.assertTrue(set(successor) <= COMPACT_TOP_LEVEL_FIELDS)
        for key, expected in semantic_blocks.items():
            self.assertEqual(successor[key], expected, key)

    def test_r101_rejects_unknown_and_generated_root_payloads(self) -> None:
        payloads = {
            "unknown_story_fact": {"meaning": "must be mapped explicitly"},
            "seedance_prompt_cache": {"EP06-004": "duplicate prompt"},
            "i2v_batches": [{"shot": "EP06-004"}],
            "derived": {"formal_master": "duplicate prose"},
            "vendor_seedance_output_cache": {"bytes": 999},
        }
        for key, value in payloads.items():
            with self.subTest(key=key):
                storyboard = _compact()
                storyboard[key] = value
                with self.assertRaisesRegex(ValueError, "compact_contract_violation"):
                    compact_storyboard_canonical(storyboard)

    def test_r101_rejects_generated_cache_nested_in_registered_extension(self) -> None:
        storyboard = _compact()
        storyboard["canonical_extension"] = {
            "semantic_fact": "preserve",
            "nested": {"i2v_prompt_cache": ["duplicate"]},
        }

        with self.assertRaisesRegex(ValueError, "top-level.canonical_extension.nested"):
            compact_storyboard_canonical(storyboard)

    def test_legacy_migration_preserves_registered_semantics_and_erases_derived(self) -> None:
        legacy = _compact()
        legacy["schema_version"] = "R10.0"
        legacy["chapter_map"] = {"chapter_id": "CH06"}
        legacy["batches"] = [{"独立执行提示词": "duplicate batch prompt"}]
        legacy["seedance_prompt_cache"] = {"EP06-004": "duplicate prompt"}
        legacy["canonical_extension"] = {
            "semantic_fact": {"door": "open"},
            "derived_cache": {"formal": "duplicate prose"},
        }

        successor = compact_storyboard_canonical(legacy)

        self.assertEqual(successor["chapter_map"], {"chapter_id": "CH06"})
        self.assertEqual(
            successor["canonical_extension"],
            {"semantic_fact": {"door": "open"}},
        )
        self.assertNotIn("batches", successor)
        self.assertNotIn("seedance_prompt_cache", successor)
        self.assertNotIn("derived_cache", str(successor))

    def test_legacy_unknown_semantic_root_requires_explicit_mapping(self) -> None:
        for field in ("unregistered_semantic_fact", "derived_character_fact"):
            with self.subTest(field=field):
                legacy = _compact()
                legacy["schema_version"] = "R10.0"
                legacy[field] = {"must_not": "silently disappear"}

                with self.assertRaisesRegex(
                    ValueError,
                    "legacy unknown semantic root fields require an explicit R10.1 mapping",
                ):
                    compact_storyboard_canonical(legacy)


if __name__ == "__main__":
    unittest.main(verbosity=2)
