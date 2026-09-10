"""Bind a repair observation to the full candidate and freshly run checks.

Hashes detect stale/tampered observations; they are not independent-audit
signatures. The service separately owns and copies the immutable source history.
No candidate-supplied pass or count can replace the current validator run.
"""
from __future__ import annotations

import copy
import hashlib
import json
from functools import lru_cache
from pathlib import Path

CONTRACT = "current-repair-check-v1"
FIELD = "current_check"
HISTORY_PREFIXES = ("repair_history[", "prevention_evidence.repair_history")


def _history(storyboard):
    evidence = storyboard.get("prevention_evidence") if isinstance(storyboard, dict) else None
    history = evidence.get("repair_history") if isinstance(evidence, dict) else None
    return history if isinstance(history, list) else []


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def current_diagnostics(issues):
    """Keep every current finding, including workflow faults and warnings."""
    fields = ("error_code", "location", "evidence", "severity", "blocking", "shot_ids")
    return sorted({digest({k: row[k] for k in fields if k in row})
                   for row in issues
                   if not str(row.get("location", "")).startswith(HISTORY_PREFIXES)})


def candidate_digest(storyboard):
    obj = copy.deepcopy(storyboard)
    history = _history(obj)
    if history and isinstance(history[-1], dict):
        history[-1].pop(FIELD, None)
    return digest(obj)


@lru_cache(maxsize=8)
def _files_digest(manifest):
    return digest([(Path(path).name, hashlib.sha256(Path(path).read_bytes()).hexdigest())
                   for path, _size, _mtime in manifest])


def checker_digest(rules):
    scripts = Path(__file__).resolve().parent
    # A helper implementation change invalidates observations even if the top
    # level validator file and its advertised version have not changed.
    manifest = tuple((str(p), p.stat().st_size, p.stat().st_mtime_ns)
                     for p in sorted(scripts.glob("*.py"))
                     if not p.name.startswith(("test_", "run_")))
    return digest({"rules": rules, "implementation": _files_digest(manifest)})


def make_check(storyboard, issues, rules):
    history = _history(storyboard)
    if not history or not isinstance(history[-1], dict):
        raise ValueError("current_repair_check_requires_history")
    diagnostics = current_diagnostics(issues)
    return {"contract": CONTRACT, "candidate_sha256": candidate_digest(storyboard),
            "history_prefix_sha256": digest(history[:-1]),
            "checker_sha256": checker_digest(rules),
            "current_diagnostics_sha256": digest(diagnostics),
            "current_issue_count": len(diagnostics),
            "scope": "complete_candidate", "semantic_closure": "requires_independent_audit"}


def check_faults(storyboard, issues, rules):
    history = _history(storyboard)
    if not isinstance(history, list) or not history or not isinstance(history[-1], dict):
        return []
    actual = history[-1].get(FIELD)
    if actual is None:
        return ["当前修正轮缺少绑定整份候选、历史前缀及实际检查结果的 current_check。"]
    try:
        expected = make_check(storyboard, issues, rules)
    except (ValueError, TypeError, OverflowError):
        return ["当前修正观察含无法规范化的值，不能核验 current_check。"]
    if actual != expected:
        return ["current_check 与当前候选、历史前缀、检查器或本次实际诊断不一致；必须重新实测。"]
    return []
