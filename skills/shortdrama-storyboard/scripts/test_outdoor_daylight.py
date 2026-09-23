"""Bundled synthetic daylight controls; no source or formal acceptance claim."""
import copy
import unittest

import production_decisions as p
from production_actions import prepare
from production_environment import render_environment
from lighting_checks import lighting_context_faults
from validate_storyboard import _lighting_context_faults


def fixture():
    light = {'period':'day','kind':'outdoor_daylight','name':'院中天光','state':'present',
             'direction_xyz':[1,0,1], 'main_tone':'neutral','shadow_tone':'neutral','materials':['衣料']}
    world = {'scenes':{'YARD':{'label':'室外庭院','door_window_state':{},'light':light,'barriers':[]}},
             'actors':{'甲':{'scene_id':'YARD','position_xyz':[0,0,0],'body_yaw':0,'head_yaw':0,
                'eye_height':1.5,'eye_state':'natural_open','posture':'站立','hands':{'left':[-.2,.3,1],'right':[.2,.3,1]},
                'gaze':None,'reach_origin_xyz':[0,0,1.2],'reach_m':.9}},
             'props':{},'points':{'取景中心':{'scene_id':'YARD','position_xyz':[0,0,1.1]}}}
    shot = {'scene_id':'YARD','continuity_id':'YARD-C1',
            'camera':{'position_xyz':[0,4,1.5],'target':'取景中心','hfov_deg':50,'vfov_deg':50,'frame':'wide'},
            'editorial':{'功能类型':'合成光源控制','参考图锚定':'无外部图像。','原文依据类型':'合成控制',
                         '原文支撑句':'甲在院中。','环境音效':{'mode':'none'},'环境动态':{'mode':'shared_world'}}}
    return world, shot


def render(raw, shot):
    world = p._world(raw, {})
    camera = p._camera(world, shot['camera'], 'YARD', 'camera')
    fields, _ = p._fields(world, shot, camera, p._view(world, camera, 'YARD'), {})
    render_environment(shot['editorial'], initial_world=world, final_world=world, scene_id='YARD', actions=[])
    return world, fields


class OutdoorDaylightTests(unittest.TestCase):
    def test_explicit_outdoor_light_reaches_actual_field_and_environment_renderers(self):
        raw, shot = fixture(); world, fields = render(raw, shot)
        self.assertEqual(lighting_context_faults(fields, legacy_checker=_lighting_context_faults), [])
        for text in (fields['光源来源'], fields['生成限制']):
            for absent in ('室内','纸窗','油灯','离地'): self.assertNotIn(absent, text)
        self.assertEqual(world['scenes']['YARD']['light']['direction_xyz'], [1,0,1])
        self.assertIn('45度', fields['光源来源'])
        self.assertNotIn('衰减', fields['光影'])
        self.assertIn('表面随朝向', fields['光影'])

    def test_daylight_direction_uses_camera_orientation_not_camera_translation(self):
        raw, shot = fixture(); _, first = render(raw, shot)
        raw['actors']['甲']['position_xyz'][0] += 10
        raw['actors']['甲']['reach_origin_xyz'][0] += 10
        raw['points']['取景中心']['position_xyz'][0] += 10
        shot['camera']['position_xyz'][0] += 10
        _, moved = render(raw, shot)
        self.assertEqual(first['光源方向'], moved['光源方向'])
        raw, shot = fixture(); shot['camera']['position_xyz'] = [0,-4,1.5]
        _, reversed_shot = render(raw, shot)
        self.assertIn('画面左侧', first['光源方向'])
        self.assertIn('画面右侧', reversed_shot['光源方向'])

    def test_invalid_light_does_not_acquire_an_invented_source_or_position(self):
        for update in ({'period':'night'},{'state':'lit'},{'position_xyz':[3,0,2]},
                       {'direction_xyz':[0,0,1]},{'direction_xyz':[1,0,-1]},
                       {'direction_xyz':[True,0,1]},{'direction_xyz':[1,float('nan'),1]}):
            with self.subTest(update=update):
                raw, shot = fixture(); raw['scenes']['YARD']['light'].update(update)
                with self.assertRaises(p.ProductionDecisionError): render(raw, shot)
        raw, shot = fixture(); raw['scenes']['YARD']['label']='书房'
        with self.assertRaisesRegex(p.ProductionDecisionError,'indoor scene'): render(raw, shot)

    def test_bridge_uses_same_daylight_validation_and_cannot_relocate_scene(self):
        raw, shot = fixture(); world, _ = render(raw, shot)
        light = copy.deepcopy(world['scenes']['YARD']['light']); light['direction_xyz']=[-1,0,1]
        action = {'id':'sunchange','actor':'甲','kind':'scene_light','source_use_id':None,'scene_id':'YARD','light':light,
                  'start':0,'end':10,'after':[]}
        prepare(world, action, {}, 'bridge.light', bridge=True)
        with self.assertRaises(p.ProductionDecisionError): prepare(world, action, {}, 'shot.light')
        light['period']='night'
        with self.assertRaises(p.ProductionDecisionError): prepare(world, action, {}, 'bridge.light', bridge=True)
        light.update(kind='oil_lamp',state='lit')
        with self.assertRaisesRegex(p.ProductionDecisionError,'cannot relocate'):
            prepare(world, action, {}, 'bridge.light', bridge=True)


if __name__ == '__main__': unittest.main()
