"""Independent geometric, field ownership and presentation counterexamples."""
import copy
import hashlib
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'skills/shortdrama-storyboard/scripts'), str(ROOT / 'server')]
from production_decisions import (ProductionDecisionError, _action_sentence, _camera,
    _display_action, _opening_description, _reference, _screen_place, _spatial_description, _world, lower_production_decisions)
from staging_geometry import screen_direction
from field_content import field_content_findings, appearance_anchor_text, r5_keyframe_sections
from picture_presentation import separate_shot_clock, separate_picture_clock, timed_picture, display_picture, FIELD
from test_v0527_production_decisions import production_fixture, action


class CameraPresentationTests(unittest.TestCase):
    def camera(self, world, origin):
        return _camera(world, {'position_xyz': origin, 'target': '取景中心',
            'hfov_deg': 90, 'vfov_deg': 90, 'frame': 'wide'}, 'ROOM', 'camera')

    def test_reverse_changes_projection_not_world_or_facing_fact(self):
        data = production_fixture(); world = data['initial_world']; before = copy.deepcopy(world)
        front = self.camera(world, [0, 4, 1.1]); reverse = self.camera(world, [0, -4, 1.1])
        self.assertEqual(_screen_place([2, 0, 1.1], front), '画面左侧')
        self.assertEqual(_screen_place([2, 0, 1.1], reverse), '画面右侧')
        self.assertEqual(screen_direction(0, front['forward']), '镜头方向')
        self.assertEqual(screen_direction(0, reverse['forward']), '画面后方')
        self.assertEqual(screen_direction(90, reverse['forward']), '画面右侧')
        self.assertEqual(_screen_place([0, 5, 1.1], front), '画外摄影机后方')
        self.assertEqual(_screen_place([9, 0, 1.1], front), '画外左侧')
        self.assertEqual(world, before)

    def test_reference_keeps_identity_and_requires_unambiguous_display(self):
        world = production_fixture()['initial_world']; camera = self.camera(world, [0, 4, 1.1])
        world['points']['东客椅'] = {'scene_id': 'ROOM', 'position_xyz': [2, 0, 0]}
        ref = {'kind': 'point', 'id': '东客椅'}
        with self.assertRaisesRegex(ProductionDecisionError, 'display name'): _reference(world, ref, camera)
        world['points']['东客椅']['display_name'] = '靠背椅'
        self.assertEqual(_reference(world, ref, camera), '画面左侧的靠背椅')
        self.assertIn('东客椅', world['points'])
        world['points']['另一椅'] = {'scene_id': 'ROOM', 'position_xyz': [2.1, 0, 0], 'display_name': '靠背椅'}
        with self.assertRaisesRegex(ProductionDecisionError, 'distinguish'): _reference(world, ref, camera)
        world['points']['另一椅']['display_name'] = '扶手椅'
        self.assertEqual(_reference(world, ref, camera), '画面左侧的靠背椅')

    def test_opening_uses_known_geometry_and_never_guesses_missing_geometry(self):
        world = production_fixture()['initial_world']; camera = self.camera(world, [0, 4, 1.1])
        self.assertEqual(_opening_description(world, 'ROOM', camera), '画面左侧的纸窗关闭。')
        scene = world['scenes']['ROOM']; scene['door_window_state'] += '北侧门开启。'
        scene['opening_labels']['北侧门'] = '双扇门'
        with self.assertRaisesRegex(ProductionDecisionError, 'no left/right is guessed'):
            _opening_description(world, 'ROOM', camera)
        world['points']['北侧门'] = {'scene_id': 'ROOM', 'position_xyz': [-2, 0, 0], 'display_name': '双扇门'}
        self.assertIn('画面右侧的双扇门开启', _opening_description(world, 'ROOM', camera))

    def test_hold_turn_and_point_slots_use_camera_axes_not_word_replacement(self):
        data = production_fixture(); world = data['initial_world']; camera = self.camera(world, [0, 4, 1.1])
        before = world['actors']['甲']
        text = _action_sentence(action('hold'), {'before': before}, world, camera)[1]
        self.assertIn('身体朝镜头方向', text); self.assertNotIn('米', text)
        text = _action_sentence(action('turn', body_yaw=90, head_yaw=90), {'before': before}, world, camera)[1]
        self.assertIn('转身至身体朝画面左侧', text)
        world['points']['东落脚处'] = {'scene_id': 'ROOM', 'position_xyz': [2, 0, 0], 'display_name': '门前空地'}
        original = action('push_actor', point='东落脚处')
        shown = _display_action(original, world, camera)
        self.assertEqual(shown['point'], '画面左侧的门前空地')
        self.assertEqual(original['point'], '东落脚处')

    def test_generated_initial_and_scene_fields_have_no_world_placement(self):
        data = production_fixture(); before = copy.deepcopy(data)
        lowered = lower_production_decisions(data)
        fields = lowered['typed_decisions']['shots'][0]['fields']
        self.assertIn('身体朝镜头方向', fields['起始状态'])
        self.assertIn('画外左侧的纸窗关闭', fields['空间站位描述'])
        self.assertFalse([f for f in field_content_findings(fields) if f['role'] == 'world_position_in_screen_field'])
        self.assertEqual(data, before)

    def test_reference_height_is_not_ground_depth_even_with_camera_tilt(self):
        world = production_fixture()['initial_world']
        world['points']['门内'] = {'scene_id': 'ROOM', 'position_xyz': [0, 0, 1.25]}
        camera = self.camera(world, [0, 4, 2.5])
        row = {'shot_id': 'S1', 'scene_id': 'ROOM'}
        sources = {'production_plan': {'shots': [{'shot_id': 'S1', 'design': {'spatial_anchors': {'甲': '门内'}}}]}}
        text = _spatial_description(world, row, camera, {'actors': {'甲': {}}, 'props': []}, sources)
        self.assertIn('处于近旁', text)
        self.assertNotIn('画面后侧', text)

    def test_prop_alias_routes_initial_action_sound_and_keeps_physical_identity(self):
        data = production_fixture()
        prop = data['initial_world']['props'].pop('信纸')
        prop.update(display_name='信纸')
        data['initial_world']['props']['东案纸'] = prop
        data['shots'][0]['actions'] = [action('move_prop', prop='东案纸', hand='right', to='案右')]
        data['shots'][0]['editorial']['环境音效'] = {'mode': 'action_bound', 'cues': [
            {'action_id': 'act1', 'sound': 'prop_contact', 'volume': 'soft'}]}
        before = copy.deepcopy(data)
        result = lower_production_decisions(data)
        fields = result['typed_decisions']['shots'][0]['fields']
        phases = result['typed_decisions']['shots'][0]['phases']
        self.assertIn('信纸位于', fields['起始状态'])
        self.assertIn('移动信纸', fields['环境音效'])
        self.assertIn('移动信纸', ''.join(p['text'] for p in phases))
        self.assertNotIn('东案纸', fields['起始状态'] + fields['环境音效'] + ''.join(p['text'] for p in phases))
        self.assertIn('东案纸', result['derivation']['final_world']['props'])
        self.assertEqual(data, before)
        camera = self.camera(data['initial_world'], [0, 4, 1.1])
        sources = {'production_plan': {'shots': [{'shot_id': 'S1', 'design': {'spatial_anchors': {'东案纸': '案右'}}}]}}
        spatial = _spatial_description(data['initial_world'], {'shot_id': 'S1', 'scene_id': 'ROOM'},
            camera, {'actors': {}, 'props': ['东案纸']}, sources)
        self.assertIn('信纸位于', spatial); self.assertNotIn('东案纸', spatial)

    def test_display_prop_drop_looks_up_stable_physics_and_preserves_source_quote(self):
        data = production_fixture(); prop = data['initial_world']['props'].pop('信纸')
        prop.update(display_name='杯子', anchor={'actor': '甲', 'hand': 'right'})
        data['initial_world']['props']['东案杯'] = prop
        world = _world(data['initial_world'], {'written_objects_by_id': {}})
        camera = self.camera(world, [0, 4, 1.1])
        drop = action('drop_prop', prop='东案杯', hand='right', to='落脚处', contact=None, breaks=False)
        text = _action_sentence(drop, {'before_all': world}, world, camera)[1]
        self.assertIn('松开杯子', text); self.assertNotIn('东案杯', text)
        speech = action('speak')
        speech['actor'] = '甲'
        line = '把东案杯放下。'
        self.assertEqual(_action_sentence(speech, {'use': {'text': line}}, world, camera)[1], '甲说道：“' + line + '”')


