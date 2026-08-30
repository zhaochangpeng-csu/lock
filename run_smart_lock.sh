#!/usr/bin/env bash
set -u

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT" || exit 1

PID_DIR="$ROOT/logs/run"
LOG_DIR="$ROOT/logs"
mkdir -p "$PID_DIR" "$LOG_DIR"

SUPERVISOR_PID="$PID_DIR/supervisor.pid"
GATEWAY_PID="$PID_DIR/gateway.pid"
AGENT_PID="$PID_DIR/agent.pid"
GUI_PID="$PID_DIR/gui.pid"
EVENT_PID="$PID_DIR/event_service.pid"

if [ -f "$ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$ROOT/.env"
  set +a
fi

AUTH_CONTEXT="${LOCK_AUTH_CONTEXT_PATH:-$ROOT/logs/auth_context.json}"
EVENT_PATH="${LOCK_EVENT_PATH:-$ROOT/latest_event.json}"
EVENT_PORT="${LOCK_EVENT_SERVICE_PORT:-8790}"
export SMART_LOCK_NO_UNLOCK="${SMART_LOCK_NO_UNLOCK:-1}"
export LOCK_AUTH_CONTEXT_PATH="$AUTH_CONTEXT"
export LOCK_EVENT_PATH="$EVENT_PATH"
export LOCK_EVENT_SERVICE_URL="${LOCK_EVENT_SERVICE_URL:-http://127.0.0.1:$EVENT_PORT}"

WATCHER_PIDS=()
STOPPING=0

read_pid() {
  [ -f "$1" ] && cat "$1" 2>/dev/null || true
}

is_running() {
  local pid=""
  pid="$(read_pid "$1")"
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

stop_one() {
  local name="$1" pidfile="$2" pid=""
  pid="$(read_pid "$pidfile")"
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7 8; do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.5
    done
    if kill -0 "$pid" 2>/dev/null; then
      kill -9 "$pid" 2>/dev/null || true
    fi
    wait "$pid" 2>/dev/null || true
  fi
  rm -f "$pidfile"
  echo "stopped $name"
}

status_one() {
  local name="$1" pidfile="$2" pid=""
  pid="$(read_pid "$pidfile")"
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    echo "$name: running pid=$pid"
  else
    echo "$name: stopped"
  fi
}

start_gateway_once() {
  is_running "$GATEWAY_PID" && return 0
  python3 -B lock_tool_gateway.py \
    --host "${LOCK_TOOL_GATEWAY_HOST:-0.0.0.0}" \
    --port "${LOCK_TOOL_GATEWAY_PORT:-8787}" \
    > "$LOG_DIR/gateway.log" 2>&1 &
  echo $! > "$GATEWAY_PID"
  echo "gateway started pid=$(read_pid "$GATEWAY_PID")"
}

start_event_once() {
  is_running "$EVENT_PID" && return 0
  python3 -B lock_event_service.py serve \
    --host "${LOCK_EVENT_SERVICE_HOST:-127.0.0.1}" \
    --port "$EVENT_PORT" \
    > "$LOG_DIR/event_service.log" 2>&1 &
  echo $! > "$EVENT_PID"
  echo "event service started pid=$(read_pid "$EVENT_PID")"
}

start_agent_once() {
  is_running "$AGENT_PID" && return 0
  python3 -B voice_agent_pipecat.py --wait-auth \
    > "$LOG_DIR/agent.log" 2>&1 &
  echo $! > "$AGENT_PID"
  echo "agent started pid=$(read_pid "$AGENT_PID")"
}

start_gui_once() {
  [ -n "${DISPLAY:-}" ] || return 0
  is_running "$GUI_PID" && return 0
  env DISPLAY="$DISPLAY" SMART_LOCK_AUTO_START=1 \
    python3 -B gui.py --hardware --no-unlock \
    > "$LOG_DIR/gui.log" 2>&1 &
  echo $! > "$GUI_PID"
  echo "gui started pid=$(read_pid "$GUI_PID")"
}

supervise() {
  local name="$1" pidfile="$2" starter="$3"
  while [ "$STOPPING" -eq 0 ]; do
    if ! is_running "$pidfile"; then
      echo "$(date '+%F %T') $name down, restarting" >> "$LOG_DIR/supervisor.log"
      "$starter"
    fi
    sleep 5
  done
}

supervise_gui() {
  while [ "$STOPPING" -eq 0 ]; do
    if [ -n "${DISPLAY:-}" ] && ! is_running "$GUI_PID"; then
      echo "$(date '+%F %T') gui down; resetting agent and credential" >> "$LOG_DIR/supervisor.log"
      stop_one agent "$AGENT_PID"
      rm -f "$AUTH_CONTEXT"
      start_agent_once
      start_gui_once
    fi
    sleep 5
  done
}

cleanup() {
  [ "$STOPPING" -eq 0 ] || return 0
  STOPPING=1
  trap - EXIT INT TERM HUP
  echo "stopping smart lock stack..."

  if [ "${#WATCHER_PIDS[@]}" -gt 0 ]; then
    kill "${WATCHER_PIDS[@]}" 2>/dev/null || true
    wait "${WATCHER_PIDS[@]}" 2>/dev/null || true
  fi

  stop_one gui "$GUI_PID"
  stop_one agent "$AGENT_PID"
  stop_one gateway "$GATEWAY_PID"
  stop_one event_service "$EVENT_PID"
  rm -f "$AUTH_CONTEXT" "$AUTH_CONTEXT.tmp" "$EVENT_PATH.tmp"

  if [ "$(read_pid "$SUPERVISOR_PID")" = "$$" ]; then
    rm -f "$SUPERVISOR_PID"
  fi
  echo "smart lock stack stopped"
}

stop_stack() {
  local supervisor=""
  supervisor="$(read_pid "$SUPERVISOR_PID")"
  if [ -n "$supervisor" ] && [ "$supervisor" != "$$" ] && kill -0 "$supervisor" 2>/dev/null; then
    kill "$supervisor" 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7 8; do
      kill -0 "$supervisor" 2>/dev/null || break
      sleep 0.5
    done
  fi
  rm -f "$SUPERVISOR_PID"
  cleanup
}

case "${1:-run}" in
  --stop|stop)
    stop_stack
    exit 0
    ;;
  --status|status)
    status_one supervisor "$SUPERVISOR_PID"
    status_one gui "$GUI_PID"
    status_one agent "$AGENT_PID"
    status_one gateway "$GATEWAY_PID"
    status_one event_service "$EVENT_PID"
    exit 0
    ;;
  --restart|restart)
    "$0" --stop
    exec "$0" run
    ;;
  run|start)
    ;;
  *)
    echo "usage: $0 [run|start|stop|restart|status]" >&2
    exit 2
    ;;
