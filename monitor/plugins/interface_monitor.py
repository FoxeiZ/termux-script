# ruff: noqa: E501

from __future__ import annotations

import asyncio
import contextlib
import datetime
import re
import socket
import time
from typing import TYPE_CHECKING, Any, ClassVar

from lib.ipc import send_json
from lib.manager import Manager
from lib.plugin import IntervalPlugin
from lib.types import IPCCommand, IPCCommandInternal, IPCRequestInternal

if TYPE_CHECKING:
    from logging import Logger

    from lib.types import Embed, EmbedField, PluginMetadata
    from lib.worker import PluginManager


def default_ifconfig_output() -> str:
    return """
dummy0: flags=195<UP,BROADCAST,RUNNING,NOARP>  mtu 1500
        inet6 fe80::3c17:e9ff:fe59:5f1  prefixlen 64  scopeid 0x20<link>
        ether 3e:17:e9:59:05:f1  txqueuelen 1000  (Ethernet)
        RX packets 0  bytes 0 (0.0 B)
        RX errors 0  dropped 0  overruns 0  frame 0
        TX packets 528  bytes 107978 (105.4 KiB)
        TX errors 0  dropped 0 overruns 0  carrier 0  collisions 0

lo: flags=73<UP,LOOPBACK,RUNNING>  mtu 65536
        inet 127.0.0.1  netmask 255.0.0.0
        inet6 ::1  prefixlen 128  scopeid 0x10<host>
        loop  txqueuelen 1000  (Local Loopback)
        RX packets 1859  bytes 105572 (103.0 KiB)
        RX errors 0  dropped 0  overruns 0  frame 0
        TX packets 1859  bytes 105572 (103.0 KiB)
        TX errors 0  dropped 0 overruns 0  carrier 0  collisions 0

r_rmnet_data0: flags=65<UP,RUNNING>  mtu 1500
        inet6 fe80::62e0:cf72:8c10:8f9f  prefixlen 64  scopeid 0x20<link>
        unspec 00-00-00-00-00-00-00-00-00-00-00-00-00-00-00-00  txqueuelen 1000  (UNSPEC)
        RX packets 0  bytes 0 (0.0 B)
        RX errors 0  dropped 0  overruns 0  frame 0
        TX packets 128  bytes 7124 (6.9 KiB)
        TX errors 0  dropped 0 overruns 0  carrier 0  collisions 0

rmnet_data0: flags=65<UP,RUNNING>  mtu 1500
        inet6 fe80::16d3:a95c:452a:3d76  prefixlen 64  scopeid 0x20<link>
        unspec 00-00-00-00-00-00-00-00-00-00-00-00-00-00-00-00  txqueuelen 1000  (UNSPEC)
        RX packets 24  bytes 3351 (3.2 KiB)
        RX errors 0  dropped 0  overruns 0  frame 0
        TX packets 33  bytes 2436 (2.3 KiB)
        TX errors 0  dropped 0 overruns 0  carrier 0  collisions 0

rmnet_ipa0: flags=65<UP,RUNNING>  mtu 9216
        unspec 00-00-00-00-00-00-00-00-00-00-00-00-00-00-00-00  txqueuelen 1000  (UNSPEC)
        RX packets 10  bytes 3209 (3.1 KiB)
        RX errors 0  dropped 0  overruns 0  frame 0
        TX packets 161  bytes 10848 (10.5 KiB)
        TX errors 0  dropped 0 overruns 0  carrier 0  collisions 0

tun0: flags=81<UP,POINTOPOINT,RUNNING>  mtu 1280
        inet 100.96.0.4  netmask 255.255.255.255  destination 100.96.0.4
        inet6 fe80::2c2a:3f37:3e90:94d0  prefixlen 64  scopeid 0x20<link>
        inet6 2606:4700:110:8747:69ce:4da:d448:ea2c  prefixlen 128  scopeid 0x0<global>
        unspec 00-00-00-00-00-00-00-00-00-00-00-00-00-00-00-00  txqueuelen 500  (UNSPEC)
        RX packets 258881  bytes 297583689 (283.7 MiB)
        RX errors 0  dropped 0  overruns 0  frame 0
        TX packets 152248  bytes 12026144 (11.4 MiB)
        TX errors 0  dropped 390 overruns 0  carrier 0  collisions 0

wlan0: flags=4163<UP,BROADCAST,RUNNING,MULTICAST>  mtu 1500
        inet 192.168.1.100  netmask 255.255.255.0  broadcast 192.168.1.255
        inet6 fe80::44eb:35ff:fe65:ce21  prefixlen 64  scopeid 0x20<link>
        inet6 2001:ee0:e9fa:2040:44eb:35ff:fe65:ce21  prefixlen 64  scopeid 0x0<global>
        inet6 2001:ee0:e9fa:2040:7bd4:7968:cce2:cb83  prefixlen 64  scopeid 0x0<global>
        ether 46:eb:35:65:ce:21  txqueuelen 3000  (Ethernet)
        RX packets 2525062  bytes 1677342759 (1.5 GiB)
        RX errors 0  dropped 0  overruns 0  frame 0
        TX packets 2965233  bytes 2395305804 (2.2 GiB)
        TX errors 0  dropped 0 overruns 0  carrier 0  collisions 0
    """


