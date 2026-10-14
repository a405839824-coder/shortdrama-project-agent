"""Historical false-trigger cases plus adversarial counterexamples.

These tests verify specific parsing boundaries, not image/video accuracy.
"""
from pathlib import Path
import copy
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'server'), str(ROOT / 'tests'),
               str(ROOT / 'skills/shortdrama-storyboard/scripts')]

from bounded_normalization import (normalize_project_literals, normalize_music_prohibition,
                                   normalize_shot_literals, normalize_emphasis_quotes,
                                   normalize_authoring_request)
from projection_semantics import mask_static_eye_exclusions, music_directives
from visible_entities import resolve_visible_subjects
from validate_storyboard import (_keyframe_eye_process_hits, _validate_public_scene_ecology,
                                 _public_audio_layers, validate_saved_projection, validate_project,
                                 project_formal_master)
from test_strict_prompt_contract import strict_fixture


class BoundedNormalizationTests(unittest.TestCase):
    def test_music_equivalents_are_canonical_and_preserve_other_bans(self):
        for phrase in ['不加额外配乐', '禁止BGM', '无背景音乐', '不得使用配乐']:
            with self.subTest(phrase=phrase):
                value = '9:16竖屏；' + phrase + '；禁止字幕、水印'
                normalized = normalize_music_prohibition(value)
                self.assertIn('禁止字幕、水印', normalized)
                self.assertIn('禁止额外配乐', normalized)
                self.assertNotIn('BGM', normalized)
                self.assertEqual(normalize_music_prohibition(normalized), normalized)
        self.assertEqual(normalize_music_prohibition('禁止BGM、字幕'), '禁止字幕；禁止额外配乐')
        bound = '9:16竖屏；禁止额外配乐。眼部动作按本镜已列事件执行；禁止字幕。'
        self.assertEqual(normalize_music_prohibition(bound), bound)

    def test_music_unknown_conflict_and_quoted_dialogue_are_never_rewritten(self):
        for text in ['不加配乐，但加入背景音乐', '不一定禁止配乐', '如果没有配乐则加入背景音乐',
                     '不能没有配乐', '无需禁止配乐', '配乐是否加入待定',
                     '她说道：“不加额外配乐。”']:
            with self.subTest(text=text):
                self.assertEqual(normalize_music_prohibition(text), text)

    def test_entry_normalization_records_exact_changes_and_preserves_source(self):
        project = {'shots': [{'画面内容': '苏清柳站定。', '音色': '无人物发言。',
                              '生成限制': '9:16竖屏；不加额外配乐',
                              '原文支撑句': '他说：“不加额外配乐。”'}]}
        before = copy.deepcopy(project)
        normalized, changes = normalize_project_literals(project)
        self.assertEqual(project, before)
        self.assertEqual(len(changes), 2)
        self.assertEqual(normalized['shots'][0]['音色'], '无对白，不适用。')
        self.assertEqual(normalized['shots'][0]['原文支撑句'], before['shots'][0]['原文支撑句'])
        self.assertEqual({row['path'] for row in changes}, {'shots[0].音色', 'shots[0].生成限制'})
        again, repeat = normalize_project_literals(normalized)
        self.assertEqual(again, normalized)
        self.assertEqual(repeat, [])

    def test_no_dialogue_alias_cannot_hide_spoken_or_unknown_speech(self):
        for picture in ['苏清柳说道：“请坐。”', '苏清柳说：请坐。']:
            shot = {'画面内容': picture, '音色': '无人物发言。'}
            self.assertEqual(normalize_shot_literals(shot)[0], shot)
        unknown = {'画面内容': '苏清柳站定。', '音色': '没有新增对白，但保留上一句。'}
        self.assertEqual(normalize_shot_literals(unknown)[0], unknown)

    def test_emphasis_list_split_requires_actual_unambiguous_words(self):
        rows = [('苏清柳', '明日请你先来。')]
        original = '基础声线：保留原声线。\n重音：“明日、先来”。\n尾音：末字“来”短收。'
        result = normalize_emphasis_quotes(original, rows)
        self.assertEqual(result, original.replace('“明日、先来”', '“明日”、“先来”'))
        for invalid in ['重音：“明日、后日”。', '重音：“明日、”。']:
            self.assertEqual(normalize_emphasis_quotes(invalid, rows), invalid)
        quoted_literal = '重音：“笔、墨”。'
        self.assertEqual(normalize_emphasis_quotes(quoted_literal, [('甲', '把笔、墨拿来。')]), quoted_literal)
        multi = '重音：“明日、先来”。'
        self.assertEqual(normalize_emphasis_quotes(multi, [('甲', '明日。'), ('乙', '先来。')]), multi)

    def test_production_request_normalizes_before_binding_without_touching_source(self):
        request = {'contract': 'production-authoring-request-v1', 'source_graph_sha256': 'a' * 64,
                   'source_graph_artifact_id': 'SOURCE-1', 'decisions': {
                       'contract': 'production-decisions-v1', 'initial_world': {'unchanged': True},
                       'project': {'prevention_evidence': {'source_decisions': {'uses': [
                           {'id': 'U1', 'mode': 'spoken', 'actor': '苏清柳', 'text': '明日请你先来。'}]}}},
                       'shots': [{'actions': [{'kind': 'speak', 'source_use_id': 'U1',
                                              'voice': {'emphasis': '“明日、先来”。', 'rhythm': '保持原值。'}}]}]}}
        original = copy.deepcopy(request)
        normalized, changes = normalize_authoring_request(request)
        self.assertEqual(request, original)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]['path'], 'decisions.shots[0].actions[0].voice.emphasis')
        self.assertEqual(normalized['decisions']['project'], original['decisions']['project'])
        self.assertEqual(normalized['source_graph_sha256'], original['source_graph_sha256'])
        self.assertEqual(normalized['decisions']['initial_world'], original['decisions']['initial_world'])
        self.assertEqual(normalized['decisions']['shots'][0]['actions'][0]['voice']['emphasis'], '“明日”、“先来”。')
        self.assertEqual(normalize_authoring_request(normalized)[1], [])
        request['decisions']['shots'][0]['actions'][0]['source_use_id'] = 'MISSING'
        self.assertEqual(normalize_authoring_request(request), (request, []))

    def test_negated_half_blink_is_static_but_real_action_still_fails(self):
        for text in ['双眼自然睁开，不定格于半眨眼状态。', '双眼睁开，禁止定格在半眨眼瞬间。',
                     '双眼自然睁开，不处于半眨眼瞬间。']:
            self.assertEqual(_keyframe_eye_process_hits(text), [], text)
            self.assertEqual(len(mask_static_eye_exclusions(text)), len(text))
        for text in ['先眨眼再转头。', '先闭眼再睁眼。', '苏清柳从闭眼到睁眼。',
                     '不定格于半眨眼状态，但眼睑持续开合。',
                     '不能不定格于半眨眼状态。', '如果不定格于半眨眼状态则眨眼。']:
            self.assertTrue(_keyframe_eye_process_hits(text), text)
        for text in ['苏清柳双眼闭合。', '苏清柳双眼自然睁开。', '禁止从闭眼到睁眼的连续过程。']:
            self.assertFalse(_keyframe_eye_process_hits(text), text)

    def test_actual_saved_static_field_uses_same_negation_parser(self):
        shot = {'镜头编号': 'EP90-001', '可见主体': '苏清柳（面部可见）',
                '起始状态': '苏清柳双眼自然睁开。'}
        for text, expected in [('不定格于半眨眼状态。', 'pass'), ('苏清柳眨眼一次。', 'fail')]:
            checks = validate_saved_projection(shot, {'GPT关键帧生成指令': text})
            self.assertEqual(next(row['status'] for row in checks if row['check_id'] == 'saved_keyframe_static'), expected)

    def test_owned_shoulders_resolve_to_person_not_extra_actor(self):
        r = resolve_visible_subjects('苏清柳的肩线、肩头；道具：杯盖', people=['苏清柳'])
        self.assertEqual(r.people, ('苏清柳',))
        self.assertEqual(r.non_people, ('杯盖',))
        self.assertFalse(r.faults)
        self.assertEqual(resolve_visible_subjects('肩线').unknown, ('肩线',))
        self.assertEqual(resolve_visible_subjects('肩头；未知人名').people, ())
        conflict = resolve_visible_subjects('木偶的肩线', non_people=['木偶'])
        self.assertTrue(conflict.conflicts)

    def public_shot(self):
        return {'镜头编号': 'EP68-006', '镜头类型': '中景', '场景连续性ID': 'EP68-SC01',
                '可见主体': '苏文礼、陈公子、元宝全身与马车车阶',
                '空间站位描述': '文礼与陈在车旁通路，元宝站车门侧。',
                '起始状态': '马车停稳。', '画面内容': '苏文礼拱手告辞后登车。',
                '环境动态': '会馆外街道普通人流稀疏，中后景两名无剧情任务的行人分先后沿街道另一侧经过；远景路人继续散行。',
                '环境音效': '脚步与车阶受力轻响。\n近层为本镜衣料与石路轻足音，中层为街道另一侧稀疏脚步，远层为弱车轮与无可辨词的人声底。人物发言或旁白时各背景层压低。',
                '生成限制': '9:16竖屏；禁止额外配乐。'}

    def test_historical_ep68_explicit_action_and_audio_layers_are_recognized(self):
        shot = self.public_shot()
        issues, metrics = _validate_public_scene_ecology([shot], {}, {})
        self.assertFalse(any(row['error_code'] in {'E-PUBLIC-CROWD-001', 'E-PUBLIC-AUDIO-001'} for row in issues), issues)
        self.assertEqual(metrics['public_scene_visual_pass_count'], 1)
        self.assertEqual(metrics['public_scene_audio_pass_count'], 1)

    def test_missing_or_negated_ecology_still_fails(self):
        shot = self.public_shot()
        shot['环境动态'] = '会馆外街道普通人流稀疏，中后景行人保持静止，不分先后沿街道经过。'
        shot['环境音效'] = '近层无脚步声，中层无低声交谈，远层无车轮声。'
        issues, _ = _validate_public_scene_ecology([shot], {}, {})
        codes = {row['error_code'] for row in issues}
        self.assertIn('E-PUBLIC-CROWD-001', codes)
        self.assertIn('E-PUBLIC-AUDIO-001', codes)
        self.assertEqual(_public_audio_layers('近层、中层、远层；脚步、含混人声。'), set())

    def test_full_validator_accepts_ban_synonym_but_blocks_conflict_and_unknown(self):
        canonical = strict_fixture()
        for phrase, blocked in [('不加额外配乐', False), ('禁止BGM', False),
                                ('禁止额外配乐，但加入背景音乐', True),
                                ('禁止额外配乐，但配乐是否加入待定', True)]:
            with self.subTest(phrase=phrase):
                obj = copy.deepcopy(canonical)
                obj['shots'][0]['生成限制'] = obj['shots'][0]['生成限制'].replace('禁止额外配乐', phrase)
                report = validate_project(obj)
                found = [row for row in report['issues'] if row['error_code'] == 'E-AUDIO-STAGE-001'
                         and row['location'].endswith('.生成限制')]
                self.assertEqual(bool(found), blocked, found)
                if not blocked:
                    formal = project_formal_master(obj['shots'][0])
                    self.assertIn('禁止额外配乐', formal)
                    self.assertNotIn('BGM', formal)
        obj = copy.deepcopy(canonical)
        obj['shots'][0]['环境音效'] = '取消禁止配乐。'
        self.assertTrue(any(row['error_code'] == 'E-AUDIO-STAGE-001'
                            and row['location'].endswith('.环境音效')
                            for row in validate_project(obj)['issues']))


if __name__ == '__main__':
    unittest.main()
