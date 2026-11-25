#!/usr/bin/env bash
# Venue Demand Intelligence - start script (Mac / Linux)
set -euo pipefail
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"
if [ -z "${PYTHON:-}" ] && [ -x .venv/bin/python ]; then PY=.venv/bin/python; fi
command -v "$PY" >/dev/null 2>&1 || PY=python
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "Python 3.10 or newer is required. Install Python and try again." >&2
  exit 1
fi
"$PY" -c "import pandas, numpy, dotenv" 2>/dev/null || "$PY" -m pip install -r requirements.txt
echo "Open http://localhost:${VDI_PORT:-8050}"
exec "$PY" -m vdi.server "$@"
