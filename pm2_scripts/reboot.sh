#!/data/data/com.termux/files/usr/bin/bash

if [ "$EUID" -ne 0 ]; then
    echo "Error: must be run as root." >&2
    exit 1
fi

USER_HOME="/data/data/com.termux/files/home"
SU_CMD="sudo -E"

if [ "$1" != "--detached" ]; then
    if command -v setsid >/dev/null 2>&1; then
        setsid "$0" --detached "$@" >/dev/null 2>&1 &
    else
        nohup "$0" --detached "$@" >/dev/null 2>&1 &
    fi
    exit 0
fi

shift

LOG_FILE="$USER_HOME/reboot.log"
exec > "$LOG_FILE" 2>&1

sleep 1

PM2_HOME="$USER_HOME/.pm2" $SU_CMD pm2 kill
PM2_HOME="$USER_HOME/.suroot/.pm2" $SU_CMD pm2 kill

MAX_WAIT_SECONDS=30
ELAPSED=0

while [ $ELAPSED -lt $MAX_WAIT_SECONDS ]; do
    ACTIVE_RUNNERS=$(pgrep -f "python.*(_monitor|tailscale|\.py)" | grep -v $$)

    if [ -z "$ACTIVE_RUNNERS" ]; then
        break
    fi

    sleep 1
    ((ELAPSED++))
done

if [ $ELAPSED -eq $MAX_WAIT_SECONDS ]; then
    pgrep -f "python.*(_monitor|tailscale|\.py)" | grep -v $$ | xargs kill -9 2>/dev/null
fi

reboot || /system/bin/reboot || svc power reboot
