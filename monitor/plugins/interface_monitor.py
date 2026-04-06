# ruff: noqa: E501

from __future__ import annotations

import asyncio
import contextlib
import datetime
import socket
import time
from typing import TYPE_CHECKING, Any, ClassVar, TypedDict

import psutil
from lib.ipc import send_json
from lib.manager import Manager
from lib.plugin import IntervalPlugin
from lib.types import IPCCommand, IPCCommandInternal, IPCRequestInternal

if TYPE_CHECKING:
    from logging import Logger

    from lib.types import Embed, EmbedField, PluginMetadata
    from lib.worker import PluginManager


class InterfaceInfo(TypedDict):
    ipv4: list[dict[str, Any]]
    ipv6: list[dict[str, Any]]
    mac: list[str]


type StateInfo = dict[str, InterfaceInfo]


class InterfaceMonitorPlugin(IntervalPlugin, requires_root=True):
    interval = 5
    if TYPE_CHECKING:
        username: str
        avatar_url: str
        _previous_state: StateInfo
        _last_reported_connectivity: bool | None

    exclude_interfaces: ClassVar[list[str]] = ["dummy0", "lo", "r_rmnet_data0", "rmnet_data0", "rmnet_ipa0"]

    def __init__(
        self,
        manager: PluginManager,
        metadata: PluginMetadata,
        logger: Logger,
    ) -> None:
        super().__init__(manager, metadata, logger)

        self.username = "RN10P"
        self.avatar_url = "https://cdn.discordapp.com/app-assets/1049685078508314696/1249009769075703888.png"

        self.reboot_enabled = bool(metadata.kwargs.get("reboot", False))
        self.reboot_threshold = int(metadata.kwargs.get("reboot_threshold", 1800))
        self.hotspot_enabled = bool(metadata.kwargs.get("hotspot", False))

        self._previous_state = {}
        self._lost_network_since: datetime.datetime | None = None
        self._hotspot_started: bool = False
        self._last_reported_connectivity: bool | None = None
        self._pending_interface_update: bool = True

    def compare_states(
        self,
        old_state: StateInfo,
        new_state: StateInfo,
    ) -> bool:
        if set(old_state.keys()) != set(new_state.keys()):
            return True

        for interface in set(old_state.keys()) & set(new_state.keys()):
            old_ipv4 = {tuple(sorted(d.items())) for d in old_state[interface]["ipv4"]}
            new_ipv4 = {tuple(sorted(d.items())) for d in new_state[interface]["ipv4"]}
            old_ipv6 = {tuple(sorted(d.items())) for d in old_state[interface]["ipv6"]}
            new_ipv6 = {tuple(sorted(d.items())) for d in new_state[interface]["ipv6"]}
            old_mac = set(old_state[interface]["mac"])
            new_mac = set(new_state[interface]["mac"])

            if old_ipv4 != new_ipv4 or old_ipv6 != new_ipv6 or old_mac != new_mac:
                return True

        return False

    def collect_network_interfaces(self) -> StateInfo:
        state: StateInfo = {}
        link_families: set[int] = set()
        with contextlib.suppress(TypeError, ValueError):
            link_families.add(int(psutil.AF_LINK))
        if hasattr(socket, "AF_PACKET"):
            with contextlib.suppress(TypeError, ValueError):
                link_families.add(int(socket.AF_PACKET))

        try:
            interface_map = psutil.net_if_addrs()
        except OSError as exc:
            self.logger.warning("failed to collect interfaces via psutil: %s", exc)
            return state

        for interface_name, addresses in interface_map.items():
            if interface_name in self.exclude_interfaces:
                continue

            interface_info: InterfaceInfo = {"ipv4": [], "ipv6": [], "mac": []}
            for address in addresses:
                if address.family == socket.AF_INET:
                    destination = address.ptp or address.broadcast
                    interface_info["ipv4"].append(
                        {
                            "address": address.address,
                            "netmask": address.netmask,
                            "destination": destination,
                        }
                    )
                elif address.family == socket.AF_INET6:
                    interface_info["ipv6"].append(
                        {
                            "address": address.address.split("%", 1)[0],
                        }
                    )
                elif int(address.family) in link_families and address.address:
                    if address.address not in interface_info["mac"]:
                        interface_info["mac"].append(address.address)

            if not interface_info["ipv4"] and not interface_info["ipv6"]:
                continue

            state[interface_name] = interface_info

        return state

    def format_interface_info(self, interface_name: str, data: dict[str, Any]) -> EmbedField:
        ipv4_info: list[str] = []
        for ip in data["ipv4"]:
            info = f"Address: {ip['address']}"
            if ip["netmask"]:
                info += f"\nNetmask: {ip['netmask']}"
            if ip["destination"]:
                info += f"\nDestination: {ip['destination']}"
            ipv4_info.append(info)

        field_value = ""
        ipv6_info = [f"Address: {ip['address']}" for ip in data["ipv6"]]
        mac_info = [f"Address: {mac}" for mac in data["mac"]]
        if ipv4_info:
            field_value += "**IPv4:**\n" + "\n\n".join(ipv4_info) + "\n\n"
        if ipv6_info:
            field_value += "**IPv6:**\n" + "\n\n".join(ipv6_info) + "\n\n"
        if mac_info:
            field_value += "**MAC:**\n" + "\n\n".join(mac_info)

        return {
            "name": interface_name,
            "value": field_value if field_value else "No IP addresses configured",
            "inline": False,
        }

    def build_embeds(self, interfaces: dict[str, Any]) -> list[Embed]:
        embeds_list: list[Embed] = []
        for interface, data in interfaces.items():
            embed: Embed = {
                "title": f"Interface: {interface}",
                "color": 3447003 + hash(interface) % 5 * 1000000,
                "fields": [self.format_interface_info(interface, data)],
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            embeds_list.append(embed)
        return embeds_list

    def has_connectivity_interface(self, interfaces: dict[str, Any]) -> bool:
        for data in interfaces.values():
            for ipv4 in data["ipv4"]:
                address = str(ipv4.get("address") or "")
                if address and not address.startswith("127."):
                    return True

            for ipv6 in data["ipv6"]:
                address = str(ipv6.get("address") or "").lower()
                if address and address != "::1" and not address.startswith("fe80:"):
                    return True

        return False

    async def has_internet_dns(
        self,
        host: str = "discord.com",
        timeout: float = 3.0,
    ) -> bool:
        loop = asyncio.get_running_loop()
        try:
            await asyncio.wait_for(
                loop.getaddrinfo(host, 443, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM),
                timeout=timeout,
            )
            return True
        except TimeoutError:
            self.logger.warning("dns reachability check timed out for host %s", host)
            return False
        except socket.gaierror as exc:
            self.logger.warning("dns reachability check failed for host %s: %s", host, exc)
            return False
        except OSError as exc:
            self.logger.warning("dns reachability check failed with OS error for host %s: %s", host, exc)
            return False

    async def perform_reboot(self) -> None:
        async with self.manager.internal_ipc() as (_, writer):
            if not writer:
                return

            request: IPCRequestInternal = {
                "cmd": IPCCommand.INTERNAL,
                "internal_cmd": IPCCommandInternal.REBOOT,
                "kwargs": {},
                "args": [],
                "password": self.manager.ipc_password,
            }
            await send_json(writer, request)

    async def update_connectivity_state(self, has_connectivity: bool) -> bool:
        async with self.manager.internal_ipc() as (_, writer):
            if not writer:
                self._last_reported_connectivity = None
                return False

            request: IPCRequestInternal = {
                "cmd": IPCCommand.INTERNAL,
                "internal_cmd": IPCCommandInternal.UPDATE_STATE,
                "kwargs": {},
                "args": [{"has_internet_access": has_connectivity}],
                "password": self.manager.ipc_password,
            }
            await send_json(writer, request)
            return True

    async def start_wifi_hotspot(self) -> None:
        """Start WiFi hotspot when network connection is lost."""
        if self._hotspot_started:
            self.logger.info("hotspot already started, skipping")
            return

        try:
            self.logger.info("starting WiFi hotspot due to network loss")

            with contextlib.suppress(Exception):
                proc_stop = await asyncio.create_subprocess_exec(
                    "cmd",
                    "wifi",
                    "stop-softap",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await proc_stop.communicate()

            proc_start = await asyncio.create_subprocess_exec(
                "cmd",
                "wifi",
                "start-softap",
                "qwerty123",
                "open",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc_start.communicate()

            if proc_start.returncode != 0:
                self.logger.error(
                    "failed to start WiFi hotspot: code %s, stderr: %s",
                    proc_start.returncode,
                    stderr.decode(errors="ignore"),
                )
            else:
                self._hotspot_started = True
                self.logger.info("wifi hotspot started successfully")

        except Exception as e:
            self.logger.error("unexpected error starting hotspot: %s", e)

    async def stop_wifi_hotspot(self) -> None:
        if not self._hotspot_started:
            return

        try:
            self.logger.info("stopping WiFi hotspot as network is restored")

            proc = await asyncio.create_subprocess_exec(
                "cmd",
                "wifi",
                "stop-softap",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()

            if proc.returncode != 0:
                self.logger.error(
                    "failed to stop WiFi hotspot: code %s, stderr: %s", proc.returncode, stderr.decode(errors="ignore")
                )
            else:
                self._hotspot_started = False
                self.logger.info("hotspot stopped successfully")

        except Exception as e:
            self.logger.error("unexpected error stopping hotspot: %s", e)

    async def start(self) -> None:
        current_state = self.collect_network_interfaces()
        changes = self.compare_states(self._previous_state, current_state)
        if changes:
            self._pending_interface_update = True
            self._previous_state = current_state

        has_connectivity = self.has_connectivity_interface(current_state)
        dns_reachable = await self.has_internet_dns()
        has_internet_access = has_connectivity and dns_reachable

        if (
            self._last_reported_connectivity is None or self._last_reported_connectivity != has_internet_access
        ) and await self.update_connectivity_state(has_internet_access):
            self._last_reported_connectivity = has_internet_access

        if not has_internet_access:
            if changes or self._lost_network_since is None:
                self.logger.warning(
                    "network unavailable (interface=%s, dns=%s), assuming no network",
                    has_connectivity,
                    dns_reachable,
                )

            if not self._lost_network_since:
                self._lost_network_since = datetime.datetime.now(datetime.UTC)
                if self.hotspot_enabled:
                    await self.start_wifi_hotspot()

            if self._lost_network_since and self.reboot_enabled:
                time_since_lost = datetime.datetime.now(datetime.UTC) - self._lost_network_since
                if time_since_lost.total_seconds() > self.reboot_threshold:
                    await self.perform_reboot()
            return

        if self._lost_network_since:
            if self.notifier is not None:
                await self.notifier.send_webhook(
                    {
                        "username": self.name,
                        "avatar_url": self.avatar_url,
                        "embeds": [
                            {
                                "title": "Network connection restored",
                                "color": 2302945,
                                "fields": [
                                    {
                                        "name": "Network connection restored",
                                        "value": f"Network connection restored after {datetime.datetime.now(datetime.UTC) - self._lost_network_since}.",
                                        "inline": False,
                                    }
                                ],
                                "footer": {
                                    "text": f"Lost network since {self._lost_network_since.strftime('%Y-%m-%d %H:%M:%S')}",
                                },
                            }
                        ],
                    }
                )
            self._lost_network_since = None
            if self.hotspot_enabled:
                await self.stop_wifi_hotspot()

            # force sending of the current state since internet is fully back
            self._pending_interface_update = True

        if self._pending_interface_update:
            embeds = self.build_embeds(current_state)
            if self.notifier is not None:
                await self.notifier.send_webhook(
                    {
                        "username": self.name,
                        "avatar_url": self.avatar_url,
                        "content": "## Network interface information" + (" (restored)" if not changes else ""),
                        "embeds": embeds,
                    }
                )
            self.logger.info("sent interface update to Discord")
            self._pending_interface_update = False


if __name__ == "__main__":
    manager = Manager()
    manager.register_plugin(InterfaceMonitorPlugin)
    asyncio.run(manager.run())
