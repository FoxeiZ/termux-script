#!/data/data/com.termux/files/usr/bin/bash

# check if root
if [ "$(id -u)" -eq 0 ]; then
    echo "This script should not be run as root. Please run it as a non-root user."
    exit 1
fi

DISTRO="{$1:-alpine}"
shift

proot-distro login "$DISTRO" --isolated -- "$@" &
PROOT_PID=$!

get_descendants() {
    local parent=$1
    local children
    if command -v pgrep >/dev/null 2>&1; then
        children=$(pgrep -P "$parent" 2>/dev/null)
    else
        children=$(ps -o pid= --ppid "$parent" 2>/dev/null | awk '{print $1}')
    fi

    for child in $children; do
        get_descendants "$child"
        echo "$child"
    done
}

wait_for_guest() {
    local limit=50
    local count=0
    while [ $count -lt $limit ]; do
        if ! kill -0 "$PROOT_PID" 2>/dev/null; then
            return 1
        fi
        if [ -n "$(get_descendants "$PROOT_PID")" ]; then
            return 0
        fi
        sleep 0.1
        count=$((count + 1))
    done
    return 1
}

# shellcheck disable=SC2329
forward_signal() {
    local sig=$1
    local descendants
    descendants=$(get_descendants "$PROOT_PID")
    for pid in $descendants; do
        if kill -0 "$pid" 2>/dev/null; then
            kill -s "$sig" "$pid"
        fi
    done
}

if ! wait_for_guest; then
    kill "$PROOT_PID" 2>/dev/null
    wait "$PROOT_PID" 2>/dev/null
    exit 1
fi

trap 'forward_signal INT' INT
trap 'forward_signal TERM' TERM
trap 'forward_signal HUP' HUP
trap 'forward_signal QUIT' QUIT

wait "$PROOT_PID"
exit $?