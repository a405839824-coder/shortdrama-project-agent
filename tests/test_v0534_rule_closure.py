"""Real generation/entry/audit consumers plus source-specific counterexamples.

No historical entry fixture override in these current-policy tests. Tiny source
fixtures exercise the complete compiler, not content usability or face area.
"""
import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'server'),str(ROOT/'tests'),str(ROOT/'skills/shortdrama-storyboard/scripts')]
from episode_rules import CONTRACT, policy, review_obligations
from production_derivation import inspect_derivation
from production_plan_test_support import with_plan
from production_input import inspect_production_input
from shortdrama_mcp.service import StoryboardService
from shortdrama_mcp.production_route import require_current_production, check_borrow_permissions
from validate_storyboard import validate_project
from test_efficiency import valid_storyboard
from test_v0527_source_decision_independent_audit import complete_source_input, submission_for
import test_v0533_production_plan as plan_tests


class RuleClosureTests(unittest.TestCase):
    def test_independent_counterexample_baseline_cannot_silently_shrink(self):
        refs=ROOT/'skills/shortdrama-storyboard/references'
        baseline=json.loads((refs/'historical_rule_baseline.json').read_text(encoding='utf-8'))
        self.assertTrue(baseline['counterexample_files'])
        for filename,digest in baseline['counterexample_files'].items():
            path=ROOT/filename
            actual=hashlib.sha256(path.read_text(encoding='utf-8').encode()).hexdigest()
            self.assertEqual(actual,digest,'Counterexample changes require an explicit baseline migration, not automatic deletion.')

    def test_camera_rejects_both_unrelated_prose_and_cross_role_voice(self):
        for text in ['她此生再不会相信任何承诺。','平视50mm；压低声音，句末气息发虚。']:
            obj=valid_storyboard();obj['shots'][0]['机位']=text
            issues=validate_project(obj)['issues']
            self.assertTrue(any(i['error_code']=='E-FIELD-CONTENT-001' for i in issues),issues)
        for camera in ['左前方平视50mm。','摄影机在门内。','甲位于乙的画面左侧。']:
            obj=valid_storyboard();obj['shots'][0]['机位']=camera
            self.assertFalse(any(i['error_code']=='E-FIELD-CONTENT-001' and '机位' in i['location'] for i in validate_project(obj)['issues']))

    def test_required_plot_in_source_or_instructions_is_not_delivered(self):
        obj=valid_storyboard();needle='蓝色木盒被放上桌'
        obj['prevention_evidence']['change_lock']['must_include']=[needle]
        obj['shots'][0]['原文支撑句']+=needle
        r=validate_project(obj)
        self.assertTrue(any('must_include' in i['location'] for i in r['issues']))
        obj['shots'][0]['画面内容']+=needle+'。'
        self.assertFalse(any('must_include' in i['location'] for i in validate_project(obj)['issues']))

    def test_source_contract_and_borrow_changes_invalidate_boundary_hash(self):
        obj=valid_storyboard();old=StoryboardService._boundary_hash(obj)
        obj['source_ingestion']={'mode':'chapter_to_storyboard','chapter_id':'51'}
        self.assertNotEqual(old,StoryboardService._boundary_hash(obj));old=StoryboardService._boundary_hash(obj)
        obj['prevention_evidence']['episode_contract']={'scope':'complete_episodes'}
        self.assertNotEqual(old,StoryboardService._boundary_hash(obj));old=StoryboardService._boundary_hash(obj)
        obj['prevention_evidence']['borrow_ledger']=[{'source_id':'different'}]
        self.assertNotEqual(old,StoryboardService._boundary_hash(obj))

    def test_admin_and_producer_cannot_label_raw_canonical_as_planned(self):
        obj=valid_storyboard()
        for role in ['admin','producer']:
            with self.assertRaisesRegex(ValueError,'production_plan_required'):
                require_current_production(obj,work_order={'operation':'storyboard_create','account_role':role},scope_kind='full')
        self.assertEqual(require_current_production(obj,work_order={'operation':'storyboard_audit'},scope_kind='full'),'existing_artifact_audit_or_repair')

    def test_signed_historical_migration_is_an_explicit_distinct_route(self):
        # Only the service passes a verified migration record to this helper;
        # workbook integration tests exercise signature/tamper verification.
        self.assertEqual(require_current_production(valid_storyboard(),work_order={'operation':'storyboard_create'},
            scope_kind='full',workbook_migration={'verified_record':'synthetic'}),'signed_historical_workbook_migration')

    def test_complete_plan_contract_is_bound_and_requires_actual_episode_scope(self):
        f=plan_tests.ProductionPlanTests();f.setUp();result,canonical=f.record()
        self.assertTrue(inspect_derivation(canonical)['production_prevention_verified'])
        self.assertEqual(require_current_production(canonical,work_order={'operation':'storyboard_create'},scope_kind='scene'),'replayed_current_production')
        with self.assertRaisesRegex(ValueError,'episode_contract_required'):
            require_current_production(canonical,work_order={'operation':'storyboard_create'},scope_kind='full')
        canonical['prevention_evidence']['episode_contract']={'contract':CONTRACT,'scope':'complete_episodes'}
        self.assertFalse(inspect_derivation(canonical)['valid'])

    def test_generation_budget_rejects_real_decisions_before_canonical(self):
        data,raw,texts=complete_source_input();data['shots'][0]['ticks']=[0,1810]
        graph=with_plan(data,raw,texts)
        r=inspect_production_input(data,graph_bytes=graph,expected_graph_sha256=hashlib.sha256(graph).hexdigest(),require_production_plan=True)
        self.assertFalse(r['ready_for_canonical'])
        self.assertTrue(any(f['code']=='E-EPISODE-DURATION-001' and f['stage']=='episode_planned_budget' for f in r['findings']))

    def test_independent_audit_requires_each_current_episode_review(self):
        f=plan_tests.ProductionPlanTests();f.setUp()
        sid=f.data['shots'][0]['shot_id'];eid=f.data['shots'][0]['episode_id']
        # A fragment cannot pass numeric full-episode gates. This test isolates
        # the independent evidence consumer and does not mint a certificate.
        f.data['project']['prevention_evidence']['episode_contract']={'contract':CONTRACT,'profile_id':policy()['profile_id'],
            'scope':'complete_episodes','episodes':[{'episode_id':eid,'core_actor':'甲','ending':'hook','beat_events':[],'source_functions':[]}]}
        # Lower directly to isolate the consumer; official authoring rightly
        # rejects this 4-second complete-episode claim before rendering.
        f.replan();comp=f.compile();lower=f.lower()
        from author_storyboard import _render_decisions
        from production_derivation import attach_derivation
        candidate=attach_derivation(_render_decisions(lower['typed_decisions']),f.data,json.dumps(f.graph,ensure_ascii=False,sort_keys=True).encode(),production=lower,source_compilation=comp)
        proof=inspect_derivation(candidate);self.assertTrue(proof['valid'],proof['faults'])
        sub=submission_for(candidate,proof['source_obligations'])
        service=object.__new__(StoryboardService);service._validator_fingerprint='a'*64
        report={'status':'pass','issues':[],'quality_metrics':{'adjacent_continuity':{
            'expected_pair_count':0,'reviewed_pair_count':0,'ordered_pair_sha256':sub['adjacent_pair_order_sha256'],
            'pair_ids':[],'failed_pair_count':0,'proof_verified':True}}}
        with self.assertRaisesRegex(ValueError,'semantic_audit_episode_review_pending'):
            service._attach_independent_evidence_matrix(report,candidate,sub)
        requirements=review_obligations(candidate)
        for r in requirements:
            layer=next(l for l in sub['layers'] if l['layer']==r['layer'])
            layer['evidence_ids'].append(r['evidence_id'])
            for ref in r['field_evidence_ids']:
                if ref not in layer['evidence_ids']:layer['evidence_ids'].append(ref)
        enriched=service._attach_independent_evidence_matrix(report,candidate,sub)
        self.assertEqual(enriched['status'],'pass')
        layer=next(l for l in sub['layers'] if l['layer']==requirements[0]['layer'])
        layer['evidence_ids'].remove(requirements[0]['evidence_id'])
        with self.assertRaisesRegex(ValueError,'semantic_audit_episode_review_pending'):
            service._attach_independent_evidence_matrix(report,candidate,sub)


