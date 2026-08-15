#!/usr/bin/env bash
set -euo pipefail

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PLUGIN_ROOT}/.venv/bin/python"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "请先运行 ./scripts/setup_local.sh" >&2
  exit 1
fi

export PYTHONPATH="${PLUGIN_ROOT}/server"
"${PYTHON_BIN}" -m compileall -q "${PLUGIN_ROOT}/server" "${PLUGIN_ROOT}/tests"
"${PYTHON_BIN}" "${PLUGIN_ROOT}/server/main.py" --self-test
"${PYTHON_BIN}" -m unittest discover -s "${PLUGIN_ROOT}/tests" -p "test_*.py" -v
"${PYTHON_BIN}" "${PLUGIN_ROOT}/tests/stdio_smoke.py"
"${PYTHON_BIN}" "${PLUGIN_ROOT}/tests/http_smoke.py"
