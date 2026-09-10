"""Synthetic public author fragments through the actual two export paths."""
import copy
import math
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'server'), str(ROOT / 'skills/shortdrama-storyboard/scripts')]
from authoring_contract_blocks import (
    ContractBlockError, VIDEO_EYE_POLICY, eye_blocks, interior_lighting_blocks,
    psychological_safety_suffix, transition_block,
)
from shortdrama_mcp.materializer import compile_formal_merged_field, compile_gpt_keyframe_instruction
import validate_storyboard as validator


def light_args(night=False):
    return dict(
        source=dict(period='night' if night else 'day', kind='oil_lamp' if night else 'paper_window',
                    name='壁灯甲' if night else '纸窗甲', state='lit' if night else 'closed',
                    world_side='东', height_m=1.2 if night else 2.2),
        screen_side='右', receiving_surfaces=['甲的面颊', '木案'], weak_reflector='西侧木墙',
        main_tone='slightly_warm', shadow_tone='neutral', materials=['衣料', '木纹'],
        air='clear', exposure='normal',
    )


def shot(kind='face', audio='none', night=False):
    variants = {
        'face': ('甲', '人物中近景', '甲坐在木椅上，右手空着放在膝上。', 'natural_open'),
        'closed': ('甲', '人物近景', '甲侧卧在布枕上，双手在被沿内。', 'source_closed'),
        'back': ('甲（背影）', '背影中景', '甲站在木案南侧，身体朝北，双手自然垂下。', 'not_visible'),
        'hands': ('甲（仅双手）', '手部近景', '甲双手空着，静放木案南沿，袖口平整。', 'not_visible'),
        'object': ('无人物入画', '物件近景', '木牌平放木案上，纸签现有可读文字：“松木”。', 'no_people'),
    }
    visible, scale, initial, state = variants[kind]
    visibility = 'no_people' if kind == 'object' else 'hidden' if kind in ['back','hands'] else 'visible'
    eye = eye_blocks(subject=None if kind == 'object' else '甲', visibility=visibility,
                     state=state if visibility=='visible' else None,
                     source_basis='原文明确甲已经睡着。' if kind == 'closed' else None)
    args = light_args(night)
    if kind == 'object': args['receiving_surfaces'] = ['纸签', '木案']
    elif kind == 'hands': args['receiving_surfaces'] = ['甲的双手', '木案']
    elif kind == 'back': args['receiving_surfaces'] = ['甲的后背衣料', '木案']
    elif kind == 'closed': args['receiving_surfaces'] = ['甲的闭合眼睑', '布枕']
    light = interior_lighting_blocks(**args)
    picture = '【0.0—0.8秒】主体维持当前静态位置。\n【0.8—3.2秒】本镜无现场对白。\n【3.2—4.0秒】物件仍在已列位置。'
    voice = '无对白，不适用。'
    psych = '无。'
    if audio == 'dialogue':
        picture = ('【0.0—0.8秒】甲在0.3秒自然眨眼一次。\n'
                   '【0.8—3.2秒】甲说道：“木牌就在桌边。”甲口型与对白一致，不看镜头；重音“桌边”期间不眨眼。\n'
                   '【3.2—4.0秒】甲闭唇，目光落在画外乙的袖口。')
        voice = ('基础声线：年龄感为成年，中音区，厚薄适中，明暗中性，常态清楚。\n'
                 '言语行为：陈述木牌位置。\n当前表达：平静，意在告知。\n发声方式：\n'
                 '音量：正常交谈。\n语速与节奏：按本句语义短句说出。\n呼吸：自然吸气。\n'
                 '停顿：句末停顿。\n重音：“桌边”。\n尾音：落稳。\n禁止语气：喊叫、嘲讽。')
    elif audio == 'narration':
        psych = '客观旁白（画外）：“木牌留在桌边。”' + psychological_safety_suffix()
    elif audio != 'none': raise ValueError(audio)
    return {
        '镜头编号':'EP01-001', '时长':4.0, '累计时长':4.0, '功能类型':'局部信息', '场景连续性ID':'SYN-ROOM',
        '镜头类型':scale, '可见主体':visible, '空间站位描述':'木案位于房间中央。'+light['spatial_state_append'],
        '起始状态':initial+eye['initial_state_append'], '机位':'固定机位，50mm，当前主体与所列物件在明确取景范围内。',
        '运镜方式':'固定镜头。', '画面内容':picture, '音色':voice, '心理音轨':psych,
        '环境音效':'低环境底声，无额外配乐。', '环境动态':'木案纹理清楚，空气清澈。',
        **light['lighting_fields'],
        '生成限制':'9:16竖屏；禁止额外配乐；不看镜头；禁止字幕、水印。'+light['generation_limits_append']+eye['generation_limits_append'],
        **transition_block(method='hard_cut'), '参考图锚定':'无。', '覆盖原文':'合成输入单元',
        '原文依据类型':'合成测试', '原文支撑句':'甲已经睡着。' if kind=='closed' else '木牌留在桌边。',
    }


