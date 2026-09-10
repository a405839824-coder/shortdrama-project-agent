"""Public synthetic eye decisions through real text, events and both products."""
from __future__ import annotations

import copy
import math
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'skills/shortdrama-storyboard/scripts'), str(ROOT/'server'), str(ROOT/'tests')]
from authoring_contract_blocks import dynamic_eye_blocks, ContractBlockError, eye_blocks
from performance_checks import eye_contract_faults
from timing_events import validate_events
from test_v0527_authoring_contract_blocks import shot
import validate_storyboard as validator
from shortdrama_mcp.materializer import compile_formal_merged_field, compile_gpt_keyframe_instruction


def target(label):
    return {'id': label, 'label': label}


def phase(start=0., end=4., mode='hold', visibility='visible'):
    return {'start': start, 'end': end, 'visibility': visibility,
            'visibility_basis': '甲眼睛清楚可见' if visibility=='visible' else '甲眼睛不入画',
            'mode': mode if visibility=='visible' else 'none',
            'targets': [target('木牌')] if visibility=='visible' else [],
            'head_relation': 'still' if visibility=='visible' else None,
            'head_basis': '甲头部保持当前方向' if visibility=='visible' else None,
            'eyelid': 'relax' if visibility=='visible' else None,
            'blinks': [{'start':.3, 'end':.5}] if visibility=='visible' else []}


def input_pair():
    return ({'version':1,'subject':'甲','shot_id':'EP01-001','duration':4.,'phases':[phase()]},
            '【0.0—4.0秒】甲眼睛清楚可见；甲头部保持当前方向；甲双手放在膝上。')


def compile_shot(decision=None, body=None):
    default_decision, default_body = input_pair()
    result = dynamic_eye_blocks(decision or default_decision, picture_body=body or default_body)
    value = shot()
    value['画面内容'] = result['picture']
    value['起始状态'] += '甲视线落在木牌。'
    return result, value


