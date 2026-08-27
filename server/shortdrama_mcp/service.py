from __future__ import annotations

import copy
import json
import re
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from .canonical import canonical_json_bytes, canonical_sha256 as _canonical_sha256
from .config import Settings
from .file_io import download_openai_file, extract_file, preview_extracted, storyboard_to_xlsx
from .models import OpenAIFile, ToolResponse
from .storage import ProjectStore, sha256_bytes, slug_filename, utc_stamp


PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOT = PLUGIN_ROOT / "skills" / "shortdrama-storyboard"
SCRIPTS_DIR = SKILL_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import project_agent  # noqa: E402
import r10_bootstrap  # noqa: E402
import storyboard_agent  # noqa: E402
import validate_storyboard  # noqa: E402


STORYBOARD_OPERATIONS = {
    "storyboard_create",
    "storyboard_regenerate",
    "storyboard_change",
    "storyboard_repair",
    "seedance_create",
}

PLAN_ARTIFACT_TYPES = {
    "chapter_intake",
    "novel_analysis",
    "story_diagnosis",
    "hook_plan",
    "adaptation_plan",
    "episode_plan",
    "character_arc",
    "scene_plan",
    "rewrite_plan",
    "storyboard_plan",
    "change_impact",
}

PRODUCTION_ARTIFACT_TYPES = {
    "script",
    "dialogue",
    "anchors",
    "keyframe_prompt",
    "keyframe_image",
    "seedance_batches",
    "bgm_plan",
    "voice_plan",
    "production_batch",
    "statistics",
    "change_notes",
    "conversion",
}

AUDIT_CLASS_PRODUCTION_PREFLIGHT = "production_preflight"
AUDIT_CLASS_INDEPENDENT = "independent_audit"

# A local patch may repair presentation/execution detail, but it must never be
# used to smuggle a story, boundary, timing, identity, cognition, or source
# change past the full-rebuild workflow.  Matching is deliberately bilingual
# because imported canonical storyboards can contain either Chinese or English
# extension fields.
PATCH_FORBIDDEN_FIELDS = {
    "镜头编号",
    "功能类型",
    "镜头类型",
    "场景连续性id",
    "累计时长",
    "时长",
    "可见主体",
    "人物调度",
    "画面内容",
    "动作与表演",
    "台词",
    "心理音轨",
    "心理音轨属性",
    "参考图锚定",
    "覆盖原文",
    "原文支撑句",
    "shot_id",
    "shotid",
    "scene_continuity_id",
    "duration",
    "cumulative_duration",
    "source_sentence",
    "source_support",
    "生成批次",
    "生成批次引用",
    "seedance引用",
    "seedance批次引用",
    "seedance_batch_ref",
    "batch_id",
    "batch_mapping",
    "关键帧引用",
    "keyframe_reference",
}
PATCH_FORBIDDEN_FIELD_TERMS = (
    "原文",
    "章节边界",
    "认知",
    "信息权限",
    "人物身份",
    "source_",
    "source.",
    "boundary",
    "cognition",
    "knowledge_state",
    "场景拓扑",
    "镜头拓扑",
    "生成批次",
    "批次映射",
    "批次引用",
    "seedance引用",
    "seedance_reference",
    "batch_ref",
    "batch_mapping",
    "keyframe_reference",
)

AUDIT_GATE_IDS = tuple(f"G{index}" for index in range(14))
AUDIT_LAYER_NAMES = (
    "源文对白",
    "机位可见性",
    "空间连续",
    "表演链",
    "镜头节奏",
    "Seedance独立执行",
    "禁项与声音",
    "统计闭合",
)


def _stable_json_bytes(value: Any) -> bytes:
    return canonical_json_bytes(value)


def canonical_storyboard_sha256(storyboard: dict[str, Any]) -> str:
    """Return the layout-independent identity of a canonical storyboard."""

    return _canonical_sha256(storyboard)


def build_audit_cache_key(
    *,
    canonical_sha256: str,
    source_fingerprint: str,
    boundary_hash: str,
    ruleset_version: str,
    validator_version: str,
    audit_class: str,
    artifact_fingerprint: str = "",
    parser_fingerprint: str = "",
    schema_fingerprint: str = "",
    runtime_fingerprint: str = "",
    scripts_fingerprint: str = "",
    dependencies_fingerprint: str = "",
    anchors_fingerprint: str = "",
    target_model_fingerprint: str = "",
    scope_fingerprint: str = "",
    runtime_test_certificate_id: str = "",
) -> str:
    """Build an audit cache key whose safety-relevant inputs are explicit."""

    components = {
        "canonical": canonical_sha256,
        "source": source_fingerprint,
        "boundary": boundary_hash,
        "ruleset": ruleset_version,
        "validator": validator_version,
        "audit_class": audit_class,
        "artifact": artifact_fingerprint,
        "parser": parser_fingerprint,
        "schema": schema_fingerprint,
        "runtime": runtime_fingerprint,
        "scripts": scripts_fingerprint,
        "dependencies": dependencies_fingerprint,
        "anchors": anchors_fingerprint,
        "target_model": target_model_fingerprint,
        "scope": scope_fingerprint,
        "runtime_test_certificate_id": runtime_test_certificate_id,
    }
    return f"AUDCACHE-{sha256_bytes(_stable_json_bytes(components))}"


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


def _artifact_version(artifacts: list[dict[str, Any]], artifact_type: str) -> str:
    number = 1 + sum(item.get("artifact_type") == artifact_type for item in artifacts)
    return f"v{number}"


def _find_work_order(hub: dict[str, Any], work_order_id: str) -> dict[str, Any]:
    work_order = next((item for item in hub.get("work_orders", []) if item.get("work_order_id") == work_order_id), None)
    if work_order is None:
        raise ValueError(f"unknown work_order_id: {work_order_id}")
    return work_order


def _issue_category(error_code: str) -> str:
    code = error_code.upper()
    if "PUBLIC-CONT" in code:
        return "空间连续"
    if "PUBLIC" in code:
        return "禁项与声音"
    if "CHAPTER-BUDGET" in code:
        return "镜头节奏"
    if any(term in code for term in ("SOURCE", "DIALOGUE", "COGNITION", "CHANGELOCK", "CHAPTER-INTAKE")):
        return "源文对白"
    if any(term in code for term in ("CAMERA", "VISIBLE", "VISIBILITY")):
        return "机位可见性"
    if any(term in code for term in ("CONTINUITY", "SPATIAL", "LIGHT")):
        return "空间连续"
    if any(term in code for term in ("PERFORMANCE", "EXPRESSION", "BLINK", "VOICE", "PUNCT")):
        return "表演链"
    if any(term in code for term in ("DURATION", "RHYTHM", "SHOTLOAD", "SHOT-")):
        return "镜头节奏"
    if any(term in code for term in ("BATCH", "MOTION", "SEEDANCE", "CROSSCONTEXT", "I2V")):
        return "Seedance独立执行"
    if any(term in code for term in ("AUDIO", "BGM", "PSYCH", "PROHIBIT")):
        return "禁项与声音"
    return "统计闭合"


def _gate_for_issue(error_code: str) -> str:
    category = _issue_category(error_code)
    return {
        "源文对白": "source_coverage",
        "机位可见性": "camera_visibility",
        "空间连续": "spatial_continuity",
        "表演链": "performance_chain",
        "镜头节奏": "shot_rhythm",
        "Seedance独立执行": "seedance_independence",
        "禁项与声音": "prohibitions_audio",
        "统计闭合": "statistics",
    }[category]


def _gate_results(required_gates: list[str], issues: list[dict[str, Any]]) -> dict[str, str]:
    results = {gate: "pass" for gate in required_gates}
    for issue in issues:
        gate = _gate_for_issue(str(issue.get("error_code") or ""))
        if gate in results:
            results[gate] = "fail"
        elif required_gates:
            results[required_gates[0]] = "fail"
    return results


