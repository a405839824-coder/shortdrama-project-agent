"""Serialize explicit per-obligation decisions; never manufacture audit verdicts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def serialize_review(manifest, decisions):
    if set(decisions) != {"checks"} or not isinstance(decisions["checks"], dict):
        raise ValueError("explicit_review_required: only checks keyed by obligation_id; no rule_defaults")
    required = {row["obligation_id"]: row for row in manifest["obligations"]}
    if len(required) != len(manifest["obligations"]) or set(decisions["checks"]) != set(required):
        raise ValueError("explicit_review_missing_or_extra: unreviewed obligations remain unknown")
    checks = []
    for identity, obligation in required.items():
        check = decisions["checks"][identity]
        if (not isinstance(check, dict) or check.get("obligation_id") != identity
                or check.get("dependency_sha256") != obligation["dependency_sha256"]
                or check.get("status") not in {"pass", "fail_current", "unknown"}
                or not str(check.get("judgement") or "").strip()
                or not isinstance(check.get("observations"), list)):
            raise ValueError("explicit_review_invalid_or_stale: " + identity)
        checks.append(check)
    return {"contract": "semantic-obligation-coverage-v1",
            "manifest_sha256": manifest["manifest_sha256"], "checks": checks}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("decisions", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = serialize_review(json.loads(args.manifest.read_text(encoding="utf-8")),
                              json.loads(args.decisions.read_text(encoding="utf-8")))
    # This is a transport draft, never a signed audit or semantic proof.
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
