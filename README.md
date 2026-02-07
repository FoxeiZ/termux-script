# Termux Script Collection

A comprehensive collection of utilities, scripts, and modules for Termux and system management.

---

## Table of Contents

- [Scripts and Utilities](#scripts-and-utilities)
- [Modules](#modules)

---

## Scripts and Utilities

### System & Server

- **adbd** - Start an ADBD server.
- **iface.py** - Monitor the network interface and post it to Discord webhook.
- **system_server-monitor.py** - Monitor system server status.
- **start-tailscaled.py** - Start Tailscale daemon.

### Development & Tools

- **dev.py** - Development utility script.
- **install.sh** - Package installer script.
- **mkvenv** - Make a Python virtual environment.
- **rcat** - Recursive cat utility.
- **stopwatch** - A simple stopwatch utility.

### Media & File Management

- **ampv** - Android-mpv wrapper.
- **litterbox** - Upload files to the litterbox service.
- **reduce** - FFMpeg script that reduces image or video files.
- **rclone-mount** - Mount remote storage using rclone.
- **tag-convert.py** - Parser for Tachiyomi -> Komga conversion.
- **torrent.py** - Torrent utility script.
- **ytmusic-downloader.py** - Download music from YouTube Music.
- **musixmatch_api.py** - Musixmatch API wrapper.

### Terminal & GUI

- **gltools** - Start GLTools with the inject license.
- **start** - Start the app from Termux.
- **start_old** - Old version of the previous start script.
- **termux-file-editor** - Text editor for Termux with configuration file (termux-file-editor.conf).
- **termux-url-opener** - Handle URL opening in Termux.
- **x11-start** - Start the X11 server with the input program (or the default XFCE desktop).

---

## Modules

### monitor

System monitoring framework with plugin architecture for tracking various system metrics and services.