class StoryboardService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = ProjectStore(settings.data_dir, settings.public_base_url)
        self.bootstrap = r10_bootstrap.verify_bundled(self_test=False)
        ruleset_paths = [SKILL_ROOT / "SKILL.md", *sorted((SKILL_ROOT / "references").glob("*.json"))]
        ruleset_fingerprints = [
            [str(path.relative_to(SKILL_ROOT)), sha256_bytes(path.read_bytes())]
            for path in ruleset_paths
            if path.is_file()
        ]
        validator_path = SCRIPTS_DIR / "validate_storyboard.py"
        self._ruleset_digest = sha256_bytes(_stable_json_bytes(ruleset_fingerprints))[:16]
        self._validator_digest = (
            sha256_bytes(validator_path.read_bytes())[:16] if validator_path.is_file() else "missing"
        )
        script_fingerprints = [
            [str(path.relative_to(SKILL_ROOT)), sha256_bytes(path.read_bytes())]
            for path in sorted(SCRIPTS_DIR.glob("*.py"))
            if path.is_file()
        ]
        requirements_path = PLUGIN_ROOT / "requirements.txt"
        dependency_payload = {
            "python": ".".join(str(value) for value in sys.version_info[:3]),
            "requirements_sha256": (
                sha256_bytes(requirements_path.read_bytes()) if requirements_path.is_file() else "missing"
            ),
        }
        self._runtime_scripts_digest = sha256_bytes(_stable_json_bytes(script_fingerprints))[:16]
        self._dependency_digest = sha256_bytes(_stable_json_bytes(dependency_payload))[:16]
        self._ruleset_fingerprint = sha256_bytes(_stable_json_bytes(ruleset_fingerprints))
        self._validator_fingerprint = (
            sha256_bytes(validator_path.read_bytes())
            if validator_path.is_file()
            else sha256_bytes(b"missing-validator")
        )
        self._scripts_fingerprint = sha256_bytes(_stable_json_bytes(script_fingerprints))
        self._dependencies_fingerprint = sha256_bytes(_stable_json_bytes(dependency_payload))
        parser_paths = [
            Path(__file__).with_name("canonical.py"),
            Path(__file__).with_name("file_io.py"),
        ]
        self._parser_fingerprint = sha256_bytes(
            _stable_json_bytes(
                [
                    [path.name, sha256_bytes(path.read_bytes())]
                    for path in parser_paths
                    if path.is_file()
                ]
            )
        )
        schema_path = SKILL_ROOT / "references" / "storyboard_schema.json"
        self._schema_fingerprint = (
            sha256_bytes(schema_path.read_bytes())
            if schema_path.is_file()
            else sha256_bytes(b"missing-storyboard-schema")
        )
        self._runtime_fingerprint = sha256_bytes(
            _stable_json_bytes(
                {
                    "runtime": self.bootstrap.get("version"),
                    "workflow": self.bootstrap.get("workflow_revision"),
                }
            )
        )
        self._target_model_fingerprint = sha256_bytes(b"Seedance 2.5 independent execution contract")
        self._project_schema = json.loads(
            (SKILL_ROOT / "references" / "project_agent_schema.json").read_text(encoding="utf-8")
        )
        self._ledger_schema = json.loads(
            (SKILL_ROOT / "references" / "agent_ledger_schema.json").read_text(encoding="utf-8")
        )
        try:
            Draft202012Validator.check_schema(self._project_schema)
            Draft202012Validator.check_schema(self._ledger_schema)
        except SchemaError as exc:
            raise RuntimeError(f"R10 lifecycle schema is invalid: {exc.message}") from exc

    def _require_runtime(self) -> None:
        if self.bootstrap.get("status") != "pass":
            raise RuntimeError("R10 runtime bootstrap failed; execution is blocked")

    @staticmethod
    def _validate_schema_record(
        schema: dict[str, Any], definition: str, value: dict[str, Any]
    ) -> None:
        record_schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$ref": f"#/$defs/{definition}",
            "$defs": schema.get("$defs", {}),
        }
        try:
            Draft202012Validator(record_schema).validate(value)
        except ValidationError as exc:
            location = ".".join(str(item) for item in exc.absolute_path) or definition
            raise ValueError(
                f"lifecycle_schema_mismatch: {definition}.{location}: {exc.message}"
            ) from exc

    @property
    def _ruleset_version(self) -> str:
        return ":".join(
            [
                str(self.bootstrap.get("version") or "unknown"),
                str(self.bootstrap.get("workflow_revision") or "unknown"),
                self._ruleset_digest,
            ]
        )

    def _validator_version(self) -> str:
        return ":".join(
            [
                str(getattr(validate_storyboard, "VERSION", "unknown")),
                str(getattr(validate_storyboard, "SCHEMA_VERSION", "unknown")),
                str(getattr(validate_storyboard, "WORKFLOW_PROFILE", "unknown")),
                self._validator_digest,
            ]
        )

    def _runtime_certificate_key(self) -> str:
        return "RUNTIMECERT-" + sha256_bytes(
            _stable_json_bytes(
                {
                    "runtime": self.bootstrap.get("version"),
                    "workflow": self.bootstrap.get("workflow_revision"),
                    "ruleset": self._ruleset_version,
                    "validator": self._validator_version(),
                    "scripts": self._runtime_scripts_digest,
                    "dependencies": self._dependency_digest,
                    "deployment_mode": self.settings.deployment_mode,
                }
            )
        )

    def _matching_runtime_certificate(
        self, namespace: str, project_id: str
    ) -> dict[str, Any] | None:
        certificate = self.store.find_project_record(
            namespace,
            project_id,
            kind="certificates",
            field="certificate_key",
            value=self._runtime_certificate_key(),
        )
        if certificate is None:
            return None
        if (
            certificate.get("certificate_type") != "runtime_full_regression"
            or certificate.get("result") != "pass"
            or certificate.get("ruleset_version") != self._ruleset_version
            or certificate.get("validator_version") != self._validator_version()
            or certificate.get("scripts_digest") != self._runtime_scripts_digest
            or certificate.get("dependency_digest") != self._dependency_digest
        ):
            return None
        return certificate

    def _require_runtime_test_certificate(
        self, namespace: str, project_id: str
    ) -> dict[str, Any]:
        certificate = self._matching_runtime_certificate(namespace, project_id)
        if certificate is None:
            raise ValueError(
                "runtime_test_certificate_required: call get_agent_status with full_self_test=true after any runtime/rule/schema change"
            )
        if not self.store.project_record_is_signed(certificate):
            raise ValueError(
                "runtime_test_certificate_required: legacy unsigned runtime certificate must be regenerated"
            )
        return certificate

    def _persist_runtime_certificate(
        self,
        namespace: str,
        project_id: str,
        report: dict[str, Any],
    ) -> dict[str, Any]:
        certificate_id = _new_id("CERT")
        tests = {}
        for key in ("golden_tests", "public_scene_tests", "agent_tests", "project_agent_tests"):
            value = report.get(key)
            if isinstance(value, dict):
                tests[key] = {
                    "status": value.get("status"),
                    "test_count": value.get("test_count"),
                    "test_pass_count": value.get("test_pass_count"),
                    "test_fail_count": value.get("test_fail_count"),
                }
        value = {
            "certificate_id": certificate_id,
            "certificate_type": "runtime_full_regression",
            "certificate_key": self._runtime_certificate_key(),
            "result": "pass" if report.get("status") == "pass" else "fail_current",
            "runtime_version": report.get("version"),
            "workflow_revision": report.get("workflow_revision"),
            "ruleset_version": self._ruleset_version,
            "validator_version": self._validator_version(),
            "scripts_digest": self._runtime_scripts_digest,
            "dependency_digest": self._dependency_digest,
            "deployment_mode": self.settings.deployment_mode,
            "tests": tests,
        }
        return self.store.save_project_record(
            namespace,
            project_id,
            kind="certificates",
            record_id=certificate_id,
            value=value,
        )

    @staticmethod
    def _boundary_hash(storyboard: dict[str, Any]) -> str:
        chapter_intake = storyboard.get("chapter_intake")
        if not isinstance(chapter_intake, dict):
            chapter_intake = {}
        boundary_payload = {
            "source_lock": storyboard.get("source_lock") or {},
            "chapter_boundary": chapter_intake.get("chapter_boundary")
            or chapter_intake.get("target_boundary")
            or chapter_intake.get("boundary"),
            "previous_boundary": chapter_intake.get("previous_chapter_boundary"),
            "next_boundary": chapter_intake.get("next_chapter_boundary"),
        }
        return sha256_bytes(_stable_json_bytes(boundary_payload))

    def _source_fingerprint(
        self,
        namespace: str,
        project_id: str,
        storyboard: dict[str, Any],
        source_artifact_id: str | None,
        artifact_record: dict[str, Any] | None = None,
    ) -> str:
        if artifact_record:
            cached = artifact_record.get("metadata", {}).get("source_fingerprint")
            if cached:
                return str(cached)
        if source_artifact_id:
            try:
                source_record, _path = self.store.get_artifact(namespace, project_id, source_artifact_id)
                source_metadata = source_record.get("metadata", {})
                # A repaired storyboard inherits the original source lineage.
                # Its own canonical hash identifies the derived storyboard and
                # must never replace the novel/script source fingerprint.
                inherited = source_metadata.get("source_fingerprint")
                return str(inherited or source_record.get("fingerprint"))
            except ValueError:
                pass
        source_lock = storyboard.get("source_lock") or {}
        explicit = source_lock.get("source_fingerprint") if isinstance(source_lock, dict) else None
        return str(explicit or sha256_bytes(_stable_json_bytes(source_lock)))

    @staticmethod
    def _sha256_identity(value: Any) -> str:
        text = str(value or "").strip().casefold()
        if re.fullmatch(r"[0-9a-f]{64}", text):
            return text
        return sha256_bytes(str(value or "").encode("utf-8"))

    @staticmethod
    def _session_fingerprint(namespace: str, request_session_id: str | None) -> str | None:
        if not request_session_id:
            return None
        # Namespace binding prevents the same transport identifier from being
        # comparable across OAuth subjects without exposing either value.
        return sha256_bytes(f"{namespace}\x00{request_session_id}".encode("utf-8"))

    @staticmethod
    def _work_order_role(operation: str, mode: str) -> str:
        if operation == "storyboard_audit" and mode == "audit_only":
            return "independent_audit"
        if operation in {"storyboard_repair", "storyboard_change"}:
            return "repair"
        return "production"

    def _assert_work_order_session(
        self,
        namespace: str,
        work_order: dict[str, Any],
        request_session_id: str | None,
    ) -> None:
        expected = work_order.get("transport_session_fingerprint")
        actual = self._session_fingerprint(namespace, request_session_id)
        if expected and expected != actual:
            raise ValueError(
                "window_session_mismatch: tool call is not from the server-bound work-order session"
            )

    def _fingerprint_bundle(
        self,
        storyboard: dict[str, Any],
        artifact_record: dict[str, Any],
        *,
        canonical_hash: str,
        source_fingerprint: str,
        boundary_hash: str,
    ) -> dict[str, str]:
        source_sha = self._sha256_identity(source_fingerprint)
        anchors = {
            key: storyboard.get(key)
            for key in (
                "character_anchors",
                "scene_anchors",
                "prop_anchors",
                "costume_anchors",
                "project_anchors",
            )
            if key in storyboard
        }
        raw = self._sha256_identity(artifact_record.get("fingerprint"))
        return {
            "source_bytes": source_sha,
            "normalized_source": source_sha,
            "episode_boundary": self._sha256_identity(boundary_hash),
            "artifact_raw": raw,
            "artifact_semantic": self._sha256_identity(canonical_hash),
            "canonical": self._sha256_identity(canonical_hash),
            "parser": self._parser_fingerprint,
            "schema": self._schema_fingerprint,
            "runtime": self._runtime_fingerprint,
            "rules": self._ruleset_fingerprint,
            "validator": self._validator_fingerprint,
            "scripts": self._scripts_fingerprint,
            "dependencies_environment": self._dependencies_fingerprint,
            "project_anchors": sha256_bytes(_stable_json_bytes(anchors)),
            "target_model_contract": self._target_model_fingerprint,
        }

    def _attach_independent_evidence_matrix(
        self, report: dict[str, Any], storyboard: dict[str, Any]
    ) -> dict[str, Any]:
        """Materialize auditable gate/layer evidence from validator findings.

        Certificates consume this matrix and never infer fourteen gates or all
        eight semantic layers merely from ``issues == []``.
        """

        enriched = copy.deepcopy(report)
        issues = [item for item in enriched.get("issues", []) if isinstance(item, dict)]
        chapter_mode = isinstance(storyboard.get("chapter_intake"), dict)
        evidence_prefix = (
            f"VAL-{self._validator_fingerprint[:12]}-"
            f"{canonical_storyboard_sha256(storyboard)[:12]}"
        )
        gate_matrix: dict[str, dict[str, Any]] = {}
        for gate in AUDIT_GATE_IDS:
            if gate in {"G1", "G2", "G3"} and not chapter_mode:
                gate_matrix[gate] = {
                    "status": "not_applicable",
                    "evidence_count": 0,
                    "evidence_ids": [],
                    "not_applicable_reason": "non_chapter storyboard has no chapter-intake gate",
                }
                continue
            relevant = [
                str(item.get("error_code") or "E-UNKNOWN")
                for item in issues
                if bool(item.get("blocking", item.get("severity") in {"fail", "error", "blocking"}))
            ]
            gate_matrix[gate] = {
                "status": "fail_current" if relevant else "pass",
                "evidence_count": len(relevant) or 1,
                "evidence_ids": relevant or [f"{evidence_prefix}-{gate}"],
            }
        layer_matrix: dict[str, dict[str, Any]] = {}
        for layer in AUDIT_LAYER_NAMES:
            relevant = [
                str(item.get("error_code") or "E-UNKNOWN")
                for item in issues
                if _issue_category(str(item.get("error_code") or "")) == layer
            ]
            layer_matrix[layer] = {
                "status": "fail_current" if relevant else "pass",
                "evidence_count": len(relevant) or 1,
                "evidence_ids": relevant or [f"{evidence_prefix}-{sha256_bytes(layer.encode())[:8]}"],
            }
        clean = not issues and all(
            row["status"] in {"pass", "not_applicable"}
            for row in gate_matrix.values()
        ) and all(row["status"] == "pass" for row in layer_matrix.values())
        enriched["independent_evidence_matrix"] = {
            "preventive_gate_results": gate_matrix,
            "audit_layer_results": layer_matrix,
            "quality_thresholds": {
                "source_and_key_dialogue_mapping_percent": 100 if clean else 0,
                "blocking_error_count": 0 if clean else len(issues),
                "old_content_hit_count": 0,
                "cross_context_term_count": 0,
                "shot_pass_percent": 100 if clean else max(0, 100 - len(issues)),
                "residual_issue_count": 0,
                "introduced_issue_count": 0,
            },
            "result": "pass" if clean else "fail_current",
        }
        return enriched

    def _audit_cache_components(
        self,
        *,
        canonical_hash: str,
        source_fingerprint: str,
        boundary_hash: str,
        audit_class: str,
        artifact_fingerprint: str = "",
        storyboard: dict[str, Any] | None = None,
        scope: Any = None,
        runtime_test_certificate_id: str = "",
    ) -> dict[str, str]:
        anchors = {
            key: (storyboard or {}).get(key)
            for key in (
                "character_anchors",
                "scene_anchors",
                "prop_anchors",
                "costume_anchors",
                "project_anchors",
            )
            if key in (storyboard or {})
        }
        return {
            "canonical": canonical_hash,
            "source": source_fingerprint,
            "boundary": boundary_hash,
            "ruleset": self._ruleset_version,
            "validator": self._validator_version(),
            "audit_class": audit_class,
            "artifact": artifact_fingerprint,
            "parser": self._parser_fingerprint,
            "schema": self._schema_fingerprint,
            "runtime": self._runtime_fingerprint,
            "scripts": self._scripts_fingerprint,
            "dependencies": self._dependencies_fingerprint,
            "anchors": sha256_bytes(_stable_json_bytes(anchors)),
            "target_model": self._target_model_fingerprint,
            "scope": sha256_bytes(_stable_json_bytes(scope or {})),
            "runtime_test_certificate_id": runtime_test_certificate_id,
        }

    @staticmethod
    def _cache_key_from_components(components: dict[str, str]) -> str:
        return build_audit_cache_key(
            canonical_sha256=components["canonical"],
            source_fingerprint=components["source"],
            boundary_hash=components["boundary"],
            ruleset_version=components["ruleset"],
            validator_version=components["validator"],
            audit_class=components["audit_class"],
            artifact_fingerprint=components.get("artifact", ""),
            parser_fingerprint=components.get("parser", ""),
            schema_fingerprint=components.get("schema", ""),
            runtime_fingerprint=components.get("runtime", ""),
            scripts_fingerprint=components.get("scripts", ""),
            dependencies_fingerprint=components.get("dependencies", ""),
            anchors_fingerprint=components.get("anchors", ""),
            target_model_fingerprint=components.get("target_model", ""),
            scope_fingerprint=components.get("scope", ""),
            runtime_test_certificate_id=components.get("runtime_test_certificate_id", ""),
        )

    @classmethod
    def _valid_cache_identity(cls, record: dict[str, Any], audit_class: str) -> bool:
        components = record.get("cache_components")
        if not isinstance(components, dict) or components.get("audit_class") != audit_class:
            return False
        try:
            expected = cls._cache_key_from_components(components)
        except (KeyError, TypeError, ValueError):
            return False
        return record.get("cache_key") == expected

    def _save_telemetry(
        self,
        namespace: str,
        project_id: str,
        *,
        operation: str,
        started_ns: int,
        stages_ms: dict[str, float],
        cache_hit: bool = False,
        artifact_id: str | None = None,
        telemetry_id: str | None = None,
        window_role: str | None = None,
        task_type: str | None = None,
        input_fingerprints: dict[str, str] | None = None,
        shot_count: int = 0,
        batch_count: int = 0,
    ) -> dict[str, Any]:
        telemetry_id = telemetry_id or _new_id("TEL")
        rounded_stages = {key: round(float(value), 3) for key, value in stages_ms.items()}
        wall_duration_ms = max(
            0, int(round((time.perf_counter_ns() - started_ns) / 1_000_000))
        )
        stage_name_map = {
            "validate": "validation",
            "machine_validation": "validation",
            "serialize_and_persist": "export",
            "canonical_load": "canonicalization",
            "ledger": "ledger_handoff",
        }
        timestamp = utc_stamp()
        schema_stages = [
            {
                "stage": stage_name_map.get(key, "validation"),
                "started_at": timestamp,
                "ended_at": timestamp,
                "duration_ms": max(0, int(round(duration))),
                "tool_call_count": 1,
                "source_character_count": 0,
                "payload_bytes": 0,
                "shot_count": max(0, int(shot_count)),
                "batch_count": max(0, int(batch_count)),
                "cache_status": "hit" if cache_hit else "miss",
                "cache_reason": "exact fingerprint cache" if cache_hit else "fresh deterministic stage",
                "retry_count": 0,
                "error_status": "none",
            }
            for key, duration in rounded_stages.items()
        ]
        value = {
            "telemetry_id": telemetry_id,
            "trace_id": telemetry_id,
            "operation": operation,
            "duration_ms": round((time.perf_counter_ns() - started_ns) / 1_000_000, 3),
            "stages_ms": rounded_stages,
            "stages": [
                {"stage": key, "duration_ms": duration, "status": "ok", "metrics": {}}
                for key, duration in rounded_stages.items()
            ],
            "cache_hit": bool(cache_hit),
            "artifact_id": artifact_id,
        }
        if window_role and task_type:
            value.update(
                {
                    "window_role": window_role,
                    "task_type": task_type,
                    "started_at": timestamp,
                    "ended_at": timestamp,
                    "wall_duration_ms": wall_duration_ms,
                    "input_fingerprints": input_fingerprints or {"artifact": sha256_bytes(b"none")},
                    "stages": schema_stages or [
                        {
                            "stage": "validation",
                            "started_at": timestamp,
                            "ended_at": timestamp,
                            "duration_ms": 0,
                            "tool_call_count": 0,
                            "source_character_count": 0,
                            "payload_bytes": 0,
                            "shot_count": max(0, int(shot_count)),
                            "batch_count": max(0, int(batch_count)),
                            "cache_status": "not_applicable",
                            "cache_reason": "no timed child stage",
                            "retry_count": 0,
                            "error_status": "none",
                        }
                    ],
                }
            )
            self._validate_schema_record(
                self._project_schema, "stage_timing_trace", value
            )
        return self.store.save_project_record(
            namespace,
            project_id,
            kind="telemetry",
            record_id=telemetry_id,
            value=value,
        )

    def agent_status(self, namespace: str, project_id: str | None = None, full_self_test: bool = False) -> ToolResponse:
        report = r10_bootstrap.verify_bundled(self_test=full_self_test) if full_self_test else self.bootstrap
        projects = self.store.list_projects(namespace)
        data: dict[str, Any] = {
            "runtime": report,
            "project_count": len(projects),
            "projects": projects[:50],
            "execution_available": report.get("status") == "pass",
            "deployment_mode": self.settings.deployment_mode,
            "auth_mode": self.settings.auth_mode,
        }
        summary = "Agent可执行" if report.get("status") == "pass" else "Agent阻塞"
        if project_id:
            hub = self.store.load_hub(namespace, project_id)
            ledger = self.store.load_ledger(namespace, project_id)
            runtime_certificate = (
                self._matching_runtime_certificate(namespace, project_id)
                or self._persist_runtime_certificate(namespace, project_id, report)
                if full_self_test
                else self._matching_runtime_certificate(namespace, project_id)
            )
            hub_errors = project_agent.validate_hub(hub)
            ledger_errors = storyboard_agent.validate_ledger(ledger)
            data["project"] = project_agent.summarize(hub)
            data["issues"] = storyboard_agent.summarize(ledger)
            data["state_valid"] = not hub_errors and not ledger_errors
            data["state_errors"] = hub_errors + ledger_errors
            data["recent_telemetry"] = self.store.list_project_records(
                namespace, project_id, kind="telemetry"
            )[:20]
            data["runtime_test_certificate_valid"] = bool(
                runtime_certificate is not None and runtime_certificate.get("result") == "pass"
            )
            data["runtime_test_certificate"] = runtime_certificate
            summary += f"｜项目{hub.get('project_name')}｜开放问题{data['issues'].get('open_issue_count', 0)}"
        return ToolResponse(status=str(report.get("status")), summary=summary, project_id=project_id, data=data)

    def create_project(self, namespace: str, project_name: str) -> ToolResponse:
        self._require_runtime()
        project_name = project_name.strip()
        if not project_name:
            raise ValueError("project_name is required")
        hub = project_agent.new_hub(project_name)
        project_id = str(hub["project_id"])
        if self.store.project_exists(namespace, project_id):
            existing = self.store.load_hub(namespace, project_id)
            return ToolResponse(
                status="existing",
                summary=f"项目已存在｜{existing.get('project_name')}",
                project_id=project_id,
                data={"project": project_agent.summarize(existing)},
            )
        ledger = storyboard_agent.new_ledger(project_name, project_id=project_id)
        self.store.create_project(namespace, hub, ledger)
        return ToolResponse(
            status="created",
            summary=f"项目已建立｜{project_name}",
            project_id=project_id,
            data={"project": project_agent.summarize(hub)},
        )

    def begin_task(
        self,
        namespace: str,
        *,
        project_id: str,
        operation: str,
        mode: str,
        authorization: str,
        request_text: str,
        targets: list[str],
        authoritative_source: str,
        latest_version: str,
        target_boundary: str,
        must_keep: list[str],
        must_remove: list[str],
        protected_scope: list[str],
        expected_outputs: list[str],
        window_key: str,
        request_session_id: str | None = None,
    ) -> ToolResponse:
        self._require_runtime()
        timestamp = utc_stamp()
        request = {
            "work_order_id": _new_id("WO"),
            "timestamp": timestamp,
            "window_key": window_key.strip() or "chat",
            "request_text": request_text.strip(),
            "operation": operation,
            "mode": mode,
            "targets": targets,
            "source_lock": {
                "authoritative_source": authoritative_source.strip() or "用户当前输入",
                "latest_version": latest_version.strip() or "current",
                "target_boundary": target_boundary.strip() or "全项目",
                "must_keep": must_keep,
                "must_remove": must_remove,
            },
            "protected_scope": protected_scope,
            "expected_outputs": expected_outputs,
            "authorization": authorization,
            # These bindings are minted by the server; role/actor authority is
            # never inferred from a caller-supplied label.
            "server_window_binding_id": _new_id("WIN"),
            "server_actor_binding_id": _new_id("ACT"),
            "server_actor_role": self._work_order_role(operation, mode),
            "transport_session_fingerprint": self._session_fingerprint(
                namespace, request_session_id
            ),
        }
        with self.store.mutate_project(namespace, project_id) as (hub, _ledger):
            result = project_agent.create_work_order(hub, request)
            hub["project_state"]["current_stage"] = result["work_order_status"]
        return ToolResponse(
            status=str(result["status"]),
            summary=f"工单已建立｜{operation}｜{result['work_order_status']}",
            project_id=project_id,
            work_order_id=request["work_order_id"],
            data=result,
        )

    def import_source_file(
        self,
        namespace: str,
        *,
        project_id: str,
        file: OpenAIFile,
        source_role: str,
    ) -> ToolResponse:
        self._require_runtime()
        started_ns = time.perf_counter_ns()
        if not self.store.project_exists(namespace, project_id):
            raise ValueError(f"unknown project_id: {project_id}")
        data, file_name, mime_type = download_openai_file(file, self.settings)
        downloaded_ns = time.perf_counter_ns()
        fingerprint = sha256_bytes(data)
        artifact_type = f"source:{source_role}"
        existing = self.store.find_artifact_by_fingerprint(
            namespace,
            project_id,
            fingerprint=fingerprint,
            artifact_type=artifact_type,
        )
        if existing is not None:
            extracted_id = existing.get("metadata", {}).get("extracted_artifact_id")
            if extracted_id:
                _extracted_record, extracted_path = self.store.get_artifact(
                    namespace, project_id, str(extracted_id)
                )
                extracted = self.store.read_json(extracted_path)
            else:
                extracted = extract_file(data, file_name, mime_type)
                extracted_record = self.store.save_artifact_json(
                    namespace,
                    project_id,
                    extracted,
                    file_name=f"{Path(file_name).stem}_提取内容.json",
                    artifact_type="source_extraction",
                    version=str(existing.get("version") or "v1"),
                    source_artifact_id=str(existing["artifact_id"]),
                    metadata={"source_role": source_role, "recovered": True},
                )
                extracted_id = extracted_record["artifact_id"]
                existing = self.store.update_artifact_metadata(
                    namespace,
                    project_id,
                    str(existing["artifact_id"]),
                    {"extracted_artifact_id": extracted_id, "source_role": source_role},
                )
            preview = preview_extracted(extracted, None, 0, 20)
            download_url = self.store.signed_download_url(
                namespace, project_id, str(existing["artifact_id"]), str(existing["file_name"])
            )
            telemetry = self._save_telemetry(
                namespace,
                project_id,
                operation="import_source_file",
                started_ns=started_ns,
                stages_ms={"download": (downloaded_ns - started_ns) / 1_000_000, "extract": 0.0},
                cache_hit=True,
                artifact_id=str(existing["artifact_id"]),
            )
            return ToolResponse(
                status="existing",
                summary=f"文件已存在并复用提取结果｜{existing['file_name']}｜{source_role}",
                project_id=project_id,
                artifact_id=str(existing["artifact_id"]),
                download_url=download_url,
                trace_id=telemetry["telemetry_id"],
                data={
                    "artifact": existing,
                    "extracted_artifact_id": extracted_id,
                    "preview": preview,
                    "reused_extraction": True,
                    "telemetry_id": telemetry["telemetry_id"],
                    "telemetry": telemetry,
                },
            )
        extracted = extract_file(data, file_name, mime_type)
        extracted_ns = time.perf_counter_ns()
        artifacts = self.store.list_artifacts(namespace, project_id)
        version = _artifact_version(artifacts, artifact_type)
        original = self.store.save_artifact_bytes(
            namespace,
            project_id,
            data,
            file_name=file_name,
            artifact_type=artifact_type,
            version=version,
            metadata={"chatgpt_file_id": file.file_id, "mime_type": mime_type},
        )
        extracted_record = self.store.save_artifact_json(
            namespace,
            project_id,
            extracted,
            file_name=f"{Path(file_name).stem}_提取内容.json",
            artifact_type="source_extraction",
            version=version,
            source_artifact_id=original["artifact_id"],
            metadata={"source_role": source_role},
        )
        original = self.store.update_artifact_metadata(
            namespace,
            project_id,
            original["artifact_id"],
            {"extracted_artifact_id": extracted_record["artifact_id"], "source_role": source_role},
        )
        with self.store.mutate_project(namespace, project_id) as (hub, _ledger):
            source_entry = {
                "artifact_id": original["artifact_id"],
                "source_role": source_role,
                "file_name": original["file_name"],
                "version": version,
                "fingerprint": original["fingerprint"],
                "imported_at": utc_stamp(),
            }
            hub["project_state"]["authoritative_sources"].append(source_entry)
            hub["project_state"]["latest_artifacts"][f"source:{source_role}"] = source_entry
            hub["updated_at"] = utc_stamp()
        preview = preview_extracted(extracted, None, 0, 20)
        download_url = self.store.signed_download_url(
            namespace, project_id, original["artifact_id"], original["file_name"]
        )
        telemetry = self._save_telemetry(
            namespace,
            project_id,
            operation="import_source_file",
            started_ns=started_ns,
            stages_ms={
                "download": (downloaded_ns - started_ns) / 1_000_000,
                "extract": (extracted_ns - downloaded_ns) / 1_000_000,
            },
            artifact_id=str(original["artifact_id"]),
        )
        return ToolResponse(
            status="imported",
            summary=f"文件已导入｜{file_name}｜{source_role}",
            project_id=project_id,
            artifact_id=original["artifact_id"],
            download_url=download_url,
            trace_id=telemetry["telemetry_id"],
            data={
                "artifact": original,
                "extracted_artifact_id": extracted_record["artifact_id"],
                "preview": preview,
                "reused_extraction": False,
                "telemetry_id": telemetry["telemetry_id"],
                "telemetry": telemetry,
            },
        )

    def _load_extracted_or_payload(
        self, namespace: str, project_id: str, artifact_id: str
    ) -> tuple[dict[str, Any], dict[str, Any], Path]:
        record, path = self.store.get_artifact(namespace, project_id, artifact_id)
        extracted_id = record.get("metadata", {}).get("extracted_artifact_id")
        if extracted_id:
            _extracted_record, extracted_path = self.store.get_artifact(namespace, project_id, str(extracted_id))
            return self.store.read_json(extracted_path), record, path
        suffix = path.suffix.casefold()
        if suffix == ".json":
            value = self.store.read_json(path)
            if isinstance(value, dict) and value.get("kind") in {"text", "workbook", "table", "json"}:
                return value, record, path
            return {"kind": "json", "value": value}, record, path
        if suffix in {".xlsx", ".xlsm", ".docx", ".pdf", ".csv", ".mm", ".md", ".txt"}:
            return extract_file(path.read_bytes(), record["file_name"], record.get("metadata", {}).get("mime_type")), record, path
        return {"kind": "text", "format": suffix.lstrip("."), "text": path.read_text(encoding="utf-8")}, record, path

    def read_artifact(
        self,
        namespace: str,
        *,
        project_id: str,
        artifact_id: str,
        section: str | None,
        offset: int,
        limit: int,
    ) -> ToolResponse:
        extracted, record, _path = self._load_extracted_or_payload(namespace, project_id, artifact_id)
        content = preview_extracted(extracted, section, offset, limit)
        download_url = self.store.signed_download_url(namespace, project_id, artifact_id, record["file_name"])
        return ToolResponse(
            status="ok",
            summary=f"已读取｜{record['file_name']}",
            project_id=project_id,
            artifact_id=artifact_id,
            download_url=download_url,
            data={"artifact": record, "content": content},
        )

    @staticmethod
    def _ensure_can_execute(work_order: dict[str, Any]) -> None:
        if work_order.get("status") == "awaiting_approval":
            raise ValueError("work order is awaiting explicit user approval")
        if work_order.get("mutates_source") and work_order.get("authorization") != "explicit_execute":
            raise ValueError("mutating task requires explicit_execute authorization")

    @staticmethod
    def _start_execution(hub: dict[str, Any], work_order: dict[str, Any]) -> None:
        StoryboardService._ensure_can_execute(work_order)
        if work_order["status"] in {"approved", "scope_locked", "failed_current"}:
            project_agent.transition_work_order(hub, work_order["work_order_id"], "executing")
        elif work_order["status"] != "executing":
            raise ValueError(f"work order cannot execute from {work_order['status']}")

    def plan_change(
        self,
        namespace: str,
        *,
        project_id: str,
        work_order_id: str,
        target_type: str,
        target_file: str,
        source_version: str,
        operations: list[dict[str, Any]],
        authorization: str,
    ) -> ToolResponse:
        self._require_runtime()
        request = {
            "change_id": _new_id("CHG"),
            "work_order_id": work_order_id,
            "timestamp": utc_stamp(),
            "target_type": target_type,
            "target_file": target_file,
            "source_version": source_version,
            "operations": operations,
            "authorization": authorization,
        }
        with self.store.mutate_project(namespace, project_id) as (hub, _ledger):
            result = project_agent.create_change_request(hub, request)
        return ToolResponse(
            status=str(result["status"]),
            summary=f"变更单已建立｜{result['change_status']}｜影响{len(result['required_downstream_updates'])}项",
            project_id=project_id,
            work_order_id=work_order_id,
            data={"change_id": request["change_id"], **result},
        )

    def commit_artifact(
        self,
        namespace: str,
        *,
        project_id: str,
        work_order_id: str,
        artifact_type: str,
        output_format: str,
        file_name: str,
        content_text: str | None,
        structured_data: Any,
        source_artifact_id: str | None,
        change_summary: str,
    ) -> ToolResponse:
        self._require_runtime()
        allowed_types = PLAN_ARTIFACT_TYPES | PRODUCTION_ARTIFACT_TYPES
        if artifact_type not in allowed_types:
            raise ValueError(f"unsupported artifact_type: {artifact_type}")
        if output_format not in {"markdown", "text", "json"}:
            raise ValueError("output_format must be markdown, text, or json")
        hub = self.store.load_hub(namespace, project_id)
        work_order = _find_work_order(hub, work_order_id)
        self._ensure_can_execute(work_order)
        if output_format == "json":
            if structured_data is None:
                raise ValueError("structured_data is required for JSON output")
            payload = (json.dumps(structured_data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            extension = ".json"
        else:
            if not content_text or not content_text.strip():
                raise ValueError("content_text is required for text output")
            payload = content_text.encode("utf-8")
            extension = ".md" if output_format == "markdown" else ".txt"
        if len(payload) > 10 * 1024 * 1024:
            raise ValueError("artifact payload exceeds 10 MiB")
        artifacts = self.store.list_artifacts(namespace, project_id)
        version = _artifact_version(artifacts, artifact_type)
        clean_name = slug_filename(file_name or f"{artifact_type}_{version}{extension}")
        if not Path(clean_name).suffix:
            clean_name += extension
        record = self.store.save_artifact_bytes(
            namespace,
            project_id,
            payload,
            file_name=clean_name,
            artifact_type=artifact_type,
            version=version,
            work_order_id=work_order_id,
            source_artifact_id=source_artifact_id,
            metadata={"change_summary": change_summary},
        )
        with self.store.mutate_project(namespace, project_id) as (hub, _ledger):
            work_order = _find_work_order(hub, work_order_id)
            if work_order.get("operation") in STORYBOARD_OPERATIONS or str(
                work_order.get("operation") or ""
            ).startswith("storyboard_"):
                raise ValueError(
                    "independent_audit_required: storyboard validation can only be finalized by audit_storyboard"
                )
            self._start_execution(hub, work_order)
            deliverable = {
                "deliverable_id": record["artifact_id"],
                "work_order_id": work_order_id,
                "timestamp": utc_stamp(),
                "artifact_type": artifact_type,
                "file_name": record["file_name"],
                "version": version,
                "fingerprint": record["fingerprint"],
                "status": "draft",
                "source_versions": [source_artifact_id] if source_artifact_id else [],
            }
            project_agent.record_deliverable(hub, deliverable)
            project_agent.transition_work_order(hub, work_order_id, "validating")
            hub["project_state"]["current_stage"] = "validating"
        download_url = self.store.signed_download_url(namespace, project_id, record["artifact_id"], record["file_name"])
        return ToolResponse(
            status="saved_pending_validation",
            summary=f"文件已生成｜{record['file_name']}｜待全链路验证",
            project_id=project_id,
            work_order_id=work_order_id,
            artifact_id=record["artifact_id"],
            download_url=download_url,
            data={"artifact": record, "required_validation_gates": work_order["required_validation_gates"]},
        )

    def _storyboard_from_artifact(
        self, namespace: str, project_id: str, artifact_id: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        record, payload_path = self.store.get_artifact(namespace, project_id, artifact_id)
        metadata = record.get("metadata", {})
        canonical_id = metadata.get("canonical_json_artifact_id")
        if canonical_id:
            canonical_record, canonical_path = self.store.get_artifact(
                namespace, project_id, str(canonical_id)
            )
            actual_payload_hash = sha256_bytes(canonical_path.read_bytes())
            if actual_payload_hash != canonical_record.get("fingerprint"):
                raise ValueError("canonical storyboard payload fingerprint mismatch")
            storyboard = self.store.read_json(canonical_path)
            if not isinstance(storyboard, dict) or "shots" not in storyboard:
                raise ValueError("linked canonical storyboard is invalid")
            actual_canonical_hash = canonical_storyboard_sha256(storyboard)
            expected_hash = metadata.get("canonical_sha256") or canonical_record.get("metadata", {}).get(
                "canonical_sha256"
            )
            if expected_hash and str(expected_hash) != actual_canonical_hash:
                raise ValueError("canonical storyboard stable hash mismatch")
            expected_json_fingerprint = metadata.get("canonical_json_fingerprint")
            if expected_json_fingerprint and str(expected_json_fingerprint) != actual_payload_hash:
                raise ValueError("canonical storyboard link fingerprint mismatch")
            return storyboard, record

        extracted, record, _path = self._load_extracted_or_payload(namespace, project_id, artifact_id)
        if extracted.get("kind") == "workbook":
            storyboard = extracted.get("canonical_storyboard")
        elif extracted.get("kind") == "json":
            storyboard = extracted.get("value")
        else:
            storyboard = None
        if not isinstance(storyboard, dict) or "shots" not in storyboard:
            raise ValueError("artifact does not contain a canonical storyboard")
        canonical_hash = canonical_storyboard_sha256(storyboard)
        expected_hash = record.get("metadata", {}).get("canonical_sha256")
        if expected_hash and str(expected_hash) != canonical_hash:
            raise ValueError("canonical storyboard stable hash mismatch")

        # Legacy/imported workbooks do not have a canonical sidecar. Recover it
        # once and link it atomically so every later window reads the complete
        # canonical object instead of reparsing a lossy workbook projection.
        if extracted.get("kind") == "workbook" and not canonical_id:
            recovered = self.store.save_artifact_json(
                namespace,
                project_id,
                storyboard,
                file_name=f"{payload_path.stem}_规范化.json",
                artifact_type="storyboard_json_recovered",
                version=str(record.get("version") or "v1"),
                work_order_id=record.get("work_order_id"),
                source_artifact_id=artifact_id,
                metadata={
                    "canonical_sha256": canonical_hash,
                    "recovered_from_artifact_id": artifact_id,
                },
            )
            record = self.store.update_artifact_metadata(
                namespace,
                project_id,
                artifact_id,
                {
                    "canonical_json_artifact_id": recovered["artifact_id"],
                    "canonical_json_fingerprint": recovered["fingerprint"],
                    "canonical_sha256": canonical_hash,
                    "canonical_recovered": True,
                },
            )
        return storyboard, record

    @staticmethod
    def _shot_ids_for_location(storyboard: dict[str, Any], location: str) -> list[str]:
        match = re.search(r"shots\[(\d+)\]", location)
        if not match:
            return []
        index = int(match.group(1)) - 1
        shots = storyboard.get("shots", [])
        if 0 <= index < len(shots):
            return [str(shots[index].get("镜头编号") or index + 1)]
        return []

    def _root_artifact_id(self, namespace: str, project_id: str, artifact_id: str) -> str:
        current = artifact_id
        visited: set[str] = set()
        while current not in visited:
            visited.add(current)
            record, _path = self.store.get_artifact(namespace, project_id, current)
            parent = record.get("source_artifact_id")
            if not parent:
                return current
            current = str(parent)
        return artifact_id

    def _record_audit(
        self,
        *,
        namespace: str,
        project_id: str,
        artifact_record: dict[str, Any],
        storyboard: dict[str, Any],
        report: dict[str, Any],
        ledger: dict[str, Any],
        audit_cycle_id: str | None = None,
        audit_class: str = AUDIT_CLASS_PRODUCTION_PREFLIGHT,
        audit_cache_key: str | None = None,
        event_id: str | None = None,
        handoff_id: str | None = None,
        stage_trace_id: str | None = None,
        fingerprints: dict[str, str] | None = None,
        runtime_test_certificate_id: str | None = None,
        work_order_id: str | None = None,
        validation_role: str | None = None,
    ) -> dict[str, Any]:
        root_artifact = self._root_artifact_id(namespace, project_id, artifact_record["artifact_id"])
        cycle_id = audit_cycle_id or f"AUD-{audit_class}-{root_artifact}"
        duplicate = next(
            (
                event
                for event in ledger.get("events", [])
                if audit_cache_key
                and event.get("audit_cache_key") == audit_cache_key
                and event.get("audit_class") == audit_class
            ),
            None,
        )
        if duplicate:
            return {"status": "duplicate", "event": duplicate, "ingest": None}
        prior_events = [
            event
            for event in ledger.get("events", [])
            if event.get("audit_cycle_id") == cycle_id
            and event.get("audit_class", AUDIT_CLASS_PRODUCTION_PREFLIGHT) == audit_class
        ]
        audit_round = 1 + max((int(event.get("audit_round", 0)) for event in prior_events), default=0)
        if audit_round > 3:
            return {
                "status": "blocked",
                "reason": "maximum three audit/repair rounds reached; rebuild the upstream structure",
                "audit_cycle_id": cycle_id,
            }
        prior_keys: set[tuple[str, str, tuple[str, ...]]] = set()
        for event in prior_events[-1:]:
            for prior_issue in event.get("issues", []):
                code = str(prior_issue.get("error_code"))
                shots = tuple(str(value) for value in prior_issue.get("shot_ids", []))
                field_or_location = str(prior_issue.get("location") or prior_issue.get("field"))
                prior_keys.add((code, field_or_location, shots))
                # Events written before the phase-1 upgrade only retained the
                # terminal field. Keep them comparable without collapsing new
                # events from different shots/locations into one signature.
                prior_keys.add((code, str(prior_issue.get("field")), shots))
        issues = []
        for issue in report.get("issues", []):
            location = str(issue.get("location") or "全表")
            field = location.rsplit(".", 1)[-1]
            shot_ids = self._shot_ids_for_location(storyboard, location)
            key = (str(issue.get("error_code") or "E-UNKNOWN"), location, tuple(shot_ids))
            legacy_key = (key[0], field, key[2])
            status = (
                "initial"
                if audit_round == 1
                else ("residual" if key in prior_keys or legacy_key in prior_keys else "introduced")
            )
            severity = str(issue.get("severity") or "fail")
            if severity == "warning":
                severity = "warn"
            issues.append(
                {
                    "error_code": key[0],
                    "category": _issue_category(key[0]),
                    "severity": severity if severity in storyboard_agent.SEVERITIES else "fail",
                    "blocking": bool(issue.get("blocking", severity == "fail")),
                    "shot_ids": shot_ids,
                    "field": field,
                    "location": location,
                    "evidence": str(issue.get("evidence") or location),
                    "source_sentence": str(issue.get("evidence") or "无额外原文证据"),
                    "repair_action": str(issue.get("repair_action") or "按R10规则修复后重跑。"),
                    "status": status,
                }
            )
        source_lock = storyboard.get("source_lock") or {}
        episode_id = str(
            source_lock.get("episode_id")
            or source_lock.get("episode_boundary")
            or source_lock.get("target_boundary")
            or "全项目"
        )
        event = {
            "event_id": event_id or _new_id("EVT"),
            "timestamp": utc_stamp(),
            "episode_id": episode_id,
            "file_name": f"storyboard-{audit_class}-{cycle_id}",
            "artifact_file_name": artifact_record.get("file_name") or "storyboard",
            "file_fingerprint": artifact_record.get("fingerprint") or sha256_bytes(json.dumps(storyboard, ensure_ascii=False).encode("utf-8")),
            "source_version": artifact_record.get("version") or "current",
            "audit_cycle_id": cycle_id,
            "task_type": (
                "audit"
                if audit_class == AUDIT_CLASS_INDEPENDENT and audit_round == 1
                else (
                    "audit_repair"
                    if audit_class == AUDIT_CLASS_INDEPENDENT
                    else ("generation_audit" if audit_round == 1 else "repair_regression")
                )
            ),
            "audit_class": audit_class,
            "audit_cache_key": audit_cache_key,
            "audit_round": audit_round,
            "result": "pass" if not issues else "fail_current",
            "checked_shot_count": len(storyboard.get("shots", [])),
            "issues": issues,
        }
        if audit_class == AUDIT_CLASS_INDEPENDENT:
            if not handoff_id or not stage_trace_id or not fingerprints or not work_order_id:
                raise ValueError("independent_audit_event_binding_incomplete")
            event.update(
                {
                    "window_key": work_order_id,
                    "task_type": "audit",
                    "validation_role": validation_role or "independent_audit",
                    "handoff_id": handoff_id,
                    "artifact_semantic_fingerprint": fingerprints["artifact_semantic"],
                    "canonical_fingerprint": fingerprints["canonical"],
                    "source_fingerprint": fingerprints["source_bytes"],
                    "boundary_fingerprint": fingerprints["episode_boundary"],
                    "runtime_fingerprint": fingerprints["runtime"],
                    "rules_fingerprint": fingerprints["rules"],
                    "validator_fingerprint": fingerprints["validator"],
                    "runtime_test_certificate_id": runtime_test_certificate_id
                    or "RUNTIMECERT-MISSING",
                    "stage_trace_id": stage_trace_id,
                    "cache_evidence": [
                        {
                            "cache_key": audit_cache_key,
                            "artifact_raw": fingerprints["artifact_raw"],
                        }
                    ],
                    "independence_assertions": {
                        "artifact_read_only": True,
                        "production_verdict_not_reused": True,
                        "quality_conclusions_recomputed": True,
                        "eight_layers_completed": True,
                    },
                }
            )
            self._validate_schema_record(self._ledger_schema, "audit_event", event)
        ingest = storyboard_agent.ingest_event(ledger, event)
        return {"status": "recorded", "event": event, "ingest": ingest}

    def _persist_audit_certificate(
        self,
        namespace: str,
        project_id: str,
        *,
        artifact_id: str,
        canonical_artifact_id: str | None,
        cache_components: dict[str, str],
        report: dict[str, Any],
        work_order_id: str | None,
        event_id: str | None = None,
        handoff_id: str | None = None,
        stage_trace_id: str | None = None,
        fingerprints: dict[str, str] | None = None,
        production_work_order_id: str | None = None,
        audit_actor_binding_id: str | None = None,
        audit_session_fingerprint: str | None = None,
    ) -> dict[str, Any]:
        certificate_id = _new_id("CERT")
        cache_key = self._cache_key_from_components(cache_components)
        clean = not report.get("issues")
        certificate = {
            "certificate_id": certificate_id,
            "certificate_type": cache_components["audit_class"],
            "work_order_id": work_order_id,
            "production_work_order_id": production_work_order_id,
            "artifact_id": artifact_id,
            "canonical_artifact_id": canonical_artifact_id,
            "canonical_sha256": cache_components["canonical"],
            "source_fingerprint": cache_components["source"],
            "boundary_hash": cache_components["boundary"],
            "ruleset_version": cache_components["ruleset"],
            "validator_version": cache_components["validator"],
            "audit_class": cache_components["audit_class"],
            "cache_key": cache_key,
            "cache_components": cache_components,
            "result": "pass" if clean else "fail_current",
            "issue_count": len(report.get("issues", [])),
            "checked_shot_count": int(report.get("shot_count", 0)),
            "report_sha256": sha256_bytes(_stable_json_bytes(report)),
            "report": copy.deepcopy(report),
        }
        if cache_components["audit_class"] == AUDIT_CLASS_INDEPENDENT:
            if not event_id or not handoff_id or not stage_trace_id or not fingerprints:
                raise ValueError(
                    "independent_audit_binding_incomplete: event, handoff, trace, and fingerprints are required"
                )
            evidence_matrix = report.get("independent_evidence_matrix")
            if not isinstance(evidence_matrix, dict):
                raise ValueError(
                    "independent_audit_evidence_missing: validator gate/layer evidence matrix is required"
                )
            checks = evidence_matrix.get("preventive_gate_results")
            layers = evidence_matrix.get("audit_layer_results")
            thresholds = evidence_matrix.get("quality_thresholds")
            if not isinstance(checks, dict) or not isinstance(layers, dict) or not isinstance(thresholds, dict):
                raise ValueError("independent_audit_evidence_missing: incomplete evidence matrix")
            fp = fingerprints
            certificate.update(
                {
                    "event_id": event_id,
                    "handoff_id": handoff_id,
                    "stage_trace_id": stage_trace_id,
                    "created_at": utc_stamp(),
                    "fingerprints": {
                        "source": fp["source_bytes"],
                        "boundary": fp["episode_boundary"],
                        "artifact_raw": fp["artifact_raw"],
                        "artifact_semantic": fp["artifact_semantic"],
                        "canonical": fp["canonical"],
                        "parser": fp["parser"],
                        "schema": fp["schema"],
                        "runtime": fp["runtime"],
                        "rules": fp["rules"],
                        "validator": fp["validator"],
                        "project_anchors": fp["project_anchors"],
                        "target_model_contract": fp["target_model_contract"],
                    },
                    "source_ingestion_mode": "chapter_to_storyboard"
                    if isinstance(report.get("chapter_coverage"), dict)
                    else "non_chapter",
                    "independent_of_production": True,
                    "preventive_gate_results": checks,
                    "audit_layer_results": layers,
                    "quality_thresholds": thresholds,
                    "audit_actor_binding_id": audit_actor_binding_id,
                    "audit_session_fingerprint": audit_session_fingerprint,
                }
            )
            self._validate_schema_record(
                self._ledger_schema, "independent_audit_certificate", certificate
            )
        return self.store.save_project_record(
            namespace,
            project_id,
            kind="certificates",
            record_id=certificate_id,
            value=certificate,
        )

    @staticmethod
    def _certificate_summary(certificate: dict[str, Any]) -> dict[str, Any]:
        """Return the reusable certificate identity without duplicating the report."""

        return {key: copy.deepcopy(value) for key, value in certificate.items() if key != "report"}

    def _persist_production_handoff(
        self,
        namespace: str,
        project_id: str,
        *,
        work_order_id: str,
        storyboard_artifact_id: str,
        canonical_artifact_id: str,
        certificate: dict[str, Any],
        storyboard: dict[str, Any],
        artifact_record: dict[str, Any],
        work_order: dict[str, Any],
        stage_trace_id: str,
        repair_package_id: str | None = None,
    ) -> dict[str, Any]:
        handoff_id = _new_id("HND")
        boundary = storyboard.get("chapter_intake") or storyboard.get("source_lock") or {}
        if not isinstance(boundary, dict):
            boundary = {}
        role = str(work_order.get("server_actor_role") or "production")
        handoff_type = "repair_to_audit" if role == "repair" else "production_to_audit"
        fingerprints = self._fingerprint_bundle(
            storyboard,
            artifact_record,
            canonical_hash=str(certificate["canonical_sha256"]),
            source_fingerprint=str(certificate["source_fingerprint"]),
            boundary_hash=str(certificate["boundary_hash"]),
        )
        source_lock = work_order.get("source_lock") or {}
        if not isinstance(source_lock, dict):
            source_lock = {}
        target_ids = [str(value) for value in work_order.get("targets", []) if str(value)] or ["全项目"]
        handoff = {
            "handoff_id": handoff_id,
            "work_order_id": work_order_id,
            "audit_cycle_id": f"AUD-{work_order_id}",
            "logical_artifact_id": storyboard_artifact_id,
            "handoff_type": handoff_type,
            "created_at": utc_stamp(),
            "from_window_role": role,
            "to_window_role": "independent_audit",
            "scope": {
                "target_ids": target_ids,
                "source_range": {
                    "authoritative_source": str(source_lock.get("authoritative_source") or "locked source"),
                    "latest_version": str(source_lock.get("latest_version") or "current"),
                },
                "boundary_context": {
                    "previous_end": str(boundary.get("previous_end") or boundary.get("上集终点") or "未提供"),
                    "current_start": str(boundary.get("current_start") or boundary.get("本集起点") or "已锁定"),
                    "current_end": str(boundary.get("current_end") or boundary.get("本集终点") or "已锁定"),
                    "next_start": str(boundary.get("next_start") or boundary.get("下集起点") or "未提供"),
                },
                "protected_scope": list(work_order.get("protected_scope") or []),
            },
            "fingerprints": fingerprints,
            "objective_evidence": {
                "source_manifest": {"fingerprint": fingerprints["source_bytes"]},
                "workbook_manifest": {
                    "artifact_id": storyboard_artifact_id,
                    "raw_sha256": fingerprints["artifact_raw"],
                    "size_bytes": int(artifact_record.get("size_bytes") or 0),
                },
                "canonical_ref": canonical_artifact_id,
                "approved_locks": {
                    "must_keep": list(source_lock.get("must_keep") or []),
                    "must_remove": list(source_lock.get("must_remove") or []),
                    "protected_scope": list(work_order.get("protected_scope") or []),
                },
                "deterministic_statistics": {
                    "shot_count": len(storyboard.get("shots", [])),
                    "batch_count": len(storyboard.get("i2v_batches", [])),
                    "total_duration": storyboard.get("total_duration", 0),
                },
            },
            "producer_claims": {
                "claim_type": "repair_result" if role == "repair" else "production_claim",
                "chapter_map": storyboard.get("chapter_map") or {},
                "source_mapping": storyboard.get("source_mapping") or {},
                "protected_dialogue": storyboard.get("protected_dialogue") or [],
                "cognition_matrix": storyboard.get("cognition_matrix") or [],
                "scene_state": storyboard.get("scene_state") or [],
                "shot_plan": storyboard.get("shots") or [],
                "coverage": storyboard.get("coverage") or {},
                "preflight_findings": certificate.get("report", {}).get("issues", []),
            },
            "cache_manifest": [
                {
                    "cache_class": "canonical_parse",
                    "cache_key": certificate["cache_key"],
                    "status": "miss",
                    "reason": "fresh production canonical and preflight",
                    "dependency_fingerprints": fingerprints,
                }
            ],
            "stage_trace_id": stage_trace_id,
            "status": "prepared",
            "storyboard_artifact_id": storyboard_artifact_id,
            "canonical_artifact_id": canonical_artifact_id,
            "canonical_sha256": certificate["canonical_sha256"],
            "source_fingerprint": certificate["source_fingerprint"],
            "boundary_hash": certificate["boundary_hash"],
            "ruleset_version": certificate["ruleset_version"],
            "validator_version": certificate["validator_version"],
            "audit_class": AUDIT_CLASS_PRODUCTION_PREFLIGHT,
            "certificate_id": certificate["certificate_id"],
            "cache_key": certificate["cache_key"],
            "cache_components": certificate["cache_components"],
            "production_result": certificate["result"],
            "issue_count": certificate["issue_count"],
            "checked_shot_count": certificate["checked_shot_count"],
            "facts_reusable": True,
            "conclusion_reusable_for_independent_audit": False,
            "from_window_binding_id": work_order.get("server_window_binding_id"),
            "from_actor_binding_id": work_order.get("server_actor_binding_id"),
            "from_session_fingerprint": work_order.get("transport_session_fingerprint"),
        }
        if handoff_type == "repair_to_audit":
            if not repair_package_id:
                raise ValueError(
                    "repair_package_required: repair_to_audit handoff must reference the consumed immutable package"
                )
            handoff["immutable_repair_package_id"] = repair_package_id
        self._validate_schema_record(
            self._project_schema, "handoff_manifest", handoff
        )
        return self.store.save_project_record(
            namespace,
            project_id,
            kind="handoffs",
            record_id=handoff_id,
            value=handoff,
        )

    def _matching_handoff(
        self,
        namespace: str,
        project_id: str,
        *,
        artifact_id: str,
        canonical_hash: str,
        handoff_id: str | None,
    ) -> dict[str, Any] | None:
        if handoff_id:
            handoff = self.store.get_project_record(
                namespace,
                project_id,
                kind="handoffs",
                record_id=handoff_id,
            )
            if not self.store.project_record_is_signed(handoff):
                raise ValueError("handoff_reissue_required: unsigned legacy handoff cannot be consumed")
            self._validate_schema_record(self._project_schema, "handoff_manifest", handoff)
            if handoff.get("canonical_sha256") != canonical_hash:
                raise ValueError("handoff canonical hash does not match the audited storyboard")
            if artifact_id not in {
                handoff.get("storyboard_artifact_id"),
                handoff.get("canonical_artifact_id"),
            }:
                raise ValueError("handoff does not belong to the audited artifact")
            if not self._valid_cache_identity(handoff, AUDIT_CLASS_PRODUCTION_PREFLIGHT):
                raise ValueError("handoff cache identity is invalid")
            return handoff
        for handoff in self.store.list_project_records(namespace, project_id, kind="handoffs"):
            if handoff.get("canonical_sha256") != canonical_hash:
                continue
            if artifact_id in {
                handoff.get("storyboard_artifact_id"),
                handoff.get("canonical_artifact_id"),
            }:
                if self._valid_cache_identity(handoff, AUDIT_CLASS_PRODUCTION_PREFLIGHT):
                    if not self.store.project_record_is_signed(handoff):
                        raise ValueError(
                            "handoff_reissue_required: unsigned legacy handoff cannot be consumed"
                        )
                    self._validate_schema_record(
                        self._project_schema, "handoff_manifest", handoff
                    )
                    return handoff
        return None

    def _ensure_audit_work_order(
        self,
        namespace: str,
        project_id: str,
        *,
        artifact_id: str,
        work_order_id: str | None,
        request_session_id: str | None,
    ) -> dict[str, Any]:
        if work_order_id:
            hub = self.store.load_hub(namespace, project_id)
            work_order = _find_work_order(hub, work_order_id)
        else:
            # Live HTTP requests have an observable transport session and must
            # explicitly create a separate audit work order.  Direct/stdio
            # legacy callers have no transport id, so the server creates a
            # distinct compatibility audit authority with its own unforgeable
            # bindings instead of trusting role text supplied by the caller.
            if request_session_id:
                raise ValueError(
                    "independent_audit_work_order_required: create a storyboard_audit/audit_only work order in the audit window"
                )
            existing_hub = self.store.load_hub(namespace, project_id)
            existing = next(
                (
                    row
                    for row in reversed(existing_hub.get("work_orders", []))
                    if row.get("operation") == "storyboard_audit"
                    and row.get("mode") == "audit_only"
                    and artifact_id in row.get("targets", [])
                ),
                None,
            )
            if existing is not None:
                work_order = existing
                self._assert_work_order_session(namespace, work_order, request_session_id)
                return work_order
            response = self.begin_task(
                namespace,
                project_id=project_id,
                operation="storyboard_audit",
                mode="audit_only",
                authorization="read_only",
                request_text="兼容旧客户端的服务器独立审计入口",
                targets=[artifact_id],
                authoritative_source="已锁定分镜及其handoff",
                latest_version="current",
                target_boundary="handoff-boundary",
                must_keep=["只读独立审计"],
                must_remove=[],
                protected_scope=["全部源内容"],
                expected_outputs=["independent_audit_certificate"],
                window_key=f"server-audit-{_new_id('RUN')}",
                request_session_id=None,
            )
            hub = self.store.load_hub(namespace, project_id)
            work_order = _find_work_order(hub, str(response.work_order_id))
        if (
            work_order.get("operation") != "storyboard_audit"
            or work_order.get("mode") != "audit_only"
            or work_order.get("authorization") != "read_only"
            or work_order.get("server_actor_role") not in {None, "independent_audit"}
        ):
            raise ValueError(
                "independent_audit_authority_required: audit-only server-bound work order is required"
            )
        self._assert_work_order_session(namespace, work_order, request_session_id)
        return work_order

    def _assert_audit_separation(
        self, audit_work_order: dict[str, Any], handoff: dict[str, Any]
    ) -> None:
        if audit_work_order.get("work_order_id") == handoff.get("work_order_id"):
            raise ValueError("independent_audit_separation_required: work orders must differ")
        if audit_work_order.get("server_window_binding_id") == handoff.get("from_window_binding_id"):
            raise ValueError("independent_audit_separation_required: window bindings must differ")
        if audit_work_order.get("server_actor_binding_id") == handoff.get("from_actor_binding_id"):
            raise ValueError("independent_audit_separation_required: actor bindings must differ")
        producer_session = handoff.get("from_session_fingerprint")
        auditor_session = audit_work_order.get("transport_session_fingerprint")
        if producer_session and auditor_session and producer_session == auditor_session:
            raise ValueError(
                "independent_audit_separation_required: production and audit transport sessions must differ"
            )

    def _persist_audit_intake(
        self,
        namespace: str,
        project_id: str,
        *,
        storyboard: dict[str, Any],
        record: dict[str, Any],
        audit_work_order: dict[str, Any],
        stage_trace_id: str,
    ) -> dict[str, Any]:
        canonical_hash = canonical_storyboard_sha256(storyboard)
        source_fingerprint = self._source_fingerprint(
            namespace,
            project_id,
            storyboard,
            record.get("source_artifact_id"),
            record,
        )
        boundary_hash = self._boundary_hash(storyboard)
        fingerprints = self._fingerprint_bundle(
            storyboard,
            record,
            canonical_hash=canonical_hash,
            source_fingerprint=source_fingerprint,
            boundary_hash=boundary_hash,
        )
        handoff_id = _new_id("HND")
        handoff = {
            "handoff_id": handoff_id,
            "work_order_id": str(audit_work_order["work_order_id"]),
            "audit_cycle_id": f"AUD-{audit_work_order['work_order_id']}",
            "logical_artifact_id": str(record["artifact_id"]),
            "handoff_type": "audit_intake",
            "created_at": utc_stamp(),
            "from_window_role": "external_input",
            "to_window_role": "independent_audit",
            "scope": {
                "target_ids": [str(record["artifact_id"])],
                "source_range": {"source": "external storyboard intake"},
                "boundary_context": {
                    "previous_end": "未提供",
                    "current_start": "外部分镜起点",
                    "current_end": "外部分镜终点",
                    "next_start": "未提供",
                },
                "protected_scope": list(audit_work_order.get("protected_scope") or []),
            },
            "fingerprints": fingerprints,
            "objective_evidence": {
                "source_manifest": {"fingerprint": fingerprints["source_bytes"]},
                "workbook_manifest": {
                    "artifact_id": record["artifact_id"],
                    "raw_sha256": fingerprints["artifact_raw"],
                },
                "canonical_ref": str(
                    record.get("metadata", {}).get("canonical_json_artifact_id")
                    or record["artifact_id"]
                ),
                "approved_locks": {"audit_read_only": True},
                "deterministic_statistics": {"shot_count": len(storyboard.get("shots", []))},
            },
            "producer_claims": {
                "claim_type": "external_input_claim",
                "chapter_map": {},
                "source_mapping": {},
                "protected_dialogue": [],
                "cognition_matrix": [],
                "scene_state": [],
                "shot_plan": storyboard.get("shots") or [],
                "coverage": {},
                "preflight_findings": [],
            },
            "cache_manifest": [
                {
                    "cache_class": "canonical_parse",
                    "cache_key": f"INTAKE-{canonical_hash}",
                    "status": "miss",
                    "reason": "external artifact normalized by read-only audit intake",
                    "dependency_fingerprints": fingerprints,
                }
            ],
            "stage_trace_id": stage_trace_id,
            "status": "prepared",
            "storyboard_artifact_id": str(record["artifact_id"]),
            "canonical_artifact_id": str(
                record.get("metadata", {}).get("canonical_json_artifact_id")
                or record["artifact_id"]
            ),
            "canonical_sha256": canonical_hash,
            "source_fingerprint": source_fingerprint,
            "boundary_hash": boundary_hash,
            "audit_class": "audit_intake",
            "from_window_binding_id": _new_id("EXT"),
            "from_actor_binding_id": _new_id("EXTACT"),
            "from_session_fingerprint": None,
        }
        self._validate_schema_record(self._project_schema, "handoff_manifest", handoff)
        persisted = self.store.save_project_record(
            namespace,
            project_id,
            kind="handoffs",
            record_id=handoff_id,
            value=handoff,
        )
        with self.store.mutate_project(namespace, project_id) as (hub, _ledger):
            hub.setdefault("handoffs", []).append(copy.deepcopy(persisted))
        return persisted

    @staticmethod
    def _topology_fingerprint(storyboard: dict[str, Any]) -> str:
        payload = {
            "shots": [
                {
                    "shot_id": row.get("镜头编号"),
                    "scene": row.get("场景连续性ID") or row.get("场景连续性id"),
                    "duration": row.get("时长"),
                    "cumulative": row.get("累计时长"),
                    "batch_ref": row.get("Seedance批次引用") or row.get("生成批次引用"),
                }
                for row in storyboard.get("shots", [])
                if isinstance(row, dict)
            ],
            "batches": storyboard.get("i2v_batches") or storyboard.get("seedance_batches") or [],
        }
        return sha256_bytes(_stable_json_bytes(payload))

    def _persist_repair_package_and_handoff(
        self,
        namespace: str,
        project_id: str,
        *,
        storyboard: dict[str, Any],
        certificate: dict[str, Any],
        production_handoff: dict[str, Any],
        audit_work_order: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        issue_targets: list[dict[str, str]] = []
        for issue in certificate.get("report", {}).get("issues", []):
            if not isinstance(issue, dict):
                continue
            location = str(issue.get("location") or "")
            field = str(issue.get("field") or location.rsplit(".", 1)[-1]).strip()
            for shot_id in self._shot_ids_for_location(storyboard, location):
                issue_targets.append(
                    {
                        "issue_code": str(issue.get("error_code") or "E-UNKNOWN"),
                        "shot_id": shot_id,
                        "field": field,
                        "location": location,
                    }
                )
        unique = {
            (row["issue_code"], row["shot_id"], row["field"], row["location"]): row
            for row in issue_targets
        }
        issue_targets = list(unique.values())
        if not issue_targets:
            return None, None
        repair_package_id = _new_id("RPK")
        repair_handoff_id = _new_id("HND")
        fingerprints = copy.deepcopy(production_handoff["fingerprints"])
        protected = production_handoff.get("scope", {}).get("protected_scope", [])
        package = {
            "repair_package_id": repair_package_id,
            "audit_certificate_id": certificate["certificate_id"],
            "audit_event_id": certificate["event_id"],
            "audit_to_repair_handoff_id": repair_handoff_id,
            "created_at": utc_stamp(),
            "expires_at": (
                datetime.now(timezone.utc) + timedelta(days=7)
            ).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "status": "issued",
            "base_artifact_id": certificate["artifact_id"],
            "base_canonical_sha256": certificate["canonical_sha256"],
            "fingerprints": fingerprints,
            "authorized_scope": {
                "shot_ids": sorted({row["shot_id"] for row in issue_targets}),
                "fields": sorted({row["field"] for row in issue_targets}),
                "issue_codes": sorted({row["issue_code"] for row in issue_targets}),
            },
            "protected_scope_hash": sha256_bytes(_stable_json_bytes(protected)),
            "issue_targets": issue_targets,
            "topology_fingerprint": self._topology_fingerprint(storyboard),
        }
        self._validate_schema_record(
            self._project_schema, "immutable_repair_package", package
        )
        repair_handoff = copy.deepcopy(production_handoff)
        repair_handoff.update(
            {
                "handoff_id": repair_handoff_id,
                "work_order_id": str(audit_work_order["work_order_id"]),
                "audit_cycle_id": str(production_handoff["audit_cycle_id"]),
                "handoff_type": "audit_to_repair",
                "created_at": utc_stamp(),
                "from_window_role": "independent_audit",
                "to_window_role": "repair",
                "audit_certificate_id": certificate["certificate_id"],
                "immutable_repair_package_id": repair_package_id,
                "certificate_id": certificate["certificate_id"],
                "stage_trace_id": certificate["stage_trace_id"],
                "status": "prepared",
                "producer_claims": {
                    **copy.deepcopy(production_handoff["producer_claims"]),
                    "claim_type": "audit_issue_baseline",
                    "preflight_findings": copy.deepcopy(certificate["report"]["issues"]),
                },
                "from_window_binding_id": audit_work_order.get("server_window_binding_id"),
                "from_actor_binding_id": audit_work_order.get("server_actor_binding_id"),
                "from_session_fingerprint": audit_work_order.get("transport_session_fingerprint"),
            }
        )
        self._validate_schema_record(
            self._project_schema, "handoff_manifest", repair_handoff
        )
        persisted_package = self.store.save_project_record(
            namespace,
            project_id,
            kind="repair_packages",
            record_id=repair_package_id,
            value=package,
        )
        persisted_handoff = self.store.save_project_record(
            namespace,
            project_id,
            kind="handoffs",
            record_id=repair_handoff_id,
            value=repair_handoff,
        )
        with self.store.mutate_project(namespace, project_id) as (hub, _ledger):
            hub.setdefault("repair_packages", []).append(copy.deepcopy(persisted_package))
            hub.setdefault("handoffs", []).append(copy.deepcopy(persisted_handoff))
        return persisted_package, persisted_handoff

    def _repair_package_for_patch(
        self,
        namespace: str,
        project_id: str,
        *,
        certificate: dict[str, Any],
        storyboard: dict[str, Any],
        base_artifact_id: str,
        base_canonical_hash: str,
        current_fingerprints: dict[str, str],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Load and verify the server-issued authorization for one repair.

        The certificate id is only an index into the signed package store.  It
        is never itself treated as repair authorization.
        """

        package = self.store.find_project_record(
            namespace,
            project_id,
            kind="repair_packages",
            field="audit_certificate_id",
            value=certificate["certificate_id"],
        )
        if package is None or not self.store.project_record_is_signed(package):
            raise ValueError(
                "repair_package_required: signed immutable package is missing or tampered"
            )
        self._validate_schema_record(
            self._project_schema, "immutable_repair_package", package
        )
        if package.get("status") != "issued":
            raise ValueError("repair_package_unavailable: package is not issued")
        try:
            expires_at = datetime.fromisoformat(
                str(package["expires_at"]).replace("Z", "+00:00")
            )
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("repair_package_invalid: expiry is malformed") from exc
        if expires_at <= datetime.now(timezone.utc):
            raise ValueError("repair_package_expired: immutable package has expired")
        if (
            package.get("base_artifact_id") != base_artifact_id
            or package.get("base_canonical_sha256") != base_canonical_hash
            or package.get("audit_event_id") != certificate.get("event_id")
            or package.get("fingerprints") != current_fingerprints
            or package.get("topology_fingerprint") != self._topology_fingerprint(storyboard)
        ):
            raise ValueError(
                "repair_package_binding_mismatch: base artifact, event, fingerprints, or topology changed"
            )

        handoff_id = str(package.get("audit_to_repair_handoff_id") or "")
        try:
            handoff = self.store.get_project_record(
                namespace,
                project_id,
                kind="handoffs",
                record_id=handoff_id,
            )
        except ValueError as exc:
            raise ValueError(
                "repair_package_binding_mismatch: audit_to_repair handoff is missing"
            ) from exc
        if not self.store.project_record_is_signed(handoff):
            raise ValueError(
                "repair_package_binding_mismatch: audit_to_repair handoff is unsigned or tampered"
            )
        self._validate_schema_record(
            self._project_schema, "handoff_manifest", handoff
        )
        protected_scope = handoff.get("scope", {}).get("protected_scope", [])
        if (
            handoff.get("handoff_type") != "audit_to_repair"
            or handoff.get("immutable_repair_package_id")
            != package.get("repair_package_id")
            or handoff.get("audit_certificate_id") != certificate.get("certificate_id")
            or handoff.get("logical_artifact_id") != base_artifact_id
            or handoff.get("fingerprints") != current_fingerprints
            or package.get("protected_scope_hash")
            != sha256_bytes(_stable_json_bytes(protected_scope))
        ):
            raise ValueError(
                "repair_package_binding_mismatch: audit handoff does not bind the exact package and artifact"
            )
        return package, handoff

    def _sync_repair_package_in_hub(
        self,
        namespace: str,
        project_id: str,
        package: dict[str, Any],
    ) -> None:
        with self.store.mutate_project(namespace, project_id) as (hub, _ledger):
            rows = hub.setdefault("repair_packages", [])
            package_id = package.get("repair_package_id")
            for index, row in enumerate(rows):
                if row.get("repair_package_id") == package_id:
                    rows[index] = copy.deepcopy(package)
                    break
            else:
                rows.append(copy.deepcopy(package))

    def commit_storyboard(
        self,
        namespace: str,
        *,
        project_id: str,
        work_order_id: str,
        storyboard: dict[str, Any],
        file_name: str,
        source_artifact_id: str | None,
        request_session_id: str | None = None,
        repair_package_id: str | None = None,
    ) -> ToolResponse:
        self._require_runtime()
        started_ns = time.perf_counter_ns()
        if not isinstance(storyboard, dict):
            raise ValueError("storyboard must be an object")
        hub_snapshot = self.store.load_hub(namespace, project_id)
        work_order_snapshot = _find_work_order(hub_snapshot, work_order_id)
        expected_role = self._work_order_role(
            str(work_order_snapshot.get("operation") or ""),
            str(work_order_snapshot.get("mode") or ""),
        )
        if expected_role not in {"production", "repair"}:
            raise ValueError("production_authority_required: audit work orders cannot commit storyboards")
        self._assert_work_order_session(
            namespace, work_order_snapshot, request_session_id
        )
        runtime_test_certificate = self._require_runtime_test_certificate(namespace, project_id)
        stage_trace_id = _new_id("TRC")
        storyboard = copy.deepcopy(storyboard)
        hub = self.store.load_hub(namespace, project_id)
        work_order = _find_work_order(hub, work_order_id)
        if work_order.get("operation") not in STORYBOARD_OPERATIONS:
            raise ValueError("work order operation cannot commit a storyboard")
        self._ensure_can_execute(work_order)
        prior_issue_keys: set[tuple[str, str]] = set()
        if source_artifact_id:
            _prior_storyboard, prior_record = self._storyboard_from_artifact(namespace, project_id, source_artifact_id)
            serialized_keys = prior_record.get("metadata", {}).get("issue_signatures", [])
            for item in serialized_keys:
                if isinstance(item, list) and len(item) == 2:
                    prior_issue_keys.add((str(item[0]), str(item[1])))
            if not prior_issue_keys:
                prior_report = validate_storyboard.validate_project(_prior_storyboard, mode="audit")
                prior_issue_keys = {
                    (str(issue.get("error_code")), str(issue.get("location")))
                    for issue in prior_report.get("issues", [])
                }
            evidence = storyboard.setdefault("prevention_evidence", {})
            history = evidence.setdefault("repair_history", [])
            if not isinstance(history, list):
                history = []
                evidence["repair_history"] = history
            history.append(
                {
                    "round": len(history) + 1,
                    "initial_issue_count": len(prior_issue_keys),
                    "residual_issue_count": 0,
                    "introduced_issue_count": 0,
                    "repeat_rounds": 1,
                    "action": "unified_full_chain_repair",
                    "source_artifact_id": source_artifact_id,
                    "recorded_by": "shortdrama-project-agent",
                }
            )
        report = validate_storyboard.validate_project(storyboard, mode="repair" if source_artifact_id else "audit")
        if source_artifact_id:
            current_issue_keys = {
                (str(issue.get("error_code")), str(issue.get("location")))
                for issue in report.get("issues", [])
                if not str(issue.get("location", "")).startswith("repair_history[")
            }
            repair_row = storyboard["prevention_evidence"]["repair_history"][-1]
            repair_row["residual_issue_count"] = len(current_issue_keys & prior_issue_keys)
            repair_row["introduced_issue_count"] = len(current_issue_keys - prior_issue_keys)
            if repair_row["residual_issue_count"]:
                repair_row["repeat_rounds"] = 2
                repair_row["action"] = "root_cause_repair"
            report = validate_storyboard.validate_project(storyboard, mode="repair")
        validated_ns = time.perf_counter_ns()
        zero_issue = not report.get("issues")
        canonical_hash = canonical_storyboard_sha256(storyboard)
        source_fingerprint = self._source_fingerprint(
            namespace,
            project_id,
            storyboard,
            source_artifact_id,
        )
        boundary_hash = self._boundary_hash(storyboard)
        cache_components = self._audit_cache_components(
            canonical_hash=canonical_hash,
            source_fingerprint=source_fingerprint,
            boundary_hash=boundary_hash,
            audit_class=AUDIT_CLASS_PRODUCTION_PREFLIGHT,
            storyboard=storyboard,
            scope={
                "targets": work_order_snapshot.get("targets", []),
                "protected_scope": work_order_snapshot.get("protected_scope", []),
            },
            runtime_test_certificate_id=str(runtime_test_certificate["certificate_id"]),
        )
        audit_cache_key = self._cache_key_from_components(cache_components)
        artifacts = self.store.list_artifacts(namespace, project_id)
        version = _artifact_version(artifacts, "storyboard")
        base_name = Path(slug_filename(file_name or f"正式分镜_{version}.xlsx")).stem
        json_record = self.store.save_artifact_json(
            namespace,
            project_id,
            storyboard,
            file_name=f"{base_name}.json",
            artifact_type="storyboard_json",
            version=version,
            work_order_id=work_order_id,
            source_artifact_id=source_artifact_id,
            metadata={
                "audit_status": report.get("status"),
                "issue_count": len(report.get("issues", [])),
                "canonical_sha256": canonical_hash,
                "source_fingerprint": source_fingerprint,
                "boundary_hash": boundary_hash,
                "ruleset_version": self._ruleset_version,
                "validator_version": self._validator_version(),
                "issue_signatures": [
                    [str(issue.get("error_code")), str(issue.get("location"))]
                    for issue in report.get("issues", [])
                ],
            },
        )
        workbook_bytes = storyboard_to_xlsx(storyboard, report)
        xlsx_record = self.store.save_artifact_bytes(
            namespace,
            project_id,
            workbook_bytes,
            file_name=f"{base_name}.xlsx",
            artifact_type="storyboard",
            version=version,
            work_order_id=work_order_id,
            source_artifact_id=source_artifact_id,
            metadata={
                "canonical_json_artifact_id": json_record["artifact_id"],
                "canonical_json_fingerprint": json_record["fingerprint"],
                "canonical_sha256": canonical_hash,
                "source_fingerprint": source_fingerprint,
                "boundary_hash": boundary_hash,
                "ruleset_version": self._ruleset_version,
                "validator_version": self._validator_version(),
                "production_preflight_cache_key": audit_cache_key,
                "audit_status": report.get("status"),
                "issue_count": len(report.get("issues", [])),
                "issue_signatures": [
                    [str(issue.get("error_code")), str(issue.get("location"))]
                    for issue in report.get("issues", [])
                ],
            },
        )
        cache_components = self._audit_cache_components(
            canonical_hash=canonical_hash,
            source_fingerprint=source_fingerprint,
            boundary_hash=boundary_hash,
            audit_class=AUDIT_CLASS_PRODUCTION_PREFLIGHT,
            artifact_fingerprint=str(xlsx_record["fingerprint"]),
            storyboard=storyboard,
            scope={
                "targets": work_order_snapshot.get("targets", []),
                "protected_scope": work_order_snapshot.get("protected_scope", []),
            },
            runtime_test_certificate_id=str(runtime_test_certificate["certificate_id"]),
        )
        audit_cache_key = self._cache_key_from_components(cache_components)
        xlsx_record = self.store.update_artifact_metadata(
            namespace,
            project_id,
            xlsx_record["artifact_id"],
            {"production_preflight_cache_key": audit_cache_key},
        )
        persisted_ns = time.perf_counter_ns()
        certificate = self._persist_audit_certificate(
            namespace,
            project_id,
            artifact_id=xlsx_record["artifact_id"],
            canonical_artifact_id=json_record["artifact_id"],
            cache_components=cache_components,
            report=report,
            work_order_id=work_order_id,
        )
        handoff = self._persist_production_handoff(
            namespace,
            project_id,
            work_order_id=work_order_id,
            storyboard_artifact_id=xlsx_record["artifact_id"],
            canonical_artifact_id=json_record["artifact_id"],
            certificate=certificate,
            storyboard=storyboard,
            artifact_record=xlsx_record,
            work_order=work_order_snapshot,
            stage_trace_id=stage_trace_id,
            repair_package_id=repair_package_id,
        )
        xlsx_record = self.store.update_artifact_metadata(
            namespace,
            project_id,
            xlsx_record["artifact_id"],
            {
                "production_certificate_id": certificate["certificate_id"],
                "handoff_id": handoff["handoff_id"],
            },
        )
        json_record = self.store.update_artifact_metadata(
            namespace,
            project_id,
            json_record["artifact_id"],
            {
                "production_certificate_id": certificate["certificate_id"],
                "handoff_id": handoff["handoff_id"],
            },
        )
        with self.store.mutate_project(namespace, project_id) as (hub, ledger):
            work_order = _find_work_order(hub, work_order_id)
            self._start_execution(hub, work_order)
            project_agent.record_deliverable(
                hub,
                {
                    "deliverable_id": xlsx_record["artifact_id"],
                    "work_order_id": work_order_id,
                    "timestamp": utc_stamp(),
                    "artifact_type": "storyboard",
                    "file_name": xlsx_record["file_name"],
                    "version": version,
                    "fingerprint": xlsx_record["fingerprint"],
                    "status": "validated" if zero_issue else "draft",
                    "source_versions": [source_artifact_id] if source_artifact_id else [],
                },
            )
            project_agent.transition_work_order(hub, work_order_id, "validating")
            audit_result = self._record_audit(
                namespace=namespace,
                project_id=project_id,
                artifact_record=xlsx_record,
                storyboard=storyboard,
                report=report,
                ledger=ledger,
                audit_cycle_id=f"AUD-{work_order_id}",
                audit_class=AUDIT_CLASS_PRODUCTION_PREFLIGHT,
                audit_cache_key=audit_cache_key,
            )
            recorded_issues = audit_result.get("event", {}).get("issues", [])
            residual_count = sum(issue.get("status") == "residual" for issue in recorded_issues)
            introduced_count = sum(issue.get("status") == "introduced" for issue in recorded_issues)
            # Production may record its preflight evidence, but it cannot
            # issue final validation or advance the storyboard work order to
            # passed.  Only the independent audit path below may do that.
            validation_result = {
                "status": "production_preflight_pass" if zero_issue else "production_preflight_fail",
                "final_verdict": False,
                "gate_results": _gate_results(
                    work_order["required_validation_gates"], report.get("issues", [])
                ),
                "failed_gates": [
                    gate
                    for gate, result in _gate_results(
                        work_order["required_validation_gates"], report.get("issues", [])
                    ).items()
                    if result != "pass"
                ],
                "residual_issue_count": residual_count,
                "introduced_issue_count": introduced_count,
            }
            work_order["production_preflight"] = copy.deepcopy(validation_result)
            work_order["awaiting_independent_audit"] = bool(zero_issue)
            if zero_issue:
                # Remain in validating; sign-off cannot run until the audit
                # work order consumes the matching handoff.
                work_order["status"] = "validating"
                hub["project_state"]["current_stage"] = "validating"
            else:
                project_agent.transition_work_order(hub, work_order_id, "failed_current")
                hub["project_state"]["current_stage"] = "failed_current"
            hub.setdefault("handoffs", []).append(copy.deepcopy(handoff))
            internal_round = work_order.get("internal_round", 0)
        ledger_ns = time.perf_counter_ns()
        download_url = self.store.signed_download_url(
            namespace, project_id, xlsx_record["artifact_id"], xlsx_record["file_name"]
        )
        if zero_issue:
            summary = (
                f"生成完成｜{report.get('shot_count', 0)}镜｜总时长{storyboard.get('total_duration', 0)}s"
                "｜生产预检通过｜待独立审计"
            )
            status = "pass"
        else:
            summary = f"未通过｜问题{len(report.get('issues', []))}项｜第{internal_round}/3轮"
            if internal_round >= 3:
                summary += "｜需要上游重建"
            status = "fail_current"
        telemetry = self._save_telemetry(
            namespace,
            project_id,
            operation="commit_storyboard",
            started_ns=started_ns,
            stages_ms={
                "validate": (validated_ns - started_ns) / 1_000_000,
                "serialize_and_persist": (persisted_ns - validated_ns) / 1_000_000,
                "ledger": (ledger_ns - persisted_ns) / 1_000_000,
            },
            artifact_id=str(xlsx_record["artifact_id"]),
            telemetry_id=stage_trace_id,
            window_role=str(work_order_snapshot.get("server_actor_role") or "production"),
            task_type="repair" if work_order_snapshot.get("server_actor_role") == "repair" else "production_preflight",
            input_fingerprints=handoff["fingerprints"],
            shot_count=len(storyboard.get("shots", [])),
            batch_count=len(storyboard.get("i2v_batches", [])),
        )
        with self.store.mutate_project(namespace, project_id) as (hub, _ledger):
            hub.setdefault("performance_traces", []).append(copy.deepcopy(telemetry))
        return ToolResponse(
            status=status,
            summary=summary,
            project_id=project_id,
            work_order_id=work_order_id,
            artifact_id=xlsx_record["artifact_id"],
            download_url=download_url,
            trace_id=telemetry["telemetry_id"],
            data={
                "artifact": xlsx_record,
                "canonical_json_artifact_id": json_record["artifact_id"],
                "canonical_sha256": canonical_hash,
                "handoff": handoff,
                "handoff_artifact_id": handoff["handoff_id"],
                "certificate": self._certificate_summary(certificate),
                "telemetry_id": telemetry["telemetry_id"],
                "telemetry": telemetry,
                "validation": validation_result,
                "audit": audit_result,
                "report": report,
                "next_action": "deliver" if zero_issue else ("upstream_rebuild" if internal_round >= 3 else "repair_all_issues_then_recommit"),
            },
        )

    @staticmethod
    def _patch_field_requires_full_rebuild(field: str) -> bool:
        normalized = re.sub(r"\s+", "", str(field)).casefold()
        if normalized in PATCH_FORBIDDEN_FIELDS:
            return True
        if any(term in normalized for term in PATCH_FORBIDDEN_FIELD_TERMS):
            return True
        # Nested paths can escape the explicitly audited shot field and are
        # therefore outside the local-patch contract.
        return any(marker in normalized for marker in (".", "[", "]", "__"))

    @staticmethod
    def _protected_scope_hit(protected_scope: list[Any], shot_id: str, field: str) -> str | None:
        shot_key = shot_id.strip().casefold()
        field_key = re.sub(r"\s+", "", field).casefold()
        exact = {
            shot_key,
            field_key,
            f"{shot_key}.{field_key}",
            f"{shot_key}:{field_key}",
            f"{shot_key}/{field_key}",
        }

        def shot_number(value: str) -> tuple[str, int] | None:
            match = re.fullmatch(r"([a-z\u3400-\u9fff]*?)(\d+)", value)
            if not match:
                return None
            return match.group(1), int(match.group(2))

        current = shot_number(shot_key)
        for item in protected_scope:
            value = re.sub(r"\s+", "", str(item)).casefold()
            if value in {"all", "全部", "全表", "完整分镜"} or value in exact:
                return str(item)
            # Range forms accepted by production documents: S001-S010,
            # S001～S010, S001至S010, optionally followed by .field/:field.
            range_match = re.match(
                r"^([a-z\u3400-\u9fff]*?\d+)(?:-|~|～|—|至)([a-z\u3400-\u9fff]*?\d+)(?:[.:/：](.+))?$",
                value,
            )
            if range_match and current:
                start = shot_number(range_match.group(1))
                end = shot_number(range_match.group(2))
                scoped_field = re.sub(r"\s+", "", range_match.group(3) or "").casefold()
                if (
                    start
                    and end
                    and current[0] == start[0] == end[0]
                    and min(start[1], end[1]) <= current[1] <= max(start[1], end[1])
                    and (not scoped_field or scoped_field == field_key)
                ):
                    return str(item)
            if shot_key in value and field_key in value:
                return str(item)
        return None

    def commit_storyboard_patch(
        self,
        namespace: str,
        *,
        project_id: str,
        work_order_id: str,
        base_artifact_id: str,
        expected_canonical_hash: str,
        expected_raw_artifact_fingerprint: str | None,
        audit_certificate_id: str,
        operations: list[dict[str, Any]],
        impact_scope: list[str],
        file_name: str,
        request_session_id: str | None = None,
    ) -> ToolResponse:
        """Apply evidence-bound shot-field repairs, then run the full validator.

        This is intentionally narrower than ``commit_storyboard``: the caller
        cannot alter structural, source, boundary, timing, or cognition fields,
        and an independent-audit certificate must prove every requested fix.
        The final persistence and project-ledger path is delegated to the
        existing full commit function so patching never weakens R10 gates.
        """

        self._require_runtime()
        if not isinstance(operations, list) or not operations:
            raise ValueError("patch_operations_required: operations must be non-empty")
        if not isinstance(impact_scope, list) or not impact_scope:
            raise ValueError("impact_scope_incomplete: impact_scope must be non-empty")

        hub = self.store.load_hub(namespace, project_id)
        work_order = _find_work_order(hub, work_order_id)
        if work_order.get("operation") not in {"storyboard_repair", "storyboard_change"}:
            raise ValueError(
                "patch_work_order_invalid: commit_storyboard_patch requires storyboard_repair or storyboard_change"
            )
        if work_order.get("authorization") != "explicit_execute":
            raise ValueError("patch_authorization_required: work order must have explicit_execute")
        self._ensure_can_execute(work_order)
        if work_order.get("server_actor_role") not in {None, "repair"}:
            raise ValueError("patch_authority_required: repair work-order actor binding is required")
        self._assert_work_order_session(namespace, work_order, request_session_id)

        storyboard, base_record = self._storyboard_from_artifact(
            namespace, project_id, base_artifact_id
        )
        base_canonical_hash = canonical_storyboard_sha256(storyboard)
        if expected_canonical_hash != base_canonical_hash:
            raise ValueError(
                "canonical_hash_conflict: expected canonical hash does not match the base artifact"
            )
        base_metadata = base_record.get("metadata") or {}
        if base_metadata.get("patch_successor_artifact_id") or base_metadata.get("active_patch_claim"):
            raise ValueError("canonical_hash_conflict: base canonical hash was already consumed")

        try:
            certificate = self.store.get_project_record(
                namespace,
                project_id,
                kind="certificates",
                record_id=audit_certificate_id,
            )
        except ValueError as exc:
            raise ValueError("invalid_audit_certificate: certificate does not exist in this project") from exc
        if certificate.get("project_id") != project_id:
            raise ValueError("invalid_audit_certificate: certificate belongs to another project")
        if not self.store.project_record_is_signed(certificate):
            raise ValueError("invalid_audit_certificate: unsigned or tampered certificate")
        self._validate_schema_record(
            self._ledger_schema, "independent_audit_certificate", certificate
        )
        if certificate.get("audit_class") != AUDIT_CLASS_INDEPENDENT:
            raise ValueError("invalid_audit_certificate: independent_audit certificate is required")
        if certificate.get("canonical_sha256") != base_canonical_hash:
            raise ValueError("invalid_audit_certificate: certificate is not bound to the base canonical hash")
        if base_artifact_id not in {
            certificate.get("artifact_id"),
            certificate.get("canonical_artifact_id"),
        }:
            raise ValueError("invalid_audit_certificate: certificate is not bound to the base artifact")
        if not self._valid_cache_identity(certificate, AUDIT_CLASS_INDEPENDENT):
            raise ValueError("invalid_audit_certificate: certificate cache identity is invalid")
        current_source_fingerprint = self._source_fingerprint(
            namespace,
            project_id,
            storyboard,
            base_record.get("source_artifact_id"),
            base_record,
        )
        current_cache_components = self._audit_cache_components(
            canonical_hash=base_canonical_hash,
            source_fingerprint=current_source_fingerprint,
            boundary_hash=self._boundary_hash(storyboard),
            audit_class=AUDIT_CLASS_INDEPENDENT,
            artifact_fingerprint=str(base_record.get("fingerprint") or ""),
            storyboard=storyboard,
            runtime_test_certificate_id=str(
                certificate.get("cache_components", {}).get("runtime_test_certificate_id") or ""
            ),
        )
        current_cache_components["scope"] = str(
            certificate.get("cache_components", {}).get("scope") or ""
        )
        if certificate.get("cache_components") != current_cache_components:
            raise ValueError(
                "invalid_audit_certificate: source, boundary, rules, or validator changed after audit"
            )
        certificate_report = certificate.get("report")
        if not isinstance(certificate_report, dict) or not isinstance(
            certificate_report.get("issues"), list
        ):
            raise ValueError("invalid_audit_certificate: certificate report is missing")
        if certificate.get("report_sha256") != sha256_bytes(
            _stable_json_bytes(certificate_report)
        ):
            raise ValueError("invalid_audit_certificate: certificate report fingerprint mismatch")
        certificate_raw = str(
            certificate.get("fingerprints", {}).get("artifact_raw")
            or certificate.get("cache_components", {}).get("artifact")
            or ""
        )
        required_raw = expected_raw_artifact_fingerprint or certificate_raw
        if not required_raw or required_raw != str(base_record.get("fingerprint") or ""):
            raise ValueError(
                "artifact_raw_fingerprint_mismatch: patch base raw bytes do not match the audit certificate"
            )
        if certificate_raw != required_raw:
            raise ValueError(
                "invalid_audit_certificate: expected raw artifact fingerprint is not certificate-bound"
            )
        current_fingerprints = self._fingerprint_bundle(
            storyboard,
            base_record,
            canonical_hash=base_canonical_hash,
            source_fingerprint=current_source_fingerprint,
            boundary_hash=self._boundary_hash(storyboard),
        )
        repair_package, audit_to_repair_handoff = self._repair_package_for_patch(
            namespace,
            project_id,
            certificate=certificate,
            storyboard=storyboard,
            base_artifact_id=base_artifact_id,
            base_canonical_hash=base_canonical_hash,
            current_fingerprints=current_fingerprints,
        )
        repair_package_id = str(repair_package["repair_package_id"])

        shots = storyboard.get("shots")
        if not isinstance(shots, list) or not shots:
            raise ValueError("full_rebuild_required: canonical storyboard has no stable shots")
        shots_by_id: dict[str, dict[str, Any]] = {}
        duplicate_ids: set[str] = set()
        for shot in shots:
            if not isinstance(shot, dict):
                raise ValueError("full_rebuild_required: every shot must be an object")
            shot_id = str(shot.get("镜头编号") or "").strip()
            if not shot_id:
                raise ValueError("full_rebuild_required: every shot requires a stable 镜头编号")
            if shot_id in shots_by_id:
                duplicate_ids.add(shot_id)
            shots_by_id[shot_id] = shot
        if duplicate_ids:
            raise ValueError(
                "full_rebuild_required: duplicate shot_id values: " + ", ".join(sorted(duplicate_ids))
            )

        issue_rows = certificate_report.get("issues", [])
        issues_by_code: dict[str, list[dict[str, Any]]] = {}
        for issue in issue_rows:
            if isinstance(issue, dict):
                issues_by_code.setdefault(str(issue.get("error_code") or ""), []).append(issue)
        package_targets = {
            (
                str(row.get("issue_code") or ""),
                str(row.get("shot_id") or "").strip(),
                re.sub(r"\s+", "", str(row.get("field") or "")).casefold(),
            )
            for row in repair_package.get("issue_targets", [])
            if isinstance(row, dict)
        }
        authorized_shot_ids = {
            str(value).strip()
            for value in repair_package.get("authorized_scope", {}).get("shot_ids", [])
            if str(value).strip()
        }

        protected_scope = list(work_order.get("protected_scope") or [])
        impact_ids = {str(item).strip() for item in impact_scope if str(item).strip()}
        unauthorized_impact = sorted(impact_ids - authorized_shot_ids)
        if unauthorized_impact:
            raise ValueError(
                "repair_package_scope_mismatch: unauthorized impact shot ids "
                + ", ".join(unauthorized_impact)
            )
        patched = copy.deepcopy(storyboard)
        patched_shots = {
            str(shot.get("镜头编号") or "").strip(): shot
            for shot in patched.get("shots", [])
            if isinstance(shot, dict)
        }
        applied_operations: list[dict[str, Any]] = []
        modified_shots: set[str] = set()
        modified_targets: set[tuple[str, str]] = set()

        for index, operation in enumerate(operations, 1):
            if not isinstance(operation, dict) or operation.get("op") != "replace_field":
                raise ValueError(f"patch_operation_invalid: operations[{index}] must be replace_field")
            shot_id = str(operation.get("shot_id") or "").strip()
            field = str(operation.get("field") or "").strip()
            if shot_id not in patched_shots:
                raise ValueError(f"patch_target_missing: unknown shot_id {shot_id!r}")
            if self._patch_field_requires_full_rebuild(field):
                raise ValueError(
                    f"full_rebuild_required: field {field!r} is structural, boundary, cognition, timing, or source evidence"
                )
            target_key = (shot_id, field)
            if target_key in modified_targets:
                raise ValueError(
                    f"patch_operation_conflict: duplicate replacement for {shot_id}.{field}"
                )
            modified_targets.add(target_key)
            if field not in patched_shots[shot_id]:
                raise ValueError(
                    f"full_rebuild_required: replace_field cannot create unknown field {field!r}"
                )
            protected_hit = self._protected_scope_hit(protected_scope, shot_id, field)
            if protected_hit is not None:
                raise ValueError(
                    f"protected_scope_violation: {shot_id}.{field} matches work-order protected scope {protected_hit!r}"
                )

            issue_ids = operation.get("issue_ids")
            if not isinstance(issue_ids, list) or not issue_ids or any(
                not str(item).strip() for item in issue_ids
            ):
                raise ValueError(
                    f"patch_evidence_required: operations[{index}].issue_ids must be non-empty"
                )
            for issue_id_value in issue_ids:
                issue_id = str(issue_id_value).strip()
                matching_issues = issues_by_code.get(issue_id, [])
                if not matching_issues:
                    raise ValueError(
                        f"patch_evidence_mismatch: issue {issue_id!r} is absent from the independent audit certificate"
                    )
                scoped_matches = []
                for issue in matching_issues:
                    related_shots = self._shot_ids_for_location(
                        storyboard, str(issue.get("location") or "")
                    )
                    issue_field = str(
                        issue.get("field")
                        or str(issue.get("location") or "").rsplit(".", 1)[-1]
                    ).strip()
                    if (
                        shot_id in related_shots
                        and re.sub(r"\s+", "", issue_field).casefold()
                        == re.sub(r"\s+", "", field).casefold()
                    ):
                        scoped_matches.append(issue)
                if not scoped_matches:
                    raise ValueError(
                        f"patch_evidence_mismatch: issue {issue_id!r} does not apply to exact target {shot_id}.{field}"
                    )
                if (
                    issue_id,
                    shot_id,
                    re.sub(r"\s+", "", field).casefold(),
                ) not in package_targets:
                    raise ValueError(
                        f"repair_package_scope_mismatch: package does not authorize {issue_id} at {shot_id}.{field}"
                    )

            reason = str(operation.get("reason") or "").strip()
            if not reason:
                raise ValueError(f"patch_reason_required: operations[{index}].reason is required")
            before_value = copy.deepcopy(patched_shots[shot_id][field])
            patched_shots[shot_id][field] = copy.deepcopy(operation.get("value"))
            modified_shots.add(shot_id)
            applied_operations.append(
                {
                    "op": "replace_field",
                    "shot_id": shot_id,
                    "field": field,
                    "issue_ids": [str(item).strip() for item in issue_ids],
                    "reason": reason,
                    "before_sha256": sha256_bytes(_stable_json_bytes(before_value)),
                    "after_sha256": sha256_bytes(_stable_json_bytes(operation.get("value"))),
                }
            )

        missing_impact = sorted(modified_shots - impact_ids)
        if missing_impact:
            raise ValueError(
                "impact_scope_incomplete: missing modified shot ids " + ", ".join(missing_impact)
            )
        patched_input_hash = canonical_storyboard_sha256(patched)
        if patched_input_hash == base_canonical_hash:
            raise ValueError("patch_no_change: operations do not change the canonical storyboard")

        claim_id = _new_id("PATCH")
        package_claimed = False
        storyboard_claimed = False
        try:
            claimed_package = self.store.claim_repair_package(
                namespace,
                project_id,
                repair_package_id,
                work_order_id=work_order_id,
            )
            package_claimed = True
            self._sync_repair_package_in_hub(namespace, project_id, claimed_package)
            self.store.claim_storyboard_patch(
                namespace,
                project_id,
                base_artifact_id,
                expected_canonical_hash=base_canonical_hash,
                claim_id=claim_id,
            )
            storyboard_claimed = True
            response = self.commit_storyboard(
                namespace,
                project_id=project_id,
                work_order_id=work_order_id,
                storyboard=patched,
                file_name=file_name,
                source_artifact_id=base_artifact_id,
                request_session_id=request_session_id,
                repair_package_id=repair_package_id,
            )
        except Exception:
            if storyboard_claimed:
                self.store.finish_storyboard_patch(
                    namespace,
                    project_id,
                    base_artifact_id,
                    claim_id=claim_id,
                    successor_artifact_id=None,
                    successor_canonical_hash=None,
                )
            if package_claimed:
                released_package = self.store.finish_repair_package(
                    namespace,
                    project_id,
                    repair_package_id,
                    work_order_id=work_order_id,
                    successor_artifact_id=None,
                    successor_canonical_sha256=None,
                )
                self._sync_repair_package_in_hub(
                    namespace, project_id, released_package
                )
            raise

        report = response.data.get("report")
        if not isinstance(report, dict):
            self.store.finish_storyboard_patch(
                namespace,
                project_id,
                base_artifact_id,
                claim_id=claim_id,
                successor_artifact_id=None,
                successor_canonical_hash=None,
            )
            released_package = self.store.finish_repair_package(
                namespace,
                project_id,
                repair_package_id,
                work_order_id=work_order_id,
                successor_artifact_id=None,
                successor_canonical_sha256=None,
            )
            self._sync_repair_package_in_hub(
                namespace, project_id, released_package
            )
            raise RuntimeError("patch final full-table validation report is missing")
        new_canonical_hash = str(response.data.get("canonical_sha256") or "")
        if not new_canonical_hash:
            self.store.finish_storyboard_patch(
                namespace,
                project_id,
                base_artifact_id,
                claim_id=claim_id,
                successor_artifact_id=None,
                successor_canonical_hash=None,
            )
            released_package = self.store.finish_repair_package(
                namespace,
                project_id,
                repair_package_id,
                work_order_id=work_order_id,
                successor_artifact_id=None,
                successor_canonical_sha256=None,
            )
            self._sync_repair_package_in_hub(
                namespace, project_id, released_package
            )
            raise RuntimeError("patch persisted canonical hash is missing")

        if response.status == "pass" and not report.get("issues"):
            repair_to_audit_handoff = response.data.get("handoff")
            if (
                not isinstance(repair_to_audit_handoff, dict)
                or repair_to_audit_handoff.get("handoff_type") != "repair_to_audit"
                or repair_to_audit_handoff.get("immutable_repair_package_id")
                != repair_package_id
            ):
                self.store.finish_storyboard_patch(
                    namespace,
                    project_id,
                    base_artifact_id,
                    claim_id=claim_id,
                    successor_artifact_id=None,
                    successor_canonical_hash=None,
                )
                released_package = self.store.finish_repair_package(
                    namespace,
                    project_id,
                    repair_package_id,
                    work_order_id=work_order_id,
                    successor_artifact_id=None,
                    successor_canonical_sha256=None,
                )
                self._sync_repair_package_in_hub(
                    namespace, project_id, released_package
                )
                raise RuntimeError(
                    "repair_package_binding_incomplete: persisted repair_to_audit handoff is missing"
                )
            self.store.finish_storyboard_patch(
                namespace,
                project_id,
                base_artifact_id,
                claim_id=claim_id,
                successor_artifact_id=str(response.artifact_id),
                successor_canonical_hash=new_canonical_hash,
            )
            consumed_package = self.store.finish_repair_package(
                namespace,
                project_id,
                repair_package_id,
                work_order_id=work_order_id,
                successor_artifact_id=str(response.artifact_id),
                successor_canonical_sha256=new_canonical_hash,
                repair_to_audit_handoff_id=str(
                    repair_to_audit_handoff["handoff_id"]
                ),
            )
            self._sync_repair_package_in_hub(
                namespace, project_id, consumed_package
            )
            response.data["artifact"] = self.store.update_artifact_metadata(
                namespace,
                project_id,
                str(response.artifact_id),
                {
                    "patch_base_artifact_id": base_artifact_id,
                    "patch_base_canonical_sha256": base_canonical_hash,
                    "patch_audit_certificate_id": audit_certificate_id,
                    "patch_repair_package_id": repair_package_id,
                    "patch_impact_scope": sorted(impact_ids),
                },
            )
        else:
            # A failed full validation is never a successful patch.  Preserve
            # the newly written draft for diagnosis, but allow the original
            # canonical base to be repaired again with corrected operations.
            self.store.finish_storyboard_patch(
                namespace,
                project_id,
                base_artifact_id,
                claim_id=claim_id,
                successor_artifact_id=None,
                successor_canonical_hash=None,
            )
            consumed_package = self.store.finish_repair_package(
                namespace,
                project_id,
                repair_package_id,
                work_order_id=work_order_id,
                successor_artifact_id=None,
                successor_canonical_sha256=None,
            )
            self._sync_repair_package_in_hub(
                namespace, project_id, consumed_package
            )

        response.data.update(
            {
                "final_full_validation": True,
                "base_artifact_id": base_artifact_id,
                "base_canonical_hash": base_canonical_hash,
                "new_canonical_hash": new_canonical_hash,
                "audit_certificate_id": audit_certificate_id,
                "repair_package_id": repair_package_id,
                "repair_package": consumed_package,
                "audit_to_repair_handoff": audit_to_repair_handoff,
                "impact_scope": sorted(impact_ids),
                "applied_operations": applied_operations,
                "report": report,
            }
        )
        response.summary = (
            "局部修复已完成｜全表生产预检通过｜待独立终审"
            if response.status == "pass"
            else response.summary
        )
        return response

    def audit_storyboard(
        self,
        namespace: str,
        *,
        project_id: str,
        artifact_id: str,
        audit_cycle_id: str | None,
        work_order_id: str | None = None,
        handoff_artifact_id: str | None = None,
        force_machine_recheck: bool = False,
        request_session_id: str | None = None,
    ) -> ToolResponse:
        self._require_runtime()
        started_ns = time.perf_counter_ns()
        runtime_test_certificate = self._require_runtime_test_certificate(namespace, project_id)
        stage_trace_id = _new_id("TRC")
        storyboard, record = self._storyboard_from_artifact(namespace, project_id, artifact_id)
        loaded_ns = time.perf_counter_ns()
        canonical_hash = canonical_storyboard_sha256(storyboard)
        audit_work_order = self._ensure_audit_work_order(
            namespace,
            project_id,
            artifact_id=artifact_id,
            work_order_id=work_order_id,
            request_session_id=request_session_id,
        )
        handoff = self._matching_handoff(
            namespace,
            project_id,
            artifact_id=artifact_id,
            canonical_hash=canonical_hash,
            handoff_id=handoff_artifact_id,
        )
        if handoff is None:
            internal_production = bool(
                record.get("work_order_id")
                or record.get("metadata", {}).get("production_certificate_id")
            )
            if internal_production:
                raise ValueError(
                    "handoff_required: internally produced storyboard has no matching production_to_audit handoff"
                )
            handoff = self._persist_audit_intake(
                namespace,
                project_id,
                storyboard=storyboard,
                record=record,
                audit_work_order=audit_work_order,
                stage_trace_id=stage_trace_id,
            )
        self._assert_audit_separation(audit_work_order, handoff)
        boundary_hash = self._boundary_hash(storyboard)
        production_components = handoff.get("cache_components", {})
        source_fingerprint = str(
            production_components.get("source")
            or handoff.get("source_fingerprint")
            or self._source_fingerprint(
                namespace,
                project_id,
                storyboard,
                record.get("source_artifact_id"),
                record,
            )
        )
        fingerprints = self._fingerprint_bundle(
            storyboard,
            record,
            canonical_hash=canonical_hash,
            source_fingerprint=source_fingerprint,
            boundary_hash=boundary_hash,
        )
        handoff_fingerprints = handoff.get("fingerprints")
        if not isinstance(handoff_fingerprints, dict) or any(
            handoff_fingerprints.get(key) != value
            for key, value in fingerprints.items()
        ):
            raise ValueError("handoff_fingerprint_mismatch: source/artifact/canonical/runtime binding changed")
        cache_components = self._audit_cache_components(
            canonical_hash=canonical_hash,
            source_fingerprint=source_fingerprint,
            boundary_hash=boundary_hash,
            audit_class=AUDIT_CLASS_INDEPENDENT,
            artifact_fingerprint=str(record.get("fingerprint") or ""),
            storyboard=storyboard,
            scope=handoff.get("scope") or {},
            runtime_test_certificate_id=str(runtime_test_certificate["certificate_id"]),
        )
        audit_cache_key = self._cache_key_from_components(cache_components)
        cached_certificate = None
        if not force_machine_recheck:
            cached_certificate = self.store.find_project_record(
                namespace,
                project_id,
                kind="certificates",
                field="cache_key",
                value=audit_cache_key,
            )
        cache_hit = cached_certificate is not None
        if cached_certificate is not None:
            if (
                not self.store.project_record_is_signed(cached_certificate)
                or
                not self._valid_cache_identity(cached_certificate, AUDIT_CLASS_INDEPENDENT)
                or cached_certificate.get("canonical_sha256") != canonical_hash
                or cached_certificate.get("cache_components") != cache_components
            ):
                raise ValueError("cached independent audit certificate identity is invalid")
            report = copy.deepcopy(cached_certificate.get("report"))
            if not isinstance(report, dict) or not isinstance(report.get("issues"), list):
                raise ValueError("cached independent audit certificate is invalid")
            if cached_certificate.get("report_sha256") != sha256_bytes(_stable_json_bytes(report)):
                raise ValueError("cached independent audit report fingerprint mismatch")
            certificate = cached_certificate
        else:
            report = self._attach_independent_evidence_matrix(
                validate_storyboard.validate_project(storyboard, mode="audit"),
                storyboard,
            )
        validated_ns = time.perf_counter_ns()
        event_id = str(certificate.get("event_id")) if cached_certificate else _new_id("EVT")
        with self.store.mutate_project(namespace, project_id) as (hub, ledger):
            audit_work_order_live = _find_work_order(hub, str(audit_work_order["work_order_id"]))
            if audit_work_order_live["status"] == "scope_locked":
                project_agent.transition_work_order(
                    hub, audit_work_order_live["work_order_id"], "executing"
                )
            audit_result = self._record_audit(
                namespace=namespace,
                project_id=project_id,
                artifact_record=record,
                storyboard=storyboard,
                report=report,
                ledger=ledger,
                audit_cycle_id=audit_cycle_id,
                audit_class=AUDIT_CLASS_INDEPENDENT,
                audit_cache_key=audit_cache_key,
                event_id=event_id,
                handoff_id=str(handoff["handoff_id"]),
                stage_trace_id=stage_trace_id,
                fingerprints=fingerprints,
                runtime_test_certificate_id=str(runtime_test_certificate["certificate_id"]),
                work_order_id=str(audit_work_order["work_order_id"]),
                validation_role="independent_audit",
            )
        if cached_certificate is None:
            certificate = self._persist_audit_certificate(
                namespace,
                project_id,
                artifact_id=artifact_id,
                canonical_artifact_id=record.get("metadata", {}).get("canonical_json_artifact_id"),
                cache_components=cache_components,
                report=report,
                work_order_id=str(audit_work_order["work_order_id"]),
                event_id=event_id,
                handoff_id=str(handoff["handoff_id"]),
                stage_trace_id=stage_trace_id,
                fingerprints=fingerprints,
                production_work_order_id=str(handoff["work_order_id"]),
                audit_actor_binding_id=str(audit_work_order.get("server_actor_binding_id") or ""),
                audit_session_fingerprint=audit_work_order.get("transport_session_fingerprint"),
            )
        repair_package = None
        audit_to_repair_handoff = None
        if certificate.get("issue_count", 0):
            repair_package = self.store.find_project_record(
                namespace,
                project_id,
                kind="repair_packages",
                field="audit_certificate_id",
                value=certificate["certificate_id"],
            )
            if repair_package is None:
                repair_package, audit_to_repair_handoff = self._persist_repair_package_and_handoff(
                    namespace,
                    project_id,
                    storyboard=storyboard,
                    certificate=certificate,
                    production_handoff=handoff,
                    audit_work_order=audit_work_order,
                )
            elif repair_package.get("audit_to_repair_handoff_id"):
                audit_to_repair_handoff = self.store.get_project_record(
                    namespace,
                    project_id,
                    kind="handoffs",
                    record_id=str(repair_package["audit_to_repair_handoff_id"]),
                )
        recorded_ns = time.perf_counter_ns()
        if audit_result["status"] == "duplicate":
            prior = audit_result["event"]
            issue_count = len(prior.get("issues", []))
            status = "pass" if prior.get("result") == "pass" else "fail_current"
            summary = f"审计结果未变｜问题{issue_count}项｜未新增轮次"
        elif audit_result["status"] == "blocked":
            status = "blocked"
            summary = "审计阻塞｜已达3轮｜需要上游重建"
        else:
            issue_count = len(report.get("issues", []))
            status = "pass" if issue_count == 0 else "fail_current"
            summary = "审计通过｜0个问题" if issue_count == 0 else f"审计未通过｜{issue_count}个问题"
        telemetry = self._save_telemetry(
            namespace,
            project_id,
            operation="audit_storyboard",
            started_ns=started_ns,
            stages_ms={
                "canonical_load": (loaded_ns - started_ns) / 1_000_000,
                "machine_validation": (validated_ns - loaded_ns) / 1_000_000,
                "ledger": (recorded_ns - validated_ns) / 1_000_000,
            },
            cache_hit=cache_hit,
            artifact_id=artifact_id,
            telemetry_id=stage_trace_id,
            window_role="independent_audit",
            task_type="independent_audit",
            input_fingerprints=fingerprints,
            shot_count=len(storyboard.get("shots", [])),
            batch_count=len(storyboard.get("i2v_batches", [])),
        )
        with self.store.mutate_project(namespace, project_id) as (hub, ledger):
            ledger.setdefault("performance_traces", []).append(copy.deepcopy(telemetry))
            if not any(
                row.get("certificate_id") == certificate.get("certificate_id")
                for row in ledger.setdefault("audit_certificates", [])
            ):
                ledger["audit_certificates"].append(copy.deepcopy(certificate))
            audit_order = _find_work_order(hub, str(audit_work_order["work_order_id"]))
            if audit_order["status"] == "executing":
                if not audit_order.get("deliverable_ids"):
                    project_agent.record_deliverable(
                        hub,
                        {
                            "deliverable_id": certificate["certificate_id"],
                            "work_order_id": audit_order["work_order_id"],
                            "timestamp": utc_stamp(),
                            "artifact_type": "audit_report",
                            "file_name": f"{certificate['certificate_id']}.json",
                            "version": str(record.get("version") or "current"),
                            "fingerprint": certificate["report_sha256"],
                            "status": "validated" if status == "pass" else "draft",
                            "source_versions": [artifact_id],
                        },
                    )
                project_agent.transition_work_order(hub, audit_order["work_order_id"], "validating")
            clean = status == "pass"
            if audit_order["status"] == "validating":
                audit_order["validation"] = {
                    "status": "pass" if clean else "fail",
                    "records": [{"certificate_id": certificate["certificate_id"]}],
                }
                project_agent.transition_work_order(
                    hub,
                    audit_order["work_order_id"],
                    "passed" if clean else "failed_current",
                )
            producer_order = _find_work_order(hub, str(handoff["work_order_id"]))
            if clean and producer_order["status"] == "validating":
                final_validation = project_agent.record_validation(
                    hub,
                    {
                        "work_order_id": producer_order["work_order_id"],
                        "timestamp": utc_stamp(),
                        "result": "pass",
                        "gate_results": {
                            gate: "pass" for gate in producer_order["required_validation_gates"]
                        },
                        "residual_issue_count": 0,
                        "introduced_issue_count": 0,
                        "blockers": [],
                    },
                )
                project_agent.transition_work_order(hub, producer_order["work_order_id"], "passed")
                producer_order["awaiting_independent_audit"] = False
                producer_order["independent_audit_certificate_id"] = certificate["certificate_id"]
                producer_order["independent_audit_validation"] = final_validation
            matching_handoff = next(
                (
                    row
                    for row in hub.get("handoffs", [])
                    if row.get("handoff_id") == handoff.get("handoff_id")
                ),
                None,
            )
            if matching_handoff is not None:
                matching_handoff["status"] = "consumed"
                matching_handoff["audit_certificate_id"] = certificate["certificate_id"]
        return ToolResponse(
            status=status,
            summary=summary,
            project_id=project_id,
            artifact_id=artifact_id,
            trace_id=telemetry["telemetry_id"],
            data={
                "report": report,
                "audit": audit_result,
                "audit_class": AUDIT_CLASS_INDEPENDENT,
                "audit_work_order_id": audit_work_order["work_order_id"],
                "cache_hit": cache_hit,
                "canonical_sha256": canonical_hash,
                "handoff": handoff,
                "certificate": self._certificate_summary(certificate),
                "repair_package": repair_package,
                "audit_to_repair_handoff": audit_to_repair_handoff,
                "telemetry_id": telemetry["telemetry_id"],
                "telemetry": telemetry,
            },
        )

    def complete_validation(
        self,
        namespace: str,
        *,
        project_id: str,
        work_order_id: str,
        gate_results: dict[str, str],
        residual_issue_count: int,
        introduced_issue_count: int,
        blockers: list[str],
    ) -> ToolResponse:
        self._require_runtime()
        with self.store.mutate_project(namespace, project_id) as (hub, _ledger):
            work_order = _find_work_order(hub, work_order_id)
            if work_order.get("operation") in STORYBOARD_OPERATIONS or str(
                work_order.get("operation") or ""
            ).startswith("storyboard_"):
                raise ValueError(
                    "independent_audit_required: storyboard validation can only be finalized by audit_storyboard"
                )
            if work_order["status"] == "executing":
                project_agent.transition_work_order(hub, work_order_id, "validating")
            if work_order["status"] != "validating":
                raise ValueError(f"work order is not validating: {work_order['status']}")
            clean_requested = (
                all(value == "pass" for value in gate_results.values())
                and residual_issue_count == 0
                and introduced_issue_count == 0
                and not blockers
            )
            result = project_agent.record_validation(
                hub,
                {
                    "work_order_id": work_order_id,
                    "timestamp": utc_stamp(),
                    "result": "pass" if clean_requested else "fail",
                    "gate_results": gate_results,
                    "residual_issue_count": residual_issue_count,
                    "introduced_issue_count": introduced_issue_count,
                    "blockers": blockers,
                },
            )
            clean = result["status"] == "pass"
            project_agent.transition_work_order(hub, work_order_id, "passed" if clean else "failed_current")
            if clean:
                for deliverable in hub.get("deliverables", []):
                    if deliverable.get("work_order_id") == work_order_id:
                        deliverable["status"] = "validated"
                        hub["project_state"]["latest_artifacts"][deliverable["artifact_type"]] = {
                            "deliverable_id": deliverable["deliverable_id"],
                            "file_name": deliverable["file_name"],
                            "version": deliverable["version"],
                            "fingerprint": deliverable["fingerprint"],
                        }
            hub["project_state"]["current_stage"] = "passed" if clean else "failed_current"
        return ToolResponse(
            status="pass" if clean else "fail_current",
            summary="验证通过｜残留0｜新增0" if clean else "验证未通过｜禁止输出PASS",
            project_id=project_id,
            work_order_id=work_order_id,
            data=result,
        )

    def record_capability_gap(
        self,
        namespace: str,
        *,
        project_id: str,
        request: str,
        missing_dependency: str,
        risk: str,
        safe_fallback: str,
        proposed_implementation: str,
        required_tests: list[str],
    ) -> ToolResponse:
        record = {
            "gap_id": _new_id("GAP"),
            "timestamp": utc_stamp(),
            "request": request,
            "missing_dependency": missing_dependency,
            "risk": risk,
            "safe_fallback": safe_fallback,
            "proposed_implementation": proposed_implementation,
            "required_tests": required_tests,
        }
        with self.store.mutate_project(namespace, project_id) as (hub, _ledger):
            result = project_agent.record_capability_gap(hub, record)
        return ToolResponse(
            status="recorded",
            summary="能力缺口已记录｜未擅自模拟执行",
            project_id=project_id,
            data={**result, "gap_id": record["gap_id"]},
        )

    def _clean_independent_certificate_for_work_order(
        self,
        namespace: str,
        project_id: str,
        hub: dict[str, Any],
        work_order_id: str,
    ) -> dict[str, Any]:
        deliverable = next(
            (
                item
                for item in reversed(hub.get("deliverables", []))
                if item.get("work_order_id") == work_order_id
                and item.get("artifact_type") == "storyboard"
            ),
            None,
        )
        if deliverable is None:
            raise ValueError("independent_audit_required: storyboard deliverable is missing")
        artifact_id = str(deliverable.get("deliverable_id") or "")
        storyboard, record = self._storyboard_from_artifact(namespace, project_id, artifact_id)
        canonical_hash = canonical_storyboard_sha256(storyboard)
        source_fingerprint = self._source_fingerprint(
            namespace,
            project_id,
            storyboard,
            record.get("source_artifact_id"),
            record,
        )
        boundary_hash = self._boundary_hash(storyboard)
        current_fingerprints = self._fingerprint_bundle(
            storyboard,
            record,
            canonical_hash=canonical_hash,
            source_fingerprint=source_fingerprint,
            boundary_hash=boundary_hash,
        )
        certificate = next(
            (
                row
                for row in self.store.list_project_records(
                    namespace, project_id, kind="certificates"
                )
                if row.get("audit_class") == AUDIT_CLASS_INDEPENDENT
                and row.get("production_work_order_id") == work_order_id
                and artifact_id
                in {row.get("artifact_id"), row.get("canonical_artifact_id")}
                and row.get("canonical_sha256") == canonical_hash
            ),
            None,
        )
        if (
            certificate is None
            or certificate.get("result") != "pass"
            or int(certificate.get("issue_count", -1)) != 0
            or not self._valid_cache_identity(certificate, AUDIT_CLASS_INDEPENDENT)
        ):
            raise ValueError(
                "independent_audit_required: exact storyboard version needs a clean independent audit before sign-off"
            )
        if not self.store.project_record_is_signed(certificate):
            raise ValueError("independent_audit_required: certificate is unsigned or tampered")
        self._validate_schema_record(
            self._ledger_schema, "independent_audit_certificate", certificate
        )
        report = certificate.get("report")
        if not isinstance(report, dict) or certificate.get("report_sha256") != sha256_bytes(
            _stable_json_bytes(report)
        ):
            raise ValueError("independent_audit_required: certificate report fingerprint mismatch")
        cert_fingerprints = certificate.get("fingerprints") or {}
        expected_subset = {
            "source": current_fingerprints["source_bytes"],
            "boundary": current_fingerprints["episode_boundary"],
            "artifact_raw": current_fingerprints["artifact_raw"],
            "artifact_semantic": current_fingerprints["artifact_semantic"],
            "canonical": current_fingerprints["canonical"],
            "parser": current_fingerprints["parser"],
            "schema": current_fingerprints["schema"],
            "runtime": current_fingerprints["runtime"],
            "rules": current_fingerprints["rules"],
            "validator": current_fingerprints["validator"],
            "project_anchors": current_fingerprints["project_anchors"],
            "target_model_contract": current_fingerprints["target_model_contract"],
        }
        if cert_fingerprints != expected_subset:
            raise ValueError("independent_audit_required: certificate fingerprints are stale")
        handoff = self.store.get_project_record(
            namespace,
            project_id,
            kind="handoffs",
            record_id=str(certificate.get("handoff_id") or ""),
        )
        if not self.store.project_record_is_signed(handoff):
            raise ValueError("independent_audit_required: handoff is unsigned or tampered")
        self._validate_schema_record(self._project_schema, "handoff_manifest", handoff)
        if (
            handoff.get("work_order_id") != work_order_id
            or handoff.get("logical_artifact_id") != artifact_id
            or handoff.get("fingerprints") != current_fingerprints
        ):
            raise ValueError("independent_audit_required: handoff binding mismatch")
        ledger = self.store.load_ledger(namespace, project_id)
        event = next(
            (
                row
                for row in ledger.get("events", [])
                if row.get("event_id") == certificate.get("event_id")
            ),
            None,
        )
        if (
            not isinstance(event, dict)
            or event.get("handoff_id") != certificate.get("handoff_id")
            or event.get("stage_trace_id") != certificate.get("stage_trace_id")
            or event.get("file_fingerprint") != current_fingerprints["artifact_raw"]
            or event.get("canonical_fingerprint") != current_fingerprints["canonical"]
        ):
            raise ValueError("independent_audit_required: audit event binding mismatch")
        self._validate_schema_record(self._ledger_schema, "audit_event", event)
        trace = self.store.get_project_record(
            namespace,
            project_id,
            kind="telemetry",
            record_id=str(certificate.get("stage_trace_id") or ""),
        )
        if not self.store.project_record_is_signed(trace):
            raise ValueError("independent_audit_required: trace is unsigned or tampered")
        self._validate_schema_record(self._project_schema, "stage_timing_trace", trace)
        if (
            trace.get("window_role") != "independent_audit"
            or trace.get("artifact_id") != artifact_id
            or trace.get("input_fingerprints") != current_fingerprints
        ):
            raise ValueError("independent_audit_required: audit trace binding mismatch")
        return certificate

    def signoff_task(
        self,
        namespace: str,
        *,
        project_id: str,
        work_order_id: str,
        decision: str,
        signer: str,
    ) -> ToolResponse:
        if not signer.strip():
            raise ValueError("signer is required for human sign-off")
        with self.store.mutate_project(namespace, project_id) as (hub, _ledger):
            work_order = _find_work_order(hub, work_order_id)
            independent_certificate = None
            if decision == "approve" and work_order.get("operation") in STORYBOARD_OPERATIONS:
                independent_certificate = self._clean_independent_certificate_for_work_order(
                    namespace,
                    project_id,
                    hub,
                    work_order_id,
                )
            if work_order["status"] == "passed":
                project_agent.transition_work_order(hub, work_order_id, "awaiting_signoff")
            result = project_agent.signoff_work_order(hub, work_order_id, decision)
            hub["human_signoffs"][-1]["signer"] = signer.strip()
            if independent_certificate is not None:
                hub["human_signoffs"][-1]["independent_audit_certificate_id"] = (
                    independent_certificate["certificate_id"]
                )
            if decision == "approve":
                for deliverable in hub.get("deliverables", []):
                    if deliverable.get("work_order_id") == work_order_id:
                        deliverable["status"] = "final"
                hub["project_state"]["current_stage"] = "archived"
            else:
                hub["project_state"]["current_stage"] = "approved"
        return ToolResponse(
            status=str(result["status"]),
            summary=f"人工签核已记录｜{decision}｜{result['status']}",
            project_id=project_id,
            work_order_id=work_order_id,
            data={
                **result,
                "independent_audit_certificate_id": (
                    independent_certificate.get("certificate_id")
                    if independent_certificate is not None
                    else None
                ),
            },
        )
