"""Server-owned semantic obligations, separate from machine content checks.

This proves that a declared review covers current dependencies and cites actual
content. It cannot prove that a human/model judgement is semantically correct.
No production code manufactures auditor conclusions or clean observations.
"""
from __future__ import annotations

import copy
import json
from typing import Any

from .canonical import canonical_sha256, stable_json_hash
from .materializer import COMPACT_SHOT_FIELDS
from .models import SemanticAuditCoverageInput, SemanticAuditCoveragePageInput, ToolResponse
from .r5_workbook import workbook_audit_scope


CONTRACT = "semantic-obligation-coverage-v1"
LEGACY_PREFIX = "audit-coverage-v1:"
PAGE_PREFIX = "audit-coverage-page-v1:"
MAX_ENVELOPE_BYTES = 8 * 1024 * 1024
NEXT_STEP = (
    "read_project_artifact(section='audit_obligations', offset=0, limit=100, max_chars=64000); "
    "follow next_call through every page, inspect current fields with the first-page rule_definitions, then submit coverage. "
    "Old tool schemas may submit the versioned envelope in layer.evidence_ids; "
    "ordinary layer PASS/summary is not coverage."
)

# The checks ask reviewers to assess natural behaviour, including stillness;
# they never require a blink, gesture, reaction, or a particular acting formula.
# Each tuple: stable rule, layer, question, relevant current-shot fields,
# mandatory observed fields, whether previous/next shots are dependencies.
RULES = (
    ("source_fidelity", "源文对白", "逐字核对授权对白、叙述转译和源文事实；没有依据不得补剧情。",
     ("画面内容", "覆盖原文", "原文依据类型", "原文支撑句"), ("画面内容", "原文支撑句"), False),
    ("camera_visibility", "机位可见性", "机位、景别与主体位置是否允许拍到所写动作；站位必须同时定位画面左/中/右与真实场景参照物，换机位重新推导屏幕方向。多人场景按说者、主要听者、关键反应取景，不能丢失画外人物世界状态或用群演替换实名角色；机位字段只描述机位。",
     ("机位", "镜头类型", "可见主体", "空间站位描述", "画面内容"), ("机位", "画面内容"), False),
    ("observable_lighting", "机位可见性", "核对光源、方向、色温与可见光影一致且无无依据室内雾化。",
     ("光影", "光源来源", "光源方向", "色温倾向", "环境动态"), ("光影", "光源来源"), True),
    ("state_continuity", "空间连续", "核对本镜起态、站位、门线、道具和相邻镜末态；首镜核对源文起态。",
     ("空间站位描述", "起始状态", "画面内容", "可见主体", "场景连续性ID"), ("空间站位描述", "起始状态", "画面内容"), True),
    ("scene_causality", "空间连续", "先对照完整授权原文核对事实图是否遗漏触发、获知、决定或结果，再只读观众能看到/听到的分镜：为何切场、时间是否同时、地点是否可辨、行动前因是否实际交付？核对因果上下游的实际镜头及旧集引用；原文有不代表观众已知。短场景、反应、伏笔和有源悬念允许保留，不能强补对白或行走空镜。悬念须有揭示位置，跨批兑现仍待核对；并行两地不共享未来状态，声音桥不授予异地听取。",
     ("空间站位描述", "起始状态", "画面内容", "心理音轨", "切镜/转场提示词", "原文支撑句"),
     ("画面内容", "切镜/转场提示词", "原文支撑句"), True),
    ("grounded_performance", "表演链", "核对design.performance的来源、人物、当前刺激与前态是否支持实际可见表情及肢体选择；发言不能默认平静平淡，也不能套统一夸张模板。静止或无人物可成立，不强制眨眼、反应或手势；动作必须在本景别可见。",
     ("可见主体", "画面内容", "原文支撑句"), ("可见主体", "画面内容", "原文支撑句"), True),
    ("performance_variation", "表演链", "横向比较整批与相邻镜表演；重复须有叙事依据，不能为差异化编造行为。",
     ("可见主体", "画面内容", "功能类型"), ("画面内容",), True),
    ("timing_capacity", "镜头节奏", "检查对白动作声音容量。4秒是规划目标；4—6秒含完整短句自然收尾，逐条核对来源、完整表达、真实切点、保留/拆分损益、估计/实测依据及延长段价值，不能强制补表情获取例外；6—8秒仅固定稳定信息阅读且无说话/表演/运镜；>8秒重拆。不按秒数或标点机械切镜；复核实际cut_anchor及候选字符位置，不将估计当词音实测对齐。声画同步事件不重复相加，不统一添加首尾停顿；切机位保持逐字及声音连续。不得把形式填全或估计当实测通过；画面正文无制作时间码。",
     ("时长", "画面内容", "音色", "心理音轨", "环境音效"), ("时长", "画面内容"), False),
    ("rhythm_transition", "镜头节奏", "核对design.framing_reason/cut_reason/cut_anchor与实际信息、关系、表演、动作及容量；中近景+近景约60%为整集软目标，特写另计，不能改标签凑数。多人关系镜按需要建立；正反打无硬配额。固定或单一简单运动须有用途且在执行器能力内，静态GPT不写运镜。",
     ("功能类型", "镜头类型", "转场类型", "切镜/转场提示词", "画面内容"), ("镜头类型", "切镜/转场提示词"), True),
    ("field_ownership", "正式母版单镜执行完整性", "逐字段检查内容归属：画面只写可见行为，机位只写机位，音色只写声音属性；导演解释、心理目的和限制各归指定字段。",
     tuple(COMPACT_SHOT_FIELDS), tuple(COMPACT_SHOT_FIELDS), False),
    ("standalone_execution", "正式母版单镜执行完整性", "核对本镜E列执行内容与K列四项光影及F列静态关键帧同源完整。E列不拼光影、不显示可见主体标签，身份仍须明确。不能用承接上一镜等代替本镜起态；关键帧无动作过程。",
     ("可见主体", "空间站位描述", "起始状态", "画面内容", "参考图锚定", "生成限制"), ("起始状态", "画面内容", "参考图锚定"), False),
    ("sound_consistency", "禁项与声音", "核对说话者、逐字台词、音色/心理音轨、连续拆镜声音及口型。基础声线保持身份稳定；当前语气、重音、节奏、音量、尾音依据当前交流，不统一平淡或喊叫；声音属性只入音色。无对白镜允许无声，不强制添加声音。",
     ("画面内容", "音色", "心理音轨", "环境音效"), ("画面内容", "音色", "环境音效"), True),
    ("generation_limits", "禁项与声音", "核对人物/道具/雾化/音乐等限制及否定语义，不能把禁止项当成实际发生的内容。",
     ("生成限制", "可见主体", "画面内容", "环境音效", "环境动态"), ("生成限制", "画面内容"), False),
    ("coverage_statistics", "统计闭合", "核对本镜源文覆盖、编号、累计时长及全表顺序；有rhythm_evidence时必须观察整批实际镜数、平均时长、分布和重复切镜理由，检查句尾碎镜、准备/等待模板、凑比例及单镜过载。3—4秒均值和镜数参考不是合格门槛，片段不冒充整集，未检查不能当通过。",
     ("镜头编号", "时长", "累计时长", "覆盖原文", "场景连续性ID"), ("镜头编号", "累计时长", "覆盖原文"), False),
)
DESIGN_FIELDS = {
    'camera_visibility': ('framing_reason', 'spatial_anchors'),
    'state_continuity': ('spatial_anchors', 'cut_reason', 'cut_anchor'),
    'grounded_performance': ('performance',),
    'performance_variation': ('performance',),
    'timing_capacity': ('duration_exception', 'cut_anchor'),
    'rhythm_transition': ('framing_reason', 'cut_reason', 'cut_anchor'),
    'sound_consistency': ('performance',),
}


