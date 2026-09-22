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
    if "field_content_policy" in runtime:
        result["field_content_contract.md"] = render_field_contract(runtime["field_content_policy"])
    if "production_prevention_policy" in runtime:
        result["production_prevention_contract.md"] = render_production_contract(runtime["production_prevention_policy"])
        design = runtime['production_prevention_policy'].get('shot_design_policy')
        if design:
            result['shot_design_contract.md'] = '\n'.join([
                '# 单镜规划与展示合同', '', '由 rule_registry.json 生成；不得单独编辑。', '',
                '合同：' + design['contract'], '', *['- ' + x for x in design['instructions']], '',
                '## 结构与数值', '', '```json', json.dumps({k:v for k,v in design.items() if k != 'instructions'}, ensure_ascii=False, indent=2), '```', '',
            ])
        narrative = runtime['production_prevention_policy'].get('scene_narrative_policy')
        if narrative:
            result['scene_narrative_contract.md'] = '\n'.join([
                '# 场景时间与叙事因果合同', '', '由 rule_registry.json 生成；不得单独编辑。', '',
                '合同：' + narrative['contract'], '', *['- ' + x for x in narrative['instructions']], '',
                '## 输入结构', '', '```json', json.dumps(narrative['shape'], ensure_ascii=False, indent=2), '```', '',
            ])
    if "workflow_integrity_policy" in runtime:
        policy = runtime["workflow_integrity_policy"]
        result["workflow_integrity_contract.md"] = "\n".join([
            "# 审计与交付完整性合同", "", "由 rule_registry.json 生成；不得单独编辑。",
            "", "合同：" + policy["contract"], "",
            *["- " + value for value in policy["instructions"]], "",
        ])
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


def render_production_contract(policy):
    lines = ['# 生产防错规则合同', '', '由 rule_registry.json 生成；不得单独编辑。', '',
             '合同：' + policy['contract'], '计划：' + policy['plan_contract'], '',
             policy['entry_policy'], '', policy['acceptance'], '',
             '输入格式见 production_plan_contract.md。声明先冻结，程序从真实输入、来源与模拟结果验证；语义准确率保持未知。', '']
    for r in policy['rules']:
        lines += ['## ' + r['id'] + ' ' + r['title'], '', '输入：' + r['inputs'], '',
                  '生成前约束：' + r['prevention'], '', '失败处理：' + r['on_failure'], '',
                  '合法例外：' + r['exceptions'], '', '执行检查：' + r['enforcement'], '',
                  '反例：' + r['counterexample'], '', '判定边界：' + r['limits'], '',
                  '修改影响：' + '、'.join(r['invalidates']), '']
    return '\n'.join(lines)


def render_field_contract(policy):
    lines = ["# 字段内容合同", "", "由 rule_registry.json 生成；不得单独编辑。",
             "", "合同：" + policy["contract"], "",
             "生产决定按字段类型输入，母版/GPT由程序投影。历史原件保留；结构和已支持错列检查不能自签来源或表演语义。",
             "字段正文不得嵌入其他字段或音色子项标题；真实对白和当前可见道具文字保留字面含义。",
             "机位、基础声线、当前表达、环境音效和光影按已登记完整子句检查；合法但未支持的句式返回not_supported，不静默删除或猜改。其余字段继续执行结构化生成、所属角色和独立语义核对，不能宣称任意自然语言已全自动判定。",
             f"当前结构化生产在实际发声期间逐0.1秒核对可见睁眼视线；摄影机方向{policy['speaking_gaze_policy']['camera_cone_degrees']}度锥内或缺明确视线均不能通过。这是保守几何余量，不是实测视觉阈值，最终眼神自然度仍须独审。",
             "", "| 字段 | 允许内容 | 关联与禁止内容 |", "|---|---|---|"]
    for field, spec in policy["fields"].items():
        lines.append(f"| {field} | {spec['allows']} | {spec['requires']} |")
    lines += ["", "## 音色子项", "", "| 子项 | 允许内容 | 禁止内容 |", "|---|---|---|"]
    for field, spec in policy["voice_subfields"].items():
        lines.append(f"| {field} | {spec['allows']} | {spec['requires']} |")
    lines += ["", "## GPT起始帧段落", "", "| 段落 | 来源 | 适用性 |", "|---|---|---|"]
    for row in policy["keyframe_sections"]:
        lines.append(f"| {row['label']} | {'、'.join(row['sources']) or '对应的受控画幅／造型／视觉政策'} | {'按可见对象适用' if row.get('optional') else '固定段落'} |")
    lines += ["", "真实对白、原文和当前可见道具文字逐字保留；仅加引号不能将执行指令伪装为字面内容。",
              "全局生成限制不塞入画面内容；剧情否定和局部禁止语气、心理音轨安全属性留在各自字段。",
              "缺依据不补造、不自动搬列；修正权威决定后同步依赖与所有展示。", "",
              "## 其他历史问题的针对方案", ""]
    for key, value in policy["remaining_problem_plan"].items():
        if isinstance(value, str):
            lines.append(f"- {key}：{value}")
    return "\n".join(lines) + "\n"


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


