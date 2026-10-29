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
    library_contract, selected_profile, style_fragment, assert_prompt_library, findings)
from production_plan import model_production_contract
from production_decisions import lower_production_decisions
from author_storyboard import _render_decisions
from test_v0527_production_decisions import performance_fixture
from shortdrama_mcp.materializer import formal_mother_template_rows, COMPACT_SHOT_FIELDS
from shortdrama_mcp.canonical import compact_storyboard_canonical
from shortdrama_mcp.service import _assert_r10_spc1_output_contract
from validate_storyboard import validate_project


def lock(library='animation', variant='3d'):
    return {'output_contract': 'R10-SPC-1', 'target_model': 'Seedance 2.5', 'aspect_ratio': '9:16',
            'prompt_library': make_selection(library, variant)}


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
        value = candidate(); del value['source_lock']['prompt_library']
        before = copy.deepcopy(value)
        assert_prompt_library(value)
        self.assertEqual(before, value)
        with self.assertRaisesRegex(PromptLibraryError, 'no automatic'):
            _assert_r10_spc1_output_contract(value)

    def test_real_compiler_and_both_projections_use_same_selected_style(self):
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
