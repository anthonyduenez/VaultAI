#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Start the local-AI-track demo against the GX10's already-running vLLM server.
# This script never starts a cloud service, changes .env, or prints document data.

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT_DIR/.env"
PYTHON="$ROOT_DIR/backend/.venv/bin/python"
RUN_DIR="$ROOT_DIR/data/run"
LOG_FILE="$RUN_DIR/backend.log"
PID_FILE="$RUN_DIR/backend.pid"
MODEL_URL="http://127.0.0.1:8000/v1/models"
APP_URL="http://127.0.0.1:8765"
HEALTH_URL="$APP_URL/api/system/status"
EXPECTED_MODEL="nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4"

usage() {
  cat <<'EOF'
Usage: ./scripts/start_gx10_demo.sh [--load-samples|--stop|--help]

  --load-samples  start VaultAI and import/process the 13 bundled demo files
  --stop          stop the backend started by this script
  --help          show this help
EOF
}

fail() {
  echo "VaultAI demo: $*" >&2
  exit 1
}

env_value() {
  local key="$1"
  sed -n "s/^${key}=//p" "$ENV_FILE" | tail -n 1 | sed 's/[[:space:]]*#.*$//' | tr -d '\r' | xargs
}

is_healthy() {
  curl --silent --show-error --fail --max-time 3 "$HEALTH_URL" >/dev/null 2>&1
}

stop_backend() {
  if [[ ! -f "$PID_FILE" ]]; then
    echo "VaultAI demo: no launcher PID file found."
    exit 0
  fi

  local pid
  pid="$(cat "$PID_FILE")"
  if ! [[ "$pid" =~ ^[0-9]+$ ]]; then
    rm -f "$PID_FILE"
    fail "invalid PID file: $PID_FILE"
  fi

  if ! kill -0 "$pid" 2>/dev/null; then
    rm -f "$PID_FILE"
    echo "VaultAI demo: backend was already stopped."
    exit 0
  fi

  kill "$pid"
  for _ in {1..30}; do
    if ! kill -0 "$pid" 2>/dev/null; then
      rm -f "$PID_FILE"
      echo "VaultAI demo: backend stopped."
      exit 0
    fi
    sleep 1
  done

  fail "backend did not stop after 30 seconds; inspect $LOG_FILE"
}

case "${1:-}" in
  --help|-h)
    usage
    exit 0
    ;;
  --stop)
    stop_backend
    ;;
  ""|--load-samples)
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac

command -v curl >/dev/null 2>&1 || fail "curl is required"
[[ -x "$PYTHON" ]] || fail "missing $PYTHON; install backend requirements first"

if [[ ! -f "$ENV_FILE" ]]; then
  cp "$ROOT_DIR/.env.example" "$ENV_FILE"
  echo "VaultAI demo: created .env from .env.example"
fi

model_backend="$(env_value VAULTAI_MODEL_BACKEND)"
model_endpoint="$(env_value VAULTAI_MODEL_ENDPOINT)"
model_name="$(env_value VAULTAI_MODEL_NAME)"
phone_enabled="$(env_value VAULTAI_PHONE_ENABLED)"

[[ "$model_backend" == "openai_compat" ]] || fail "set VAULTAI_MODEL_BACKEND=openai_compat in .env"
[[ "$model_endpoint" == "http://127.0.0.1:8000/v1" ]] || fail "set VAULTAI_MODEL_ENDPOINT=http://127.0.0.1:8000/v1 in .env"
[[ "$model_name" == "$EXPECTED_MODEL" ]] || fail "set VAULTAI_MODEL_NAME=$EXPECTED_MODEL in .env"
[[ "$phone_enabled" == "false" ]] || fail "set VAULTAI_PHONE_ENABLED=false in .env for the imported-files demo"

model_response="$(curl --silent --show-error --fail --max-time 10 "$MODEL_URL")" \
  || fail "Nemotron is not reachable at $MODEL_URL; start the GX10 vLLM server first"
grep -Fq "$EXPECTED_MODEL" <<<"$model_response" \
  || fail "the model endpoint does not advertise $EXPECTED_MODEL"

if [[ ! -f "$ROOT_DIR/frontend/dist/index.html" ]]; then
  [[ -d "$ROOT_DIR/frontend/node_modules" ]] || fail "frontend/node_modules is missing; run npm install in frontend first"
  echo "VaultAI demo: building frontend"
  npm --prefix "$ROOT_DIR/frontend" run build
fi

mkdir -p "$RUN_DIR"

if is_healthy; then
  echo "VaultAI demo: backend already healthy at $APP_URL"
else
  if [[ -f "$PID_FILE" ]]; then
    existing_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [[ "$existing_pid" =~ ^[0-9]+$ ]] && kill -0 "$existing_pid" 2>/dev/null; then
      fail "launcher PID $existing_pid is running but $HEALTH_URL is not healthy; inspect $LOG_FILE"
    fi
    rm -f "$PID_FILE"
  fi

  echo "VaultAI demo: starting backend; log is $LOG_FILE"
  (
    cd "$ROOT_DIR/backend"
    exec "$PYTHON" -m vaultai
  ) >>"$LOG_FILE" 2>&1 &
  backend_pid=$!
  echo "$backend_pid" >"$PID_FILE"

  ready=false
  for _ in {1..30}; do
    if is_healthy; then
      ready=true
      break
    fi
    if ! kill -0 "$backend_pid" 2>/dev/null; then
      break
    fi
    sleep 1
  done
  [[ "$ready" == true ]] || {
    echo "VaultAI demo: backend failed to become healthy. Last log lines:" >&2
    tail -n 40 "$LOG_FILE" >&2 || true
    rm -f "$PID_FILE"
    exit 1
  }
fi

if [[ "${1:-}" == "--load-samples" ]]; then
  echo "VaultAI demo: importing and processing bundled samples"
  (
    cd "$ROOT_DIR/backend"
    "$PYTHON" -m vaultai load-samples
  )
fi

echo "VaultAI demo is ready: $APP_URL"
echo "Nemotron endpoint: $MODEL_URL"
echo "Backend log: $LOG_FILE"