def _historical_migration_matches(registry, kind, identity, previous, current):
    """Allow only named reviewed transitions, with both sides pinned.

    The historical baseline remains immutable. This is not an arbitrary
    registry-authored waiver: every other historical rule/error stays exact.
    """
    permitted = {
        'atomic_semantic_definitions': 'AR-R10-EXPR-003',
        'error_semantic_definitions': 'E-EXPRESSION-003',
    }
    migrations = registry.get('historical_rule_migrations', {})
    if not isinstance(migrations, dict):
        return False
    if kind == 'critical_runtime_policies' and identity in {'field_content_policy', 'production_prevention_policy'}:
        migration = migrations.get('unified-production-0552', {})
        hashes = migration.get(kind, {}).get(identity, {})
        if (migration.get('basis') == 'user-authorized-unified-root-cause-fix-2026-09-22'
                and migration.get('legacy_applies_to_current_generation') is False
                and hashes.get('baseline_sha256') == registry_sha256(previous)
                and hashes.get('current_sha256') == registry_sha256(current)):
            return True
    if kind == 'critical_runtime_policies' and identity == 'production_prevention_policy':
        migration = migrations.get('shot-rhythm-0549', {})
        hashes = migration.get(kind, {}).get(identity, {})
        if (migration.get('basis') == 'user-authorized-shot-rhythm-2026-09-20'
                and migration.get('legacy_applies_to_current_generation') is False
                and current.get('shot_design_policy', {}).get('contract') == 'shortdrama-shot-design-v2'
                and hashes.get('baseline_sha256') == registry_sha256(previous)
                and hashes.get('current_sha256') == registry_sha256(current)):
            return True
        migration = migrations.get('scene-causality-0547', {})
        hashes = migration.get(kind, {}).get(identity, {})
        if (migration.get('basis') == 'user-authorized-scene-causality-2026-09-20'
                and migration.get('legacy_applies_to_current_generation') is False
                and current.get('scene_narrative_policy', {}).get('contract') == 'scene-narrative-v1'
                and hashes.get('baseline_sha256') == registry_sha256(previous)
                and hashes.get('current_sha256') == registry_sha256(current)):
            return True
    reviewed = {
        'atomic_semantic_definitions': {'AR-R10-GLOBAL-004', 'AR-R10-TEMPLATE-001', 'AR-R10-BATCH-001',
            'AR-R10-PG1-017', 'AR-R10-PG1-028', 'AR-R10-FIELD-001', 'AR-R10-EPISODE-006'},
        'error_semantic_definitions': {'E-STAT-001', 'E-SEEDANCE-VERSION-001', 'E-DUPLICATE-GENERATION-001', 'E-EPISODE-RHYTHM-001'},
        'critical_runtime_policies': {'field_content_policy', 'production_prevention_policy'},
        'episode_policy': {'episode_rule_policy'},
    }
    if identity in reviewed.get(kind, set()):
        migration = migrations.get('shot-design-0546', {})
        hashes = migration.get(kind, {}).get(identity, {})
        return (migration.get('basis') == 'user-authorized-shot-design-2026-09-20'
                and migration.get('legacy_applies_to_current_generation') is False
                and registry['runtime']['production_prevention_policy'].get('shot_design_policy', {}).get('contract') in {'shortdrama-shot-design-v1', 'shortdrama-shot-design-v2'}
                and hashes.get('baseline_sha256') == registry_sha256(previous)
                and hashes.get('current_sha256') == registry_sha256(current))
    if permitted.get(kind) != identity:
        return False
    migration = migrations.get('eye-event-applicability-v1')
    if (not isinstance(migration, dict) or not migration.get('reason')
            or migration.get('legacy_applies_to_current_generation') is not False
            or migration.get('basis') != 'production_prevention_policy:PP-05'
            or registry['runtime'].get('blink_policy', {}).get('contract') != 'eye-event-applicability-v1'):
        return False
    for family, required_id in permitted.items():
        if set(migration.get(family, {})) != {required_id}:
            return False
    hashes = migration[kind][identity]
    return (hashes.get('baseline_sha256') == registry_sha256(previous)
            and hashes.get('current_sha256') == registry_sha256(current))


