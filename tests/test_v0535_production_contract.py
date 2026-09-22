"""Regression of observed omissions, with a healthy full synthetic episode.

This is software acceptance, not source-story quality or a 98% benchmark.
No real project is read or modified and no validator is mocked.
"""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'server'), str(ROOT/'tests'), str(ROOT/'skills/shortdrama-storyboard/scripts')]
from author_storyboard import record_attempt, _render_decisions
from production_plan_test_support import frozen_plan
from production_decisions import lower_production_decisions, ProductionDecisionError
from source_decisions import compile_source_decisions
from production_derivation import inspect_derivation
from validate_storyboard import validate_project, project_formal_master, project_gpt_keyframe
from run_golden_tests import build_adjacent_continuity_audit
from field_content import field_content_findings
from episode_rules import inspect_episodes
from test_v0527_production_decisions import production_fixture, action, voice_spec


def complete_episode():
    f = ROOT/'tests/fixtures/synthetic_complete_episode'
    return json.loads((f/'input.json').read_text(encoding='utf-8')), json.loads((f/'graph.json').read_text(encoding='utf-8'))


def replan(data, graph):
    """Author a new synthetic plan and compute actual adjacent-state evidence."""
    evidence = data['project']['prevention_evidence']
    texts = {r['source_id']:r['source_text'] for r in evidence['source_ledger']}
    graph['production_plan'] = frozen_plan(data, graph, texts)
    graph['production_plan']['shots'][0]['visible'] = [{'kind':'eyes','subject':next(iter(data['initial_world']['actors'])),'start':0,'end':30}]
    raw = json.dumps(graph, ensure_ascii=False, sort_keys=True).encode()
    source = compile_source_decisions(raw, evidence['source_decisions'], source_texts=texts,
                                     expected_graph_sha256=hashlib.sha256(raw).hexdigest())
    preview = _render_decisions(lower_production_decisions(data, source_compilation=source)['typed_decisions'])
    evidence['adjacent_continuity_audit'] = build_adjacent_continuity_audit(
        preview['shots'], preview['prevention_evidence']['scene_state_ledger'])
    return raw


def generate(folder, data, graph):
    folder = Path(folder); folder.mkdir(parents=True, exist_ok=True)
    raw = replan(data, graph)
    (folder/'graph.json').write_bytes(raw)
    (folder/'input.json').write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
    result = record_attempt(folder/'input.json',folder/'attempts',source_graph_path=folder/'graph.json',require_production_plan=True)
    if not result.get('canonical_path'):
        raise AssertionError(result)
    return json.loads(Path(result['canonical_path']).read_text(encoding='utf-8'))


