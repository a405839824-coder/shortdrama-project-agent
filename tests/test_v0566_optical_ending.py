"""Explicit fantasy source mechanisms cannot become arbitrary world edits."""
import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tests'),str(ROOT/'skills/shortdrama-storyboard/scripts')]
import production_decisions as p
import production_optical as optical
import production_surface as surface
from author_storyboard import _render_decisions
from test_v0527_production_decisions import production_fixture, action
from test_v0537_source_actions import sourced, next_shot


def mirror():
    d=production_fixture(); w=d['initial_world']
    w['props']={'古镜':{'anchor':{'point':'案左'},'written_ref':None,
        'physical':{'material':'metal','contents':'none','integrity':'intact'},
        'surface':{'pattern':'still','glow':'faint','mist':'none','extent_m':.4,'particles':'local_glimmer'}}}
    w['points']['镜下地面']={'scene_id':'ROOM','position_xyz':[.2,.3,0]}
    w['points']['取景中心']['position_xyz']=[0,0,.8]
    d['shots'][0]['camera'].update(position_xyz=[0,2,1.2],hfov_deg=75,vfov_deg=96,frame='wide')
    return d


class OpticalEndingTests(unittest.TestCase):
    def test_shatter_is_source_bound_contact_then_ground_fragments(self):
        d=mirror(); src=sourced(d,'shatter_surface',{'prop':'古镜','hand':'right','to':'镜下地面'},text='甲抬手击碎古镜，镜片四散落地。')
        d['shots'][0]['editorial']['环境音效']={'mode':'action_bound','cues':[{'action_id':'event1','sound':'mirror_break','volume':'normal'}]}
        next_shot(d)['editorial']['环境音效']={'mode':'none'}
        result=p.lower_production_decisions(d,source_compilation=src)
        end=result['derivation']['final_world']['props']['古镜']
        self.assertEqual(end['anchor'],{'point':'镜下地面'})
        self.assertEqual(end['physical']['integrity'],'broken')
        self.assertEqual(end['surface']['glow'],'none')
        rows=_render_decisions(result['typed_decisions'])['shots']
        self.assertIn('2.0秒',rows[0]['环境音效'])
        self.assertIn('碎片散落',rows[1]['起始状态'])
        self.assertNotIn('镜面平整',rows[1]['起始状态'])
        self.assertEqual(result['typed_decisions']['shots'][0]['end_state'],result['typed_decisions']['shots'][1]['start_state'])
        self.assertFalse(result['derivation']['source_action_execution']['source_entailment_proved'])

    def test_shatter_cannot_be_null_source_remote_or_extra_mutation(self):
        d=mirror(); d['shots'][0]['actions']=[action('shatter_surface',prop='古镜',hand='right',to='镜下地面')]
        with self.assertRaisesRegex(p.ProductionDecisionError,'frozen physical source'):p.lower_production_decisions(d)
        d=mirror(); d['initial_world']['points']['镜下地面']['position_xyz']=[8,0,0]
        src=sourced(d,'shatter_surface',{'prop':'古镜','hand':'right','to':'镜下地面'})
        with self.assertRaisesRegex(p.ProductionDecisionError,'same local floor'):p.lower_production_decisions(d,source_compilation=src)
        d=mirror(); src=sourced(d,'shatter_surface',{'prop':'古镜','hand':'right','to':'镜下地面'})
        import production_actions
        original=production_actions.apply
        def corrupt(world,act,info,tick):
            original(world,act,info,tick);world['actors']['甲']['body_yaw']=99
        with patch.object(production_actions,'apply',corrupt),self.assertRaisesRegex(p.ProductionDecisionError,'outside'):
            p.lower_production_decisions(d,source_compilation=src)

    def test_phantom_fades_persists_and_cannot_resurrect_or_hide_bodily_actor(self):
        d=mirror();d['initial_world']['actors']['甲']['optical']={'opacity':1,'extent_m':.4,'glimmer':True}
        src=sourced(d,'phantom_fade',{'to':.4,'glimmer':True},text='甲身形渐渐透明。');next_shot(d)
        result=p.lower_production_decisions(d,source_compilation=src)
        self.assertAlmostEqual(result['derivation']['final_world']['actors']['甲']['optical']['opacity'],.4)
        self.assertIn('半透明',_render_decisions(result['typed_decisions'])['shots'][1]['起始状态'])
        d=mirror(); src=sourced(d,'phantom_fade',{'to':0,'glimmer':True})
        with self.assertRaisesRegex(p.ProductionDecisionError,'registered phantom'):p.lower_production_decisions(d,source_compilation=src)
        d['initial_world']['actors']['甲']['optical']={'opacity':.4,'extent_m':.4,'glimmer':True}
        src=sourced(d,'phantom_fade',{'to':1,'glimmer':True})
        with self.assertRaisesRegex(p.ProductionDecisionError,'resurrection unsupported'):p.lower_production_decisions(d,source_compilation=src)
        src=sourced(d,'phantom_fade',{'to':0,'glimmer':True})
        result=p.lower_production_decisions(d,source_compilation=src)
        final=result['derivation']['final_world']
        self.assertNotIn('甲',p._view(final,p._camera(final,d['shots'][0]['camera'],'ROOM','test'),'ROOM')['actors'])
        self.assertFalse(final['actors']['甲']['optical']['glimmer'])

    def test_violent_surface_has_bounded_static_and_dynamic_forms(self):
        value={'pattern':'surge','glow':'flicker','mist':'local_white_surge','extent_m':.5}
        surface.validate_surface(value,'test')
        self.assertIn('剧烈起伏',surface.surface_text('古镜',value))
        self.assertIn('翻涌',surface.surface_text('古镜',value))
        static=surface.surface_text('古镜',value,static=True)
        self.assertNotIn('翻涌',static);self.assertNotIn('剧烈',static)
        self.assertIn('团状白雾',static)
        value['extent_m']=1.1
        with self.assertRaises(p.ProductionDecisionError):surface.validate_surface(value,'test')

    def test_hold_does_not_describe_hidden_hands(self):
        d=production_fixture();d['shots'][0]['camera'].update(position_xyz=[0,1,1.5],hfov_deg=18,vfov_deg=28,frame='face')
        d['initial_world']['points']['取景中心']['position_xyz']=[0,0,1.45]
        d['shots'][0]['actions']=[action('hold',start=0,end=40)]
        result=p.lower_production_decisions(d)
        row=_render_decisions(result['typed_decisions'])['shots'][0]
        self.assertNotIn('手停在',row['画面内容'])
        self.assertEqual(result['derivation']['final_world']['actors']['甲']['hands'],d['initial_world']['actors']['甲']['hands'])

    def test_visible_foliage_only_has_source_authored_local_motion(self):
        d=production_fixture()
        d['initial_world']['props']['信纸']['foliage']={'motion':'breeze_petals','extent_m':1.2}
        d['initial_world']['props']['信纸']['display_name']='桃花枝'
        result=p.lower_production_decisions(d)
        row=_render_decisions(result['typed_decisions'])['shots'][0]
        self.assertIn('花枝随微风轻摆',row['环境动态'])
        self.assertIn('空中有零星花瓣',row['起始状态'])
        self.assertNotIn('飘落',row['起始状态'])
        with self.assertRaises(p.ProductionDecisionError):surface.validate_foliage({'motion':'storm','extent_m':8},'test')


if __name__=='__main__':unittest.main()
