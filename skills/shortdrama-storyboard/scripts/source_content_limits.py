"""Structural bindings for the narrow, independently reviewed THG-16 exception.

This module never decides semantic entailment.  Its returned obligations remain
pending until the independent source layer reviews the exact source partition
and actual objective narration.  Existing ledgers need no partition unless they
use this treatment.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

CONTRACT = "source-content-limit-v1"
TREATMENT = "来源未给内容的客观叙述"
AFFORDANCE = "仅未给内容不转对白"
ENTAILMENT = "source_event_only_pending_independent"
KINDS = {"instruction_contents_unstated", "answer_contents_unstated",
         "apology_contents_unstated", "instruction_receipt_contents_unstated"}


def text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def binding_sha256(value: Any) -> str:
    return text_sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _summary_shape(text: str, kind: str) -> bool:
    # This is only a conservative syntactic filter, not a semantic whitelist.
    # A real question, quoted instruction, list, or reported answer cannot be
    # hidden inside the event-only span.  The independent review still decides
    # whether the remaining prose actually withholds its communication content.
    if re.search(r'[“”「」『』"：:！？!?\n\d]|是否|必须|不得|不能|不要', text):
        return False
    if kind == "instruction_contents_unstated":
        return bool(re.fullmatch(r"[^，,；;。]{1,100}逐项(?:交代|说明)(?:清楚|完毕)[，,；;。]?", text))
    if kind == "answer_contents_unstated":
        return bool(re.fullmatch(r"[^，,；;。]{1,60}(?:能|能够|已|已经)(?:完整|清楚)回答[，,；;。]?", text))
    if kind in {"apology_contents_unstated", "instruction_receipt_contents_unstated"}:
        # Only the local event clause belongs here. Given content, a promise,
        # denial, condition or quoted words cannot be hidden in either slot.
        # Name/place slots are syntactic, not semantic evidence: their exact
        # meaning and the withheld-content claim still require source review.
        if re.search(r"说|问|回答|告诉|要求|命令|承诺|保证|因为|所以|并|且|却|但|将|曾|未|没有|不|是否", text):
            return False
        slot = r"[^，,；;。：:！？!?\n]{1,40}"
        if kind == "apology_contents_unstated":
            return bool(re.fullmatch(slot + r"向" + slot + r"道歉时[，,；;。]?", text))
        return bool(re.fullmatch(slot + r"守在" + slot + r"听医嘱[，,；;。]?", text))
    return False


def oral_communication_candidate_text(text: str) -> str:
    """Mask only proved nonoral cue tokens, preserving adjacent oral acts.

    This is a source-clause filter, not an author classification flag. It
    cannot suppress a whole paragraph or turn supplied content into unknown
    content. The remaining candidate still goes through the ordinary oral
    communication check and independent semantic review.
    """
    spans: list[tuple[int, int]] = []
    patterns = (
        # A document noun is not the act of explaining aloud.
        r"书面(?P<cue>说明)",
        # Written acknowledgement immediately follows signing that document.
        r"在书面说明上落名[，,]\s*(?P<cue>承认)",
        # Content attributed to an attached note, not a subsequent speaker.
        r"附(?:了)?(?:一张)?[^，,；;。：:\n“”\"]{0,8}礼单[，,]\s*只(?P<cue>说)(?!道|着|话|[：:“\"])",
        # Deferred handling is not a completed conversation. Only 再说 is
        # masked; any preceding 要求/回答/said act remains an oral candidate.
        r"等[^，,；;。：:\n“”\"]{1,30}(?P<cue>再说)(?=[。；;]|$)",
    )
    for pattern in patterns:
        spans.extend(match.span("cue") for match in re.finditer(pattern, text))
    chars = list(text)
    for start, end in spans:
        chars[start:end] = " " * (end - start)
    return "".join(chars)


def _inspect_source_content_limits(obj: dict[str, Any], shots: list[dict[str, Any]], protected_alignment: dict[str, Any] | None) -> dict[str, Any]:
    """Return structural faults and exact *pending* independent obligations."""
    evidence = obj.get("prevention_evidence") or {}
    audit = evidence.get("dialogue_adaptation_audit") or {}
    entries = audit.get("entries") or []
    if not isinstance(entries, list):
        return {"issues": [], "obligations": []}
    limited = [e for e in entries if isinstance(e, dict) and e.get("treatment") == TREATMENT]
    partitions = audit.get("source_content_partitions")
    if not limited and partitions is None:
        return {"issues": [], "obligations": []}
    issues: list[str] = []
    obligations: list[dict[str, Any]] = []
    if not isinstance(partitions, list) or not partitions or not limited:
        return {"issues": ["source-content-limit: partitions and limited entries must both be present"], "obligations": []}
    ledger = evidence.get("source_ledger")
    if not isinstance(ledger, list):
        return {"issues": ["source-content-limit: source_ledger is required"], "obligations": []}
    scope = audit.get("scope_episode_ids")
    if not isinstance(scope, list) or not scope:
        return {"issues": ["source-content-limit: explicit episode scope is required"], "obligations": []}
    by_shot = {str(s.get("镜头编号") or s.get("shot_uid") or ""): s for s in shots}
    by_entry = {str(e.get("adaptation_id") or ""): e for e in entries if isinstance(e, dict)}
    by_partition: dict[str, dict[str, Any]] = {}
    segment_by_entry: dict[str, tuple[str, dict[str, Any]]] = {}
    used_ledger: set[int] = set()
    for part in partitions:
        if not isinstance(part, dict) or set(part) != {"partition_id", "source_text", "source_text_sha256", "segments"}:
            issues.append("source-content-limit: invalid parent partition fields")
            continue
        pid, full, segments = part["partition_id"], part["source_text"], part["segments"]
        if not isinstance(pid, str) or not pid or pid in by_partition or not isinstance(full, str) or not full:
            issues.append("source-content-limit: parent identity/text must be nonempty and unique")
            continue
        by_partition[pid] = part
        if part["source_text_sha256"] != text_sha256(full):
            issues.append(f"source-content-limit: {pid} parent source hash mismatch")
        if not isinstance(segments, list) or not segments:
            issues.append(f"source-content-limit: {pid} needs a complete ordered partition")
            continue
        cursor = 0
        has_limited = False
        previous_ledger_index = 0
        for seg in segments:
            protected = isinstance(seg, dict) and seg.get("kind") == "protected_dialogue"
            link_key = "protected_occurrence_id" if protected else "adaptation_id"
            if not isinstance(seg, dict) or set(seg) != {"source_ledger_index", "source_unit_id", "char_range", "kind", link_key}:
                issues.append(f"source-content-limit: {pid} invalid segment fields")
                continue
            index, span, aid = seg["source_ledger_index"], seg["char_range"], seg.get("adaptation_id", "")
            if (type(index) is not int or not 1 <= index <= len(ledger)
                    or not isinstance(span, list) or len(span) != 2 or any(type(v) is not int for v in span)
                    or not cursor == span[0] < span[1] <= len(full)):
                issues.append(f"source-content-limit: {pid} segment range/index invalid or leaves a gap")
                continue
            cursor = span[1]
            if index in used_ledger or index <= previous_ledger_index:
                issues.append(f"source-content-limit: {pid} reused or unordered ledger segment")
            used_ledger.add(index)
            previous_ledger_index = index
            unit = ledger[index - 1]
            entry = by_entry.get(str(aid))
            piece = full[span[0]:span[1]]
            if protected:
                proof = protected_alignment or {}
                occurrences = [r for r in proof.get("occurrences", []) if r.get("occurrence_id") == seg["protected_occurrence_id"]]
                fragments = [r for r in proof.get("fragments", []) if r.get("occurrence_id") == seg["protected_occurrence_id"]]
                targets = unit.get("target_shots") if isinstance(unit, dict) else None
                quoted = re.findall(r'[“「『"]([^“”「」『』"]+)[”」』"]', piece)
                if (not isinstance(unit, dict) or unit.get("source_id") != seg["source_unit_id"]
                        or unit.get("source_text") != piece or proof.get("status") != "pass"
                        or len(occurrences) != 1 or quoted != [occurrences[0].get("text")]
                        or not isinstance(targets, list) or not targets
                        or list(dict.fromkeys(f.get("shot_id") for f in fragments)) != targets
                        or any(not isinstance(t, str) or t not in by_shot or t.split("-", 1)[0] not in scope for t in targets)):
                    issues.append(f"source-content-limit: {pid} protected span must retain exact THG-05 occurrence, text, speaker and actual targets")
                continue
            if not isinstance(unit, dict) or not isinstance(entry, dict):
                issues.append(f"source-content-limit: {pid} missing ledger/adaptation entry")
                continue
            if (unit.get("source_id") != seg["source_unit_id"] or unit.get("source_text") != piece
                    or entry.get("source_ledger_index") != index or entry.get("source_unit_id") != seg["source_unit_id"]
                    or entry.get("source_text") != piece or entry.get("target_shots") != unit.get("target_shots")):
                issues.append(f"source-content-limit: {pid} exact source/entry/targets binding mismatch")
            targets = entry.get("target_shots")
            if (not isinstance(targets, list) or not targets or any(t not in by_shot or t.split("-", 1)[0] not in scope for t in targets if isinstance(t, str))
                    or any(not isinstance(t, str) for t in (targets or []))):
                issues.append(f"source-content-limit: {pid} target scope mismatch")
            kind = seg["kind"]
            if kind == "given_dialogue":
                if entry.get("treatment") != "原文事实转化对白" or entry.get("dialogue_affordance") != "必须转对白":
                    issues.append(f"source-content-limit: {pid} given communication must remain ordinary adapted dialogue")
            elif kind == "event_only":
                has_limited = True
                if entry.get("treatment") != TREATMENT:
                    issues.append(f"source-content-limit: {pid} event segment lacks its limited treatment")
            elif kind == "non_dialogue":
                if entry.get("scene_semantics") != "非会话叙述" or entry.get("treatment") == TREATMENT:
                    issues.append(f"source-content-limit: {pid} non-dialogue classification mismatch")
            else:
                issues.append(f"source-content-limit: {pid} unsupported segment kind")
            if aid in segment_by_entry:
                issues.append(f"source-content-limit: {pid} reused adaptation entry")
            segment_by_entry[str(aid)] = (pid, seg)
        if cursor != len(full) or not has_limited:
            issues.append(f"source-content-limit: {pid} whole parent must be covered once and contain the exception")

    for entry in limited:
        aid = str(entry.get("adaptation_id") or "")
        limit = entry.get("source_content_limit")
        if not isinstance(limit, dict) or set(limit) != {"contract", "partition_id", "source_text_sha256", "unprovided_content_kind", "narration", "action"}:
            issues.append(f"source-content-limit: {aid} invalid limit fields (no author approval flags)")
            continue
        linked = segment_by_entry.get(aid)
        pid = limit["partition_id"]
        if not linked or linked[0] != pid or linked[1]["kind"] != "event_only" or pid not in by_partition:
            issues.append(f"source-content-limit: {aid} event must belong to its complete parent partition")
            continue
        source_text = str(entry.get("source_text") or "")
        if limit["contract"] != CONTRACT or limit["source_text_sha256"] != text_sha256(source_text):
            issues.append(f"source-content-limit: {aid} contract/source hash mismatch")
        if not _summary_shape(source_text, limit["unprovided_content_kind"]):
            issues.append(f"source-content-limit: {aid} unsupported event syntax or given content; do not invent missing dialogue to satisfy this capability boundary")
        if (entry.get("dialogue_affordance") != AFFORDANCE or entry.get("entailment_status") != ENTAILMENT
                or entry.get("scene_semantics") not in {"叙述性会话场景", "混合会话场景"}
                or entry.get("speaker") or entry.get("adapted_text")):
            issues.append(f"source-content-limit: {aid} must retain conversation semantics and pending independent status without invented speaker/dialogue")
        narration, action = limit["narration"], limit["action"]
        if (not isinstance(narration, dict) or set(narration) != {"shot_id", "field", "text", "field_sha256"}
                or not isinstance(action, dict) or set(action) != {"shot_id", "field", "field_sha256"}):
            issues.append(f"source-content-limit: {aid} objective narration and action bindings required")
            continue
        target = narration.get("shot_id")
        shot = by_shot.get(str(target))
        if (not isinstance(target, str) or shot is None or entry.get("target_shots") != [target]
                or action.get("shot_id") != target or narration.get("field") != "心理音轨" or action.get("field") != "画面内容"):
            issues.append(f"source-content-limit: {aid} requires one exact in-scope narration/action shot")
            continue
        track, visual = shot.get("心理音轨"), shot.get("画面内容")
        if not isinstance(track, str) or not isinstance(visual, str) or not visual.strip():
            issues.append(f"source-content-limit: {aid} actual narration/action missing")
            continue
        match = re.fullmatch(r'(?:叙述旁白|客观旁白)[：:]\s*“([^“”]+)”([^“”]*)', track.strip())
        if not match or match.group(1) != narration.get("text"):
            issues.append(f"source-content-limit: {aid} must bind one explicit objective narration, never character inner speech")
        if narration["field_sha256"] != text_sha256(track) or action["field_sha256"] != text_sha256(visual):
            issues.append(f"source-content-limit: {aid} actual output hash mismatch")
        payload = {"contract": CONTRACT, "scope_episode_ids": scope, "partition": by_partition[pid],
                   "partition_source_ledger_units": [ledger[s["source_ledger_index"] - 1] for s in by_partition[pid]["segments"]],
                   "partition_entries": [by_entry.get(str(s.get("adaptation_id"))) for s in by_partition[pid]["segments"]],
                   "partition_actual_fields": [
                       {"shot_id": ref, "画面内容": by_shot[ref].get("画面内容"), "心理音轨": by_shot[ref].get("心理音轨")}
                       for seg in by_partition[pid]["segments"]
                       for ref in ledger[seg["source_ledger_index"] - 1].get("target_shots", [])
                       if ref in by_shot
                   ],
                   "protected_binding": protected_alignment if any(s.get("kind") == "protected_dialogue" for s in by_partition[pid]["segments"]) else None,
                   "actual_narration": track, "actual_action": visual}
        digest = binding_sha256(payload)
        ref = f"source-limit:{aid}:{digest}"
        if len(ref) > 256:
            issues.append(f"source-content-limit: {aid} evidence identity too long")
        obligations.append({"adaptation_id": aid, "source_unit_id": entry.get("source_unit_id"),
                            "source_state": "pending_independent", "binding_sha256": digest, "evidence_id": ref,
                            "field_evidence_ids": [f"field:{target}.心理音轨", f"field:{target}.画面内容"]})
    return {"issues": issues, "obligations": obligations}


def inspect_source_content_limits(obj: dict[str, Any], shots: list[dict[str, Any]], *, protected_alignment: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        return _inspect_source_content_limits(obj, shots, protected_alignment)
    except (TypeError, ValueError, KeyError, IndexError, AttributeError) as exc:
        return {"issues": [f"source-content-limit: malformed binding ({type(exc).__name__})"], "obligations": []}
