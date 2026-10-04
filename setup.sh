#!/usr/bin/env bash
# One-shot setup for the lineart pipeline (macOS / Linux).
#
# Usage:
#   ./setup.sh            # detect python, create .venv, install deps + binaries
#   ./setup.sh --no-py    # reuse an existing .venv (must exist)
#
# Afterwards:
#   source .venv/bin/activate
#   python pipeline.py -i source/ -o output/ -d medium

set -euo pipefail
cd "$(dirname "$0")"

HAS_PY=1
for arg in "$@"; do
  case "$arg" in
    --no-py) HAS_PY=0 ;;
  esac
done

# --- 1. Python venv ---------------------------------------------------------
if [ "$HAS_PY" = "1" ]; then
  if [ -d .venv ]; then
    echo ".venv exists – reusing."
  elif command -v uv >/dev/null 2>&1; then
    echo "Creating .venv with uv (Python 3.12 preferred for wheel availability)..."
    uv venv --python 3.12 .venv
  else
    echo "Creating .venv with python3..."
    python3 -m venv .venv
  fi
fi

PY=.venv/bin/python
[ -x "$PY" ] || { echo "error: .venv/bin/python not found" >&2; exit 1; }

# --- 2. Python dependencies -------------------------------------------------
if command -v uv >/dev/null 2>&1; then
  uv pip install --python "$PY" -r requirements.txt
else
  "$PY" -m pip install --upgrade pip >/dev/null
  "$PY" -m pip install -r requirements.txt
fi

# --- 3. Native tools ---------------------------------------------------------
if command -v potrace >/dev/null 2>&1; then
  echo "potrace: $$(potrace --version | head -1)"
elif command -v brew >/dev/null 2>&1; then
  brew install potrace
elif command -v apt-get >/dev/null 2>&1; then
  sudo apt-get install -y potrace
else
  echo "warning: potrace not found and no brew/apt – please install it manually (required)."
fi

if command -v resvg >/dev/null 2>&1; then
  echo "resvg: $$(resvg --version)"
elif command -v brew >/dev/null 2>&1; then
  brew install resvg || echo "warning: resvg install failed – PNG previews will be skipped."
else
  echo "note: resvg not available – PNG previews will be skipped (SVGs are still produced)."
fi

echo
echo "Setup complete."
echo "Run the pipeline:"
echo "  source .venv/bin/activate"
echo "  python pipeline.py -i source/ -o output/ -d medium"
