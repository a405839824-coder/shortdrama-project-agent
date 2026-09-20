from __future__ import annotations

import copy
from pathlib import Path
from typing import Any
from unittest.mock import patch

import shortdrama_mcp.service as service_module

from semantic_audit_helpers import semantic_audit_for_artifact
from test_efficiency import valid_storyboard
import test_v057_preflight_evidence_reissue as v057


class _V057FixtureOnly(v057.V057PreflightEvidenceReissueTests):
    """Reuse the signed production fixture without rerunning v0.5.7 tests."""


for _test_name in dir(v057.V057PreflightEvidenceReissueTests):
    if _test_name.startswith("test_"):
        setattr(_V057FixtureOnly, _test_name, None)


class V0511HistoricalPreflightAttestationTests(_V057FixtureOnly):
    historical_gap_id = service_module.CONTROLLED_V053_PREFLIGHT_GAP_ID
    recovery_gap_id = service_module.CONTROLLED_HANDOFF_RECOVERY_GAP_ID
    historical_ids = list(
        service_module.CONTROLLED_V053_PREFLIGHT_HANDOFF_IDS
    )
    all_recovery_ids = list(
        service_module.CONTROLLED_HANDOFF_RECOVERY_IDS
    )
    c086_ruleset = (
        "V16.3-148-R10-StableRuntime:R10-PG1:c0867f81158ffca0"
    )
    c086_fingerprint = (
        "c0867f81158ffca02cd5f2b3d53e527e0dcddf5a7ad02341758a54954636f5b9"
    )
    d884_ruleset = (
        "V16.3-148-R10-StableRuntime:R10-PG1:d8841a2f52d86bed"
    )
    d884_fingerprint = (
        "d8841a2f52d86bed8554b21c3543e85f753b4b6f01a1b0a94964a9da4bcf90dc"
    )

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.generic_current_handoff_id = self.old_handoff_id
        self.generic_current_backfill_work_order_id = (
            self.backfill_work_order_id
        )
        self.current_source_handoff_id = self.clean_peer_handoff_id
        self.production_session_id = "v0511-controlled-production"

        # This is a genuine contract-drift example: v0.5.3 accepts the
        # storyboard, while the current validator requires the newly added
        # exact ``9:16竖屏`` phrase and therefore fails it.
        self.historical_storyboard = valid_storyboard()
        self.historical_storyboard["shots"][0]["生成限制"] = (
            self.historical_storyboard["shots"][0]["生成限制"].replace(
                "9:16竖屏；", ""
            )
        )
        self.historical_reports = {
            ruleset: self.service._frozen_v053_production_validation(
                self.historical_storyboard,
                ruleset_version=ruleset,
            )[0]
            for ruleset in (self.c086_ruleset, self.d884_ruleset)
        }
        historical_report = self.historical_reports[self.c086_ruleset]
        self.assertEqual(
            historical_report,
            self.historical_reports[self.d884_ruleset],
        )
        current_report = service_module.validate_storyboard.validate_project(
            self.historical_storyboard, mode="audit"
        )
        self.assertEqual(historical_report["status"], "pass")
        self.assertEqual(historical_report["issues"], [])
        self.assertEqual(current_report["status"], "fail")
        self.assertEqual(len(current_report["issues"]), 1)
        self.assertEqual(
            current_report["issues"][0]["error_code"], "E-I2V-INDEP-002"
        )
        self.historical_report = historical_report
        self.current_historical_report = current_report

        # Production carries six immutable, real-record seals.  Synthetic
        # fixtures must establish their own equally exact seals instead of
        # weakening or reusing those production constants.
        synthetic_canonical_sha256 = (
            service_module.canonical_storyboard_sha256(
                self.historical_storyboard
            )
        )
        synthetic_report_sha256 = service_module.sha256_bytes(
            service_module._stable_json_bytes(current_report)
        )
        synthetic_histogram = self.service._validation_error_histogram(
            current_report
        )
        synthetic_profiles = {
            handoff_id: {
                "handoff_id": handoff_id,
                "canonical_sha256": synthetic_canonical_sha256,
                "current_report_sha256": synthetic_report_sha256,
                "shot_count": len(self.historical_storyboard["shots"]),
                "error_count": len(current_report["issues"]),
                "warning_count": 0,
                "error_histogram": copy.deepcopy(synthetic_histogram),
                "current_validator_composite": (
                    self.service._validator_version()
                ),
                "current_validator_fingerprint": (
                    self.service._validator_fingerprint
                ),
            }
            for handoff_id in self.historical_ids
        }
        self.synthetic_current_validation_profiles = synthetic_profiles
        self._synthetic_profiles_patch = patch.object(
            service_module,
            "CONTROLLED_V053_CURRENT_VALIDATION_PROFILES",
            synthetic_profiles,
        )
        self._synthetic_profiles_patch.start()
        self.addCleanup(self._synthetic_profiles_patch.stop)

        # Produce a synthetically old authority chain without opening a second
        # production work order (the fixture correctly has a pending handoff,
        # so the public production gate must remain closed).  Its workbook is
        # then projected to the exact v0.5.3 transitional envelope by the
        # established helper.
        historical_source_id = self._create_historical_source_handoff()
        self.old_handoff_id = historical_source_id
        self._remove_handoff_preflight_summary(historical_source_id)
        self.historical_fixture = (
            self._install_v053_transitional_workbook_fixture()
        )
        self._remove_historical_artifact_current_claims()

        self._install_exact_controlled_handoffs()
        self._record_exact_gaps()
        self.historical_work_order_id = self._exact_work_order(
            gap_id=self.historical_gap_id,
            handoff_ids=self.historical_ids,
            phase="preflight_evidence_backfill",
        )
        self.exact_recovery_work_order_id = self._exact_work_order(
            gap_id=self.recovery_gap_id,
            handoff_ids=self.all_recovery_ids,
            phase="handoff_recovery",
        )

    @staticmethod
    def _record_value(record: dict[str, Any]) -> dict[str, Any]:
        return {
            key: copy.deepcopy(value)
            for key, value in record.items()
            if key not in {"record_id", "project_id", "integrity_hmac"}
        }

    def _create_historical_source_handoff(self) -> str:
        source = self.service.store.get_project_record(
            self.namespace,
            self.project_id,
            kind="handoffs",
            record_id=self.current_source_handoff_id,
        )
        hub = self.service.store.load_hub(self.namespace, self.project_id)
        production_work_order = next(
            row
            for row in hub["work_orders"]
            if row["work_order_id"] == source["work_order_id"]
        )
        storyboard = copy.deepcopy(self.historical_storyboard)
        canonical_hash = service_module.canonical_storyboard_sha256(
            storyboard
        )
        source_fingerprint = self.service._source_fingerprint(
            self.namespace, self.project_id, storyboard, None
        )
        boundary_hash = self.service._boundary_hash(storyboard)
        canonical_record = self.service.store.save_artifact_json(
            self.namespace,
            self.project_id,
            storyboard,
            file_name="v0511-historical-fixture.json",
            artifact_type="storyboard_json",
            version="v0.5.3",
            work_order_id=str(production_work_order["work_order_id"]),
            metadata={"canonical_sha256": canonical_hash},
        )
        artifact_record = self.service.store.save_artifact_bytes(
            self.namespace,
            self.project_id,
            service_module.storyboard_to_xlsx(
                storyboard,
                self.historical_report,
                assume_compact=True,
            ),
            file_name="v0511-historical-fixture.xlsx",
            artifact_type="storyboard",
            version="v0.5.3",
            work_order_id=str(production_work_order["work_order_id"]),
            metadata={
                "canonical_json_artifact_id": canonical_record[
                    "artifact_id"
                ],
                "canonical_json_fingerprint": canonical_record[
                    "fingerprint"
                ],
                "canonical_sha256": canonical_hash,
                "source_fingerprint": source_fingerprint,
                "boundary_hash": boundary_hash,
                "production_preflight_status": (
                    service_module.PRODUCTION_PREFLIGHT_PASS
                ),
                "production_preflight_issue_count": 0,
                "independent_audit_status": "PENDING_AUDIT",
            },
        )
        cache_components = self.service._audit_cache_components(
            canonical_hash=canonical_hash,
            source_fingerprint=source_fingerprint,
            boundary_hash=boundary_hash,
            audit_class=service_module.AUDIT_CLASS_PRODUCTION_PREFLIGHT,
            artifact_fingerprint=str(artifact_record["fingerprint"]),
            storyboard=storyboard,
            scope={
                "targets": production_work_order.get("targets", []),
                "protected_scope": production_work_order.get(
                    "protected_scope", []
                ),
            },
            runtime_test_certificate_id=str(
                self.runtime_certificate["certificate_id"]
            ),
        )
        certificate = self.service._persist_audit_certificate(
            self.namespace,
            self.project_id,
            artifact_id=str(artifact_record["artifact_id"]),
            canonical_artifact_id=str(canonical_record["artifact_id"]),
            cache_components=cache_components,
            report=self.historical_report,
            work_order_id=str(production_work_order["work_order_id"]),
        )
        trace_id = "TRC-v0511-historical-source"
        fingerprints = self.service._fingerprint_bundle(
            storyboard,
            artifact_record,
            canonical_hash=canonical_hash,
            source_fingerprint=source_fingerprint,
            boundary_hash=boundary_hash,
        )
        self.service._save_telemetry(
            self.namespace,
            self.project_id,
            operation="commit_storyboard",
            started_ns=service_module.time.perf_counter_ns(),
            stages_ms={"validation": 0.0},
            artifact_id=str(artifact_record["artifact_id"]),
            telemetry_id=trace_id,
            window_role="production",
            task_type="production_preflight",
            input_fingerprints=fingerprints,
            shot_count=len(storyboard.get("shots", [])),
            batch_count=0,
            input_payload_bytes=int(artifact_record["size_bytes"]),
            output_payload_bytes=int(artifact_record["size_bytes"]),
            materialization_passes=1,
        )
        handoff = self.service._persist_production_handoff(
            self.namespace,
            self.project_id,
            work_order_id=str(production_work_order["work_order_id"]),
            storyboard_artifact_id=str(artifact_record["artifact_id"]),
            canonical_artifact_id=str(canonical_record["artifact_id"]),
            certificate=certificate,
            storyboard=storyboard,
            artifact_record=artifact_record,
            work_order=production_work_order,
            stage_trace_id=trace_id,
        )
        with self.service.store.mutate_project(
            self.namespace, self.project_id
        ) as (live_hub, _ledger):
            live_hub["handoffs"].append(
                self.service._handoff_record_projection(handoff)
            )
        with self.service.store.mutate_project_record(
            self.namespace,
            self.project_id,
            kind="handoffs",
            record_id=str(handoff["handoff_id"]),
        ) as stale:
            stale["fingerprints"]["runtime"] = "0" * 64
        with self.service.store.mutate_project_record(
            self.namespace,
            self.project_id,
            kind="telemetry",
            record_id=trace_id,
        ) as stale_trace:
            stale_trace["input_fingerprints"]["runtime"] = "0" * 64
        rebound = self.service.store.get_project_record(
            self.namespace,
            self.project_id,
            kind="handoffs",
            record_id=str(handoff["handoff_id"]),
        )
        self._replace_hub_handoff_rows(
            str(handoff["handoff_id"]),
            [self.service._handoff_record_projection(rebound)],
        )
        return str(handoff["handoff_id"])

    def _remove_historical_artifact_current_claims(self) -> None:
        handoff = self.service.store.get_project_record(
            self.namespace,
            self.project_id,
            kind="handoffs",
            record_id=self.old_handoff_id,
        )
        artifact_id = str(handoff["storyboard_artifact_id"])
        meta_path = (
            self.service.store.project_dir(self.namespace, self.project_id)
            / "artifacts"
            / f"{artifact_id}.meta.json"
        )
        metadata = self.service.store.read_json(meta_path)
        artifact_metadata = metadata.get("metadata")
        self.assertIsInstance(artifact_metadata, dict)
        for field in (
            "production_preflight_status",
            "production_preflight_issue_count",
            "independent_audit_status",
        ):
            artifact_metadata.pop(field, None)
        self.service.store.atomic_write_json(meta_path, metadata)

    def _clone_signed_handoff(
        self,
        *,
        source_handoff_id: str,
        target_handoff_id: str,
        historical_profile: tuple[str, str] | None,
    ) -> None:
        source = self.service.store.get_project_record(
            self.namespace,
            self.project_id,
            kind="handoffs",
            record_id=source_handoff_id,
        )
        source_certificate = self.service.store.get_project_record(
            self.namespace,
            self.project_id,
            kind="certificates",
            record_id=str(source["certificate_id"]),
        )
        source_trace = self.service.store.get_project_record(
            self.namespace,
            self.project_id,
            kind="telemetry",
            record_id=str(source["stage_trace_id"]),
        )
        suffix = target_handoff_id.removeprefix("HND-")
        certificate_id = f"CERT-v0511-origin-{suffix}"
        trace_id = f"TRC-v0511-origin-{suffix}"
        certificate = self._record_value(source_certificate)
        trace = self._record_value(source_trace)
        handoff = self.service._handoff_record_projection(source)
        handoff["handoff_id"] = target_handoff_id
        handoff["certificate_id"] = certificate_id
        handoff["stage_trace_id"] = trace_id
        certificate["certificate_id"] = certificate_id
        trace["trace_id"] = trace_id
        trace["telemetry_id"] = trace_id

        if historical_profile is not None:
            ruleset_version, rules_fingerprint = historical_profile
            (
                legacy_runtime_certificate_id,
                legacy_runtime_fingerprint,
                legacy_scripts_fingerprint,
                legacy_dependencies_fingerprint,
            ) = self._legacy_runtime_certificate(
                suffix=suffix,
                ruleset_version=ruleset_version,
            )
            fingerprints = copy.deepcopy(handoff["fingerprints"])
            fingerprints["validator"] = (
                service_module.FROZEN_V053_VALIDATOR_SHA256
            )
            fingerprints["rules"] = rules_fingerprint
            fingerprints["runtime"] = legacy_runtime_fingerprint
            fingerprints["scripts"] = legacy_scripts_fingerprint
            fingerprints["dependencies_environment"] = (
                legacy_dependencies_fingerprint
            )
            cache_components = copy.deepcopy(
                certificate["cache_components"]
            )
            cache_components["ruleset"] = ruleset_version
            cache_components["validator"] = (
                service_module.FROZEN_V053_VALIDATOR_COMPOSITE
            )
            cache_components["audit_class"] = (
                service_module.AUDIT_CLASS_PRODUCTION_PREFLIGHT
            )
            cache_components["runtime"] = legacy_runtime_fingerprint
            cache_components["scripts"] = legacy_scripts_fingerprint
            cache_components["dependencies"] = (
                legacy_dependencies_fingerprint
            )
            cache_components["runtime_test_certificate_id"] = (
                legacy_runtime_certificate_id
            )
            cache_key = self.service._cache_key_from_components(
                cache_components
            )
            certificate.update(
                {
                    "ruleset_version": ruleset_version,
                    "validator_version": (
                        service_module.FROZEN_V053_VALIDATOR_COMPOSITE
                    ),
                    "audit_class": (
                        service_module.AUDIT_CLASS_PRODUCTION_PREFLIGHT
                    ),
                    "cache_components": cache_components,
                    "cache_key": cache_key,
                    "report": copy.deepcopy(
                        self.historical_reports[ruleset_version]
                    ),
                    "report_sha256": service_module.sha256_bytes(
                        service_module._stable_json_bytes(
                            self.historical_reports[ruleset_version]
                        )
                    ),
                    "preflight_status": (
                        service_module.PRODUCTION_PREFLIGHT_PASS
                    ),
                    "preflight_issue_count": 0,
                    "independent_audit_status": "PENDING_AUDIT",
                    "result": "pass",
                    "issue_count": 0,
                    "checked_shot_count": len(
                        self.historical_storyboard["shots"]
                    ),
                }
            )
            # The legacy conclusion vocabulary used result/issue_count; it did
            # not carry today's production_preflight_* conclusion aliases.
            for field in (
                "preflight_status",
                "preflight_issue_count",
                "independent_audit_status",
            ):
                certificate.pop(field, None)
            handoff.update(
                {
                    "fingerprints": fingerprints,
                    "ruleset_version": ruleset_version,
                    "validator_version": (
                        service_module.FROZEN_V053_VALIDATOR_COMPOSITE
                    ),
                    "audit_class": (
                        service_module.AUDIT_CLASS_PRODUCTION_PREFLIGHT
                    ),
                    "cache_components": copy.deepcopy(cache_components),
                    "cache_key": cache_key,
                    "production_result": "pass",
                    "issue_count": 0,
                    "checked_shot_count": len(
                        self.historical_storyboard["shots"]
                    ),
                }
            )
            for field in (
                "production_preflight_status",
                "production_preflight_issue_count",
                "independent_audit_status",
            ):
                handoff.pop(field, None)
            for entry in handoff["cache_manifest"]:
                entry["cache_key"] = cache_key
                entry["dependency_fingerprints"] = copy.deepcopy(
                    fingerprints
                )
            trace["input_fingerprints"] = copy.deepcopy(fingerprints)

        self.service.store.save_project_record(
            self.namespace,
            self.project_id,
            kind="certificates",
            record_id=certificate_id,
            value=certificate,
        )
        self.service.store.save_project_record(
            self.namespace,
            self.project_id,
            kind="telemetry",
            record_id=trace_id,
            value=trace,
        )
        persisted = self.service.store.save_project_record(
            self.namespace,
            self.project_id,
            kind="handoffs",
            record_id=target_handoff_id,
            value=handoff,
        )
        with self.service.store.mutate_project(
            self.namespace, self.project_id
        ) as (hub, _ledger):
            hub["handoffs"].append(
                self.service._handoff_record_projection(persisted)
            )

    def _legacy_runtime_certificate(
        self,
        *,
        suffix: str,
        ruleset_version: str,
    ) -> tuple[str, str, str, str]:
        certificate_id = f"CERT-v0511-runtime-{suffix}"
        scripts_fingerprint = service_module.sha256_bytes(
            f"v053-scripts:{ruleset_version}".encode("utf-8")
        )
        dependencies_fingerprint = service_module.sha256_bytes(
            f"v053-dependencies:{ruleset_version}".encode("utf-8")
        )
        scripts_digest = scripts_fingerprint[:16]
        dependency_digest = dependencies_fingerprint[:16]
        service_code_fingerprint = service_module.sha256_bytes(
            f"v053-service:{ruleset_version}".encode("utf-8")
        )
        value: dict[str, Any] = {
            "certificate_id": certificate_id,
            "certificate_type": "runtime_full_regression",
            "result": "pass",
            "runtime_version": "V16.3-148-R10-StableRuntime",
            "workflow_revision": "R10-PG1",
            "ruleset_version": ruleset_version,
            "validator_version": (
                service_module.FROZEN_V053_VALIDATOR_COMPOSITE
            ),
            "scripts_digest": scripts_digest,
            "service_code_fingerprint": service_code_fingerprint,
            "dependency_digest": dependency_digest,
            "deployment_mode": "local",
            "output_contract": "R10-SPC-1",
            "tests": {
                name: {
                    "status": "pass",
                    "test_count": count,
                    "test_pass_count": count,
                    "test_fail_count": 0,
                }
                for name, count in {
                    "golden_tests": 73,
                    "strict_prompt_tests": 10,
                    "public_scene_tests": 11,
                    "agent_tests": 16,
                    "project_agent_tests": 21,
                }.items()
            },
        }
        value["certificate_key"] = "RUNTIMECERT-" + (
            service_module.sha256_bytes(
                service_module._stable_json_bytes(
                    {
                        "runtime": value["runtime_version"],
                        "workflow": value["workflow_revision"],
                        "ruleset": value["ruleset_version"],
                        "validator": value["validator_version"],
                        "scripts": value["scripts_digest"],
                        "service_code": value[
                            "service_code_fingerprint"
                        ],
                        "dependencies": value["dependency_digest"],
                        "deployment_mode": value["deployment_mode"],
                        "output_contract": value["output_contract"],
                    }
                )
            )
        )
        runtime_fingerprint = service_module.sha256_bytes(
            service_module._stable_json_bytes(
                {
                    "runtime": value["runtime_version"],
                    "workflow": value["workflow_revision"],
                    "service_code": value["service_code_fingerprint"],
                }
            )
        )
        self.service.store.save_project_record(
            self.namespace,
            self.project_id,
            kind="certificates",
            record_id=certificate_id,
            value=value,
        )
        return (
            certificate_id,
            runtime_fingerprint,
            scripts_fingerprint,
            dependencies_fingerprint,
        )

    def _install_exact_controlled_handoffs(self) -> None:
        for handoff_id in self.all_recovery_ids[:5]:
            self._clone_signed_handoff(
                source_handoff_id=self.current_source_handoff_id,
                target_handoff_id=handoff_id,
                historical_profile=None,
            )
        for index, handoff_id in enumerate(self.historical_ids):
            profile = (
                (self.c086_ruleset, self.c086_fingerprint)
                if index < 2
                else (self.d884_ruleset, self.d884_fingerprint)
            )
            self._clone_signed_handoff(
                source_handoff_id=self.old_handoff_id,
                target_handoff_id=handoff_id,
                historical_profile=profile,
            )

    def _record_exact_gaps(self) -> None:
        with self.service.store.mutate_project(
            self.namespace, self.project_id
        ) as (hub, _ledger):
            for gap_id, dependency in (
                (
                    self.historical_gap_id,
                    "controlled historical production preflight attestation",
                ),
                (
                    self.recovery_gap_id,
                    "controlled storyboard handoff recovery",
                ),
            ):
                service_module.project_agent.record_capability_gap(
                    hub,
                    {
                        "gap_id": gap_id,
                        "timestamp": service_module.utc_stamp(),
                        "request": dependency,
                        "missing_dependency": dependency,
                        "risk": "legacy authority cannot be consumed safely",
                        "safe_fallback": "keep all old records unconsumed",
                        "proposed_implementation": "signed append-only replay",
                        "required_tests": [
                            "zero-write plan",
                            "exact scope",
                            "independent audit separation",
                        ],
                    },
                )

    def _exact_work_order(
        self,
        *,
        gap_id: str,
        handoff_ids: list[str],
        phase: str,
    ) -> str:
        response = self.service.begin_task(
            self.namespace,
            project_id=self.project_id,
            operation="rule_improvement",
            mode="execute",
            authorization="explicit_execute",
            request_text=f"authorized exact controlled {phase}",
            targets=[gap_id, *handoff_ids, f"controlled_phase:{phase}"],
            authoritative_source="signed project records",
            latest_version="0.5.11",
            target_boundary="exact authorized legacy handoff list",
            must_keep=[
                "storyboard",
                "dialogue",
                "canonical",
                "XLSX",
                "production certificates",
                "audit history",
            ],
            must_remove=[],
            protected_scope=[
                "authored content",
                "historical authority records",
            ],
            expected_outputs=[phase],
            window_key=self.production_session_id,
            request_session_id=self.production_session_id,
        )
        return str(response.work_order_id)

    def _historical_plan(self) -> Any:
        return self.service.recover_storyboard_handoff(
            self.namespace,
            project_id=self.project_id,
            work_order_id=self.historical_work_order_id,
            gap_id=self.historical_gap_id,
            handoff_ids=self.historical_ids,
            action="plan",
            expected_plan_sha256=None,
            confirmation=None,
            request_session_id=self.production_session_id,
            phase="preflight_evidence_backfill",
        )

    def _historical_execute(self, plan_sha256: str) -> Any:
        return self.service.recover_storyboard_handoff(
            self.namespace,
            project_id=self.project_id,
            work_order_id=self.historical_work_order_id,
            gap_id=self.historical_gap_id,
            handoff_ids=self.historical_ids,
            action="execute",
            expected_plan_sha256=plan_sha256,
            confirmation=(
                service_module.PREFLIGHT_EVIDENCE_REISSUE_CONFIRMATION
            ),
            request_session_id=self.production_session_id,
            phase="preflight_evidence_backfill",
        )

    def _exact_recovery_plan(self) -> Any:
        return self.service.recover_storyboard_handoff(
            self.namespace,
            project_id=self.project_id,
            work_order_id=self.exact_recovery_work_order_id,
            gap_id=self.recovery_gap_id,
            handoff_ids=self.all_recovery_ids,
            action="plan",
            expected_plan_sha256=None,
            confirmation=None,
            request_session_id=self.production_session_id,
            phase="handoff_recovery",
        )

    def _exact_recovery_execute(self, plan_sha256: str) -> Any:
        return self.service.recover_storyboard_handoff(
            self.namespace,
            project_id=self.project_id,
            work_order_id=self.exact_recovery_work_order_id,
            gap_id=self.recovery_gap_id,
            handoff_ids=self.all_recovery_ids,
            action="execute",
            expected_plan_sha256=plan_sha256,
            confirmation=service_module.HANDOFF_RECOVERY_CONFIRMATION,
            request_session_id=self.production_session_id,
            phase="handoff_recovery",
        )

    def _historical_artifact_bytes(self) -> bytes:
        handoff = self.service.store.get_project_record(
            self.namespace,
            self.project_id,
            kind="handoffs",
            record_id=self.historical_ids[0],
        )
        _record, path = self.service.store.get_artifact(
            self.namespace,
            self.project_id,
            str(handoff["storyboard_artifact_id"]),
        )
        return Path(path).read_bytes()

    def _semantic_pass_for_historical_storyboard(
        self, artifact_id: str
    ) -> dict[str, Any]:
        return semantic_audit_for_artifact(
            self.service,
            self.settings,
            self.project_id,
            artifact_id,
        )

    async def test_normal_current_revalidation_route_is_unchanged(self) -> None:
        response = self.service.recover_storyboard_handoff(
            self.namespace,
            project_id=self.project_id,
            work_order_id=self.generic_current_backfill_work_order_id,
            gap_id=self.gap_id,
            handoff_ids=[self.generic_current_handoff_id],
            action="plan",
            expected_plan_sha256=None,
            confirmation=None,
            request_session_id=self.session_id,
            phase="preflight_evidence_backfill",
        )
        plan = response.data["preflight_evidence_plan"]
        self.assertEqual(
            plan["items"][0]["evidence_type"],
            "controlled_current_production_preflight_revalidation",
        )
        result = self.service.recover_storyboard_handoff(
            self.namespace,
            project_id=self.project_id,
            work_order_id=self.generic_current_backfill_work_order_id,
            gap_id=self.gap_id,
            handoff_ids=[self.generic_current_handoff_id],
            action="execute",
            expected_plan_sha256=plan["plan_sha256"],
            confirmation=(
                service_module.PREFLIGHT_EVIDENCE_REISSUE_CONFIRMATION
            ),
            request_session_id=self.session_id,
            phase="preflight_evidence_backfill",
        )
        mapping = result.data["mappings"][0]
        certificate = self.service.store.get_project_record(
            self.namespace,
            self.project_id,
            kind="certificates",
            record_id=str(mapping["certificate_id"]),
        )
        self.assertEqual(
            certificate["certificate_type"],
            service_module.AUDIT_CLASS_PRODUCTION_PREFLIGHT,
        )
        self.assertEqual(
            certificate["evidence_origin"],
            "controlled_current_revalidation",
        )
        self.assertNotIn("production_validation_replay", certificate)
        self.assertTrue(certificate["cache_key"].startswith("AUDCACHE-"))

    async def test_historical_backfill_plan_execute_and_replay_are_dual_report_and_append_only(
        self,
    ) -> None:
        old_hashes = {
            handoff_id: self.service.store.project_state_sha256(
                self.service.store.get_project_record(
                    self.namespace,
                    self.project_id,
                    kind="handoffs",
                    record_id=handoff_id,
                )
            )
            for handoff_id in self.historical_ids
        }
        artifact_before = self._historical_artifact_bytes()
        authority_before = self._recovery_authority_snapshot()
        response = self._historical_plan()
        self.assertEqual(
            self._recovery_authority_snapshot(), authority_before
        )
        plan = response.data["preflight_evidence_plan"]
        self.assertEqual(plan["authorized_handoff_ids"], self.historical_ids)
        self.assertEqual(
            plan["expected_confirmation"],
            service_module.PREFLIGHT_EVIDENCE_REISSUE_CONFIRMATION,
        )
        for item in plan["items"]:
            replay = item["production_validation_replay"]
            self.assertEqual(
                item["evidence_type"],
                "controlled_historical_production_preflight_attestation",
            )
            self.assertEqual(
                item["production_preflight_status"],
                service_module.HISTORICAL_PRODUCTION_PREFLIGHT_PASS,
            )
            self.assertEqual(item["current_validation_status"], "fail_current")
            self.assertEqual(item["current_validation_issue_count"], 1)
            self.assertEqual(
                replay["current_validation_error_histogram"],
                {"E-I2V-INDEP-002": 1},
            )
            self.assertFalse(item["current_production_pass_claimed"])
            self.assertFalse(item["current_preflight_cache_reusable"])
            self.assertFalse(
                item["conclusion_reusable_for_independent_audit"]
            )
            self.assertTrue(item["requires_full_current_independent_audit"])
            replay_without_proof = copy.deepcopy(replay)
            proof_sha256 = replay_without_proof.pop("proof_sha256")
            self.assertEqual(
                proof_sha256,
                service_module.sha256_bytes(
                    service_module._stable_json_bytes(replay_without_proof)
                ),
            )

        zero_write_before_confirmation = (
            self._recovery_authority_snapshot()
        )
        with self.assertRaisesRegex(
            ValueError,
            "preflight_evidence_reissue_confirmation_required",
        ):
            self.service.recover_storyboard_handoff(
                self.namespace,
                project_id=self.project_id,
                work_order_id=self.historical_work_order_id,
                gap_id=self.historical_gap_id,
                handoff_ids=self.historical_ids,
                action="execute",
                expected_plan_sha256=plan["plan_sha256"],
                confirmation="wrong",
                request_session_id=self.production_session_id,
                phase="preflight_evidence_backfill",
            )
        self.assertEqual(
            self._recovery_authority_snapshot(),
            zero_write_before_confirmation,
        )

        result = self._historical_execute(plan["plan_sha256"])
        self.assertEqual(result.status, "preflight_evidence_reissued")
        self.assertEqual(len(result.data["mappings"]), 6)
        for mapping in result.data["mappings"]:
            evidence = self.service.store.get_project_record(
                self.namespace,
                self.project_id,
                kind="preflight_evidence_reissues",
                record_id=str(mapping["preflight_evidence_id"]),
            )
            certificate = self.service.store.get_project_record(
                self.namespace,
                self.project_id,
                kind="certificates",
                record_id=str(mapping["certificate_id"]),
            )
            self.assertEqual(
                evidence["evidence_type"],
                "controlled_historical_production_preflight_attestation",
            )
            self.assertEqual(
                evidence["historical_validation_report_sha256"],
                service_module.sha256_bytes(
                    service_module._stable_json_bytes(
                        evidence["historical_validation_report"]
                    )
                ),
            )
            self.assertEqual(
                evidence["current_validation_report_sha256"],
                service_module.sha256_bytes(
                    service_module._stable_json_bytes(
                        evidence["current_validation_report"]
                    )
                ),
            )
            self.assertEqual(
                evidence["current_validation_error_histogram"],
                {"E-I2V-INDEP-002": 1},
            )
            self.assertEqual(
                certificate["certificate_type"],
                service_module.AUDIT_CLASS_HISTORICAL_PRODUCTION_PREFLIGHT,
            )
            self.assertTrue(certificate["cache_key"].startswith("HISTCACHE-"))
            self.assertEqual(certificate["current_validation_issue_count"], 1)
            self.assertFalse(certificate["current_production_pass_claimed"])
            self.assertTrue(
                certificate["requires_full_current_independent_audit"]
            )
            old_id = str(mapping["old_handoff_id"])
            self.assertEqual(
                self.service.store.project_state_sha256(
                    self.service.store.get_project_record(
                        self.namespace,
                        self.project_id,
                        kind="handoffs",
                        record_id=old_id,
                    )
                ),
                old_hashes[old_id],
            )
        self.assertEqual(self._historical_artifact_bytes(), artifact_before)
        authority_after = self._recovery_authority_snapshot()
        replay_result = self._historical_execute(plan["plan_sha256"])
        self.assertTrue(
            all(
                row["idempotent_replay"]
                for row in replay_result.data["mappings"]
            )
        )
        self.assertEqual(
            self._recovery_authority_snapshot(), authority_after
        )

    async def test_wrong_allowed_rules_profile_fails_closed_before_any_write(
        self,
    ) -> None:
        handoff_id = self.historical_ids[0]
        handoff = self.service.store.get_project_record(
            self.namespace,
            self.project_id,
            kind="handoffs",
            record_id=handoff_id,
        )
        certificate_id = str(handoff["certificate_id"])
        trace_id = str(handoff["stage_trace_id"])
        cache_components = copy.deepcopy(handoff["cache_components"])
        cache_components["ruleset"] = self.d884_ruleset
        cache_key = self.service._cache_key_from_components(cache_components)
        with self.service.store.mutate_project_record(
            self.namespace,
            self.project_id,
            kind="certificates",
            record_id=certificate_id,
        ) as certificate:
            certificate["ruleset_version"] = self.d884_ruleset
            certificate["cache_components"] = copy.deepcopy(cache_components)
            certificate["cache_key"] = cache_key
        with self.service.store.mutate_project_record(
            self.namespace,
            self.project_id,
            kind="handoffs",
            record_id=handoff_id,
        ) as poisoned:
            poisoned["ruleset_version"] = self.d884_ruleset
            poisoned["fingerprints"]["rules"] = self.d884_fingerprint
            poisoned["cache_components"] = copy.deepcopy(cache_components)
            poisoned["cache_key"] = cache_key
            for entry in poisoned["cache_manifest"]:
                entry["cache_key"] = cache_key
                entry["dependency_fingerprints"]["rules"] = (
                    self.d884_fingerprint
                )
        poisoned = self.service.store.get_project_record(
            self.namespace,
            self.project_id,
            kind="handoffs",
            record_id=handoff_id,
        )
        with self.service.store.mutate_project_record(
            self.namespace,
            self.project_id,
            kind="telemetry",
            record_id=trace_id,
        ) as trace:
            trace["input_fingerprints"] = copy.deepcopy(
                poisoned["fingerprints"]
            )
        self._replace_hub_handoff_rows(
            handoff_id,
            [self.service._handoff_record_projection(poisoned)],
        )
        snapshot = self._recovery_authority_snapshot()
        artifact_before = self._historical_artifact_bytes()
        with self.assertRaisesRegex(
            ValueError, "historical_preflight_origin_mismatch"
        ):
            self._historical_plan()
        self.assertEqual(self._recovery_authority_snapshot(), snapshot)
        self.assertEqual(self._historical_artifact_bytes(), artifact_before)

    async def test_same_histogram_but_changed_report_evidence_fails_closed_before_any_write(
        self,
    ) -> None:
        poisoned_report = copy.deepcopy(self.current_historical_report)
        poisoned_report["issues"][0]["evidence"] += " [resigned poison]"
        self.assertEqual(
            self.service._validation_error_histogram(poisoned_report),
            self.service._validation_error_histogram(
                self.current_historical_report
            ),
        )
        self.assertNotEqual(
            service_module.sha256_bytes(
                service_module._stable_json_bytes(poisoned_report)
            ),
            self.synthetic_current_validation_profiles[
                self.historical_ids[0]
            ]["current_report_sha256"],
        )
        snapshot = self._recovery_authority_snapshot()
        artifact_before = self._historical_artifact_bytes()
        with patch.object(
            service_module.validate_storyboard,
            "validate_project",
            return_value=poisoned_report,
        ):
            with self.assertRaisesRegex(
                ValueError,
                "historical_preflight_current_issue_scope_mismatch",
            ):
                self._historical_plan()
        self.assertEqual(self._recovery_authority_snapshot(), snapshot)
        self.assertEqual(self._historical_artifact_bytes(), artifact_before)

    async def test_swapped_exact_current_profiles_fail_closed_before_any_write(
        self,
    ) -> None:
        first, second = self.historical_ids[:2]
        swapped_profiles = copy.deepcopy(
            self.synthetic_current_validation_profiles
        )
        swapped_profiles[first], swapped_profiles[second] = (
            swapped_profiles[second],
            swapped_profiles[first],
        )
        snapshot = self._recovery_authority_snapshot()
        artifact_before = self._historical_artifact_bytes()
        with patch.object(
            service_module,
            "CONTROLLED_V053_CURRENT_VALIDATION_PROFILES",
            swapped_profiles,
        ):
            with self.assertRaisesRegex(
                ValueError,
                "historical_preflight_current_issue_scope_mismatch",
            ):
                self._historical_plan()
        self.assertEqual(self._recovery_authority_snapshot(), snapshot)
        self.assertEqual(self._historical_artifact_bytes(), artifact_before)

    async def test_current_report_bool_count_fails_closed_even_with_resigned_hash(
        self,
    ) -> None:
        poisoned_report = copy.deepcopy(self.current_historical_report)
        poisoned_report["error_count"] = True
        poisoned_profiles = copy.deepcopy(
            self.synthetic_current_validation_profiles
        )
        poisoned_profiles[self.historical_ids[0]][
            "current_report_sha256"
        ] = service_module.sha256_bytes(
            service_module._stable_json_bytes(poisoned_report)
        )
        snapshot = self._recovery_authority_snapshot()
        artifact_before = self._historical_artifact_bytes()
        with patch.object(
            service_module,
            "CONTROLLED_V053_CURRENT_VALIDATION_PROFILES",
            poisoned_profiles,
        ), patch.object(
            service_module.validate_storyboard,
            "validate_project",
            return_value=poisoned_report,
        ):
            with self.assertRaisesRegex(
                ValueError,
                "historical_preflight_current_issue_scope_mismatch",
            ):
                self._historical_plan()
        self.assertEqual(self._recovery_authority_snapshot(), snapshot)
        self.assertEqual(self._historical_artifact_bytes(), artifact_before)

    async def test_current_validator_composite_and_fingerprint_drift_fail_closed_before_any_write(
        self,
    ) -> None:
        artifact_before = self._historical_artifact_bytes()
        for field, poisoned_value in (
            ("_validator_digest", "0" * 16),
            ("_validator_fingerprint", "0" * 64),
        ):
            with self.subTest(field=field):
                snapshot = self._recovery_authority_snapshot()
                with patch.object(
                    self.service, field, poisoned_value
                ):
                    self.assertFalse(
                        self.service._sealed_historical_current_validation_profile(
                            handoff_id=self.historical_ids[0],
                            storyboard=self.historical_storyboard,
                            report=self.current_historical_report,
                        )
                    )
                    with self.assertRaises(ValueError):
                        self._historical_plan()
                self.assertEqual(
                    self._recovery_authority_snapshot(), snapshot
                )
                self.assertEqual(
                    self._historical_artifact_bytes(), artifact_before
                )

    async def test_frozen_init_tamper_fails_closed_before_any_write(
        self,
    ) -> None:
        frozen_init = (
            Path(service_module.frozen_v053_validator.__file__)
            .resolve()
            .parent
            / "__init__.py"
        )
        original_read_bytes = Path.read_bytes

        def tampered_read_bytes(path: Path) -> bytes:
            payload = original_read_bytes(path)
            if path.resolve() == frozen_init:
                return payload + b"\n# controlled test tamper\n"
            return payload

        snapshot = self._recovery_authority_snapshot()
        artifact_before = self._historical_artifact_bytes()
        with patch.object(Path, "read_bytes", new=tampered_read_bytes):
            with self.assertRaisesRegex(
                RuntimeError,
                "frozen_v053_validator_integrity_failed: init",
            ):
                self._historical_plan()
        self.assertEqual(self._recovery_authority_snapshot(), snapshot)
        self.assertEqual(self._historical_artifact_bytes(), artifact_before)
        self.assertEqual(
            original_read_bytes(frozen_init),
            frozen_init.read_bytes(),
        )

    async def test_legacy_bool_count_fields_fail_closed_before_any_write(
        self,
    ) -> None:
        handoff_id = self.historical_ids[0]
        handoff = self.service.store.get_project_record(
            self.namespace,
            self.project_id,
            kind="handoffs",
            record_id=handoff_id,
        )
        certificate_id = str(handoff["certificate_id"])
        cases = (
            ("certificate", "issue_count"),
            ("certificate", "checked_shot_count"),
            ("handoff", "issue_count"),
            ("handoff", "checked_shot_count"),
            ("report", "error_count"),
            ("report", "warning_count"),
            ("report", "shot_count"),
        )
        artifact_before = self._historical_artifact_bytes()

        for owner, field in cases:
            with self.subTest(owner=owner, field=field):
                record_kind = (
                    "handoffs" if owner == "handoff" else "certificates"
                )
                record_id = (
                    handoff_id if owner == "handoff" else certificate_id
                )
                current = self.service.store.get_project_record(
                    self.namespace,
                    self.project_id,
                    kind=record_kind,
                    record_id=record_id,
                )
                container = current["report"] if owner == "report" else current
                original_value = copy.deepcopy(container[field])
                try:
                    with self.service.store.mutate_project_record(
                        self.namespace,
                        self.project_id,
                        kind=record_kind,
                        record_id=record_id,
                    ) as poisoned:
                        target = (
                            poisoned["report"]
                            if owner == "report"
                            else poisoned
                        )
                        target[field] = True
                    if owner == "handoff":
                        poisoned_handoff = (
                            self.service.store.get_project_record(
                                self.namespace,
                                self.project_id,
                                kind="handoffs",
                                record_id=handoff_id,
                            )
                        )
                        self._replace_hub_handoff_rows(
                            handoff_id,
                            [
                                self.service._handoff_record_projection(
                                    poisoned_handoff
                                )
                            ],
                        )
                    snapshot = self._recovery_authority_snapshot()
                    with self.assertRaises(ValueError):
                        self._historical_plan()
                    self.assertEqual(
                        self._recovery_authority_snapshot(), snapshot
                    )
                    self.assertEqual(
                        self._historical_artifact_bytes(), artifact_before
                    )
                finally:
                    with self.service.store.mutate_project_record(
                        self.namespace,
                        self.project_id,
                        kind=record_kind,
                        record_id=record_id,
                    ) as restored:
                        target = (
                            restored["report"]
                            if owner == "report"
                            else restored
                        )
                        target[field] = original_value
                    if owner == "handoff":
                        restored_handoff = (
                            self.service.store.get_project_record(
                                self.namespace,
                                self.project_id,
                                kind="handoffs",
                                record_id=handoff_id,
                            )
                        )
                        self._replace_hub_handoff_rows(
                            handoff_id,
                            [
                                self.service._handoff_record_projection(
                                    restored_handoff
                                )
                            ],
                        )

    async def test_legacy_runtime_contract_drift_fails_closed_before_any_write(
        self,
    ) -> None:
        handoff = self.service.store.get_project_record(
            self.namespace,
            self.project_id,
            kind="handoffs",
            record_id=self.historical_ids[0],
        )
        runtime_certificate_id = str(
            handoff["cache_components"]["runtime_test_certificate_id"]
        )
        runtime_certificate = self.service.store.get_project_record(
            self.namespace,
            self.project_id,
            kind="certificates",
            record_id=runtime_certificate_id,
        )

        def runtime_certificate_key(value: dict[str, Any]) -> str:
            return "RUNTIMECERT-" + service_module.sha256_bytes(
                service_module._stable_json_bytes(
                    {
                        "runtime": value["runtime_version"],
                        "workflow": value["workflow_revision"],
                        "ruleset": value["ruleset_version"],
                        "validator": value["validator_version"],
                        "scripts": value["scripts_digest"],
                        "service_code": value["service_code_fingerprint"],
                        "dependencies": value["dependency_digest"],
                        "deployment_mode": value["deployment_mode"],
                        "output_contract": value["output_contract"],
                    }
                )
            )

        cases: list[tuple[tuple[str, ...], Any]] = [
            (("runtime_version",), "V16.3-148-R10-TAMPERED"),
            (("workflow_revision",), "R10-PG1-TAMPERED"),
            (("output_contract",), "R10-SPC-1-TAMPERED"),
        ]
        for suite_name, suite in runtime_certificate["tests"].items():
            cases.extend(
                (
                    (
                        ("tests", suite_name, "test_count"),
                        int(suite["test_count"]) + 1,
                    ),
                    (
                        ("tests", suite_name, "test_pass_count"),
                        int(suite["test_pass_count"]) - 1,
                    ),
                )
            )
        artifact_before = self._historical_artifact_bytes()

        for field_path, poisoned_value in cases:
            with self.subTest(field_path=field_path):
                original = self.service.store.get_project_record(
                    self.namespace,
                    self.project_id,
                    kind="certificates",
                    record_id=runtime_certificate_id,
                )
                target: Any = original
                for key in field_path[:-1]:
                    target = target[key]
                original_value = copy.deepcopy(target[field_path[-1]])
                try:
                    with self.service.store.mutate_project_record(
                        self.namespace,
                        self.project_id,
                        kind="certificates",
                        record_id=runtime_certificate_id,
                    ) as poisoned:
                        target = poisoned
                        for key in field_path[:-1]:
                            target = target[key]
                        target[field_path[-1]] = copy.deepcopy(poisoned_value)
                        poisoned["certificate_key"] = (
                            runtime_certificate_key(poisoned)
                        )
                    resigned = self.service.store.get_project_record(
                        self.namespace,
                        self.project_id,
                        kind="certificates",
                        record_id=runtime_certificate_id,
                    )
                    self.assertTrue(
                        self.service.store.project_record_is_signed(resigned)
                    )
                    snapshot = self._recovery_authority_snapshot()
                    with self.assertRaisesRegex(
                        ValueError,
                        "historical_preflight_origin_mismatch",
                    ):
                        self._historical_plan()
                    self.assertEqual(
                        self._recovery_authority_snapshot(), snapshot
                    )
                    self.assertEqual(
                        self._historical_artifact_bytes(), artifact_before
                    )
                finally:
                    with self.service.store.mutate_project_record(
                        self.namespace,
                        self.project_id,
                        kind="certificates",
                        record_id=runtime_certificate_id,
                    ) as restored:
                        target = restored
                        for key in field_path[:-1]:
                            target = target[key]
                        target[field_path[-1]] = original_value
                        restored["certificate_key"] = (
                            runtime_certificate_key(restored)
                        )

    async def test_historical_current_fail_cannot_recover_without_committed_pfe(
        self,
    ) -> None:
        snapshot = self._recovery_authority_snapshot()
        artifact_before = self._historical_artifact_bytes()
        with self.assertRaisesRegex(
            ValueError,
            "handoff_preflight_evidence_reissue_required",
        ):
            self._exact_recovery_plan()
        self.assertEqual(self._recovery_authority_snapshot(), snapshot)
        self.assertEqual(self._historical_artifact_bytes(), artifact_before)

    async def test_historical_publication_fence_rejects_resigned_pfe_authority_poison(
        self,
    ) -> None:
        preflight_plan = self._historical_plan().data[
            "preflight_evidence_plan"
        ]
        self._historical_execute(preflight_plan["plan_sha256"])
        recovery_plan = self._exact_recovery_plan().data["recovery_plan"]
        recovery_result = self._exact_recovery_execute(
            recovery_plan["plan_sha256"]
        )
        historical_mapping = recovery_result.data["mappings"][-1]
        successor_id = str(historical_mapping["new_handoff_id"])
        successor = self.service.store.get_project_record(
            self.namespace,
            self.project_id,
            kind="handoffs",
            record_id=successor_id,
        )
        evidence_id = str(successor["preflight_evidence_reissue_id"])
        self.assertTrue(
            self.service.store.handoff_is_published(
                self.namespace,
                self.project_id,
                successor,
            )
        )
        evidence = self.service.store.get_project_record(
            self.namespace,
            self.project_id,
            kind="preflight_evidence_reissues",
            record_id=evidence_id,
        )
        poisons: dict[str, Any] = {
            "work_order_id": "WO-resigned-poison",
            "evidence_key": "3" * 64,
            "runtime_test_certificate_id": "CERT-resigned-poison-runtime",
            "current_fingerprints_sha256": "4" * 64,
            "artifact_id": "ART-resigned-poison",
            "canonical_sha256": "5" * 64,
            "production_preflight_issue_count": 1,
            "history_records_modified": 1,
            "gap_id": "GAP-resigned-poison",
            "confirmation": "RESIGNED_BUT_UNAUTHORIZED",
            "authorized_handoff_ids": list(
                reversed(evidence["authorized_handoff_ids"])
            ),
            "plan_sha256": "0" * 64,
            "batch_trace_id": "TRC-resigned-poison-batch",
            "trace_id": "TRC-resigned-poison-item",
            "old_handoff_sha256": "1" * 64,
            "original_certificate_id": "CERT-resigned-poison-origin",
            "original_production_trace_id": "TRC-resigned-poison-origin",
            "current_validation_report_sha256": "2" * 64,
            "current_validation_error_histogram": {"POISON": 1},
        }
        for field, poisoned_value in poisons.items():
            with self.subTest(field=field):
                original_value = copy.deepcopy(evidence[field])
                with self.service.store.mutate_project_record(
                    self.namespace,
                    self.project_id,
                    kind="preflight_evidence_reissues",
                    record_id=evidence_id,
                ) as poisoned:
                    poisoned[field] = copy.deepcopy(poisoned_value)
                self.assertFalse(
                    self.service.store.handoff_is_published(
                        self.namespace,
                        self.project_id,
                        successor,
                    ),
                    field,
                )
                with self.service.store.mutate_project_record(
                    self.namespace,
                    self.project_id,
                    kind="preflight_evidence_reissues",
                    record_id=evidence_id,
                ) as restored:
                    restored[field] = original_value
                evidence[field] = original_value
                self.assertTrue(
                    self.service.store.handoff_is_published(
                        self.namespace,
                        self.project_id,
                        successor,
                    ),
                    field,
                )

    async def test_historical_recovery_successor_can_be_reissued_after_runtime_change(
        self,
    ) -> None:
        preflight_plan = self._historical_plan().data[
            "preflight_evidence_plan"
        ]
        self._historical_execute(preflight_plan["plan_sha256"])
        first_plan = self._exact_recovery_plan().data["recovery_plan"]
        first_result = self._exact_recovery_execute(
            first_plan["plan_sha256"]
        )
        mapping = next(
            row
            for row in first_result.data["mappings"]
            if row["old_handoff_id"] == self.historical_ids[0]
        )
        first_successor_id = str(mapping["new_handoff_id"])
        first_successor = self.service.store.get_project_record(
            self.namespace,
            self.project_id,
            kind="handoffs",
            record_id=first_successor_id,
        )
        self.assertEqual(
            first_successor["production_preflight_status"],
            service_module.HISTORICAL_PRODUCTION_PREFLIGHT_PASS,
        )

        # Keep the first signed recovery journal and all authored artifacts
        # unchanged while modeling a later orchestration-only deployment.
        self.service._service_code_fingerprint = "d" * 64
        self.service._runtime_fingerprint = "c" * 64
        current_runtime_certificate = (
            self.service._persist_runtime_certificate(
                self.namespace,
                self.project_id,
                {
                    "status": "pass",
                    "version": self.service.bootstrap.get("version"),
                    "workflow_revision": self.service.bootstrap.get(
                        "workflow_revision"
                    ),
                },
            )
        )
        gap_id = "GAP-test-historical-successor-recovery"
        with self.service.store.mutate_project(
            self.namespace, self.project_id
        ) as (hub, _ledger):
            service_module.project_agent.record_capability_gap(
                hub,
                {
                    "gap_id": gap_id,
                    "timestamp": service_module.utc_stamp(),
                    "request": "recover one sealed historical successor",
                    "missing_dependency": "successor lineage refresh",
                    "risk": "runtime drift blocks current audit",
                    "safe_fallback": "leave it pending",
                    "proposed_implementation": "verify signed immediate journal and sealed fail profile",
                    "required_tests": ["lineage", "current fail profile"],
                },
            )
        session_id = "v0511-historical-successor-recovery"
        order = self.service.begin_task(
            self.namespace,
            project_id=self.project_id,
            operation="rule_improvement",
            mode="execute",
            authorization="explicit_execute",
            request_text="authorized historical successor recovery",
            targets=[
                gap_id,
                first_successor_id,
                "controlled_phase:handoff_recovery",
            ],
            authoritative_source="signed recovery journal and historical attestation",
            latest_version="next-runtime",
            target_boundary="one exact historical successor",
            must_keep=["history", "current failure diagnosis"],
            must_remove=[],
            protected_scope=["storyboard", "canonical", "audit history"],
            expected_outputs=["prepared current-fingerprint successor"],
            window_key=session_id,
            request_session_id=session_id,
        )
        plan_response = self.service.recover_storyboard_handoff(
            self.namespace,
            project_id=self.project_id,
            work_order_id=str(order.work_order_id),
            gap_id=gap_id,
            handoff_ids=[first_successor_id],
            action="plan",
            expected_plan_sha256=None,
            confirmation=None,
            request_session_id=session_id,
            phase="handoff_recovery",
        )
        plan = plan_response.data["recovery_plan"]
        self.assertEqual(
            plan["runtime_test_certificate_id"],
            current_runtime_certificate["certificate_id"],
        )
        self.assertEqual(
            plan["items"][0]["preflight_evidence_source"],
            "controlled_historical_contract_replay",
        )
        result = self.service.recover_storyboard_handoff(
            self.namespace,
            project_id=self.project_id,
            work_order_id=str(order.work_order_id),
            gap_id=gap_id,
            handoff_ids=[first_successor_id],
            action="execute",
            expected_plan_sha256=plan["plan_sha256"],
            confirmation=service_module.HANDOFF_RECOVERY_CONFIRMATION,
            request_session_id=session_id,
            phase="handoff_recovery",
        )
        second_successor = self.service.store.get_project_record(
            self.namespace,
            self.project_id,
            kind="handoffs",
            record_id=str(result.data["mappings"][0]["new_handoff_id"]),
        )
        first_after = self.service.store.get_project_record(
            self.namespace,
            self.project_id,
            kind="handoffs",
            record_id=first_successor_id,
        )
        self.assertEqual(first_after["status"], "superseded")
        self.assertEqual(second_successor["status"], "prepared")
        self.assertEqual(
            second_successor["production_preflight_status"],
            service_module.HISTORICAL_PRODUCTION_PREFLIGHT_PASS,
        )
        self.assertEqual(
            second_successor["production_validation_replay"],
            first_after["production_validation_replay"],
        )
        self.assertFalse(second_successor["current_production_pass_claimed"])
        self.assertTrue(
            second_successor["requires_full_current_independent_audit"]
        )
        replay = self.service.recover_storyboard_handoff(
            self.namespace,
            project_id=self.project_id,
            work_order_id=str(order.work_order_id),
            gap_id=gap_id,
            handoff_ids=[first_successor_id],
            action="execute",
            expected_plan_sha256=plan["plan_sha256"],
            confirmation=service_module.HANDOFF_RECOVERY_CONFIRMATION,
            request_session_id=session_id,
            phase="handoff_recovery",
        )
        self.assertTrue(replay.data["mappings"][0]["idempotent_replay"])

    async def test_eleven_handoffs_recover_then_historical_successor_forces_current_audit(
        self,
    ) -> None:
        preflight_plan = self._historical_plan().data[
            "preflight_evidence_plan"
        ]
        self._historical_execute(preflight_plan["plan_sha256"])

        response = self._exact_recovery_plan()
        plan = response.data["recovery_plan"]
        self.assertEqual(len(plan["items"]), 11)
        self.assertEqual(
            [row["old_handoff_id"] for row in plan["items"]],
            self.all_recovery_ids,
        )
        self.assertEqual(
            [row["preflight_evidence_source"] for row in plan["items"][:5]],
            ["original_handoff"] * 5,
        )
        self.assertEqual(
            [row["preflight_evidence_source"] for row in plan["items"][5:]],
            ["controlled_historical_contract_replay"] * 6,
        )
        result = self._exact_recovery_execute(plan["plan_sha256"])
        mappings = result.data["mappings"]
        self.assertEqual(len(mappings), 11)
        self.assertEqual(
            [row["old_handoff_id"] for row in mappings],
            self.all_recovery_ids,
        )
        for mapping in mappings:
            old_handoff = self.service.store.get_project_record(
                self.namespace,
                self.project_id,
                kind="handoffs",
                record_id=str(mapping["old_handoff_id"]),
            )
            successor = self.service.store.get_project_record(
                self.namespace,
                self.project_id,
                kind="handoffs",
                record_id=str(mapping["new_handoff_id"]),
            )
            self.assertEqual(old_handoff["status"], "superseded")
            self.assertEqual(successor["status"], "prepared")
            self.assertEqual(
                successor["independent_audit_status"], "PENDING_AUDIT"
            )
        historical_mapping = mappings[-1]
        historical_successor = self.service.store.get_project_record(
            self.namespace,
            self.project_id,
            kind="handoffs",
            record_id=str(historical_mapping["new_handoff_id"]),
        )
        historical_certificate = self.service.store.get_project_record(
            self.namespace,
            self.project_id,
            kind="certificates",
            record_id=str(historical_successor["certificate_id"]),
        )
        self.assertEqual(
            historical_certificate["certificate_type"],
            service_module.AUDIT_CLASS_HISTORICAL_PRODUCTION_PREFLIGHT,
        )
        self.assertEqual(
            historical_successor["production_preflight_status"],
            service_module.HISTORICAL_PRODUCTION_PREFLIGHT_PASS,
        )
        self.assertFalse(
            historical_successor["current_production_pass_claimed"]
        )
        self.assertTrue(
            historical_successor["requires_full_current_independent_audit"]
        )
        self.assertTrue(
            historical_successor["recovery_publication_fenced"]
        )
        self.assertNotIn("production_result", historical_successor)
        self.assertNotIn("issue_count", historical_successor)
        replay = self._exact_recovery_execute(plan["plan_sha256"])
        self.assertTrue(
            all(row["idempotent_replay"] for row in replay.data["mappings"])
        )

        audit_session = "v0511-independent-audit"
        audit_order = self.service.begin_task(
            self.namespace,
            project_id=self.project_id,
            operation="storyboard_audit",
            mode="audit_only",
            authorization="read_only",
            request_text="independent audit must run the current validator",
            targets=[historical_successor["storyboard_artifact_id"]],
            authoritative_source="signed recovered production handoff",
            latest_version="0.5.11",
            target_boundary="one recovered historical successor",
            must_keep=["read-only independent audit"],
            must_remove=[],
            protected_scope=["all authored content"],
            expected_outputs=["independent audit certificate"],
            window_key=audit_session,
            request_session_id=audit_session,
        )
        current_validator = (
            service_module.validate_storyboard.validate_project
        )
        with patch.object(
            service_module.validate_storyboard,
            "validate_project",
            wraps=current_validator,
        ) as validator_spy:
            audit_response = self.service.audit_storyboard(
                self.namespace,
                project_id=self.project_id,
                artifact_id=str(
                    historical_successor["storyboard_artifact_id"]
                ),
                audit_cycle_id=None,
                work_order_id=str(audit_order.work_order_id),
                handoff_artifact_id=str(
                    historical_successor["handoff_id"]
                ),
                force_machine_recheck=False,
                semantic_audit=(
                    self._semantic_pass_for_historical_storyboard(
                        str(historical_successor["storyboard_artifact_id"])
                    )
                ),
                request_session_id=audit_session,
            )
        self.assertGreaterEqual(validator_spy.call_count, 1)
        self.assertEqual(audit_response.status, "fail_current")
        self.assertEqual(
            audit_response.data["report"]["issues"][0]["error_code"],
            "E-I2V-INDEP-002",
        )
        audit_certificate = self.service.store.get_project_record(
            self.namespace,
            self.project_id,
            kind="certificates",
            record_id=str(
                audit_response.data["certificate"]["certificate_id"]
            ),
        )
        self.assertEqual(
            audit_certificate["certificate_type"],
            service_module.AUDIT_CLASS_INDEPENDENT,
        )
        self.assertEqual(audit_certificate["result"], "fail_current")
        self.assertNotEqual(
            audit_certificate["cache_key"], historical_certificate["cache_key"]
        )

        audited_root = self.service.store.get_project_record(
            self.namespace,
            self.project_id,
            kind="handoffs",
            record_id=str(historical_successor["handoff_id"]),
        )
        self.assertEqual(audited_root["status"], "consumed")
        self.assertEqual(
            audited_root["audit_certificate_id"],
            audit_certificate["certificate_id"],
        )
        reaudit_response = self.service.audit_storyboard(
            self.namespace,
            project_id=self.project_id,
            artifact_id=str(historical_successor["storyboard_artifact_id"]),
            audit_cycle_id=None,
            work_order_id=str(audit_order.work_order_id),
            handoff_artifact_id=str(historical_successor["handoff_id"]),
            force_machine_recheck=True,
            semantic_audit=self._semantic_pass_for_historical_storyboard(
                str(historical_successor["storyboard_artifact_id"])
            ),
            request_session_id=audit_session,
        )
        self.assertEqual(reaudit_response.status, "fail_current")
        reaudit_successor_id = str(
            reaudit_response.data["handoff"]["handoff_id"]
        )
        self.assertNotEqual(
            reaudit_successor_id,
            historical_successor["handoff_id"],
        )
        reaudit_successor = self.service.store.get_project_record(
            self.namespace,
            self.project_id,
            kind="handoffs",
            record_id=reaudit_successor_id,
        )
        self.assertEqual(reaudit_successor["status"], "consumed")
        self.assertEqual(
            reaudit_successor["reaudit_kind"],
            "independent_audit_successor",
        )
        self.assertEqual(
            reaudit_successor["reaudit_root_handoff_id"],
            historical_successor["handoff_id"],
        )
        self.assertEqual(
            reaudit_successor["reaudit_parent_handoff_id"],
            historical_successor["handoff_id"],
        )
        self.assertEqual(reaudit_successor["reaudit_sequence"], 1)
        self.assertTrue(
            reaudit_successor["recovery_publication_fenced"]
        )
        self.assertNotIn("production_result", reaudit_successor)
        self.assertNotIn("issue_count", reaudit_successor)
        self.assertEqual(
            reaudit_successor["previous_audit_certificate_id"],
            audit_certificate["certificate_id"],
        )

        # A forced re-audit may add only server-minted lineage and audit
        # lifecycle fields.  Every recovered historical production fact must
        # remain byte-for-byte equal to the audited root.
        self.assertEqual(
            self.service._reaudit_immutable_identity(reaudit_successor),
            self.service._reaudit_immutable_identity(audited_root),
        )
        expected_lineage_sha256 = service_module.sha256_bytes(
            service_module._stable_json_bytes(
                self.service._reaudit_lineage_payload(
                    root=audited_root,
                    parent=audited_root,
                    successor=reaudit_successor,
                )
            )
        )
        self.assertEqual(
            reaudit_successor["reaudit_lineage_sha256"],
            expected_lineage_sha256,
        )
        reaudit_certificate = self.service.store.get_project_record(
            self.namespace,
            self.project_id,
            kind="certificates",
            record_id=str(
                reaudit_response.data["certificate"]["certificate_id"]
            ),
        )
        self.assertEqual(reaudit_certificate["result"], "fail_current")
        self.assertEqual(
            reaudit_certificate["handoff_id"], reaudit_successor_id
        )


if __name__ == "__main__":
    import unittest

    unittest.main()
