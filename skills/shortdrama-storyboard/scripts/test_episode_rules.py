"""Synthetic timeline controls, not a generated-story usability benchmark."""
import copy
from decimal import Decimal
import json
import tempfile
from pathlib import Path
import unittest

from episode_rules import (CONTRACT, chapter_required, inspect_episodes,
                          inspect_borrowing, inspect_planned_budget, policy, review_obligations)
from rule_registry import check_registry_definition, load_registry, REFERENCE_ROOT, validate_registry


def episode_fixture():
    durations=[.5,.5,1.5,1.5]+[6]*16  # 100 seconds, 10% <1s and 20% <2s.
    shots=[];sources=[];beats=[];functions=[];utterances=[]
    for i,d in enumerate(durations):
        sid=f'EP01-{i+1:03}';src=f'S{i+1}';quote=f'甲展示编号{i+1}。'
        shots.append({'镜头编号':sid,'时长':d,'画面内容':quote,'机位':'平视近景。','起始状态':'甲面部在画内。','原文支撑句':quote,'心理音轨':'无'})
        sources.append({'source_id':src,'source_text':quote,'target_shots':[sid],'treatment':'保留','authorization':'无'})
        beats.append({'id':f'B{i+1}','kind':'opening_hook' if i==0 else 'ending_hook' if i==19 else 'advance',
            'shot_id':sid,'source_ids':[src],'delivery':{'field':'画面内容','quote':quote},'start':0,'end':d/2})
        functions.append({'source_id':src,'kind':'plot','shot_ids':[sid]})
        utterances.append({'kind':'dialogue','shot_id':sid,'start':0,'end':d*.4})
    ep={'episode_id':'EP01','core_actor':'甲','ending':'hook','beat_events':beats,'source_functions':functions}
    evidence={'source_ledger':sources,'episode_contract':{'contract':CONTRACT,'profile_id':policy()['profile_id'],
        'scope':'complete_episodes','episodes':[ep]},'production_derivation':{'source_graph_json':json.dumps({'production_plan':{'shots':[
            {'shot_id':shots[0]['镜头编号'],'visible':[{'kind':'eyes','subject':'甲','start':0,'end':5}]}]}})}}
    return {'shots':shots,'prevention_evidence':evidence},{'utterances':utterances}


