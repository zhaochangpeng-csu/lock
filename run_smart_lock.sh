#!/usr/bin/env bash
set -u

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT" || exit 1

PID_DIR="$ROOT/logs/run"
LOG_DIR="$ROOT/logs"
mkdir -p "$PID_DIR" "$LOG_DIR"

GATEWAY_PID="$PID_DIR/gateway.pid"
AGENT_PID="$PID_DIR/agent.pid"
GUI_PID="$PID_DIR/gui.pid"
AUTH_CONTEXT="${LOCK_AUTH_CONTEXT_PATH:-$ROOT/logs/auth_context.json}"

# Load .env without overriding already-exported variables.
if [ -f "$ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$ROOT/.env"
  set +a
fi
export SMART_LOCK_NO_UNLOCK="${SMART_LOCK_NO_UNLOCK:-1}"
export LOCK_AUTH_CONTEXT_PATH="$AUTH_CONTEXT"

is_running() {
  local pidfile="$1"
  local pid=""
  [ -f "$pidfile" ] || return 1
  pid="$(cat "$pidfile" 2>/dev/null || true)"
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

stop_one() {
  local name="$1" pidfile="$2"
  if [ -f "$pidfile" ]; then
    local pid=""
    pid="$(cat "$pidfile" 2>/dev/null || true)"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      for _ in 1 2 3 4 5; do
        kill -0 "$pid" 2>/dev/null || break
        sleep 1
      done
    fi
    rm -f "$pidfile"
  fi
  echo "stopped $name"
}

start_gateway_once() {
  if is_running "$GATEWAY_PID"; then
    return 0
  fi
  nohup python3 -B lock_tool_gateway.py --host "${LOCK_TOOL_GATEWAY_HOST:-0.0.0.0}" \
    --port "${LOCK_TOOL_GATEWAY_PORT:-8787}" \
    > "$LOG_DIR/gateway.log" 2>&1 &
  echo $! > "$GATEWAY_PID"
  echo "gateway started pid=$(cat "$GATEWAY_PID")"
}

start_agent_once() {
  if is_running "$AGENT_PID"; then
    return 0
  fi
  # Preloads FunASR and waits for a fresh hardware auth credential
  # before opening the microphone. It must be started before the GUI.
  nohup python3 -B voice_agent_pipecat.py --wait-auth \
    > "$LOG_DIR/agent.log" 2>&1 &
  echo $! > "$AGENT_PID"
  echo "agent started pid=$(cat "$AGENT_PID")"
}

start_gui_once() {
  if [ -z "${DISPLAY:-}" ]; then
    return 0
  fi
  if is_running "$GUI_PID"; then
    return 0
  fi
  nohup env DISPLAY="$DISPLAY" python3 -B gui.py --hardware --no-unlock \
    > "$LOG_DIR/gui.log" 2>&1 &
  echo $! > "$GUI_PID"
  echo "gui started pid=$(cat "$GUI_PID")"
}

supervise_gateway() {
  while true; do
    if ! is_running "$GATEWAY_PID"; then
      echo "$(date '+%F %T') gateway down, restarting" >> "$LOG_DIR/supervisor.log"
      start_gateway_once
    fi
    sleep 5
  done
}

supervise_agent() {
  while true; do
    if ! is_running "$AGENT_PID"; then
      echo "$(date '+%F %T') agent down, restarting" >> "$LOG_DIR/supervisor.log"
      start_agent_once
    fi
    sleep 5
  done
}

supervise_gui() {
  while true; do
    if [ -n "${DISPLAY:-}" ] && ! is_running "$GUI_PID"; then
      echo "$(date '+%F %T') gui down, restarting" >> "$LOG_DIR/supervisor.log"
      start_gui_once
    fi
    sleep 10
  done
}

cleanup() {
  echo "stopping supervised services..."
  stop_one gateway "$GATEWAY_PID" || true
  stop_one agent "$AGENT_PID" || true
  stop_one gui "$GUI_PID" || true
}

trap cleanup EXIT INT TERM

if [ "${1:-}" = "--stop" ]; then
  cleanup
  exit 0
fi

# Idempotent startup: never start duplicate processes.
rm -f "$AUTH_CONTEXT"   # stale credentials must not wake the Agent.

start_gateway_once
start_agent_once
if [ -n "${DISPLAY:-}" ]; then
  start_gui_once
fi

supervise_gateway &
supervise_agent &
supervise_gui &

echo "smart lock supervisor running; logs in $LOG_DIR"
echo "gateway pidfile: $GATEWAY_PID"
echo "agent pidfile:   $AGENT_PID"
echo "gui pidfile:     $GUI_PID"

# Keep the supervisor in the foreground.
wait