class CurrentMcpEntryTests(unittest.TestCase):
    def setUp(self):
        from test_v0527_mcp_production_authoring import McpProductionAuthoringTests
        self.f=McpProductionAuthoringTests();self.f.setUp()
        self.f.service.__dict__.pop('_require_current_production',None)
    def tearDown(self):self.f.tearDown()

    def test_raw_canonical_is_rejected_at_real_public_mcp(self):
        response=self.f.mcp('preflight_storyboard_draft',self.f.arguments(valid_storyboard()))
        self.assertFalse(response['data']['commit_ready'])
        self.assertIn('production_plan_required',str(response['data']['report']))
        self.f.assert_no_publication()

    def test_real_fragment_and_full_episode_use_distinct_contracts(self):
        args=self.f.arguments();response=self.f.mcp('preflight_storyboard_draft',args)
        self.assertIsNotNone(response['data']['compiled_storyboard'])
        metrics=response['data']['report']['quality_metrics']['episode_compliance']
        self.assertEqual(metrics['status'],'insufficient_evidence')
        args=self.f.arguments();args.update(scope_kind='full',scope_id='EP90')
        response=self.f.mcp('preflight_storyboard_draft',args)
        self.assertFalse(response['data']['commit_ready'])
        self.assertEqual(response['status'],'authoring_rejected')
        self.assertIsNone(response['data']['compiled_storyboard'])
        self.assertIn('episode_contract_required',str(response['data']['authoring_attempt']))

    def test_explicit_borrow_permission_is_bound_to_the_project_and_source(self):
        text='下一段来源';grant={'source_id':'S-borrow','source_sha256':hashlib.sha256(text.encode()).hexdigest(),
            'origin_episode':'EP02','target_episode':'EP01','next_source_id':'S-next','authorization_basis':'此合成工单明确授权本条借用'}
        args={'project_id':self.f.project,'operation':'storyboard_create','mode':'execute','authorization':'explicit_execute',
            'request_text':'合成借用权限验证','targets':['EP01'],'authoritative_source':'合成来源','latest_version':'1',
            'target_boundary':'EP01','must_keep':['S-borrow'],'must_remove':[],'protected_scope':[],
            'expected_outputs':['合成片段'],'borrow_permissions':[grant]}
        response=self.f.mcp('begin_project_task',args)
        minted=response['data']['borrow_permissions'][0]
        self.assertTrue(minted['authorization_id'].startswith('BORROW'))
        obj={'prevention_evidence':{'source_ledger':[{'source_id':'S-borrow','source_text':text}],
            'borrow_ledger':[{k:v for k,v in minted.items() if k!='source_sha256'}]}}
        permissions=self.f.service._project_borrow_permissions(self.f.namespace,self.f.project)
        check_borrow_permissions(obj,permissions)
        with self.assertRaisesRegex(ValueError,'borrow_permission_required'):check_borrow_permissions(obj,[])
        obj['prevention_evidence']['borrow_ledger'][0]['next_source_id']='different'
        with self.assertRaisesRegex(ValueError,'borrow_permission_required'):check_borrow_permissions(obj,permissions)
        args.update(mode='plan_only',authorization='plan_only')
        with self.assertRaisesRegex(Exception,'borrow_permission_authority_invalid'):
            self.f.mcp('begin_project_task',args)

    def test_imported_novel_cannot_be_downgraded_by_candidate_mode_before_generation(self):
        import test_v056_source_dialogue_authority as source_tests
        source_tests.SourceDialoguePreflightTests._attach_source(self.f,self.f.project,'甲坐在桌边。',role='novel')
        self.f.order=self.f.begin('storyboard_create')
        request=copy.deepcopy(self.f.request)
        request['decisions']['project']['source_ingestion']={'mode':'other'}
        request['decisions']['project']['prevention_evidence']['episode_contract']={
            'contract':CONTRACT,'scope':'fragment','source_kind':'other'}
        result=self.f.mcp('preflight_storyboard_draft',self.f.arguments(request))
        self.assertEqual(result['status'],'authoring_rejected')
        self.assertIsNone(result['data']['compiled_storyboard'])
        self.assertIn('chapter_source_scope_required',str(result['data']['authoring_attempt']))
        self.assertFalse((self.f.records()[0]/'compiler_attempts').exists())

    def test_candidate_cannot_create_borrow_permission_before_generation(self):
        request=copy.deepcopy(self.f.request)
        request['decisions']['project']['prevention_evidence']['borrow_ledger']=[{
            'source_id':'invented','authorization_id':'model-invented','origin_episode':'EP91',
            'target_episode':'EP90','next_source_id':'invented-next','authorization_basis':'self-declared'}]
        result=self.f.mcp('preflight_storyboard_draft',self.f.arguments(request))
        self.assertEqual(result['status'],'authoring_rejected')
        self.assertIn('borrow_permission_required',str(result['data']['authoring_attempt']))
        self.assertFalse((self.f.records()[0]/'compiler_attempts').exists())
        self.f.assert_no_publication()

    def test_direct_replayed_canonical_must_also_honor_imported_novel_scope(self):
        result=self.f.mcp('preflight_storyboard_draft',self.f.arguments())
        candidate=result['data']['compiled_storyboard']
        self.assertTrue(inspect_derivation(candidate)['production_prevention_verified'])
        import test_v056_source_dialogue_authority as source_tests
        source_tests.SourceDialoguePreflightTests._attach_source(self.f,self.f.project,'甲坐在桌边。',role='novel')
        result=self.f.mcp('preflight_storyboard_draft',self.f.arguments(candidate))
        self.assertFalse(result['data']['commit_ready'])
        self.assertIn('chapter_source_scope_required',str(result['data']['report']))
        with self.assertRaisesRegex(Exception,'chapter_source_scope_required'):
            self.f.mcp('commit_storyboard',{'project_id':self.f.project,'work_order_id':self.f.order,
                'storyboard':candidate,'file_name':'synthetic.xlsx'})

    def test_malformed_episode_and_borrow_envelopes_are_recorded_rejections(self):
        for key,value in [('prevention_evidence','invalid'),('borrow_ledger',{}),('borrow_ledger',['invalid'])]:
            with self.subTest(key=key,value=value):
                request=copy.deepcopy(self.f.request)
                p=request['decisions']['project']
                if key=='prevention_evidence':p[key]=value
                else:p['prevention_evidence'][key]=value
                result=self.f.mcp('preflight_storyboard_draft',self.f.arguments(request))
                self.assertEqual(result['status'],'authoring_rejected')
                self.assertIsNone(result['data']['compiled_storyboard'])
                self.assertIsNone(result['data']['quality'])
        self.assertTrue(all((p/'bridge_result.json').exists() for p in self.f.records()))
        self.f.assert_no_publication()


if __name__=='__main__':unittest.main()
