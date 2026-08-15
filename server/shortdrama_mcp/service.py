from __future__ import annotations

import copy
import json
import re
import sys
import uuid
from pathlib import Path
from typing import Any

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
    if any(term in code for term in ("SOURCE", "DIALOGUE", "COGNITION", "CHANGELOCK")):
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

    def _require_runtime(self) -> None:
        if self.bootstrap.get("status") != "pass":
            raise RuntimeError("R10 runtime bootstrap failed; execution is blocked")

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
            hub_errors = project_agent.validate_hub(hub)
            ledger_errors = storyboard_agent.validate_ledger(ledger)
            data["project"] = project_agent.summarize(hub)
            data["issues"] = storyboard_agent.summarize(ledger)
            data["state_valid"] = not hub_errors and not ledger_errors
            data["state_errors"] = hub_errors + ledger_errors
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
        if not self.store.project_exists(namespace, project_id):
            raise ValueError(f"unknown project_id: {project_id}")
        data, file_name, mime_type = download_openai_file(file, self.settings)
        extracted = extract_file(data, file_name, mime_type)
        artifacts = self.store.list_artifacts(namespace, project_id)
        version = _artifact_version(artifacts, f"source:{source_role}")
        original = self.store.save_artifact_bytes(
            namespace,
            project_id,
            data,
            file_name=file_name,
            artifact_type=f"source:{source_role}",
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
        self.store.update_artifact_metadata(
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
        return ToolResponse(
            status="imported",
            summary=f"文件已导入｜{file_name}｜{source_role}",
            project_id=project_id,
            artifact_id=original["artifact_id"],
            download_url=download_url,
            data={
                "artifact": original,
                "extracted_artifact_id": extracted_record["artifact_id"],
                "preview": preview,
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
        extracted, record, _path = self._load_extracted_or_payload(namespace, project_id, artifact_id)
        if extracted.get("kind") == "workbook":
            storyboard = extracted.get("canonical_storyboard")
        elif extracted.get("kind") == "json":
            storyboard = extracted.get("value")
        else:
            storyboard = None
        if not isinstance(storyboard, dict) or "shots" not in storyboard:
            raise ValueError("artifact does not contain a canonical storyboard")
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
    ) -> dict[str, Any]:
        root_artifact = self._root_artifact_id(namespace, project_id, artifact_record["artifact_id"])
        cycle_id = audit_cycle_id or f"AUD-{root_artifact}"
        duplicate = next(
            (
                event
                for event in ledger.get("events", [])
                if event.get("file_fingerprint") == artifact_record.get("fingerprint")
            ),
            None,
        )
        if duplicate:
            return {"status": "duplicate", "event": duplicate, "ingest": None}
        prior_events = [event for event in ledger.get("events", []) if event.get("audit_cycle_id") == cycle_id]
        audit_round = 1 + max((int(event.get("audit_round", 0)) for event in prior_events), default=0)
        if audit_round > 3:
            return {
                "status": "blocked",
                "reason": "maximum three audit/repair rounds reached; rebuild the upstream structure",
                "audit_cycle_id": cycle_id,
            }
        prior_keys = {
            (str(issue.get("error_code")), str(issue.get("field")))
            for event in prior_events[-1:]
            for issue in event.get("issues", [])
        }
        issues = []
        for issue in report.get("issues", []):
            location = str(issue.get("location") or "全表")
            field = location.rsplit(".", 1)[-1]
            key = (str(issue.get("error_code") or "E-UNKNOWN"), field)
            status = "initial" if audit_round == 1 else ("residual" if key in prior_keys else "introduced")
            severity = str(issue.get("severity") or "fail")
            if severity == "warning":
                severity = "warn"
            issues.append(
                {
                    "error_code": key[0],
                    "category": _issue_category(key[0]),
                    "severity": severity if severity in storyboard_agent.SEVERITIES else "fail",
                    "blocking": bool(issue.get("blocking", severity == "fail")),
                    "shot_ids": self._shot_ids_for_location(storyboard, location),
                    "field": field,
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
            "event_id": _new_id("EVT"),
            "timestamp": utc_stamp(),
            "episode_id": episode_id,
            "file_name": f"storyboard-{cycle_id}",
            "artifact_file_name": artifact_record.get("file_name") or "storyboard",
            "file_fingerprint": artifact_record.get("fingerprint") or sha256_bytes(json.dumps(storyboard, ensure_ascii=False).encode("utf-8")),
            "source_version": artifact_record.get("version") or "current",
            "audit_cycle_id": cycle_id,
            "task_type": "generation_audit" if audit_round == 1 else "repair_regression",
            "audit_round": audit_round,
            "result": "pass" if not issues else "fail_current",
            "checked_shot_count": len(storyboard.get("shots", [])),
            "issues": issues,
        }
        ingest = storyboard_agent.ingest_event(ledger, event)
        return {"status": "recorded", "event": event, "ingest": ingest}

    def commit_storyboard(
        self,
        namespace: str,
        *,
        project_id: str,
        work_order_id: str,
        storyboard: dict[str, Any],
        file_name: str,
        source_artifact_id: str | None,
    ) -> ToolResponse:
        self._require_runtime()
        if not isinstance(storyboard, dict):
            raise ValueError("storyboard must be an object")
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
        zero_issue = not report.get("issues")
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
                "audit_status": report.get("status"),
                "issue_count": len(report.get("issues", [])),
                "issue_signatures": [
                    [str(issue.get("error_code")), str(issue.get("location"))]
                    for issue in report.get("issues", [])
                ],
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
            )
            recorded_issues = audit_result.get("event", {}).get("issues", [])
            residual_count = sum(issue.get("status") == "residual" for issue in recorded_issues)
            introduced_count = sum(issue.get("status") == "introduced" for issue in recorded_issues)
            validation_result = project_agent.record_validation(
                hub,
                {
                    "work_order_id": work_order_id,
                    "timestamp": utc_stamp(),
                    "result": "pass" if zero_issue else "fail",
                    "gate_results": _gate_results(work_order["required_validation_gates"], report.get("issues", [])),
                    "residual_issue_count": residual_count,
                    "introduced_issue_count": introduced_count,
                    "blockers": [],
                },
            )
            project_agent.transition_work_order(hub, work_order_id, "passed" if zero_issue else "failed_current")
            hub["project_state"]["current_stage"] = "passed" if zero_issue else "failed_current"
            internal_round = work_order.get("internal_round", 0)
        download_url = self.store.signed_download_url(
            namespace, project_id, xlsx_record["artifact_id"], xlsx_record["file_name"]
        )
        if zero_issue:
            summary = f"生成完成｜{report.get('shot_count', 0)}镜｜总时长{storyboard.get('total_duration', 0)}s｜审计通过"
            status = "pass"
        else:
            summary = f"未通过｜问题{len(report.get('issues', []))}项｜第{internal_round}/3轮"
            if internal_round >= 3:
                summary += "｜需要上游重建"
            status = "fail_current"
        return ToolResponse(
            status=status,
            summary=summary,
            project_id=project_id,
            work_order_id=work_order_id,
            artifact_id=xlsx_record["artifact_id"],
            download_url=download_url,
            data={
                "artifact": xlsx_record,
                "canonical_json_artifact_id": json_record["artifact_id"],
                "validation": validation_result,
                "audit": audit_result,
                "report": report,
                "next_action": "deliver" if zero_issue else ("upstream_rebuild" if internal_round >= 3 else "repair_all_issues_then_recommit"),
            },
        )

    def audit_storyboard(
        self,
        namespace: str,
        *,
        project_id: str,
        artifact_id: str,
        audit_cycle_id: str | None,
    ) -> ToolResponse:
        self._require_runtime()
        storyboard, record = self._storyboard_from_artifact(namespace, project_id, artifact_id)
        report = validate_storyboard.validate_project(storyboard, mode="audit")
        with self.store.mutate_project(namespace, project_id) as (_hub, ledger):
            audit_result = self._record_audit(
                namespace=namespace,
                project_id=project_id,
                artifact_record=record,
                storyboard=storyboard,
                report=report,
                ledger=ledger,
                audit_cycle_id=audit_cycle_id,
            )
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
        return ToolResponse(
            status=status,
            summary=summary,
            project_id=project_id,
            artifact_id=artifact_id,
            data={"report": report, "audit": audit_result},
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
            if work_order["status"] == "passed":
                project_agent.transition_work_order(hub, work_order_id, "awaiting_signoff")
            result = project_agent.signoff_work_order(hub, work_order_id, decision)
            hub["human_signoffs"][-1]["signer"] = signer.strip()
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
            data=result,
        )
