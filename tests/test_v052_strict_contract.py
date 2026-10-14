from __future__ import annotations

import unittest

from shortdrama_mcp.canonical import COMPACT_SHOT_FIELDS, compact_storyboard_canonical
from shortdrama_mcp.materializer import (
    MOTHER_TEMPLATE_COLUMNS,
    compile_formal_merged_field,
    compile_gpt_keyframe_instruction,
    formal_mother_template_rows,
)


def strict_shot(shot_id: str, duration: float, cumulative: float) -> dict:
    shot = {field: f"{field}-{shot_id}" for field in COMPACT_SHOT_FIELDS}
    shot.update(
        {
            "镜头编号": shot_id,
            "时长": duration,
            "累计时长": cumulative,
            "镜头类型": "双人中近景",
            "可见主体": "江氏、邹氏",
            "空间站位描述": "江氏在画面左前，邹氏在右后主位。",
            "起始状态": "江氏侧身看向邹氏，邹氏双手搭在扶手。",
            "机位": "平视50mm固定机位。",
            "运镜方式": "固定镜头。",
            "画面内容": (
                f"【0.0—0.7秒】江氏转向邹氏。"
                f"【0.7—{duration - 0.4:.1f}秒】江氏看着邹氏说道：“请大嫂放心。”"
                "口型与对白一致；邹氏晚半拍抬眼。"
                f"【{duration - 0.4:.1f}—{duration:.1f}秒】江氏停稳，邹氏保持回望。"
            ),
            "音色": "基础声线：年龄感未知，中音区，厚薄适中，明暗中性，常态温和。\n言语行为：安抚。\n当前表达：温和，意在回应关切。\n发声方式：\n音量：轻声。\n语速与节奏：平稳。\n呼吸：稳定。\n停顿：句中短停。\n重音：‘放心’。\n尾音：短收，末字轻收。\n禁止语气：喊叫、嘲讽。",
            "心理音轨": "无。",
            "环境音效": "近处衣料轻响，对白时底声避让。",
            "环境动态": "窗纸轻动。",
            "光影": "人物面部明暗可读，衣料高光克制，暗部保留木构材质。",
            "光源来源": "画面右侧木格窗晨光，左前浅墙弱反射补光。",
            "光源方向": "主光由画面右上向左下斜落，暗侧弱补并向室内纵深衰减。",
            "色温倾向": "前景中性微暖、后景略冷，肤色与木构衣料保持低饱和本色。",
            "生成限制": "禁止字幕、水印、现代物件、人物看镜头、无来源光。",
            "转场类型": "对白落点切。",
            "切镜/转场提示词": "江氏停稳、邹氏完成回望后切。",
        }
    )
    return shot


class StrictContractServerTests(unittest.TestCase):
    def test_exact_header_formal_layout_and_visual_only_projection(self) -> None:
        shot = strict_shot("EP08-001", 3.0, 3.0)
        formal = compile_formal_merged_field(shot, assume_compact=True)
        keyframe = compile_gpt_keyframe_instruction(shot, assume_compact=True)

        self.assertEqual(MOTHER_TEMPLATE_COLUMNS[4], "正式分镜母版")
        self.assertTrue(
            formal.startswith(
                "镜头序号：EP08-001（3.0s；累计3.0s）\n\n"
                "镜头类型：双人中近景｜江氏、邹氏"
            )
        )
        expected_order = (
            "空间站位描述",
            "起始状态",
            "机位",
            "运镜方式",
            "画面内容",
            "音色",
            "心理音轨",
            "环境音效",
            "环境动态",
            "生成限制",
            "转场类型",
            "切镜/转场提示词",
        )
        positions = [formal.index(f"\n\n{field}：") for field in expected_order]
        self.assertEqual(positions, sorted(positions))
        row = formal_mother_template_rows({"shots": [shot]}, assume_compact=True)[0]
        self.assertEqual(len(row), 11)
        self.assertEqual(MOTHER_TEMPLATE_COLUMNS[10], "光影")
        for field in ("光影", "光源来源", "光源方向", "色温倾向"):
            self.assertNotIn(f"\n\n{field}：", formal)
            self.assertIn(f"{field}：{shot[field]}", row["光影"])
        for forbidden in ("【0.0—", "说道：", "请大嫂放心", "口型", "音量", "环境音效"):
            self.assertNotIn(forbidden, keyframe)

    def test_incomplete_baseline_is_rejected_by_current_projection(self) -> None:
        # Retain the old layout fixture's incomplete baseline as a negative case.
        # assume_compact selects an input representation, not a historical rule set.
        shot = strict_shot("EP08-001", 3.0, 3.0)
        shot["音色"] = shot["音色"].replace(
            "基础声线：年龄感未知，中音区，厚薄适中，明暗中性，常态温和。",
            "基础声线：江氏声线温和。",
            1,
        )
        with self.assertRaisesRegex(ValueError, "基础声线.*缺少必要"):
            compile_formal_merged_field(shot, assume_compact=True)

    def test_episode_local_cumulative_and_batch_total(self) -> None:
        storyboard = {
            "schema_version": "R10.1",
            "source_lock": {
                "output_contract": "R10-SPC-1",
                "target_model": "Seedance 2.5",
                "aspect_ratio": "9:16",
            },
            "shots": [
                strict_shot("EP08-001", 3.0, 999.0),
                strict_shot("EP08-002", 4.0, 999.0),
                strict_shot("EP09-001", 2.5, 999.0),
            ],
        }
        compact = compact_storyboard_canonical(storyboard)
        self.assertEqual(
            [shot["累计时长"] for shot in compact["shots"]],
            [3, 7, 2.5],
        )
        self.assertEqual(compact["total_duration"], 9.5)


if __name__ == "__main__":
    unittest.main()
