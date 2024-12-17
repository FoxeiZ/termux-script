#!/data/data/com.termux/files/usr/bin/env #!/data/data/com.termux/files/usr/bin/python

import re
import subprocess
import time
import http.client
import json
from urllib.parse import urlparse

DISCORD_WEBHOOK_URL = ""
USERNAME = "RN10P"
AVATAR_URL = (
    "https://cdn.discordapp.com/app-assets/1049685078508314696/1249009769075703888.png"
)


def send_discord_message(embed: dict) -> None:
    try:
        url = urlparse(DISCORD_WEBHOOK_URL)

        conn = http.client.HTTPSConnection(url.netloc)
        headers = {
            "Content-Type": "application/json",
        }

        conn.request("POST", url.path + "?" + url.query, embed, headers)
        response = conn.getresponse()
        conn.close()

        if response.status not in (200, 201, 204):
            print(f"Failed to send Discord message. Status: {response.status}")

    except Exception as e:
        print(f"Failed to send Discord message: {e}")


def compare_states(old_state: dict, new_state: dict) -> bool:
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


def get_ifconfig_output():
    try:
        result = subprocess.run(["ifconfig"], capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout

        result = subprocess.run(["/sbin/ifconfig"], capture_output=True, text=True)
        return result.stdout

    except FileNotFoundError:
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


def parse_network_interfaces(ifconfig_output):
    interface_pattern = r"^(\w+[\w\d_]*): "

    inet_pattern = r"inet (\d+\.\d+\.\d+\.\d+)(?:\s+netmask (\d+\.\d+\.\d+\.\d+))?(?:\s+destination (\d+\.\d+\.\d+\.\d+))?"
    inet6_pattern = r"inet6 ([a-f0-9:]+)\s+prefixlen (\d+)"

    interfaces = {}
    current_interface = None

    for line in ifconfig_output.splitlines():
        interface_match = re.match(interface_pattern, line)
        if interface_match:
            current_interface = interface_match.group(1)
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


def format_interface_info(interface_name: str, data: dict) -> dict:
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
        ipv6_info.append(f"Address: {ip['address']}\nPrefix Length: {ip['prefixlen']}")

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


def build_embed(interfaces: dict) -> str:
    embeds_list = []
    for interface, data in interfaces.items():
        embed = {
            "title": f"Interface: {interface}",
            "color": 3447003 + hash(interface) % 5 * 1000000,
            "fields": [format_interface_info(interface, data)],
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        embeds_list.append(embed)

    payload = {
        "username": USERNAME,
        "avatar_url": AVATAR_URL,
        "content": "## Network interface information",
        "embeds": embeds_list,
    }
    return json.dumps(payload)


def monitor_network_changes():
    previous_state = {}

    while True:
        try:
            current_state = parse_network_interfaces(get_ifconfig_output())
            changes = compare_states(previous_state, current_state)
            if changes and "wlan0" in current_state:
                embed = build_embed(current_state)
                send_discord_message(embed)
                print("Network change detected, sent update to Discord")

            previous_state = current_state
            time.sleep(5)

        except KeyboardInterrupt:
            print("Monitoring stopped by user")
            break
        except Exception as e:
            print(f"Error during monitoring: {e}")
            time.sleep(5)


if __name__ == "__main__":
    print("Starting network interface monitoring...")
    monitor_network_changes()
