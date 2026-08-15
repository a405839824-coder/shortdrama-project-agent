#!/usr/bin/env bash
set -euo pipefail

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"

"${PYTHON_BIN}" -m venv "${PLUGIN_ROOT}/.venv"
"${PLUGIN_ROOT}/.venv/bin/python" -m pip install --upgrade pip
"${PLUGIN_ROOT}/.venv/bin/python" -m pip install -r "${PLUGIN_ROOT}/requirements.txt"

echo "本地环境已建立：${PLUGIN_ROOT}/.venv"

