"""Independent adversarial controls for the 0.5.64 parsing relaxation."""
import copy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "server"), str(ROOT / "tests"),
               str(ROOT / "skills/shortdrama-storyboard/scripts")]

from bounded_normalization import normalize_music_prohibition
from projection_semantics import music_directives
from test_strict_prompt_contract import strict_fixture
from validate_storyboard import _keyframe_eye_process_hits, _public_audio_layers, validate_project


class IndependentParserReview(unittest.TestCase):
    def music_blocked(self, phrase):
        value = copy.deepcopy(strict_fixture())
        value["shots"][0]["生成限制"] = phrase
        original = copy.deepcopy(value)
        report = validate_project(value)
        self.assertEqual(value, original, "Audit must never normalize saved canonical in place")
        return any(row["error_code"] == "E-AUDIO-STAGE-001" and row["location"].endswith(".生成限制")
                   for row in report["issues"])

    def test_reversed_or_excluded_music_prohibitions_cannot_pass_as_bans(self):
        for phrase in ["不是禁止配乐", "不能禁止配乐", "不要禁止配乐", "撤销禁止配乐",
                       "取消禁止配乐", "没有要求禁止配乐", "无须执行禁止配乐",
                       "禁止额外配乐之外的音乐", "禁止除配乐以外的声音", "禁止无配乐",
                       "不加没有配乐的版本", "禁止BGM、音乐不要停止"]:
            with self.subTest(phrase=phrase):
                self.assertEqual(normalize_music_prohibition(phrase), phrase)
                self.assertTrue(self.music_blocked(phrase), music_directives(phrase))

    def test_ambiguous_conjunction_does_not_reverse_subtitle_constraint(self):
        phrase = "禁止BGM和不使用字幕"
        self.assertEqual(normalize_music_prohibition(phrase), phrase)

    def test_closed_open_sequence_cannot_hide_at_clause_boundary(self):
        for phrase in ["先闭眼，随后睁眼。", "先闭上眼睛，然后睁开眼睛。",
                       "从闭眼逐渐到睁眼。", "先闭眼又睁眼。"]:
            with self.subTest(phrase=phrase):
                self.assertTrue(_keyframe_eye_process_hits(phrase))

    def test_cancelled_static_exclusion_is_not_proved_static(self):
        for phrase in ["不必再避免半眨眼状态。", "无需再避免半眨眼状态。"]:
            with self.subTest(phrase=phrase):
                self.assertTrue(_keyframe_eye_process_hits(phrase))

    def test_stopped_audio_sources_do_not_supply_audible_layers(self):
        for phrase in ["近层脚步已停止，中层低声交谈已停止，远层车轮声已停止。",
                       "近层脚步、远层车轮没有声音。"]:
            with self.subTest(phrase=phrase):
                self.assertEqual(_public_audio_layers(phrase), set())


if __name__ == "__main__":
    unittest.main()
