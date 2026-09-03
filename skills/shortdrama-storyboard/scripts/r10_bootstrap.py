#!/usr/bin/env python3
"""Resolve an R10 project or verify the skill's bundled stable runtime."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


VERSION = "V16.3-148-R10-StableRuntime"
WORKFLOW_REVISION = "R10-PG1"
OUTPUT_CONTRACT = "R10-SPC-1"
PRIORITY = [
    "安全合规",
    "源文忠实",
    "分集边界",
    "台词容量",
    "统计闭合",
    "可见性",
    "图生视频可执行性",
    "节奏留存",
    "美学细节",
]


def skill_root() -> Path:
    return Path(__file__).resolve().parent.parent


def is_project_root(path: Path) -> bool:
    required = [
        path / "R10_BOOTSTRAP.md",
        path / "rules" / "storyboard_runtime_rules.json",
        path / "rules" / "storyboard_schema.json",
        path / "rules" / "conflict_crosswalk.json",
        path / "rules" / "task_package_cards.json",
        path / "scripts" / "bootstrap_storyboard.py",
    ]
    return all(item.is_file() and item.stat().st_size > 0 for item in required)


def find_project(explicit: str | None) -> Path | None:
    if explicit:
        candidate = Path(explicit).expanduser().resolve()
        if not is_project_root(candidate):
            raise ValueError(f"not an R10 project root: {candidate}")
        return candidate
    cwd = Path.cwd().resolve()
    for candidate in (cwd, *cwd.parents):
        if is_project_root(candidate):
            return candidate
    return None


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def run_test_script(root: Path, script_name: str) -> dict:
    proc = subprocess.run(
        [sys.executable, str(root / "scripts" / script_name)],
        cwd=root,
        text=True,
        capture_output=True,
    )
    try:
        full = json.loads(proc.stdout)
        return {
            "status": full.get("status"),
            "test_count": full.get("test_count"),
            "test_pass_count": full.get("test_pass_count"),
            "test_fail_count": full.get("test_fail_count"),
            "exit_code": proc.returncode,
        }
    except Exception:
        return {
            "status": "fail",
            "exit_code": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }


def run_project(project: Path, self_test: bool) -> dict:
    command = [sys.executable, str(project / "scripts" / "bootstrap_storyboard.py")]
    if self_test:
        command.append("--self-test")
    proc = subprocess.run(command, cwd=project, text=True, capture_output=True)
    try:
        report = json.loads(proc.stdout)
    except Exception:
        report = {
            "status": "fail",
            "error_code": "E-BOOTSTRAP-001",
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }
    report.update({"mode": "project", "project_root": str(project), "exit_code": proc.returncode})
    report["output_contract"] = OUTPUT_CONTRACT
    if report.get("version") != VERSION or proc.returncode != 0:
        report["status"] = "fail"
        report.setdefault("error_code", "E-BOOTSTRAP-001")
    if self_test:
        strict_prompt_tests = run_test_script(skill_root(), "test_strict_prompt_contract.py")
        public_scene_tests = run_test_script(skill_root(), "test_public_scene_ecology.py")
        agent_tests = run_test_script(skill_root(), "run_agent_tests.py")
        project_agent_tests = run_test_script(skill_root(), "run_project_agent_tests.py")
        report["strict_prompt_tests"] = strict_prompt_tests
        report["public_scene_tests"] = public_scene_tests
        report["agent_tests"] = agent_tests
        report["project_agent_tests"] = project_agent_tests
        if strict_prompt_tests.get("status") != "pass" or strict_prompt_tests.get("exit_code") != 0:
            report["status"] = "fail"
            report.setdefault("error_code", "E-BOOTSTRAP-001")
        if public_scene_tests.get("status") != "pass" or public_scene_tests.get("exit_code") != 0:
            report["status"] = "fail"
            report.setdefault("error_code", "E-BOOTSTRAP-001")
        if agent_tests.get("status") != "pass" or agent_tests.get("exit_code") != 0:
            report["status"] = "fail"
            report.setdefault("error_code", "E-BOOTSTRAP-001")
        if project_agent_tests.get("status") != "pass" or project_agent_tests.get("exit_code") != 0:
            report["status"] = "fail"
            report.setdefault("error_code", "E-BOOTSTRAP-001")
    return report


def verify_bundled(self_test: bool) -> dict:
    root = skill_root()
    refs = root / "references"
    required = [
        refs / "four_source_methods.md",
        refs / "storyboard_runtime_rules.json",
        refs / "storyboard_schema.json",
        refs / "conflict_crosswalk.json",
        refs / "task_package_cards.json",
        refs / "deprecated_rule_map.json",
        refs / "error_catalog.json",
        refs / "validator_ruleset.json",
        refs / "atomic_rule_registry.jsonl",
        refs / "template_library.md",
        refs / "golden_tests.json",
        refs / "agent_runtime.md",
        refs / "agent_ledger_schema.json",
        refs / "full_agent_runtime.md",
        refs / "project_agent_capabilities.json",
        refs / "project_agent_schema.json",
        refs / "jimeng_timing_policy.md",
        refs / "strict_prompt_contract.md",
        refs / "novel_to_storyboard_intake.md",
        refs / "public_scene_ecology.md",
        root / "scripts" / "validate_storyboard.py",
        root / "scripts" / "run_golden_tests.py",
        root / "scripts" / "test_strict_prompt_contract.py",
        root / "scripts" / "test_public_scene_ecology.py",
        root / "scripts" / "storyboard_agent.py",
        root / "scripts" / "run_agent_tests.py",
        root / "scripts" / "project_agent.py",
        root / "scripts" / "run_project_agent_tests.py",
    ]
    missing = [str(path) for path in required if not path.is_file() or path.stat().st_size == 0]
    if missing:
        return {
            "status": "fail",
            "mode": "bundled_reference",
            "version": VERSION,
            "output_contract": OUTPUT_CONTRACT,
            "error_code": "E-BOOTSTRAP-001",
            "missing": missing,
        }

    runtime = read_json(refs / "storyboard_runtime_rules.json")
    modules = runtime.get("modules", {})
    counts = {
        "emotions": len(modules.get("EXPRESSION", {}).get("emotion_catalog", [])),
        "actor_states": len(modules.get("EXPRESSION", {}).get("actor_state_catalog", [])),
        "motions": len(modules.get("MOTION", {}).get("catalog", [])),
        "shot_sizes": len(modules.get("CINEMATIC", {}).get("shot_sizes", [])),
        "lighting_modes": len(modules.get("CINEMATIC", {}).get("lighting", [])),
        "punctuation_types": len(modules.get("VOICE", {}).get("punctuation", [])),
        "voice_templates": len(modules.get("VOICE", {}).get("templates", [])),
    }
    expected = {
        "emotions": 20,
        "actor_states": 15,
        "motions": 16,
        "shot_sizes": 4,
        "lighting_modes": 4,
        "punctuation_types": 8,
        "voice_templates": 12,
    }
    structural_counts = {
        "conflicts": len(read_json(refs / "conflict_crosswalk.json")),
        "task_cards": len(read_json(refs / "task_package_cards.json")),
        "validators": len(read_json(refs / "validator_ruleset.json")),
        "error_codes": len(read_json(refs / "error_catalog.json")),
        "atomic_rules": sum(1 for line in (refs / "atomic_rule_registry.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()),
        "templates": sum(1 for line in (refs / "template_library.md").read_text(encoding="utf-8").splitlines() if line.startswith("## ")),
        "golden_tests": len(read_json(refs / "golden_tests.json").get("tests", [])),
    }
    expected_structural = {
        "conflicts": 31,
        "task_cards": 30,
        "validators": 49,
        "error_codes": 66,
        "atomic_rules": 154,
        "templates": 48,
        "golden_tests": 82,
    }
    policy = runtime.get("preventive_gate_policy", {})
    preventive_counts = {
        "ordered_gates": len(runtime.get("gates", {}).get("ordered_preventive_gates", [])),
        "required_evidence_blocks": len(policy.get("required_evidence_blocks", [])),
        "state_vector_fields": len(policy.get("state_vector_fields", [])),
        "performance_chain_fields": len(policy.get("performance_chain_fields", [])),
        "compact_master_execution_evidence": len(policy.get("compact_master_execution_evidence", [])),
        "audit_layers": len(policy.get("audit_layer_order", [])),
    }
    expected_preventive = {
        "ordered_gates": 14,
        "required_evidence_blocks": 11,
        "state_vector_fields": 7,
        "performance_chain_fields": 7,
        "compact_master_execution_evidence": 11,
        "audit_layers": 8,
    }
    errors = []
    if runtime.get("version") != VERSION:
        errors.append(f"runtime version={runtime.get('version')!r}")
    if runtime.get("workflow_revision") != WORKFLOW_REVISION or policy.get("profile") != WORKFLOW_REVISION:
        errors.append(
            f"workflow revision={runtime.get('workflow_revision')!r}; "
            f"policy profile={policy.get('profile')!r}"
        )
    if runtime.get("global_priority") != PRIORITY:
        errors.append("global priority mismatch")
    if counts != expected:
        errors.append(f"knowledge counts={counts!r}")
    if structural_counts != expected_structural:
        errors.append(f"structural counts={structural_counts!r}")
    if preventive_counts != expected_preventive:
        errors.append(f"preventive counts={preventive_counts!r}")
    expected_audit_layers = [
        "源文对白", "机位可见性", "空间连续", "表演链", "镜头节奏",
        "正式母版单镜执行完整性", "禁项与声音", "统计闭合",
    ]
    if policy.get("audit_layer_order") != expected_audit_layers:
        errors.append(f"audit layer order={policy.get('audit_layer_order')!r}")

    golden = None
    strict_prompt_tests = None
    public_scene_tests = None
    agent_tests = None
    project_agent_tests = None
    if self_test and not errors:
        golden = run_test_script(root, "run_golden_tests.py")
        strict_prompt_tests = run_test_script(root, "test_strict_prompt_contract.py")
        public_scene_tests = run_test_script(root, "test_public_scene_ecology.py")
        agent_tests = run_test_script(root, "run_agent_tests.py")
        project_agent_tests = run_test_script(root, "run_project_agent_tests.py")
        if golden.get("exit_code") != 0 or golden.get("status") != "pass":
            errors.append("functional golden tests failed")
        if strict_prompt_tests.get("exit_code") != 0 or strict_prompt_tests.get("status") != "pass":
            errors.append("R10-SPC-1 strict prompt tests failed")
        if public_scene_tests.get("exit_code") != 0 or public_scene_tests.get("status") != "pass":
            errors.append("public-scene ecology tests failed")
        if agent_tests.get("exit_code") != 0 or agent_tests.get("status") != "pass":
            errors.append("Agent safety tests failed")
        if project_agent_tests.get("exit_code") != 0 or project_agent_tests.get("status") != "pass":
            errors.append("full-project Agent tests failed")

    output = {
        "status": "fail" if errors else "pass",
        "mode": "bundled_reference",
        "version": VERSION,
        "workflow_revision": WORKFLOW_REVISION,
        "output_contract": OUTPUT_CONTRACT,
        "global_priority": PRIORITY,
        "knowledge_counts": counts,
        "structural_counts": structural_counts,
        "preventive_gate_counts": preventive_counts,
        "project_archive_claim_allowed": False,
        "errors": errors,
    }
    if golden is not None:
        output["golden_tests"] = golden
    if strict_prompt_tests is not None:
        output["strict_prompt_tests"] = strict_prompt_tests
    if public_scene_tests is not None:
        output["public_scene_tests"] = public_scene_tests
    if agent_tests is not None:
        output["agent_tests"] = agent_tests
    if project_agent_tests is not None:
        output["project_agent_tests"] = project_agent_tests
    if errors:
        output["error_code"] = "E-BOOTSTRAP-001"
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        project = find_project(args.project_root)
    except ValueError as exc:
        report = {
            "status": "fail",
            "mode": "project",
            "version": VERSION,
            "error_code": "E-BOOTSTRAP-001",
            "error": str(exc),
        }
    else:
        report = run_project(project, args.self_test) if project else verify_bundled(args.self_test)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