class ProductionContractTests(unittest.TestCase):
    def test_fixture_cut_uses_actual_boundary_and_refuses_to_invent_one(self):
        data, graph = complete_episode()
        replan(data, graph)
        anchor = graph['production_plan']['shots'][1]['design']['cut_anchor']
        self.assertEqual((anchor['shot_id'], anchor['action_id'], anchor['tick']),
                         ('EP90-001', 'SPEECH1', 35))
        data['shots'][0]['actions'][0]['end'] = 30
        original_actions = copy.deepcopy([shot['actions'] for shot in data['shots']])
        with self.assertRaisesRegex(ValueError, 'no actual boundary event: EP90-006'):
            replan(data, graph)
        self.assertEqual([shot['actions'] for shot in data['shots']], original_actions)

    def test_complete_episode_compiler_validator_and_projection_agree(self):
        data, graph = complete_episode()
        with tempfile.TemporaryDirectory() as folder:
            obj = generate(folder, data, graph)
        self.assertEqual(len(obj['shots']),23)
        self.assertEqual(obj['total_duration'],91.5)
        self.assertTrue(all(shot['时长'] <= 4 for shot in obj['shots']))
        proof = inspect_derivation(obj)
        self.assertTrue(proof['production_prevention_verified'],proof)
        report = validate_project(obj)
        self.assertEqual(report['issues'],[],report['issues'])
        self.assertEqual(report['quality_metrics']['episode_compliance']['status'],'pass')
        for shot in obj['shots']:
            if '说道' in shot['画面内容']:
                self.assertIn('口部入画时口型与对白一致',shot['画面内容'])
            for rendered in (project_formal_master(shot),project_gpt_keyframe(shot)):
                for note in ('本动作片段结束','该动作在本段','结束该同步片段','保持当前站位与手态'):
                    self.assertNotIn(note,rendered)
        changed = copy.deepcopy(obj);changed['shots'][0]['画面内容'] += '甲不看镜头。'
        self.assertFalse(inspect_derivation(changed)['valid'])

    def test_actual_speech_gaze_direct_and_unknown_cannot_pass_by_relabel(self):
        for point in ([0,2,1.5],None):
            data, graph = complete_episode()
            if point is None:data['initial_world']['actors']['甲']['gaze']=None
            else:data['initial_world']['points']['观察点']['position_xyz']=point
            with self.subTest(point=point),self.assertRaisesRegex(ProductionDecisionError,'speaking_gaze_'):
                replan(data,graph)

    def test_declared_hook_clock_cannot_move_actual_delivery(self):
        data, graph = complete_episode()
        beats=data['project']['prevention_evidence']['episode_contract']['episodes'][0]['beat_events']
        beat=beats[0]
        late=next(b for b in beats if b['id']=='B7')
        beat.update(shot_id=late['shot_id'],source_ids=copy.deepcopy(late['source_ids']),
                    delivery=copy.deepcopy(late['delivery']))
        beats.remove(late)
        beat.update(start=0,end=.2)
        with tempfile.TemporaryDirectory() as folder:
            obj=generate(folder,data,graph)
        report=validate_project(obj);r=report['quality_metrics']['episode_compliance']
        self.assertEqual(r['status'],'fail')
        self.assertTrue(any('clock differs' in x['evidence'] for x in report['issues']))
        self.assertTrue(any('opening hook' in x['evidence'] for x in report['issues']))

    def test_delivery_identity_and_source_are_both_required(self):
        data,graph=complete_episode()
        with tempfile.TemporaryDirectory() as folder:obj=generate(folder,data,graph)
        for field,value in [('event_ids',['SPEECH6']),('event_ids',[]),('quote','另一个句子')]:
            candidate=copy.deepcopy(obj)
            candidate['prevention_evidence']['episode_contract']['episodes'][0]['beat_events'][0]['delivery'][field]=value
            r=inspect_episodes(candidate,production_verified=True)
            self.assertTrue(any('beat_events[0]' in x['location'] for x in r['faults']))
        self.assertEqual(inspect_episodes(obj,production_verified=False)['status'],'fail')

    def test_current_written_delivery_uses_actual_initial_visibility(self):
        data,graph=complete_episode()
        beat=data['project']['prevention_evidence']['episode_contract']['episodes'][0]['beat_events'][0]
        beat.update(source_ids=['DOC'],start=0,end=3.5,
                    delivery={'field':'起始状态','quote':'若条件满足，明日交付。','event_ids':['state:EP90-001:P1']})
        with tempfile.TemporaryDirectory() as folder:obj=generate(folder,data,graph)
        report=validate_project(obj)
        self.assertFalse(any('beat_events[0]' in x['location'] for x in report['issues']),report['issues'])

    def test_combining_event_sets_cannot_count_an_existing_delivery_twice(self):
        from test_episode_rules import episode_fixture
        obj,timing=episode_fixture()
        evidence=obj['prevention_evidence'];trace=evidence['production_derivation']['action_trace']
        extra=copy.deepcopy(trace['shots'][0]['actions'][0]);extra['id']='A1-extra'
        trace['shots'][0]['actions'].append(extra)
        event=copy.deepcopy(trace['world_events'][0]);event.update(id='A1-extra',start=.25,end=.5)
        trace['world_events'].append(event)
        beats=evidence['episode_contract']['episodes'][0]['beat_events']
        combined=copy.deepcopy(beats[0]);combined.update(id='combined',kind='advance',end=.5)
        combined['delivery']['event_ids']=['A1','A1-extra'];beats.append(combined)
        report=inspect_episodes(obj,timing=timing,production_verified=True)
        self.assertTrue(any('relabeling' in f['evidence'] for f in report['faults']))

    def test_hold_prose_and_clock_keep_real_world_effects(self):
        data=production_fixture();data['shots'][0]['actions'][0].update(start=5,end=35)
        lowered=lower_production_decisions(data)
        obj=_render_decisions(lowered['typed_decisions'])
        picture=obj['shots'][0]['画面内容']
        self.assertNotIn('当前站位',picture)
        self.assertNotIn('片段结束',picture)
        self.assertIn('保持站立',picture)
        self.assertEqual(lowered['derivation']['world_events'][0]['start'],.5)
        self.assertEqual(lowered['derivation']['world_events'][0]['end'],3.5)
        self.assertEqual(lowered['derivation']['final_world']['completed'].count('hold1'),1)

    def test_whole_clause_ownership_rejects_clean_prefix_with_unrelated_tail(self):
        from authoring_contract_blocks import voice_block, ContractBlockError
        for key,tail in [('baseline','她终于知道自己的身世。'),('current_expression','她把信纸藏进袖中。')]:
            args=voice_spec();args[key]+=tail
            with self.subTest(field=key),self.assertRaises(ContractBlockError):
                voice_block(mode='spoken',dialogue_rows=[('甲','明日再议。')],**args)
        for field,text in [('机位','焦点在她此生再不会相信任何承诺'),
                           ('环境音效','她终于知道自己的身世。'),
                           ('光影','空气清澈。她终于知道自己的身世。'),
                           ('画面内容','甲保持站立。承接上镜状态。')]:
            self.assertTrue(field_content_findings({field:text}),field)
        for field,text in [('环境音效','近处衣料与呼吸声。'),('光影','空气清澈，高光不过曝。')]:
            self.assertEqual(field_content_findings({field:text}),[])

    def test_registered_physical_aliases_do_not_license_unrelated_sentences(self):
        for field,text in [('环境音效','纸张和指腹接触的轻响。对白时底声避让。'),
                           ('光影','纸纹清楚，正常曝光，人物面部明暗可读，衣料高光克制，暗部保留木构材质。')]:
            self.assertEqual(field_content_findings({field:text}),[])
            self.assertTrue(field_content_findings({field:text+'她终于知道自己的身世。'}))


