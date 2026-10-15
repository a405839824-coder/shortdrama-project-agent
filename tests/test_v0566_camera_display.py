"""Camera prose uses the registered label without renaming geometric identities."""
import copy
import unittest
from test_v0552_spatial_presentation import production_fixture, lower_production_decisions
from staging_text_bindings import bind_fields


class CameraDisplayTests(unittest.TestCase):
    def test_visible_held_prop_keeps_holder_in_independent_initial_frame(self):
        from shortdrama_mcp.materializer import compile_gpt_keyframe_instruction
        data = production_fixture()
        data['initial_world']['props']['信纸']['anchor'] = {'actor':'甲','hand':'right'}
        result = lower_production_decisions(data)['typed_decisions']['shots'][0]
        fields, _ = bind_fields(result['fields'], result['staging'])
        self.assertIn('信纸在甲右手中。', fields['起始状态'])
        self.assertIn('信纸在甲右手中', compile_gpt_keyframe_instruction(fields,assume_compact=True))

    def test_registered_target_label_reaches_actual_camera_field(self):
        data = production_fixture()
        identity = data['shots'][0]['camera']['target']
        data['initial_world']['points'][identity]['display_name'] = '榻前取景点'
        original = copy.deepcopy(data)
        result = lower_production_decisions(data)['typed_decisions']['shots'][0]
        fields, _ = bind_fields(result['fields'], result['staging'])
        self.assertIn('摄影机位于榻前取景点', fields['机位'])
        self.assertNotIn(identity, fields['机位'])
        self.assertEqual(data, original)


if __name__ == '__main__':
    unittest.main()
