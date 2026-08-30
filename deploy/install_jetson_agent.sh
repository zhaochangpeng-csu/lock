#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3}"

echo "==> Checking Python version"
"$PYTHON" - <<'PYCHECK'
import sys
if sys.version_info < (3, 10):
    print("ERROR: Pipecat requires Python >= 3.10.")
    print("Current:", sys.version)
    print("Install Miniforge/conda Python 3.10 on Jetson Nano first.")
    raise SystemExit(1)
print("Python OK:", sys.version.split()[0])
PYCHECK

echo "==> Installing pipecat-ai 0.0.108 without upstream dependency resolution"
"$PYTHON" -m pip install --no-deps pipecat-ai==0.0.108

echo "==> Installing Jetson-compatible Pipecat dependencies"
"$PYTHON" -m pip install -r deploy/requirements-agent-jetson.txt

echo "==> Checking Pipecat runtime imports"
"$PYTHON" - <<'PYIMPORT'
import pipecat, onnxruntime, numba, soxr
print("pipecat:", pipecat.__version__)
print("onnxruntime:", onnxruntime.__version__)
print("numba:", numba.__version__)
print("soxr:", soxr.__version__)
PYIMPORT

echo "==> Jetson Pipecat dependencies installed"