class SpatialFieldBoundaryTests(unittest.TestCase):
    def spatial(self, **fields):
        return [r for r in field_content_findings(fields) if r['role'] == 'world_position_in_screen_field']

    def test_correct_prefix_does_not_mask_old_layout_in_any_output_field(self):
        for field in ('空间站位描述', '起始状态', '画面内容', '环境动态', '可见主体'):
            for old in ('甲站在北侧。', '甲身体朝东北。', '东客椅摆放在这里。', '南侧双扇门开启。', '甲坐在西侧。'):
                self.assertTrue(self.spatial(**{field: '甲在画面中部。' + old}), (field, old))

    def test_dialogue_ordinary_words_and_allowed_camera_light_are_preserved(self):
        text = '甲说道：“那东西放到东客椅，随后向北走。”'
        self.assertFalse(self.spatial(**{'画面内容': text}))
        self.assertFalse(self.spatial(**{'空间站位描述': '东西摆在画面右侧。南宫世子站在门旁。'}))
        self.assertFalse(self.spatial(**{'机位': '摄影机朝东北。', '光源方向': '光从世界东侧入射。'}))
        self.assertTrue(self.spatial(**{'起始状态': '站位要求：“甲位于北侧。”'}))

    def test_actual_renderers_reject_other_projected_spatial_slots(self):
        from test_v050_compact_master_io import compact_storyboard
        from validate_storyboard import project_formal_master, project_gpt_keyframe
        from shortdrama_mcp.materializer import compile_formal_merged_field, compile_gpt_keyframe_instruction
        for field, value in [('可见主体', '甲站在木案东侧'),
                             ('参考图锚定', '文字造型锚：甲站在木案东侧。'),
                             ('生成限制', '甲必须站在木案东侧。')]:
            for renderer in (project_formal_master, project_gpt_keyframe, compile_formal_merged_field, compile_gpt_keyframe_instruction):
                shot = compact_storyboard()['shots'][0]; shot[field] = value
                self.assertTrue(field_content_findings(shot), (field, renderer.__name__))
                with self.assertRaisesRegex(ValueError, 'field_content_violation'):
                    renderer(shot)

    def test_reference_ids_source_notes_and_owned_light_directions_remain_data(self):
        value = '文字造型锚：甲穿青色长袍。\n参考图ID：东侧原图123\n来源依据：原文曾在北侧。'
        self.assertEqual(appearance_anchor_text(value), '甲穿青色长袍。')
        self.assertFalse(field_content_findings({'参考图锚定': value}))
        self.assertFalse(self.spatial(**{'可见主体': '东方不败（面部）'}))
        for light in ('禁止改变东侧主光。', '主光位于东侧。'):
            self.assertFalse(self.spatial(**{'生成限制': light}))
            r5_keyframe_sections({}, aspect='9:16竖屏', visual_limits=light, appearance_anchor='')
        with self.assertRaisesRegex(ValueError, 'field_content_violation'):
            r5_keyframe_sections({}, aspect='9:16竖屏', visual_limits='东侧主光保持不变，甲必须站在西侧。', appearance_anchor='')

    def test_written_coordinates_use_the_same_literal_scope_in_all_renderers(self):
        from test_v0527_written_payload_binding import shot
        from validate_storyboard import project_formal_master, project_gpt_keyframe
        from shortdrama_mcp.materializer import compile_formal_merged_field, compile_gpt_keyframe_instruction
        for text in ('木牌刻有“东厢”；纸边平整。',
                     '纸面当前已有两个字段：去处为“北门”，朝向为“甲站在西侧”。',
                     '纸面不是写着“甲位于东侧”，而是写着“乙位于西侧”。'):
            value = shot(text); before = copy.deepcopy(value)
            self.assertFalse(field_content_findings(value), text)
            formal = project_formal_master(value)
            self.assertEqual(formal, compile_formal_merged_field(value, assume_compact=True))
            self.assertEqual(project_gpt_keyframe(value), compile_gpt_keyframe_instruction(value, assume_compact=True))
            self.assertIn(text, formal)
            self.assertEqual(value, before)
        limits = '【可见文字】“甲站在东侧”；禁止字幕。'
        self.assertFalse(self.spatial(**{'生成限制': limits}))
        r5_keyframe_sections({}, aspect='9:16竖屏', visual_limits=limits, appearance_anchor='')

    def test_written_scope_never_masks_external_staging_or_fake_carriers(self):
        for text in ('站位要求：“甲位于北侧。”', '站位要求写着“甲位于北侧。”',
                     '写着“甲位于北侧。”', '纸面写着“北门”，甲站在西侧。',
                     '纸面写着“北门”。位置为“甲位于西侧”。',
                     '页首朝北；纸面现有文字：“北门”。'):
            self.assertTrue(self.spatial(**{'起始状态': text}), text)
        text = '纸面写着“北门”，甲站在木案西侧。'
        faults = self.spatial(**{'起始状态': text})
        self.assertEqual(len(faults), 1)
        self.assertEqual(faults[0]['excerpt'], '西侧')
        self.assertEqual(text[slice(*faults[0]['offset'])], '西侧')
        with self.assertRaisesRegex(ValueError, 'field_content_violation'):
            r5_keyframe_sections({}, aspect='9:16竖屏', visual_limits='站位要求：“甲位于北侧。”', appearance_anchor='')

    def test_bare_scene_reference_is_distinct_from_subject_placement(self):
        for name in ('南门通道', '南门通道。', '南门通道：'):
            self.assertFalse(self.spatial(**{'空间站位描述': name}), name)
        for text in ('甲站在南门通道。', '南门通道位于东侧。',
                     '南门通道，甲位于北侧。', '东客椅', '甲朝南门通道。'):
            self.assertTrue(self.spatial(**{'空间站位描述': text}), text)


