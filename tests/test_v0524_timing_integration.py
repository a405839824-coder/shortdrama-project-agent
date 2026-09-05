"""Real validator/service timing checks without replacing either boundary.

Fixtures contain complete canonical fields.  Timing failures are selected by
their family and shot binding, so unrelated semantic failures cannot satisfy
these assertions.  Candidate calibration notices never authorize a pass.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest

from shortdrama_mcp import service as service_module
from shortdrama_mcp.canonical import COMPACT_SHOT_FIELDS
from test_efficiency import valid_storyboard


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PLUGIN_ROOT / "skills" / "shortdrama-storyboard"
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from test_strict_prompt_contract import strict_fixture  # noqa: E402
from run_golden_tests import (  # noqa: E402
    build_adjacent_continuity_audit,
    inject_pg1_fixture_evidence,
)
from validate_storyboard import (  # noqa: E402
    COMPACT_MOTHER_COLUMNS,
    project_export_row,
    validate_project,
)


def validate(candidate: dict) -> dict:
    return validate_project(
        candidate,
        mode="audit",
        rules_path=str(SKILL_ROOT / "references" / "storyboard_runtime_rules.json"),
    )


def timing_issues(report: dict) -> list[dict]:
    return [row for row in report["issues"] if row.get("check_family") == "timing"]


def short_dialogue_window() -> dict:
    candidate = strict_fixture()
    # Preserve the four-second shot, source dialogue and three positive,
    # contiguous phases.  Only its actual speech window becomes 0.2 seconds.
    candidate["shots"][0]["画面内容"] = (
        candidate["shots"][0]["画面内容"]
        .replace("【0.8—3.3秒】", "【0.8—1.0秒】")
        .replace("【3.3—4.0秒】", "【1.0—4.0秒】")
    )
    return candidate


def cross_scene_psychological_audio() -> dict:
    candidate = valid_storyboard()
    first = candidate["shots"][0]
    second = copy.deepcopy(first)
    second.update({
        "镜头编号": "EP01-002",
        "场景连续性ID": "EP01-SC02",
        "累计时长": 8.0,
        "空间站位描述": (
            "同一清晨，相邻书房室内；女主位于左前门边，桌与信件在画面右中部，"
            "闭合木窗在左后，仅出现有源人物与道具。"
        ),
        "切镜/转场提示词": "女主在相邻书房确认桌面信件，保持视线与手部落点后切。",
    })
    first["切镜/转场提示词"] = "心理音轨不断，画面切入相邻书房的门边与桌案。"
    candidate["shots"].append(second)
    candidate["total_duration"] = 8.0
    inject_pg1_fixture_evidence(candidate)
    evidence = candidate["prevention_evidence"]
    evidence["scene_state_ledger"][1]["transition_explanation"] = (
        "场景切入相邻书房，省略中间走动；同一心理发言以声音桥连续覆盖切镜。"
    )
    evidence["adjacent_continuity_audit"] = build_adjacent_continuity_audit(
        candidate["shots"], evidence["scene_state_ledger"]
    )
    spoken = "我得先看清信上写了什么再决定。"
    first["心理音轨"] = (
        f"心理旁白：“{spoken}”；后期声音；人物不张嘴、不驱动口型、"
        "不生成字幕、不计入角色对白。"
    )
    second["心理音轨"] = (
        "心理音轨引用：PSY-CROSS-001；按后台片段范围连续发声；"
        "人物不张嘴、不驱动口型、不生成字幕、不计入角色对白。"
    )
    evidence["audio_workflow"]["timing"] = {
        "contract": "shot-timing-v1",
        "utterances": [{
            "id": "PSY-CROSS-001",
            "kind": "psychological",
            "speaker": "女主",
            "source": {"shot_id": "EP01-001"},
            "segments": [
                {"shot_id": "EP01-001", "start": 2.0, "end": 3.3, "char_range": [0, 6]},
                {"shot_id": "EP01-001", "start": 3.3, "end": 4.0, "char_range": [6, 9]},
                {"shot_id": "EP01-002", "start": 0.0, "end": 0.8, "char_range": [9, 13]},
                {"shot_id": "EP01-002", "start": 0.8, "end": 1.0, "char_range": [13, 15]},
            ],
        }],
        "events": [],
    }
    return candidate


class TimingIntegrationTests(unittest.TestCase):
    def test_candidate_rate_is_explicit_and_advisory_is_not_a_quality_issue(self) -> None:
        rules = json.loads(
            (SKILL_ROOT / "references" / "storyboard_runtime_rules.json").read_text(
                encoding="utf-8"
            )
        )
        policy = rules["timing_policy"]
        profile = next(
            row for row in policy["profiles"]
            if row["id"] == policy["default_profile_id"]
        )
        self.assertEqual(profile["units_per_second"], 5)
        self.assertEqual(profile["evidence_status"], "provisional")
        self.assertEqual(profile["rate_basis"], "utterance_average")
        self.assertIs(profile["includes_normal_pauses"], True)

        report = validate(strict_fixture())
        self.assertEqual(report["status"], "pass", report["issues"])
        self.assertEqual(report["issues"], [])
        self.assertEqual(report["warning_count"], 0)
        self.assertTrue(report["timing_advisories"], report)
        metadata = json.dumps(report["quality_metrics"]["timing"], ensure_ascii=False)
        self.assertIn(profile["id"], metadata)
        self.assertIn("provisional", metadata)

    def test_short_phase_fails_even_when_the_whole_shot_is_long_enough(self) -> None:
        base = strict_fixture()
        baseline = validate(base)
        self.assertEqual(baseline["status"], "pass", baseline["issues"])
        candidate = short_dialogue_window()
        report = validate(candidate)
        faults = timing_issues(report)
        self.assertTrue(faults, report)
        self.assertTrue(
            any(row.get("shot_ids") == ["EP05-001"] for row in faults), faults
        )
        self.assertTrue(all(row["severity"] == "fail" for row in faults), faults)
        self.assertTrue(all(row["template_gate_id"] == "THG-10" for row in faults))
        self.assertEqual(candidate["shots"][0]["时长"], 4.0)
        self.assertEqual(candidate["protected_dialogue"], base["protected_dialogue"])

    def test_scene_preflight_keeps_a_locally_decidable_timing_fault(self) -> None:
        candidate = short_dialogue_window()
        report, _ = service_module._scene_scoped_report(candidate, candidate["shots"])
        faults = timing_issues(report)
        self.assertTrue(faults, report)
        self.assertEqual(report["status"], "fail_current")
        self.assertTrue(
            any(
                row.get("shot_ids") == ["EP05-001"]
                and row.get("timing_scope_decidable") is True
                for row in faults
            ),
            faults,
        )

    def test_450_character_psychological_audio_does_not_fit_four_seconds(self) -> None:
        # No role dialogue: an overlap fault cannot substitute for the
        # psychological track's own 450-character capacity failure.
        candidate = valid_storyboard()
        spoken = "念" * 450
        candidate["shots"][0]["心理音轨"] = (
            f"心理旁白：“{spoken}”；后期声音；人物不张嘴、不驱动口型、"
            "不生成字幕、不计入角色对白。"
        )
        report = validate(candidate)
        faults = timing_issues(report)
        self.assertTrue(faults, report)
        self.assertTrue(
            any(row.get("shot_ids") == ["EP01-001"] for row in faults), faults
        )
        self.assertEqual(candidate["shots"][0]["时长"], 4.0)
        self.assertEqual(candidate["shots"][0]["心理音轨"].count(spoken), 1)

    def test_timing_analysis_does_not_expand_or_duplicate_the_canonical(self) -> None:
        candidate = strict_fixture()
        before = copy.deepcopy(candidate)
        report = validate(candidate)
        self.assertEqual(candidate, before)
        self.assertEqual(len(COMPACT_SHOT_FIELDS), 27)
        self.assertEqual(
            [key for key in candidate["shots"][0] if key in COMPACT_SHOT_FIELDS],
            list(COMPACT_SHOT_FIELDS),
        )
        row = project_export_row(candidate["shots"][0])
        self.assertEqual(list(row), list(COMPACT_MOTHER_COLUMNS))
        self.assertEqual(len(row), 10)
        spoken = candidate["protected_dialogue"][0]["text"]
        self.assertEqual(row["正式分镜母版"].count(spoken), 1)
        self.assertNotIn(spoken, row["GPT关键帧生成指令"])
        self.assertNotIn(
            spoken,
            json.dumps(report["quality_metrics"]["timing"], ensure_ascii=False),
        )

    def test_psychological_timing_metadata_does_not_copy_the_audio_text(self) -> None:
        candidate = valid_storyboard()
        spoken = "我得马上离开。"
        candidate["shots"][0]["心理音轨"] = (
            f"心理旁白：“{spoken}”；后期声音；人物不张嘴、不驱动口型、"
            "不生成字幕、不计入角色对白。"
        )
        before = copy.deepcopy(candidate)
        report = validate(candidate)
        self.assertEqual(report["status"], "pass", report["issues"])
        self.assertEqual(candidate, before)
        self.assertFalse(timing_issues(report), report)
        self.assertNotIn(
            spoken,
            json.dumps(report["quality_metrics"]["timing"], ensure_ascii=False),
        )
        row = project_export_row(candidate["shots"][0])
        self.assertEqual(row["正式分镜母版"].count(spoken), 1)
        self.assertNotIn(spoken, row["GPT关键帧生成指令"])

    def test_scene_preflight_retains_complete_cross_scene_audio_context(self) -> None:
        candidate = cross_scene_psychological_audio()
        full = validate(candidate)
        self.assertFalse(timing_issues(full), full)
        report, scoped = service_module._scene_scoped_report(
            candidate, [candidate["shots"][1]]
        )
        self.assertEqual(len(scoped["shots"]), 1)
        self.assertFalse(timing_issues(report), report)
        self.assertFalse(
            [row for row in report["not_applicable_findings"]
             if "timing" in row.get("location", "")],
            report,
        )
        spans = report["quality_metrics"]["timing"]["utterances"]
        self.assertEqual(len(spans), 4)
        self.assertEqual({row["utterance_id"] for row in spans}, {"PSY-CROSS-001"})
        self.assertEqual([row["char_range"] for row in spans], [[0, 6], [6, 9], [9, 13], [13, 15]])
        self.assertEqual(report["quality_metrics"]["timing"]["timeline_seconds"], 8.0)

    def test_scene_preflight_rejects_a_dangling_psychological_audio_reference(self) -> None:
        candidate = cross_scene_psychological_audio()
        candidate["shots"][1]["心理音轨"] = candidate["shots"][1]["心理音轨"].replace(
            "PSY-CROSS-001", "PSY-MISSING-001"
        )
        report, _ = service_module._scene_scoped_report(
            candidate, [candidate["shots"][1]]
        )
        faults = timing_issues(report)
        self.assertTrue(faults, report)
        self.assertTrue(
            any(row.get("shot_ids") == ["EP01-002"]
                and row.get("timing_scope_decidable") is True
                and "引用" in row.get("evidence", "") for row in faults),
            faults,
        )


if __name__ == "__main__":
    unittest.main()
