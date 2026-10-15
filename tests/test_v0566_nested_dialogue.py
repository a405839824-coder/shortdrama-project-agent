"""Quote delimiters must not truncate source words or their timing range."""
from pathlib import Path
import sys,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'skills/shortdrama-storyboard/scripts'))
from voice_sequence import extract_dialogues,INTEGRATED_DIALOGUE_RE
from timing_model import DIALOGUE
from performance_checks import DIALOGUE_RE

class NestedDialogueTests(unittest.TestCase):
    def test_all_consumers_preserve_nested_quote_and_later_utterance(self):
        source='他称她为“故人”，却不曾见过我。'
        picture='甲说道：“'+source+'”；乙说道：“我听到了。”'
        self.assertEqual(extract_dialogues(picture),[('甲',source),('乙','我听到了。')])
        for pattern in (INTEGRATED_DIALOGUE_RE,DIALOGUE,DIALOGUE_RE):
            self.assertEqual([m.group('line') for m in pattern.finditer(picture)],[source,'我听到了。'])
    def test_quoted_speaker_syntax_is_not_an_extra_utterance(self):
        text='她复述“乙说道：“好。””，然后走了。'
        self.assertEqual(extract_dialogues('甲说道：“'+text+'”'),[('甲',text)])
    def test_unbalanced_quotes_do_not_return_a_truncated_speech(self):
        self.assertEqual(extract_dialogues('甲说道：“所谓“故人”，后半句未闭合'),[])
    def test_flat_ascii_and_legacy_remain_readable(self):
        self.assertEqual(extract_dialogues('甲说道:"好。"；【乙】明日再议。'),[('甲','好。'),('乙','明日再议。')])

if __name__=='__main__':unittest.main()
