#!/usr/bin/env python3
"""Keep the original CLI entry point for the canonical storage regression test."""
from pathlib import Path
import runpy


if __name__ == "__main__":
    runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "tests" / "test_v0516_external_storage.py"),
        run_name="__main__",
    )
