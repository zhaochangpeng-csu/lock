#!/usr/bin/env bash
set -euo pipefail

JETSON_HOST="${JETSON_HOST:-192.168.1.89}"
JETSON_USER="${JETSON_USER:-newland}"
JETSON_PROJECT="${JETSON_PROJECT:-/home/newland/smart_lock_ai_20260829_1915}"
SSH_KEY="${SSH_KEY:-/home/hoyo/.ssh/id_rsa}"
CONTROL_PATH="${CONTROL_PATH:-/tmp/smart-lock-jetson-tunnel.sock}"
LOCAL_GATEWAY_BIND="${LOCAL_GATEWAY_BIND:-0.0.0.0}"
TOOL_GATEWAY_PORT="${LOCK_TOOL_GATEWAY_PORT:-8787}"
FASTGPT_PORT="${FASTGPT_PORT:-3300}"
REMOTE="$JETSON_USER@$JETSON_HOST"
SSH=(ssh -i "$SSH_KEY" -o BatchMode=yes -o ConnectTimeout=8)

tunnel_running() {
  [ -S "$CONTROL_PATH" ] && ssh -S "$CONTROL_PATH" -O check "$REMOTE" >/dev/null 2>&1
}

start_tunnel() {
  tunnel_running && return 0
  rm -f "$CONTROL_PATH"
  ssh -M -S "$CONTROL_PATH" -fnNT \
    -i "$SSH_KEY" \
    -o BatchMode=yes \
    -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=15 \
    -o ServerAliveCountMax=3 \
    -L "$LOCAL_GATEWAY_BIND:$TOOL_GATEWAY_PORT:127.0.0.1:$TOOL_GATEWAY_PORT" \
    -R "$FASTGPT_PORT:127.0.0.1:$FASTGPT_PORT" \
    "$REMOTE"
  echo "SSH tunnel started"
}

stop_tunnel() {
  if tunnel_running; then
    ssh -S "$CONTROL_PATH" -O exit "$REMOTE" >/dev/null 2>&1 || true
  fi
  rm -f "$CONTROL_PATH"
  echo "SSH tunnel stopped"
}

start_remote() {
  "${SSH[@]}" "$REMOTE" \
    "cd '$JETSON_PROJECT' && mkdir -p logs/run && \
     if [ -f logs/run/supervisor.pid ] && kill -0 \"\$(cat logs/run/supervisor.pid)\" 2>/dev/null; then \
       echo 'Jetson stack already running'; \
     else \
       nohup env DISPLAY=:0 XAUTHORITY=/home/newland/.Xauthority \
         XDG_RUNTIME_DIR=/run/user/1000 \
         DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus \
         PULSE_SERVER=unix:/run/user/1000/pulse/native \
         FASTGPT_API_BASE='http://127.0.0.1:$FASTGPT_PORT' \
         LOCK_TOOL_GATEWAY_PORT='$TOOL_GATEWAY_PORT' \
         ./run_smart_lock.sh run > logs/full_stack.log 2>&1 < /dev/null & \
       sleep 1; ./run_smart_lock.sh status; \
     fi"
}

stop_remote() {
  "${SSH[@]}" "$REMOTE" "cd '$JETSON_PROJECT' && ./run_smart_lock.sh stop" || true
}

status() {
  if tunnel_running; then echo "tunnel: running"; else echo "tunnel: stopped"; fi
  "${SSH[@]}" "$REMOTE" "cd '$JETSON_PROJECT' && ./run_smart_lock.sh status"
}

cleanup() {
  trap - EXIT INT TERM HUP
  stop_remote
  stop_tunnel
}

case "${1:-run}" in
  start)
    start_tunnel
    start_remote
    status
    ;;
  stop)
    cleanup
    ;;
  restart)
    cleanup
    start_tunnel
    start_remote
    status
    ;;
  status)
    status
    ;;
  run)
    trap cleanup EXIT INT TERM HUP
    start_tunnel
    start_remote
    status
    echo "Full stack is running. Press Ctrl+C to stop and clean everything."
    while true; do sleep 30; done
    ;;
  *)
    echo "usage: $0 [run|start|stop|restart|status]" >&2
    exit 2
    ;;
esac
