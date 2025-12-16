#!/usr/bin/env bash
set -euo pipefail

# Create & activate a local venv in .venv/, then install dependencies.
# Usage:
#   ./scripts/venv.sh

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"

needs_recreate=false
if [[ -d ".venv" ]]; then
  # If a previous venv creation failed, we may end up with a partial .venv (no activate/pip).
  if [[ ! -f ".venv/bin/activate" ]] || [[ ! -x ".venv/bin/pip" ]]; then
    needs_recreate=true
  fi
fi

if [[ "$needs_recreate" == "true" ]]; then
  echo "Detected a partial .venv (missing activate/pip). Recreating..."
  rm -rf .venv
fi

if [[ ! -d ".venv" ]]; then
  set +e
  "$PYTHON_BIN" -m venv .venv
  status=$?
  set -e
  if [[ $status -ne 0 ]]; then
    echo
    echo "Failed to create venv. On Debian/Ubuntu you likely need python3-venv:"
    echo "  sudo apt update && sudo apt install -y python3-venv"
    echo
    echo "Then rerun:"
    echo "  ./scripts/venv.sh"
    exit $status
  fi
fi

# shellcheck disable=SC1091
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

echo
echo "Venv ready. Activate with:"
echo "  source .venv/bin/activate"


