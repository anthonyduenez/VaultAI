#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$ROOT_DIR/backend/.venv/bin/python"
MODEL_URL="http://127.0.0.1:8000/v1/models"
MODEL_NAME="nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4"
RESULTS_DIR="$ROOT_DIR/eval-results"

cd "$ROOT_DIR"

[[ -x "$PYTHON" ]] || {
  echo "Missing Python environment: $PYTHON"
  exit 1
}

echo "Checking Nemotron server..."
MODEL_RESPONSE="$(curl --silent --show-error --fail --max-time 10 "$MODEL_URL")"

grep -Fq "$MODEL_NAME" <<<"$MODEL_RESPONSE" || {
  echo "Expected Nemotron model was not found."
  exit 1
}

mkdir -p "$RESULTS_DIR"

for VARIANT in text stress ocr; do
  OUTPUT="$RESULTS_DIR/nemotron-$VARIANT.json"

  echo
  echo "Running Nemotron evaluation: $VARIANT"
  "$PYTHON" scripts/eval_samples.py \
    --backend openai_compat \
    --variant "$VARIANT" \
    --json "$OUTPUT"

  echo "Saved: $OUTPUT"
done

echo
echo "All Nemotron evaluations completed."
echo "Results are in: $RESULTS_DIR"