def coverage_context(service: Any) -> dict[str, str]:
    return {
        "rules": service._ruleset_fingerprint,
        "validator": service._validator_fingerprint,
        "runtime": service._runtime_fingerprint,
        "schema": service._schema_fingerprint,
    }


def _text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)


def build_obligations(storyboard: dict[str, Any], context: dict[str, str]) -> dict[str, Any]:
    shots = [s for s in storyboard.get("shots", []) if isinstance(s, dict)]
    ids = [str(s.get("镜头编号") or s.get("shot_uid") or "").strip() for s in shots]
    if not ids or any(not s for s in ids) or len(set(ids)) != len(ids):
        raise ValueError("audit_coverage_shot_identity_invalid")
    # A change anywhere in the frozen source/plan/global pattern invalidates a
    # previous review. Field hashes also expose exactly what each check reads.
    root_dependencies = {k: v for k, v in storyboard.items() if k != "shots"}
    root_hash = stable_json_hash(root_dependencies)
    pattern_hash = stable_json_hash([
        {k: s.get(k) for k in ("镜头编号", "画面内容", "音色", "时长", "镜头类型")}
        for s in shots
    ])
    values = {
        f"field:{sid}.{field}": _text(shot.get(field, ""))
        for sid, shot in zip(ids, shots) for field in COMPACT_SHOT_FIELDS
    }
    field_hashes = {ref: stable_json_hash(value) for ref, value in values.items()}
    try:
        graph = json.loads(storyboard['prevention_evidence']['production_derivation']['source_graph_json'])
        designs = {s['shot_id']: s['design'] for s in graph['production_plan']['shots']}
        narrative = graph['production_plan'].get('narrative')
    except (KeyError, TypeError, ValueError):
        designs = {}  # Historical inputs lack a current production design.
        narrative = None
    # Compute from the actual artifact, not a producer's prewritten statistics.
    from .production_plan import shot_rhythm_statistics, shot_policy
    from episode_rules import episode_id
    labels = {v: k for k, v in shot_policy()['framing'].items()}
    rhythm_rows = [{'shot_id': sid, 'episode_id': episode_id(shot, storyboard),
                   'duration_seconds': shot.get('时长'),
                   'frame': labels.get(shot.get('镜头类型'), shot.get('镜头类型')),
                   'design': designs.get(sid, {})} for sid, shot in zip(ids, shots)]
    try:
        rhythm = shot_rhythm_statistics(rhythm_rows, scope=storyboard.get('prevention_evidence', {}).get('episode_contract', {}).get('scope', 'fragment'))
    except (TypeError, ValueError):
        rhythm = [{'status': 'insufficient_evidence', 'reason': 'actual episode timing is unavailable; review artifact fields'}]
    rhythm_ref = 'batch:shot_rhythm'
    values[rhythm_ref] = json.dumps(rhythm, ensure_ascii=False, sort_keys=True)
    field_hashes[rhythm_ref] = stable_json_hash(values[rhythm_ref])
    facts_by_shot = {}
    if narrative:
        source_uses = {u['id']: u['fact_ids'] for u in storyboard.get('prevention_evidence', {}).get('source_decisions', {}).get('uses', [])}
        facts_by_shot = {s['shot_id']: set(s['purpose_fact_ids']) |
            {fid for uid in s['source_use_ids'] for fid in source_uses.get(uid, [])}
            for s in graph['production_plan']['shots']}
    rows = []
    for index, (sid, shot) in enumerate(zip(ids, shots)):
        for rule, layer, question, fields, observed, adjacent in RULES:
            refs = [f"field:{sid}.{field}" for field in fields]
            if adjacent:
                refs.extend(f"field:{ids[n]}.{field}" for n in (index-1, index+1)
                            if 0 <= n < len(ids)
                            for field in ("画面内容", "起始状态", "空间站位描述", "可见主体"))
            dependencies = {ref: field_hashes[ref] for ref in refs}
            row = {
                "obligation_id": f"{sid}/{rule}", "shot_id": sid, "rule_id": rule,
                "layer": layer, "question": question,
                "dependency_fields": dependencies,
                "required_observation_refs": [f"field:{sid}.{field}" for field in observed],
                "allowed_statuses": ["pass", "fail_current"],
                "applicability": "Required review; natural stillness, silent shots and prop-only frames are valid when supported, not omissions.",
            }
            if sid in designs and rule in DESIGN_FIELDS:
                ref = f'plan:{sid}.{rule}'
                text = json.dumps({k: designs[sid].get(k) for k in DESIGN_FIELDS[rule]}, ensure_ascii=False, sort_keys=True)
                values[ref] = text
                row['dependency_fields'][ref] = stable_json_hash(text)
                row['required_observation_refs'].append(ref)
                row['planning_evidence'] = {'reference': ref, 'text': text}
            if index == 0 and rule == 'coverage_statistics':
                row['dependency_fields'][rhythm_ref] = field_hashes[rhythm_ref]
                row['required_observation_refs'].append(rhythm_ref)
                row['rhythm_evidence'] = {'reference': rhythm_ref, 'text': values[rhythm_ref]}
            if rule == 'scene_causality' and narrative:
                segments = [s for s in narrative['segments'] if sid in s['shot_ids']]
                shot_facts = facts_by_shot.get(sid, set())
                links = [x for x in narrative['causal_links'] if x['fact_id'] in shot_facts or
                         x['requires_fact_id'] in shot_facts or sid in (x['establishment_shot_id'], x['reveal_shot_id'])]
                ref = f'plan:{sid}.scene_causality'
                planning = {'segments': segments, 'causal_links': links,
                    'prior_deliveries': [x for x in narrative['prior_deliveries'] if x['id'] in
                                        {link['prior_delivery_id'] for link in links}]}
                text = json.dumps(planning, ensure_ascii=False, sort_keys=True)
                values[ref] = text
                row['dependency_fields'][ref] = stable_json_hash(text)
                row['required_observation_refs'].append(ref)
                row['planning_evidence'] = {'reference': ref, 'text': text}
                related = {s for segment in segments for s in segment['shot_ids']}
                related.update(x[k] for x in links for k in ('establishment_shot_id', 'reveal_shot_id') if x[k])
                involved = {x[k] for x in links for k in ('fact_id', 'requires_fact_id')}
                related.update(other for other, facts in facts_by_shot.items() if facts & involved)
                for other in (other for other in ids if other in related):
                    for field in ('画面内容', '起始状态', '原文支撑句'):
                        dep = f'field:{other}.{field}'
                        row['dependency_fields'][dep] = field_hashes[dep]
                        if dep not in row['required_observation_refs']:
                            row['required_observation_refs'].append(dep)
            # There is no previous-shot obligation on the first shot. The state
            # check itself still applies to its own source-defined opening.
            row["dependency_sha256"] = stable_json_hash({
                "contract": CONTRACT, "rule": rule, "definition": row,
                "root_dependencies": root_hash, "batch_pattern": pattern_hash,
                "context": context,
            })
            rows.append(row)
    manifest = {"contract": CONTRACT, "canonical_sha256": canonical_sha256(storyboard),
                "context": context, "shot_count": len(shots), "obligation_count": len(rows),
                "root_dependencies_sha256": root_hash, "batch_pattern_sha256": pattern_hash,
                "obligations": rows}
    manifest["manifest_sha256"] = stable_json_hash(manifest)
    # Values are private validation data, excluded from the manifest identity
    # and from obligation pages. The exact fields are read with the artifact.
    manifest["_values"] = values
    return manifest


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("audit_coverage_duplicate_json_key")
        result[key] = value
    return result


