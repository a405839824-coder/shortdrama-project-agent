"""Actual-cell mutations and renamed scenes exercise shared semantic entrypoints."""
from pathlib import Path
import copy
import io
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'server'), str(ROOT/'skills/shortdrama-storyboard/scripts'), str(ROOT/'tests')]
from openpyxl import Workbook
from shortdrama_mcp.file_io import extract_xlsx
from shortdrama_mcp.materializer import compile_gpt_keyframe_instruction, compile_formal_merged_field
from production_decisions import lower_production_decisions
from author_storyboard import _render_decisions
from authoring_contract_blocks import voice_block, ContractBlockError
from projection_semantics import music_directives, explicit_state_conflicts, environment_phase_findings
from validate_storyboard import (project_export_row, project_gpt_keyframe, project_formal_master,
    validate_saved_projection, validate_project, _shot_semantic_checks, _validate_adapted_dialogue_audit)
from performance_checks import eye_contract_faults
from test_v0527_production_decisions import production_fixture, voice_spec
from test_generated_action_binding import dynamic_subject_fixture
from test_outdoor_daylight import fixture as outdoor_fixture, render as render_outdoor


def renamed(value, names):
    if isinstance(value, dict):
        return {renamed(key, names): renamed(item, names) for key, item in value.items()}
    if isinstance(value, list):
        return [renamed(item, names) for item in value]
    if isinstance(value, str):
        for old, new in names.items():
            value = value.replace(old, new)
    return value


def generated(*, closed=False, name='林工程师', scene='研究站控制室'):
    data = renamed(production_fixture(), {'甲': name, '书房': scene, '信纸': '检修纸签'})
    if closed:
        data['initial_world']['actors'][name]['eye_state'] = 'closed'
        data['shots'][0]['editorial']['原文支撑句'] = name + '昏迷闭眼。'
    return data, _render_decisions(lower_production_decisions(data)['typed_decisions'])


