#!/usr/bin/env bash
set -e

export DISPLAY="${DISPLAY:-:0}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export PYTHONUNBUFFERED=1

cd "$(dirname "$0")"
exec ./.venv/bin/python -u -m omar_ai_core
