#!/usr/bin/env python3
"""IPC client for PluginManager - sends JSON commands over TCP."""

from __future__ import annotations

import argparse
import contextlib
import json
import socket
import sys
from typing import Any


def parse_kwargs_string(kwargs_strings: list[str] | None) -> dict[str, Any]:
    """Parse kwargs from key=value strings, supporting semicolon-separated pairs.

    Args:
        kwargs_strings: List of strings containing key=value pairs, optionally separated by semicolons

    Returns:
        Dictionary of parsed kwargs

    Example:
        ['abc=1', 'bcd=2', 'def=3;fff=4']
        -> {'abc': '1', 'bcd': '2', 'def': '3', 'fff': '4'}
    """
    kwargs: dict[str, Any] = {}
    if not kwargs_strings:
        return kwargs

    for kwargs_str in kwargs_strings:
        pairs = kwargs_str.split(";")
        for kv_pair in pairs:
            stripped_pair = kv_pair.strip()
            if "=" in stripped_pair:
                key, value = stripped_pair.split("=", 1)
                kwargs[key.strip()] = value.strip()

    return kwargs


def send_tcp(port: int, request: dict[str, Any]) -> None:
    """Send JSON request to TCP server and print response.

    Args:
        port: TCP port number
        request: Request dictionary to send as JSON
    """
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
            json_data = json.dumps(request)
            s.sendall(json_data.encode("utf-8"))

            with contextlib.suppress(socket.timeout):
                data = s.recv(4096)
                if data:
                    response_str = data.decode("utf-8", errors="ignore").strip()
                    try:
                        response = json.loads(response_str)
                        status = response.get("status", "unknown")
                        message = response.get("message", "")
                        data_field = response.get("data")

                        if status == "ok":
                            print(f"[OK] {message}")
                            if data_field:
                                print(f"  Data: {data_field}")
                        else:
                            print(f"[FAILED] {message}", file=sys.stderr)
                            if data_field:
                                print(f"  Error details:\\n{data_field}", file=sys.stderr)
                            sys.exit(1)
                    except json.JSONDecodeError:
                        print(f"Response: {response_str}")
    except (OSError, ConnectionRefusedError) as e:
        print(f"Connection error: {e}", file=sys.stderr)
        sys.exit(2)


def main() -> None:
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s start MyPlugin
  %(prog)s start MyPlugin --args arg1 --args arg2
  %(prog)s start MyPlugin --args arg1 --kwargs home_dir=/path/to/dir --kwargs auth_key=mykey
  %(prog)s start MyPlugin --kwargs "key1=val1;key2=val2"
  %(prog)s stop MyPlugin
  %(prog)s restart MyPlugin
  %(prog)s list
        """,
    )
    parser.add_argument("cmd", help="Command: start, stop, restart, list")
    parser.add_argument(
        "plugin_name",
        nargs="?",
        default="",
        help="Plugin name",
    )
    parser.add_argument(
        "--args",
        action="append",
        help="Positional arguments for plugin (can be used multiple times)",
    )
    parser.add_argument(
        "--kwargs",
        action="append",
        help="Keyword arguments as key=value pairs (can be used multiple times, supports semicolon-separated pairs)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="TCP port to use on localhost (default: 8765)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass root privilege checks when starting plugins",
    )
    args = parser.parse_args()

    cmd = args.cmd.lower()
    plugin_name = args.plugin_name

    parsed_args: list[str] = args.args or []
    parsed_kwargs: dict[str, Any] = parse_kwargs_string(args.kwargs)

    request: dict[str, Any] = {
        "cmd": cmd,
        "plugin_name": plugin_name,
        "args": parsed_args,
        "kwargs": parsed_kwargs,
        "force": args.force,
    }

    send_tcp(args.port, request)


if __name__ == "__main__":
    main()
