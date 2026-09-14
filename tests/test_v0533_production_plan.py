"""Plan restrictions, neighbouring valid cases, actual compiler and MCP path."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'server'),str(ROOT/'tests'),str(ROOT/'skills/shortdrama-storyboard/scripts')]
from source_decisions import compile_source_decisions, SourceDecisionError
from production_decisions import lower_production_decisions, ProductionDecisionError
from production_input import inspect_production_input
from production_plan import (CONTRACT, digest, policy, runtime_context,
                             model_production_contract, validate_source_plan)
from production_plan_test_support import frozen_plan, with_plan
from test_v0527_source_decision_independent_audit import complete_source_input
from test_v0527_production_decisions import action, voice_spec
import test_v0527_source_decisions as source_tests
from author_storyboard import record_attempt
from production_derivation import inspect_derivation
from validate_storyboard import validate_project


class ProductionPlanTests(unittest.TestCase):
    def setUp(self):
        self.data, raw, self.texts = complete_source_input()
        self.graph = json.loads(with_plan(self.data, raw, self.texts))
        self.plan = self.graph['production_plan']

    def compile(self):
        raw = json.dumps(self.graph,ensure_ascii=False,sort_keys=True).encode()
        return compile_source_decisions(raw,self.data['project']['prevention_evidence']['source_decisions'],
            source_texts=self.texts,expected_graph_sha256=hashlib.sha256(raw).hexdigest())

    def lower(self):
        return lower_production_decisions(self.data,source_compilation=self.compile())

    def replan(self):
        self.plan = self.graph['production_plan'] = frozen_plan(self.data,self.graph,self.texts)

    def record(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root/'input.json').write_text(json.dumps(self.data), encoding='utf-8')
            (root/'graph.json').write_text(json.dumps(self.graph), encoding='utf-8')
            result = record_attempt(root/'input.json', root/'attempts',
                source_graph_path=root/'graph.json', require_production_plan=True)
            canonical = (json.loads(Path(result['canonical_path']).read_text(encoding='utf-8'))
                         if result['canonical_path'] else None)
            return result, canonical

    def rejects(self, text):
        with self.assertRaisesRegex((SourceDecisionError,ProductionDecisionError),text): self.lower()

    def test_policy_has_all_inputs_failure_exception_and_counterexample_contracts(self):
        p=policy()
        self.assertEqual([r['id'] for r in p['rules']],[f'PP-{i:02}' for i in range(1,13)])
        for rule in p['rules']:
            for field in ('inputs','prevention','on_failure','exceptions','enforcement','counterexample','limits','invalidates'):
                self.assertTrue(rule[field])
        self.assertIsNone(model_production_contract()['semantic_accuracy_percent'])
        self.assertEqual(set(model_production_contract()['plan_shape']), {'contract','context','decisions','knowledge','shots'})

    def test_good_plan_uses_actual_compiler_and_never_self_signs_semantics(self):
        before=copy.deepcopy((self.data,self.graph))
        r=self.lower()['derivation']['production_prevention']
        self.assertEqual(r['status'],'pass')
        self.assertEqual(r['plan_sha256'],digest(self.plan))
        self.assertEqual(r['semantic_status'],'pending_independent_review')
        self.assertIsNone(r['semantic_accuracy_percent'])
        self.assertEqual((self.data,self.graph),before)

    def test_each_context_fingerprint_rejects_stale_values(self):
        for key in runtime_context():
            with self.subTest(key=key):
                original=self.plan['context'][key]; self.plan['context'][key]='0'*64
                self.rejects('stale active'); self.plan['context'][key]=original
        self.plan['context']['source_sha256']={}
        self.rejects('source context')

    def test_initial_state_change_invalidates_the_plan(self):
        self.data['initial_world']['actors']['甲']['body_yaw']=5
        self.rejects('initial world changed')

    def test_all_owned_decision_partitions_are_required(self):
        self.plan['decisions'].pop()
        self.rejects('coverage differs')

    def test_duplicate_and_extra_partitions_rejected(self):
        self.plan['decisions'].append(copy.deepcopy(self.plan['decisions'][0]))
        self.rejects('unique')
        self.plan['decisions'][-1]['path']='/whole-payload'
        self.rejects('extra=')

    def test_camera_changes_require_upstream_replanning(self):
        self.data['shots'][0]['camera']['hfov_deg']=60
        self.rejects('differs from independently frozen')
        self.replan(); self.lower()

    def test_identity_and_source_scope_cannot_drift(self):
        self.data['shots'][0]['source_unit_ids']=['different-source']
        self.rejects('differs from independently frozen')

    def test_source_basis_cannot_omit_fact_evidence(self):
        self.plan['decisions'][0]['basis']='source_fact'
        self.rejects('at least one source')

    def test_derived_state_cannot_be_declared_as_freely_authored(self):
        self.plan['decisions'][0]['basis']='derived_state'
        self.rejects('compiler-owned')

    def test_locked_setting_requires_real_source_span(self):
        d=self.plan['decisions'][0]; d['basis']='locked_setting'
        self.rejects('actual source text')
        sid,text=next(iter(self.texts.items()))
        d['source_spans']=[{'source_id':sid,'start':0,'end':len(text),'text':text}]
        self.lower()
        d['source_spans'][0]['text']='伪造设定'
        self.rejects('differs from actual')

    def test_a_valid_director_choice_does_not_need_a_fabricated_source_fact(self):
        self.assertTrue(all(d['basis']=='director_choice' for d in self.plan['decisions']))
        self.lower()

    def test_missing_whole_shot_or_action_applicability_fails(self):
        original=copy.deepcopy(self.plan['shots']); self.plan['shots']=[]
        self.rejects('whole-shot plan coverage'); self.plan['shots']=original
        self.plan['knowledge']['uses'].pop()
        self.rejects('every actual action')

    def test_missing_required_event_or_information_cannot_be_worded_away(self):
        s=self.plan['shots'][0]; s['required_action_ids'].append('unexecuted')
        self.rejects('required event absent'); s['required_action_ids'].pop()
        s['source_use_ids'].append('missing-use')
        self.rejects('no actual source use')

    def test_purpose_is_delivered_in_this_shot_not_only_in_source_column(self):
        self.plan['shots'][0]['source_use_ids']=[]
        self.rejects('purpose has no actual declared delivery')

    def test_landmark_requirement_detects_real_camera_cropping(self):
        self.plan['shots'][0]['visible']=[{'kind':'left_hand','subject':'甲','start':0,'end':40}]
        self.lower()
        self.data['initial_world']['points']['取景中心']['position_xyz']=[0,0,1.5]
        self.data['shots'][0]['camera'].update(position_xyz=[0,4,1.5],hfov_deg=10,vfov_deg=10)
        self.replan()
        self.plan['shots'][0]['visible']=[{'kind':'left_hand','subject':'甲','start':0,'end':40}]
        self.rejects('not visible')

    def test_front_facing_requirement_and_back_view_are_distinct(self):
        self.data['shots'][0]['camera']['position_xyz']=[0,-4,1.5]; self.replan()
        self.plan['shots'][0]['visible']=[{'kind':'actor','subject':'甲','start':0,'end':40}]
        self.lower()
        self.plan['shots'][0]['visible'][0]['kind']='eyes'; self.rejects('not visible')

    def test_visibility_does_not_claim_readability_or_complete_mesh(self):
        self.plan['shots'][0]['visible']=[{'kind':'readable_text','subject':'信纸','start':0,'end':40}]
        self.rejects('model beyond declared landmarks')

    def test_visibility_window_must_exist_and_have_unique_requirements(self):
        v={'kind':'prop','subject':'信纸','start':0,'end':50}
        self.plan['shots'][0]['visible']=[v]; self.rejects('outside this shot')
        v['end']=40; self.plan['shots'][0]['visible'].append(copy.deepcopy(v)); self.rejects('duplicate visibility')

    def test_empty_action_static_shot_is_allowed_when_information_is_visible(self):
        self.data['shots'][0]['actions']=[]; self.replan()
        self.plan['shots'][0]['visible']=[{'kind':'current_written','subject':'信纸','start':0,'end':40}]
        result, canonical = self.record()
        self.assertEqual(result['status'], 'rendered_pending_preflight', result.get('findings'))
        self.assertTrue(inspect_derivation(canonical)['production_prevention_verified'])
        report = validate_project(canonical)
        self.assertFalse([i for i in report['issues'] if i['error_code'] in {'E-EXPRESSION-003', 'E-PERFORMANCE-001'}])
        self.assertNotIn('眨眼', canonical['shots'][0]['画面内容'])
        self.assertNotIn('眨眼', canonical['shots'][0]['生成限制'])
        self.assertIsNone(result['semantic_accuracy_percent'])

    def test_planned_entry_still_rejects_conflicting_real_eye_events(self):
        self.data['shots'][0]['actions'] = [
            action('eyelid', id='relax', to='relaxed'),
            action('eyelid', id='tense', to='slightly_tense')]
        self.replan()
        result, canonical = self.record()
        self.assertEqual(result['status'], 'rejected')
        self.assertIsNone(canonical)
        self.assertIn('overlapping writes', str(result['findings']))

    def test_changed_content_or_a_forged_plan_flag_cannot_get_performance_exception(self):
        result, canonical = self.record()
        self.assertEqual(result['status'], 'rendered_pending_preflight', result.get('findings'))
        canonical['shots'][0]['画面内容'] = '甲站在桌旁。'
        canonical['prevention_evidence']['production_derivation']['production_prevention_verified'] = True
        proof = inspect_derivation(canonical)
        self.assertFalse(proof['valid'])
        self.assertFalse(proof.get('production_prevention_verified', False))
        report = validate_project(canonical)
        self.assertTrue(any(i['error_code'] == 'E-GATE-001' and 'production_derivation' in i['location']
                            for i in report['issues']))

    def test_closed_paper_window_remains_valid(self):
        self.assertEqual(self.data['initial_world']['scenes']['ROOM']['light']['state'],'closed')
        self.lower()

    def speech(self):
        graph,uses,texts=source_tests.fixture()
        self.data['project']['prevention_evidence']['source_decisions']=uses
        self.data['project']['prevention_evidence']['source_ledger']=[{'source_id':sid,'source_text':t} for sid,t in texts.items()]
        self.data['initial_world']['props']['信纸']['written_ref']=None
        # Keep the knowledge tests focused on disclosure, with a physically
        # valid off-camera gaze during the fixture's speech.
        self.data['initial_world']['points']['观察点']['position_xyz']=[1,2,1.5]
        self.data['shots'][0]['actions']=[action('speak',id='speech',source_use_id='U1',voice=voice_spec())]
        self.graph,self.texts=graph,texts; self.replan()

    def test_same_source_identity_cannot_supply_narrator_knowledge_to_actor(self):
        self.speech(); self.lower()
        self.plan['knowledge']['initial']=[]
        self.rejects('before its acquisition')
        self.plan['knowledge']['uses'][0]['stance']='narrator'; self.rejects('cannot use narrator')

    def test_suspicion_requires_evidence_but_not_true_knowledge(self):
        self.speech(); self.plan['knowledge']['initial']=[]
        u=self.plan['knowledge']['uses'][0]; u['stance']='suspected'
        self.rejects('requires explicit source evidence')
        u['evidence_fact_ids']=u['fact_ids'].copy(); self.lower()

    def test_known_source_use_cannot_be_hidden_as_not_applicable(self):
        self.speech(); self.plan['knowledge']['uses'][0].update(stance='not_applicable',fact_ids=[])
        self.rejects('omits actual source facts')

    def test_real_audio_and_offscreen_voice_do_not_require_visible_lips(self):
        self.speech(); self.data['shots'][0]['camera']['position_xyz']=[0,-4,1.5]; self.replan(); self.lower()
        self.plan['shots'][0]['audio_action_ids']=[]; self.rejects('actual audio does not match')

    def test_actual_actor_cannot_be_changed_by_knowledge_declaration(self):
        self.speech(); self.plan['knowledge']['uses'][0]['actor']='乙'; self.rejects('differs from the actual')

    def test_unsupported_acquisition_is_explicit_and_does_not_become_known(self):
        self.speech()
        self.plan['knowledge']['acquisitions']=[{'id':'learn','actor':'甲','fact_ids':['F1'],
             'evidence_fact_ids':['F1'],'action_id':'speech','mode':'telepathy'}]
        self.rejects('not modelled')

    def learned_reaction(self, start=30):
        self.speech()
        listener=copy.deepcopy(self.data['initial_world']['actors']['甲'])
        listener.update(position_xyz=[1,0,0],reach_origin_xyz=[1,0,1.2],gaze=None)
        self.data['initial_world']['actors']['乙']=listener
        self.data['shots'][0]['actions'].append(action('hold',id='reaction',actor='乙',start=start,end=start+5))
        self.replan()
        use=next(x for x in self.plan['knowledge']['uses'] if x['action_id']=='reaction')
        use.update(fact_ids=['F1'],stance='reported')
        self.plan['knowledge']['acquisitions']=[{'id':'learn','actor':'乙','fact_ids':['F1'],
            'evidence_fact_ids':['F1'],'action_id':'speech','mode':'heard'}]

    def test_disclosure_end_and_dependent_start_at_same_tick_is_valid(self):
        self.learned_reaction(); self.lower()

    def test_listener_cannot_use_a_disclosure_before_it_finishes(self):
        self.learned_reaction(20); self.rejects('before actually hearing')

    def test_no_acquisition_and_self_hearing_do_not_grant_knowledge(self):
        self.learned_reaction(); self.plan['knowledge']['acquisitions']=[]
        self.rejects('before actually hearing')
        self.learned_reaction(); self.plan['knowledge']['acquisitions'][0]['actor']='甲'
        self.rejects('no actual same-scene disclosure')

    def test_hearing_a_claim_does_not_certify_its_truth(self):
        self.learned_reaction()
        next(x for x in self.plan['knowledge']['uses'] if x['action_id']=='reaction')['stance']='known'
        self.rejects('before its acquisition')

    def test_new_plan_is_part_of_actual_independent_source_review_obligations(self):
        compiled=self.compile().to_dict()
        for obligation in compiled['review_obligations']:
            binding=obligation['binding']['production_plan_review']
            self.assertEqual(binding['plan_sha256'],digest(self.plan))
            self.assertEqual(len(binding['required_questions']),3)
            self.assertEqual(binding['semantic_status'],'pending_independent_review')

    def test_new_production_requires_plan_but_legacy_inspection_is_labelled(self):
        del self.graph['production_plan']
        raw=json.dumps(self.graph).encode()
        old=self.lower()['derivation']['production_prevention']; self.assertEqual(old['status'],'insufficient_evidence')
        r=inspect_production_input(self.data,graph_bytes=raw,expected_graph_sha256=hashlib.sha256(raw).hexdigest(),require_production_plan=True)
        self.assertFalse(r['ready_for_canonical'])
        self.assertTrue(any(f.get('status')=='insufficient_evidence' for f in r['findings']))

    def test_missing_plan_is_recorded_before_rejection_and_produces_no_canonical(self):
        del self.graph['production_plan']
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); (root/'input.json').write_text(json.dumps(self.data),encoding='utf-8')
            (root/'graph.json').write_text(json.dumps(self.graph),encoding='utf-8')
            r=record_attempt(root/'input.json',root/'attempts',source_graph_path=root/'graph.json',require_production_plan=True)
            self.assertEqual(r['status'],'rejected'); self.assertIsNone(r['canonical_path'])
            self.assertEqual(next((root/'attempts').glob('*/input.original.json')).read_bytes(),(root/'input.json').read_bytes())


class ProductionPlanMcpTests(unittest.TestCase):
    def setUp(self):
        import test_v0527_mcp_production_authoring as fixtures
        self.f=fixtures.McpProductionAuthoringTests()
        self.f.setUp()

    def tearDown(self): self.f.tearDown()

    def test_model_receives_the_same_runtime_contract_as_the_actual_compiler(self):
        response=self.f.mcp('get_agent_status',{'full_self_test':False})
        contract=response['data']['production_prevention_contract']
        self.assertEqual(contract['context'],runtime_context())
        self.assertEqual(len(contract['rules']),12)
        self.assertEqual(contract['plan_contract'],CONTRACT)

    def test_real_mcp_rejects_decision_drift_before_artifact_creation(self):
        self.f.request['decisions']['shots'][0]['camera']['vfov_deg']=40
        response=self.f.mcp('preflight_storyboard_draft',self.f.arguments())
        self.assertEqual(response['status'],'authoring_rejected')
        self.assertIsNone(response['data']['compiled_storyboard'])
        self.assertIn('differs from independently frozen',str(response['data']['authoring_attempt']['findings']))
        self.f.assert_no_publication()
        root,=self.f.records()
        self.assertEqual(json.loads((root/'request.original.json').read_text()),self.f.request)

    def test_missing_plan_in_real_source_artifact_cannot_bypass_new_production(self):
        graph=json.loads(self.f.graph_bytes); del graph['production_plan']
        plan_order=self.f.begin('storyboard_plan',planning=True)
        saved=self.f.mcp('commit_project_artifact',{
            'project_id':self.f.project,'work_order_id':plan_order,
            'artifact_type':'storyboard_plan','output_format':'json','file_name':'legacy-source.json','structured_data':graph})
        record,path=self.f.service.store.get_artifact(self.f.namespace,self.f.project,saved['artifact_id'])
        request=copy.deepcopy(self.f.request)
        request.update(source_graph_artifact_id=saved['artifact_id'],source_graph_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        production_order=self.f.begin('storyboard_create')
        result=self.f.mcp('preflight_storyboard_draft',self.f.arguments(request,order=production_order))
        self.assertEqual(result['status'],'authoring_rejected')
        self.assertIn('insufficient_evidence',str(result['data']['authoring_attempt']['findings']))
        self.assertIsNone(result['data']['compiled_storyboard'])
        self.assertFalse(result['data']['artifact_created'])


if __name__=='__main__': unittest.main()
