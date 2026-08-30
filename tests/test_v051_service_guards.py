from __future__ import annotations

import copy
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openpyxl import load_workbook

import shortdrama_mcp.file_io as file_io
import shortdrama_mcp.service as service_module
from shortdrama_mcp.app import create_mcp
from shortdrama_mcp.config import Settings
from shortdrama_mcp.file_io import storyboard_to_xlsx

from semantic_audit_helpers import semantic_audit_for_artifact
from test_efficiency import valid_storyboard


class V051ServiceGuardTests(unittest.TestCase):
    namespace = "local-single-user"

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="shortdrama-v051-service-")
        self.settings = Settings(
            data_dir=Path(self.temp.name),
            public_base_url="http://127.0.0.1:8765",
            host="127.0.0.1",
            port=8765,
            deployment_mode="local",
            auth_mode="none",
            oauth_issuer=None,
            oauth_audience=None,
            oauth_jwks_url=None,
            oauth_scopes=("storyboard:read", "storyboard:write"),
            allow_local_file_urls=False,
            max_file_bytes=50 * 1024 * 1024,
        )
        _server, self.service = create_mcp(self.settings)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _project(self, label: str) -> str:
        project_id = str(
            self.service.create_project(self.namespace, label).project_id
        )
        status = self.service.agent_status(
            self.namespace, project_id, full_self_test=True
        )
        self.assertTrue(status.data.get("runtime_test_certificate_valid"))
        return project_id

    def _begin_production(self, project_id: str, label: str) -> tuple[str, str]:
        session_id = f"production-{label}"
        response = self.service.begin_task(
            self.namespace,
            project_id=project_id,
            operation="storyboard_create",
            mode="execute",
            authorization="explicit_execute",
            request_text=f"v0.5.1 service guard: {label}",
            targets=["第一集"],
            authoritative_source="测试剧本",
            latest_version="v1",
            target_boundary="第一集",
            must_keep=["剧情、对白、连续性"],
            must_remove=[],
            protected_scope=[],
            expected_outputs=["正式分镜"],
            window_key=session_id,
            request_session_id=session_id,
        )
        return str(response.work_order_id), session_id

    def _begin_audit(
        self, project_id: str, artifact_id: str, label: str
    ) -> tuple[str, str]:
        session_id = f"audit-{label}"
        response = self.service.begin_task(
            self.namespace,
            project_id=project_id,
            operation="storyboard_audit",
            mode="audit_only",
            authorization="read_only",
            request_text=f"v0.5.1 independent audit: {label}",
            targets=[artifact_id],
            authoritative_source="已锁定分镜及其handoff",
            latest_version="current",
            target_boundary="第一集",
            must_keep=["只读独立审计"],
            must_remove=[],
            protected_scope=["全部源内容"],
            expected_outputs=["independent_audit_certificate"],
            window_key=session_id,
            request_session_id=session_id,
        )
        return str(response.work_order_id), session_id

    def test_unknown_certificate_cannot_authorize_an_audit_overlay(self) -> None:
        project_id = self._project("unknown-overlay-certificate")

        with self.assertRaisesRegex(
            ValueError,
            "independent_audit_overlay_certificate_unknown_or_tampered",
        ):
            self.service._load_verified_independent_audit_certificate(
                self.namespace,
                project_id,
                certificate_id="CERT-UNKNOWN",
                artifact_id="ART-UNKNOWN",
                canonical_sha256="a" * 64,
                source_artifact_raw_sha256="b" * 64,
            )

    def test_fake_hmac_on_persisted_certificate_cannot_authorize_overlay(self) -> None:
        project_id = self._project("tampered-overlay-certificate")
        certificate_id = "CERT-TAMPERED"
        certificate = self.service.store.save_project_record(
            self.namespace,
            project_id,
            kind="certificates",
            record_id=certificate_id,
            value={
                "certificate_id": certificate_id,
                "audit_class": "independent_audit",
            },
        )
        certificate["integrity_hmac"] = "0" * 64
        certificate_path = (
            self.service.store.project_dir(self.namespace, project_id)
            / "certificates"
            / f"{certificate_id}.json"
        )
        self.service.store.atomic_write_json(certificate_path, certificate)

        with self.assertRaisesRegex(
            ValueError,
            "independent_audit_overlay_certificate_unknown_or_tampered",
        ):
            self.service._load_verified_independent_audit_certificate(
                self.namespace,
                project_id,
                certificate_id=certificate_id,
                artifact_id="ART-UNKNOWN",
                canonical_sha256="a" * 64,
                source_artifact_raw_sha256="b" * 64,
            )

    def _commit(
        self,
        project_id: str,
        work_order_id: str,
        session_id: str,
        storyboard: dict,
        label: str,
    ):
        return self.service.commit_storyboard(
            self.namespace,
            project_id=project_id,
            work_order_id=work_order_id,
            storyboard=storyboard,
            file_name=f"{label}.xlsx",
            source_artifact_id=None,
            request_session_id=session_id,
        )

    def _assert_no_publication_side_effects(self, project_id: str) -> None:
        self.assertEqual(self.service.store.list_artifacts(self.namespace, project_id), [])
        self.assertEqual(
            self.service.store.list_project_records(
                self.namespace, project_id, kind="handoffs"
            ),
            [],
        )
        hub = self.service.store.load_hub(self.namespace, project_id)
        self.assertEqual(hub.get("deliverables", []), [])
        self.assertEqual(hub.get("handoffs", []), [])
        self.assertNotIn("storyboard", hub["project_state"]["latest_artifacts"])

    def test_preflight_guards_create_no_artifact_version_export_or_handoff(self) -> None:
        cases = (
            (
                "aspect",
                lambda shot: shot.__setitem__(
                    "生成限制", "16:9横屏；禁止字幕；禁止额外配乐"
                ),
                "aspect_ratio_contract_violation",
                None,
                0,
            ),
            (
                "bgm",
                lambda shot: shot.__setitem__(
                    "环境音效", str(shot["环境音效"]) + "；BGM渐入"
                ),
                None,
                "THG-AUDIO",
                0,
            ),
            (
                "dynamic-keyframe",
                lambda shot: shot.__setitem__(
                    "起始状态", str(shot["起始状态"]) + "；随后抬手"
                ),
                None,
                "THG-03",
                0,
            ),
            (
                "duplicate-transition",
                lambda shot: shot.__setitem__(
                    "切镜/转场提示词", "手停时切；；手停时切"
                ),
                "transition_contract_violation",
                None,
                0,
            ),
        )
        for label, mutate, expected_error, expected_gate, projector_calls in cases:
            with self.subTest(label=label):
                project_id = self._project(f"preflight-{label}")
                work_order_id, session_id = self._begin_production(
                    project_id, label
                )
                storyboard = valid_storyboard()
                mutate(storyboard["shots"][0])
                with patch.object(
                    service_module,
                    "_export_compact_storyboard",
                    wraps=service_module._export_compact_storyboard,
                ) as projector:
                    if expected_error:
                        with self.assertRaisesRegex(ValueError, expected_error):
                            self._commit(
                                project_id,
                                work_order_id,
                                session_id,
                                storyboard,
                                label,
                            )
                    else:
                        response = self._commit(
                            project_id,
                            work_order_id,
                            session_id,
                            storyboard,
                            label,
                        )
                        self.assertEqual(response.status, "fail_current")
                        self.assertFalse(response.data["artifact_created"])
                        self.assertFalse(response.data["handoff_created"])
                        gate_ids = {
                            issue.get("template_gate_id")
                            for issue in response.data["report"]["issues"]
                        }
                        self.assertIn(expected_gate, gate_ids)
                self.assertEqual(projector.call_count, projector_calls)
                self._assert_no_publication_side_effects(project_id)

                # A rejected attempt cannot consume a storyboard version.
                retry_id, retry_session = self._begin_production(
                    project_id, f"{label}-retry"
                )
                committed = self._commit(
                    project_id,
                    retry_id,
                    retry_session,
                    valid_storyboard(),
                    f"{label}-retry",
                )
                self.assertEqual(committed.status, "production_preflight_pass")
                self.assertEqual(committed.data["artifact"]["version"], "v1")

    def test_projector_exception_leaves_no_orphan_artifact(self) -> None:
        project_id = self._project("projector-transaction")
        work_order_id, session_id = self._begin_production(
            project_id, "projector-transaction"
        )
        with patch.object(
            service_module,
            "_export_compact_storyboard",
            side_effect=RuntimeError("projector exploded"),
        ):
            with self.assertRaisesRegex(RuntimeError, "projector exploded"):
                self._commit(
                    project_id,
                    work_order_id,
                    session_id,
                    valid_storyboard(),
                    "projector-transaction",
                )
        self._assert_no_publication_side_effects(project_id)

        committed = self._commit(
            project_id,
            work_order_id,
            session_id,
            valid_storyboard(),
            "projector-retry",
        )
        self.assertEqual(committed.status, "production_preflight_pass")
        self.assertEqual(committed.data["artifact"]["version"], "v1")

    def test_new_fail_after_pass_revokes_latest_and_writes_fail_overlay(self) -> None:
        project_id = self._project("pass-then-fail")
        production_id, production_session = self._begin_production(
            project_id, "pass-then-fail"
        )
        committed = self._commit(
            project_id,
            production_id,
            production_session,
            valid_storyboard(),
            "pass-then-fail",
        )
        artifact_id = str(committed.artifact_id)
        handoff_id = str(committed.data["handoff"]["handoff_id"])
        audit_id, audit_session = self._begin_audit(
            project_id, artifact_id, "pass-then-fail"
        )
        clean_semantic = semantic_audit_for_artifact(
            self.service, self.settings, project_id, artifact_id
        )
        first = self.service.audit_storyboard(
            self.namespace,
            project_id=project_id,
            artifact_id=artifact_id,
            audit_cycle_id=None,
            work_order_id=audit_id,
            handoff_artifact_id=handoff_id,
            semantic_audit=clean_semantic,
            request_session_id=audit_session,
        )
        self.assertEqual(first.status, "pass")
        pass_overlay_id = first.data["audited_workbook"]["artifact_id"]
        hub = self.service.store.load_hub(self.namespace, project_id)
        self.assertEqual(
            hub["project_state"]["latest_artifacts"]["storyboard"][
                "deliverable_id"
            ],
            pass_overlay_id,
        )

        failed_semantic = copy.deepcopy(clean_semantic)
        failed_layer = failed_semantic["layers"][0]
        failed_layer["status"] = "fail_current"
        failed_layer["summary"] = "独立复核发现原文语义错位。"
        evidence_id = failed_layer["evidence_ids"][0]
        shot_id = str(valid_storyboard()["shots"][0]["镜头编号"])
        failed_semantic["issues"] = [
            {
                "issue_id": "SEM-NEW-FAIL-001",
                "layer": failed_layer["layer"],
                "error_code": "E-SOURCE-SELECT-001",
                "severity": "blocking",
                "blocking": True,
                "shot_ids": [shot_id],
                "batch_ids": [],
                "field": "原文支撑句",
                "evidence_ids": [evidence_id],
                "evidence": "原文与镜头语义不一致。",
                "repair_action": "重建该镜语义绑定后重新独立审计。",
            }
        ]
        failed_semantic["conclusion"] = {
            "status": "fail_current",
            "summary": "独立复核发现阻塞问题。",
        }

        # A semantic-audit contract change invalidates the old cache while the
        # producer bytes remain identical, exercising authority revocation.
        with patch.object(
            service_module,
            "SEMANTIC_AUDIT_CONTRACT_FINGERPRINT",
            "f" * 64,
        ):
            second = self.service.audit_storyboard(
                self.namespace,
                project_id=project_id,
                artifact_id=artifact_id,
                audit_cycle_id=None,
                work_order_id=audit_id,
                handoff_artifact_id=handoff_id,
                force_machine_recheck=True,
                semantic_audit=failed_semantic,
                request_session_id=audit_session,
            )

        self.assertEqual(second.status, "fail_current")
        self.assertEqual(second.data["audited_workbook"]["status"], "FAIL_CURRENT")
        fail_overlay_id = second.data["audited_workbook"]["artifact_id"]
        self.assertNotEqual(fail_overlay_id, pass_overlay_id)
        fail_record, _path = self.service.store.get_artifact(
            self.namespace, project_id, fail_overlay_id
        )
        self.assertEqual(fail_record["artifact_type"], "storyboard_audit_overlay")
        self.assertEqual(
            fail_record["metadata"]["independent_audit_status"], "FAIL_CURRENT"
        )

        hub = self.service.store.load_hub(self.namespace, project_id)
        self.assertNotIn("storyboard", hub["project_state"]["latest_artifacts"])
        producer = next(
            row
            for row in hub["work_orders"]
            if row["work_order_id"] == production_id
        )
        self.assertEqual(producer["status"], "failed_current")
        self.assertTrue(producer["awaiting_independent_audit"])
        self.assertNotIn("independent_audit_certificate_id", producer)
        self.assertNotIn("verified_independent_audit_certificate_id", producer)
        audit_order = next(
            row
            for row in hub["work_orders"]
            if row["work_order_id"] == audit_id
        )
        self.assertEqual(audit_order["status"], "failed_current")
        self.assertEqual(
            audit_order["reopened_by_independent_audit"],
            second.data["certificate"]["certificate_id"],
        )
        persisted_handoff = self.service.store.get_project_record(
            self.namespace,
            project_id,
            kind="handoffs",
            record_id=handoff_id,
        )
        self.assertEqual(persisted_handoff["reaudit_count"], 1)
        self.assertEqual(
            persisted_handoff["audit_certificate_ids"],
            [
                first.data["certificate"]["certificate_id"],
                second.data["certificate"]["certificate_id"],
            ],
        )
        ledger = self.service.store.load_ledger(self.namespace, project_id)
        self.assertEqual(ledger["human_signoffs"], [])
        self.assertEqual(len(ledger["authority_reopens"]), 1)
        self.assertEqual(
            ledger["authority_reopens"][0]["authority"],
            "independent_audit",
        )
        source_record, _path = self.service.store.get_artifact(
            self.namespace, project_id, artifact_id
        )
        self.assertEqual(source_record["metadata"]["audit_status"], "FAIL_CURRENT")

    def test_xlsx_without_canonical_sidecar_cannot_be_independently_audited(self) -> None:
        project_id = self._project("no-sidecar")
        payload = storyboard_to_xlsx(
            valid_storyboard(), {"status": "pass", "issues": []}, assume_compact=True
        )
        workbook = load_workbook(io.BytesIO(payload), data_only=False)
        self.assertIn(file_io.CANONICAL_SIDECAR_SHEET, workbook.sheetnames)
        workbook.remove(workbook[file_io.CANONICAL_SIDECAR_SHEET])
        output = io.BytesIO()
        workbook.save(output)
        workbook.close()
        record = self.service.store.save_artifact_bytes(
            self.namespace,
            project_id,
            output.getvalue(),
            file_name="legacy-no-sidecar.xlsx",
            artifact_type="storyboard",
            version="v1",
            metadata={
                "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            },
        )
        audit_id, audit_session = self._begin_audit(
            project_id, str(record["artifact_id"]), "no-sidecar"
        )
        artifact_count = len(
            self.service.store.list_artifacts(self.namespace, project_id)
        )
        certificates_before = self.service.store.list_project_records(
            self.namespace, project_id, kind="certificates"
        )

        with self.assertRaisesRegex(
            ValueError, "canonical_projection_migration_required"
        ):
            self.service.audit_storyboard(
                self.namespace,
                project_id=project_id,
                artifact_id=str(record["artifact_id"]),
                audit_cycle_id=None,
                work_order_id=audit_id,
                semantic_audit=None,
                request_session_id=audit_session,
            )

        self.assertEqual(
            len(self.service.store.list_artifacts(self.namespace, project_id)),
            artifact_count,
        )
        self.assertEqual(
            self.service.store.list_project_records(
                self.namespace, project_id, kind="handoffs"
            ),
            [],
        )
        self.assertEqual(
            self.service.store.list_project_records(
                self.namespace, project_id, kind="certificates"
            ),
            certificates_before,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
