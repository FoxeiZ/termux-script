#!/data/data/com.termux/files/usr/bin/sh

PM2_HOME_ROOT="/data/data/com.termux/files/home/.suroot/.pm2"
PM2_HOME_NONROOT="/data/data/com.termux/files/home/.pm2"
INIT_SCRIPT_NONROOT="/data/data/com.termux/files/home/scripts/pm2_scripts/nonroot/start.sh"
INIT_SCRIPT_ROOT="/data/data/com.termux/files/home/scripts/pm2_scripts/root/start.sh"

startup_pm2() {
    role="$1"
    pm2_home="$2"
    pm2_dump="$pm2_home/dump.pm2"

    case "$role" in
    root)
        label="Root"
        init_script="$INIT_SCRIPT_ROOT"
        use_sudo=1
        ;;
    nonroot)
        label="Non-root"
        init_script="$INIT_SCRIPT_NONROOT"
        use_sudo=0
        ;;
    *)
        echo "Error: invalid role '$role'" >&2
        return 1
        ;;
    esac

    if [ -f "$pm2_dump" ]; then
        echo "$label PM2 dump found. Attempting to restore $label PM2 session..."
        if [ "$use_sudo" -eq 1 ]; then
            PM2_HOME="$pm2_home" sudo -E pm2 resurrect
        else
            PM2_HOME="$pm2_home" pm2 resurrect
        fi

        if [ $? -ne 0 ]; then
            echo "Warning: $label PM2 resurrection failed. Falling back to clean $label initialization..."
            if [ "$use_sudo" -eq 1 ]; then
                sudo -E sh "$init_script"
            else
                sh "$init_script"
            fi
        fi
    else
        echo "No $label PM2 dump found. Running fresh $label initialization..."
        if [ "$use_sudo" -eq 1 ]; then
            sudo -E sh "$init_script"
        else
            sh "$init_script"
        fi
    fi
}

startup_pm2 root "$PM2_HOME_ROOT"
startup_pm2 nonroot "$PM2_HOME_NONROOT"
