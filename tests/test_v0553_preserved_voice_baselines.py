"""Server integration preserves bundled original voice controls on round-trip."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "server"), str(ROOT / "tests"),
               str(ROOT / "skills/shortdrama-storyboard/scripts")]

from authoring_contract_blocks import voice_block
from shortdrama_mcp.file_io import _parse_labeled_cell
from shortdrama_mcp.materializer import compile_formal_merged_field
from test_preserved_voice_baselines import BASELINES, INHERITED, ROWS, spec
from test_v0518_voice_template import compact_shot


class PreservedVoiceProjectionTests(unittest.TestCase):
    def test_originals_survive_formal_projection_and_reverse_parse_verbatim(self):
        for baseline in (*BASELINES, INHERITED):
            with self.subTest(baseline=baseline):
                value = voice_block(mode="spoken", dialogue_rows=ROWS, **spec(baseline))
                shot = compact_shot()
                shot.update(音色=value, 画面内容='甲说道：“明日再商议。”')
                formal = compile_formal_merged_field(shot, assume_compact=True)
                self.assertEqual(_parse_labeled_cell(formal)["音色"], value)
                self.assertIn("基础声线：" + baseline, formal)


if __name__ == "__main__":
    unittest.main()
