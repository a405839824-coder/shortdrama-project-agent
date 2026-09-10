"""Real-candidate regressions: absence of a lamp is not a lighting source."""
import unittest
from validate_storyboard import _lighting_context_faults


def room(source, physical='女子坐在案前。', effect='空气清澈，背景木构清晰，高光不过曝，不泛白、不雾化。'):
    return {
        '空间站位描述': '午后正厅，东侧木格窗闭合，窗纸透入漫射日光。',
        '起始状态': physical, '画面内容': '女子双手置于案沿。',
        '光源来源': source, '光源方向': '东窗日光从画面右前上方漫射，浅墙弱反射补暗面。',
        '光影': effect, '色温倾向': '窗光中性偏冷，木色微暖。',
    }


class PracticalLightNegationTests(unittest.TestCase):
    def faults(self, source, physical='女子坐在案前。'):
        return [x for x in _lighting_context_faults(room(source, physical)) if '对应灯火' in x]

    def test_daylight_with_explicit_absence_is_not_a_lamp(self):
        for text in ('东窗漫射日光，均不新增灯火。', '东窗漫射日光，无油灯。', '东窗漫射日光，禁止烛火、灯笼。'):
            with self.subTest(text=text):
                self.assertEqual(self.faults(text), [])

    def test_positive_source_still_needs_an_actual_practical(self):
        for physical in ('女子坐在案前。', '案上没有油灯。'):
            with self.subTest(physical=physical):
                self.assertEqual(len(self.faults('案上油灯为主光。', physical)), 1)

    def test_negative_source_cannot_hide_a_later_positive_source(self):
        for source in ('不用烛火，但使用油灯为主光。', '无烛火、改用油灯。', '无烛火、点燃油灯。'):
            with self.subTest(source=source):
                self.assertEqual(len(self.faults(source)), 1)

    def test_explicitly_unlit_practicals_are_not_light_sources(self):
        for source in ('油灯未点燃，仅有东窗日光。', '未点燃的油灯，仅有东窗日光。', '油灯已经熄灭，东窗日光为主光。'):
            with self.subTest(source=source):
                self.assertEqual(self.faults(source), [])
        for physical in ('案上油灯未点燃。', '案上摆着未点燃的油灯。'):
            with self.subTest(physical=physical):
                self.assertEqual(len(self.faults('油灯为主光。', physical)), 1)

    def test_unlit_mention_does_not_waive_another_lit_source(self):
        self.assertEqual(len(self.faults('油灯未点燃，但案上烛火照明。')), 1)

    def test_established_practical_and_external_aperture_remain_legal(self):
        self.assertEqual(self.faults('案上油灯为主光。', '案上摆放点燃的油灯。'), [])
        self.assertEqual(self.faults('窗外灯笼为弱补光。'), [])

    def test_negated_source_cannot_support_positive_eye_fire_reflection(self):
        shot = room('东窗漫射日光，没有灯火。', effect='眼中灯火反射清晰；空气清澈，背景清楚，高光不过曝。')
        self.assertTrue(any('眼中灯火反射缺' in x for x in _lighting_context_faults(shot)))


if __name__ == '__main__':
    unittest.main()
