#!/usr/bin/env bash
# One-shot setup: virtualenv, Python dependencies and model weights.
# Usage: ./setup.sh            (creates .venv in the repo)
set -euo pipefail
cd "$(dirname "$0")"

PY=${PYTHON:-python3}
"$PY" -c 'import sys; assert sys.version_info >= (3, 10), "Python >= 3.10 required"'

if [ ! -d .venv ]; then
  "$PY" -m venv .venv
fi
.venv/bin/pip install --upgrade pip >/dev/null
.venv/bin/pip install -r requirements-dev.txt

# cairo is only needed for PNG previews; warn instead of failing.
if ! .venv/bin/python -c 'import cairosvg' 2>/dev/null; then
  echo "WARN: cairosvg/cairo not usable – PNG previews will be skipped."
  echo "      macOS: brew install cairo   Debian/Ubuntu: sudo apt install libcairo2"
fi

# DexiNed weights (~47 MB, opencv_zoo, MIT) -> weights/, folded to fp32.
.venv/bin/python -m lineart.models

echo
echo "Done. Run:  .venv/bin/python pipeline.py -i source/ -o output/"