def check_registry_definition(registry, reference_root=None):
    """Validate ID sets, applicability, definitions, parameters and references."""
    failures = []
    baseline_path=Path(reference_root or REFERENCE_ROOT)/'historical_rule_baseline.json'
    if not baseline_path.is_file():
        failures.append('historical rule baseline missing')
    else:
        baseline=json.loads(baseline_path.read_text(encoding='utf-8'))
        current={row.get('rule_id') for row in registry.get('atomic_rules',[])}
        missing=set(baseline['required_current_atomic_ids'])-current
        if missing: failures.append('historical rule preservation: missing '+', '.join(sorted(missing)))
        if [s['number'] for s in baseline['master_sections']]!=list(range(149)):
            failures.append('historical baseline: complete 0..148 source chapter mapping required')
        if (registry.get('runtime',{}).get('episode_rule_policy') != baseline['episode_policy'] and not
                _historical_migration_matches(registry, 'episode_policy', 'episode_rule_policy',
                    baseline['episode_policy'], registry['runtime']['episode_rule_policy'])):
            failures.append('historical baseline: episode thresholds, scope or retirement decisions changed without migration')
        for key,expected in baseline.get('critical_runtime_policies',{}).items():
            if (registry.get('runtime',{}).get(key) != expected and not
                    _historical_migration_matches(registry, 'critical_runtime_policies', key, expected, registry['runtime'].get(key))):
                failures.append('historical baseline: critical runtime policy changed without migration: '+key)
        for row in registry.get('atomic_rules', []):
            expected = baseline.get('atomic_semantic_definitions', {}).get(row.get('rule_id'))
            current_definition = {k:row.get(k) for k in expected} if expected is not None else None
            if (expected is not None and current_definition != expected and not
                    _historical_migration_matches(registry, 'atomic_semantic_definitions', row['rule_id'], expected, current_definition)):
                failures.append('historical baseline: rule semantics or trigger changed: ' + row['rule_id'])
        for row in registry.get('errors', []):
            expected=baseline.get('error_semantic_definitions',{}).get(row.get('error_code'))
            current_definition = {k:row.get(k) for k in expected} if expected is not None else None
            if (expected is not None and current_definition != expected and not
                    _historical_migration_matches(registry, 'error_semantic_definitions', row['error_code'], expected, current_definition)):
                failures.append('historical baseline: error or repair guidance changed: '+row['error_code'])
    if registry.get("registry_contract") != "r10-rule-registry-v1":
        failures.append("unsupported registry contract")
    runtime = registry["runtime"]
    version = registry["runtime_version"]
    revision = registry["plugin_version"]
    production = runtime.get('production_prevention_policy', {})
    if production.get('contract') != 'storyboard-production-prevention-v1' or production.get('plan_contract') != 'storyboard-production-plan-v3':
        failures.append('production prevention: required contract missing')
    else:
        wanted = {f'PP-{i:02}' for i in range(1, 14)}
        found = _unique(production.get('rules', []), 'id', 'production prevention', failures)
        if found != wanted:
            failures.append('production prevention: complete PP-01..PP-13 set required')
        if production.get('scene_narrative_policy', {}).get('contract') != 'scene-narrative-v1':
            failures.append('production prevention: scene narrative contract missing')
        for row in production['rules']:
            for key in ('title','inputs','prevention','on_failure','exceptions','enforcement','counterexample','limits','invalidates'):
                if not row.get(key): failures.append('production prevention: incomplete rule ' + row['id'] + '.' + key)
        if production.get('statuses') != ['pass','fail','insufficient_evidence','not_supported']:
            failures.append('production prevention: unknown states must not become pass')
        if production.get('authored_basis') != ['source_fact','locked_setting','director_choice'] or production.get('compiler_owned_basis') != 'derived_state':
            failures.append('production prevention: decision ownership differs')
        if registry['capabilities'].get('production_prevention_contract') != production['contract']:
            failures.append('production prevention: capability contract differs')
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
    fields = runtime.get("field_content_policy")
    if fields is None:
        failures.append("field content contract: required authoritative policy is missing")
    else:
        if fields.get("contract") != "storyboard-field-content-v1" or list(fields.get("fields", {})) != runtime["canonical_shot_fields"]:
            failures.append("field content contract: missing, reordered or unknown canonical field")
        labels = [row["label"] for row in fields.get("keyframe_sections", [])]
        if len(labels) != 11 or len(set(labels)) != 11 or sum(bool(row.get("optional")) for row in fields["keyframe_sections"]) != 1:
            failures.append("field content contract: requires ten fixed keyframe sections and one optional anchor")
        if list(fields.get("voice_subfields", {})) != ["基础声线", "言语行为", "当前表达", "发声方式", "音量", "语速与节奏", "呼吸", "停顿", "重音", "尾音", "禁止语气"]:
            failures.append("field content contract: requires the eleven existing voice labels")
        expected_sources = {"画幅": [], "场景与站位": ["空间站位描述"],
            "镜头类型及可见主体": ["镜头类型", "可见主体"], "静态姿态": ["起始状态"],
            "机位": ["机位"], "光影": ["光影"], "光源来源": ["光源来源"],
            "光源方向": ["光源方向"], "色温倾向": ["色温倾向"], "文字造型锚": [], "视觉限制": []}
        if labels != list(expected_sources) or any(row.get("sources") != expected_sources.get(row["label"])
            or bool(row.get("optional")) != (row["label"] == "文字造型锚") for row in fields.get("keyframe_sections", [])):
            failures.append("field content contract: keyframe routing does not match field ownership")
        for spec in [*fields.get("fields", {}).values(), *fields.get("voice_subfields", {}).values()]:
            if not spec.get("allows") or not spec.get("requires"):
                failures.append("field content contract: missing content boundary")
            if set(spec.get("forbidden_roles", [])) - set(fields.get("role_patterns", {})):
                failures.append("field content contract: unknown semantic role")
        for expressions in fields.get("role_patterns", {}).values():
            for expression in expressions:
                try:
                    re.compile(expression)
                except re.error:
                    failures.append("field content contract: invalid role expression")
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
        failures = check_registry_definition(registry, root)
        if failures:
            return failures
        for filename, expected in render_views(registry).items():
            path = root / filename
            if not path.is_file():
                failures.append(f"missing generated view: {filename}")
                continue
            text = path.read_text(encoding="utf-8")
            actual = text if filename.endswith(".md") else ([json.loads(line) for line in text.splitlines() if line.strip()] if filename.endswith(".jsonl") else json.loads(text))
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
    failures = check_registry_definition(registry, root)
    if failures:
        raise ValueError("; ".join(failures))
    for filename, value in render_views(registry).items():
        if filename.endswith(".md"):
            content = value
        else:
            content = ("\n".join(json.dumps(row, ensure_ascii=False) for row in value) if filename.endswith(".jsonl") else json.dumps(value, ensure_ascii=False, indent=2)) + "\n"
        (root / filename).write_text(content, encoding="utf-8")


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
