#!/data/data/com.termux/files/usr/bin/sh

PM2_HOME_ROOT="/data/data/com.termux/files/home/.suroot/.pm2"
PM2_HOME_NONROOT="/data/data/com.termux/files/home/.pm2"

get_file_hash() {
    _hashfile="$1"
    _hashcmd="sha256sum"
    if ! command -v sha256sum >/dev/null 2>&1; then
        _hashcmd="md5sum"
    fi
    $_hashcmd "$_hashfile" 2>/dev/null | cut -d' ' -f1
}

startup_pm2() {
    role="$1"
    pm2_home="$2"
    pm2_dump="$pm2_home/dump.pm2"
    hash_file="/data/data/com.termux/files/home/scripts/pm2_scripts/$role/ecosystem_hash"
    ecosystem_config="/data/data/com.termux/files/home/scripts/pm2_scripts/$role/ecosystem.config.js"

    if [ ! -f "$ecosystem_config" ]; then
        echo "Error: $role ecosystem.config.js not found at $ecosystem_config" >&2
        return 1
    fi

    case "$role" in
    root)
        label="Root"
        use_sudo=1
        ;;
    nonroot)
        label="Non-root"
        use_sudo=0
        ;;
    *)
        echo "Error: invalid role '$role'" >&2
        return 1
        ;;
    esac

    current_hash=$(get_file_hash "$ecosystem_config")
    saved_hash=$(cat "$hash_file" 2>/dev/null)
    config_changed=0

    if [ -n "$current_hash" ] && [ "$current_hash" != "$saved_hash" ]; then
        echo "$label ecosystem.config.js changed or hash file missing. Skipping resurrection..."
        config_changed=1
    fi

    if [ "$config_changed" -eq 0 ] && [ -f "$pm2_dump" ]; then
        echo "$label PM2 dump found. Attempting to restore $label PM2 session..."
        if [ "$use_sudo" -eq 1 ]; then
            PM2_HOME="$pm2_home" sudo pm2 resurrect
        else
            PM2_HOME="$pm2_home" pm2 resurrect
        fi

        # shellcheck disable=SC2181
        # since the pm2 startup command is conditional
        if [ $? -ne 0 ]; then
            echo "Warning: $label PM2 resurrection failed. Falling back to clean $label initialization..."
            if [ "$use_sudo" -eq 1 ]; then
                sudo pm2 start "$ecosystem_config" --update-env
            else
                pm2 start "$ecosystem_config" --update-env
            fi
            if [ -n "$current_hash" ]; then
                echo "$current_hash" > "$hash_file"
            fi
        fi
    else
        echo "Running fresh $label initialization..."
        if [ "$use_sudo" -eq 1 ]; then
            sudo pm2 start "$ecosystem_config" --update-env
        else
            pm2 start "$ecosystem_config" --update-env
        fi
        if [ -n "$current_hash" ]; then
            echo "$current_hash" > "$hash_file"
        fi
    fi
}

startup_pm2 root "$PM2_HOME_ROOT"
startup_pm2 nonroot "$PM2_HOME_NONROOT"
