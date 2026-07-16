#!/usr/bin/env bash
set -u

cd "$(dirname "$0")"

if [ ! -x ./.venv/bin/python ]; then
  echo "[FAIL] No existe ./.venv/bin/python. Ejecuta primero install_pi4.sh."
  exit 2
fi

exec ./.venv/bin/python -u -m tools.diagnose_pi_live
