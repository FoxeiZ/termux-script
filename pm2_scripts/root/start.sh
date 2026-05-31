#!/data/data/com.termux/files/usr/bin/bash

if [ "$EUID" -ne 0 ]; then
    exit 1
fi

BASE_DIR="/data/data/com.termux/files/home/scripts/pm2_scripts"
SCRIPT_DIR="$BASE_DIR/root"

export PM2_HOME="/data/data/com.termux/files/home/.pm2-root"

pm2 kill

# why `sudo -E`? and not using `pm2` with the `--interpreter` option?
# because `pm2` with `--interpreter` runs the script with and forks a child process, making us lose the root privileges
# executing the script with `sudo` directly allows us to run the script with root privileges even if pm2 forks it
# and `-E` is needed to preserve the environment variables, otherwise the script won't be able to find the modules
pm2 start "sudo -E python $SCRIPT_DIR/system_monitor.py" --name "root-system-monitor" --cwd "$SCRIPT_DIR"
pm2 start "sudo -E python $SCRIPT_DIR/system_server_monitor.py" --name "root-server-monitor" --cwd "$SCRIPT_DIR"
pm2 start "sudo -E python $SCRIPT_DIR/process_watch_monitor.py" --name "root-process-monitor" --cwd "$SCRIPT_DIR"
pm2 start "sudo -E python $SCRIPT_DIR/interface_monitor.py" --name "root-interface-monitor" --cwd "$SCRIPT_DIR"
pm2 start "sudo -E python $SCRIPT_DIR/tailscale/tailscale.py" --name "root-tailscale" --cwd "$SCRIPT_DIR/tailscale"

pm2 save
