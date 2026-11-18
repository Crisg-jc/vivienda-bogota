#!/usr/bin/env sh
# Linux / macOS: instala (la primera vez) y abre la aplicación.   ./iniciar.sh [--depurar]
set -e
cd "$(dirname "$0")"
PY=$(command -v python3 || command -v python || true)
[ -z "$PY" ] && { echo "[ERROR] Instale Python 3.11 o más reciente"; exit 1; }
[ -x .venv/bin/python ] || "$PY" -m venv .venv
.venv/bin/python herramientas/preparar.py
exec .venv/bin/python -m app --abrir "$@"
