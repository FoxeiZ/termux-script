const fs = require("node:fs");
const path = require("node:path");
const { parseEnv } = require("node:util");

const BASE_DIR = "/data/data/com.termux/files/home/scripts/pm2_scripts";
const SCRIPT_DIR = path.join(BASE_DIR, "nonroot");

function readEnvFile(filePath) {
  if (fs.existsSync(filePath)) {
    try {
      const rawContent = fs.readFileSync(filePath, "utf8");
      return parseEnv(rawContent);
    } catch (err) {
      console.error(`Failed to parse keys from ${filePath}:`, err);
      return {};
    }
  } else {
    console.warn(
      `Environment file ${filePath} not found. Using empty environment.`,
    );
    return {};
  }
}

const sslocalEnv = readEnvFile(path.join(SCRIPT_DIR, ".env.sslocal"));

module.exports = {
  apps: [
    {
      name: "komga",
      script: "java",
      args: "-jar -Xmx2g /data/data/com.termux/files/home/komga/komga-1.20.0.jar",
      exec_mode: "fork",
      autorestart: true,
    },

    {
      name: "sslocal",
      script: "sslocal",
      args: `-b 127.0.0.1:8071 -s \"${sslocalEnv.SS_HOST || ""}\" -m aes-256-cfb -k \"${sslocalEnv.SS_PASS || ""}\" -vvv`,
      cwd: "/data/data/com.termux/files/home/projects/shadowsocks/",
      exec_mode: "fork",
      autorestart: false,
      env: sslocalEnv,
    },

    // {
    //   name: "nameless",
    //   script: "bootstrapper.py",
    //   interpreter: "python",
    //   cwd: "/data/data/com.termux/files/home/projects/nameless-discord-bot",
    //   exec_mode: "fork",
    //   autorestart: true,
    //   env_file: path.join(SCRIPT_DIR, ".env.nameless"),
    // },
    {
      name: "nameless",
      script:
        "/data/data/com.termux/files/home/scripts/pm2_scripts/nonroot/proot-wrapper.sh",
      // cwd: "/data/data/com.termux/files/home/projects/nameless-discord-bot",
      args: "uv --directory nameless-discord-bot/ run python bootstrapper.py",
      exec_mode: "fork",
      autorestart: true,
      env_file: path.join(SCRIPT_DIR, ".env.nameless"),
    },

    // {
    //   name: "lavalink",
    //   script: "java",
    //   args: "-Xmx800M -jar Lavalink.jar",
    //   cwd: "/data/data/com.termux/files/home/lavalink",
    //   exec_mode: "fork",
    //   autorestart: true
    // },
  ],
};
