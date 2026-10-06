"""Known missed boundary, rule mutations and legal near-misses; no quality-rate claim."""
import copy
import unittest

from shortdrama_mcp import audit_coverage
from shortdrama_mcp.materializer import COMPACT_SHOT_FIELDS
from continuity_checks import audit_adjacent_text, speech_state_findings, terminal_excerpt
import rule_registry
from serialize_audit_review import serialize_review


def actual_boundary():
    shots = [{key: "" for key in COMPACT_SHOT_FIELDS} for _ in range(2)]
    for index, shot in enumerate(shots):
        shot.update({"镜头编号": f"EP55-{39 + index:03}", "时长": 3,
                     "累计时长": (index + 1) * 3, "场景连续性ID": "hall",
                     "可见主体": "江", "镜头类型": "近景", "画面内容": "江保持原位。"})
    shots[0]["切镜/转场提示词"] = "传给其他人说完切江羞恼。"
    shots[1]["起始状态"] = "江唇还半开，听着祖母最后训词，眉头僵住。"
    shots[1]["音色"] = "无人物发声。"
    return {"shots": shots}


class RuleExecutionTests(unittest.TestCase):
    def test_rule_mutations_rejected_and_baseline_still_valid(self):
        self.assertEqual(rule_registry.validate_registry(), [])
        original = rule_registry.load_registry()
        mutations = [
            lambda r: r['validators'][0].update(handler='definitely_missing_handler'),
            lambda r: r['validators'][0].update(implementation='scripts/definitely_missing.py'),
            lambda r: next(x for x in r['conflicts'] if x['conflict_id'] == 'C-015').update(
                decision='模型自评pass可以覆盖代码校验fail。'),
            lambda r: r['runtime']['global_priority'].append(r['runtime']['global_priority'][0]),
            lambda r: r['runtime']['semantic_review_policy']['adjacent_review']['sound_consistency']['neighbor_fields'].remove('切镜/转场提示词'),
            lambda r: r['runtime']['semantic_review_policy'].update(unreviewed_status='pass'),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                candidate = copy.deepcopy(original)
                mutation(candidate)
                self.assertTrue(rule_registry.check_registry_definition(candidate))

    def test_actual_cut_field_and_speech_conflict_are_observed(self):
        project = actual_boundary()
        text, field, offset = terminal_excerpt(project['shots'][0])
        self.assertEqual((text, field), ('传给其他人说完', '切镜/转场提示词'))
        self.assertEqual(offset, 0)
        summary, checks = audit_adjacent_text(project, project['shots'])
        self.assertEqual(summary['failed_pair_count'], 1)
        self.assertEqual(checks[0]['status'], 'fail')
        self.assertIn('field:EP55-040.音色', checks[0]['field_sha256'])

    def test_legitimate_silence_memory_and_offscreen_speech_not_rejected(self):
        original = actual_boundary()['shots'][1]
        for start, voice in [
            ('江已经听完祖母训词，眉头僵住。', '无人物发声。'),
            ('江沉默站定，没有新的动作。', '无人物发声。'),
            ('江听着远处风声。', '无人物发声。'),
            ('江回忆起听着祖母训词的情形。', '无人物发声。'),
            ('江听着录音中的祖母训话。', '无人物发声。'),
            (original['起始状态'], '祖母在画外继续训话，江不发声。'),
        ]:
            with self.subTest(start=start):
                self.assertEqual(speech_state_findings({**original, '起始状态':start, '音色':voice}), [])

    def test_uncertain_or_quoted_cut_never_becomes_terminal_fact(self):
        for text in ['如果说完切近景。', '对白写着“说完切江羞恼”。', '准备起身切近景。']:
            self.assertEqual(terminal_excerpt({'切镜/转场提示词': text})[0], '')

    def test_boundary_dependencies_and_observations_include_both_sides(self):
        project = actual_boundary()
        manifest = audit_coverage.build_obligations(project, {})
        row = next(r for r in manifest['obligations'] if r['obligation_id'] == 'EP55-040/sound_consistency')
        for ref in ('field:EP55-039.切镜/转场提示词','field:EP55-039.音色','field:EP55-040.起始状态'):
            self.assertIn(ref, row['dependency_fields'])
            self.assertIn(ref, row['required_observation_refs'])
        changed = copy.deepcopy(project)
        changed['shots'][0]['切镜/转场提示词'] = '新的实际切点'
        new = audit_coverage.build_obligations(changed, {})
        replacement = next(r for r in new['obligations'] if r['obligation_id'] == row['obligation_id'])
        self.assertNotEqual(row['dependency_sha256'], replacement['dependency_sha256'])

    def test_bound_wrong_pass_cannot_override_machine_contradiction(self):
        project = actual_boundary()
        manifest = audit_coverage.build_obligations(project, {})
        row = next(r for r in manifest['obligations'] if r['obligation_id'] == 'EP55-040/sound_consistency')
        check = dict(obligation_id=row['obligation_id'], dependency_sha256=row['dependency_sha256'],
            status='pass', judgement='江听训后脸红闭口，堂内安静由她停话引起。', issue_ids=[],
            observations=[{'reference':ref, 'quote':manifest['_values'][ref]} for ref in row['required_observation_refs']])
        submission = {'layers':[{'layer':row['layer'], 'status':'pass'}], 'issues':[],
            'coverage':{'contract':audit_coverage.CONTRACT,'manifest_sha256':manifest['manifest_sha256'],'checks':[check]}}
        with self.assertRaisesRegex(ValueError, 'audit_coverage_machine_conflict'):
            audit_coverage.validate_coverage(project, submission, {}, _page_ids=[row['obligation_id']])
        check['status'] = 'unknown'
        result = audit_coverage.validate_coverage(project, submission, {}, _page_ids=[row['obligation_id']], allow_unresolved=True)
        self.assertFalse(result['complete'])
        self.assertEqual(result['passed_shot_count'], 0)

    def test_missing_neighbor_evidence_rejected_on_corrected_text(self):
        project = actual_boundary()
        project['shots'][1]['起始状态'] = '江已经听完祖母训词，眉头僵住。'
        manifest = audit_coverage.build_obligations(project, {})
        row = next(r for r in manifest['obligations'] if r['obligation_id'] == 'EP55-040/sound_consistency')
        check = dict(obligation_id=row['obligation_id'], dependency_sha256=row['dependency_sha256'],
            status='pass', judgement='声音已结束，当前起态明确为听完，未继续发言。', issue_ids=[],
            observations=[{'reference':ref, 'quote':manifest['_values'][ref]} for ref in row['required_observation_refs']
                          if not ref.startswith('field:EP55-039.')])
        submission = {'layers':[{'layer':row['layer'], 'status':'pass'}], 'issues':[],
            'coverage':{'contract':audit_coverage.CONTRACT,'manifest_sha256':manifest['manifest_sha256'],'checks':[check]}}
        with self.assertRaisesRegex(ValueError, 'audit_coverage_observation_missing'):
            audit_coverage.validate_coverage(project, submission, {}, _page_ids=[row['obligation_id']])
        check['observations'] = [{'reference':ref, 'quote':manifest['_values'][ref]} for ref in row['required_observation_refs']]
        self.assertTrue(audit_coverage.validate_coverage(project, submission, {}, _page_ids=[row['obligation_id']])['complete'])

    def test_serializer_does_not_expand_default_pass_or_rebind_old_decisions(self):
        manifest = {'manifest_sha256':'f'*64, 'obligations':[{'obligation_id':'one','dependency_sha256':'a'*64}]}
        with self.assertRaisesRegex(ValueError, 'explicit_review_required'):
            serialize_review(manifest, {'rule_defaults':{'state_continuity':'pass'}, 'checks':{}})
        with self.assertRaisesRegex(ValueError, 'explicit_review_missing'):
            serialize_review(manifest, {'checks':{}})
        check = dict(obligation_id='one', dependency_sha256='a'*64, status='unknown',
                     judgement='本项缺少独立内容核验，不能判断通过。', observations=[])
        result = serialize_review(manifest, {'checks':{'one':check}})
        self.assertEqual(result['checks'][0]['status'], 'unknown')
        check['dependency_sha256'] = 'b'*64
        with self.assertRaisesRegex(ValueError, 'explicit_review_invalid_or_stale'):
            serialize_review(manifest, {'checks':{'one':check}})


if __name__ == '__main__':
    unittest.main()
