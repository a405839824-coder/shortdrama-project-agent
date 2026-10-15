import copy
import hashlib
import unittest

from shortdrama_mcp.source_dialogue import parse_source_dialogue_authority,SourceDialogueAuthorityError
from shortdrama_mcp.screenplay_scope import select_partition,source_paragraphs

PLAY='''春日（完整版｜纯台词剧本）
人物
江氏：府中长女
赵氏：她的朋友
第一幕 初见
【场景：庭院】
（江氏走到门边）
江氏：我的身份？嬷嬷，你可知道？
人人唤我“江氏”，可我还有自己的名字。
（江氏转头看向赵氏）
我想亲自去看。
（赵氏沉默）
赵氏：……
第二幕 春色
【场景：花庭】
赵氏：好。
'''


def partition(text=PLAY):
    return {'contract':'script-episode-partition-v1','source_artifact_id':'ART-source',
            'source_sha256':hashlib.sha256(text.encode()).hexdigest(),
            'episodes':[{'episode_id':'EP01','paragraph_range':[5,13]},
                        {'episode_id':'EP02','paragraph_range':[14,16]}]}


class PurePlayTests(unittest.TestCase):
    def test_source_only_dialogue_roster_and_stage_are_separate(self):
        rows=parse_source_dialogue_authority(PLAY,source_role='script')
        self.assertEqual([(r.speaker,r.text) for r in rows],[
            ('江氏','我的身份？嬷嬷，你可知道？'),('江氏','人人唤我“江氏”，可我还有自己的名字。'),
            ('江氏','我想亲自去看。'),('赵氏','好。')])
        for row in rows:self.assertEqual(PLAY[row.char_offset:row.char_offset+len(row.text)],row.text)
        self.assertEqual(rows,parse_source_dialogue_authority(PLAY.replace('\n','\r\n'),source_role='script'))

    def test_other_scripts_do_not_gain_implicit_continuations(self):
        with self.assertRaises(SourceDialogueAuthorityError):
            parse_source_dialogue_authority('江氏：好。\n我还要走。',source_role='script')

    def test_cast_profile_malformed_does_not_fall_back(self):
        with self.assertRaisesRegex(SourceDialogueAuthorityError,'pure_dialogue_play_invalid'):
            parse_source_dialogue_authority(PLAY.replace('人物','演员'),source_role='script')

    def test_different_actor_stage_cannot_inherit_speaker(self):
        with self.assertRaisesRegex(SourceDialogueAuthorityError,'missing speaker'):
            parse_source_dialogue_authority(PLAY.replace('（江氏转头看向赵氏）','（赵氏转头）'),source_role='script')

    def test_stage_cannot_hide_direct_speech(self):
        for stage in ['（江氏说：“走。”）','（江氏说走吧）','（江氏：走。）']:
            with self.subTest(stage=stage),self.assertRaisesRegex(SourceDialogueAuthorityError,'speech within stage'):
                parse_source_dialogue_authority(PLAY.replace('（江氏走到门边）',stage),source_role='script')

    def test_scene_voice_requires_source_staging(self):
        source=PLAY.replace('【场景：花庭】','【场景：古镜之前】\n（镜面回荡回声，无实体人影）').replace('赵氏：好。','古镜残音：好。')
        self.assertEqual(parse_source_dialogue_authority(source,source_role='script')[-1].speaker,'古镜残音')
        with self.assertRaises(SourceDialogueAuthorityError):
            parse_source_dialogue_authority(source.replace('【场景：古镜之前】','【场景：花庭】'),source_role='script')

    def test_unbalanced_literal_quotes_fail(self):
        with self.assertRaises(SourceDialogueAuthorityError):
            parse_source_dialogue_authority(PLAY.replace('“江氏”','“江氏'),source_role='script')

    def test_complete_partition_selects_exact_whole_acts(self):
        rows=parse_source_dialogue_authority(PLAY,source_role='script');plan=partition()
        selected,scope=select_partition(plan,PLAY,rows,['EP02'],'ART-source',plan['source_sha256'])
        self.assertEqual([x.text for x in selected],['好。'])
        self.assertEqual(scope['source_occurrence_count'],4)
        self.assertEqual(selected[0].occurrence_id,rows[-1].occurrence_id)

    def test_partition_rejects_gaps_overlaps_missing_tail_partial_act_and_stale_source(self):
        rows=parse_source_dialogue_authority(PLAY,source_role='script')
        variants=[]
        for start,end in [(6,13),(5,12),(5,14),(5,99)]:
            plan=partition();plan['episodes'][0]['paragraph_range']=[start,end];variants.append(plan)
        plan=partition();plan['episodes'].pop();variants.append(plan)
        plan=partition();plan['source_sha256']='wrong';variants.append(plan)
        for plan in variants:
            with self.subTest(plan=plan),self.assertRaises(ValueError):
                select_partition(plan,PLAY,rows,['EP01'],'ART-source',partition()['source_sha256'])

    def test_partition_rejects_unknown_duplicate_and_reverse_episode_selection(self):
        for ids in [[],['EP03'],['EP01','EP01'],['EP02','EP01']]:
            with self.subTest(ids=ids),self.assertRaises(ValueError):
                select_partition(partition(),PLAY,[],ids,'ART-source',partition()['source_sha256'])