class NaturalPictureTests(unittest.TestCase):
    def test_clock_owned_comma_removed_but_literal_and_relative_order_survive(self):
        text = '0.0秒，甲说道：“三秒后，向东走。”三秒后，甲抬手。'
        shot = separate_shot_clock({'画面内容': text, '可见主体': '甲（面部）'})
        self.assertEqual(shot['画面内容'], '甲说道：“三秒后，向东走。”随后，甲抬手。')
        self.assertEqual(timed_picture(shot), text)

    def test_invariant_states_fold_but_repeated_events_and_original_timing_remain(self):
        state = '甲口部入画时口型与对白一致。甲眼睛清楚可见。甲视线持续停在乙。甲头部保持不动。甲最终视线停在乙。'
        text = '\n'.join(f'【{i}.0—{i+1}.0秒】{i}.0秒，甲自然眨眼一次。{state}' for i in range(3))
        shot = separate_shot_clock({'画面内容': text, '可见主体': '甲（面部）'})
        self.assertEqual(shot['画面内容'].count('甲眼睛清楚可见。'), 1)
        self.assertEqual(shot['画面内容'].count('甲口部入画时口型与对白一致。'), 1)
        self.assertEqual(shot['画面内容'].count('甲自然眨眼一次。'), 3)
        self.assertEqual(shot['画面内容'].count('甲最终视线停在乙。'), 1)
        self.assertEqual(timed_picture(shot), text)
        self.assertEqual(separate_shot_clock(shot), shot)
        changed = text.replace('【1.0—2.0秒】1.0秒，甲自然眨眼一次。' + state,
                               '【1.0—2.0秒】甲视线持续停在丙。')
        self.assertEqual(separate_shot_clock({'画面内容': changed, '可见主体': '甲（面部）'})['画面内容'].count('视线持续停在乙'), 2)

    def test_legacy_v1_with_dangling_comma_still_replays_exactly(self):
        text = '【0.0—1.0秒】0.0秒，甲抬手。'
        prose, record = separate_picture_clock(text, compact=False)
        self.assertEqual(record['contract'], 'picture-clock-annotations-v1')
        self.assertIn('，甲', prose)
        self.assertEqual(timed_picture({'画面内容': prose, FIELD: record}), text)
        self.assertEqual(display_picture({'画面内容': prose, FIELD: record}), '甲抬手。')

    def test_compaction_cannot_insert_a_new_event(self):
        text = '【0.0—1.0秒】甲眼睛清楚可见。\n【1.0—2.0秒】甲眼睛清楚可见。'
        shot = separate_shot_clock({'画面内容': text, '可见主体': '甲（面部）'})
        repetition = next(r for r in shot[FIELD]['annotations'] if r['kind'] == 'repeated_state')
        repetition['token'] = '甲自然眨眼一次。'
        with self.assertRaisesRegex(ValueError, 'token'): timed_picture(shot)

    def test_action_clause_cannot_masquerade_as_a_long_actor_name(self):
        clause = '甲推开乙后甲眼睛清楚可见。'
        text = '\n'.join(f'【{i}.0—{i+1}.0秒】{clause}' for i in range(3))
        shot = separate_shot_clock({'画面内容': text, '可见主体': '甲（面部）；乙（面部）'})
        self.assertEqual(shot['画面内容'].count(clause), 3)
        self.assertEqual(timed_picture(shot), text)


if __name__ == '__main__': unittest.main()