class EpisodeRulesTests(unittest.TestCase):
    def setUp(self):self.obj,self.timing=episode_fixture()
    def inspect(self):return inspect_episodes(self.obj,timing=self.timing,production_verified=True)
    def codes(self):return {f['error_code'] for f in self.inspect()['faults']}
    def er(self):return self.obj['prevention_evidence']['episode_contract']['episodes'][0]

    def test_valid_numeric_profile_does_not_claim_semantics_or_face_area(self):
        r=self.inspect();self.assertEqual(r['faults'],[])
        self.assertIsNone(r['semantic_accuracy_percent'])
        self.assertEqual(r['episodes'][0]['seconds'],100)
        self.assertEqual(r['episodes'][0]['opening_face_status'],'partial')
        self.assertEqual(r['episodes'][0]['opening_face_area_status'],'pending_independent_review')
        self.assertEqual(len(r['independent_review_obligations']),4)

    def test_duration_thresholds_are_inclusive_and_fragments_are_not_episodes(self):
        for value,fail in [(90,False),(180,False),(180.01,True),(400,True),(89.99,False)]:
            o={'shots':[{'镜头编号':'EP01-001','时长':value}]}
            r=inspect_episodes(o)
            self.assertEqual('E-EPISODE-DURATION-001' in {f['error_code'] for f in r['faults']},fail)
            if not fail:self.assertEqual(r['status'],'insufficient_evidence')
        self.obj['shots']=self.obj['shots'][:1]
        self.assertIn('E-EPISODE-DURATION-001',self.codes())

    def test_planned_overflow_rejected_before_rendering(self):
        raw={'project':{},'shots':[{'episode_id':'EP01','ticks':[0,1810]}]}
        self.assertEqual(inspect_planned_budget(raw)[0]['code'],'E-EPISODE-DURATION-001')
        raw['shots'][0]['ticks'][-1]=1800
        self.assertEqual(inspect_planned_budget(raw),[])

    def test_short_thresholds_are_strict_not_less_or_equal(self):
        self.obj['shots'][0]['时长']=1
        self.assertIn('E-EPISODE-RHYTHM-001',self.codes())
        self.obj['shots'][0]['时长']=.999
        self.assertNotIn('E-EPISODE-RHYTHM-001',self.codes())
        self.obj['shots'][2]['时长']=2;self.obj['shots'][3]['时长']=2
        self.assertIn('E-EPISODE-RHYTHM-001',self.codes())

    def test_dialogue_uses_union_not_duplicate_tracks_or_psychology(self):
        before=self.inspect()['episodes'][0]['dialogue_seconds']
        self.timing['utterances']*=2
        self.assertEqual(self.inspect()['episodes'][0]['dialogue_seconds'],before)
        for u in self.timing['utterances']:u['kind']='psychological'
        self.assertIn('E-EPISODE-DIALOGUE-001',self.codes())
        self.assertEqual(self.inspect()['episodes'][0]['dialogue_seconds'],0)

    def test_dialogue_35_boundary_without_floating_tolerance(self):
        for u in self.timing['utterances']:
            shot=next(s for s in self.obj['shots'] if s['镜头编号']==u['shot_id'])
            u['end']=str(Decimal(str(shot['时长']))*Decimal('.35'))
        self.assertNotIn('E-EPISODE-DIALOGUE-001',self.codes())
        self.timing['utterances'][0]['end']='0.1749'
        self.assertIn('E-EPISODE-DIALOGUE-001',self.codes())

    def test_bare_mode_change_cannot_disable_chapter_intake(self):
        for obj in [{'chapter_intake':{}},{'source_ingestion':{'mode':'non_chapter','chapter_id':'51'}},
                    {'prevention_evidence':{'episode_contract':{'source_kind':'novel'}}}]:
            self.assertTrue(chapter_required(obj))
        self.assertFalse(chapter_required({'source_ingestion':{'mode':'non_chapter'}}))
        self.assertFalse(chapter_required({'prevention_evidence':'malformed'}))

    def test_source_only_beat_claim_and_duplicate_relabel_are_rejected(self):
        event=self.er()['beat_events'][0]
        event['delivery']['field']='原文支撑句'
        self.assertIn('E-EPISODE-BEAT-001',self.codes())
        event['delivery']['field']='画面内容'
        duplicate=copy.deepcopy(event);duplicate['id']='new-label';duplicate['kind']='advance'
        self.er()['beat_events'].append(duplicate)
        self.assertTrue(any('relabeling' in f['evidence'] for f in self.inspect()['faults']))

    def test_late_face_and_unverified_face_do_not_pass_opening(self):
        self.obj['prevention_evidence']['production_derivation']['source_graph_json']=json.dumps({'production_plan':{'shots':[
            {'shot_id':'EP01-006','visible':[{'kind':'eyes','subject':'甲','start':0,'end':5}]}]}})
        self.assertIn('E-EPISODE-OPENING-001',self.codes())
        r=inspect_episodes(self.obj,timing=self.timing)
        self.assertTrue(any(f.get('status')=='insufficient_evidence' for f in r['faults']))

    def test_montage_aggregates_shots_and_time(self):
        for f in self.er()['source_functions'][4:9]:f['kind']='montage'
        self.assertIn('E-EPISODE-SOURCE-BUDGET-001',self.codes())
        for f in self.er()['source_functions'][4:9]:f['kind']='plot'
        self.er()['source_functions'][4]['kind']='montage'
        self.assertNotIn('E-EPISODE-SOURCE-BUDGET-001',self.codes())

    def test_description_run_cannot_be_split_into_sources_to_evade_budget(self):
        for f in self.er()['source_functions'][4:7]:f['kind']='description'
        self.assertIn('E-EPISODE-SOURCE-BUDGET-001',self.codes())
        self.er()['source_functions'][5]['kind']='plot'
        self.assertNotIn('E-EPISODE-SOURCE-BUDGET-001',self.codes())

    def test_details_limit_is_per_shot_not_episode_source_count(self):
        for f in self.er()['source_functions'][4:9]:f.update(kind='detail',micro_elements=['手部位置'],baseline_seconds=4)
        self.assertNotIn('E-EPISODE-SOURCE-BUDGET-001',self.codes())
        self.er()['source_functions'][4]['micro_elements']=['1','2','3','4']
        self.assertIn('E-EPISODE-SOURCE-BUDGET-001',self.codes())

    def test_borrow_cannot_omit_authorization_next_boundary_or_debt(self):
        source=self.obj['prevention_evidence']['source_ledger'][0];source['treatment']='借用'
        self.assertTrue(inspect_borrowing(self.obj))
        record={'source_id':'S1','authorization_id':'grant-1','authorization_basis':'原始授权待独审核实','origin_episode':'EP02',
            'target_episode':'EP01','next_source_id':'S2','repayment_state':'boundary_replanned'}
        source['authorization']='grant-1';self.obj['prevention_evidence']['borrow_ledger']=[record]
        self.assertEqual(inspect_borrowing(self.obj),[])  # Coherent declaration, not verified permission.
        self.assertIn('borrowing',{r['kind'] for r in review_obligations(self.obj)})
        for key in ('authorization_id','next_source_id','repayment_state'):
            old=record.pop(key);self.assertTrue(inspect_borrowing(self.obj));record[key]=old

    def test_contract_identity_and_global_scope_cannot_be_faked(self):
        c=self.obj['prevention_evidence']['episode_contract'];c['max_seconds']=500
        self.assertIn('E-EPISODE-CONTRACT-001',self.codes());del c['max_seconds']
        c.update(scope='complete_series',series_episode_ids=['EP01','EP02'])
        self.assertIn('E-EPISODE-CONTRACT-001',self.codes())
        c['series_episode_ids']=['EP01']
        self.assertNotIn('E-EPISODE-CONTRACT-001',self.codes())

    def test_scene_projection_never_certifies_a_complete_episode(self):
        r=inspect_episodes(self.obj,shots=self.obj['shots'][:1],fragment=True)
        self.assertEqual(r['status'],'insufficient_evidence')
        self.assertEqual(r['independent_review_obligations'],[])

    def test_review_identity_changes_with_real_content_and_boundaries(self):
        ids=lambda:[r['evidence_id'] for r in review_obligations(self.obj)]
        old=ids();self.obj['shots'][0]['画面内容']+='新内容';self.assertNotEqual(ids(),old)
        old=ids();self.er()['core_actor']='乙';self.assertNotEqual(ids(),old)

    def test_bad_numeric_and_container_types_return_failed_reports(self):
        empty={'shots':[],'prevention_evidence':{'episode_contract':{
            'contract':CONTRACT,'profile_id':policy()['profile_id'],'scope':'complete_episodes','episodes':[]}}}
        self.assertEqual(inspect_episodes(empty)['status'],'fail')
        for v in [True,float('nan'),float('inf'),-1,'not time']:
            self.obj['shots'][0]['时长']=v
            self.assertEqual(self.inspect()['status'],'fail')
        self.obj['prevention_evidence']='wrong type'
        self.assertEqual(self.inspect()['status'],'fail')

    def test_deleting_rule_or_changing_trigger_and_threshold_fails_baseline(self):
        r=load_registry();r['atomic_rules']=[a for a in r['atomic_rules'] if a['rule_id']!='AR-R10-GLOBAL-004']
        self.assertTrue(check_registry_definition(r))
        r=load_registry();r['atomic_rules'][0]['trigger_condition']='never'
        self.assertTrue(check_registry_definition(r))
        r=load_registry();r['runtime']['episode_rule_policy']['max_seconds']=181
        self.assertTrue(check_registry_definition(r))
        r=load_registry();r['runtime']['field_content_policy']['camera_clause_patterns']=['.*']
        self.assertTrue(any('critical runtime' in f for f in check_registry_definition(r)))
        r=load_registry();r['runtime']['production_prevention_policy']['rules'][0]['enforcement']='optional'
        self.assertTrue(any('critical runtime' in f for f in check_registry_definition(r)))
        r=load_registry();rule=next(a for a in r['atomic_rules'] if a['rule_id']=='AR-R10-PG1-016')
        self.assertNotIn('三轮',rule['rule_name'])
        rule['fail_condition']='连续多轮仍局部补丁'
        self.assertTrue(any('rule semantics' in f for f in check_registry_definition(r)))
        r=load_registry();error=next(a for a in r['errors'] if a['error_code']=='E-SYSTEMIC-001')
        self.assertNotIn('第三轮',error['repair_action'])
        error['repair_action']='第三轮停止补丁并重建'
        self.assertTrue(any('repair guidance' in f for f in check_registry_definition(r)))

    def test_baseline_missing_from_selected_root_does_not_use_global_copy(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'rule_registry.json').write_bytes((REFERENCE_ROOT/'rule_registry.json').read_bytes())
            self.assertTrue(any('baseline missing' in f for f in validate_registry(root)))


if __name__=='__main__':unittest.main()
