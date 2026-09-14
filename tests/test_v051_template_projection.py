from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

from shortdrama_mcp.materializer import (
    ASPECT_RATIO_TOKEN,
    COMPACT_SHOT_FIELDS,
    FORMAL_MASTER_FIELD_ALIASES,
    FORMAL_MERGED_ATOMIC_FIELDS,
    FORMAL_MERGED_FIELD,
    GPT_KEYFRAME_FIELD,
    KEYFRAME_FORBIDDEN_DYNAMIC_TERMS,
    LEGACY_FORMAL_MERGED_FIELD,
    MOTHER_TEMPLATE_COLUMNS,
    compile_formal_merged_field,
    compile_gpt_keyframe_instruction,
    formal_mother_template_rows,
    materialize_storyboard,
    migrate_legacy_shot,
)
from shortdrama_mcp.canonical import compact_storyboard_canonical


SKILL_SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "shortdrama-storyboard"
    / "scripts"
)
sys.path.insert(0, str(SKILL_SCRIPTS))
from validate_storyboard import project_export_row, validate_project  # noqa: E402


def compact_storyboard() -> dict:
    shot = {
        "镜头编号": "EP01-001",
        "时长": 3.0,
        "累计时长": 3.0,
        "功能类型": "关系试探",
        "场景连续性ID": "EP01-S01",
        "镜头类型": "双人中近景",
        "可见主体": "苏清柳、邹氏",
        "空间站位描述": "苏清柳站画面左中部，邹氏坐右侧主位。",
        "起始状态": "苏清柳双手垂在袖侧，视线落在邹氏面部。",
        "机位": "正厅左前方平视50mm，焦点落在苏清柳。",
        "运镜方式": "极缓慢推镜，随后跟镜至苏清柳面部。",
        "画面内容": (
            "【0.0—0.7秒】苏清柳抬眼；"
            "【0.7—2.6秒】她说道：“母亲，请见谅。”；"
            "【2.6—3.0秒】几秒后她站稳，硬切至门外。"
        ),
        "音色": "平静克制。",
        "心理音轨": "无。",
        "环境音效": "衣料与呼吸细响。",
        "环境动态": "窗纸停在向内微弯的可见瞬间。",
        "光影": "苏清柳面部保持自然明暗层次。",
        "光源来源": "右侧木格窗自然晨光。",
        "光源方向": "右上向左下斜落，暗侧弱补。",
        "色温倾向": "中性微暖，木色保持低饱和本色。",
        "生成限制": "9:16竖屏；禁止字幕、水印、人物看镜头。",
        "转场类型": "动作落点切。",
        "切镜/转场提示词": "苏清柳站稳后切至门外。",
        "参考图锚定": "CHAR-SU、CHAR-ZOU、SCENE-HALL。",
        "覆盖原文": "是",
        "原文依据类型": "直接依据",
        "原文支撑句": "苏清柳敛袖赔礼。",
    }
    return {
        "project_version": "V16.3-148-R10-StableRuntime",
        "schema_version": "R10.1",
        "workflow_profile": "R10-PG1",
        "prevention_evidence": {},
        "source_lock": {
            "output_contract": "R10-SPC-1",
            "target_model": "Seedance 2.5",
            "aspect_ratio": "9:16",
        },
        "field_order": list(COMPACT_SHOT_FIELDS),
        "total_duration": 3.0,
        "shots": [shot],
    }