def normalize_coverage_transport(submission: dict[str, Any]) -> dict[str, Any]:
    """Decode only explicit bounded legacy fragments; leave normal refs alone."""
    result = copy.deepcopy(submission)
    page_layers = [layer for layer in result.get("layers", [])
                   if any(str(ref).startswith(PAGE_PREFIX) for ref in layer.get("evidence_ids", []))]
    if page_layers:
        if (len(page_layers) != 1 or result.get("coverage") is not None or
                result.get("coverage_page") is not None or
                any(str(ref).startswith(LEGACY_PREFIX) for layer in result["layers"] for ref in layer["evidence_ids"])):
            raise ValueError("audit_coverage_conflicting_transports")
        layer, fragments, refs = page_layers[0], [], []
        for ref in layer["evidence_ids"]:
            if not str(ref).startswith(PAGE_PREFIX):
                refs.append(ref)
                continue
            parts = ref[len(PAGE_PREFIX):].split(":", 1)
            if len(parts) != 2 or parts[0] != f"{len(fragments):05d}" or not parts[1] or len(ref) > 256:
                raise ValueError("audit_coverage_fragment_sequence_invalid")
            fragments.append(parts[1])
        encoded = "".join(fragments)
        if not refs or len(encoded.encode("utf-8")) > 512*1024:
            raise ValueError("audit_coverage_page_envelope_invalid_or_too_large")
        try:
            value = json.loads(encoded, object_pairs_hook=_unique_object)
        except (json.JSONDecodeError, RecursionError) as exc:
            raise ValueError("audit_coverage_fragment_json_invalid") from exc
        result["coverage_page"] = SemanticAuditCoveragePageInput.model_validate(value).model_dump()
        layer["evidence_ids"] = refs
    if result.get("coverage_page") is not None and result.get("coverage") is not None:
        raise ValueError("audit_coverage_conflicting_transports")
    fragments_present = any(str(ref).startswith(LEGACY_PREFIX)
                            for layer in result.get("layers", [])
                            for ref in layer.get("evidence_ids", []))
    if not fragments_present:
        return result
    if result.get("coverage") is not None:
        raise ValueError("audit_coverage_conflicting_transports")
    envelopes = []
    total_bytes = 0
    for layer in result["layers"]:
        refs, fragments = [], []
        for ref in layer["evidence_ids"]:
            if not str(ref).startswith(LEGACY_PREFIX):
                refs.append(ref)
                continue
            parts = ref[len(LEGACY_PREFIX):].split(":", 1)
            if len(parts) != 2 or parts[0] != f"{len(fragments):05d}" or not parts[1]:
                raise ValueError("audit_coverage_fragment_sequence_invalid")
            if len(ref) > 256 or len(fragments) >= 4096:
                raise ValueError("audit_coverage_fragment_limit_exceeded")
            fragments.append(parts[1])
        if not fragments:
            raise ValueError("audit_coverage_fragment_layer_missing: " + layer["layer"])
        encoded = "".join(fragments)
        total_bytes += len(encoded.encode("utf-8"))
        if total_bytes > MAX_ENVELOPE_BYTES:
            raise ValueError("audit_coverage_envelope_too_large")
        try:
            envelope = json.loads(encoded, object_pairs_hook=_unique_object)
        except (json.JSONDecodeError, RecursionError) as exc:
            raise ValueError("audit_coverage_fragment_json_invalid") from exc
        envelope = SemanticAuditCoverageInput.model_validate(envelope).model_dump()
        envelopes.append((layer["layer"], envelope))
        if not refs:
            raise ValueError("audit_coverage_legacy_layer_evidence_missing")
        layer["evidence_ids"] = refs
    identities = {(v["contract"], v["manifest_sha256"]) for _, v in envelopes}
    if len(identities) != 1:
        raise ValueError("audit_coverage_fragment_manifest_mismatch")
    checks = []
    rule_layers = {row[0]: row[1] for row in RULES}
    for layer, envelope in envelopes:
        # The route is bound to the rule's declared layer during validation.
        for check in envelope["checks"]:
            if rule_layers.get(check["obligation_id"].rsplit("/", 1)[-1]) != layer:
                raise ValueError("audit_coverage_fragment_layer_mismatch")
            checks.append(dict(check))
    result["coverage"] = SemanticAuditCoverageInput.model_validate({
        "contract": CONTRACT, "manifest_sha256": envelopes[0][1]["manifest_sha256"],
        "checks": checks,
    }).model_dump()
    return result


