"""Bounded regression retained for later execution; not run for this release."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/shortdrama-storyboard/scripts'))
from production_plan import (ProductionPlanError, check_cut_anchor,
                             check_duration_design, shot_rhythm_statistics)


class ShotRhythmRegression(unittest.TestCase):
    def test_completion_anchors_and_nonquota_statistics(self):
        row = {'shot_id': 'EP01-001', 'ticks': [0, 10, 30, 55], 'camera': {'frame': 'face'},
               'actions': [{'id': 'line', 'kind': 'speak', 'source_use_id': 'U1', 'start': 0, 'end': 55}]}
        uses = {'U1': {'actor': '甲', 'text': '我不是不愿意嫁，是这门婚事骗了我。'}}
        design = {'duration_exception': {'kind': 'complete_dialogue', 'required_seconds': 5.5}}
        check_duration_design(row, design, 'shot', uses)
        padded = copy.deepcopy(row); padded['actions'][0]['end'] = 40
        with self.assertRaises(ProductionPlanError): check_duration_design(padded, design, 'shot', uses)
        long = copy.deepcopy(row); long['ticks'][-1] = 61; long['actions'][0]['end'] = 61
        with self.assertRaises(ProductionPlanError):
            check_duration_design(long, {'duration_exception': {'kind': 'complete_dialogue', 'required_seconds': 6.1}}, 'shot', uses)
        anchor = {'kind': 'dialogue', 'shot_id': row['shot_id'], 'action_id': 'line',
                  'source_use_id': 'U1', 'char_offset': 8, 'tick': 25}
        check_cut_anchor(anchor, row, None, uses, 'cut', alternative=True)
        with self.assertRaises(ProductionPlanError): check_cut_anchor(anchor, row, None, uses, 'cut')
        with self.assertRaises(ProductionPlanError):
            check_cut_anchor({**anchor, 'source_use_id': 'unrelated'}, row, None, uses, 'cut', alternative=True)
        rows = [{'shot_id': str(i), 'episode_id': 'EP01', 'duration_seconds': 2, 'frame': 'face'} for i in range(60)]
        stats = shot_rhythm_statistics(rows, scope='complete_episodes')[0]
        self.assertEqual(stats['planning_shot_range'], [30, 40])
        self.assertEqual(stats['count_reference_status'], 'outside_reference_review_content')
        self.assertEqual(stats['style_status'], 'observation_only_pending_semantic_review')


if __name__ == '__main__':
    unittest.main()
