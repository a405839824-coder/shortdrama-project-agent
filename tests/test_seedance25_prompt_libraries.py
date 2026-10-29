"""Separation, adversarial inputs and real canonical/projection integration."""
import copy
import asyncio
import json
from pathlib import Path
import sys
import unittest
from mcp.server.mcpserver.exceptions import ToolError

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'server'), str(ROOT / 'tests'),
               str(ROOT / 'skills/shortdrama-storyboard/scripts')]

from prompt_libraries import (PromptLibraryError, make_selection, resolve_selection,
    library_contract, selected_profile, style_fragment, assert_prompt_library, findings, make_visual_style)
from production_plan import model_production_contract
from production_decisions import lower_production_decisions
from author_storyboard import _render_decisions
from test_v0527_production_decisions import performance_fixture
from shortdrama_mcp.materializer import formal_mother_template_rows, COMPACT_SHOT_FIELDS
from shortdrama_mcp.canonical import compact_storyboard_canonical
from shortdrama_mcp.service import _assert_r10_spc1_output_contract
from validate_storyboard import validate_project


def lock(library='animation', variant='3d'):
    selection = make_selection(library, variant)
    visual = make_visual_style(selection, style_id='synthetic-project-look', revision='1',
        render_mode={'3d':'stylized_realism','2d':'line_art','live_action_look':'cinematic'}[variant],
        character_design='成年角色七头身，椭圆脸型，五官比例保持一致',
        materials='皮肤哑光，头发分束，棉布低光泽，金属保留局部反射',
        palette='青灰与暖棕为主色，朱红作为小面积强调色',
        shading='体块明暗清楚，阴影边缘柔和，轮廓清晰')
    return {'output_contract': 'R10-SPC-1', 'target_model': 'Seedance 2.5', 'aspect_ratio': '9:16',
            'prompt_library': selection, 'visual_style': visual}


def candidate(library='animation', variant='3d'):
    selection = lock(library, variant)
    return {'source_lock': selection, 'shots': [{'生成限制': '9:16竖屏；' + style_fragment(selection),
              '画面内容': '甲说道：“这是动画，不是真人电影风格。”'}]}