class V051TemplateProjectionTests(unittest.TestCase):
    def test_multi_episode_cumulative_time_resets_per_episode(self) -> None:
        storyboard = compact_storyboard()
        shots = []
        for shot_id, scene_id, duration in (
            ("EP01-001", "EP01-S01", 2.0),
            ("EP01-002", "EP01-S01", 3.0),
            ("EP02-001", "EP02-S01", 4.0),
            ("EP02-002", "EP02-S01", 1.5),
        ):
            shot = copy.deepcopy(storyboard["shots"][0])
            shot["镜头编号"] = shot_id
            shot["场景连续性ID"] = scene_id
            shot["时长"] = duration
            shot["累计时长"] = 999
            shots.append(shot)
        storyboard["shots"] = shots

        compact = compact_storyboard_canonical(storyboard)

        self.assertEqual(
            [shot["累计时长"] for shot in compact["shots"]],
            [2, 5, 4, 5.5],
        )
        self.assertEqual(compact["total_duration"], 10.5)

    def test_skill_and_service_export_projection_are_identical(self) -> None:
        storyboard = materialize_storyboard(compact_storyboard())
        service_row = formal_mother_template_rows(
            storyboard,
            assume_compact=True,
        )[0]

        self.assertEqual(project_export_row(storyboard["shots"][0]), service_row)

    def test_new_header_and_strict_blank_line_formal_contract(self) -> None:
        row = formal_mother_template_rows(compact_storyboard())[0]

        self.assertEqual(FORMAL_MERGED_FIELD, "正式分镜母版")
        self.assertEqual(MOTHER_TEMPLATE_COLUMNS[4], "正式分镜母版")
        self.assertEqual(list(row), MOTHER_TEMPLATE_COLUMNS)
        formal = row[FORMAL_MERGED_FIELD]
        paragraphs = formal.split("\n\n")
        self.assertEqual(paragraphs[0], "镜头序号：EP01-001（3.0s；累计3.0s）")
        self.assertEqual(
            paragraphs[1],
            "镜头类型：双人中近景｜可见主体：苏清柳、邹氏",
        )
        voice_index = 2 + list(FORMAL_MERGED_ATOMIC_FIELDS).index("音色")
        self.assertEqual(paragraphs[voice_index], "音色：")
        self.assertEqual(
            paragraphs[voice_index + 1], compact_storyboard()["shots"][0]["音色"]
        )
        field_paragraphs = paragraphs[2 : voice_index + 1] + paragraphs[voice_index + 2 :]
        self.assertEqual(
            [paragraph.split("：", 1)[0] for paragraph in field_paragraphs],
            list(FORMAL_MERGED_ATOMIC_FIELDS),
        )
        self.assertNotIn("功能类型：", formal)
        self.assertNotIn("场景连续性ID：", formal)
        self.assertEqual(formal.count(ASPECT_RATIO_TOKEN), 1)

    def test_gpt_projection_uses_static_whitelist_and_explicit_aspect(self) -> None:
        shot = compact_storyboard()["shots"][0]

        gpt = compile_gpt_keyframe_instruction(shot, assume_compact=True)

        self.assertEqual(gpt.count(f"画幅：{ASPECT_RATIO_TOKEN}"), 1)
        self.assertNotIn("运镜方式：", gpt)
        self.assertNotIn("画面内容：", gpt)
        self.assertNotIn("母亲，请见谅", gpt)
        self.assertNotIn("0.0—0.7", gpt)
        for term in KEYFRAME_FORBIDDEN_DYNAMIC_TERMS:
            self.assertNotIn(term, gpt)
        for retained in (
            "镜头类型及可见主体：双人中近景；苏清柳、邹氏",
            "场景与站位：",
            "静态姿态：",
            "机位：",
            "光源来源：",
            "视觉限制：",
        ):
            self.assertIn(retained, gpt)

    def test_dynamic_language_in_static_keyframe_field_blocks_projection(self) -> None:
        for term in KEYFRAME_FORBIDDEN_DYNAMIC_TERMS:
            with self.subTest(term=term):
                shot = copy.deepcopy(compact_storyboard()["shots"][0])
                shot["起始状态"] = f"人物保持站位，{term}进入下一状态。"
                with self.assertRaisesRegex(
                    ValueError,
                    "(?:field_content_violation|keyframe_static_contract_violation)",
                ):
                    compile_gpt_keyframe_instruction(shot, assume_compact=True)

    def test_new_transition_empty_separator_or_adjacent_duplicate_blocks(self) -> None:
        bad_values = (
            "动作落稳后切；；动作落稳后切",
            "动作落稳后切；动作落稳后切",
            "动作落稳后切。动作落稳后切。",
            "甲落稳。；乙入画",
            "甲落稳；;乙入画",
            "“甲落稳”；甲落稳",
        )
        for value in bad_values:
            with self.subTest(value=value):
                storyboard = compact_storyboard()
                storyboard["shots"][0]["切镜/转场提示词"] = value
                with self.assertRaisesRegex(
                    ValueError,
                    "transition_contract_violation",
                ):
                    materialize_storyboard(storyboard)

    def test_legacy_transition_is_cleaned_and_aspect_is_normalized(self) -> None:
        storyboard = compact_storyboard()
        storyboard["schema_version"] = "R10.0"
        shot = storyboard["shots"][0]
        shot["生成限制"] = "9:16竖屏真人古装短剧截图质感；禁止字幕、水印。"
        shot["切镜/转场提示词"] = "动作落稳后切；；动作落稳后切"

        compact = materialize_storyboard(storyboard)
        migrated = compact["shots"][0]

        self.assertEqual(migrated["切镜/转场提示词"], "动作落稳后切")
        self.assertEqual(migrated["生成限制"].count(ASPECT_RATIO_TOKEN), 1)
        self.assertNotIn("截图质感", migrated["生成限制"])

    def test_every_compact_shot_receives_exact_aspect_constraint(self) -> None:
        storyboard = compact_storyboard()
        storyboard["shots"][0]["生成限制"] = "禁止字幕、水印。"

        compact = materialize_storyboard(storyboard)

        self.assertEqual(
            compact["shots"][0]["生成限制"],
            "9:16竖屏；禁止字幕、水印",
        )

    def test_formal_projection_never_emits_bgm_literal(self) -> None:
        storyboard = compact_storyboard()
        storyboard["shots"][0]["生成限制"] = "9:16竖屏；禁止BGM、字幕。"

        row = formal_mother_template_rows(storyboard)[0]

        self.assertNotIn("BGM", row[FORMAL_MERGED_FIELD])
        self.assertIn("禁止额外配乐", row[FORMAL_MERGED_FIELD])

        active_music = copy.deepcopy(storyboard["shots"][0])
        active_music["画面内容"] += "；画面内加入BGM。"
        with self.assertRaisesRegex(
            ValueError,
            "formal_master_audio_contract_violation",
        ):
            compile_formal_merged_field(active_music, assume_compact=True)

    def test_music_bans_normalize_but_affirmative_music_never_inverts(self) -> None:
        normalized_cases = {
            "禁止BGM、字幕、水印": "9:16竖屏；禁止字幕、水印；禁止额外配乐",
            "禁止字幕、BGM、水印": "9:16竖屏；禁止字幕、水印；禁止额外配乐",
            "禁止字幕、水印、额外配乐": "9:16竖屏；禁止字幕、水印；禁止额外配乐",
            "无音乐与字幕": "9:16竖屏；无字幕；禁止额外配乐",
            "禁止BGM且字幕": "9:16竖屏；禁止字幕；禁止额外配乐",
            "禁止BGM及字幕": "9:16竖屏；禁止字幕；禁止额外配乐",
            "禁止BGM以及字幕": "9:16竖屏；禁止字幕；禁止额外配乐",
            "不得BGM且不得字幕": "9:16竖屏；不得字幕；禁止额外配乐",
        }
        for value, expected in normalized_cases.items():
            with self.subTest(value=value):
                storyboard = compact_storyboard()
                storyboard["shots"][0]["生成限制"] = value
                compact = materialize_storyboard(storyboard)
                self.assertEqual(compact["shots"][0]["生成限制"], expected)
                self.assertEqual(
                    project_export_row(compact["shots"][0]),
                    formal_mother_template_rows(
                        compact,
                        assume_compact=True,
                    )[0],
                )

        active_cases = (
            "禁止字幕并加入BGM",
            "禁止人物看镜头同时播放背景音乐",
            "背景音乐低沉铺底",
            "配乐渐强烘托情绪",
            "音乐保持低音持续",
            "古琴配乐衬托氛围",
        )
        for value in active_cases:
            with self.subTest(value=value):
                storyboard = compact_storyboard()
                storyboard["shots"][0]["生成限制"] = value
                with self.assertRaisesRegex(ValueError, "audio_contract_violation"):
                    materialize_storyboard(storyboard)

    def test_music_words_inside_dialogue_or_visible_text_are_not_directives(self) -> None:
        for picture in (
            '苏清柳说道：“我不喜欢这段音乐。”',
            '她问：“背景音乐从哪里来？”',
            '匾额写着“音乐坊”。',
            "匾额写着音乐坊。",
            "牌匾可见文字：音乐坊。",
            "对白：我不喜欢音乐。",
        ):
            with self.subTest(picture=picture):
                shot = copy.deepcopy(compact_storyboard()["shots"][0])
                shot["画面内容"] = picture
                formal = compile_formal_merged_field(shot, assume_compact=True)
                self.assertIn(picture, formal)

        for picture in (
            "匾额写着音乐坊，随后播放背景音乐。",
            "对白：我不喜欢音乐；随后加入配乐。",
        ):
            with self.subTest(active_picture=picture):
                shot = copy.deepcopy(compact_storyboard()["shots"][0])
                shot["画面内容"] = picture
                with self.assertRaisesRegex(
                    ValueError,
                    "formal_master_audio_contract_violation",
                ):
                    compile_formal_merged_field(shot, assume_compact=True)

    def test_malformed_or_competing_aspect_ratios_are_blocked(self) -> None:
        for value in (
            "16:9横屏", "19:16竖屏", "09:16竖屏", "9:016竖屏",
            "90:160竖屏", "9:16.0竖屏", "9/16竖屏",
        ):
            with self.subTest(value=value):
                storyboard = compact_storyboard()
                storyboard["shots"][0]["生成限制"] = value
                with self.assertRaisesRegex(ValueError, "aspect_ratio_contract_violation"):
                    materialize_storyboard(storyboard)

        for field, value in (
            ("画面内容", "道具牌写着9:16竖屏。"),
            ("画面内容", "按16:9横屏构图。"),
            ("参考图锚定", "参考16:9横屏构图"),
            ("参考图锚定", "CHAR-A；9:16竖屏"),
        ):
            with self.subTest(field=field, value=value):
                shot = copy.deepcopy(compact_storyboard()["shots"][0])
                shot[field] = value
                projector = (
                    compile_gpt_keyframe_instruction
                    if field == "参考图锚定"
                    else compile_formal_merged_field
                )
                with self.assertRaisesRegex(ValueError, "aspect_ratio_projection_violation"):
                    projector(shot, assume_compact=True)

    def test_static_keyframe_blocks_process_variants_without_false_positives(self) -> None:
        blocked = (
            "镜头缓缓推近人物", "摄影机向后退开", "相机向左摇动",
            "镜头向右平移", "镜头缓缓拉远", "镜头变焦至面部",
            "镜头旋转半圈", "从全景移动到近景", "画面逐渐放大人物面部",
            "人物正在起身", "开始转头", "继续行走", "逐渐抬手",
            "将要离开", "帘幕正在飘动", "0.5秒后人物抬眼",
            "第1秒人物站定", "一秒后人物抬眼", "3s后人物抬眼", "3S后人物抬眼",
            "镜头持续3S", "一瞬后",
            "切至门外", "切到门外", "切换至下个场景", "画面转至厅外",
            "镜头转到窗边", "溶解至下一场", "淡出后进入", "叠化到夜景",
            "跳切到门口", "口唇连续开合", "嘴型同步说话",
        )
        for value in blocked:
            with self.subTest(value=value):
                shot = copy.deepcopy(compact_storyboard()["shots"][0])
                shot["起始状态"] = value
                with self.assertRaisesRegex(ValueError, "(?:field_content_violation|keyframe_static_contract_violation)"):
                    compile_gpt_keyframe_instruction(shot, assume_compact=True)

        allowed = (
            "妆发准备齐全", "屏风之后站着婢女", "她的脚步停在门槛内",
            "衣料褶皱清晰", "嘴唇微张的静态瞬间", "人物转向门外后停稳",
            "参考锚点CHAR-3S-A",
        )
        for value in allowed:
            with self.subTest(value=value):
                shot = copy.deepcopy(compact_storyboard()["shots"][0])
                shot["起始状态"] = value
                prompt = compile_gpt_keyframe_instruction(shot, assume_compact=True)
                self.assertIn(value, prompt)

    def test_keyframe_scrubs_dialogue_and_environment_audio(self) -> None:
        from shortdrama_mcp.materializer import _visual_only_text
        from validate_storyboard import _visual_only_clauses, project_gpt_keyframe

        shot = copy.deepcopy(compact_storyboard()["shots"][0])
        shot["起始状态"] = '甲闭口站定；“母亲，请见谅。”；【甲】第二句。'
        shot["环境动态"] = "古琴声从院内传来；雨声持续；窗纸停在内弯瞬间"
        for fn in (project_gpt_keyframe, lambda s: compile_gpt_keyframe_instruction(s, assume_compact=True)):
            with self.assertRaisesRegex(ValueError, 'keyframe_static_contract_violation: unbound_visual_text'):
                fn(shot)
        # The legacy helper's explicit cleanup route retains its old contract.
        for fn in (_visual_only_text, _visual_only_clauses):
            prompt = fn(shot["起始状态"], strip_dialogue=True) + fn(shot["环境动态"], strip_dialogue=True)
            self.assertNotIn("母亲，请见谅", prompt)
            self.assertNotIn("第二句", prompt)
            self.assertNotIn("古琴声", prompt)
            self.assertNotIn("雨声", prompt)
            self.assertIn("窗纸停在内弯瞬间", prompt)

    def test_keyframe_preserves_explicit_visual_labels_and_visible_text(self) -> None:
        shot = copy.deepcopy(compact_storyboard()["shots"][0])
        shot["参考图锚定"] = "文字造型锚：绛红衣饰固定。\n素材绑定：CHAR-A。"
        shot["空间站位描述"] = (
            "画内匾额“香雅院”清晰可见；"
            "【匾额】音乐坊；牌匾可见文字：音乐坊"
        )

        service_prompt = compile_gpt_keyframe_instruction(
            shot,
            assume_compact=True,
        )
        validator_prompt = project_export_row(shot)[GPT_KEYFRAME_FIELD]

        self.assertEqual(service_prompt, validator_prompt)
        for visible in (
            "文字造型锚：绛红衣饰固定",
            "画内匾额“香雅院”清晰可见",
            "【匾额】音乐坊",
            "牌匾可见文字：音乐坊",
        ):
            self.assertIn(visible, service_prompt)
        self.assertNotIn("声线", service_prompt)
        self.assertNotIn("古琴声", service_prompt)

    def test_timing_precision_is_hard_gated(self) -> None:
        storyboard = compact_storyboard()
        storyboard["shots"][0]["时长"] = 3.25
        storyboard["shots"][0]["累计时长"] = 3.25
        with self.assertRaisesRegex(ValueError, "0.1-second precision"):
            compact_storyboard_canonical(storyboard)
        with self.assertRaisesRegex(ValueError, "timing_precision_contract_violation"):
            compile_formal_merged_field(
                storyboard["shots"][0], assume_compact=True
            )

    def test_validator_returns_structured_projection_issues(self) -> None:
        for field, value in (
            ("生成限制", "16:9横屏"),
            ("参考图锚定", "参考16:9横屏构图"),
            ("画面内容", "按16:9横屏构图"),
        ):
            with self.subTest(field=field):
                storyboard = compact_storyboard()
                storyboard["shots"][0][field] = value
                report = validate_project(storyboard)
                self.assertEqual(report["status"], "fail")
                self.assertTrue(
                    any(
                        issue.get("template_gate_id") == "THG-01"
                        for issue in report["issues"]
                    )
                )

    def test_legacy_formal_header_is_read_only_alias(self) -> None:
        self.assertEqual(
            FORMAL_MASTER_FIELD_ALIASES,
            ("正式分镜母版", "正式分镜母版·字段合并"),
        )
        source = copy.deepcopy(compact_storyboard()["shots"][0])
        source[LEGACY_FORMAL_MERGED_FIELD] = "旧派生正文"

        migrated = migrate_legacy_shot(source, legacy=True)

        self.assertNotIn(LEGACY_FORMAL_MERGED_FIELD, migrated)
        self.assertNotIn(FORMAL_MERGED_FIELD, migrated)

    def test_cross_shot_reference_blocks_formal_projection(self) -> None:
        shot = copy.deepcopy(compact_storyboard()["shots"][0])
        shot["起始状态"] = "承接上一镜，苏清柳保持原站位。"

        with self.assertRaisesRegex(
            ValueError,
            "formal_master_context_violation",
        ):
            compile_formal_merged_field(shot, assume_compact=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
