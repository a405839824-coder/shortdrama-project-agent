"""Exact scope of reviewed indirect speech; server storage supplies authority.

Offline compilation checks bindings only. A receipt ID is not a signature and
never certifies semantics or permits publication without the service gate.
"""
from __future__ import annotations

import hashlib
import json

PROPOSAL = "source-dialogue-proposal-v1"
REVIEW = "source-dialogue-review-v1"
DIMENSIONS = ("source_entailment", "speaker_attribution", "conditions_and_causality",
              "no_added_plot", "original_dialogue_preserved")


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def bound_realizations(graph):
    """Return exact approved-use claims; caller must verify live signed records."""
    rows = graph.get("dialogue_realizations", [])
    if not isinstance(rows, list):
        raise ValueError("source_realization_shape: expected a receipt list")
    result = {}
    facts = {f["id"]: f for f in graph["facts"]}
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {"proposal_id", "review_id", "binding"}
                or not all(isinstance(row[k], str) and row[k].startswith("NAR-")
                           for k in ("proposal_id", "review_id"))):
            raise ValueError("source_realization_shape: exact proposal, review and binding required")
        binding = row["binding"]
        if (not isinstance(binding, dict) or set(binding) != {"sources", "facts", "uses"}
                or not isinstance(binding["sources"], dict) or not binding["sources"]
                or not isinstance(binding["facts"], list) or not binding["facts"]
                or not isinstance(binding["uses"], list) or not binding["uses"]):
            raise ValueError("source_realization_binding_invalid")
        selected = {f["id"]: f for f in binding["facts"]}
        if (len(selected) != len(binding["facts"])
                or any(graph["sources"].get(k) != v for k, v in binding["sources"].items())
                or any(facts.get(k) != v for k, v in selected.items())):
            raise ValueError("source_realization_source_or_fact_changed")
        if any(s["source_id"] not in binding["sources"]
               for f in selected.values() for s in f["supports"]):
            raise ValueError("source_realization_source_unbound")
        for use in binding["uses"]:
            if (not isinstance(use, dict) or set(use) != {"id", "fact_ids", "mode", "wording", "actor", "text"}
                    or use["mode"] != "spoken" or use["wording"] != "adapted"
                    or not isinstance(use["id"], str) or not use["id"]
                    or not isinstance(use["actor"], str) or not use["actor"]
                    or not isinstance(use["text"], str) or not use["text"].strip()
                    or not isinstance(use["fact_ids"], list) or not use["fact_ids"]
                    or any(fid not in selected for fid in use["fact_ids"])):
                raise ValueError("source_realization_use_invalid")
            if use["id"] in result:
                raise ValueError("source_realization_duplicate_use")
            event = selected[use["fact_ids"][0]]["event_id"]
            oral = [selected[fid] for fid in use["fact_ids"]
                    if selected[fid]["event_id"] == event and selected[fid]["channel"] == "oral"]
            # Existing direct words, paper, thoughts and speculative speech do
            # not become rewriteable by attaching an indirect-speech receipt.
            if not oral or any(f["subject"] != use["actor"]
                    or f["content_availability"] != "unstated" or f["content_policy"] != "event_only"
                    or f["content_text"] != "" or f["expression_status"] != "asserted"
                    or f["expression_occurrence"] not in {"occurred", "current"} for f in oral):
                raise ValueError("source_realization_protected_or_nonactual_speech")
            result[use["id"]] = {"use": use, "proposal_id": row["proposal_id"], "review_id": row["review_id"],
                                  "binding_sha256": digest(binding), "ordered_use_ids": [u["id"] for u in binding["uses"]]}
    return result


def validate_proposal(plan, source_text):
    if (not isinstance(plan, dict) or set(plan) != {"contract", "source_artifact_id", "source_sha256", "source_texts", "binding"}
            or plan["contract"] != PROPOSAL or not isinstance(plan["source_texts"], dict)):
        raise ValueError("source_realization_proposal_shape")
    binding = plan["binding"]
    texts = plan["source_texts"]
    if (not isinstance(binding, dict) or set(texts) != set(binding.get("sources", {}))
            or any(not isinstance(t, str) or not t or source_text.count(t) != 1
                   or hashlib.sha256(t.encode("utf-8")).hexdigest() != binding["sources"][sid]
                   for sid, t in texts.items())):
        raise ValueError("source_realization_source_anchor: each exact excerpt must identify one occurrence in the current source")
    graph = {"contract": "source-facts-v1", "sources": binding["sources"], "facts": binding["facts"],
             "dialogue_realizations": [{"proposal_id": "NAR-pending", "review_id": "NAR-pending", "binding": binding}]}
    from source_decisions import compile_source_decisions, graph_sha256
    compile_source_decisions(graph, {"contract": "source-decisions-v1", "uses": binding["uses"], "written_objects": []},
                             source_texts=texts, expected_graph_sha256=graph_sha256(graph))


def validate_review(plan, review):
    permitted = {"contract", "target_sha256", "checks", "supersedes_review_id", "change_reason"}
    if (not isinstance(review, dict) or set(review) - permitted or review.get("contract") != REVIEW
            or not isinstance(review.get("checks"), list)):
        raise ValueError("source_realization_review_shape")
    expected = {(use["id"], dimension) for use in plan["binding"]["uses"] for dimension in DIMENSIONS}
    seen, clean = set(), True
    for row in review["checks"]:
        if not isinstance(row, dict) or set(row) != {"use_id", "dimension", "status", "evidence", "reason"}:
            raise ValueError("source_realization_review_check_shape")
        key = (row["use_id"], row["dimension"])
        if key not in expected or key in seen or row["status"] not in {"pass", "fail", "unknown"}:
            raise ValueError("source_realization_review_coverage")
        seen.add(key)
        if not isinstance(row["reason"], str) or not row["reason"].strip() or not isinstance(row["evidence"], list) or not row["evidence"]:
            raise ValueError("source_realization_review_evidence_required")
        for span in row["evidence"]:
            if not isinstance(span, dict) or set(span) != {"source_id", "start", "end", "text"}:
                raise ValueError("source_realization_review_span_invalid")
            text = plan["source_texts"].get(span["source_id"])
            a, b = span["start"], span["end"]
            if not isinstance(text, str) or type(a) is not int or type(b) is not int or not 0 <= a < b <= len(text) or text[a:b] != span["text"]:
                raise ValueError("source_realization_review_span_invalid")
        clean = clean and row["status"] == "pass"
    if seen != expected:
        raise ValueError("source_realization_review_coverage")
    return clean
