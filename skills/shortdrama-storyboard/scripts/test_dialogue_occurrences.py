#!/usr/bin/env python3
"""Positive and negative tests for lossless source dialogue splits."""
from __future__ import annotations

import copy
import unittest

from dialogue_occurrences import (
    align_dialogue_occurrences,
    align_storyboard_dialogue,
    build_occurrence_inventory,
    protected_dialogue_rows,
)

def _actual(texts: list[str], speaker: str = "江氏") -> list[dict]:
    return [{"shot_id": f"EP01-{index + 1:03d}", "dialogue_index": 0,
             "speaker": speaker, "text": text} for index, text in enumerate(texts)]


class DialogueOccurrenceRangeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = "大嫂，没来晚吧？"
        self.rows = build_occurrence_inventory([{"speaker": "江氏", "text": self.text}])
        self.actual = _actual([self.text[:3], self.text[3:]])
        self.fragments = [
            {"occurrence_id": self.rows[0]["occurrence_id"], "shot_id": "EP01-001",
             "dialogue_index": 0, "char_range": [0, 3]},
            {"occurrence_id": self.rows[0]["occurrence_id"], "shot_id": "EP01-002",
             "dialogue_index": 0, "char_range": [3, len(self.text)]},
        ]

    def result(self, *, rows=None, actual=None, fragments=None):
        return align_dialogue_occurrences(
            self.rows if rows is None else rows,
            self.actual if actual is None else actual,
            fragments=self.fragments if fragments is None else fragments,
        )

    def assert_bad(self, result, code=None):
        self.assertEqual(result["status"], "fail_current", result)
        if code is not None:
            self.assertIn(code, {issue["code"] for issue in result["issues"]}, result)

    def test_explicit_split_is_lossless(self):
        result = self.result()
        self.assertEqual(result["status"], "pass", result)
        self.assertEqual(result["fragments"], self.fragments)
        self.assertEqual(len(result["covered_utterances"]), 2)

    def test_legacy_whole_line_infers_one_fragment(self):
        result = align_dialogue_occurrences(self.rows, _actual([self.text]))
        self.assertEqual(result["status"], "pass", result)
        self.assertEqual(result["fragments"][0]["char_range"], [0, len(self.text)])

    def test_unambiguous_split_derives_ranges_without_duplicate_text(self):
        result = align_dialogue_occurrences(self.rows, self.actual)
        self.assertEqual(result["status"], "pass", result)
        self.assertEqual(result["fragments"], self.fragments)
        self.assertTrue(all("text" not in row for row in result["fragments"]))

    def test_every_partition_of_exact_source_rejoins_once(self):
        for mask in range(1 << (len(self.text) - 1)):
            offsets = [0] + [index for index in range(1, len(self.text))
                             if mask & (1 << (index - 1))] + [len(self.text)]
            texts = [self.text[left:right] for left, right in zip(offsets, offsets[1:])]
            with self.subTest(offsets=offsets):
                result = align_dialogue_occurrences(self.rows, _actual(texts))
                self.assertEqual(result["status"], "pass", result)
                self.assertEqual([r["char_range"] for r in result["fragments"]],
                                 [[left, right] for left, right in zip(offsets, offsets[1:])])

    def test_any_single_character_deletion_cannot_pass_implicit_mapping(self):
        for index in range(len(self.text)):
            with self.subTest(index=index):
                text = self.text[:index] + self.text[index + 1:]
                self.assert_bad(align_dialogue_occurrences(self.rows, _actual([text])), "missing_source_range")

    def test_missing_character_fails(self):
        self.actual[1]["text"] = self.actual[1]["text"][1:]
        self.assert_bad(self.result(), "exact_text_mismatch")

    def test_duplicate_character_fails(self):
        self.actual[1]["text"] = self.text[2:]
        self.assert_bad(self.result(), "exact_text_mismatch")

    def test_punctuation_change_fails(self):
        self.actual[1]["text"] = self.actual[1]["text"].replace("？", "。")
        self.assert_bad(self.result(), "exact_text_mismatch")

    def test_speaker_change_fails(self):
        self.actual[1]["speaker"] = "许氏"
        self.assert_bad(self.result(), "speaker_mismatch")

    def test_gap_in_half_open_ranges_fails(self):
        self.fragments[1]["char_range"][0] += 1
        self.assert_bad(self.result(), "range_gap_or_overlap")

    def test_overlapping_ranges_fail(self):
        self.fragments[1]["char_range"][0] -= 1
        self.assert_bad(self.result(), "range_gap_or_overlap")

    def test_omitted_tail_fails(self):
        self.assert_bad(self.result(fragments=self.fragments[:1]), "missing_source_range")

    def test_invalid_ranges_fail(self):
        for value in ([0, 0], [-1, 3], [0, 100], [False, 3], [0.0, 3], [0], "0:3"):
            with self.subTest(value=value):
                fragments = copy.deepcopy(self.fragments)
                fragments[0]["char_range"] = value
                self.assert_bad(self.result(fragments=fragments), "invalid_range")

    def test_fragment_list_cannot_reorder_canonical(self):
        self.assert_bad(self.result(fragments=list(reversed(self.fragments))), "canonical_order_mismatch")

    def test_unknown_occurrence_fails(self):
        self.fragments[0]["occurrence_id"] = "forged"
        self.assert_bad(self.result(), "unknown_occurrence")

    def test_unknown_actual_dialogue_fails(self):
        self.fragments[0]["dialogue_index"] = 8
        self.assert_bad(self.result(), "unknown_utterance")

    def test_same_actual_fragment_cannot_cover_two_occurrences(self):
        rows = build_occurrence_inventory([
            {"speaker": "江氏", "text": "好。"},
            {"speaker": "江氏", "text": "好。"},
        ])
        fragments = [{"occurrence_id": row["occurrence_id"], "shot_id": "EP01-001",
                      "dialogue_index": 0, "char_range": [0, 2]} for row in rows]
        self.assert_bad(align_dialogue_occurrences(rows, _actual(["好。"]), fragments=fragments),
                        "duplicate_fragment_consumption")

    def test_two_equal_occurrences_remain_distinct(self):
        rows = build_occurrence_inventory([
            {"speaker": "江氏", "text": "好。"},
            {"speaker": "江氏", "text": "好。"},
        ])
        self.assertNotEqual(rows[0]["occurrence_id"], rows[1]["occurrence_id"])
        result = align_dialogue_occurrences(rows, _actual(["好。", "好。"] ))
        self.assertEqual(result["status"], "pass", result)
        self.assertEqual(len({r["occurrence_id"] for r in result["fragments"]}), 2)

    def test_one_equal_occurrence_cannot_stand_for_two(self):
        rows = [{"speaker": "江氏", "text": "好。"}] * 2
        self.assert_bad(align_dialogue_occurrences(rows, _actual(["好。"])), "missing_source_range")

    def test_extra_source_repetition_fails(self):
        rows = [{"speaker": "江氏", "text": "好。"}]
        self.assert_bad(align_dialogue_occurrences(rows, _actual(["好。", "好。"])), "unbound_source_fragment")

    def test_explicit_source_occurrence_order_fails(self):
        rows = build_occurrence_inventory([
            {"speaker": "江氏", "text": "好。"}, {"speaker": "江氏", "text": "走。"},
        ])
        actual = _actual(["走。", "好。"])
        fragments = [{"occurrence_id": rows[row_index]["occurrence_id"],
                      "shot_id": actual[index]["shot_id"], "dialogue_index": 0,
                      "char_range": [0, 2]} for index, row_index in enumerate([1, 0])]
        self.assert_bad(align_dialogue_occurrences(rows, actual, fragments=fragments), "source_order_mismatch")

    def test_unrelated_adapted_dialogue_is_returned_for_separate_audit(self):
        actual = _actual(["证据已在案上。", self.text[:3], self.text[3:]])
        result = align_dialogue_occurrences(self.rows, actual)
        self.assertEqual(result["status"], "pass", result)
        self.assertEqual([r["text"] for r in result["unmapped_utterances"]], ["证据已在案上。"])

    def test_short_adapted_answer_inside_long_source_is_not_a_repeat(self):
        rows = [{"speaker": "江氏", "text": "是。此事交给我，你先回去。"}]
        actual = _actual([rows[0]["text"], "是。"])
        result = align_dialogue_occurrences(rows, actual)
        self.assertEqual(result["status"], "pass", result)
        self.assertEqual([row["text"] for row in result["unmapped_utterances"]], ["是。"])

    def test_short_answer_does_not_cover_rest_of_long_source(self):
        rows = [{"speaker": "江氏", "text": "是。此事交给我，你先回去。"}]
        self.assert_bad(align_dialogue_occurrences(rows, _actual(["是。"])), "missing_source_range")

    def test_unicode_non_bmp_character_uses_one_codepoint(self):
        rows = [{"speaker": "江氏", "text": "𠮷，来。"}]
        result = align_dialogue_occurrences(rows, _actual(["𠮷，", "来。"] ))
        self.assertEqual(result["status"], "pass", result)
        self.assertEqual([r["char_range"] for r in result["fragments"]], [[0, 2], [2, 4]])

    def test_whitespace_is_not_normalized_away(self):
        rows = [{"speaker": "江氏", "text": "等 一下。"}]
        self.assert_bad(align_dialogue_occurrences(rows, _actual(["等一下。"])), "missing_source_range")

    def test_source_target_shots_are_binding(self):
        self.rows[0]["target_shots"] = ["EP01-003"]
        self.assert_bad(self.result(), "target_shot_mismatch")

    def test_duplicate_declared_occurrence_ids_fail(self):
        self.assert_bad(self.result(rows=self.rows * 2), "invalid_occurrence_inventory")

    def test_mirrored_inventories_do_not_double_count(self):
        rows = [{"speaker": "江氏", "text": "好。"}] * 2
        storyboard = {"protected_dialogue": copy.deepcopy(rows),
                      "source_ingestion": {"protected_dialogue": copy.deepcopy(rows)}}
        selected = protected_dialogue_rows(storyboard)
        self.assertEqual(len(selected), 2)
        self.assertEqual(align_dialogue_occurrences(selected, _actual(["好。", "好。"] ))["status"], "pass")

    def test_conflicting_mirrors_fail(self):
        with self.assertRaisesRegex(ValueError, "mirrors differ"):
            protected_dialogue_rows({"protected_dialogue": self.rows,
                                     "source_ingestion": {"protected_dialogue": []}})

    def test_explicit_null_inventory_is_not_absent(self):
        with self.assertRaisesRegex(ValueError, "array of objects"):
            protected_dialogue_rows({"protected_dialogue": None})

    def test_mirror_only_identity_remains_a_claim_to_verify(self):
        storyboard = {"protected_dialogue": [{"speaker": "江氏", "text": "好。", "occurrence_id": "forged"}],
                      "source_ingestion": {"protected_dialogue": [{"speaker": "江氏", "text": "好。"}]}}
        self.assertEqual(protected_dialogue_rows(storyboard)[0]["occurrence_id"], "forged")

    def test_chapter_protected_beat_mapping_does_not_waive_actual_speech(self):
        storyboard = {"source_ingestion": {"protected_dialogue": [{
            "speaker": "女主", "text": "有人跟着我，我得马上离开。",
            "target_beats": ["BEAT-002"],
        }]}}
        result = align_storyboard_dialogue(storyboard, [{"镜头编号": "S001", "画面内容": "无对白"}],
                                          lambda content: [])
        self.assert_bad(result, "missing_source_range")

    def test_chapter_protected_beat_mapping_accepts_exact_actual_speech(self):
        row = {"speaker": "女主", "text": "有人跟着我，我得马上离开。", "target_beats": ["BEAT-002"]}
        storyboard = {"source_ingestion": {"protected_dialogue": [row]}}
        result = align_storyboard_dialogue(storyboard, [{"镜头编号": "S001", "画面内容": "本镜确实发声"}],
                                          lambda content: [(row["speaker"], row["text"])])
        self.assertEqual(result["status"], "pass", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
