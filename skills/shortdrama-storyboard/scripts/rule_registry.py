#!/usr/bin/env python3
"""Single-source rule compilation and fail-closed, read-only drift validation.

The registry owns current definitions. Generated legacy-shaped JSON views exist
only for existing consumers; no generated file may become a second authority.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import re

REFERENCE_ROOT = Path(__file__).resolve().parents[1] / "references"
VIEW_KEYS = {
    "conflict_crosswalk.json": ("conflicts", "conflict_id"),
    "atomic_rule_registry.jsonl": ("atomic_rules", "rule_id"),
    "validator_ruleset.json": ("validators", "validator_rule_id"),
    "error_catalog.json": ("errors", "error_code"),
    "task_package_cards.json": ("task_cards", "task_package_id"),
}


def load_registry(reference_root=None):
    return json.loads((Path(reference_root or REFERENCE_ROOT) / "rule_registry.json").read_text(encoding="utf-8"))


def registry_sha256(registry):
    encoded = json.dumps(registry, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def render_views(registry):
    """Compile all compatibility views without reading any generated view."""
    result = {}
    runtime = copy.deepcopy(registry["runtime"])
    runtime["error_catalog"] = copy.deepcopy(registry["errors"])
    runtime["rule_registry_contract"] = registry["registry_contract"]
    runtime["rule_registry_revision"] = registry["plugin_version"]
    runtime["rule_registry_sha256"] = registry_sha256(registry)
    result["storyboard_runtime_rules.json"] = runtime
    result["project_agent_capabilities.json"] = copy.deepcopy(registry["capabilities"])
    result["deprecated_rule_map.json"] = {
        "version": registry["runtime_version"],
        "items": copy.deepcopy(runtime["deprecated_rules"]),
    }
    conflicts = {row["conflict_id"]: row for row in registry["conflicts"]}
    gate_ids = [row["gate_id"] for row in runtime["gates"]["template_hard_gates"]]
    for filename, (key, _) in VIEW_KEYS.items():
        rows = _resolve_release_declarations(registry[key], registry["plugin_version"])
        for row in rows:
            if "conflict_ref" in row:
                ref = row.pop("conflict_ref")
                conflict = conflicts[ref]
                row["rule_name"] = conflict["conflict_pair"]
                row["rule_full_text"] = f"高优先级={conflict['higher_priority_rule']}；裁决={conflict['decision']}"
                row["linked_error_codes"] = copy.deepcopy(conflict["linked_error_codes"])
            if "voice_template_index" in row:
                index = row.pop("voice_template_index")
                row["rule_full_text"] = runtime["modules"]["VOICE"]["templates"][index]["delivery"]
            if row.pop("required_template_gate_scope", None) == "all_active":
                row["required_template_gate_ids"] = gate_ids.copy()
        result[filename] = rows
    return result


def _resolve_release_declarations(value, version):
    if isinstance(value, str):
        return value.replace("{{plugin_version}}", version)
    if isinstance(value, list):
        return [_resolve_release_declarations(item, version) for item in value]
    if isinstance(value, dict):
        return {key: _resolve_release_declarations(item, version) for key, item in value.items()}
    return copy.deepcopy(value)


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)


def _unique(rows, field, label, failures):
    ids = [row.get(field) for row in rows]
    if any(not isinstance(value, str) or not value.strip() for value in ids):
        failures.append(f"{label}: missing {field}")
    if len(ids) != len(set(ids)):
        failures.append(f"{label}: duplicate {field}")
    return set(ids)


def check_registry_definition(registry):
    """Validate ID sets, applicability, definitions, parameters and references."""
    failures = []
    if registry.get("registry_contract") != "r10-rule-registry-v1":
        failures.append("unsupported registry contract")
    runtime = registry["runtime"]
    version = registry["runtime_version"]
    revision = registry["plugin_version"]
    ids = {}
    for _, (key, field) in VIEW_KEYS.items():
        ids[key] = _unique(registry[key], field, key, failures)
        for row in registry[key]:
            if row.get("version") != version or row.get("rule_revision") != revision:
                failures.append(f"{key}:{row.get(field)}: stale active version")
    error_ids = ids["errors"]
    module_ids = set(registry["module_ids"])
    gate_rows = runtime["gates"]["template_hard_gates"]
    gate_ids = _unique(gate_rows, "gate_id", "template gates", failures)
    if gate_ids != {f"THG-{index:02}" for index in range(1, 18)}:
        failures.append("template gates: complete THG-01..THG-17 set differs")
    if len(runtime["canonical_shot_fields"]) != 27 or len(set(runtime["canonical_shot_fields"])) != 27:
        failures.append("canonical field identity set/order contract must retain 27 distinct fields")
    for field in ("runtime", "capabilities"):
        if registry[field]["compact_master_contract"]["plugin_version"] != revision:
            failures.append(f"{field}: plugin revision differs")
    if runtime.get("project_ledger_policy") != registry["capabilities"].get("project_ledger_policy"):
        failures.append("project ledger policy differs between runtime and capability contract")
    if runtime["windowed_execution_policy"]["activation"].get("window_role_restrictions") is not False or registry["capabilities"]["storyboard_window_architecture"].get("window_role_restrictions") is not False:
        failures.append("current user windows must remain window-neutral")
    if runtime["dialogue_policy"].get("timing_policy_ref") != "timing_policy" or "default_cjk_chars_per_second" in runtime["dialogue_policy"]:
        failures.append("dialogue policy must reference the sole timing_policy, not a shadow rate")
    if runtime["modules"]["ADAPTATION_BUDGET"].get("timing_policy_ref") != "timing_policy" or "dialogue_rate_cjk_per_second" in runtime["modules"]["ADAPTATION_BUDGET"]:
        failures.append("chapter budget must reference the sole timing_policy")
    timing = runtime["timing_policy"]
    profile_ids = _unique(timing["profiles"], "id", "timing profiles", failures)
    if timing.get("default_profile_id") not in profile_ids:
        failures.append("timing default profile has dangling identity")
    for profile in timing["profiles"]:
        speed = profile.get("units_per_second")
        if not isinstance(speed, (int, float)) or isinstance(speed, bool) or not math.isfinite(speed) or speed <= 0:
            failures.append(f"timing profile {profile.get('id')}: invalid speed")
        if profile.get("evidence_status") not in {"provisional", "measured"}:
            failures.append(f"timing profile {profile.get('id')}: unknown evidence status")
        if profile.get("evidence_status") == "provisional" and profile.get("rate_basis") == "articulation":
            failures.append("unmeasured articulation rate cannot claim net speech measurement")
        if profile.get("evidence_status") == "measured":
            evidence = profile.get("evidence", {})
            sample = evidence.get("sample_sha256", "")
            if not isinstance(sample, str) or len(sample) != 64 or any(char not in "0123456789abcdefABCDEF" for char in sample) or evidence.get("alignment_verified") is not True or evidence.get("reviewed") is not True or not evidence.get("model_version") or not evidence.get("voice"):
                failures.append(f"timing profile {profile.get('id')}: measured label lacks aligned reviewed sample identity")
    for row in gate_rows:
        for error in row["error_code"].split("/"):
            if error not in error_ids:
                failures.append(f"{row['gate_id']}: dangling error {error}")
    for key in ("conflicts", "atomic_rules", "validators"):
        for row in registry[key]:
            identity = row.get("rule_id", row.get("validator_rule_id", row.get("conflict_id")))
            refs = row.get("linked_error_codes", [row["error_code"]] if "error_code" in row else [])
            for ref in refs:
                if ref not in error_ids:
                    failures.append(f"{identity}: dangling error {ref}")
            if "conflict_ref" in row and row["conflict_ref"] not in ids["conflicts"]:
                failures.append(f"{identity}: dangling conflict {row['conflict_ref']}")
            if key == "atomic_rules" and row.get("current_status") == "active":
                for required in ("trigger_condition", "pass_condition", "fail_condition", "repair_action"):
                    if not row.get(required):
                        failures.append(f"{identity}: missing applicability/meaning field {required}")
    for row in registry["task_cards"]:
        if row.get("required_template_gate_scope") not in (None, "all_active"):
            failures.append(f"{row['task_package_id']}: unknown template gate scope")
        for module in row.get("must_call_modules", []):
            if module not in module_ids:
                failures.append(f"{row['task_package_id']}: dangling module {module}")
    for row in registry["validators"]:
        if not row.get("applies_when") or not row.get("pass_condition") or not row.get("implementation"):
            failures.append(f"{row['validator_rule_id']}: missing applicability or implementation")
    deprecated = runtime["deprecated_rules"]
    _unique(deprecated, "deprecated", "deprecated rules", failures)
    for row in deprecated:
        if row.get("replacement") == "R10.DIALOGUE_RATE_2_8" or not row.get("replacement") or not row.get("reason"):
            failures.append(f"deprecated rule {row.get('deprecated')}: stale or missing replacement")
    for text in _strings(registry):
        if re.search(r"插件当前发布版本[^。；]*统一标记\s*\d+\.\d+\.\d+", text):
            failures.append("current release natural-language declaration must derive from {{plugin_version}}, not a shadow literal")
        for match in re.finditer(r"当前\s*(0\.5\.\d+)\s*(?:增量路线|版本|插件)", text):
            if match.group(1) != revision:
                failures.append("natural-language current-version claim refers to a retired release")
    return failures


def check_documentation(registry, documents):
    """Check current entry/template declarations without treating optical f/2.8 as speech."""
    failures = []
    for filename, contract in registry.get("documentation_contracts", {}).items():
        if filename not in documents:
            failures.append(f"missing contracted documentation: {filename}")
            continue
        text = documents[filename]
        for term in contract.get("required_terms", []):
            if term not in text:
                failures.append(f"{filename}: missing current declaration {term}")
        for pattern in contract.get("forbidden_patterns", []):
            if re.search(pattern, text):
                failures.append(f"{filename}: retired active declaration matches {pattern}")
        if "template_ids" in contract:
            actual = re.findall(r"^## (TPL-R10-\d+)\b", text, flags=re.MULTILINE)
            expected = contract["template_ids"]
            if len(actual) != len(set(actual)):
                failures.append(f"{filename}: duplicate template identity")
            if set(actual) != set(expected):
                failures.append(f"{filename}: complete template ID set differs")
    return failures


def validate_registry(reference_root=None):
    """Return concrete failures; never mutate files and never self-declare PASS."""
    root = Path(reference_root or REFERENCE_ROOT)
    try:
        registry = load_registry(root)
        failures = check_registry_definition(registry)
        if failures:
            return failures
        for filename, expected in render_views(registry).items():
            path = root / filename
            if not path.is_file():
                failures.append(f"missing generated view: {filename}")
                continue
            text = path.read_text(encoding="utf-8")
            actual = [json.loads(line) for line in text.splitlines() if line.strip()] if filename.endswith(".jsonl") else json.loads(text)
            if actual != expected:
                if filename in VIEW_KEYS:
                    identity = VIEW_KEYS[filename][1]
                    wanted = {row[identity] for row in expected}
                    got = _unique(actual, identity, filename, failures)
                    if got != wanted:
                        failures.append(f"{filename}: ID set differs; missing={sorted(wanted-got)} extra={sorted(got-wanted)}")
                failures.append(f"{filename}: content/order/applicability/parameter drift from registry")
        documents = {}
        for filename in registry.get("documentation_contracts", {}):
            if Path(filename).name != filename or not filename.endswith(".md"):
                failures.append(f"invalid documentation reference: {filename}")
                continue
            path = root / filename
            if path.is_file():
                documents[filename] = path.read_text(encoding="utf-8")
        failures.extend(check_documentation(registry, documents))
        return failures
    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        return [f"rule registry invalid: {type(exc).__name__}: {exc}"]


def write_views(reference_root=None):
    root = Path(reference_root or REFERENCE_ROOT)
    registry = load_registry(root)
    failures = check_registry_definition(registry)
    if failures:
        raise ValueError("; ".join(failures))
    for filename, value in render_views(registry).items():
        content = "\n".join(json.dumps(row, ensure_ascii=False) for row in value) if filename.endswith(".jsonl") else json.dumps(value, ensure_ascii=False, indent=2)
        (root / filename).write_text(content + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-root", type=Path, default=REFERENCE_ROOT)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", help="read-only validation (default)")
    group.add_argument("--write", action="store_true", help="explicitly regenerate compatibility views")
    args = parser.parse_args()
    if args.write:
        write_views(args.reference_root)
    failures = validate_registry(args.reference_root)
    print(json.dumps({"status": "FAIL" if failures else "PASS", "failures": failures}, ensure_ascii=False))
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
