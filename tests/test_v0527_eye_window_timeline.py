"""A registered eye visibility window is not a fourth macro stage."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'tests'),str(ROOT/'skills/shortdrama-storyboard/scripts')]
from author_storyboard import record_attempt
from performance_checks import eye_visibility_timeline_scan, eye_contract_faults
from picture_presentation import timed_picture
from validate_storyboard import _validate_timeline, validate_project
from test_v0527_authoring_serializer import decisions
from test_strict_prompt_contract import strict_fixture


WINDOW = '甲眼睛仅在0.0—1.2秒清楚可见'
def body(extra):
    return '【0.0—0.7秒】'+extra+'。\n【0.7—3.3秒】甲保持双手放在桌边。\n【3.3—4.0秒】甲视线落在信纸。'


class EyeWindowTimelineTests(unittest.TestCase):
    def test_registered_window_not_a_stage_source_text_is_preserved(self):
        text=body(WINDOW); scan=eye_visibility_timeline_scan(text,duration=4,visible_subjects='甲')
        self.assertEqual(len(scan),len(text))
        self.assertEqual(3,scan.count('【'))
        self.assertNotIn('0.0—1.2',scan)
        self.assertIn(WINDOW,text)
        self.assertEqual([],_validate_timeline(text,4,strict=True,visible_subjects='甲'))
        self.assertEqual([],_validate_timeline(text,4,strict=False,visible_subjects='甲'))

    def test_real_record_and_full_validator_share_grammar_without_rewriting(self):
        value=decisions(); row=value['shots'][0]
        row['fields']['可见主体']='甲'
        row['phases'][0]['text']+=WINDOW+'。'
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); path=root/'input.json'
            raw=json.dumps(value,ensure_ascii=False,indent=2).encode('utf-8');path.write_bytes(raw)
            result=record_attempt(path,root/'attempts',attempt_id='visible')
            self.assertEqual('rendered_pending_preflight',result['status'],result)
            frozen=root/'attempts/visible/input.original.json'
            self.assertEqual(hashlib.sha256(raw).hexdigest(),hashlib.sha256(frozen.read_bytes()).hexdigest())
            output=json.loads((root/'attempts/visible/canonical.json').read_text('utf-8'))
            self.assertIn(WINDOW,timed_picture(output['shots'][0]))
            self.assertIn('甲眼睛一度清楚可见',output['shots'][0]['画面内容'])
            self.assertNotIn('0.0—1.2秒',output['shots'][0]['画面内容'])
            report=validate_project(output,mode='audit')
            self.assertFalse(any(i['error_code']=='E-TIMELINE-001' for i in report['issues']),report['issues'])
        # Valid formal fixture independently exercises the actual project caller.
        formal=strict_fixture()
        actor=formal['shots'][0]['可见主体'].split('、')[0]
        formal['shots'][0]['画面内容']+=WINDOW.replace('甲',actor)+'。'
        original=copy.deepcopy(formal); report=validate_project(formal,mode='audit')
        self.assertFalse(any(i['error_code']=='E-TIMELINE-001' for i in report['issues']))
        self.assertEqual(original,formal)

    def test_unknown_actor_quote_negation_and_unbound_range_are_not_exempt(self):
        invalid=['乙眼睛仅在0.0—1.2秒清楚可见',
                 '甲说道：“'+WINDOW+'。”',
                 '客观旁白：“说明。'+WINDOW+'。”',
                 '甲眼睛并非仅在0.0—1.2秒清楚可见',
                 '没有'+WINDOW,'眼睛仅在0.0—1.2秒清楚可见',
                 '0.0—1.2秒甲抬手',WINDOW+'；1.2—2.0秒甲抬手']
        for extra in invalid:
            with self.subTest(extra=extra):
                self.assertTrue(_validate_timeline(body(extra),4,strict=True,visible_subjects='甲'))

    def test_out_of_bounds_empty_reverse_and_negative_windows_stay_invalid(self):
        for window in ['0.0—4.1','1.2—1.2','1.2—0.0','-0.1—1.2']:
            with self.subTest(window=window):
                self.assertTrue(_validate_timeline(body(WINDOW.replace('0.0—1.2',window)),4,strict=True,visible_subjects='甲'))

    def test_real_four_macro_stages_and_disguised_stage_markers_still_fail(self):
        for extra in [WINDOW+'；【1.0—1.2秒】甲看向信纸',
                      '甲眼睛仅在【0.0—1.2秒】清楚可见',
                      '[0.0-1.2秒]甲抬手',
                      '【0—1.2秒】甲抬手',
                      '【0.0—1.2s】甲抬手']:
            with self.subTest(extra=extra):
                self.assertTrue(_validate_timeline(body(extra),4,strict=True,visible_subjects='甲'))

    def test_actor_binding_and_existing_accepted_visibility_spellings(self):
        for token in ['甲眼睛仅在0.0—1.2秒清楚可见',
                      '甲的双眼清晰仅在0.0-1.2秒完整可见',
                      '甲面部仅在0–1.2秒露出']:
            with self.subTest(token=token):
                self.assertEqual([],_validate_timeline(body(token),4,strict=True,visible_subjects='甲、乙'))
                self.assertTrue(_validate_timeline(body(token),4,strict=True,visible_subjects='乙'))

    def test_full_eye_consumer_still_rejects_blink_outside_visible_window(self):
        value={'可见主体':'甲','镜头类型':'人物近景',
               '起始状态':'甲双眼自然睁开，不处于半眨眼瞬间。',
               '画面内容':body(WINDOW)+'甲在3.5秒自然眨眼一次。','生成限制':''}
        self.assertEqual([],_validate_timeline(value['画面内容'],4,strict=True,visible_subjects='甲'))
        self.assertTrue(any('可见区间' in f for f in eye_contract_faults(value,duration=4)))

    def test_standard_three_phase_inputs_remain_unchanged(self):
        text=body('甲看向信纸')
        self.assertEqual(text,eye_visibility_timeline_scan(text,duration=4,visible_subjects='甲'))
        self.assertEqual([],_validate_timeline(text,4,strict=True))


if __name__=='__main__': unittest.main()
