#!/usr/bin/env python3
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

from shortdrama_mcp.file_io import _parse_labeled_cell  # noqa: E402
from shortdrama_mcp.materializer import (  # noqa: E402
    COMPACT_SHOT_FIELDS,
    compile_formal_merged_field,
    compile_gpt_keyframe_instruction,
    migrate_storyboard_to_compact,
)


VOICE_BLOCK = (
    "基础声线：二十余岁青年女声，中音区，声线偏薄而清亮，长期保持温润平稳的质感。\n"
    "言语行为：带试探意味的问候。\n"
    "当前表达：表面温和亲近，暗含确认自己是否迟到的试探。\n"
    "发声方式：\n"
    "音量：比日常交谈低一档，仅够近处对象听清。\n"
    "语速与节奏：前半句自然，问句略放慢，不拖长字音。\n"
    "呼吸：开口前轻吸气，句中不额外抢气。\n"
    "停顿：在“大嫂”后的逗号处短停。\n"
    "重音：明确落在原文中的“没来晚”。\n"
    "尾音：克制上抬。\n"
    "禁止语气：撒娇、讨好、夸张惊慌。"
)


def compact_shot() -> dict[str, object]:
    shot: dict[str, object] = {field: "不适用。" for field in COMPACT_SHOT_FIELDS}
    shot.update({
        "镜头编号": "EP05-001",
        "时长": 4.0,
        "累计时长": 4.0,
        "镜头类型": "中近景",
        "可见主体": "江氏",
        "音色": VOICE_BLOCK,
        "生成限制": "9:16竖屏；禁止额外配乐。",
    })
    return shot


class FormalVoiceTemplateTests(unittest.TestCase):
    def test_formal_projection_and_reverse_parse_preserve_nested_voice(self) -> None:
        formal = compile_formal_merged_field(compact_shot(), assume_compact=True)
        self.assertIn("\n\n音色：\n\n基础声线：", formal)
        parsed = _parse_labeled_cell(formal)
        self.assertEqual(parsed["音色"], VOICE_BLOCK)
        self.assertNotIn("发声方式", parsed)

    def test_compact_contract_accepts_nested_group_heading(self) -> None:
        storyboard = {
            "schema_version": "R10.1",
            "field_order": list(COMPACT_SHOT_FIELDS),
            "shots": [compact_shot()],
        }
        migrated = migrate_storyboard_to_compact(storyboard)
        self.assertEqual(migrated["shots"][0]["音色"], VOICE_BLOCK)

    def test_compact_contract_still_rejects_top_level_delivery_field(self) -> None:
        shot = copy.deepcopy(compact_shot())
        shot["发声方式"] = "旧顶层字段"
        storyboard = {
            "schema_version": "R10.1",
            "field_order": list(COMPACT_SHOT_FIELDS),
            "shots": [shot],
        }
        with self.assertRaisesRegex(ValueError, "forbids legacy"):
            migrate_storyboard_to_compact(storyboard)

    def test_gpt_keyframe_rejects_unobservable_symbolic_narration(self) -> None:
        shot = compact_shot()
        shot["光影"] = "红色代表爱情、婚姻和希望。"
        with self.assertRaisesRegex(ValueError, "keyframe_unobservable_contract_violation"):
            compile_gpt_keyframe_instruction(shot, assume_compact=True)


if __name__ == "__main__":
    unittest.main()
