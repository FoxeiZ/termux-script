#!/data/data/com.termux/files/usr/bin/bash

BASE_DIR="/data/data/com.termux/files/home/scripts/pm2_scripts"
SCRIPT_DIR="$BASE_DIR/root"

export PM2_HOME="/data/data/com.termux/files/home/.pm2"

pm2 kill