class ProjectionSemanticsTests(unittest.TestCase):
    def checks(self, shot, saved, **kwargs):
        return {item['check_id']: item for item in validate_saved_projection(shot, saved, **kwargs)}

    def test_renamed_sleeping_start_and_saved_frame_mutation(self):
        for name, scene in [('林工程师', '研究站控制室'), ('安禾', '山寺客房')]:
            with self.subTest(name=name):
                data, canonical = generated(closed=True, name=name, scene=scene)
                shot = canonical['shots'][0]
                saved = project_export_row(shot)
                original = copy.deepcopy(saved)
                self.assertEqual(project_gpt_keyframe(shot), compile_gpt_keyframe_instruction(shot, assume_compact=True))
                self.assertEqual(self.checks(shot, saved)['saved_initial_state']['status'], 'pass')
                saved['GPT关键帧生成指令'] += '\n入画眼睛自然睁开。'
                report = self.checks(shot, saved)
                self.assertEqual(report['saved_initial_state']['status'], 'fail')
                self.assertEqual(report['saved_projection_binding']['status'], 'unknown')
                self.assertNotEqual(report['saved_initial_state']['field_sha256']['GPT关键帧生成指令'],
                                    self.checks(shot, original)['saved_initial_state']['field_sha256']['GPT关键帧生成指令'])
                self.assertEqual(data['initial_world']['actors'][name]['eye_state'], 'closed')

    def test_mixed_eye_states_are_per_person(self):
        initial = '安禾双眼闭合，杜衡双眼自然睁开。'
        self.assertEqual(explicit_state_conflicts(initial, initial, ['安禾', '杜衡']), [])
        self.assertTrue(explicit_state_conflicts(initial, '安禾双眼自然睁开，杜衡双眼自然睁开。', ['安禾', '杜衡']))

    def test_actual_xlsx_F_mutation_is_checked_without_sidecar_or_regeneration(self):
        _, canonical = generated()
        row = project_export_row(canonical['shots'][0])
        row['GPT关键帧生成指令'] = row['GPT关键帧生成指令'].replace('机位：', '机位：从抱起到落地全过程；')
        workbook = Workbook(); sheet = workbook.active; sheet.title = '正式分镜母版'
        sheet.append(list(row)); sheet.append(list(row.values()))
        read = workbook.create_sheet('EP90_逐镜阅读'); read.append(['not authoritative'])
        out = io.BytesIO(); workbook.save(out)
        parsed = extract_xlsx(out.getvalue(), audit_only=True)['canonical_storyboard']
        self.assertEqual(parsed['audit_workbook_evidence']['rows'][0]['cells']['GPT关键帧生成指令']['value'], row['GPT关键帧生成指令'])
        report = validate_project(parsed)
        checks = report['quality_metrics']['semantic_checks']
        self.assertTrue(any(item['check_id'] == 'saved_keyframe_static' and item['status'] == 'fail' for item in checks))
        self.assertTrue(any(item['check_id'] == 'saved_projection_binding' and item['status'] == 'unknown' for item in checks))
        self.assertTrue(any(item['location'] == 'audit_workbook_evidence' for item in validate_project(parsed, mode='archive')['issues']))

    def test_saved_K_change_and_missing_legacy_K_are_distinct(self):
        _, canonical = generated(); shot = canonical['shots'][0]; saved = project_export_row(shot)
        changed = dict(saved, 光影=saved['光影'].replace('画面左侧', '画面右侧'))
        self.assertNotEqual(changed['光影'], saved['光影'])
        self.assertEqual(self.checks(shot, changed)['saved_light_consistency']['status'], 'fail')
        del changed['光影']
        self.assertEqual(self.checks(shot, changed)['saved_light_consistency']['status'], 'unknown')

    def test_dynamic_camera_purpose_cannot_be_saved_as_static_geometry(self):
        _, canonical = generated(); shot = canonical['shots'][0]
        for purpose in ['屈膝后的坐稳接触', '从取物到落地全过程', '同前镜']:
            saved = project_export_row(shot); saved['GPT关键帧生成指令'] = saved['GPT关键帧生成指令'].replace('机位：', '机位：' + purpose + '；')
            self.assertEqual(self.checks(shot, saved)['saved_keyframe_static']['status'], 'fail')

    def test_environment_cannot_replay_the_previous_actor_phase(self):
        shot = {'起始状态': '周船长保持坐姿。', '画面内容': '周船长坐稳，视线落在仪表。',
                '环境动态': '衣料随周船长起身和急步轻动。'}
        self.assertEqual(environment_phase_findings(shot, ['周船长'])[0]['status'], 'fail')
        shot['画面内容'] = '周船长起身后急步走向舱门。'
        self.assertEqual(environment_phase_findings(shot, ['周船长']), [])
        shot.update(起始状态='周船长站定。', 画面内容='周船长停步，视线落在舱门。', 环境动态='衣料随周船长急步摆动。')
        self.assertEqual(environment_phase_findings(shot, ['周船长'])[0]['status'], 'fail')

    def test_existing_shared_world_reverses_light_and_preserves_out_of_frame_actor(self):
        world, shot = outdoor_fixture()
        world = renamed(world, {'甲': '姜向导'}); shot = renamed(shot, {'甲': '姜向导'})
        _, first = render_outdoor(world, shot)
        shot['camera']['position_xyz'] = [0, -4, 1.5]
        _, second = render_outdoor(world, shot)
        self.assertIn('画面左侧', first['光源方向']); self.assertIn('画面右侧', second['光源方向'])
        self.assertIn('姜向导后脑', second['光源方向']); self.assertNotIn('姜向导面部', second['光源方向'])
        data = renamed(dynamic_subject_fixture(), {'甲': '方研究员', '乙': '季工程师'})
        original = copy.deepcopy(data['initial_world'])
        canonical = _render_decisions(lower_production_decisions(data)['typed_decisions'])
        f = project_gpt_keyframe(canonical['shots'][0])
        self.assertNotIn('季工程师（', f)
        self.assertIn('季工程师', data['initial_world']['actors'])
        self.assertEqual(data['initial_world'], original)

    def test_capacity_blocks_projection_without_deleting_world_people(self):
        _, canonical = generated(); shot = canonical['shots'][0]
        shot['可见主体'] = '；'.join(name + '（面部）' for name in ['明宇', '小岚', '老周', '阿禾'])
        before = copy.deepcopy(shot)
        with self.assertRaisesRegex(ValueError, 'capacity'):
            project_gpt_keyframe(shot)
        self.assertEqual(shot, before)

    def test_geometry_reframes_four_people_without_deleting_world_actor(self):
        data = production_fixture()
        for name in ['乙', '丙', '丁']:
            data['initial_world']['actors'][name] = copy.deepcopy(data['initial_world']['actors']['甲'])
        original = copy.deepcopy(data)
        with self.assertRaisesRegex(ValueError, 'camera.visible_actors'):
            lower_production_decisions(data)
        self.assertEqual(data, original)
        data['initial_world']['actors']['丁']['position_xyz'] = [20, 0, 0]
        data['initial_world']['actors']['丁']['reach_origin_xyz'] = [20, 0, 1.2]
        lowered = lower_production_decisions(data)
        self.assertEqual(len(lowered['derivation']['final_world']['actors']), 4)
        self.assertNotIn('丁（', _render_decisions(lowered['typed_decisions'])['shots'][0]['可见主体'])

    def test_zero_blinks_remain_legal(self):
        _, canonical = generated(); shot = canonical['shots'][0]
        self.assertFalse(any('缺' in item and '眨眼' in item for item in eye_contract_faults(
            shot, duration=4.0, strict=True, visible_names=['林工程师'], legacy_components=False)))

    def test_delivery_binds_actual_words_and_preserves_baseline(self):
        for line, emphasis in [('请先核对氧压。', '氧压'), ('这份卷宗明日再看。', '明日')]:
            spec = voice_spec(); spec['emphasis'] = '“' + emphasis + '”。'
            spec['speech_act'] = '请求核对压力。' if '氧压' in line else '陈述延后阅读的安排。'
            spec['current_expression'] = '平静，意在确认当前请求。'
            block = voice_block(mode='spoken', dialogue_rows=[('沈值班员', line)], **spec)
            self.assertIn('基础声线：' + spec['baseline'], block)
            self.assertIn('末字“' + line[-2] + '”', block)
            shot = {'镜头编号': 'EP01-001', '画面内容': '沈值班员说道：“' + line + '”', '音色': block}
            self.assertEqual(_shot_semantic_checks(shot)[0]['status'], 'pass')
            shot['音色'] = block.replace('重音：“' + emphasis + '”。', '重音：“未知词”。')
            self.assertEqual(_shot_semantic_checks(shot)[0]['status'], 'fail')
        spec = voice_spec(); spec['tail_execution'] = '末字“无关”落稳。'
        with self.assertRaisesRegex(ContractBlockError, '实际句末'):
            voice_block(mode='spoken', dialogue_rows=[('沈值班员', '明日再议。')], **spec)

    def test_music_negation_uses_scope_not_visual_object_allowlist(self):
        for clause in ['不加字幕、水印、黑边或额外配乐', '禁止浮雕、彩带以及BGM', '不要光环与音乐']:
            self.assertEqual({row['status'] for row in music_directives(clause)}, {'negative'})
            _, canonical = generated(); shot = canonical['shots'][0]; shot['生成限制'] = '9:16竖屏；' + clause
            self.assertEqual(project_formal_master(shot), compile_formal_merged_field(shot, assume_compact=True))
            self.assertNotIn('配乐', project_gpt_keyframe(shot))
        for clause in ['禁止字幕但加入音乐', '不加字幕、水印并加入背景音乐', '无BGM；随后播放音乐',
                       '无BGM且加入配乐', '无BGM并恢复配乐', '无BGM而恢复配乐', '无BGM后恢复配乐',
                       '背景音乐低沉铺底', '配乐渐强烘托情绪', '不是没有音乐', '不能没有BGM']:
            self.assertIn('affirmative', {row['status'] for row in music_directives(clause)})
        for clause in ['是否加入音乐', '未禁止音乐', '音乐待定', '如果需要则播放BGM',
                       '没有禁止配乐', '不必没有BGM', '无须禁止音乐']:
            self.assertEqual({row['status'] for row in music_directives(clause)}, {'unknown'})
        for clause in ['此镜不加音乐', '音乐不得响起', '禁止字幕和使用配乐']:
            self.assertEqual({row['status'] for row in music_directives(clause)}, {'negative'})
        self.assertEqual(music_directives('沈值班员说道：“禁止音乐。”'), [])

    def test_malformed_semantic_evidence_returns_diagnostics_and_unknown(self):
        _, canonical = generated()
        for malformed in ('invalid', None, []):
            with self.subTest(prevention_evidence=malformed):
                obj = copy.deepcopy(canonical)
                obj['prevention_evidence'] = malformed
                self.assertTrue(validate_project(obj)['issues'])
        obj = copy.deepcopy(canonical)
        obj['prevention_evidence']['source_ledger'] = [
            {'source_id': 'S1', 'source_text': '林工程师告诉站长备用系统已经断电。', 'target_shots': 7}]
        checks = validate_project(obj)['quality_metrics']['semantic_checks']
        self.assertTrue(any(c['check_id'] == 'source_communication' and c['status'] == 'unknown' for c in checks))
        for evidence in ({'rows': None}, {'rows': [{'shot_id': canonical['shots'][0]['镜头编号'], 'cells': 'invalid'}]}):
            with self.subTest(saved_evidence=evidence):
                obj = copy.deepcopy(canonical)
                obj['audit_workbook_evidence'] = evidence
                report = validate_project(obj)
                self.assertTrue(report['issues'])
                self.assertTrue(any(c['check_id'] == 'saved_projection_format' and c['status'] == 'unknown'
                                    for c in report['quality_metrics']['semantic_checks']))

    def test_unquoted_communication_without_optional_contract_remains_unknown(self):
        for source in ['站长通知工程师明日检修。', '驿使向军官宣读撤离命令。', '船长请求舵手关闭舱门。']:
            shots = [{'镜头编号': 'EP01-001', '原文支撑句': source, '画面内容': '听者点头。',
                      '心理音轨': '旁白：' + source}]
            for obj in ({}, {'workflow_profile': 'R10-PG1', 'prevention_evidence': {}}):
                issues = _validate_adapted_dialogue_audit(obj, shots, {})
                self.assertTrue(issues)
                self.assertEqual({row['check_status'] for row in issues}, {'unknown'})
                self.assertEqual(issues[0]['shot_ids'], ['EP01-001'])

    def test_exact_colon_source_dialogue_is_not_invented_adaptation(self):
        shot = {'镜头编号': 'EP01-001', '原文支撑句': '罗工程师说道：可以启动检修。',
                '画面内容': '罗工程师说道：“可以启动检修。”'}
        obj = {'prevention_evidence': {'source_decisions': {'uses': [
            {'id': 'U1', 'actor': '罗工程师', 'mode': 'spoken', 'text': '可以启动检修。'}]}}}
        self.assertEqual(_validate_adapted_dialogue_audit(obj, [shot], {}, production_verified=True), [])
        self.assertEqual(_validate_adapted_dialogue_audit(obj, [shot], {})[0]['check_status'], 'unknown')
        shot['画面内容'] = '罗工程师说道：“立即撤离。”'
        self.assertEqual(_validate_adapted_dialogue_audit(obj, [shot], {}, production_verified=True)[0]['check_status'], 'unknown')


if __name__ == '__main__':
    unittest.main()
