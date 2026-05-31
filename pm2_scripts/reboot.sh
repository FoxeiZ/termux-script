#!/data/data/com.termux/files/usr/bin/bash

if [ "$EUID" -ne 0 ]; then
    echo "Error: must be run as root." >&2
    exit 1
fi

USER_HOME="/data/data/com.termux/files/home"
SU_CMD="sudo -E"

echo "[1/4] Sending shutdown broadcast to User PM2 instances..."
PM2_HOME="$USER_HOME/.pm2" $SU_CMD "pm2 kill"

echo "[2/4] Sending shutdown broadcast to Root PM2 instances..."
PM2_HOME="$USER_HOME/.suroot/.pm2" $SU_CMD "pm2 kill"

echo "[3/4] Waiting for processes to terminate..."
MAX_WAIT_SECONDS=30
ELAPSED=0

while [ $ELAPSED -lt $MAX_WAIT_SECONDS ]; do
    ACTIVE_RUNNERS=$(pgrep -f "python.*(_monitor|tailscale|\.py)" | grep -v $$)

    if [ -z "$ACTIVE_RUNNERS" ]; then
        echo " -> Success: All PM2 python processes have exited cleanly."
        break
    fi

    echo " -> Waiting for active PIDs to drain: $(echo "$ACTIVE_RUNNERS" | tr '\n' ' ')"
    sleep 1
    ((ELAPSED++))
done

if [ $ELAPSED -eq $MAX_WAIT_SECONDS ]; then
    echo "[WARNING] Timeout reached! Forcing SIGKILL on remaining stale processes..."
    pgrep -f "python.*(_monitor|tailscale|\.py)" | grep -v $$ | xargs kill -9 2>/dev/null
fi

echo "[4/4] Starting kernel reboot pipeline now."
reboot