class InterfaceMonitorPlugin(IntervalPlugin, requires_root=True):
    interval = 5
    if TYPE_CHECKING:
        username: str
        avatar_url: str
        _previous_state: dict[str, Any]
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
        old_state: dict[str, Any],
        new_state: dict[str, Any],
    ) -> bool:
        if set(old_state.keys()) != set(new_state.keys()):
            return True

        for interface in set(old_state.keys()) & set(new_state.keys()):
            old_ipv4 = {tuple(sorted(d.items())) for d in old_state[interface]["ipv4"]}
            new_ipv4 = {tuple(sorted(d.items())) for d in new_state[interface]["ipv4"]}
            old_ipv6 = {tuple(sorted(d.items())) for d in old_state[interface]["ipv6"]}
            new_ipv6 = {tuple(sorted(d.items())) for d in new_state[interface]["ipv6"]}

            if old_ipv4 != new_ipv4 or old_ipv6 != new_ipv6:
                return True

        return False

    async def get_ifconfig_output(self) -> str:
        try:
            proc = await asyncio.create_subprocess_exec(
                "ifconfig",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode == 0:
                return stdout.decode(errors="ignore")

            proc = await asyncio.create_subprocess_exec(
                "/sbin/ifconfig",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            return stdout.decode(errors="ignore")

        except FileNotFoundError:
            return default_ifconfig_output()

    def parse_network_interfaces(self, ifconfig_output: str) -> dict[str, Any]:
        interface_pattern = r"^(\w+[\w\d_]*): "

        inet_pattern = (
            r"inet (\d+\.\d+\.\d+\.\d+)(?:\s+netmask (\d+\.\d+\.\d+\.\d+))?(?:\s+destination (\d+\.\d+\.\d+\.\d+))?"
        )
        inet6_pattern = r"inet6 ([a-f0-9:]+)\s+prefixlen (\d+)"

        interfaces: dict[str, Any] = {}
        current_interface = None

        for line in ifconfig_output.splitlines():
            interface_match = re.match(interface_pattern, line)
            if interface_match:
                current_interface = interface_match.group(1)
                if current_interface in self.exclude_interfaces:
                    current_interface = None
                    continue

                interfaces[current_interface] = {"ipv4": [], "ipv6": []}
                continue

            if current_interface:
                inet_match = re.search(inet_pattern, line)
                if inet_match:
                    ipv4_info = {
                        "address": inet_match.group(1),
                        "netmask": inet_match.group(2) if inet_match.group(2) else None,
                        "destination": inet_match.group(3) if inet_match.group(3) else None,
                    }
                    interfaces[current_interface]["ipv4"].append(ipv4_info)

                inet6_match = re.search(inet6_pattern, line)
                if inet6_match:
                    ipv6_info = {
                        "address": inet6_match.group(1),
                        "prefixlen": inet6_match.group(2),
                    }
                    interfaces[current_interface]["ipv6"].append(ipv6_info)

        return interfaces

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
        ipv6_info = [f"Address: {ip['address']}\nPrefix Length: {ip['prefixlen']}" for ip in data["ipv6"]]
        if ipv4_info:
            field_value += "**IPv4:**\n" + "\n\n".join(ipv4_info) + "\n\n"
        if ipv6_info:
            field_value += "**IPv6:**\n" + "\n\n".join(ipv6_info)

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
        raw_output = await self.get_ifconfig_output()
        current_state = self.parse_network_interfaces(raw_output)
        changes = self.compare_states(self._previous_state, current_state)
        self.logger.debug(f"======\n{raw_output=}\n{current_state=}\n{self._previous_state=}\n{changes=}\n======")

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
