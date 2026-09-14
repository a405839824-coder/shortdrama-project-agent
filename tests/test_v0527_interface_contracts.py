"""EP34's exact interface counterexamples, never a new production score."""
from __future__ import annotations

import copy
import unittest

from test_v051_template_projection import compact_storyboard
from test_v0527_authoring_serializer import decisions, render_decisions, RejectedDecisions
from shortdrama_mcp import materializer as server
import validate_storyboard as standalone
from performance_checks import _people, visible_subject_tokens


def preventive(obj):
    rules = standalone.load_runtime_rules()
    issues, _ = standalone._validate_preventive_gates(
        obj, obj['shots'], [], 'audit', rules, standalone._catalog_map(rules))
    return issues


class EmptyHandStateContractTests(unittest.TestCase):
    def canonical_with_empty_hands(self):
        data = decisions()
        for label in ('start_state', 'end_state'):
            data['shots'][0][label]['手持道具'] = []
        return render_decisions(data)

    def test_explicit_empty_hands_survive_typed_and_preventive_routes(self):
        obj = self.canonical_with_empty_hands()
        before = copy.deepcopy(obj)
        faults = preventive(obj)
        self.assertFalse([i for i in faults if '手持道具' in i['evidence']])
        self.assertEqual(obj, before)
        self.assertEqual(obj['prevention_evidence']['scene_state_ledger'][0]['start_state']['手持道具'], [])
        # Accepting an explicit state does not manufacture independent evidence.
        self.assertTrue(any('adjacent_continuity_audit' in i['evidence'] for i in faults))

    def test_null_missing_and_non_array_empty_props_are_still_missing(self):
        for value in (None, '', {}):
            with self.subTest(value=value):
                data = decisions(); data['shots'][0]['start_state']['手持道具'] = value
                with self.assertRaises(RejectedDecisions):
                    render_decisions(data)
                obj = self.canonical_with_empty_hands()
                obj['prevention_evidence']['scene_state_ledger'][0]['start_state']['手持道具'] = value
                self.assertTrue(any(i['location'].endswith('.start_state') and '手持道具' in i['evidence'] for i in preventive(obj)))
        obj = self.canonical_with_empty_hands()
        del obj['prevention_evidence']['scene_state_ledger'][0]['start_state']['手持道具']
        self.assertTrue(any(i['location'].endswith('.start_state') and '手持道具' in i['evidence'] for i in preventive(obj)))

    def test_other_empty_state_lists_remain_invalid(self):
        obj = self.canonical_with_empty_hands()
        obj['prevention_evidence']['scene_state_ledger'][0]['start_state']['人物位置'] = []
        self.assertTrue(any(i['location'].endswith('.start_state') and '人物位置' in i['evidence'] for i in preventive(obj)))

    def test_server_scene_entry_uses_the_same_explicit_empty_state_contract(self):
        from shortdrama_mcp.service import _scene_scoped_report
        obj = self.canonical_with_empty_hands()
        report, _ = _scene_scoped_report(obj, obj['shots'])
        self.assertFalse([i for i in report['issues'] if i['location'].startswith('scene_state:')
                          and '手持道具' in i['evidence'] and '缺字段' in i['evidence']])

    def test_nonempty_prop_list_still_rejects_two_holders(self):
        props = [{'prop_id': 'letter', 'holder': holder, 'state': '持有'} for holder in ('甲', '乙')]
        obj = self.canonical_with_empty_hands()
        obj['prevention_evidence']['scene_state_ledger'][0]['start_state']['手持道具'] = props
        self.assertTrue(any('同时属于' in i['evidence'] for i in preventive(obj)))

    def test_canonical_scene_entry_requires_real_state_and_single_string_holder(self):
        from shortdrama_mcp.service import _scene_scoped_report
        for prop in (
            {'prop_id': 'letter', 'holder': '甲'},
            {'prop_id': 'letter', 'holder': '甲', 'state': ''},
            {'prop_id': 'letter', 'holder': ['甲', '乙'], 'state': '持有'},
            {'prop_id': 'letter', 'holder': '甲/乙', 'state': '持有'},
        ):
            with self.subTest(prop=prop):
                obj = self.canonical_with_empty_hands()
                obj['prevention_evidence']['scene_state_ledger'][0]['start_state']['手持道具'] = [prop]
                for issues in (preventive(obj), _scene_scoped_report(obj, obj['shots'])[0]['issues']):
                    self.assertTrue(any(i['location'].endswith('.start_state.手持道具') for i in issues))