class CurrentMcpContractTests(unittest.TestCase):
    def setUp(self):
        from test_v0527_mcp_production_authoring import McpProductionAuthoringTests
        self.f=McpProductionAuthoringTests();self.f.setUp()
        self.f.service.__dict__.pop('_require_current_production',None)
    def tearDown(self):self.f.tearDown()

    def request(self,data,graph):
        f=self.f;replan(data,graph)
        order=f.begin('storyboard_plan',planning=True)
        saved=f.mcp('commit_project_artifact',{'project_id':f.project,'work_order_id':order,
            'artifact_type':'storyboard_plan','output_format':'json','file_name':'complete-source-graph.json','structured_data':graph})
        _,path=f.service.store.get_artifact(f.namespace,f.project,saved['artifact_id'])
        f.order=f.begin('storyboard_create')
        return {'contract':'production-authoring-request-v1','decisions':data,'source_graph_artifact_id':saved['artifact_id'],
            'source_graph_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}

    def test_full_current_episode_scene_full_commit_and_signed_review(self):
        from semantic_audit_helpers import semantic_audit_for_artifact
        from episode_rules import review_obligations
        f=self.f;data,graph=complete_episode();request=self.request(data,graph)
        args={'project_id':f.project,'work_order_id':f.order,'storyboard':request,'scope_kind':'scene','scope_id':'ROOM-C1'}
        scene=f.mcp('preflight_storyboard_draft',args)
        self.assertEqual(scene['data']['report']['issues'],[],scene['data']['report']['issues'])
        args.update(scope_kind='full',scope_id='EP90')
        full=f.mcp('preflight_storyboard_draft',args)
        self.assertTrue(full['data']['commit_ready'],full['data'].get('report', {}).get('issues', []))
        obj=full['data']['compiled_storyboard']
        committed=f.mcp('commit_storyboard',{'project_id':f.project,'work_order_id':f.order,'storyboard':obj,
            'draft_preflight_id':full['trace_id'],'file_name':'合成整集链路验证.xlsx'})
        artifact=committed['artifact_id']
        submission=semantic_audit_for_artifact(f.service,f.fixture.settings,f.project,artifact)
        proof=inspect_derivation(obj)
        layer=next(x for x in submission['layers'] if x['layer']=='源文对白')
        for r in proof['source_obligations']:
            for ref in [r['evidence_id']]+r['field_evidence_ids']:
                if ref not in layer['evidence_ids']:layer['evidence_ids'].append(ref)
        for r in review_obligations(obj):
            layer=next(x for x in submission['layers'] if x['layer']==r['layer'])
            for ref in [r['evidence_id']]+r['field_evidence_ids']:
                if ref not in layer['evidence_ids']:layer['evidence_ids'].append(ref)
        for layer in submission['layers']:layer['summary']='合成固定样例的软件验收输入；不计真实内容正确率。'
        order,session=f.fixture._begin_audit(f.project,artifact,label='complete-current')
        audited=f.service.audit_storyboard(f.namespace,project_id=f.project,artifact_id=artifact,
            work_order_id=order,audit_cycle_id=None,handoff_artifact_id=committed['data']['handoff_artifact_id'],
            force_machine_recheck=False,semantic_audit=submission,request_session_id=session)
        self.assertEqual(audited.status,'pass',audited.data)
        self.assertTrue(audited.data['certificate']['certificate_id'])
        self.assertTrue(inspect_derivation(obj)['production_prevention_verified'])

    def test_rejected_actual_request_can_be_corrected_without_erasing_first_attempt(self):
        f=self.f;data,graph=complete_episode();healthy=self.request(data,graph)
        bad=copy.deepcopy(data);bad['shots'][0]['actions'][0]['voice']['baseline']+='她终于知道自己的身世。'
        bad_graph=copy.deepcopy(graph)
        texts={r['source_id']:r['source_text'] for r in bad['project']['prevention_evidence']['source_ledger']}
        bad_graph['production_plan']=frozen_plan(bad,bad_graph,texts)
        order=f.begin('storyboard_plan',planning=True)
        saved=f.mcp('commit_project_artifact',{'project_id':f.project,'work_order_id':order,
            'artifact_type':'storyboard_plan','output_format':'json','file_name':'bad-source-graph.json','structured_data':bad_graph})
        _,path=f.service.store.get_artifact(f.namespace,f.project,saved['artifact_id'])
        bad_request={'contract':'production-authoring-request-v1','decisions':bad,'source_graph_artifact_id':saved['artifact_id'],
                     'source_graph_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        args={'project_id':f.project,'work_order_id':f.order,'storyboard':bad_request,'scope_kind':'scene','scope_id':'ROOM-C1'}
        failed=f.mcp('preflight_storyboard_draft',args)
        self.assertEqual(failed['status'],'authoring_rejected')
        original=f.records()[0]/'request.original.json';before=original.read_bytes()
        self.assertEqual(json.loads(before),bad_request)
        args['storyboard']=healthy
        scene=f.mcp('preflight_storyboard_draft',args)
        self.assertEqual(scene['data']['report']['issues'],[])
        args.update(scope_kind='full',scope_id='EP90')
        full=f.mcp('preflight_storyboard_draft',args)
        self.assertTrue(full['data']['commit_ready'],full['data'].get('report', {}).get('issues', []))
        self.assertEqual(original.read_bytes(),before)
        self.assertGreaterEqual(len(f.records()),3)

    def test_legacy_parameter_envelope_mints_only_equally_authorized_grants(self):
        f=self.f
        grant={'source_id':'S-borrow','source_sha256':hashlib.sha256('合成来源'.encode()).hexdigest(),
               'origin_episode':'EP02','target_episode':'EP01','next_source_id':'S-next','authorization_basis':'合成测试明确授权'}
        args={'project_id':f.project,'operation':'storyboard_create','mode':'execute','authorization':'explicit_execute',
            'request_text':'合成旧窗口兼容','targets':['EP01'],'authoritative_source':'合成来源','latest_version':'1',
            'target_boundary':'EP01','must_keep':[],'must_remove':[],'protected_scope':[],'expected_outputs':['合成'],
            'confirmation':'task-options-v1:'+json.dumps({'contract':'storyboard-task-options-v1','borrow_permissions':[grant]},ensure_ascii=False)}
        result=f.mcp('begin_project_task',args)
        self.assertTrue(result['data']['borrow_permissions'][0]['authorization_id'].startswith('BORROW'))
        for mutate in [dict(borrow_permissions=[grant]),dict(authorization='plan_only',mode='plan_only'),
                       dict(operation='rule_improvement'),dict(confirmation='task-options-v1:{"contract":"storyboard-task-options-v1","contract":"storyboard-task-options-v1","borrow_permissions":[]}'),
                       dict(confirmation='task-options-v1:{"contract":"storyboard-task-options-v1","borrow_permissions":null}')]:
            with self.subTest(mutate=mutate),self.assertRaises(Exception):
                f.mcp('begin_project_task',{**args,**mutate})
        status=f.mcp('get_agent_status',{'full_self_test':False})
        self.assertTrue(status['data']['task_options_contract']['same_authorization_and_validation'])


if __name__=='__main__':unittest.main()