class AuthoringContractBlocksTests(unittest.TestCase):
    def projections(self, value):
        before=copy.deepcopy(value)
        formal=validator.project_formal_master(value)
        static=validator.project_gpt_keyframe(value)
        self.assertEqual(formal, compile_formal_merged_field(value, assume_compact=True))
        self.assertEqual(static, compile_gpt_keyframe_instruction(value, assume_compact=True))
        self.assertEqual(value,before)
        return formal,static

    def test_face_back_hands_object_and_source_closed_both_routes(self):
        for kind in ['face','closed','back','hands','object']:
            for night in [False,True]:
                with self.subTest(kind=kind,night=night):
                    value=shot(kind,night=night)
                    formal,static=self.projections(value)
                    self.assertIn(value['起始状态'],formal)
                    self.assertIn(value['光源来源'],formal)
                    self.assertIn('曝光正常',static)
                    self.assertIn('空气清澈',static)
                    self.assertNotIn('视频中的',static)
                    self.assertNotIn('禁止高频机械眨眼',static)
                    if kind=='object':
                        self.assertIn('“松木”',static)
                        self.assertNotIn('甲的面颊',static)
                    if kind=='closed':
                        self.assertIn('甲双眼合拢',static)
                        self.assertNotIn('双眼自然睁开',static)
                    if kind in ['back','hands']:
                        self.assertNotIn('人物眼神自然聚焦',static)

    def test_dialogue_and_narration_payload_not_replaced(self):
        for kind,audio in [('face','dialogue'),('face','narration'),('hands','narration'),('object','narration')]:
            with self.subTest(kind=kind,audio=audio):
                value=shot(kind,audio)
                formal,static=self.projections(value)
                if audio=='dialogue':
                    self.assertIn('“木牌就在桌边。”',formal)
                    self.assertIn('口型与对白一致',formal)
                    self.assertNotIn('木牌就在桌边',static)
                else:
                    self.assertIn(value['心理音轨'],formal)
                    self.assertIn('不计入角色对白时长',formal)
                    self.assertIn('完整计入实际音轨占时',formal)
                    self.assertNotIn('木牌留在桌边',static)
                self.assertNotIn('客观旁白',static)

    def test_registered_eye_policy_is_retained_formally_and_removed_statically(self):
        value=shot()
        self.assertIn('甲眼神自然聚焦；'+VIDEO_EYE_POLICY,value['生成限制'])
        formal,static=self.projections(value)
        self.assertIn('视频中的眼球换焦',formal)
        self.assertIn('禁止高频机械眨眼',formal)
        self.assertIn('眼神自然聚焦',static)
        self.assertNotIn('眼球换焦',static)
        self.assertNotIn('眼睑松紧变化',static)

    def test_named_focus_satisfies_the_generation_field_not_only_initial_state(self):
        from performance_checks import eye_contract_faults
        value=shot()
        message='生成限制缺‘眼神自然聚焦’'
        before=copy.deepcopy(value)
        before['生成限制']=before['生成限制'].replace('甲眼神自然聚焦；','')
        self.assertIn('甲眼神自然聚焦',before['起始状态'])
        self.assertIn(message,eye_contract_faults(before,duration=4.0))
        self.assertNotIn(message,eye_contract_faults(value,duration=4.0))
        # This small fixture deliberately does not claim a complete eye action
        # chain. Other failures must remain; the helper cannot invent actions.
        self.assertEqual([x for x in eye_contract_faults(before,duration=4.0) if x!=message],
                         eye_contract_faults(value,duration=4.0))

    def test_mixed_visible_open_and_source_closed_eyes_keep_actor_scope(self):
        value=shot('closed')
        value['可见主体']='甲、乙'
        opened=eye_blocks(subject='乙',visibility='visible',state='natural_open')
        value['起始状态']+='乙坐在木椅上。'+opened['initial_state_append']
        value['生成限制']+=opened['generation_limits_append']
        self.assertIn('乙眼神自然聚焦',value['生成限制'])
        self.assertNotIn('甲眼神自然聚焦',value['生成限制'])
        formal,static=self.projections(value)
        for output in [formal,static]:
            self.assertIn('甲双眼合拢',output)
            self.assertIn('乙双眼自然睁开',output)
            self.assertIn('乙眼神自然聚焦',output)
            self.assertNotIn('甲双眼自然睁开',output)
            self.assertNotIn('人物眼神自然聚焦',output)

    def test_fragments_do_not_overwrite_or_invent_start_or_motion(self):
        initial='甲的右手按在木牌左缘。'
        action='甲把木牌递给乙。'
        before=(initial,action)
        eye=eye_blocks(subject='甲',visibility='visible',state='natural_open')
        combined=initial+eye['initial_state_append']
        self.assertTrue(combined.startswith(initial))
        self.assertEqual((initial,action),before)
        self.assertNotRegex(eye['initial_state_append'],r'\d+秒|随后|转身|抬手|换焦')
        self.assertEqual(eye_blocks(subject=None,visibility='no_people',state=None),{'initial_state_append':'','generation_limits_append':''})
        hidden=eye_blocks(subject='甲',visibility='hidden',state=None)
        self.assertEqual(hidden,{'initial_state_append':'甲眼睛不入画。','generation_limits_append':''})
        self.assertNotIn('面部',hidden['initial_state_append'])
        for kind in ['closed','back','hands','object']:
            self.assertNotIn('眼神自然聚焦',shot(kind)['生成限制'])

    def test_day_and_night_light_context_checks_have_explicit_sources(self):
        for night in [False,True]:
            for kind in ['face','back','hands','object']:
                with self.subTest(night=night,kind=kind):
                    value=shot(kind,night=night)
                    faults=validator.lighting_context_faults(value,legacy_checker=validator._lighting_context_faults)
                    self.assertEqual(faults,[])
                    if night:
                        self.assertIn('油灯',value['空间站位描述'])
                        self.assertNotIn('天光',value['光源来源'])
                    else:
                        self.assertIn('纸窗甲关闭',value['空间站位描述'])
                        self.assertNotIn('灯',value['光源来源'])
                    self.assertNotIn('烟',value['光源来源'])

    def test_explicit_open_window_and_absent_weak_reflector(self):
        args=light_args();args['source']['state']='open';args['weak_reflector']=None
        result=interior_lighting_blocks(**args)
        self.assertIn('纸窗甲敞开',result['spatial_state_append'])
        self.assertNotIn('木墙',str(result))
        self.assertNotIn('反射补光',str(result))
        self.assertNotIn('关闭',str(result))

    def test_transition_uses_only_named_destination_and_registered_cut(self):
        for method in ['hard_cut','dissolve']:
            result=transition_block(method=method,destination_place='书房',destination_subject='木牌')
            self.assertIn('书房的木牌',result['切镜/转场提示词'])
            value=shot('object');value.update(result);self.projections(value)
        self.assertEqual(transition_block(method='hard_cut')['切镜/转场提示词'],'硬切。')

    def test_unknown_dynamic_policy_is_not_silently_exempted(self):
        value=shot();value['生成限制']+='视频中的抬手按画面内容所列时点执行。'
        for fn in [validator.project_gpt_keyframe,lambda s:compile_gpt_keyframe_instruction(s,assume_compact=True)]:
            with self.assertRaisesRegex(ValueError,'keyframe_static_contract_violation'):fn(value)

    def test_eye_input_rejections(self):
        bad=[dict(subject='甲',visibility='visible',state='unknown'),dict(subject=None,visibility='visible',state='natural_open'),
             dict(subject='甲',visibility='visible',state='source_closed'),dict(subject='甲',visibility='visible',state='source_closed',source_basis=' '),
             dict(subject='甲',visibility='no_people',state=None),dict(subject='甲',visibility='visible',state='natural_open',source_basis='闭眼来源'),
             dict(subject='甲；下一镜',visibility='visible',state='natural_open'),
             dict(subject='甲',visibility='hidden',state='natural_open'),
             dict(subject='甲',visibility='hidden',state='source_closed',source_basis='明确闭眼来源'),
             dict(subject='甲',visibility='hidden',state=None,source_basis='明确闭眼来源'),
             dict(subject=None,visibility='no_people',state='natural_open'),
             dict(subject='甲',visibility='visible',state=None),dict(subject='甲',visibility='unknown',state=None)]
        for args in bad:
            with self.subTest(args=args),self.assertRaises(ContractBlockError):eye_blocks(**args)

    def test_lighting_source_pairing_and_unknown_key_rejections(self):
        for update in [dict(period='dusk'),dict(kind='unknown'),dict(period='night'),dict(state='unknown'),
                       dict(world_side='左或右'),dict(name='待定'),dict(extra=True)]:
            args=light_args();args['source'].update(update)
            with self.subTest(update=update),self.assertRaises(ContractBlockError):interior_lighting_blocks(**args)
        for state in ['unlit','unknown',False]:
            args=light_args(True);args['source']['state']=state
            with self.subTest(state=state),self.assertRaises(ContractBlockError):interior_lighting_blocks(**args)

    def test_numeric_and_surface_inputs_are_explicit_and_finite(self):
        for value in [True,False,0,-1,math.nan,math.inf,-math.inf,10**1000,'2.2',None]:
            args=light_args();args['source']['height_m']=value
            with self.subTest(value=value),self.assertRaises(ContractBlockError):interior_lighting_blocks(**args)
        for key,value in [('receiving_surfaces',[]),('receiving_surfaces','面部'),('receiving_surfaces',['面部','面部']),
                          ('materials',[]),('weak_reflector','未知'),('screen_side','上'),('main_tone','maybe'),
                          ('shadow_tone',''),('air','fog'),('exposure','unspecified')]:
            args=light_args();args[key]=value
            with self.subTest(key=key,value=value),self.assertRaises(ContractBlockError):interior_lighting_blocks(**args)

    def test_input_objects_are_not_mutated(self):
        args=light_args();before=copy.deepcopy(args)
        interior_lighting_blocks(**args)
        self.assertEqual(args,before)

    def test_registered_cross_context_labels_and_unsafe_names_rejected(self):
        for value in sorted(validator.CROSS_SHOT_TERMS)+['未知','甲或乙','木牌；抬手','[[staging:camera]]','']:
            with self.subTest(value=value),self.assertRaises(ContractBlockError):
                transition_block(method='hard_cut',destination_place=value)
        with self.assertRaises(ContractBlockError):transition_block(method='unknown')


if __name__=='__main__':unittest.main()
