from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import shortdrama_mcp.source_dialogue as source_dialogue_module
from shortdrama_mcp.app import create_mcp
from shortdrama_mcp.config import Settings
from shortdrama_mcp.source_dialogue import (
    SOURCE_DIALOGUE_GRAMMAR_SHA256,
    SOURCE_DIALOGUE_GRAMMAR_VERSION,
    SOURCE_DIALOGUE_PARSER_VERSION,
    SourceDialogueAuthorityError,
    authority_claims_match_exactly,
    candidate_protected_dialogue_claims,
    extracted_source_text,
    missing_authoritative_occurrences,
    parse_source_dialogue_authority,
    source_dialogue_grammar_fingerprint,
    source_dialogue_grammar_manifest,
)

from test_efficiency import valid_storyboard


def _pairs(text: str, *, role: str = "novel") -> list[tuple[str, str]]:
    return [
        (row.speaker, row.text)
        for row in parse_source_dialogue_authority(text, source_role=role)
    ]


class SourceDialogueParserV6Tests(unittest.TestCase):
    def test_parser_version_is_v6(self) -> None:
        self.assertEqual(SOURCE_DIALOGUE_PARSER_VERSION, "source-dialogue-authority-v6")

    def test_grammar_fingerprint_binds_every_classification_component(self) -> None:
        base = source_dialogue_grammar_fingerprint()
        self.assertEqual(base, SOURCE_DIALOGUE_GRAMMAR_SHA256)
        self.assertEqual(len(base), 64)
        manifest = source_dialogue_grammar_manifest()
        self.assertGreaterEqual(len(manifest), 24)
        self.assertEqual(
            manifest["implementation_source_sha256"],
            hashlib.sha256(
                Path(source_dialogue_module.__file__).read_bytes()
            ).hexdigest(),
        )
        variants: set[str] = set()
        for key, original in manifest.items():
            variant = source_dialogue_grammar_fingerprint(
                component_overrides={
                    key: {"mutated_component": key, "original": original}
                }
            )
            with self.subTest(component=key):
                self.assertNotEqual(variant, base)
            variants.add(variant)
        self.assertEqual(len(variants), len(manifest))

    def test_runtime_classification_constant_mutation_changes_grammar_hash(self) -> None:
        base = source_dialogue_grammar_fingerprint()
        with self.assertRaises(SourceDialogueAuthorityError):
            _pairs("新标签：“甲。”")
        with mock.patch.object(
            source_dialogue_module,
            "_NON_DIALOGUE_LABELS",
            {*source_dialogue_module._NON_DIALOGUE_LABELS, "新标签"},
        ):
            self.assertNotEqual(source_dialogue_grammar_fingerprint(), base)
            self.assertEqual(_pairs("新标签：“甲。”"), [])

        with mock.patch.object(
            source_dialogue_module,
            "_VISUAL_LABEL_ALIASES",
            {*source_dialogue_module._VISUAL_LABEL_ALIASES, "hud"},
        ):
            self.assertNotEqual(source_dialogue_grammar_fingerprint(), base)
            self.assertEqual(_pairs("HUD：静默。", role="script"), [])

        with self.assertRaises(SourceDialogueAuthorityError):
            _pairs("王先生说：“甲。”")
        with mock.patch.object(
            source_dialogue_module,
            "_ROLE_SUFFIXES",
            (*source_dialogue_module._ROLE_SUFFIXES, "先生"),
        ):
            self.assertNotEqual(source_dialogue_grammar_fingerprint(), base)
            self.assertEqual(_pairs("王先生说：“甲。”"), [("王先生", "甲。")])

    def test_grammar_fingerprint_is_stable_across_python_hash_seeds(self) -> None:
        server_dir = Path(source_dialogue_module.__file__).resolve().parents[1]
        fingerprints: set[str] = set()
        for seed in ("0", "1", "7", "42", "random"):
            environment = os.environ.copy()
            environment["PYTHONHASHSEED"] = seed
            environment["PYTHONPATH"] = os.pathsep.join(
                filter(
                    None,
                    (str(server_dir), environment.get("PYTHONPATH", "")),
                )
            )
            value = subprocess.check_output(
                [
                    sys.executable,
                    "-c",
                    (
                        "from shortdrama_mcp.source_dialogue import "
                        "SOURCE_DIALOGUE_GRAMMAR_SHA256; "
                        "print(SOURCE_DIALOGUE_GRAMMAR_SHA256)"
                    ),
                ],
                env=environment,
                text=True,
            ).strip()
            fingerprints.add(value)
        self.assertEqual(fingerprints, {SOURCE_DIALOGUE_GRAMMAR_SHA256})

    def test_suddenly_said_strips_adverb(self) -> None:
        self.assertEqual(
            _pairs("江氏突然说：“大嫂，没来晚吧？”"),
            [("江氏", "大嫂，没来晚吧？")],
        )

    def test_suddenly_asked_strips_adverb(self) -> None:
        self.assertEqual(
            _pairs("江氏忽然问道：“你去哪？”"),
            [("江氏", "你去哪？")],
        )

    def test_slowly_said_strips_adverb(self) -> None:
        self.assertEqual(
            _pairs("江氏缓缓说道：“慢些。”"),
            [("江氏", "慢些。")],
        )

    def test_could_not_help_shouting_strips_adverb(self) -> None:
        self.assertEqual(
            _pairs("江氏忍不住喊道：“住手！”"),
            [("江氏", "住手！")],
        )

    def test_smiling_said_strips_adverb(self) -> None:
        self.assertEqual(
            _pairs("江氏笑着说：“好。”"),
            [("江氏", "好。")],
        )

    def test_unquoted_comma_speech_is_counted(self) -> None:
        self.assertEqual(
            _pairs("江氏说，大嫂，没来晚吧？"),
            [("江氏", "大嫂，没来晚吧？")],
        )

    def test_unquoted_dash_speech_is_counted(self) -> None:
        self.assertEqual(
            _pairs("江氏说——大嫂，没来晚吧？"),
            [("江氏", "大嫂，没来晚吧？")],
        )

    def test_whitespace_or_zero_separator_speech_fails_closed(self) -> None:
        for verb in (
            "说", "说道", "问", "问道", "喊", "喊道", "叫", "叫道",
            "答", "答道", "念道", "读道",
        ):
            for gap in (" ", "  ", "\t", "\t\t", " \t", "　", "\u00a0", "\u2002"):
                with self.subTest(verb=verb, gap=repr(gap)), self.assertRaises(
                    SourceDialogueAuthorityError
                ):
                    _pairs(f"江氏{verb}{gap}甲。")
        for verb in ("说", "说道", "问", "喊", "答"):
            with self.subTest(verb=verb), self.assertRaises(
                SourceDialogueAuthorityError
            ):
                _pairs(f"江氏{verb}甲。")
        for source in (
            "江氏说 甲。李氏说：“乙。”",
            "江氏说甲。李氏说：“乙。”",
            "江氏问 甲？李氏说：“乙。”",
            "江氏喊甲！李氏说：“乙。”",
        ):
            with self.subTest(source=source), self.assertRaises(
                SourceDialogueAuthorityError
            ):
                _pairs(source)

    def test_multiple_unquoted_cues_preserve_terminal_punctuation(self) -> None:
        for source, expected in (
            ("江氏说，甲。李氏说，乙。", [("江氏", "甲。"), ("李氏", "乙。")]),
            ("江氏喊——甲！王氏问——乙？", [("江氏", "甲！"), ("王氏", "乙？")]),
            ("江氏说，甲……李氏说，乙。", [("江氏", "甲……"), ("李氏", "乙。")]),
        ):
            with self.subTest(source=source):
                self.assertEqual(_pairs(source), expected)

    def test_role_colon_newline_is_counted(self) -> None:
        self.assertEqual(
            _pairs("江氏：\n大嫂，没来晚吧？", role="script"),
            [("江氏", "大嫂，没来晚吧？")],
        )

    def test_parenthetical_role_line_is_counted(self) -> None:
        self.assertEqual(
            _pairs("（江氏）\n大嫂，没来晚吧？", role="script"),
            [("江氏", "大嫂，没来晚吧？")],
        )

    def test_script_role_inherits_across_dialogue_lines(self) -> None:
        self.assertEqual(
            _pairs("江氏：\n第一句。\n江氏：\n第二句。", role="source:script"),
            [("江氏", "第一句。"), ("江氏", "第二句。")],
        )

    def test_script_role_does_not_leak_into_following_action(self) -> None:
        for action in (
            "△李氏转身离去。",
            "▲李氏转身离去。",
            "【动作】李氏转身离去。",
            "（动作）李氏转身离去。",
            "镜头推近李氏。",
            "门外传来脚步声。",
            "李氏推门而入。",
        ):
            with self.subTest(action=action):
                if action.startswith(("△", "▲")):
                    self.assertEqual(
                        _pairs(f"江氏：\n快走。\n{action}", role="script"),
                        [("江氏", "快走。")],
                    )
                else:
                    with self.assertRaises(SourceDialogueAuthorityError):
                        _pairs(f"江氏：\n快走。\n{action}", role="script")

    def test_blank_line_closes_script_role_turn(self) -> None:
        with self.assertRaises(SourceDialogueAuthorityError):
            _pairs("江氏：\n快走。\n\n李氏推门而入。", role="script")

    def test_script_stage_direction_does_not_break_role_inheritance(self) -> None:
        self.assertEqual(
            _pairs("江氏：\n【低声】\n第二句。", role="script"),
            [("江氏", "第二句。")],
        )

    def test_inline_comma_separated_roles_are_distinct(self) -> None:
        self.assertEqual(
            _pairs("江氏：第一句，邹氏：第二句。", role="script"),
            [("江氏", "第一句"), ("邹氏", "第二句。")],
        )

    def test_inline_slash_separated_roles_are_distinct(self) -> None:
        self.assertEqual(
            _pairs("江氏：第一句／邹氏：第二句。", role="script"),
            [("江氏", "第一句"), ("邹氏", "第二句。")],
        )

    def test_inline_script_roles_split_after_terminal_or_quote(self) -> None:
        first_forms = (
            ("甲。", "甲。"),
            ("甲！", "甲！"),
            ("甲？", "甲？"),
            ("甲……", "甲……"),
            ("“甲。”", "甲。"),
            ("‘甲。’", "甲。"),
            ("「甲。」", "甲。"),
        )
        for first, expected_first in first_forms:
            source = f"江氏：{first}李氏：乙。"
            with self.subTest(source=source):
                self.assertEqual(
                    _pairs(source, role="script"),
                    [("江氏", expected_first), ("李氏", "乙。")],
                )

    def test_role_prefix_plus_quoted_suffix_is_not_truncated(self) -> None:
        self.assertEqual(
            _pairs("江氏：前半台词，“后半台词。”", role="script"),
            [("江氏", "前半台词，“后半台词。”")],
        )

    def test_explicit_reader_makes_letter_text_audible(self) -> None:
        self.assertEqual(
            _pairs("信上写着：“母亲安好。三日后归。”江氏随后念了出来。"),
            [("江氏", "母亲安好。三日后归。")],
        )

    def test_source_only_antecedent_resolves_reader_pronoun(self) -> None:
        self.assertEqual(
            _pairs("江氏拆开信。信上写着：“母亲安好。”她随后念了出来。"),
            [("江氏", "母亲安好。")],
        )

    def test_unresolved_reader_pronoun_fails_closed(self) -> None:
        with self.assertRaisesRegex(SourceDialogueAuthorityError, "pronoun"):
            _pairs("信上写着：“母亲安好。”她随后念了出来。")

    def test_silent_letter_text_is_not_dialogue(self) -> None:
        self.assertEqual(_pairs("信上写着：“母亲安好。”"), [])

    def test_subtitle_quote_is_not_dialogue(self) -> None:
        self.assertEqual(_pairs("字幕：“三日后”"), [])

    def test_note_quote_is_not_dialogue(self) -> None:
        self.assertEqual(_pairs("备注：“此处停顿”"), [])

    def test_sound_quote_is_not_dialogue(self) -> None:
        self.assertEqual(_pairs("音效：“砰”"), [])

    def test_picture_quote_is_not_dialogue(self) -> None:
        self.assertEqual(_pairs("画面：“树叶摇晃”"), [])

    def test_phone_screen_is_not_dialogue(self) -> None:
        self.assertEqual(_pairs("手机屏幕：未接来电"), [])

    def test_screen_quote_is_not_dialogue(self) -> None:
        self.assertEqual(_pairs("屏幕：“未接来电”"), [])

    def test_pronoun_speech_cue_fails_closed(self) -> None:
        with self.assertRaisesRegex(SourceDialogueAuthorityError, "pronoun"):
            _pairs("她说：“别走。”")

    def test_unknown_action_residue_fails_closed(self) -> None:
        with self.assertRaisesRegex(SourceDialogueAuthorityError, "residue"):
            _pairs("江氏沉痛地说：“别走。”")

    def test_subjectless_voice_cue_fails_closed(self) -> None:
        with self.assertRaisesRegex(SourceDialogueAuthorityError, "residue"):
            _pairs("低声说：“别走。”")

    def test_unattributed_quote_fails_closed(self) -> None:
        with self.assertRaisesRegex(SourceDialogueAuthorityError, "no source-authoritative speaker"):
            _pairs("门外传来一句：“别走。”")

    def test_unclosed_speech_quote_fails_closed(self) -> None:
        with self.assertRaisesRegex(SourceDialogueAuthorityError, "not closed"):
            _pairs("江氏说：“别走。")

    def test_two_prose_cues_on_one_line_are_both_counted(self) -> None:
        self.assertEqual(
            _pairs("江氏说：“甲。”赵氏问：“乙？”"),
            [("江氏", "甲。"), ("赵氏", "乙？")],
        )

    def test_two_dao_cues_on_one_line_are_both_counted(self) -> None:
        self.assertEqual(
            _pairs("江氏道：“甲。”赵氏道：“乙。”"),
            [("江氏", "甲。"), ("赵氏", "乙。")],
        )

    def test_parenthetical_action_cannot_become_script_speaker(self) -> None:
        with self.assertRaisesRegex(SourceDialogueAuthorityError, "parenthetical action"):
            _pairs("（冷笑）\n别怕。", role="script")

    def test_conservative_embedded_action_cue_fails_closed(self) -> None:
        with self.assertRaisesRegex(SourceDialogueAuthorityError, "ambiguous"):
            _pairs("江氏嘴角一挑，说：“别怕。”")

    def test_candidate_inventory_cannot_authorize_fake_speaker(self) -> None:
        storyboard = {
            "character_inventory": [{"name": "江氏突然"}],
            "source_ingestion": {
                "protected_dialogue": [
                    {"speaker": "江氏突然", "text": "大嫂，没来晚吧？"}
                ]
            },
        }
        occurrences = parse_source_dialogue_authority(
            "江氏突然说：“大嫂，没来晚吧？”", source_role="novel"
        )
        missing = missing_authoritative_occurrences(
            occurrences, candidate_protected_dialogue_claims(storyboard)
        )
        self.assertEqual([(row.speaker, row.text) for row in missing], [("江氏", "大嫂，没来晚吧？")])

    def test_candidate_ledger_cognition_and_anchor_are_ignored(self) -> None:
        storyboard = {
            "source_treatment_ledger": [{"speaker": "江氏突然"}],
            "cognition_matrix": [{"character": "江氏突然"}],
            "anchors": {"character": {"江氏突然": "红衣"}},
            "protected_dialogue": [
                {"speaker": "江氏", "text": "大嫂，没来晚吧？"}
            ],
        }
        self.assertEqual(
            candidate_protected_dialogue_claims(storyboard),
            [("江氏", "大嫂，没来晚吧？")],
        )

    def test_divergent_protected_dialogue_mirrors_fail_closed(self) -> None:
        row = {"speaker": "江氏", "text": "好。"}
        storyboard = {
            "protected_dialogue": [copy.deepcopy(row), copy.deepcopy(row)],
            "source_ingestion": {"protected_dialogue": [copy.deepcopy(row)]},
        }
        with self.assertRaisesRegex(
            SourceDialogueAuthorityError, "mirror_divergence"
        ):
            candidate_protected_dialogue_claims(storyboard)

    def test_matching_protected_dialogue_mirrors_do_not_double_count(self) -> None:
        row = {"speaker": "江氏", "text": "好。"}
        storyboard = {
            "protected_dialogue": [copy.deepcopy(row)],
            "source_ingestion": {"protected_dialogue": [copy.deepcopy(row)]},
        }
        self.assertEqual(
            candidate_protected_dialogue_claims(storyboard), [("江氏", "好。")]
        )

    def test_malformed_candidate_claims_fail_closed(self) -> None:
        malformed = (
            ["not-an-object"],
            [{}],
            [{"speaker": "", "text": "甲。"}],
            [{"speaker": "江氏", "text": "   "}],
        )
        for rows in malformed:
            with self.subTest(rows=rows), self.assertRaisesRegex(
                SourceDialogueAuthorityError, "malformed"
            ):
                candidate_protected_dialogue_claims(
                    {"protected_dialogue": copy.deepcopy(rows)}
                )

    def test_repeated_source_occurrence_requires_repeated_claim(self) -> None:
        occurrences = parse_source_dialogue_authority(
            "江氏说：“好。”\n江氏说：“好。”", source_role="novel"
        )
        missing = missing_authoritative_occurrences(occurrences, [("江氏", "好。")])
        self.assertEqual(len(missing), 1)

    def test_source_order_must_be_preserved(self) -> None:
        occurrences = parse_source_dialogue_authority(
            "江氏说：“甲。”\n邹氏说：“乙。”", source_role="novel"
        )
        missing = missing_authoritative_occurrences(
            occurrences, [("邹氏", "乙。"), ("江氏", "甲。")]
        )
        self.assertEqual([(row.speaker, row.text) for row in missing], [("邹氏", "乙。")])

    def test_authority_exact_match_rejects_every_extra_claim_position(self) -> None:
        occurrences = parse_source_dialogue_authority(
            "江氏说：“甲。”\n邹氏说：“乙。”", source_role="novel"
        )
        exact = [("江氏", "甲。"), ("邹氏", "乙。")]
        self.assertTrue(authority_claims_match_exactly(occurrences, exact))
        for claims in (
            [("伪角色", "伪台词。"), *exact],
            [*exact, ("伪角色", "伪台词。")],
            [exact[0], ("伪角色", "伪台词。"), exact[1]],
            [exact[0], exact[0], exact[1]],
            list(reversed(exact)),
        ):
            with self.subTest(claims=claims):
                self.assertFalse(
                    authority_claims_match_exactly(occurrences, claims)
                )

    def test_visual_prefix_does_not_hide_later_same_line_speech(self) -> None:
        for source in (
            "画面：江氏推门。江氏说：“快走。”",
            "画面：“树叶”。江氏说：“快走。”",
            "字幕：三日后。江氏说：“快走。”",
            "手机屏幕：未接来电。江氏说：“快走。”",
        ):
            with self.subTest(source=source):
                self.assertEqual(_pairs(source), [("江氏", "快走。")])

    def test_visual_prefix_does_not_hide_later_same_line_script_role(self) -> None:
        labels = (
            "画面", "字幕", "屏幕", "手机屏幕", "短信", "微信", "聊天框",
            "电视画面", "监控画面", "信件", "来信", "纸条", "公告", "告示",
            "标题", "屏显", "旁注", "OSD", "UI", "osd", "ui", "镜头", "音效",
        )
        for label in labels:
            for visual in ("静默。", "“静默。”", "‘静默。’"):
                source = f"{label}：{visual}江氏：甲。"
                with self.subTest(source=source):
                    try:
                        parsed = _pairs(source, role="script")
                    except SourceDialogueAuthorityError:
                        # A visible written-object label can make the same-line
                        # boundary conservative.  Fail-closed is safe; an
                        # empty authority inventory is not.
                        continue
                    self.assertEqual(parsed, [("江氏", "甲。")])

    def test_visual_label_family_never_becomes_a_script_speaker(self) -> None:
        for label in (
            "OSD", "UI", "osd", "ui", "手机屏幕上", "手机界面", "电视画面",
            "监控画面", "画外字幕", "字幕内容", "画面显示", "镜头内文字",
        ):
            with self.subTest(label=label):
                self.assertEqual(_pairs(f"{label}：静默。", role="script"), [])

    def test_all_supported_quote_pairs_are_exact(self) -> None:
        pairs = (
            ("“", "”"),
            ('"', '"'),
            ("'", "'"),
            ("‘", "’"),
            ("「", "」"),
            ("『", "』"),
            ("«", "»"),
            ("‹", "›"),
            ("＂", "＂"),
            ("〝", "〞"),
            ("﹁", "﹂"),
            ("﹃", "﹄"),
            ("〔", "〕"),
        )
        for opening, closing in pairs:
            with self.subTest(opening=opening, closing=closing):
                self.assertEqual(
                    _pairs(f"江氏说：{opening}快走。{closing}"),
                    [("江氏", "快走。")],
                )

    def test_html_quote_entities_fail_closed(self) -> None:
        for source in (
            "江氏说：&ldquo;快走。&rdquo;",
            "江氏说：&quot;快走。&quot;",
            "&ldquo;快走。&rdquo;",
        ):
            with self.subTest(source=source), self.assertRaisesRegex(
                SourceDialogueAuthorityError, "HTML quote entities"
            ):
                _pairs(source)

    def test_unknown_unicode_speech_separators_fail_closed(self) -> None:
        for separator in ("﹕", "∶", "︰", "；", "……", "–", "―", "‥", "、"):
            with self.subTest(separator=separator), self.assertRaises(
                SourceDialogueAuthorityError
            ):
                _pairs(f"江氏说{separator}快走。")

    def test_line_break_after_speech_cue_fails_closed(self) -> None:
        for verb, utterance in (
            ("说", "快走。"),
            ("说道", "快走。"),
            ("问", "你去哪？"),
            ("喊", "快跑！"),
            ("叫", "回来！"),
            ("念", "第一句。"),
            ("读", "第一句。"),
        ):
            with self.subTest(verb=verb), self.assertRaisesRegex(
                SourceDialogueAuthorityError, "line break"
            ):
                _pairs(f"江氏{verb}\n{utterance}")

    def test_unknown_speech_lexeme_with_colon_fails_closed(self) -> None:
        lexemes = (
            "回应", "解释", "补充", "强调", "提醒", "命令", "劝告", "央求",
            "嘀咕", "嘟囔", "低语", "斥责", "怒吼", "惊呼", "改口", "宣布", "复述",
        )
        for lexeme in lexemes:
            with self.subTest(lexeme=lexeme), self.assertRaisesRegex(
                SourceDialogueAuthorityError, "unsupported source speech cue"
            ):
                _pairs(f"江氏{lexeme}：甲。")

    def test_role_plus_unknown_predicate_and_speech_separator_fails_closed(self) -> None:
        lexemes = (
            "吩咐", "命令", "警告", "提醒", "嘱咐", "告诫", "呵斥", "怒斥",
            "反驳", "辩解", "声明", "宣布", "承诺", "威胁", "嘀咕", "低语",
            "耳语", "自言自语", "咆哮", "惊呼", "叫嚷", "插嘴", "接话",
            "附和", "应声", "哭诉", "感叹", "讥讽", "催促",
        )
        for lexeme in lexemes:
            for separator in ("，", "——", ","):
                with self.subTest(lexeme=lexeme, separator=separator), self.assertRaises(
                    SourceDialogueAuthorityError
                ):
                    _pairs(f"江氏{lexeme}{separator}快走。")

    def test_unproved_action_or_recipient_never_becomes_speaker(self) -> None:
        residues = (
            "对李氏说", "向李氏问道", "冲众人喊道", "与李氏齐声说道",
            "抿唇说道", "脱口而出说", "回应道", "解释说", "反问道",
            "摇头说道", "颔首答道", "附耳低语", "一字一顿说道",
        )
        for residue in residues:
            source = f"江氏{residue}：“甲。”"
            with self.subTest(source=source):
                try:
                    parsed = _pairs(source)
                except SourceDialogueAuthorityError:
                    continue
                self.assertEqual(parsed, [("江氏", "甲。")])

    def test_versioned_narrative_action_matrix_remains_silent(self) -> None:
        controls = (
            "江氏推门，走进屋里。", "江氏起身，走到窗前。", "江氏喝茶，望向窗外。",
            "江氏拿信，放入袖中。", "江氏转身，离开院子。", "江氏点头，继续赶路。",
            "江氏皱眉，看向李氏。", "江氏抬手，关上房门。", "江氏看书，直到天黑。",
            "江氏接过纸条，收进怀里。", "江氏推窗，清风入室。", "江氏落座，众人随之坐下。",
            "江氏执笔，写下姓名。", "江氏取伞，走入雨中。", "江氏翻页，继续读书。",
            "江氏放灯，转身离去。", "江氏开门，李氏随后进屋。", "江氏锁门，把钥匙收好。",
            "江氏穿衣，准备出门。", "江氏梳头，铜镜映出侧脸。", "江氏洗手，水面泛起涟漪。",
            "江氏倒茶，茶香渐渐散开。", "江氏端碗，走向里屋。", "江氏夹菜，放进孩子碗中。",
            "江氏拾柴，堆在灶台旁。", "江氏生火，屋内逐渐暖和。", "江氏浇花，泥土很快湿润。",
            "江氏扫地，尘土落入簸箕。", "江氏擦桌，木纹重新显露。", "江氏铺床，被角压得平整。",
            "江氏收衣，把长衫叠好。", "江氏抱起孩子，走回卧房。", "江氏牵马，沿山路缓行。",
            "江氏上车，车帘随风晃动。", "江氏下车，踩上青石路面。", "江氏登楼，脚步声渐远。",
            "江氏下楼，扶住木质栏杆。", "江氏过桥，河水从桥下流过。", "江氏绕路，避开泥泞小径。",
            "江氏进城，街市已很热闹。", "江氏出城，城门在身后合拢。", "江氏抬箱，众人随后搭手。",
            "江氏推车，车轮压过碎石。", "江氏挖土，泥块堆在一旁。", "江氏种树，根须埋入土中。",
            "江氏摘花，花瓣落在掌心。", "江氏折纸，纸鹤逐渐成形。", "江氏系带，衣襟重新合拢。",
            "江氏戴帽，帽檐遮住眉眼。", "江氏脱鞋，把鞋摆在门边。",
        )
        self.assertEqual(len(controls), 50)
        for source in controls:
            with self.subTest(source=source):
                self.assertEqual(_pairs(source), [])

    def test_script_stage_labels_cannot_self_certify_as_roles(self) -> None:
        for label in (
            "空镜", "全景", "白场", "黄昏", "蒙太奇", "计划", "景别", "台词"
        ):
            for marker in (f"（{label}）", f"{label}："):
                with self.subTest(marker=marker), self.assertRaises(
                    SourceDialogueAuthorityError
                ):
                    _pairs(f"{marker}\n风声渐紧。", role="script")

    def test_read_aloud_does_not_cross_scene_or_destroyed_object(self) -> None:
        sources = (
            "信上写着：“甲。”她把信烧了。三日后，江氏拿起诗册。江氏随后念了出来。",
            "信上写着：“甲。”\n场景：三日后书房。\n江氏念了出来。",
        )
        for source in sources:
            with self.subTest(source=source), self.assertRaisesRegex(
                SourceDialogueAuthorityError, "binding crosses"
            ):
                _pairs(source)

    def test_non_adjacent_read_requires_explicit_written_object(self) -> None:
        source = "信上写着：“甲。”中间经过一段叙述。江氏随后念了出来。"
        with self.assertRaisesRegex(SourceDialogueAuthorityError, "unconsumed prose"):
            _pairs(source)
        with self.assertRaisesRegex(SourceDialogueAuthorityError, "unconsumed prose"):
            _pairs("信上写着：“甲。”中间经过一段叙述。江氏随后将信念了出来。")

    def test_direct_read_quote_does_not_promote_different_visible_text(self) -> None:
        self.assertEqual(
            _pairs("信上写着：“甲。”江氏随后读道：“乙。”"),
            [("江氏", "乙。")],
        )

    def test_explicit_read_object_selects_compatible_visible_type(self) -> None:
        self.assertEqual(
            _pairs(
                "信上写着：“甲。”纸条上写着：“乙。”江氏将信念了出来。"
            ),
            [("江氏", "甲。")],
        )
        self.assertEqual(
            _pairs(
                "纸条上写着：“甲。”信上写着：“乙。”江氏将纸条念了出来。"
            ),
            [("江氏", "甲。")],
        )

    def test_same_kind_read_objects_require_unique_reference(self) -> None:
        with self.assertRaisesRegex(SourceDialogueAuthorityError, "uniquely select"):
            _pairs(
                "信上写着：“甲。”信上写着：“乙。”江氏将信念了出来。"
            )

    def test_post_quote_object_transfer_controls_reader_pronoun(self) -> None:
        self.assertEqual(
            _pairs(
                "江氏接过信。信上写着：“甲。”李氏抢过信。她随后将信念了出来。"
            ),
            [("李氏", "甲。")],
        )
        self.assertEqual(
            _pairs(
                "江氏接过信。信上写着：“甲。”江氏把信递给李氏。她随后将信念了出来。"
            ),
            [("李氏", "甲。")],
        )

    def test_pronoun_reader_does_not_reuse_holder_across_prior_object_boundary(self) -> None:
        source = (
            "江氏接过信。信上写着：“旧信。”江氏念了出来。"
            "信上写着：“新信。”她随后念了出来。"
        )
        with self.assertRaisesRegex(SourceDialogueAuthorityError, "object holder"):
            _pairs(source)

    def test_multiple_same_kind_holders_fail_unless_same_person(self) -> None:
        for source in (
            "江氏接过信。李氏接过信。信上写着：“甲。”她随后将信念了出来。",
            "李氏接过信。江氏接过信。信上写着：“甲。”她随后将信念了出来。",
            "江氏拿起纸条。李氏捧起纸条。纸条上写着：“甲。”她随后将纸条念了出来。",
        ):
            with self.subTest(source=source), self.assertRaisesRegex(
                SourceDialogueAuthorityError, "multiple source-proved holders"
            ):
                _pairs(source)
        self.assertEqual(
            _pairs(
                "江氏接过信。江氏展开信。信上写着：“甲。”她随后将信念了出来。"
            ),
            [("江氏", "甲。")],
        )

    def test_unknown_or_pronominal_transfer_never_falls_back_to_old_holder(self) -> None:
        gaps = (
            "她抢过信。",
            "江氏把信递给她。",
            "江氏把信交还给李氏。",
            "江氏把信塞给李氏。",
            "江氏把信转交给李氏。",
            "李氏从江氏手中接过信。",
            "李氏一把夺过信。",
            "李氏抢走信。",
            "李氏取走信。",
            "李氏捡起信。",
            "江氏把信放到李氏手里。",
            "江氏把信送到李氏手上。",
        )
        for gap in gaps:
            source = f"江氏接过信。信上写着：“甲。”{gap}她随后将信念了出来。"
            with self.subTest(gap=gap), self.assertRaises(
                SourceDialogueAuthorityError
            ):
                _pairs(source)

    def test_other_object_transfer_makes_pronoun_reader_ambiguous(self) -> None:
        with self.assertRaisesRegex(SourceDialogueAuthorityError, "different written object"):
            _pairs(
                "江氏接过信。信上写着：“甲。”纸条上写着：“乙。”"
                "李氏抢过纸条。她随后将信念了出来。"
            )

    def test_introductory_prefix_cannot_become_part_of_role_atom(self) -> None:
        prefixes = (
            "听见", "闻得", "等到", "待", "见", "恰逢", "轮到", "由", "让",
            "请", "只听", "原来", "没想到是",
        )
        roles = (
            "李氏", "王夫人", "赵小姐", "孙公子", "周嬷嬷", "吴王爷",
            "郑大人", "陈姑娘", "林将军", "何掌柜",
        )
        for prefix in prefixes:
            for role in roles:
                source = f"{prefix}{role}说：“甲。”"
                with self.subTest(prefix=prefix, role=role), self.assertRaises(
                    SourceDialogueAuthorityError
                ):
                    _pairs(source)

    def test_legal_suffixed_role_atoms_remain_supported(self) -> None:
        roles = (
            "李氏", "欧阳氏", "司马氏", "上官氏", "长孙氏", "王夫人",
            "老夫人", "赵小姐", "孙公子", "周嬷嬷", "吴王爷", "郑大人",
            "陈姑娘", "林将军", "何掌柜",
        )
        for role in roles:
            with self.subTest(role=role):
                self.assertEqual(_pairs(f"{role}说：“甲。”"), [(role, "甲。")])

    def test_text_extraction_accepts_text_payload(self) -> None:
        self.assertEqual(
            extracted_source_text({"kind": "text", "text": "江氏说：“好。”"}),
            "江氏说：“好。”",
        )

    def test_text_extraction_accepts_single_json_text_key(self) -> None:
        self.assertEqual(
            extracted_source_text({"kind": "json", "value": {"script": "江氏：好。"}}),
            "江氏：好。",
        )

    def test_text_extraction_rejects_ambiguous_json_payload(self) -> None:
        with self.assertRaisesRegex(SourceDialogueAuthorityError, "unsupported_source"):
            extracted_source_text(
                {"kind": "json", "value": {"text": "甲", "script": "乙"}}
            )

    def test_text_extraction_rejects_table_payload(self) -> None:
        with self.assertRaisesRegex(SourceDialogueAuthorityError, "unsupported_source"):
            extracted_source_text({"kind": "table", "rows": []})

    def test_delimiterless_pronoun_or_role_residue_fails_closed_atomically(self) -> None:
        sources = (
            "她说甲。",
            "她说 甲。",
            "他问你是谁？",
            "江氏沉痛地说甲。",
            "江氏沉痛地说 甲。",
            "江氏又说甲。",
            "她说甲。李氏说：\u201c乙。\u201d",
            "江氏又说甲。李氏说：\u201c乙。\u201d",
        )
        for source in sources:
            with self.subTest(source=source), self.assertRaises(
                SourceDialogueAuthorityError
            ):
                _pairs(source)

    def test_script_action_marker_excludes_action_and_parses_explicit_cue(self) -> None:
        for marker in ("△", "▲"):
            with self.subTest(marker=marker, kind="pure_action"):
                self.assertEqual(
                    _pairs(f"{marker}李氏转身离去。", role="script"), []
                )
            with self.subTest(marker=marker, kind="pure_reading_action"):
                self.assertEqual(
                    _pairs(f"{marker}江氏翻页，继续读书。", role="script"), []
                )
            with self.subTest(marker=marker, kind="explicit_cue"):
                self.assertEqual(
                    _pairs(
                        f"{marker}江氏推门。江氏说：\u201c快走。\u201d",
                        role="script",
                    ),
                    [("江氏", "快走。")],
                )

        for source in (
            "△江氏推门。江氏命令：\u201c快走。\u201d",
            "▲门外传来一句：\u201c快走。\u201d",
            "△江氏推门。江氏说：\u201c快走。",
        ):
            with self.subTest(source=source), self.assertRaises(
                SourceDialogueAuthorityError
            ):
                _pairs(source, role="script")

    def test_inline_visual_labels_segment_script_role_dialogue(self) -> None:
        cases = (
            (
                "江氏：甲。字幕：三日后。阿玉：乙。",
                [("江氏", "甲。"), ("阿玉", "乙。")],
            ),
            (
                "江氏：甲。手机屏幕：未接来电。Alice：乙。",
                [("江氏", "甲。"), ("Alice", "乙。")],
            ),
            (
                "江氏：甲。画面：树叶摇晃。李氏：乙。",
                [("江氏", "甲。"), ("李氏", "乙。")],
            ),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(_pairs(source, role="script"), expected)

    def test_inline_ah_and_latin_script_roles_are_distinct(self) -> None:
        for source, expected in (
            (
                "阿青：甲。阿玉：乙。",
                [("阿青", "甲。"), ("阿玉", "乙。")],
            ),
            (
                "Alice：甲。Bob：乙。",
                [("Alice", "甲。"), ("Bob", "乙。")],
            ),
            (
                "江氏：甲。阿青：乙。Bob：丙。",
                [("江氏", "甲。"), ("阿青", "乙。"), ("Bob", "丙。")],
            ),
        ):
            with self.subTest(source=source):
                self.assertEqual(_pairs(source, role="script"), expected)

    def test_script_role_dialogue_rejects_unclosed_or_mismatched_quotes(self) -> None:
        sources = (
            "江氏：\u201c快走。",
            "江氏：快走。\u201d",
            "江氏：\u201c甲。\u2019",
            "江氏：前半，\u201c后半。",
            "江氏：甲。字幕：\u201c三日后。",
        )
        for source in sources:
            with self.subTest(source=source), self.assertRaises(
                SourceDialogueAuthorityError
            ):
                _pairs(source, role="script")

    def test_prose_speech_cue_rejects_mismatched_nested_quotes(self) -> None:
        sources = (
            "江氏说：\u201c甲。\u2019乙。\u201d",
            "江氏说：\u201c甲。\u300d乙。\u201d",
            "江氏说：\u2018甲。\u201d乙。\u2019",
            "江氏说：\u201c甲。\u300e乙。\u201d",
        )
        for source in sources:
            with self.subTest(source=source), self.assertRaises(
                SourceDialogueAuthorityError
            ):
                _pairs(source)

    def test_malformed_dotall_visible_text_quote_fails_closed(self) -> None:
        sources = (
            "信上写着：\u201c甲。\n江氏说：\u201c乙。\u201d",
            "纸条上写着：\u201c甲。\n江氏说：乙。\u201d",
            "信中：\u201c甲。\n李氏问：\u201c乙？\u201d",
        )
        for source in sources:
            with self.subTest(source=source), self.assertRaises(
                SourceDialogueAuthorityError
            ):
                _pairs(source)

    def test_visual_prefix_unknown_or_unattributed_speech_fails_closed(self) -> None:
        sources = (
            "画面：静默。门外传来一句：\u201c快走。\u201d",
            "画面：江氏推门。江氏命令：\u201c快走。\u201d",
            "画面：江氏推门。江氏警告：快走。",
            "字幕：三日后。江氏低语：\u201c快走。\u201d",
            "手机屏幕：未接来电。江氏怒吼：\u201c快走！\u201d",
        )
        for source in sources:
            with self.subTest(source=source), self.assertRaises(
                SourceDialogueAuthorityError
            ):
                _pairs(source)

    def test_plain_han_names_are_supported_only_in_explicit_source_contexts(self) -> None:
        for source, role, expected in (
            ("苏清柳说：\u201c甲。\u201d", "novel", [("苏清柳", "甲。")]),
            ("张三问道：\u201c乙？\u201d", "novel", [("张三", "乙？")]),
            ("苏清柳：甲。", "script", [("苏清柳", "甲。")]),
            ("苏清柳：\n甲。", "script", [("苏清柳", "甲。")]),
            ("（苏清柳）\n甲。", "script", [("苏清柳", "甲。")]),
            (
                "苏清柳：甲。张三：乙。",
                "script",
                [("苏清柳", "甲。"), ("张三", "乙。")],
            ),
        ):
            with self.subTest(source=source, role=role):
                self.assertEqual(_pairs(source, role=role), expected)

    def test_plain_han_name_support_does_not_promote_stage_labels(self) -> None:
        for label in (
            "空镜", "全景", "白场", "黄昏", "蒙太奇", "计划", "景别", "台词"
        ):
            with self.subTest(label=label), self.assertRaises(
                SourceDialogueAuthorityError
            ):
                _pairs(f"{label}：\n风声渐紧。", role="script")

    def test_phone_screen_text_is_silent_until_explicitly_read(self) -> None:
        for source in (
            "手机屏幕：\u201c甲。\u201d",
            "手机屏幕上写着：\u201c甲。\u201d",
            "屏幕文字：\u201c甲。\u201d",
            "手机短信：\u201c甲。\u201d",
        ):
            with self.subTest(source=source, kind="silent"):
                self.assertEqual(_pairs(source), [])

        for source in (
            "手机屏幕上写着：\u201c甲。\u201d江氏随后念了出来。",
            "手机屏幕：\u201c甲。\u201d江氏随后将手机屏幕上的字读了出来。",
            "手机短信：\u201c甲。\u201d江氏随后将短信念了出来。",
            "微信消息：\u201c甲。\u201d江氏随后将微信消息念了出来。",
        ):
            with self.subTest(source=source, kind="read_aloud"):
                self.assertEqual(_pairs(source), [("江氏", "甲。")])

        self.assertEqual(
            _pairs("手机屏幕：\u201c甲。\u201d江氏读道：\u201c乙。\u201d"),
            [("江氏", "乙。")],
        )

    def test_phone_screen_read_aloud_requires_unique_written_object(self) -> None:
        ambiguous = (
            "信上写着：\u201c纸本。\u201d手机屏幕上写着：\u201c屏幕。\u201d江氏随后念了出来。",
            "手机屏幕上写着：\u201c甲。\u201d手机屏幕上写着：\u201c乙。\u201d"
            "江氏随后将手机屏幕上的字念了出来。",
            "手机屏幕上写着：\u201c甲。\u201d她随后念了出来。",
        )
        for source in ambiguous:
            with self.subTest(source=source), self.assertRaises(
                SourceDialogueAuthorityError
            ):
                _pairs(source)

        self.assertEqual(
            _pairs(
                "信上写着：\u201c纸本。\u201d手机屏幕上写着：\u201c屏幕。\u201d"
                "江氏随后将手机屏幕上的字念了出来。"
            ),
            [("江氏", "屏幕。")],
        )

    def test_script_role_char_offset_points_to_clean_dialogue_first_character(self) -> None:
        sources = (
            "江氏：\u201c甲。\u201d",
            "江氏：  \u201c甲。\u201d",
            "江氏：【低声】\u201c甲。\u201d",
            "江氏：  【低声】  \u201c甲。\u201d",
            "（江氏）\n\u201c甲。\u201d",
            "江氏：\n【低声】\n\u201c甲。\u201d",
        )
        for source in sources:
            with self.subTest(source=source):
                rows = parse_source_dialogue_authority(source, source_role="script")
                self.assertEqual([(row.speaker, row.text) for row in rows], [("江氏", "甲。")])
                self.assertEqual(rows[0].char_offset, source.index("甲。"))
                self.assertEqual(
                    source[rows[0].char_offset : rows[0].char_offset + len(rows[0].text)],
                    rows[0].text,
                )

    def test_same_line_cue_role_mixes_preserve_every_occurrence(self) -> None:
        cases = (
            (
                "江氏说：\u201c甲。\u201d李氏：乙。",
                [("江氏", "甲。"), ("李氏", "乙。")],
            ),
            (
                "江氏说：\u201c甲。\u201d李氏：乙。赵氏说：\u201c丙。\u201d",
                [("江氏", "甲。"), ("李氏", "乙。"), ("赵氏", "丙。")],
            ),
            (
                "字幕：三日后。江氏说：\u201c甲。\u201d李氏：乙。",
                [("江氏", "甲。"), ("李氏", "乙。")],
            ),
            (
                "△江氏推门。江氏说：\u201c甲。\u201d李氏：乙。",
                [("江氏", "甲。"), ("李氏", "乙。")],
            ),
            (
                "李氏：乙。赵氏说：\u201c丙。\u201d",
                [("李氏", "乙。"), ("赵氏", "丙。")],
            ),
            (
                "李氏：乙。赵氏说：\u201c丙。\u201d孙氏：丁。",
                [("李氏", "乙。"), ("赵氏", "丙。"), ("孙氏", "丁。")],
            ),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                rows = parse_source_dialogue_authority(source, source_role="script")
                self.assertEqual([(row.speaker, row.text) for row in rows], expected)
                self.assertEqual(
                    [row.char_offset for row in rows],
                    [source.index(text) for _speaker, text in expected],
                )

    def test_visual_role_without_supported_separator_fails_closed(self) -> None:
        sources = (
            "字幕：三日后江氏：甲。",
            "画面：静默江氏：甲。",
            "手机屏幕：未接来电江氏：甲。",
            "江氏：甲字幕：三日后。",
            "江氏：甲手机屏幕：未接来电。",
            "江氏：甲OSD：三日后。",
        )
        for source in sources:
            with self.subTest(source=source), self.assertRaises(
                SourceDialogueAuthorityError
            ):
                _pairs(source, role="script")

    def test_derived_stage_or_visual_labels_cannot_be_plain_han_names(self) -> None:
        labels = (
            "全景图", "白场戏", "黄昏时", "计划表", "景别栏", "台词栏",
            "时间轴", "时间线", "王字幕", "苏画面", "李镜头", "周场景",
            "吴动作", "陈备注", "张标题",
        )
        for label in labels:
            with self.subTest(label=label, context="script"), self.assertRaises(
                SourceDialogueAuthorityError
            ):
                _pairs(f"{label}：静默。", role="script")
            with self.subTest(label=label, context="novel"), self.assertRaises(
                SourceDialogueAuthorityError
            ):
                _pairs(f"{label}说：\u201c甲。\u201d")


class SourceDialoguePreflightTests(unittest.TestCase):
    namespace = "local-single-user"

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="shortdrama-v056-source-")
        settings = Settings(
            data_dir=Path(self.temp.name),
            public_base_url="http://127.0.0.1:8765",
            host="127.0.0.1",
            port=8765,
            deployment_mode="local",
            auth_mode="none",
            oauth_issuer=None,
            oauth_audience=None,
            oauth_jwks_url=None,
            oauth_scopes=("storyboard:read", "storyboard:write"),
            allow_local_file_urls=False,
            max_file_bytes=50 * 1024 * 1024,
        )
        self.server, self.service = create_mcp(settings)
        self._project_index = 0

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _project_and_task(self) -> tuple[str, str, str]:
        self._project_index += 1
        project_id = str(
            self.service.create_project(
                self.namespace, f"源文authority-{self._project_index}"
            ).project_id
        )
        status = self.service.agent_status(self.namespace, project_id, full_self_test=True)
        self.assertTrue(status.data["runtime_test_certificate_valid"])
        session_id = f"v056-source-authority-{self._project_index}"
        task = self.service.begin_task(
            self.namespace,
            project_id=project_id,
            operation="storyboard_create",
            mode="execute",
            authorization="explicit_execute",
            request_text="源文说话人authority parser v6",
            targets=["第一集"],
            authoritative_source="已导入原文",
            latest_version="v1",
            target_boundary="第一集",
            must_keep=["原文对白逐字与说话人"],
            must_remove=[],
            protected_scope=[],
            expected_outputs=["正式分镜"],
            window_key=session_id,
            request_session_id=session_id,
        )
        return project_id, str(task.work_order_id), session_id

    def _attach_source(self, project_id: str, text: str, *, role: str = "novel") -> None:
        original = self.service.store.save_artifact_bytes(
            self.namespace,
            project_id,
            text.encode("utf-8"),
            file_name=f"authority.{role}.txt",
            artifact_type=f"source:{role}",
            version="v1",
            metadata={"source_role": role, "mime_type": "text/plain"},
        )
        extracted = self.service.store.save_artifact_json(
            self.namespace,
            project_id,
            {"kind": "text", "format": "txt", "text": text},
            file_name=f"authority.{role}_提取内容.json",
            artifact_type="source_extraction",
            version="v1",
            source_artifact_id=str(original["artifact_id"]),
            metadata={"source_role": role},
        )
        original = self.service.store.update_artifact_metadata(
            self.namespace,
            project_id,
            str(original["artifact_id"]),
            {"extracted_artifact_id": extracted["artifact_id"], "source_role": role},
        )
        entry = {
            "artifact_id": original["artifact_id"],
            "work_order_id": "WO-source-fixture",
            "source_role": role,
            "file_name": original["file_name"],
            "version": "v1",
            "fingerprint": original["fingerprint"],
            "imported_at": original["created_at"],
        }
        with self.service.store.mutate_project(self.namespace, project_id) as (hub, _ledger):
            hub["project_state"]["authoritative_sources"].append(entry)
            hub["project_state"]["latest_artifacts"][f"source:{role}"] = entry

    def _assert_no_formal_publication(self, project_id: str) -> None:
        artifacts = self.service.store.list_artifacts(self.namespace, project_id)
        self.assertFalse(
            any(str(row.get("artifact_type") or "").startswith("storyboard") for row in artifacts)
        )
        self.assertEqual(
            self.service.store.list_project_records(
                self.namespace, project_id, kind="handoffs"
            ),
            [],
        )
        hub = self.service.store.load_hub(self.namespace, project_id)
        self.assertEqual(hub.get("deliverables", []), [])
        self.assertNotIn("storyboard", hub["project_state"]["latest_artifacts"])

    def _full_preflight(
        self,
        project_id: str,
        work_order_id: str,
        session_id: str,
        candidate: dict[str, object],
    ):
        return self.service.preflight_storyboard_draft(
            self.namespace,
            project_id=project_id,
            work_order_id=work_order_id,
            storyboard=candidate,
            scope_kind="full",
            scope_id="EP01",
            request_session_id=session_id,
        )

    def _assert_authority_preflight_failed_without_publication(
        self, project_id: str, result
    ) -> None:
        self.assertEqual(result.status, "draft_preflight_fail")
        self.assertFalse(result.data["commit_ready"])
        for field in (
            "artifact_created",
            "version_created",
            "xlsx_created",
            "handoff_created",
        ):
            self.assertFalse(result.data[field])
        authority = result.data["report"]["source_dialogue_authority"]
        self.assertEqual(authority["status"], "fail_current")
        self.assertTrue(
            any(
                row.get("semantic_layer") == "source_dialogue_authority"
                and row.get("blocking") is True
                for row in result.data["report"]["issues"]
            )
        )
        records = self.service.store.list_project_records(
            self.namespace, project_id, kind="draft_preflights"
        )
        self.assertEqual(len(records), 1)
        self.assertTrue(self.service.store.project_record_is_signed(records[0]))
        self.assertEqual(records[0]["ledger_event"]["result"], "fail_current")
        self._assert_no_formal_publication(project_id)

    def test_fake_candidate_speaker_cannot_authorize_source(self) -> None:
        project_id, work_order_id, session_id = self._project_and_task()
        self._attach_source(project_id, "江氏突然说：“大嫂，没来晚吧？”")
        candidate = valid_storyboard()
        candidate["protected_dialogue"] = [
            {"speaker": "江氏突然", "text": "大嫂，没来晚吧？"}
        ]
        candidate["character_inventory"] = [{"name": "江氏突然"}]
        candidate["source_treatment_ledger"] = [{"speaker": "江氏突然"}]
        candidate["cognition_matrix"] = [{"character": "江氏突然"}]
        candidate["anchors"] = {"character": {"江氏突然": "红衣"}}

        result = self._full_preflight(
            project_id, work_order_id, session_id, candidate
        )

        self._assert_authority_preflight_failed_without_publication(
            project_id, result
        )
        authority = result.data["report"]["source_dialogue_authority"]
        self.assertEqual(authority["candidate_authority_fields_used"], [])
        self.assertEqual(authority["occurrences"][0]["speaker"], "江氏")

    def test_same_line_second_source_occurrence_cannot_be_deleted(self) -> None:
        project_id, work_order_id, session_id = self._project_and_task()
        self._attach_source(
            project_id, "江氏说：“甲。”赵氏问：“乙？”"
        )
        candidate = valid_storyboard()
        candidate["protected_dialogue"] = [{"speaker": "江氏", "text": "甲。"}]

        result = self._full_preflight(
            project_id, work_order_id, session_id, candidate
        )

        self._assert_authority_preflight_failed_without_publication(
            project_id, result
        )
        authority = result.data["report"]["source_dialogue_authority"]
        self.assertEqual(authority["occurrence_count"], 2)
        self.assertEqual(authority["missing_occurrence_count"], 1)

    def test_structural_parser_bypass_matrix_fails_full_preflight_atomically(self) -> None:
        cases = (
            (
                "object_action_before_speech",
                "江氏举起纸条向众人说道：“甲。”",
                "novel",
                [],
            ),
            (
                "whitespace_first_occurrence_deleted",
                "江氏说 甲。李氏说：“乙。”",
                "novel",
                [{"speaker": "李氏", "text": "乙。"}],
            ),
            (
                "zero_separator_first_occurrence_deleted",
                "江氏说甲。李氏说：“乙。”",
                "novel",
                [{"speaker": "李氏", "text": "乙。"}],
            ),
            (
                "first_unquoted_terminal_stripped",
                "江氏说，甲。李氏说，乙。",
                "novel",
                [
                    {"speaker": "江氏", "text": "甲"},
                    {"speaker": "李氏", "text": "乙。"},
                ],
            ),
            (
                "second_inline_script_role_swallowed",
                "江氏：甲。李氏：乙。",
                "script",
                [{"speaker": "江氏", "text": "甲。李氏：乙。"}],
            ),
            (
                "visual_prefix_hides_inline_script_role",
                "画面：静默。江氏：甲。",
                "script",
                [],
            ),
            (
                "osd_cannot_self_certify_as_script_role",
                "OSD：静默。",
                "script",
                [{"speaker": "OSD", "text": "静默。"}],
            ),
        )
        for name, source, role, claims in cases:
            with self.subTest(case=name):
                project_id, work_order_id, session_id = self._project_and_task()
                self._attach_source(project_id, source, role=role)
                candidate = valid_storyboard()
                candidate["protected_dialogue"] = copy.deepcopy(claims)
                result = self._full_preflight(
                    project_id, work_order_id, session_id, candidate
                )
                self._assert_authority_preflight_failed_without_publication(
                    project_id, result
                )

    def test_new_source_grammar_fail_closed_matrix_has_zero_publication(self) -> None:
        cases = (
            (
                "delimiterless_pronoun",
                "她说甲。",
                "novel",
                [],
            ),
            (
                "delimiterless_residue_cannot_return_partial_authority",
                "江氏又说甲。李氏说：\u201c乙。\u201d",
                "novel",
                [{"speaker": "李氏", "text": "乙。"}],
            ),
            (
                "script_action_marker_cannot_hide_explicit_cue",
                "△江氏推门。江氏说：\u201c快走。\u201d",
                "script",
                [],
            ),
            (
                "script_action_marker_unknown_cue",
                "▲江氏推门。江氏命令：\u201c快走。\u201d",
                "script",
                [],
            ),
            (
                "inline_visual_cannot_be_claimed_as_role_dialogue",
                "江氏：甲。字幕：三日后。",
                "script",
                [{"speaker": "江氏", "text": "甲。字幕：三日后。"}],
            ),
            (
                "script_role_bad_quote",
                "江氏：\u201c甲。",
                "script",
                [{"speaker": "江氏", "text": "\u201c甲。"}],
            ),
            (
                "prose_cue_mismatched_quote",
                "江氏说：\u201c甲。\u2019乙。\u201d",
                "novel",
                [],
            ),
            (
                "dotall_visible_quote_cannot_consume_later_speech",
                "信上写着：\u201c甲。\n江氏说：\u201c乙。\u201d",
                "novel",
                [],
            ),
            (
                "visual_prefix_unknown_cue",
                "画面：静默。门外传来一句：\u201c快走。\u201d",
                "novel",
                [],
            ),
            (
                "visual_prefix_unquoted_unknown_cue",
                "画面：江氏推门。江氏警告：快走。",
                "novel",
                [],
            ),
            (
                "phone_screen_read_requires_claim",
                "手机屏幕上写着：\u201c甲。\u201d江氏随后念了出来。",
                "novel",
                [],
            ),
            (
                "phone_screen_objectless_read_is_ambiguous",
                "信上写着：\u201c纸本。\u201d手机屏幕上写着：\u201c屏幕。\u201d"
                "江氏随后念了出来。",
                "novel",
                [],
            ),
        )
        for name, source, role, claims in cases:
            with self.subTest(case=name):
                project_id, work_order_id, session_id = self._project_and_task()
                self._attach_source(project_id, source, role=role)
                candidate = valid_storyboard()
                candidate["protected_dialogue"] = copy.deepcopy(claims)

                result = self._full_preflight(
                    project_id, work_order_id, session_id, candidate
                )

                self._assert_authority_preflight_failed_without_publication(
                    project_id, result
                )

    def test_hidden_visual_role_and_derived_labels_fail_preflight_atomically(self) -> None:
        cases = (
            (
                "visual_to_role_missing_separator",
                "字幕：三日后江氏：甲。",
                "script",
                [],
            ),
            (
                "phone_visual_to_role_missing_separator",
                "手机屏幕：未接来电江氏：甲。",
                "script",
                [],
            ),
            (
                "role_to_visual_missing_separator",
                "江氏：甲字幕：三日后。",
                "script",
                [{"speaker": "江氏", "text": "甲字幕：三日后。"}],
            ),
            (
                "role_to_phone_visual_missing_separator",
                "江氏：甲手机屏幕：未接来电。",
                "script",
                [{"speaker": "江氏", "text": "甲手机屏幕：未接来电。"}],
            ),
            (
                "derived_stage_full_view",
                "全景图：静默。",
                "script",
                [{"speaker": "全景图", "text": "静默。"}],
            ),
            (
                "derived_stage_plan_table",
                "计划表：静默。",
                "script",
                [{"speaker": "计划表", "text": "静默。"}],
            ),
            (
                "derived_stage_timeline",
                "时间轴：静默。",
                "script",
                [{"speaker": "时间轴", "text": "静默。"}],
            ),
            (
                "derived_visual_family_name",
                "王字幕说：\u201c甲。\u201d",
                "novel",
                [{"speaker": "王字幕", "text": "甲。"}],
            ),
        )
        for name, source, role, claims in cases:
            with self.subTest(case=name):
                project_id, work_order_id, session_id = self._project_and_task()
                self._attach_source(project_id, source, role=role)
                candidate = valid_storyboard()
                candidate["protected_dialogue"] = copy.deepcopy(claims)

                result = self._full_preflight(
                    project_id, work_order_id, session_id, candidate
                )

                self._assert_authority_preflight_failed_without_publication(
                    project_id, result
                )

    def test_new_source_grammar_legal_matrix_has_exact_manifest(self) -> None:
        cases = (
            (
                "pure_script_action",
                "△李氏转身离去。",
                "script",
                [],
            ),
            (
                "pure_script_reading_action",
                "△江氏翻页，继续读书。",
                "script",
                [],
            ),
            (
                "script_action_with_explicit_cue",
                "△江氏推门。江氏说：\u201c快走。\u201d",
                "script",
                [("江氏", "快走。")],
            ),
            (
                "inline_visual_and_explicit_roles",
                "江氏：甲。字幕：三日后。阿玉：乙。Bob：丙。",
                "script",
                [("江氏", "甲。"), ("阿玉", "乙。"), ("Bob", "丙。")],
            ),
            (
                "plain_han_name",
                "苏清柳说：\u201c甲。\u201d",
                "novel",
                [("苏清柳", "甲。")],
            ),
            (
                "silent_phone_screen",
                "手机屏幕上写着：\u201c甲。\u201d",
                "novel",
                [],
            ),
            (
                "explicit_phone_screen_read",
                "手机屏幕上写着：\u201c甲。\u201d江氏随后念了出来。",
                "novel",
                [("江氏", "甲。")],
            ),
            (
                "explicit_wechat_message_read",
                "微信消息：\u201c甲。\u201d江氏随后将微信消息念了出来。",
                "novel",
                [("江氏", "甲。")],
            ),
            (
                "explicit_screen_object_selects_screen",
                "信上写着：\u201c纸本。\u201d手机屏幕上写着：\u201c屏幕。\u201d"
                "江氏随后将手机屏幕上的字念了出来。",
                "novel",
                [("江氏", "屏幕。")],
            ),
        )
        for name, source, role, expected in cases:
            with self.subTest(case=name):
                self._project_index += 1
                project_id = str(
                    self.service.create_project(
                        self.namespace, f"源文authority-legal-{self._project_index}"
                    ).project_id
                )
                self._attach_source(project_id, source, role=role)
                candidate = valid_storyboard()
                candidate["protected_dialogue"] = [
                    {"speaker": speaker, "text": text}
                    for speaker, text in expected
                ]

                manifest, issues = self.service._source_dialogue_authority_manifest(
                    self.namespace, project_id, candidate
                )

                self.assertEqual(issues, [])
                self.assertEqual(manifest["status"], "pass")
                self.assertTrue(manifest["exact_claim_match"])
                self.assertEqual(manifest["occurrence_count"], len(expected))
                self.assertEqual(
                    [
                        (row["speaker"], row["text"])
                        for row in manifest["occurrences"]
                    ],
                    expected,
                )
                self._assert_no_formal_publication(project_id)

    def test_same_line_cue_role_mixes_have_exact_service_manifest(self) -> None:
        cases = (
            (
                "cue_role",
                "江氏说：\u201c甲。\u201d李氏：乙。",
                [("江氏", "甲。", "speech_cue"), ("李氏", "乙。", "role_label")],
            ),
            (
                "cue_role_cue",
                "江氏说：\u201c甲。\u201d李氏：乙。赵氏说：\u201c丙。\u201d",
                [
                    ("江氏", "甲。", "speech_cue"),
                    ("李氏", "乙。", "role_label"),
                    ("赵氏", "丙。", "speech_cue"),
                ],
            ),
            (
                "visual_prefix_cue_role",
                "字幕：三日后。江氏说：\u201c甲。\u201d李氏：乙。",
                [("江氏", "甲。", "speech_cue"), ("李氏", "乙。", "role_label")],
            ),
            (
                "action_prefix_cue_role",
                "△江氏推门。江氏说：\u201c甲。\u201d李氏：乙。",
                [("江氏", "甲。", "speech_cue"), ("李氏", "乙。", "role_label")],
            ),
            (
                "role_cue",
                "李氏：乙。赵氏说：\u201c丙。\u201d",
                [("李氏", "乙。", "role_label"), ("赵氏", "丙。", "speech_cue")],
            ),
            (
                "role_cue_role",
                "李氏：乙。赵氏说：\u201c丙。\u201d孙氏：丁。",
                [
                    ("李氏", "乙。", "role_label"),
                    ("赵氏", "丙。", "speech_cue"),
                    ("孙氏", "丁。", "role_label"),
                ],
            ),
        )
        for name, source, expected in cases:
            with self.subTest(case=name):
                self._project_index += 1
                project_id = str(
                    self.service.create_project(
                        self.namespace, f"源文authority-mixed-{self._project_index}"
                    ).project_id
                )
                self._attach_source(project_id, source, role="script")
                candidate = valid_storyboard()
                candidate["protected_dialogue"] = [
                    {"speaker": speaker, "text": text}
                    for speaker, text, _kind in expected
                ]

                manifest, issues = self.service._source_dialogue_authority_manifest(
                    self.namespace, project_id, candidate
                )

                self.assertEqual(issues, [])
                self.assertEqual(manifest["status"], "pass")
                self.assertTrue(manifest["exact_claim_match"])
                self.assertEqual(manifest["occurrence_count"], len(expected))
                self.assertEqual(
                    manifest["occurrences"],
                    [
                        {
                            "speaker": speaker,
                            "text": text,
                            "line_number": 1,
                            "char_offset": source.index(text),
                            "kind": kind,
                        }
                        for speaker, text, kind in expected
                    ],
                )
                self._assert_no_formal_publication(project_id)

    def test_extra_candidate_claim_is_blocking_and_has_zero_publication(self) -> None:
        project_id, work_order_id, session_id = self._project_and_task()
        self._attach_source(project_id, "江氏说：“甲。”")
        candidate = valid_storyboard()
        candidate["protected_dialogue"] = [
            {"speaker": "伪角色", "text": "伪台词。"},
            {"speaker": "江氏", "text": "甲。"},
        ]

        result = self._full_preflight(
            project_id, work_order_id, session_id, candidate
        )

        self._assert_authority_preflight_failed_without_publication(
            project_id, result
        )
        authority = result.data["report"]["source_dialogue_authority"]
        self.assertFalse(authority["exact_claim_match"])
        self.assertEqual(authority["missing_occurrence_count"], 0)

    def test_malformed_claim_becomes_signed_blocking_failure(self) -> None:
        project_id, work_order_id, session_id = self._project_and_task()
        self._attach_source(project_id, "江氏说：“甲。”")
        candidate = valid_storyboard()
        candidate["protected_dialogue"] = [
            {"speaker": "江氏", "text": "甲。"},
            {"speaker": "", "text": "伪台词。"},
        ]

        result = self._full_preflight(
            project_id, work_order_id, session_id, candidate
        )

        self._assert_authority_preflight_failed_without_publication(
            project_id, result
        )
        authority = result.data["report"]["source_dialogue_authority"]
        self.assertIn("malformed", authority["claim_validation_error"])

    def test_divergent_claim_mirror_is_blocking(self) -> None:
        project_id, work_order_id, session_id = self._project_and_task()
        self._attach_source(project_id, "江氏说：“甲。”")
        candidate = valid_storyboard()
        candidate["protected_dialogue"] = [
            {"speaker": "江氏", "text": "甲。"},
            {"speaker": "伪角色", "text": "伪台词。"},
        ]
        candidate["source_ingestion"] = {
            "protected_dialogue": [{"speaker": "江氏", "text": "甲。"}]
        }

        result = self._full_preflight(
            project_id, work_order_id, session_id, candidate
        )

        self._assert_authority_preflight_failed_without_publication(
            project_id, result
        )
        authority = result.data["report"]["source_dialogue_authority"]
        self.assertIn("mirror_divergence", authority["claim_validation_error"])

    def test_source_lock_fingerprint_and_role_mismatch_are_blocking(self) -> None:
        project_id, _work_order_id, _session_id = self._project_and_task()
        self._attach_source(project_id, "江氏说：“甲。”", role="novel")
        base = valid_storyboard()
        base["protected_dialogue"] = [{"speaker": "江氏", "text": "甲。"}]
        clean_manifest, clean_issues = self.service._source_dialogue_authority_manifest(
            self.namespace, project_id, base
        )
        self.assertEqual(clean_manifest["status"], "pass")
        self.assertEqual(clean_issues, [])

        for field, value in (
            ("source_fingerprint", "f" * 64),
            ("authoritative_source_fingerprint", "e" * 64),
            ("source_role", "script"),
            ("authoritative_source_role", "source:script"),
        ):
            candidate = copy.deepcopy(base)
            candidate.setdefault("source_lock", {})[field] = value
            manifest, issues = self.service._source_dialogue_authority_manifest(
                self.namespace, project_id, candidate
            )
            with self.subTest(field=field):
                self.assertEqual(manifest["status"], "fail_current")
                self.assertTrue(issues)
                self.assertTrue(
                    any(
                        field.removeprefix("authoritative_")
                        in str(issue.get("location") or "")
                        for issue in issues
                    )
                )

    def test_source_lock_fingerprint_and_role_mismatch_fail_full_preflight(self) -> None:
        for field, value in (
            ("source_fingerprint", "f" * 64),
            ("source_role", "script"),
        ):
            with self.subTest(field=field):
                project_id, work_order_id, session_id = self._project_and_task()
                self._attach_source(project_id, "江氏说：“甲。”", role="novel")
                candidate = valid_storyboard()
                candidate["protected_dialogue"] = [
                    {"speaker": "江氏", "text": "甲。"}
                ]
                candidate["source_lock"][field] = value
                result = self._full_preflight(
                    project_id, work_order_id, session_id, candidate
                )
                self._assert_authority_preflight_failed_without_publication(
                    project_id, result
                )
                self.assertTrue(
                    any(
                        field in str(issue.get("location") or "")
                        for issue in result.data["report"]["issues"]
                    )
                )

    def test_source_lock_alias_conflict_and_non_object_fail_full_preflight(self) -> None:
        cases = ("alias_conflict", "non_object")
        for case in cases:
            with self.subTest(case=case):
                project_id, work_order_id, session_id = self._project_and_task()
                self._attach_source(project_id, "江氏说：“甲。”", role="novel")
                candidate = valid_storyboard()
                candidate["protected_dialogue"] = [
                    {"speaker": "江氏", "text": "甲。"}
                ]
                if case == "alias_conflict":
                    clean, _issues = self.service._source_dialogue_authority_manifest(
                        self.namespace, project_id, candidate
                    )
                    candidate["source_lock"]["source_fingerprint"] = clean[
                        "source_fingerprint"
                    ]
                    candidate["source_lock"][
                        "authoritative_source_fingerprint"
                    ] = "f" * 64
                else:
                    candidate["source_lock"] = "not-an-object"
                result = self._full_preflight(
                    project_id, work_order_id, session_id, candidate
                )
                self._assert_authority_preflight_failed_without_publication(
                    project_id, result
                )

    def test_source_lock_authority_fields_without_server_source_fail_preflight(self) -> None:
        cases = (
            "non_object",
            "malformed_fingerprint",
            "forged_role",
            "role_alias_conflict",
        )
        for case in cases:
            with self.subTest(case=case):
                project_id, work_order_id, session_id = self._project_and_task()
                candidate = valid_storyboard()
                candidate["protected_dialogue"] = []
                if case == "non_object":
                    candidate["source_lock"] = ["bad"]
                elif case == "malformed_fingerprint":
                    candidate["source_lock"]["source_fingerprint"] = 123
                elif case == "forged_role":
                    candidate["source_lock"]["source_role"] = "script"
                else:
                    candidate["source_lock"]["source_role"] = "novel"
                    candidate["source_lock"]["authoritative_source_role"] = "script"
                result = self._full_preflight(
                    project_id, work_order_id, session_id, candidate
                )
                self._assert_authority_preflight_failed_without_publication(
                    project_id, result
                )

    def test_source_lock_all_alias_groups_reject_hidden_conflicts(self) -> None:
        project_id, _work_order_id, _session_id = self._project_and_task()
        self._attach_source(project_id, "江氏说：“甲。”", role="novel")
        base = valid_storyboard()
        base["protected_dialogue"] = [{"speaker": "江氏", "text": "甲。"}]
        clean, _issues = self.service._source_dialogue_authority_manifest(
            self.namespace, project_id, base
        )
        groups = (
            (
                "source_artifact_id",
                clean["source_artifact_id"],
                "authoritative_source_artifact_id",
                "ART-fake",
            ),
            (
                "source_fingerprint",
                clean["source_fingerprint"],
                "authoritative_source_fingerprint",
                "f" * 64,
            ),
            ("source_role", "novel", "authoritative_source_role", "script"),
        )
        for primary, correct, alias, forged in groups:
            candidate = copy.deepcopy(base)
            candidate["source_lock"][primary] = correct
            candidate["source_lock"][alias] = forged
            manifest, issues = self.service._source_dialogue_authority_manifest(
                self.namespace, project_id, candidate
            )
            with self.subTest(primary=primary):
                self.assertEqual(manifest["status"], "fail_current")
                self.assertTrue(
                    any("alias_conflict" in str(issue.get("location") or "") for issue in issues)
                )

    def test_exact_authority_is_bound_in_full_preflight_report_and_record(self) -> None:
        project_id, work_order_id, session_id = self._project_and_task()
        self._attach_source(project_id, "江氏说：“甲。”")
        candidate = valid_storyboard()
        candidate["protected_dialogue"] = [{"speaker": "江氏", "text": "甲。"}]

        result = self._full_preflight(
            project_id, work_order_id, session_id, candidate
        )

        authority = result.data["report"]["source_dialogue_authority"]
        self.assertEqual(authority["status"], "pass")
        self.assertEqual(authority["grammar_version"], SOURCE_DIALOGUE_GRAMMAR_VERSION)
        self.assertEqual(authority["grammar_sha256"], SOURCE_DIALOGUE_GRAMMAR_SHA256)
        self.assertTrue(authority["exact_claim_match"])
        self.assertEqual(authority["missing_occurrence_count"], 0)
        record = result.data["draft_preflight"]
        self.assertEqual(record["source_dialogue_authority"], authority)
        self.assertEqual(
            record["source_dialogue_authority_sha256"],
            authority["manifest_sha256"],
        )
        self.assertEqual(
            record["report"]["source_dialogue_authority"]["manifest_sha256"],
            authority["manifest_sha256"],
        )
        recomputed, recomputed_issues = self.service._source_dialogue_authority_manifest(
            self.namespace, project_id, candidate
        )
        self.assertEqual(recomputed_issues, [])
        self.assertEqual(recomputed["grammar_sha256"], SOURCE_DIALOGUE_GRAMMAR_SHA256)
        self.assertEqual(recomputed["manifest_sha256"], authority["manifest_sha256"])
        self.assertEqual(result.data["commit_ready"], record["commit_ready"])
        self._assert_no_formal_publication(project_id)

    def test_exact_match_matrix_rejects_insertions_without_missing_only_blindspot(self) -> None:
        project_id, _work_order_id, _session_id = self._project_and_task()
        self._attach_source(project_id, "江氏说：“甲。”赵氏说：“乙。”")
        exact = [
            {"speaker": "江氏", "text": "甲。"},
            {"speaker": "赵氏", "text": "乙。"},
        ]
        variants = (
            [{"speaker": "伪角色", "text": "伪。"}, *exact],
            [*exact, {"speaker": "伪角色", "text": "伪。"}],
            [exact[0], {"speaker": "伪角色", "text": "伪。"}, exact[1]],
            [exact[0], exact[0], exact[1]],
            list(reversed(exact)),
        )
        for rows in variants:
            candidate = valid_storyboard()
            candidate["protected_dialogue"] = copy.deepcopy(rows)
            manifest, issues = self.service._source_dialogue_authority_manifest(
                self.namespace, project_id, candidate
            )
            with self.subTest(rows=rows):
                self.assertEqual(manifest["status"], "fail_current")
                self.assertFalse(manifest["exact_claim_match"])
                self.assertTrue(issues)


if __name__ == "__main__":
    unittest.main()
