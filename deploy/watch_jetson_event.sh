#!/usr/bin/env bash
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INTERVAL="${EVENT_SYNC_INTERVAL_SECONDS:-10}"

case "$INTERVAL" in
  ''|*[!0-9]*)
    echo "EVENT_SYNC_INTERVAL_SECONDS must be a positive integer" >&2
    exit 2
    ;;
esac
if [ "$INTERVAL" -lt 1 ]; then
  echo "EVENT_SYNC_INTERVAL_SECONDS must be at least 1" >&2
  exit 2
fi

trap 'exit 0' INT TERM

while true; do
  "$ROOT/deploy/sync_jetson_event.sh" || true
  sleep "$INTERVAL" &
  wait $!
done