class PartitionServiceTests(unittest.TestCase):
    def setUp(self):
        import test_v056_source_dialogue_authority as fixture_module
        self.fixture=fixture_module.SourceDialoguePreflightTests(methodName='runTest')
        self.fixture.setUp();self.addCleanup(self.fixture.tearDown)
        self.service=self.fixture.service;self.ns=self.fixture.namespace
        self.pid=str(self.service.create_project(self.ns,'script-partition').project_id)
        self.fixture._attach_source(self.pid,PLAY,role='script')
        self.hub=self.service.store.load_hub(self.ns,self.pid)
        source=self.hub['project_state']['latest_artifacts']['source:script']
        self.plan=partition();self.plan.update(source_artifact_id=source['artifact_id'],source_sha256=source['fingerprint'])
        task=self.service.begin_task(self.ns,project_id=self.pid,operation='episode_plan',mode='plan_only',authorization='plan_only',
            request_text='保留所有幕，完整分为两集',targets=['EP01','EP02'],authoritative_source=source['artifact_id'],
            latest_version='v1',target_boundary='全剧',must_keep=['全部原话'],must_remove=[],protected_scope=[],expected_outputs=['分集边界'],window_key='partition-test')
        self.wo=task.work_order_id
        artifact=self.service.commit_artifact(self.ns,project_id=self.pid,work_order_id=self.wo,
            artifact_type='episode_plan',output_format='json',file_name='partition.json',structured_data=self.plan,
            content_text=None,source_artifact_id=source['artifact_id'],change_summary='完整分集')
        self.art=artifact.data['artifact']
        rows=parse_source_dialogue_authority(PLAY,source_role='script')
        self.candidate={'source_lock':{'episode_partition':{'artifact_id':self.art['artifact_id'],'sha256':self.art['fingerprint']}},
            'protected_dialogue':[{'speaker':rows[-1].speaker,'text':rows[-1].text}],
            'prevention_evidence':{'episode_contract':{'episodes':[{'episode_id':'EP02'}]}},
            'shots':[{'镜头编号':'EP02-001','画面内容':'赵氏说道：“好。”'}]}

    def validate_plan(self):
        self.service.complete_validation(self.ns,project_id=self.pid,work_order_id=self.wo,
            gate_results={x:'pass' for x in self.artifact_gates()},residual_issue_count=0,introduced_issue_count=0,blockers=[])

    def artifact_gates(self):
        hub=self.service.store.load_hub(self.ns,self.pid)
        return next(w for w in hub['work_orders'] if w['work_order_id']==self.wo)['required_validation_gates']

    def manifest(self):return self.service._source_dialogue_authority_manifest(self.ns,self.pid,self.candidate)

    def test_unvalidated_partition_cannot_authorize_partial_source(self):
        report,issues=self.manifest()
        self.assertTrue(issues);self.assertIn('source_partition_plan_not_validated',str(issues))

    def test_validated_partition_accepts_scoped_original_words(self):
        self.validate_plan();report,issues=self.manifest()
        self.assertEqual(issues,[]);self.assertEqual(report['occurrence_count'],1)
        self.assertEqual(report['source_partition']['source_occurrence_count'],4)

    def test_changed_claim_or_foreign_plan_reference_fails(self):
        self.validate_plan();self.candidate['protected_dialogue'][0]['text']='不好。'
        self.assertTrue(self.manifest()[1])
        self.candidate['source_lock']['episode_partition']['sha256']='0'*64
        self.assertIn('source_partition_artifact_identity',str(self.manifest()[1]))
