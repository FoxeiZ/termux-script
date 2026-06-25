#!/data/data/com.termux/files/usr/bin/bash

# unused, kept for reference
BASE_DIR="/data/data/com.termux/files/home/scripts/pm2_scripts"
SCRIPT_DIR="$BASE_DIR/nonroot"

export PM2_HOME="/data/data/com.termux/files/home/.pm2"

pm2 kill

pm2 start "java -jar -Xmx2g /data/data/com.termux/files/home/komga/komga-1.20.0.jar" --name "komga"
if [ -f "$SCRIPT_DIR/.env.sslocal" ]; then
    source "$SCRIPT_DIR/.env.sslocal"
    pm2 start "sslocal -b 127.0.0.1:8071 -s \"$SS_HOST\" -m aes-256-cfb -k \"$SS_PASS\" -vvv" \
        --name "sslocal" \
        --no-autorestart \
        --interpreter bash \
        --interpreter-args "-c" \
        --cwd "/data/data/com.termux/files/home/projects/shadowsocks/"
fi
if [ -f "$SCRIPT_DIR/.env.nameless" ]; then
    source "$SCRIPT_DIR/.env.nameless"
    pm2 start "bootstrapper.py" --cwd "/data/data/com.termux/files/home/projects/nameless-discord-bot" \
        --name "nameless" \
        --interpreter python
fi
# pm2 start "java -Xmx800M -jar Lavalink.jar" --name "lavalink" --cwd "/data/data/com.termux/files/home/lavalink"

pm2 save