class PromptLibraryTests(unittest.TestCase):
    def test_public_mcp_reads_each_library_without_persisting_a_selection(self):
        from test_v055_quality_migration import V055QualityAndMigrationTests
        fixture = V055QualityAndMigrationTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        before = fixture.service.store.list_projects(fixture.namespace)
        for library, variant in [('animation', '3d'), ('animation', '2d'), ('photorealistic', 'live_action_look')]:
            response = asyncio.run(fixture.server.call_tool('get_agent_status', {
                'prompt_library_id': library, 'prompt_variant': variant, 'full_self_test': False}))
            self.assertFalse(response.is_error, response.content)
            actual = response.structured_content['data']['selected_prompt_library']
            self.assertEqual(actual, resolve_selection(make_selection(library, variant)))
            self.assertNotIn('variants', actual)
        ordinary = asyncio.run(fixture.server.call_tool('get_agent_status', {'full_self_test': False}))
        self.assertFalse(ordinary.is_error, ordinary.content)
        self.assertNotIn('selected_prompt_library', ordinary.structured_content['data'])
        self.assertEqual(fixture.service.store.list_projects(fixture.namespace), before)

    def test_public_mcp_rejects_partial_and_cross_library_requests(self):
        from test_v055_quality_migration import V055QualityAndMigrationTests
        fixture = V055QualityAndMigrationTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        for arguments in [{'prompt_library_id': 'animation'}, {'prompt_variant': '3d'},
                          {'prompt_library_id': 'animation', 'prompt_variant': 'live_action_look'},
                          {'prompt_library_id': 'photorealistic', 'prompt_variant': '3d'},
                          {'prompt_library_id': 'mixed', 'prompt_variant': '3d'}]:
            with self.subTest(arguments=arguments):
                with self.assertRaises(ToolError):
                    asyncio.run(fixture.server.call_tool('get_agent_status', arguments))

    def test_public_mcp_prepares_complete_visual_card_without_changing_project(self):
        from test_v055_quality_migration import V055QualityAndMigrationTests
        fixture=V055QualityAndMigrationTests();fixture.setUp();self.addCleanup(fixture.tearDown)
        project=fixture._project('视觉卡只读准备')
        before=fixture.service.store.list_projects(fixture.namespace)
        card=lock()['visual_style']
        spec={k:card[k] for k in ('style_id','revision','render_mode','character_design','materials','palette','shading')}
        arguments={'project_id':project,'prompt_library_id':'animation','prompt_variant':'3d','prompt_visual_style':spec}
        result=asyncio.run(fixture.server.call_tool('get_agent_status',arguments))
        self.assertFalse(result.is_error,result.content)
        self.assertEqual(result.structured_content['data']['selected_prompt_library']['visual_style'],card)
        self.assertEqual(fixture.service.store.list_projects(fixture.namespace),before)
        self.assertEqual(fixture.service.store.list_artifacts(fixture.namespace,project),[])
        for changes in [{'prompt_visual_style':{}},{'prompt_visual_style':{**spec,'render_mode':'cinematic'}},
                {'prompt_visual_style':{**spec,'materials':'真人实拍质感'}},
                {'prompt_visual_style':spec,'prompt_library_id':None},
                {'prompt_visual_style':{**spec,'made_up':'unsupported'}}]:
            with self.subTest(changes=changes),self.assertRaises(ToolError):
                asyncio.run(fixture.server.call_tool('get_agent_status',{**arguments,**changes}))

    def test_exactly_two_libraries_and_three_independent_variants(self):
        catalog = model_production_contract()['prompt_libraries']['catalog']
        self.assertEqual([x['library_id'] for x in catalog], ['animation', 'photorealistic'])
        self.assertEqual([v['id'] for v in catalog[0]['variants']], ['3d', '2d'])
        self.assertEqual(len(catalog[1]['variants']), 1)
        self.assertNotIn('style_prompt', json.dumps(catalog))

    def test_resolver_returns_one_style_without_other_library_examples(self):
        resolved = resolve_selection(make_selection('animation', '3d'))
        self.assertIn('3D动漫', resolved['profile']['style_prompt'])
        self.assertNotIn('variants', resolved)
        resolved['profile']['style_prompt'] = 'mutated'
        self.assertIn('3D动漫', resolve_selection(make_selection('animation', '3d'))['profile']['style_prompt'])

    def test_missing_unknown_mixed_and_malformed_selections_rejected(self):
        for library, variant in [('animation', 'live_action_look'), ('photorealistic', '3d'), ('mixed', '3d'), ([], '3d'), ('animation', [])]:
            with self.subTest(library=library, variant=variant), self.assertRaises(PromptLibraryError):
                make_selection(library, variant)
        for bad in [None, {}, [], 'animation', {'library_id': 'animation'}]:
            with self.subTest(bad=bad), self.assertRaises(PromptLibraryError):
                resolve_selection(bad)

    def test_stale_fingerprint_and_model_mismatch_fail(self):
        value = lock(); value['prompt_library']['content_sha256'] = '0' * 64
        with self.assertRaises(PromptLibraryError): selected_profile(value)
        value = lock(); value['target_model'] = 'Seedance 2.0'
        with self.assertRaises(PromptLibraryError): selected_profile(value)

    def test_visual_reference_cannot_cross_library_but_motion_can(self):
        value = lock()
        value['prompt_references'] = [{'id': '视频1', 'role': 'appearance', 'library_id': 'photorealistic', 'variant': 'live_action_look'}]
        with self.assertRaises(PromptLibraryError): selected_profile(value)
        value['prompt_references'][0]['role'] = 'motion'
        self.assertIsNotNone(selected_profile(value))
        value['prompt_references'][0]['role'] = 'style'
        value['prompt_references'][0].update(library_id='animation', variant='2d')
        with self.assertRaises(PromptLibraryError): selected_profile(value)

    def test_reference_ambiguity_and_invalid_record_rejected(self):
        value = lock()
        for reference in [{'id': '图1', 'role': 'style', 'library_id': 'unknown', 'variant': ''},
                          {'id': '图1', 'role': [], 'library_id': 'animation', 'variant': '3d'}]:
            value['prompt_references'] = [reference]
            with self.assertRaises(PromptLibraryError): selected_profile(value)

    def test_conflicting_style_blocked_in_both_directions(self):
        for library, variant, wrong in [('animation', '3d', '真人电影风格'),
                ('animation', '2d', '3D动漫'), ('photorealistic', 'live_action_look', '2D动漫')]:
            value = candidate(library, variant); value['shots'][0]['生成限制'] += wrong + '。'
            with self.subTest(library=library, variant=variant), self.assertRaises(PromptLibraryError):
                assert_prompt_library(value, required=True)
            self.assertTrue(findings(value)[0]['blocking'])

    def test_negation_dialogue_and_realistic_material_do_not_change_library(self):
        value = candidate(); before = value['shots'][0]['画面内容']
        value['shots'][0]['生成限制'] += '禁止真人电影风格；真实光影、丝绸材质。'
        assert_prompt_library(value, required=True)
        self.assertEqual(value['shots'][0]['画面内容'], before)

    def test_negation_cannot_hide_contradictory_positive_clause(self):
        for text in ['禁止2D动漫，但采用真人电影风格。', '禁止2D动漫，采用真人电影风格。']:
            value = candidate(); value['shots'][0]['生成限制'] += text
            with self.subTest(text=text), self.assertRaises(PromptLibraryError):
                assert_prompt_library(value)

    def test_duplicate_or_missing_style_fragment_fails(self):
        for extra in ['', style_fragment(lock()) * 2]:
            value = candidate(); value['shots'][0]['生成限制'] = '9:16竖屏；' + extra
            with self.assertRaises(PromptLibraryError): assert_prompt_library(value)

    def test_legacy_read_only_is_not_silently_classified_and_write_requires_choice(self):
        value = candidate(); del value['source_lock']['prompt_library']; del value['source_lock']['visual_style']
        before = copy.deepcopy(value)
        assert_prompt_library(value)
        self.assertEqual(before, value)
        with self.assertRaisesRegex(PromptLibraryError, 'no automatic'):
            _assert_r10_spc1_output_contract(value)

    def test_real_compiler_and_both_projections_use_same_selected_style(self):
        baseline = None
        for library, variant in [('animation', '3d'), ('animation', '2d'), ('photorealistic', 'live_action_look')]:
            with self.subTest(library=library, variant=variant):
                data = performance_fixture()
                data['project']['source_lock'].update(lock(library, variant))
                before = copy.deepcopy(data)
                canonical = _render_decisions(lower_production_decisions(data)['typed_decisions'])
                self.assertEqual(data, before)
                self.assertEqual(len(COMPACT_SHOT_FIELDS), 27)
                _assert_r10_spc1_output_contract(canonical)
                _assert_r10_spc1_output_contract(compact_storyboard_canonical(canonical))
                expected = style_fragment(canonical['source_lock']).rstrip('。；;')
                row = formal_mother_template_rows(canonical)[0]
                self.assertEqual(len(row), 11)
                self.assertEqual(row['正式分镜母版'].count(expected), 1)
                self.assertEqual(row['GPT关键帧生成指令'].count(expected), 1)
                self.assertFalse(findings(canonical))
                protected = [{key:value for key,value in shot.items() if key != '生成限制'} for shot in canonical['shots']]
                if baseline is None:
                    baseline = protected
                self.assertEqual(protected, baseline)  # Same dialogue, voice, timing, geometry and actions.

    def test_actual_source_dialogue_voice_and_timing_do_not_change_between_libraries(self):
        from test_v0527_production_decisions import production_fixture, action, voice_spec, compile_source, source_fixture
        from timing_model import validate_timing
        source=compile_source(*source_fixture())
        baseline=None
        for library,variant in [('animation','3d'),('animation','2d'),('photorealistic','live_action_look')]:
            data=production_fixture()
            data['shots'][0]['actions']=[action('speak',source_use_id='U1',voice=voice_spec())]
            data['project']['source_lock'].update(lock(library,variant))
            canonical=_render_decisions(lower_production_decisions(data,source_compilation=source)['typed_decisions'])
            _assert_r10_spc1_output_contract(canonical)
            row=canonical['shots'][0]
            self.assertEqual(row['画面内容'].count('明日再议。'),1)
            self.assertIn('基础声线',row['音色'])
            self.assertEqual(validate_timing(canonical,canonical['shots'])['faults'],[])
            protected={'shot':{k:v for k,v in row.items() if k!='生成限制'},
                       'audio_workflow':canonical['prevention_evidence']['audio_workflow']}
            if baseline is None: baseline=protected
            with self.subTest(library=library,variant=variant): self.assertEqual(protected,baseline)

    def test_review_regressions_are_blocked_in_visual_instruction_fields(self):
        cases = [('生成限制','采用真人实拍质感。'), ('生成限制','日系二维动画。'),
                 ('生成限制','禁止真人电影风格并采用仿真人风格。'),
                 ('画面内容','全片采用真人电影风格，甲站在书房里。'),
                 ('参考图锚定','图片1作为人物形象，采用真人电影风格。')]
        for field, text in cases:
            with self.subTest(field=field,text=text):
                value = candidate()
                value['shots'][0][field] = (value['shots'][0].get(field,'') if field=='生成限制' else '') + text
                with self.assertRaisesRegex(PromptLibraryError, 'conflicting positive style'):
                    _assert_r10_spc1_output_contract(value)

    def test_alias_spacing_case_and_negative_scope_cannot_bypass(self):
        for text in ['真人实拍质感','真 人 电 影 风 格','Ｌｉｖｅ－ａｃｔｉｏｎ look',
                     'photorealistic','日系二维动画','2 D anime','禁止真人电影风格\n采用仿真人风格',
                     '禁止真人电影风格；仍要仿真人风格','禁止真人电影风格，仿真人风格除外',
                     '不能不采用真人电影风格','不是不使用仿真人风格',
                     '禁止字幕真人电影风格','禁止真人电影风格并保留仿真人质感',
                     '禁止2D动漫 and use photorealistic','禁止2D动漫；仿真人风格',
                     '不要画面模糊采用仿真人风格','禁止非仿真人风格','禁止任何非仿真人风格',
                     '非不采用真人电影风格']:
            with self.subTest(text=text):
                value=candidate(); value['shots'][0]['生成限制']+='；'+text
                with self.assertRaises(PromptLibraryError): assert_prompt_library(value,required=True)
        value=candidate('photorealistic','live_action_look');value['shots'][0]['生成限制']+='；3 D 动漫。'
        with self.assertRaises(PromptLibraryError): assert_prompt_library(value,required=True)

    def test_all_visual_instruction_fields_are_checked_and_error_location_is_specific(self):
        from prompt_libraries import VISUAL_FIELDS
        for field in VISUAL_FIELDS:
            value=candidate();value['shots'][0][field]=value['shots'][0].get(field,'')+'；采用真人实拍质感。'
            with self.subTest(field=field), self.assertRaisesRegex(PromptLibraryError, field):
                assert_prompt_library(value,required=True)

    def test_genuine_negations_dialogue_and_written_words_remain_exact(self):
        value=candidate()
        value['shots'][0]['生成限制']+='；禁止采用真人电影风格；不使用仿真人风格；禁止真人实拍质感和二维动画；禁止直接采用仿真人风格；no photorealistic；真实光影与丝绸材质。'
        value['shots'][0]['画面内容']='甲说道：“我要采用真人实拍质感。”甲指向纸面。纸面当前全文只有：“日系二维动画”。'
        original=copy.deepcopy(value)
        assert_prompt_library(value,required=True)
        self.assertEqual(value,original)
        value['shots'][0]['画面内容']+='采用“真人电影风格”。'
        with self.assertRaises(PromptLibraryError): assert_prompt_library(value,required=True)

    def test_quoted_reference_or_style_instruction_is_not_a_literal_exemption(self):
        for field,text in [('参考图锚定','【参考图锚点】采用“真人电影风格”'),
                ('生成限制','视觉风格：“仿真人风格”'),
                ('画面内容','纸面当前全文只有：“二维动画”。全片采用仿真人风格。')]:
            value=candidate();value['shots'][0][field]=value['shots'][0].get(field,'')+'；'+text
            with self.subTest(field=field),self.assertRaises(PromptLibraryError): assert_prompt_library(value,required=True)

    def test_selected_style_block_cannot_be_negated_or_only_quoted(self):
        for prefix,suffix in [('禁止',''),('不要',''),('范例：“','”'),('纸面当前全文只有：“','”')]:
            value=candidate()
            value['shots'][0]['生成限制']='9:16竖屏；'+prefix+style_fragment(value['source_lock'])+suffix
            with self.subTest(prefix=prefix),self.assertRaisesRegex(PromptLibraryError,'positive complete'):
                assert_prompt_library(value,required=True)

    def test_render_technology_does_not_assign_animation_style_to_photorealism(self):
        value=candidate('photorealistic','live_action_look')
        value['shots'][0]['生成限制']+='；采用三维模型渲染，PBR材质，真实光影。'
        assert_prompt_library(value,required=True)
        value['shots'][0]['生成限制']+='采用三维模型赛璐璐渲染。'
        with self.assertRaises(PromptLibraryError): assert_prompt_library(value,required=True)

    def test_every_render_mode_projects_and_explicit_mode_switch_is_blocked(self):
        for library,variant in [('animation','3d'),('animation','2d'),('photorealistic','live_action_look')]:
            selected=make_selection(library,variant)
            for mode in resolve_selection(selected)['profile']['render_modes']:
                value=lock(library,variant)
                spec=value['visual_style']
                value['visual_style']=make_visual_style(selected,**{k:spec[k] for k in
                    ('style_id','revision','character_design','materials','palette','shading')},render_mode=mode)
                canonical={'source_lock':value,'shots':[{'生成限制':'9:16竖屏；'+style_fragment(value)}]}
                with self.subTest(library=library,mode=mode): assert_prompt_library(canonical,required=True)
        value=candidate();value['shots'][0]['光影']='全片采用三渲二。'
        with self.assertRaisesRegex(PromptLibraryError,'光影'): assert_prompt_library(value,required=True)

    def test_visual_style_requires_explicit_mode_complete_dimensions_and_valid_hash(self):
        value=lock();del value['visual_style']
        with self.assertRaisesRegex(PromptLibraryError,'explicit project visual style'): style_fragment(value)
        for name,replacement in [('materials',''),('materials','待定'),('palette','另一套配色'),('render_mode','cinematic'),('library_id','photorealistic')]:
            value=lock();value['visual_style'][name]=replacement
            with self.subTest(name=name), self.assertRaises(PromptLibraryError): selected_profile(value,required=True)
        value=lock();spec=value['visual_style'];spec['materials']='采用真人实拍质感'
        with self.assertRaisesRegex(PromptLibraryError,'conflicting positive style'): selected_profile(value,required=True)

    def test_visual_reference_binds_project_style_and_asset_identity_without_pixel_claim(self):
        value=lock();value['prompt_references']=[{'id':'图1','role':'appearance','library_id':'animation','variant':'3d',
            'asset_sha256':'a'*64,'visual_style_sha256':value['visual_style']['content_sha256']}]
        self.assertIsNotNone(selected_profile(value,required=True))
        for key in ['asset_sha256','visual_style_sha256']:
            broken=copy.deepcopy(value);broken['prompt_references'][0][key]='invalid'
            with self.subTest(key=key),self.assertRaises(PromptLibraryError): selected_profile(broken,required=True)
        self.assertEqual(library_contract()['visual_style_contract']['actual_media_validation'],'not_verified')

    def test_style_change_requires_refreezing_existing_source_plan(self):
        from test_v0527_mcp_production_authoring import McpProductionAuthoringTests
        fixture=McpProductionAuthoringTests();fixture.setUp();self.addCleanup(fixture.tearDown)
        from production_plan import ProductionPlanGuard, ProductionPlanError
        graph=json.loads(fixture.graph_bytes)
        changed=copy.deepcopy(fixture.decisions)
        changed['project']['source_lock'].update(lock('photorealistic','live_action_look'))
        with self.assertRaisesRegex(ProductionPlanError,'decision differs from independently frozen plan'):
            ProductionPlanGuard(changed,{'production_plan':graph['production_plan']})
        changed=copy.deepcopy(fixture.decisions)
        selected=changed['project']['source_lock']['prompt_library']
        spec=changed['project']['source_lock']['visual_style']
        changed['project']['source_lock']['visual_style']=make_visual_style(selected,**{
            **{k:spec[k] for k in ('style_id','revision','render_mode','character_design','materials','palette','shading')},
            'palette':'暗红主色，金色点缀'})
        with self.assertRaisesRegex(ProductionPlanError,'decision differs from independently frozen plan'):
            ProductionPlanGuard(changed,{'production_plan':graph['production_plan']})
        changed=copy.deepcopy(fixture.decisions)
        changed['project']['source_lock']['prompt_references']=[{'id':'video1','role':'motion','library_id':'neutral','variant':''}]
        with self.assertRaises(ProductionPlanError):
            ProductionPlanGuard(changed,{'production_plan':graph['production_plan']})

    def test_shared_validator_rejects_tampered_library_field(self):
        value = candidate(); value['source_lock']['prompt_library']['variant'] = 'live_action_look'
        report = validate_project(value)
        self.assertTrue(any(i['error_code'] == 'E-REFERENCE-001' and 'prompt_library:' in i['evidence'] for i in report['issues']))

    def test_mcp_preflight_preserves_selection_without_claiming_formal_approval(self):
        from test_v0527_mcp_production_authoring import McpProductionAuthoringTests
        fixture = McpProductionAuthoringTests()
        fixture.setUp()
        try:
            result = fixture.mcp('preflight_storyboard_draft', fixture.arguments())['data']
            canonical = result['compiled_storyboard']
            self.assertEqual(canonical['source_lock']['prompt_library'], make_selection('animation', '3d'))
            assert_prompt_library(canonical, required=True)
            self.assertFalse(any('prompt_library:' in str(issue) for issue in result['report']['issues']))
            self.assertFalse(any(issue['error_code'] == 'E-DRAFT-CONTRACT-001' for issue in result['report']['issues']))
            self.assertFalse(result['commit_ready'])  # Synthetic input still needs the existing full review.
            fixture.assert_no_publication()
        finally:
            fixture.tearDown()

    def test_real_mcp_preflight_blocks_style_conflict_after_source_plan_freeze(self):
        import hashlib
        from production_plan_test_support import with_plan
        from test_v0527_mcp_production_authoring import McpProductionAuthoringTests
        fixture=McpProductionAuthoringTests();fixture.setUp();self.addCleanup(fixture.tearDown)
        fixture.decisions['shots'][0]['editorial']['参考图锚定']='无外部图像锚点；全片采用真人实拍质感。'
        texts={row['source_id']:row['source_text'] for row in fixture.decisions['project']['prevention_evidence']['source_ledger']}
        frozen=with_plan(fixture.decisions,fixture.graph_bytes,texts)
        plan_order=fixture.begin('storyboard_plan',planning=True)
        saved=fixture.mcp('commit_project_artifact',{'project_id':fixture.project,'work_order_id':plan_order,
            'artifact_type':'storyboard_plan','output_format':'json','file_name':'conflicting-style-plan.json',
            'structured_data':json.loads(frozen)})
        _,graph_path=fixture.service.store.get_artifact(fixture.namespace,fixture.project,saved['artifact_id'])
        request=copy.deepcopy(fixture.request)
        request.update(source_graph_artifact_id=saved['artifact_id'],
                       source_graph_sha256=hashlib.sha256(graph_path.read_bytes()).hexdigest())
        before=copy.deepcopy(request)
        result=fixture.mcp('preflight_storyboard_draft',fixture.arguments(request))['data']
        self.assertFalse(result['commit_ready'])
        self.assertIn('prompt_library:',str(result['report']['issues']))
        self.assertIn('参考图锚定',str(result['report']['issues']))
        self.assertEqual(request,before)
        artifacts=fixture.service.store.list_artifacts(fixture.namespace,fixture.project)
        self.assertEqual({row['artifact_id'] for row in artifacts},{fixture.graph_id,saved['artifact_id']})
        self.assertEqual(fixture.service.store.list_project_records(fixture.namespace,fixture.project,kind='handoffs'),[])

    def test_only_recorded_c010_migration_can_change_the_old_priority(self):
        import rule_registry
        registry = rule_registry.load_registry()
        self.assertEqual(rule_registry.check_registry_definition(registry), [])
        for identity, field in [('C-010', 'exception_allowed'), ('C-009', 'higher_priority_rule')]:
            changed = copy.deepcopy(registry)
            row = next(x for x in changed['conflicts'] if x['conflict_id'] == identity)
            row[field] = 'unrelated change'
            self.assertTrue(rule_registry.check_registry_definition(changed))

    def test_library_data_participates_in_existing_compiler_fingerprint(self):
        from production_derivation import compiler_fingerprints
        files = compiler_fingerprints()
        for name in ['prompt_library_animation.json', 'prompt_library_photorealistic.json', 'seedance25_guidance.json']:
            self.assertIn('references/' + name, files)


if __name__ == '__main__':
    unittest.main()