def validate_coverage(storyboard: dict[str, Any], submission: dict[str, Any],
                      context: dict[str, str], *, _page_ids: list[str] | None = None) -> dict[str, Any]:
    manifest = build_obligations(storyboard, context)
    coverage = submission.get("coverage")
    if not isinstance(coverage, dict):
        raise ValueError("audit_coverage_required: " + NEXT_STEP)
    if len(json.dumps(coverage, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > MAX_ENVELOPE_BYTES:
        raise ValueError("audit_coverage_envelope_too_large")
    if coverage.get("contract") != CONTRACT or coverage.get("manifest_sha256") != manifest["manifest_sha256"]:
        raise ValueError("audit_coverage_manifest_stale: " + NEXT_STEP)
    rows = coverage.get("checks") or []
    by_id = {r["obligation_id"]: r for r in manifest["obligations"]}
    if _page_ids is not None:
        if not _page_ids or set(_page_ids) - set(by_id):
            raise ValueError("audit_coverage_page_range_invalid")
        by_id = {key: by_id[key] for key in _page_ids}
    actual = [row.get("obligation_id") for row in rows]
    if len(actual) != len(set(actual)):
        raise ValueError("audit_coverage_duplicate_obligation")
    missing, extra = set(by_id) - set(actual), set(actual) - set(by_id)
    if missing or extra:
        raise ValueError("audit_coverage_incomplete: " + json.dumps({
            "missing_count": len(missing), "extra_count": len(extra),
            "missing_examples": sorted(missing)[:5], "extra_examples": sorted(extra)[:5],
        }, ensure_ascii=False) + "; " + NEXT_STEP)
    layers = {r["layer"]: r for r in submission["layers"]}
    issues = {r["issue_id"]: r for r in submission.get("issues", [])}
    failed = 0
    for check in rows:
        expected = by_id[check["obligation_id"]]
        if check.get("_transport_layer", expected["layer"]) != expected["layer"]:
            raise ValueError("audit_coverage_fragment_layer_mismatch")
        if check.get("dependency_sha256") != expected["dependency_sha256"]:
            raise ValueError("audit_coverage_dependency_stale: " + check["obligation_id"])
        if check.get("status") not in expected["allowed_statuses"]:
            raise ValueError("audit_coverage_unresolved: unknown/unjustified not_applicable cannot pass")
        observations = check.get("observations") or []
        observed = [r.get("reference") for r in observations]
        if len(observed) != len(set(observed)):
            raise ValueError("audit_coverage_duplicate_observation")
        if set(expected["required_observation_refs"]) - set(observed):
            raise ValueError("audit_coverage_observation_missing: " + check["obligation_id"])
        for observation in observations:
            ref, quote = observation["reference"], observation["quote"]
            if ref not in expected["dependency_fields"]:
                raise ValueError("audit_coverage_observation_outside_dependencies")
            value = manifest["_values"][ref]
            # Empty content must be explicitly observed as empty; an empty
            # substring must not serve as evidence for a populated field.
            if (value and len(quote.strip()) < min(8, len(value.strip()))) or quote not in value or (not value and quote != ""):
                raise ValueError("audit_coverage_observation_not_in_current_field: " + ref)
        if not str(check.get("judgement") or "").strip() or len(check["judgement"].strip()) < 12:
            raise ValueError("audit_coverage_judgement_required")
        if check["status"] == "fail_current":
            failed += 1
            if layers[expected["layer"]]["status"] != "fail_current" or not check.get("issue_ids"):
                raise ValueError("audit_coverage_failed_check_requires_issue")
            for issue_id in check["issue_ids"]:
                issue = issues.get(issue_id)
                if not issue or issue["layer"] != expected["layer"] or expected["shot_id"] not in issue.get("shot_ids", []):
                    raise ValueError("audit_coverage_failed_check_issue_mismatch")
        elif check.get("issue_ids"):
            raise ValueError("audit_coverage_passing_check_has_issues")
    return {"contract": CONTRACT, "manifest_sha256": manifest["manifest_sha256"],
            "canonical_sha256": manifest["canonical_sha256"],
            "context": context, "expected_count": len(by_id), "reviewed_count": len(rows),
            "coverage_percent": 100.0, "failed_count": failed,
            "checks_sha256": stable_json_hash(rows),
            "meaning": "Complete bound reviewer declarations; not proof of semantic correctness."}


def verify_certificate_coverage(service: Any, storyboard: dict[str, Any], certificate: dict[str, Any]) -> dict[str, Any]:
    report = certificate.get("report") or {}
    submission = report.get("semantic_audit")
    if not isinstance(submission, dict):
        raise ValueError("audit_coverage_certificate_missing: " + NEXT_STEP)
    receipt = validate_coverage(storyboard, submission, coverage_context(service))
    if (certificate.get("semantic_obligation_coverage") != receipt or
            report.get("independent_evidence_matrix", {}).get("semantic_obligation_coverage") != receipt):
        raise ValueError("audit_coverage_certificate_binding_missing_or_stale: " + NEXT_STEP)
    page_proof = certificate.get("audit_coverage_page_receipt")
    if page_proof != report.get("audit_coverage_page_receipt"):
        raise ValueError("audit_coverage_page_receipt_mismatch")
    if page_proof is not None:
        binding = page_proof.get("binding") or {}
        authority = page_proof.get("finalizing_authority") or binding.get("authority") or {}
        if (page_proof.get("checks_sha256") != receipt["checks_sha256"] or
                page_proof.get("manifest_sha256") != receipt["manifest_sha256"] or
                binding.get("artifact_id") != certificate.get("artifact_id") or
                binding.get("work_order_id") != certificate.get("work_order_id") or
                authority.get("actor_binding_id") != certificate.get("audit_actor_binding_id") or
                authority.get("session_fingerprint") != certificate.get("audit_session_fingerprint") or
                not page_proof.get("collection_id") or not page_proof.get("page_ids")):
            raise ValueError("audit_coverage_page_receipt_binding_mismatch")
    return receipt


def _obligation_rule_definitions() -> dict[str, dict[str, Any]]:
    """Shared read view; hashes and full validation remain server-owned."""
    return {
        rule: {
            "layer": layer, "question": question,
            "dependency_fields": list(fields),
            "required_observation_fields": list(observed),
            "neighbor_dependency_fields": ["画面内容", "起始状态", "空间站位描述", "可见主体"] if adjacent else [],
            "allowed_statuses": ["pass", "fail_current"],
        }
        for rule, layer, question, fields, observed, adjacent in RULES
    }


def _compact_obligation(row: dict[str, Any], neighbor_ids: list[str]) -> dict[str, Any]:
    result = {key: row[key] for key in ("obligation_id", "shot_id", "rule_id", "dependency_sha256")}
    if neighbor_ids:
        result["neighbor_shot_ids"] = neighbor_ids
    if 'planning_evidence' in row:
        result['planning_evidence'] = row['planning_evidence']
    if 'rhythm_evidence' in row:
        result['rhythm_evidence'] = row['rhythm_evidence']
    return result


def read_audit_obligations(service: Any, namespace: str, project_id: str, artifact_id: str,
                          *, offset: int = 0, limit: int | None = 50,
                          max_chars: int = 24_000) -> ToolResponse:
    storyboard, _ = service._storyboard_from_artifact(
        namespace, project_id, artifact_id, persist_recovered_canonical=False,
        require_bound_projection=True)
    manifest = build_obligations(storyboard, coverage_context(service))
    total = manifest["obligation_count"]
    if offset < 0 or offset > total:
        raise ValueError("audit_obligations_offset_invalid")
    limit = min(max(int(limit or 50), 1), 100)
    budget = min(max(int(max_chars or 24_000), 1), 64_000)
    definitions = _obligation_rule_definitions()
    definition_hash = stable_json_hash(definitions)
    shot_ids = list(dict.fromkeys(row['shot_id'] for row in manifest['obligations']))
    neighbors = {
        sid: [shot_ids[n] for n in (index - 1, index + 1) if 0 <= n < len(shot_ids)]
        for index, sid in enumerate(shot_ids)
    }
    next_call = {"project_id": project_id, "artifact_id": artifact_id,
                 "section": "audit_obligations", "offset": offset,
                 "limit": limit, "max_chars": budget}
    data = {
        **{k: v for k, v in manifest.items() if k not in {"obligations", "_values"}},
        "read_view": "compact-obligations-v1",
        "workbook_audit_scope": workbook_audit_scope(),
        "rule_definitions_sha256": definition_hash,
        "definition_reference": {"offset": 0, "manifest_sha256": manifest["manifest_sha256"]},
        "reference_pattern": "field:{shot_id}.{field}",
        "offset": offset, "obligations": [], "next_offset": offset,
        "complete": False, "next_call": next_call,
        "semantic_truth_guaranteed": False,
    }
    if offset == 0:
        data.update({
            "rule_definitions": definitions,
            "submission_schema": SemanticAuditCoverageInput.model_json_schema(),
            "page_submission_schema": SemanticAuditCoveragePageInput.model_json_schema(),
            "read_contract": {
                "row_fields": "Use rule_definitions[row.rule_id] for layer, question, dependency fields and required observation fields.",
                "dependencies": "Expand dependency_fields with row.shot_id, then neighbor_dependency_fields with each row.neighbor_shot_ids (absent means none), using reference_pattern.",
                "observations": "Expand required_observation_fields with row.shot_id. Also observe planning_evidence.reference and rhythm_evidence.reference when present, quoting their text and judging the actual design and whole-batch rhythm against the source and rendered fields. Read actual artifact content and submit literal excerpts plus a separate judgement; definitions alone are not review evidence.",
                "fingerprints": "Return each obligation_id and dependency_sha256 unchanged. Per-field hashes stay server-side; full manifest identity and validation are unchanged.",
                "consistency": "All pages must have the same manifest_sha256 and rule_definitions_sha256. If either changes, restart at offset=0; never combine versions.",
                "applicability": "Natural stillness, silent shots and prop-only frames are valid when supported, not omissions. Unresolved checks cannot pass.",
            },
            "next_step": NEXT_STEP,
            "legacy_transport": {"field": "layers[].evidence_ids", "prefix": LEGACY_PREFIX,
                "format": "audit-coverage-v1:00000:<JSON fragment>; consecutive zero-based five-digit sequence per layer",
                "envelope": "coverage object containing only this layer's checks, same manifest_sha256 on all eight layers",
                "max_reference_chars": 256, "max_total_bytes": MAX_ENVELOPE_BYTES,
                "mixed_typed_and_legacy_forbidden": True},
            "paged_transport": {"contract": "semantic-obligation-pages-v1", "prefix": PAGE_PREFIX,
                "field": "coverage_page (or versioned fragments in one layer.evidence_ids)",
                "initial_append": "collection_key + offset=0 + current contiguous checks (at most 64)",
                "next_append": "server-issued collection_id + next offset + contiguous checks",
                "finalize": "collection_id + explicit complete unique server-issued page_ids; no checks",
                "page_status": "coverage_pending; never PASS, certificate or delivery eligibility"},
        })
    response = ToolResponse(status="audit_obligations", summary="当前逐镜审计义务｜按共享规则逐项读取并判断",
        project_id=project_id, artifact_id=artifact_id, data=data)

    def set_next() -> None:
        cursor = offset + len(response.data['obligations'])
        complete = cursor == total
        response.data['next_offset'] = None if complete else cursor
        response.data['complete'] = complete
        response.data['next_call'] = None if complete else {**next_call, 'offset': cursor}

    for row in manifest['obligations'][offset:offset + limit]:
        adjacent = neighbors[row['shot_id']] if definitions[row['rule_id']]['neighbor_dependency_fields'] else []
        response.data['obligations'].append(_compact_obligation(row, adjacent))
        set_next()
        if len(response.model_dump_json()) > budget:
            response.data['obligations'].pop()
            set_next()
            break
    set_next()
    if (offset < total and not response.data['obligations']) or len(response.model_dump_json()) > budget:
        raise ValueError("audit_obligations_max_chars_too_small: increase max_chars to 64000")
    return response
