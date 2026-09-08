"""Distribute every unittest discovery occurrence, including repeated imports."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import unittest


def flatten(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from flatten(test)
        else:
            yield test


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("index", type=int)
    parser.add_argument("--count", type=int, default=4)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.count < 1 or not 0 <= args.index < args.count:
        parser.error("index must be in [0, count), and count must be positive")
    root = Path(__file__).resolve().parents[1]
    paths = [str(root / "server"), str(root / "tests")]
    sys.path[:0] = paths
    os.environ["PYTHONPATH"] = os.pathsep.join(paths)
    tests = list(flatten(unittest.defaultTestLoader.discover(str(root / "tests"), pattern="test_*.py")))
    indexed = [(f"{index}:{test.id()}", test) for index, test in enumerate(tests)]
    selected = [(identity, test) for identity, test in indexed
                if int(hashlib.sha256(identity.encode()).hexdigest(), 16) % args.count == args.index]
    started = time.monotonic()
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(test for _, test in selected))
    report = {
        "shard": args.index, "shard_count": args.count,
        "total_discovered": len(tests), "unique_test_ids": len({test.id() for test in tests}),
        "execution_ids": [identity for identity, _ in selected],
        "tests_run": result.testsRun,
        "failures": [(test.id(), error) for test, error in result.failures],
        "errors": [(test.id(), error) for test, error in result.errors],
        "skipped": [(test.id(), reason) for test, reason in result.skipped],
        "seconds": round(time.monotonic() - started, 2),
        "successful": result.wasSuccessful(),
    }
    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
