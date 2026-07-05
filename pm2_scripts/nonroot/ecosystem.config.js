const fs = require("node:fs");
const path = require("node:path");
const { parseEnv } = require("node:util");

/**
 * @typedef {{ [key: string]: string }} EnvObject
 *
 * @typedef {Object} AppConfig
 * @property {string} name
 * @property {string} script
 * @property {string | ((env: EnvObject) => string)} [args]
 * @property {string} [cwd]
 * @property {string} [exec_mode]
 * @property {boolean} [autorestart]
 * @property {EnvObject} [env]
 * @property {string} [env_file]
 * @property {string} [interpreter]
 */

const BASE_DIR = "/data/data/com.termux/files/home/scripts/pm2_scripts";
const SCRIPT_DIR = path.join(BASE_DIR, "nonroot");

/**
 * @param {string} filePath
 * @returns {EnvObject}
 */
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

/**
 * @param {Object} options
 * @param {AppConfig[]} options.appsConfig
 * @param {AppConfig} options.appConfig
 * @param {string} options.envFilePath
 * @param {boolean} [options.skipOnMissing=false]
 * @param {boolean} [options.warnOnMissing=true]
 * @param {function(EnvObject): boolean} [options.checkCallback]
 */
function addWithEnvFile({
  appsConfig,
  appConfig,
  envFilePath,
  skipOnMissing = false,
  warnOnMissing = true,
  checkCallback,
}) {
  const exists = fs.existsSync(envFilePath);
  if (!exists) {
    if (warnOnMissing) {
      console.warn(`Environment file ${envFilePath} not found.`);
    }
    if (skipOnMissing) return;
  }

  const env = exists ? readEnvFile(envFilePath) : {};
  if (checkCallback && !checkCallback(env)) {
    if (warnOnMissing) {
      console.warn(
        `Validation failed for ${appConfig.name || "unnamed app"}. Skipping.`,
      );
    }
    return;
  }

  const resolvedConfig = { ...appConfig };
  if (typeof resolvedConfig.args === "function") {
    resolvedConfig.args = resolvedConfig.args(env);
  }

  resolvedConfig.env = env;
  appsConfig.push(resolvedConfig);
}

/**
 * @type {AppConfig[]}
 */
const apps = [];

addWithEnvFile({
  appsConfig: apps,
  appConfig: {
    name: "nameless",
    script:
      "/data/data/com.termux/files/home/scripts/pm2_scripts/nonroot/proot-wrapper.sh",
    cwd: "/data/data/com.termux/files/home/",
    args: "alpine /root/.local/bin/uv --directory nameless-discord-bot/ run python bootstrapper.py",
    exec_mode: "fork",
    autorestart: true,
  },
  envFilePath: path.join(SCRIPT_DIR, ".env.nameless"),
  skipOnMissing: false,
  warnOnMissing: true,
});

addWithEnvFile({
  appsConfig: apps,
  appConfig: {
    name: "nhentai2komga",
    script:
      "/data/data/com.termux/files/home/scripts/pm2_scripts/nonroot/proot-wrapper.sh",
    cwd: "/data/data/com.termux/files/home/",
    args: "alpine /root/.local/bin/uv --directory nhentai2komga run --no-dev fastapi run --host 0.0.0.0 --port 25601",
    exec_mode: "fork",
    autorestart: true,
  },
  envFilePath: path.join(SCRIPT_DIR, ".env.nhentai2komga"),
  skipOnMissing: true,
  warnOnMissing: false,
});

addWithEnvFile({
  appsConfig: apps,
  appConfig: {
    name: "sslocal",
    script: "sslocal",
    args: (env) =>
      `-b 127.0.0.1:8071 -s \"${env.SS_HOST}\" -m aes-256-cfb -k \"${env.SS_PASS}\" -vvv`,
    cwd: "/data/data/com.termux/files/home/projects/shadowsocks/",
    exec_mode: "fork",
    autorestart: false,
  },
  envFilePath: path.join(SCRIPT_DIR, ".env.sslocal"),
  skipOnMissing: true,
  warnOnMissing: false,
  checkCallback: (env) => !!(env.SS_HOST && env.SS_PASS),
});

const KOMGA_DIR = "/data/data/com.termux/files/home/komga/";
try {
  if (fs.existsSync(KOMGA_DIR)) {
    const files = fs.readdirSync(KOMGA_DIR);
    const matchedFile = files
      .filter((file) => file.startsWith("komga") && file.endsWith(".jar"))
      .sort((a, b) =>
        b.localeCompare(a, undefined, { numeric: true, sensitivity: "base" }),
      )[0];

    if (matchedFile) {
      apps.push({
        name: "komga",
        script: "java",
        args: `-jar -Xmx2g ${path.join(KOMGA_DIR, matchedFile)}`,
        exec_mode: "fork",
        autorestart: true,
      });
    } else {
      console.warn(`No 'komga*.jar' file found in ${KOMGA_DIR}.`);
    }
  }
} catch (err) {
  console.error("Error occurred resolving Komga target:", err.message);
}

const NAVIDROME_CONFIG =
  "/data/data/com.termux/files/home/.config/navidrome/navidrome.toml";
try {
  if (fs.existsSync(NAVIDROME_CONFIG)) {
    apps.push({
      name: "navidrome",
      script: "navidrome",
      args: `--configfile ${NAVIDROME_CONFIG}`,
      exec_mode: "fork",
      autorestart: true,
    });
  }
} catch (err) {
  console.error("Error occurred resolving Navidrome config:", err.message);
}

module.exports = {
  apps: apps,
};
