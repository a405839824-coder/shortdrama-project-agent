"""Independent voice grammar controls; these do not certify storyboard content."""
import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'server'), str(ROOT / 'skills/shortdrama-storyboard/scripts')]

from authoring_contract_blocks import ContractBlockError, voice_block
from field_content import field_policy, voice_content_findings, field_content_findings
from shortdrama_mcp.field_content import voice_content_findings as server_voice_findings
from validate_storyboard import _voice_template_errors
from voice_sequence import build_voice_sequence

ROWS = [('阿宁', '请等等我。')]
BASE_TAIL = '中音区，厚薄适中，明暗中性，常态清楚。'


def voice(baseline='年龄感未知，' + BASE_TAIL):
    return dict(baseline=baseline, speech_act='请求等候。',
                current_expression='急切，意在请求关切。', volume='日常交谈音量。',
                rhythm='短句自然说出。', breath='自然气息支持。',
                pauses='句末自然短停。', emphasis='“等等”。',
                tail_direction='短收', tail_execution='末字轻收。',
                prohibited_tones=['嘲讽', '播音腔'])


class UnifiedVoiceContractTests(unittest.TestCase):
    def assert_routes_accept(self, spec, rows=ROWS):
        value = voice_block(mode='spoken', dialogue_rows=rows, **spec)
        self.assertEqual(_voice_template_errors(value, ''.join(x[1] for x in rows), rows), [])
        self.assertEqual(voice_content_findings(value, dialogue_rows=rows), [])
        self.assertEqual(server_voice_findings(value, dialogue_rows=rows), [])
        return value

    def assert_routes_reject_baseline(self, baseline):
        spec = voice(baseline)
        with self.assertRaises(ContractBlockError):
            voice_block(mode='spoken', dialogue_rows=ROWS, **spec)
        valid = self.assert_routes_accept(voice())
        value = valid.replace('基础声线：' + voice()['baseline'], '基础声线：' + baseline, 1)
        self.assertTrue(_voice_template_errors(value, ROWS[0][1], ROWS))
        self.assertTrue(voice_content_findings(value, dialogue_rows=ROWS))
        self.assertTrue(server_voice_findings(value, dialogue_rows=ROWS))

    def test_age_forms_have_identical_completeness_and_ownership(self):
        for age in ('成年女声', '成年男声', '年龄感为成年女声', '年龄感成年男声',
                    '年龄感为儿童', '儿童女声', '年龄感幼年男声', '幼年女声',
                    '童声', '女童声', '男童', '年龄感为幼儿', '年龄感婴幼儿',
                    '年龄感未知', '年龄感未明确', '年龄感未提供',
                    '年龄感未知，女声', '八岁女声', '年龄感为年长'):
            with self.subTest(age=age):
                baseline = age + '，' + BASE_TAIL
                value = self.assert_routes_accept(voice(baseline))
                self.assertEqual(value.splitlines()[0], '基础声线：' + baseline)

    def test_unknown_age_is_retained_without_invented_adult_or_numeric_age(self):
        value = self.assert_routes_accept(voice())
        self.assertEqual(value.splitlines()[0], '基础声线：年龄感未知，' + BASE_TAIL)
        self.assertNotRegex(value.splitlines()[0], r'成年|[一二三四五六七八九十\d]+岁')

    def test_stable_trait_order_is_explicit_without_keyword_only_acceptance(self):
        for trait in ('常态清楚', '长期声线清亮', '声线长期温和', '稳定柔和而清亮'):
            self.assert_routes_accept(voice('成年女声，中音区，厚薄适中，明暗中性，' + trait + '。'))
        self.assert_routes_reject_baseline('成年女声，中音区，厚薄适中，明暗中性，长期。')
        self.assert_routes_accept(voice('二十余岁青年女声，中音区，声线偏薄而清亮，长期保持温润平稳的质感。'))
        self.assert_routes_accept(voice('童声，中高音区，清亮偏薄，常态温润。'))

    def test_explicit_unprefixed_thickness_and_brightness_remain_legal(self):
        for thickness in ('偏薄', '偏厚', '浑厚', '声线偏薄'):
            for brightness in ('明亮', '清亮', '低沉', '温润', '沙哑', '声线明亮'):
                with self.subTest(thickness=thickness, brightness=brightness):
                    self.assert_routes_accept(voice('年龄感为成年，高音区，' + thickness + '，' + brightness + '，常态清楚。'))
        for bad in ('偏薄而转身', '明亮并抬手'):
            self.assert_routes_reject_baseline('年龄感为成年，高音区，偏薄，明亮，常态清楚，' + bad + '。')

    def test_each_missing_component_is_rejected_by_both_checks(self):
        complete = ['成年女声', '中音区', '厚薄适中', '明暗中性', '常态清楚']
        for i in range(len(complete)):
            with self.subTest(missing=i):
                self.assert_routes_reject_baseline('，'.join(complete[:i] + complete[i + 1:]) + '。')
        raw = self.assert_routes_accept(voice()).replace(voice()['baseline'], '成年女声。', 1)
        missing = [f for f in field_content_findings({'音色':raw}, dialogue_rows=ROWS)
                   if f['role']=='missing_owned_clause_group']
        self.assertEqual(len(missing), 4)

    def test_valid_clause_does_not_license_unknown_or_action_text(self):
        for age in ('年龄感', '年龄感未知但实际八十岁', '年龄感为成年女声转身离开'):
            self.assert_routes_reject_baseline(age + '，' + BASE_TAIL)
        for extra in ('她转身离开。', '摄影机向左推镜。', '哭喊求助。', '玄妙声波。'):
            self.assert_routes_reject_baseline(voice()['baseline'] + extra)

    def test_supported_crying_delivery_preserves_actual_words(self):
        rows = [('子平', '呜哇哇！二婶婶，哇……子平怕……呜……')]
        for act in ('哭喊', '哭诉', '哭诉害怕', '哭喊求助'):
            spec = voice('年龄感儿童，高音区，厚薄偏薄，明暗偏明，长期声线清亮。')
            spec.update(speech_act=act, current_expression='受惊而带哭腔，意在请求关切。',
                        emphasis='“子平怕”。', volume='哭声较响。',
                        rhythm='断续哭声间说出完整原词，不加速。')
            self.assert_routes_accept(spec, rows)
            self.assertEqual(rows[0][1], '呜哇哇！二婶婶，哇……子平怕……呜……')
        for field, bad in (('speech_act', '哭诉并转身离开'),
                           ('current_expression', '受惊而带哭腔，意在请求关切。她抬手。'),
                           ('current_expression', '哭腔，意在请求关切。摄影机向左推镜。')):
            spec = voice(); spec[field] = bad
            with self.subTest(field=field, bad=bad), self.assertRaises(ContractBlockError):
                voice_block(mode='spoken', dialogue_rows=ROWS, **spec)

    def test_registry_age_extension_reaches_both_routes_without_python_wordlist(self):
        policy = field_policy()
        patterns = policy['owned_clause_patterns']['音色.基础声线']
        before = patterns[0]
        try:
            patterns[0] = '(?:' + before + '|年龄感待来源确认)'
            spec = voice('年龄感待来源确认，' + BASE_TAIL)
            value = voice_block(mode='spoken', dialogue_rows=ROWS, **spec)
            self.assertEqual(_voice_template_errors(value, ROWS[0][1], ROWS), [])
            self.assertEqual(voice_content_findings(value, dialogue_rows=ROWS), [])
        finally:
            patterns[0] = before
        self.assertTrue(_voice_template_errors(value, ROWS[0][1], ROWS))
        self.assertTrue(voice_content_findings(value, dialogue_rows=ROWS))

    def test_each_multi_speaker_baseline_uses_the_same_groups(self):
        rows = ROWS + [('阿禾', ROWS[0][1])]
        specs = [voice('童声，' + BASE_TAIL), voice('年龄感为成年女声，' + BASE_TAIL)]
        value = build_voice_sequence([(who, line, copy.deepcopy(spec))
                                      for (who, line), spec in zip(rows, specs)])
        self.assertEqual(_voice_template_errors(value, ''.join(t for _, t in rows), rows), [])
        self.assertEqual(voice_content_findings(value, dialogue_rows=rows), [])
        bad = value.replace('〔2:阿禾〕年龄感为成年女声，', '〔2:阿禾〕', 1)
        self.assertTrue(_voice_template_errors(bad, ''.join(t for _, t in rows), rows))
        self.assertTrue(voice_content_findings(bad, dialogue_rows=rows))


if __name__ == '__main__':
    unittest.main()
