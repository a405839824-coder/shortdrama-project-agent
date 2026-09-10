"""Actual production error patterns, represented as minimal planar decisions."""
from __future__ import annotations
import copy
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = (Path(__file__).resolve().parents[1] /
          "skills/shortdrama-storyboard/scripts/staging_geometry.py")
SPEC = importlib.util.spec_from_file_location("test_staging_geometry_module", SCRIPT)
geometry = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(geometry)


def actor(name, point, head=180, body=180, gaze=None, clear_face=True):
    return {"actor_id": name, "actor_position": point, "body_yaw": body,
            "head_yaw": head, "gaze_target": gaze if gaze is not None else [point[0], point[1]-1],
            "clear_face": clear_face}


def scene():
    return {"contract": geometry.CONTRACT, "camera": [-3,-3], "camera_target": [0,0],
            "actors": [actor("front",[0,0]), actor("rear",[0,3.5])]}


class StagingGeometryTests(unittest.TestCase):
    def test_r3_004_accountant_is_left_of_xu_from_northeast(self):
        data = {"contract":geometry.CONTRACT,"camera":[4,5],"camera_target":[1.5,2.15],
                "actors":[actor("xu",[.7,2.3],90,90,[2.8,2]),
                          actor("accountant",[2.8,2],270,270,[.7,2.3],False)],
                "screen_left_of":[{"left":"xu","right":"accountant"}]}
        result = geometry.analyze_scene(data)
        self.assertLess(result["actors"][1]["screen_x_normalized"],result["actors"][0]["screen_x_normalized"])
        self.assertEqual(result["screen_left_of_checks"][0]["check"],"contradiction")
        data["screen_left_of"]=[{"left":"accountant","right":"xu"}]
        self.assertEqual(geometry.analyze_scene(data)["screen_left_of_checks"][0]["check"],"consistent_2d_order_only")

    def test_r3_031_north_rear_row_is_left_not_right(self):
        data=scene(); data["screen_left_of"]=[{"left":"front","right":"rear"}]
        result=geometry.analyze_scene(data)
        self.assertEqual(result["status"],"contradiction_found")
        self.assertAlmostEqual(result["actors"][1]["screen_x_normalized"],-3.5/9.5)
        self.assertEqual(result["actors"][1]["screen_side_of_optical_axis"],"left")

    def test_ep33_038_end_and_040_start_east_head_northwest_camera(self):
        data={"contract":geometry.CONTRACT,"camera":[-3,3],"camera_target":[0,0],
              "actors":[actor("Su",[0,0],90,0,[2,0])]}
        result=geometry.analyze_scene(data)
        self.assertAlmostEqual(result["actors"][0]["head_to_camera_angle_degrees"],135)
        self.assertEqual(result["actors"][0]["clear_face_claim_check"],"contradiction")
        data["actors"][0]["head_yaw"]=0
        result=geometry.analyze_scene(data)
        self.assertAlmostEqual(result["actors"][0]["head_to_camera_angle_degrees"],45)
        self.assertEqual(result["actors"][0]["clear_face_claim_check"],"not_disproved_by_2d_geometry")
        self.assertFalse(result["commit_eligible"])

    def test_positive_profile_remains_unproven_and_gaze_not_automatically_fixed(self):
        data={"contract":geometry.CONTRACT,"camera":[-3,0],"camera_target":[0,0],
              "actors":[actor("Su",[0,0],0,0,[0,-2])]}
        before=copy.deepcopy(data); result=geometry.analyze_scene(data)["actors"][0]
        self.assertAlmostEqual(result["head_to_camera_angle_degrees"],90)
        self.assertEqual(result["clear_face_claim_check"],"not_disproved_by_2d_geometry")
        self.assertEqual(result["gaze_world_direction"],"南")
        self.assertEqual(result["gaze_minus_head_degrees"],-180)
        self.assertEqual(data,before)

    def test_nonpositive_depth_is_not_visible_but_offscreen_actor_can_be_described(self):
        for point in ([0,-2],[2,0]):
            data={"contract":geometry.CONTRACT,"camera":[0,0],"camera_target":[0,1],
                  "actors":[actor("A",list(point),0,0,[0,2])]}
            result=geometry.analyze_scene(data)
            self.assertEqual(result["actors"][0]["clear_face_claim_check"],"contradiction")
            self.assertIsNone(result["actors"][0]["screen_x_normalized"])
            data["actors"][0]["clear_face"]=False
            self.assertEqual(geometry.analyze_scene(data)["status"],"no_2d_contradiction_found")

    def test_perspective_order_uses_depth_not_raw_lateral_position(self):
        data={"contract":geometry.CONTRACT,"camera":[0,0],"camera_target":[0,1],
              "actors":[actor("near",[1,2],180,180,[1,1]),actor("far",[2,10],180,180,[2,9])],
              "screen_left_of":[{"left":"far","right":"near"}]}
        result=geometry.analyze_scene(data)
        self.assertGreater(result["actors"][1]["screen_x_world"],result["actors"][0]["screen_x_world"])
        self.assertLess(result["actors"][1]["screen_x_normalized"],result["actors"][0]["screen_x_normalized"])
        self.assertEqual(result["screen_left_of_checks"][0]["check"],"consistent_2d_order_only")

    def test_translation_and_uniform_scale_preserve_camera_facts(self):
        data=scene(); original=geometry.analyze_scene(data)
        for key in ["camera","camera_target"]:
            data[key]=[data[key][0]*7+11,data[key][1]*7-20]
        for a in data["actors"]:
            for key in ["actor_position","gaze_target"]:
                a[key]=[a[key][0]*7+11,a[key][1]*7-20]
        derived=geometry.analyze_scene(data)
        for a,b in zip(original["actors"],derived["actors"]):
            self.assertAlmostEqual(a["screen_x_normalized"],b["screen_x_normalized"])
            self.assertAlmostEqual(a["head_to_camera_angle_degrees"],b["head_to_camera_angle_degrees"])

    def test_subnormal_vectors_never_produce_incorrect_angles_or_order(self):
        # The old direct hypot normalization returned (1, 1) at the smallest
        # float, turning a true 45-degree head/camera angle into zero degrees.
        for scale in [1e-320, 5e-324]:
            with self.subTest(scale=scale):
                vector = geometry._unit((scale, scale), "test")
                self.assertAlmostEqual(math.hypot(*vector), 1)
                self.assertAlmostEqual(vector[0], math.sqrt(.5))
                data={"contract":geometry.CONTRACT,"camera":[0,0],
                      "camera_target":[scale,scale],
                      "actors":[actor("A",[scale,scale],180,180,[0,0])]}
                with self.assertRaisesRegex(geometry.GeometryInputError, "stable arithmetic range"):
                    geometry.analyze_scene(data)
        data={"contract":geometry.CONTRACT,"camera":[0,0],"camera_target":[1,1],
              "actors":[actor("A",[1,1],180,180,[0,0])]}
        self.assertAlmostEqual(geometry.analyze_scene(data)["actors"][0]["head_to_camera_angle_degrees"],45)

    def test_underflow_to_zero_projection_rejected_but_exact_zero_retained(self):
        with self.assertRaisesRegex(geometry.GeometryInputError, "stable arithmetic range"):
            geometry._dot((5e-324, 0), (.1, 1), "test")
        self.assertEqual(geometry._dot((0, 1), (1, 0), "test"), 0)

    def test_rejects_nonfinite_boolean_missing_and_undefined_directions(self):
        bad=[]
        for number in [True,False,"1",None,float("nan"),float("inf"),-float("inf")]:
            value=scene(); value["camera"][0]=number; bad.append(value)
        value=scene(); value["camera_target"]=value["camera"].copy(); bad.append(value)
        value=scene(); value["actors"][0]["actor_position"]=value["camera"].copy(); bad.append(value)
        value=scene(); value["actors"][0]["gaze_target"]=[0,0]; bad.append(value)
        for yaw in [True,360,-1,float("nan")]:
            value=scene(); value["actors"][0]["head_yaw"]=yaw; bad.append(value)
        value=scene(); del value["actors"][0]["body_yaw"]; bad.append(value)
        value=scene(); value["actors"][1]["actor_id"]="front"; bad.append(value)
        value=scene(); value["actors"][0]["clear_face"]="true"; bad.append(value)
        value=scene(); value["camera"][0]=1e308; value["camera_target"][0]=-1e308; bad.append(value)
        value=scene(); value["screen_left_of"]=[{"left":"front","right":"unknown"}]; bad.append(value)
        value=scene(); value["actors"][0]["invented_state"]="passed"; bad.append(value)
        for value in bad:
            with self.subTest(value=value):
                with self.assertRaises(geometry.GeometryInputError): geometry.analyze_scene(value)

    def test_cli_keeps_hash_rejects_duplicate_json_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); input_path=root/"decisions.json"; output_path=root/"report.json"
            raw=b'{"contract":"staging-geometry-v1","camera":[0,0],"camera":[1,1]}'
            input_path.write_bytes(raw)
            command=[sys.executable,str(SCRIPT),str(input_path),"--output",str(output_path)]
            result=subprocess.run(command,capture_output=True,text=True)
            self.assertEqual(result.returncode,2)
            report=json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(report["status"],"invalid_input")
            self.assertEqual(report["input_sha256"],hashlib.sha256(raw).hexdigest())
            original=output_path.read_bytes()
            input_path.write_text(json.dumps(scene()),encoding="utf-8")
            result=subprocess.run(command,capture_output=True,text=True)
            self.assertEqual(result.returncode,2)
            self.assertEqual(output_path.read_bytes(),original)
            self.assertEqual(json.loads(input_path.read_text(encoding="utf-8")),scene())


if __name__ == "__main__":
    unittest.main()