class MusicAbsenceContractTests(unittest.TestCase):
    def compilers(self):
        return (standalone.project_formal_master, standalone.project_gpt_keyframe,
                lambda s: server.compile_formal_merged_field(s, assume_compact=True),
                lambda s: server.compile_gpt_keyframe_instruction(s, assume_compact=True))

    def test_ep34_exact_absence_forms_are_not_affirmative_music(self):
        for text in ('没有额外配乐。', '没有任何背景音乐。', '不加音乐。', '不加额外配乐。'):
            with self.subTest(text=text):
                self.assertEqual(standalone._active_music_directive(text), '')
                self.assertEqual(server._active_music_directive(text), '')
                shot = copy.deepcopy(compact_storyboard()['shots'][0])
                shot['环境音效'] = '纸张和指腹接触的轻响，室内底声低。'
                shot['生成限制'] = '9:16竖屏；' + text + '禁止字幕、水印和现代物件。'
                misplaced = copy.deepcopy(shot)
                misplaced['环境音效'] += text
                for compile_one in self.compilers():
                    with self.assertRaisesRegex(ValueError, 'field_content_violation'):
                        compile_one(misplaced)
                original = copy.deepcopy(shot)
                formal = standalone.project_formal_master(shot)
                keyframe = standalone.project_gpt_keyframe(shot)
                self.assertEqual(formal, server.compile_formal_merged_field(shot, assume_compact=True))
                self.assertEqual(keyframe, server.compile_gpt_keyframe_instruction(shot, assume_compact=True))
                self.assertIn('禁止额外配乐', formal)
                self.assertIn('禁止字幕、水印和现代物件', keyframe)
                self.assertNotIn('配乐', keyframe)
                self.assertEqual(shot, original)

    def test_absence_clause_never_negates_later_clear_air_or_loses_other_bans(self):
        for text, expected in (
            ('没有额外配乐，空气清澈。', '空气清澈'),
            ('不加音乐，背景清晰。', '背景清晰'),
            ('没有额外配乐和字幕；禁止水印。', '没有字幕'),
            ('没有字幕、水印和额外配乐。', '没有字幕、水印'),
        ):
            with self.subTest(text=text):
                left = standalone._strip_negative_music_clauses(text)
                right = server._strip_negative_music_clauses(text)
                self.assertEqual(left, right)
                self.assertTrue(left[1]); self.assertIn(expected, left[0])
                self.assertNotIn('没有空气清澈', left[0])
                self.assertNotIn('不加背景清晰', left[0])

    def test_affirmative_and_double_negative_music_stays_rejected(self):
        for text in ('没有额外配乐。随后加入背景音乐。', '不加音乐；开始播放BGM。',
                     '没有禁止额外配乐。', '不是没有背景音乐。', '不能没有音乐。', '不禁止配乐。'):
            with self.subTest(text=text):
                self.assertTrue(standalone._active_music_directive(text))
                self.assertTrue(server._active_music_directive(text))
                shot = copy.deepcopy(compact_storyboard()['shots'][0])
                shot['生成限制'] = '9:16竖屏；' + text
                for compile_one in self.compilers():
                    with self.assertRaisesRegex(ValueError, 'audio_contract_violation'):
                        compile_one(shot)

    def test_conjoined_visual_assertion_does_not_inherit_music_negation(self):
        text = '没有额外配乐且空气清澈。'
        for strip in (standalone._strip_negative_music_clauses, server._strip_negative_music_clauses):
            result, mentioned = strip(text)
            self.assertTrue(mentioned)
            self.assertIn('空气清澈', result)
            self.assertNotIn('没有空气清澈', result)

    def test_contrast_and_indirect_double_negation_do_not_become_music_bans(self):
        for text in ('没有音乐但后来保留配乐。', '无音乐却恢复配乐。',
                     '未禁止音乐。', '并未禁止配乐。', '不必没有音乐。', '无须禁止音乐。'):
            with self.subTest(text=text):
                self.assertTrue(standalone._active_music_directive(text))
                self.assertTrue(server._active_music_directive(text))
                shot = copy.deepcopy(compact_storyboard()['shots'][0])
                shot['生成限制'] = '9:16竖屏；' + text
                for compile_one in self.compilers():
                    with self.assertRaisesRegex(ValueError, 'audio_contract_violation'):
                        compile_one(shot)

    def test_written_music_ban_is_preserved_as_payload(self):
        for text, payload in (
            ('纸面文字“没有音乐。”保持可读。', '“没有音乐。”'),
            ('【可见文字】“说明；没有额外配乐；完”', '“说明；没有额外配乐；完”'),
        ):
            with self.subTest(text=text):
                shot = copy.deepcopy(compact_storyboard()['shots'][0])
                shot['生成限制'] = '9:16竖屏；' + text
                for compile_one in self.compilers():
                    self.assertIn(payload, compile_one(shot))
                shot['生成限制'] += '随后加入背景音乐。'
                for compile_one in self.compilers():
                    with self.assertRaisesRegex(ValueError, 'audio_contract_violation'):
                        compile_one(shot)

    def test_connectors_only_change_when_an_adjacent_music_atom_was_removed(self):
        for strip in (standalone._strip_negative_music_clauses, server._strip_negative_music_clauses):
            for text in ('然而空气清澈。', '室内光线柔和；但背景仍清晰。', '但未禁止音乐。'):
                self.assertEqual(strip(text), (text, False))
            self.assertEqual(strip('没有音乐但空气清澈。'), ('空气清澈。', True))
            self.assertEqual(strip('空气清澈但没有音乐。'), ('空气清澈。', True))
            self.assertEqual(strip('没有音乐但没有配乐。'), ('。', True))


