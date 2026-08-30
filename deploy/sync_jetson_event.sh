#!/usr/bin/env bash
set -euo pipefail

JETSON_HOST="${JETSON_HOST:-192.168.1.89}"
JETSON_USER="${JETSON_USER:-newland}"
JETSON_PROJECT="${JETSON_PROJECT:-/home/newland/smart_lock_ai_20260829_1915}"
LOCAL_PROJECT="${LOCAL_PROJECT:-/mnt/c/users/hoyo/desktop/lock}"
SSH_KEY="${SSH_KEY:-/home/hoyo/.ssh/id_rsa}"

destination="$LOCAL_PROJECT/latest_event.json"
temporary="$destination.tmp"

if ! ssh -i "$SSH_KEY" -o BatchMode=yes -o ConnectTimeout=8 \
    "$JETSON_USER@$JETSON_HOST" "test -f '$JETSON_PROJECT/latest_event.json'"; then
  printf '%s\n' "No Jetson event file yet; local file was not changed."
  exit 0
fi

scp -q -i "$SSH_KEY" -o BatchMode=yes -o ConnectTimeout=8 \
  "$JETSON_USER@$JETSON_HOST:$JETSON_PROJECT/latest_event.json" "$temporary"
mv "$temporary" "$destination"
printf '%s\n' "$destination"
