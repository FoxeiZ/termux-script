#!/data/data/com.termux/files/usr/bin/bash

# check if root
if [ "$(id -u)" -eq 0 ]; then
    echo "This script should not be run as root. Please run it as a non-root user."
    exit 1
fi

DISTRO="$1"
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

# shellcheck disable=SC2329
forward_signal() {
    local sig=$1
    local descendants
    descendants=$(get_descendants "$PROOT_PID")
    for pid in $descendants; do
        if kill -0 "$pid" 2>/dev/null; then
            kill -s "$sig" "$pid" 2>/dev/null
        fi
    done
    if kill -0 "$PROOT_PID" 2>/dev/null; then
        kill -s "$sig" "$PROOT_PID" 2>/dev/null
    fi
}

trap 'forward_signal INT' INT
trap 'forward_signal TERM' TERM
trap 'forward_signal HUP' HUP
trap 'forward_signal QUIT' QUIT

wait "$PROOT_PID"
exit $?