class DynamicEyeDecisionsTests(unittest.TestCase):
    def test_hold_does_not_invent_shift_and_both_real_products_preserve_scope(self):
        decision,body=input_pair(); before=copy.deepcopy(decision)
        result,value=compile_shot(decision,body)
        self.assertEqual(decision,before)
        self.assertNotIn('换焦',result['picture'])
        self.assertEqual([],eye_contract_faults(value,duration=4))
        formal=validator.project_formal_master(value)
        static=validator.project_gpt_keyframe(value)
        self.assertEqual(formal,compile_formal_merged_field(value,assume_compact=True))
        self.assertEqual(static,compile_gpt_keyframe_instruction(value,assume_compact=True))
        self.assertIn('视线持续落在木牌',formal)
        self.assertNotIn('视线持续落在木牌',static)
        self.assertIn('双眼自然睁开',static)
        self.assertNotIn('在0.3秒',static)
        self.assertNotIn('passed',str(result))
        self.assertTrue(result['review_obligations'])

    def test_real_events_bind_actor_action_actual_basis_and_phase(self):
        result,value=compile_shot()
        project={'prevention_evidence':{'audio_workflow':{'timing':{'events':result['events']}}}}
        self.assertEqual([],validate_events(project,[value],[])['faults'])
        for event in result['events']:
            self.assertEqual('甲',event['actor'])
            self.assertIn(event['basis'],value['画面内容'])
        value['画面内容']=value['画面内容'].replace('甲视线持续落在木牌','乙视线持续落在木牌')
        self.assertTrue(validate_events(project,[value],[])['faults'])

    def test_hold_tracks_actual_head_without_an_invented_new_target(self):
        decision,body=input_pair(); p=decision['phases'][0]
        p.update(head_relation='tracks_target',head_basis='甲头部略低')
        result,value=compile_shot(decision,body.replace('甲头部保持当前方向','甲头部略低'))
        self.assertEqual([],eye_contract_faults(value,duration=4))
        self.assertNotIn('换焦',result['picture'])
        self.assertEqual(result['initial_target'],result['final_target'])

    def test_negated_head_action_cannot_supply_a_declared_relation(self):
        for relation,mode,head in [('eyes_first','shift','甲头部随后没有转动'),
                                   ('follows_head','head_led','甲头部没有低垂'),
                                   ('tracks_target','hold','甲头部并未偏转')]:
            decision,body=input_pair(); p=decision['phases'][0]
            p.update(mode=mode,head_relation=relation,head_basis=head)
            if mode!='hold': p['targets']=[target('木牌'),target('纸签')]
            with self.subTest(relation=relation),self.assertRaises(ContractBlockError):
                dynamic_eye_blocks(decision,picture_body=body.replace('甲头部保持当前方向',head))

    def test_explicit_shift_and_head_led_motion(self):
        for mode,relation,head in [('shift','still','甲头部保持当前方向'),
                                   ('shift','eyes_first','甲头部随后轻转'),
                                   ('head_led','follows_head','甲低头')]:
            with self.subTest(mode=mode,relation=relation):
                decision,body=input_pair(); p=decision['phases'][0]
                p.update(mode=mode,targets=[target('木牌'),target('纸签')],head_relation=relation,head_basis=head)
                body=body.replace('甲头部保持当前方向',head)
                result,value=compile_shot(decision,body)
                self.assertEqual([],eye_contract_faults(value,duration=4))
                self.assertEqual('纸签',result['final_target']['label'])
                self.assertEqual([],validate_events({'audio_workflow':{'timing':{'events':result['events']}}},[value],[])['faults'])

    def test_visible_then_back_only_checks_actual_visible_phase(self):
        decision,body=input_pair()
        decision['phases']=[phase(0,1),phase(1,4,visibility='hidden')]
        body='【0.0—1.0秒】甲眼睛清楚可见；甲头部保持当前方向。\n【1.0—4.0秒】甲眼睛不入画；甲背向机位。'
        result,value=compile_shot(decision,body)
        value['镜头类型']='人物全景'
        self.assertEqual([],eye_contract_faults(value,duration=4))
        self.assertEqual('',result['phase_appends'][1]['text'])
        self.assertIsNone(result['final_target'])
        self.assertTrue(all(e['end']<=1 for e in result['events']))

    def test_all_back_and_existing_closed_eye_route_remain_legal(self):
        decision,_=input_pair(); decision['phases']=[phase(0,4,visibility='hidden')]
        result,value=compile_shot(decision,'【0.0—4.0秒】甲眼睛不入画；甲背向机位。')
        value['可见主体']='甲（背影）'; value['起始状态']='甲背向机位。'
        self.assertEqual([],result['events']); self.assertEqual([],eye_contract_faults(value,duration=4))
        value=shot('closed'); value['起始状态']+='原文明确甲已经睡着，要求闭眼。'
        value['画面内容']='【0.0—4.0秒】甲双眼始终合拢，呼吸平稳。'
        self.assertEqual([],eye_contract_faults(value,duration=4))

    def test_no_silent_target_repair_between_visible_phases(self):
        decision,_=input_pair(); decision['phases']=[phase(0,2),phase(2,4)]
        decision['phases'][1]['blinks']=[]
        decision['phases'][1]['targets']=[target('纸签')]
        body='【0.0—2.0秒】甲眼睛清楚可见；甲头部保持当前方向。\n【2.0—4.0秒】甲眼睛清楚可见；甲头部保持当前方向。'
        with self.assertRaisesRegex(ContractBlockError,'preceding visible phase'):
            dynamic_eye_blocks(decision,picture_body=body)

    def test_shift_missing_or_same_endpoint_rejected(self):
        for targets in [[],[target('木牌')],[target('木牌'),target('木牌')],
                        [target('木牌'),{'id':'P2','label':'木牌'}]]:
            decision,body=input_pair(); decision['phases'][0].update(mode='shift',targets=targets)
            with self.subTest(targets=targets),self.assertRaises(ContractBlockError):
                dynamic_eye_blocks(decision,picture_body=body)

    def test_visibility_must_be_actual_actor_unquoted_affirmative(self):
        for text in ['乙眼睛清楚可见','甲说道：“甲眼睛清楚可见”','甲眼睛不入画',
                     '甲没有眼睛清楚可见','甲眼睛清楚可见；甲背向机位']:
            decision,body=input_pair(); body=body.replace('甲眼睛清楚可见',text)
            with self.subTest(text=text),self.assertRaises(ContractBlockError):
                dynamic_eye_blocks(decision,picture_body=body)

    def test_stage_and_blink_bounds_no_hidden_eye_action(self):
        for mutation in ['phase','blink','hidden']:
            decision,body=input_pair(); p=decision['phases'][0]
            if mutation=='phase': p['end']=3.9
            if mutation=='blink': p['blinks']=[{'start':3.9,'end':4.1}]
            if mutation=='hidden': p['visibility']='hidden'; p['visibility_basis']='甲眼睛不入画'; body=body.replace('甲眼睛清楚可见','甲眼睛不入画')
            with self.subTest(mutation=mutation),self.assertRaises(ContractBlockError):
                dynamic_eye_blocks(decision,picture_body=body)

    def test_no_overlay_of_existing_eye_track_or_unknown_keys(self):
        decision,body=input_pair()
        with self.assertRaisesRegex(ContractBlockError,'second track'):
            dynamic_eye_blocks(decision,picture_body=body+'甲在0.3秒眨眼一次。')
        decision['entailment']='passed'
        with self.assertRaises(ContractBlockError): dynamic_eye_blocks(decision,picture_body=body)

    def test_invalid_numeric_types_precision_and_nonfinite(self):
        for value in [True,'4',None,math.nan,math.inf,-1,4.01]:
            decision,body=input_pair(); decision['duration']=value
            with self.subTest(value=value),self.assertRaises(ContractBlockError):
                dynamic_eye_blocks(decision,picture_body=body)


