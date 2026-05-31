#!/data/data/com.termux/files/usr/bin/bash

BASE_DIR="/data/data/com.termux/files/home/scripts/pm2_scripts"
SCRIPT_DIR="$BASE_DIR/root"

export PM2_HOME="/data/data/com.termux/files/home/.pm2"

pm2 kill

pm2 start "java -jar -Xmx2g /data/data/com.termux/files/home/komga/komga-1.20.0.jar" --name "komga"
# pm2 start "java -Xmx800M -jar Lavalink.jar" --name "lavalink" --cwd "/data/data/com.termux/files/home/lavalink"
