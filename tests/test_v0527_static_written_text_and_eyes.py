"""Real EP33 paper labels and video eye policy must survive the correct route."""
import copy
import unittest

from shortdrama_mcp.materializer import compile_gpt_keyframe_instruction, compile_formal_merged_field
from test_v051_template_projection import compact_storyboard
from validate_storyboard import project_gpt_keyframe, project_formal_master


class StaticWrittenTextAndEyesTests(unittest.TestCase):
    def shot(self):
        shot = copy.deepcopy(compact_storyboard()['shots'][0])
        shot['生成限制'] = '9:16竖屏；禁止额外配乐；不看镜头；禁止字幕、水印；空气清澈、无雾、无烟。'
        return shot

    def both(self, shot):
        before = copy.deepcopy(shot)
        result = compile_gpt_keyframe_instruction(shot, assume_compact=True)
        self.assertEqual(result, project_gpt_keyframe(shot))
        self.assertEqual(before, shot)
        return result

    def reject(self, shot):
        for fn in (project_gpt_keyframe, lambda s: compile_gpt_keyframe_instruction(s, assume_compact=True)):
            with self.assertRaisesRegex(ValueError, '(?:field_content_violation|keyframe_static_contract_violation)'):
                fn(shot)

    def test_actual_three_paper_label_shapes_keep_all_quotes(self):
        cases = [
            '纸面已有简略岸线及“得昌下水处”“孩子上岸处”“最初站位”的小字定位标签，管事笔尖停在标签旁，左手压纸边。',
            '记录人笔尖停在简略岸线旁，纸面局部已有“孩子失足处”“得昌下水处”“最早站位”标签；纸张四边不同时入画。',
            '记录纸三栏题头为“体温”“呼吸”“神志”，没有拍出数值；干毯与相握手位于右侧榻边。',
            '记录纸已按孩子实际话语顺序写好前段，最后一行写到“我踩上去，”；笔尖停在逗号后。',
            '外侧页签文字“镜湖值守记录”清楚。',
        ]
        import re
        for value in cases:
            with self.subTest(value=value):
                shot = self.shot()
                shot['起始状态'] = value
                result = self.both(shot)
                for label in re.findall('“[^”]+”', value):
                    self.assertIn(label, result)

    def test_final_visible_quote_is_balanced(self):
        shot = self.shot()
        shot['起始状态'] = '记录纸题头为“体温”“呼吸”“神志”'
        self.assertIn(shot['起始状态'], self.both(shot))

    def test_formal_transition_sentences_are_identical_between_entrances(self):
        shot = self.shot()
        shot['切镜/转场提示词'] = '位置记录留待后续核对。硬切到外院候问处，人物手掌已放回案边。'
        before = copy.deepcopy(shot)
        self.assertEqual(compile_formal_merged_field(shot, assume_compact=True), project_formal_master(shot))
        self.assertEqual(shot, before)

    def test_motion_words_written_on_paper_are_not_execution(self):
        shot = self.shot()
        shot['起始状态'] = '纸面标签写着“眨眼”“两秒后”“下一镜”，纸边平整。'
        result = self.both(shot)
        self.assertIn('“眨眼”“两秒后”“下一镜”', result)

    def test_speech_near_paper_is_not_reclassified_as_visible_text(self):
        from shortdrama_mcp.materializer import _visual_only_text
        from validate_storyboard import _visual_only_clauses

        shot = self.shot()
        shot['起始状态'] = '记录人手按纸面标签，低声说道：“快回来。”'
        for fn in (project_gpt_keyframe, lambda s: compile_gpt_keyframe_instruction(s, assume_compact=True)):
            with self.assertRaisesRegex(ValueError, '(?:field_content_violation|keyframe_static_contract_violation: unbound_visual_text)'):
                fn(shot)
        for fn in (_visual_only_text, _visual_only_clauses):
            result = fn(shot['起始状态'], strip_dialogue=True)
            self.assertNotIn('快回来', result)
            self.assertIn('手按纸面标签', result)

    def test_dynamic_directive_after_visible_label_still_rejected(self):
        for value in ('纸面标签写着“猫”，随后抬手。', '纸面标签写着“猫”随后抬手。', '【可见文字】“猫”随后抬手。'):
            with self.subTest(value=value):
                shot = self.shot()
                shot['起始状态'] = value
                self.reject(shot)

    def test_standard_video_eye_policy_removed_only_in_static(self):
        shot = self.shot()
        policy = '眼神自然聚焦，眼球轻微换焦与位移、眼睑松紧按画面内容执行。禁止无依据全程不眨眼；禁止僵直瞪视和目光僵死；禁止高频机械眨眼；禁止多人同步眨眼。'
        shot['生成限制'] += policy
        result = self.both(shot)
        for word in ('换焦', '位移', '松紧', '眨眼'):
            self.assertNotIn(word, result)
        for word in ('眼神自然聚焦', '禁止僵直瞪视和目光僵死', '禁止字幕、水印', '空气清澈、无雾、无烟'):
            self.assertIn(word, result)
        formal = compile_formal_merged_field(shot, assume_compact=True)
        self.assertEqual(formal, project_formal_master(shot))
        self.assertIn(policy.rstrip('。'), formal)

    def test_mixed_unknown_policy_not_silently_dropped_or_polarity_flipped(self):
        for policy in ('禁止高频机械眨眼、雾化', '禁止多人同步眨眼，空气清澈', '眼球换焦并保持皮肤纹理'):
            with self.subTest(policy=policy):
                shot = self.shot()
                shot['生成限制'] += policy
                self.reject(shot)

    def test_explicit_video_eye_scope_projects_only_to_video(self):
        for policy in (
            '视频中的换焦、眼睑与眨眼按画面内容所列时点执行',
            '视频中眼球换焦和眨眼按画面内容执行',
            '动态画面中的眼睑变化与眨眼按画面内容执行',
        ):
            with self.subTest(policy=policy):
                shot = self.shot()
                shot['生成限制'] += '首帧双眼自然睁开、自然聚焦。' + policy + '。禁止僵直瞪视。'
                result = self.both(shot)
                self.assertNotIn(policy, result)
                self.assertIn('首帧双眼自然睁开、自然聚焦', result)
                self.assertIn('禁止僵直瞪视', result)
                formal = compile_formal_merged_field(shot, assume_compact=True)
                self.assertEqual(formal, project_formal_master(shot))
                self.assertIn(policy, formal)

    def test_mixed_video_scope_cannot_hide_body_action_or_negation(self):
        for policy in (
            '视频中的换焦与抬手按画面内容执行',
            '视频中的抬手按画面内容执行',
            '视频中的开门按画面内容执行',
            '禁止视频中的换焦按画面内容执行',
            '视频中的换焦，按画面内容执行',
            '视频中的换焦、抬手与眨眼按画面内容所列时点执行',
            '视频中的换焦、眼睑与眨眼按画面内容所列时点执行并保持皮肤纹理',
            '禁止视频中的换焦、眼睑与眨眼按画面内容所列时点执行',
            '视频中的换焦、眼睑与眨眼按画面内容所列时点执行，空气清澈',
        ):
            with self.subTest(policy=policy):
                shot = self.shot()
                shot['生成限制'] += policy
                self.reject(shot)

    def test_video_policy_words_inside_written_text_remain_exact(self):
        for visible in (
            '【可见文字】“说明；视频中的换焦、眼睑与眨眼按画面内容所列时点执行；完”',
            '纸面可见文字为“说明；视频中的眨眼按画面内容执行；完”',
        ):
            with self.subTest(visible=visible):
                shot = self.shot()
                shot['生成限制'] += visible
                result = self.both(shot)
                self.assertIn(visible, result)

    def test_existing_contract_policy_forms_are_video_only(self):
        for policy in ('人物眼神自然聚焦，保留轻微眼球焦点调整和眼睑松紧变化', '眼神自然聚焦；眼球换焦或位移；眼睑松紧变化'):
            with self.subTest(policy=policy):
                shot = self.shot()
                shot['生成限制'] += policy
                result = self.both(shot)
                self.assertIn('眼神自然聚焦', result)
                for motion in ('焦点调整', '换焦', '松紧'):
                    self.assertNotIn(motion, result)

    def test_actual_eye_motion_in_start_or_environment_is_rejected(self):
        for field, value in (('起始状态', '眼球轻微换焦与位移'), ('环境动态', '人物眨眼一次'), ('起始状态', '眼睑缓慢开合')):
            with self.subTest(field=field, value=value):
                shot = self.shot()
                shot[field] = value
                self.reject(shot)

    def test_single_frame_avoids_half_blink_without_animation(self):
        shot = self.shot()
        shot['起始状态'] = '双眼自然睁开，避开半眨眼瞬间；瞳孔朝向纸面。'
        self.assertIn('双眼自然睁开', self.both(shot))

    def test_authored_video_processes_are_rejected_in_static_fields(self):
        for field, value in (
            ('空间站位描述', '府医仍在西侧画外接近。'),
            ('镜头类型', '单人全身中景，容纳站立至蹲下动作'),
            ('机位', '蹲下后仍留足脸部空间。'),
            ('镜头类型', '中全景，府医蹲姿至站姿的全身范围'),
            ('空间站位描述', '暖轿沿西向道路返院，暖轿仍停镜湖西侧岸路。'),
            ('空间站位描述', '苏清柳在敞开的南侧轿门外随行'),
            ('空间站位描述', '画框只保留当前正在书写的纸面局部。'),
            ('机位', '东门旁荣婶儿在画外能够听清低声交代'),
            ('空间站位描述', '府医从案西侧沿案南侧空隙接近得昌'),
        ):
            with self.subTest(field=field, value=value):
                shot = self.shot()
                shot[field] = value
                self.reject(shot)

    def test_static_pose_and_written_process_words_are_retained(self):
        shot = self.shot()
        shot['起始状态'] = '府医为蹲姿，笔尖停在纸面上方；纸面标签写着“正在书写”“听清交代”。'
        result = self.both(shot)
        self.assertIn('府医为蹲姿', result)
        self.assertIn('“正在书写”“听清交代”', result)

    def test_explicit_visual_marker_never_hides_trailing_environment_motion(self):
        for value in ('【可见文字】“猫”衣摆持续摆动。', '门牌写着“猫”衣摆持续摆动。'):
            with self.subTest(value=value):
                shot = self.shot()
                shot['起始状态'] = value
                self.reject(shot)

    def test_punctuation_inside_explicit_written_payload_is_not_a_boundary(self):
        shot = self.shot()
        shot['起始状态'] = '【可见文字】“两秒后，随后抬手”；纸边平整。'
        self.assertIn('“两秒后，随后抬手”', self.both(shot))

    def test_speech_after_paper_label_is_removed_without_losing_label(self):
        shot = self.shot()
        shot['起始状态'] = '纸面标签写着“猫”且人物说“快回来。”'
        from shortdrama_mcp.materializer import _visual_only_text
        result = _visual_only_text(shot['起始状态'], strip_dialogue=True)
        self.assertIn('“猫”', result)
        self.assertNotIn('快回来', result)
        self.reject(shot)  # An unfinished speech attribution is not a still pose.


if __name__ == '__main__':
    unittest.main()
