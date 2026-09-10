#!/usr/bin/env python3
"""Run the rule-upgrade regressions as one certificate-bound JSON suite."""
from __future__ import annotations

import io
import json
import unittest


TEST_MODULES = (
    "test_timing_model",
    "test_timing_events",
    "test_timing_boundaries",
    "test_dialogue_occurrences",
    "test_performance_checks",
    "test_lighting_checks",
    "test_practical_light_negation",
    "test_rule_registry",
)


def main() -> int:
    suite = unittest.defaultTestLoader.loadTestsFromNames(TEST_MODULES)
    output = io.StringIO()
    result = unittest.TextTestRunner(stream=output, verbosity=2).run(suite)
    failed = len(result.failures) + len(result.errors)
    skipped = len(result.skipped)
    # A skipped or unexpectedly successful negative check is not release evidence.
    passed = result.testsRun - failed - skipped - len(result.unexpectedSuccesses)
    clean = result.wasSuccessful() and skipped == 0 and result.testsRun > 0
    print(json.dumps({
        "status": "pass" if clean else "fail",
        "test_count": result.testsRun,
        "test_pass_count": passed,
        "test_fail_count": failed + skipped + len(result.unexpectedSuccesses),
        "test_modules": list(TEST_MODULES),
        "speech_calibration_status": "not_measured",
        "failure_details": output.getvalue() if not clean else "",
    }, ensure_ascii=False, indent=2))
    return 0 if clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
