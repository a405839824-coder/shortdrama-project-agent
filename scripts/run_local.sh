#!/usr/bin/env bash
set -euo pipefail

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PLUGIN_ROOT}/.venv/bin/python"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "请先运行 ./scripts/setup_local.sh" >&2
  exit 1
fi

export HOST="${HOST:-127.0.0.1}"
export PORT="${PORT:-8000}"
export PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-http://${HOST}:${PORT}}"
export SHORTDRAMA_DEPLOYMENT_MODE="${SHORTDRAMA_DEPLOYMENT_MODE:-local}"
export SHORTDRAMA_AUTH_MODE="${SHORTDRAMA_AUTH_MODE:-none}"
export SHORTDRAMA_DATA_DIR="${SHORTDRAMA_DATA_DIR:-${PLUGIN_ROOT}/data}"

exec "${PYTHON_BIN}" "${PLUGIN_ROOT}/server/main.py" --http