class VisibleAnnotationContractTests(unittest.TestCase):
    REAL = '孟子平（面部清楚可见，近侧眼可辨）；苏清柳（仅后侧肩背与坐姿）'

    def test_real_ep34_people_and_visibility_descriptions_have_same_owners(self):
        self.assertEqual(standalone._visible_character_names(self.REAL), ['孟子平', '苏清柳'])
        names, descriptions = _people(self.REAL, None)
        self.assertEqual(names, ['孟子平', '苏清柳'])
        self.assertEqual(descriptions['苏清柳'], '苏清柳（仅后侧肩背与坐姿）')
        self.assertEqual(descriptions['孟子平'], '孟子平（面部清楚可见，近侧眼可辨）')
        self.assertNotIn('坐姿', names)

    def test_nested_annotations_keep_their_text_and_do_not_hide_four_people(self):
        text = '甲（面部（双眼，近眼）清楚）与乙(肩与背可见)、丙（正面）、丁（侧面）'
        expected = ['甲', '乙', '丙', '丁']
        self.assertEqual(standalone._visible_character_names(text), expected)
        self.assertEqual(_people(text, None)[0], expected)
        self.assertIn('甲（面部（双眼，近眼）清楚）', visible_subject_tokens(text))
        obj = compact_storyboard(); obj['shots'][0]['可见主体'] = text
        report = standalone.validate_project(obj, mode='audit')
        self.assertTrue(any(i['error_code'] == 'E-VIS-001' and '当前=4' in i['evidence'] for i in report['issues']))

    def test_unbalanced_parenthesis_does_not_swallow_later_people(self):
        for text in ('甲（面部，乙、丙、丁', '甲（面部),乙、丙、丁'):
            with self.subTest(text=text):
                names = standalone._visible_character_names(text)
                self.assertTrue(all(name in names for name in ('甲', '乙', '丙', '丁')))

    def test_server_scene_entry_still_rejects_four_annotated_people(self):
        from shortdrama_mcp.service import _scene_scoped_report
        obj = render_decisions(decisions())
        obj['shots'][0]['可见主体'] = '甲（面部，双眼）、乙（肩与背）、丙（正面）、丁（侧面）'
        report, _ = _scene_scoped_report(obj, obj['shots'])
        self.assertTrue(any(i['error_code'] == 'E-VIS-001' and '当前=4' in i['evidence'] for i in report['issues']))

    def test_body_part_enumeration_outside_parentheses_keeps_existing_owner(self):
        names, descriptions = _people('甲面部可见、双眼清楚；乙仅双手、手指与手腕可见', None)
        self.assertEqual(names, ['甲', '乙'])
        self.assertIn('双眼清楚', descriptions['甲'])
        self.assertIn('手腕可见', descriptions['乙'])

    def test_visible_annotations_do_not_disable_multi_person_gate(self):
        for text in (self.REAL, '甲（面部）；乙（面部）；丙（侧脸）；丁（手部）',
                     '甲面部可见、双眼清楚；乙仅双手、手指与手腕可见'):
            with self.subTest(text=text):
                self.assertTrue(standalone._is_multi_person_visible(text))

    def test_long_connector_and_leading_family_name_keep_identity(self):
        for text, expected in (
            ('甲以及乙和丙与丁', ['甲', '乙', '丙', '丁']),
            ('和珅、李时珍、苏清柳、邹氏', ['和珅', '李时珍', '苏清柳', '邹氏']),
            ('甲和和珅', ['甲', '和珅']),
        ):
            with self.subTest(text=text):
                self.assertEqual(standalone._visible_character_names(text), expected)
                self.assertEqual(_people(text, None)[0], expected)

    def test_hidden_extra_people_are_rejected_as_list_syntax_not_counted_by_guess(self):
        from shortdrama_mcp.service import _scene_scoped_report
        from performance_checks import visible_subject_annotation_faults
        for text in ('甲（与乙、丙、丁同框，面部清楚可见）',
                     '甲（与乙和丙一同入画）', '甲（与乙、丙均可见）'):
            with self.subTest(text=text):
                self.assertTrue(visible_subject_annotation_faults(text))
                obj = render_decisions(decisions()); obj['shots'][0]['可见主体'] = text
                for report in (standalone.validate_project(obj, mode='audit'), _scene_scoped_report(obj, obj['shots'])[0]):
                    self.assertTrue(any(i['error_code']=='E-VIS-001' and '顶层逐位列明' in i['evidence'] for i in report['issues']))
        for text in ('苏清柳（仅后侧肩背与坐姿）', '甲（左手与右手同框）', '甲（与左手、右手同框）'):
            self.assertEqual(visible_subject_annotation_faults(text), [])

    def test_long_top_level_identities_cannot_disappear_from_count(self):
        text = 'AleksandrovichPetrov、BenjaminChristopher、CatherineAlexandra、DmitriKonstantin'
        self.assertEqual(len(standalone._visible_character_names(text)), 4)
        self.assertTrue(standalone._is_multi_person_visible(text))


if __name__ == '__main__':
    unittest.main()
