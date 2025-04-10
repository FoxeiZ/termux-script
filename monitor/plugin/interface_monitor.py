import datetime
import re
import subprocess
import time
from typing import TYPE_CHECKING, Any

from lib._types import Embed, EmbedField
from lib.manager import get_logger
from lib.plugin import IntervalPlugin

logger = get_logger("IfacePlugin")


def default_ifconfig_output():
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


class InterfaceMonitorPlugin(IntervalPlugin):
    if TYPE_CHECKING:
        username: str
        avatar_url: str
        exclude_interfaces: list[str]
        _previous_state: dict[str, Any]

    exclude_interfaces = ["dummy0", "lo", "r_rmnet_data0", "rmnet_data0", "rmnet_ipa0"]

    def __init__(self, manager, interval: int = 5, webhook_url: str = "") -> None:
        super().__init__(
            manager,
            interval=interval,
            webhook_url=webhook_url,
        )

        self.username = "RN10P"
        self.avatar_url = "https://cdn.discordapp.com/app-assets/1049685078508314696/1249009769075703888.png"

        self._previous_state = {}
        self._lost_network_since: datetime.datetime | None = None

    def compare_states(self, old_state: dict, new_state: dict) -> bool:
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

    def get_ifconfig_output(self):
        try:
            result = subprocess.run(["ifconfig"], capture_output=True, text=True)
            if result.returncode == 0:
                return result.stdout

            result = subprocess.run(["/sbin/ifconfig"], capture_output=True, text=True)
            return result.stdout

        except FileNotFoundError:
            return default_ifconfig_output()

    def parse_network_interfaces(self, ifconfig_output):
        interface_pattern = r"^(\w+[\w\d_]*): "

        inet_pattern = r"inet (\d+\.\d+\.\d+\.\d+)(?:\s+netmask (\d+\.\d+\.\d+\.\d+))?(?:\s+destination (\d+\.\d+\.\d+\.\d+))?"
        inet6_pattern = r"inet6 ([a-f0-9:]+)\s+prefixlen (\d+)"

        interfaces = {}
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
                        "destination": inet_match.group(3)
                        if inet_match.group(3)
                        else None,
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

    def format_interface_info(self, interface_name: str, data: dict) -> EmbedField:
        ipv4_info = []
        for ip in data["ipv4"]:
            info = f"Address: {ip['address']}"
            if ip["netmask"]:
                info += f"\nNetmask: {ip['netmask']}"
            if ip["destination"]:
                info += f"\nDestination: {ip['destination']}"
            ipv4_info.append(info)

        ipv6_info = []
        for ip in data["ipv6"]:
            ipv6_info.append(
                f"Address: {ip['address']}\nPrefix Length: {ip['prefixlen']}"
            )

        field_value = ""
        if ipv4_info:
            field_value += "**IPv4:**\n" + "\n\n".join(ipv4_info) + "\n\n"
        if ipv6_info:
            field_value += "**IPv6:**\n" + "\n\n".join(ipv6_info)

        return {
            "name": interface_name,
            "value": field_value if field_value else "No IP addresses configured",
            "inline": False,
        }

    def build_embeds(self, interfaces: dict) -> list[Embed]:
        embeds_list = []
        for interface, data in interfaces.items():
            embed = {
                "title": f"Interface: {interface}",
                "color": 3447003 + hash(interface) % 5 * 1000000,
                "fields": [self.format_interface_info(interface, data)],
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            embeds_list.append(embed)
        return embeds_list

    def run(self):
        current_state = self.parse_network_interfaces(self.get_ifconfig_output())
        changes = self.compare_states(self._previous_state, current_state)
        if changes:
            if "wlan0" not in current_state:
                logger.warning("Network interface wlan0 not found, assuming no network")

                self._lost_network_since = datetime.datetime.now(datetime.UTC)
                self._previous_state = current_state
                return

            if self._lost_network_since:
                self.send_webhook(
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
                                        "value": f"Network connection restored after {datetime.datetime.now(datetime.UTC) - self._lost_network_since}",
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

            embeds = self.build_embeds(current_state)
            self.send_webhook(
                {
                    "username": self.name,
                    "avatar_url": self.avatar_url,
                    "content": "## Network interface information",
                    "embeds": embeds,
                }
            )
            logger.info("Network change detected, sent update to Discord")
            self._previous_state = current_state


if __name__ == "__main__":
    from lib.manager import PluginManager

    manager = PluginManager()
    manager.register_plugin(InterfaceMonitorPlugin)
    manager.run()