class NaturalEyePathConsumerTests(unittest.TestCase):
    def faults(self,text,initial='甲视线落在木牌。'):
        value=shot(); value['起始状态']+=initial
        value['画面内容']='【0.0—1.0秒】甲在0.3秒自然眨眼一次。\n【1.0—3.2秒】'+text+'甲眼睑自然放松。\n【3.2—4.0秒】甲最终视线落在纸签。'
        return eye_contract_faults(value,duration=4)

    def test_ordered_two_endpoint_and_head_led_natural_prose(self):
        for text in ['甲眼球先移向木牌，头部略低，随后眼球换焦到纸签。',
                     '甲低头时眼球随头向下落向木牌，抬头后重新落在纸签。']:
            with self.subTest(text=text): self.assertEqual([],self.faults(text))

    def test_generic_microfocus_is_still_missing_endpoints_but_head_hold_is_recognized(self):
        faults=self.faults('甲对木牌作一次眼球微小换焦，头部保持当前方向。')
        self.assertTrue(any('缺视线从起点' in f for f in faults))
        self.assertFalse(any('缺眼球先动' in f for f in faults))

    def test_quoted_negated_or_other_actor_endpoint_does_not_complete_route(self):
        for text in ['甲说道：“眼球先移向木牌，头部略低，随后眼球换焦到纸签。”',
                     '甲眼球先移向木牌，头部略低，随后眼球并未换焦到纸签。',
                     '甲眼球先移向木牌，头部略低。乙随后眼球换焦到纸签。',
                     '甲眼球先移向木牌，头部略低，随后眼球换焦到木牌。']:
            with self.subTest(text=text):
                value=shot(); value['可见主体']='甲、乙' if '乙' in text else '甲'
                value['画面内容']='【0.0—4.0秒】'+text
                faults=eye_contract_faults(value,duration=4)
                self.assertTrue(any('甲[' in f and '缺视线从起点' in f for f in faults),faults)

    def test_named_hold_cannot_hide_unresolved_shift_or_target_departure(self):
        for added in ['甲眼球微小换焦。','甲视线移开。','甲视线从木牌换焦至木牌。']:
            result,value=compile_shot(); value['画面内容']+=added
            faults=eye_contract_faults(value,duration=4)
            self.assertTrue(any('缺视线从起点' in f for f in faults),faults)

    def test_same_target_head_adjustment_keeps_initial_action_final_anchor(self):
        value=shot(); value['起始状态']+='甲视线落在位于x=0.4、y=0.8米的纸签方向。'
        value['画面内容']='【0.0—1.0秒】甲在0.3秒自然眨眼一次。\n【1.0—3.2秒】甲眼球微移向纸签，头部只作小幅偏转，眼睑松紧自然，呼吸平稳。\n【3.2—4.0秒】甲最终视线落在纸签方向。'
        self.assertEqual([],eye_contract_faults(value,duration=4))
        value['起始状态']=value['起始状态'].replace('纸签方向','木牌方向')
        self.assertTrue(any('缺视线从起点' in f for f in eye_contract_faults(value,duration=4)))


if __name__=='__main__': unittest.main()
