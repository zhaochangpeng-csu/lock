#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
export DISPLAY="${DISPLAY:-:0}"
python3 gui.py --hardware --no-unlock

