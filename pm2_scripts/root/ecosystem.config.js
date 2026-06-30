const BASE_DIR = "/data/data/com.termux/files/home/scripts/pm2_scripts";
const SCRIPT_DIR = `${BASE_DIR}/root`;

module.exports = {
  apps: [
    {
      name: "root-system-monitor",
      script: "sudo",
      args: `-E python ${SCRIPT_DIR}/system_monitor.py`,
      cwd: SCRIPT_DIR,
      exec_mode: "fork",
      autorestart: true,
      interpreter: "none",
    },
    {
      name: "root-server-monitor",
      script: "sudo",
      args: `-E python ${SCRIPT_DIR}/system_server_monitor.py`,
      cwd: SCRIPT_DIR,
      exec_mode: "fork",
      autorestart: true,
      interpreter: "none",
    },
    {
      name: "root-process-monitor",
      script: "sudo",
      args: `-E python ${SCRIPT_DIR}/process_watch_monitor.py`,
      cwd: SCRIPT_DIR,
      exec_mode: "fork",
      autorestart: true,
      interpreter: "none",
    },
    {
      name: "root-interface-monitor",
      script: "sudo",
      args: `-E python ${SCRIPT_DIR}/interface_monitor.py`,
      cwd: SCRIPT_DIR,
      exec_mode: "fork",
      autorestart: true,
      interpreter: "none",
    },
    {
      name: "root-tailscale",
      script: "sudo",
      args: `-E python ${SCRIPT_DIR}/tailscale/tailscale.py`,
      cwd: `${SCRIPT_DIR}/tailscale`,
      exec_mode: "fork",
      autorestart: true,
      interpreter: "none",
    },
    {
      name: "mask-dnsserver",
      script: "sudo",
      args: `-E dnsmasq -p 53 --listen-address=::1 --no-resolv --server=1.1.1.1 --no-daemon`,
      exec_mode: "fork",
    },
  ],
};
