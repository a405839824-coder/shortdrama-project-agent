"""A real named hold must not require an invented gaze destination."""
import unittest

from test_v0527_dynamic_eye_decisions import compile_shot
from performance_checks import eye_contract_faults


class SameTargetFocusTests(unittest.TestCase):
    def value(self):
        _, value = compile_shot()
        value['画面内容'] += '甲眼球轻微进行焦点调整。'
        return value

    def assert_path_fault(self, value):
        faults = eye_contract_faults(value, duration=4)
        self.assertTrue(any('缺视线从起点' in fault for fault in faults), faults)

    def test_explicit_same_target_fine_adjustment_needs_no_fake_route(self):
        self.assertEqual([], eye_contract_faults(self.value(), duration=4))

    def test_different_initial_target_is_not_a_hold(self):
        value = self.value()
        value['起始状态'] = value['起始状态'].replace('木牌', '纸签')
        self.assert_path_fault(value)

    def test_missing_or_ambiguous_hold_target_is_not_resolved(self):
        for replacement in ['原目标', '某处', '木牌或纸签', '']:
            value = self.value()
            value['画面内容'] = value['画面内容'].replace('视线持续落在木牌', '视线持续落在' + replacement)
            with self.subTest(replacement=replacement):
                self.assert_path_fault(value)

    def test_fine_adjustment_cannot_hide_another_destination_or_unresolved_shift(self):
        for addition in ['甲眼球轻微进行焦点调整到纸签。', '甲对纸签作眼球轻微进行焦点调整。',
                         '甲眼球微小换焦。', '甲视线移开。', '甲眼球位移。',
                         '甲视线从木牌换焦至木牌。', '甲视线持续停在纸签。']:
            value = self.value()
            value['画面内容'] += addition
            with self.subTest(addition=addition):
                self.assert_path_fault(value)

    def test_hold_in_another_time_window_cannot_supply_the_target(self):
        value = self.value()
        value['画面内容'] = value['画面内容'].replace('甲眼球轻微进行焦点调整。', '')
        value['画面内容'] += '\n【3.8—4.0秒】甲眼球轻微进行焦点调整。'
        self.assert_path_fault(value)


if __name__ == '__main__':
    unittest.main()