esac

if is_running "$SUPERVISOR_PID" && [ "$(read_pid "$SUPERVISOR_PID")" != "$$" ]; then
  echo "smart lock supervisor is already running pid=$(read_pid "$SUPERVISOR_PID")"
  exit 0
fi

echo $$ > "$SUPERVISOR_PID"
trap cleanup EXIT INT TERM HUP

if command -v pactl >/dev/null 2>&1; then
  XFM_SRC=$(pactl list short sources 2>/dev/null | grep "XFM-DP" | head -1 | awk '{print $1}')
  [ -n "$XFM_SRC" ] && pactl set-default-source "$XFM_SRC"
  USB_SINK=$(pactl list short sinks 2>/dev/null | grep -iE "usb.*(audio|c-media)" | head -1 | awk '{print $1}')
  [ -n "$USB_SINK" ] && pactl set-default-sink "$USB_SINK"
  pactl set-sink-volume @DEFAULT_SINK@ 90% 2>/dev/null || true
fi

rm -f "$AUTH_CONTEXT" "$AUTH_CONTEXT.tmp" "$EVENT_PATH.tmp"

start_gateway_once
start_event_once
start_agent_once
start_gui_once

supervise gateway "$GATEWAY_PID" start_gateway_once & WATCHER_PIDS+=("$!")
supervise event_service "$EVENT_PID" start_event_once & WATCHER_PIDS+=("$!")
supervise agent "$AGENT_PID" start_agent_once & WATCHER_PIDS+=("$!")
supervise_gui & WATCHER_PIDS+=("$!")

echo "smart lock stack running; logs in $LOG_DIR"
echo "supervisor pid=$$"
echo "press Ctrl+C to stop and clean all services"

wait
