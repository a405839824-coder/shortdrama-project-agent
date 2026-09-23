"""Service and standalone keyframes consume the same computed first-frame view."""
from pathlib import Path
import copy
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'server'),str(ROOT/'skills/shortdrama-storyboard/scripts')]
from shortdrama_mcp.materializer import compile_gpt_keyframe_instruction
from validate_storyboard import project_gpt_keyframe
from author_storyboard import _render_decisions
from production_decisions import lower_production_decisions
from test_generated_action_binding import dynamic_subject_fixture
from test_failed_push import fixture as public_world
from picture_presentation import timed_picture, PictureTimingError
from shortdrama_mcp.canonical import compact_storyboard_canonical


class SubjectProjectionTests(unittest.TestCase):
    def test_compaction_preserves_bound_folded_empty_phases_byte_for_byte(self):
        data,_=public_world()
        candidate=_render_decisions(lower_production_decisions(data)['typed_decisions'])
        original=candidate['shots'][0]
        self.assertTrue(original['画面内容'].endswith('\n'))
        result=compact_storyboard_canonical(candidate)
        self.assertEqual(result['shots'][0]['画面内容'],original['画面内容'])
        self.assertEqual(result['shots'][0]['picture_timing'],original['picture_timing'])
        self.assertEqual(timed_picture(result['shots'][0]),timed_picture(original))
        self.assertEqual(compact_storyboard_canonical(result),result)

    def test_compaction_cannot_refresh_stale_hash_or_discard_picture_changes(self):
        data,_=public_world()
        candidate=_render_decisions(lower_production_decisions(data)['typed_decisions'])
        for change in ('append','strip','hash'):
            altered=copy.deepcopy(candidate)
            if change=='append': altered['shots'][0]['画面内容']+='甲转身。'
            if change=='strip': altered['shots'][0]['画面内容']=altered['shots'][0]['画面内容'].strip()
            if change=='hash': altered['shots'][0]['picture_timing']['text_sha256']='0'*64
            with self.subTest(change=change),self.assertRaises(ValueError): compact_storyboard_canonical(altered)

    def test_initial_subject_selection_is_identical_in_both_real_exports(self):
        for turn in (False,True):
            data=dynamic_subject_fixture(turn=turn)
            canonical=_render_decisions(lower_production_decisions(data)['typed_decisions'])
            shot=canonical['shots'][0]
            local=project_gpt_keyframe(shot)
            server=compile_gpt_keyframe_instruction(shot,assume_compact=True)
            self.assertEqual(local,server)
            self.assertNotIn('首帧：',server)
            if turn: self.assertIn('乙（后侧头部',server)
            else: self.assertNotIn('乙（',server)


if __name__=='__main__': unittest.main()
