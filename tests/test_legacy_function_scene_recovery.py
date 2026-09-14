from __future__ import annotations

import io
import re
import unittest

from openpyxl import load_workbook

from shortdrama_mcp.file_io import (
    CANONICAL_SIDECAR_SHEET,
    _parse_function_scene_cell,
    extract_xlsx,
    storyboard_to_xlsx,
)
from shortdrama_mcp.materializer import COMPACT_SHOT_FIELDS
from test_v050_compact_master_io import compact_storyboard


class LegacyFunctionSceneRecoveryTests(unittest.TestCase):
    def test_labeled_format_keeps_existing_meaning(self) -> None:
        cases = (
            ("功能类型：接问／转折｜场景连续性ID：SCENE-N3-HALL", "接问／转折", "SCENE-N3-HALL"),
            ("功能类型：接问 / 回答\n场景连续性ID：SCENE-N3-HALL", "接问 / 回答", "SCENE-N3-HALL"),
            ("功能类型：接问 / SCENE-N3-HALL", "接问 / SCENE-N3-HALL", ""),
            ("功能类型：接问｜场景连续性ID：", "接问", ""),
        )
        for value, function, scene in cases:
            with self.subTest(value=value):
                self.assertEqual(_parse_function_scene_cell(value), (function, scene))

    def test_unlabeled_legacy_cell_recovers_explicit_identifier(self) -> None:
        cases = (
            ("接上集／问清打算 / SHY-N3-HALL", "接上集／问清打算", "SHY-N3-HALL"),
            ("  决定去留  /  SGY-D4-CHILD-DOOR  ", "决定去留", "SGY-D4-CHILD-DOOR"),
            ("问答\t/\tscene_01", "问答", "scene_01"),
        )
        for value, function, scene in cases:
            with self.subTest(value=value):
                self.assertEqual(_parse_function_scene_cell(value), (function, scene))

    def test_missing_or_ambiguous_scene_is_not_guessed(self) -> None:
        cases = (
            "接上集／问清打算",
            "问答 / 正厅",
            "问答 / scene",
            "问答 / SCENE-01 / SCENE-02",
            "问答 / SCENE-01另一个场景",
            "问答 / SCENE-01\nSCENE-02",
            "问答 / dir/SCENE-01",
            "问答/SCENE-01",
            "问答／SCENE-01",
            " / SCENE-01",
        )
        for value in cases:
            with self.subTest(value=value):
                self.assertEqual(_parse_function_scene_cell(value), (value.strip(), ""))

    def test_workbook_recovers_all_atoms_without_claiming_a_sidecar(self) -> None:
        source = compact_storyboard()
        shot = source["shots"][0]
        shot["功能类型"] = "接上集／问清打算"
        shot["场景连续性ID"] = "SHY-N3-HALL"
        shot["生成限制"] = "9:16竖屏；禁止字幕、水印；禁止额外配乐"
        # Preserve literal punctuation and multiple utterance/voice lines.
        shot["画面内容"] += "\n【邹氏】你说。\n【苏清柳】是。"
        shot["音色"] += "\n【邹氏】语气平稳。\n【苏清柳】语气克制。"
        payload = storyboard_to_xlsx(source, {"issues": []})
        book = load_workbook(io.BytesIO(payload))
        del book[CANONICAL_SIDECAR_SHEET]
        # Pin the actual legacy timed field instead of deriving old input from
        # today's untimed export. Recovery must still preserve every old atom.
        formal = book["正式分镜母版"]["E2"].value
        match = re.search(r"(?ms)(^画面内容：)(.*?)(?=^音色：)", formal)
        self.assertIsNotNone(match)
        book["正式分镜母版"]["E2"] = (
            formal[:match.start(2)] + shot["画面内容"] + "\n\n" + formal[match.end(2):]
        )
        book["正式分镜母版"]["D2"] = "接上集／问清打算 / SHY-N3-HALL"
        output = io.BytesIO()
        book.save(output)
        legacy_payload = output.getvalue()

        result = extract_xlsx(legacy_payload)

        self.assertFalse(result["canonical_sidecar_present"])
        recovered = result["canonical_storyboard"]["shots"]
        self.assertEqual(len(recovered), 1)
        self.assertEqual(
            {field: recovered[0][field] for field in COMPACT_SHOT_FIELDS},
            {field: shot[field] for field in COMPACT_SHOT_FIELDS},
        )
        self.assertNotIn("audit_certificate_id", result)
        self.assertNotIn("handoff_id", result)


if __name__ == "__main__":
    unittest.main()
