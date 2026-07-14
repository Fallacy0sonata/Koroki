'use strict';

require('dotenv').config({ path: '../../.env' });

// The "random freezes" (round 17) were SILENT PROCESS CRASHES: pathfinder/protocol
// internals throw on this server's NaN desyncs, and the stack used to disappear on
// stderr. Fatal errors are now logged to relayed stdout, then the process exits so
// discord_bot.py's external supervisor can rebuild a clean Mineflayer state. Keeping
// a process alive after an uncaught error lets its heartbeat hide corrupted state.
let fatalExitStarted = false;
function exitAfterFatal(kind, err) {
  if (fatalExitStarted) return;
  fatalExitStarted = true;
  const detail = (err && (err.stack || err.message)) || err;
  const line = `[MC] ${kind} (exiting for supervisor restart): ${detail}\n`;
  const fallback = setTimeout(() => process.exit(1), 1000);
  fallback.unref();
  process.stdout.write(line, () => process.exit(1));
}
process.on('uncaughtException', (err) => exitAfterFatal('UNCAUGHT EXCEPTION', err));
process.on('unhandledRejection', (err) => exitAfterFatal('UNHANDLED REJECTION', err));

const { createBot } = require('./bot');

// Liveness heartbeat for the EXTERNAL supervisor (discord_bot.py). A wedged event
// loop can't print this — 120s of silence tells the supervisor to kill+restart us.
// The relay filters 'hb' lines out of the Discord channel.
setInterval(() => console.log('[MC] hb'), 45_000);

// When the server runs a version minecraft-data doesn't have full data for,
// mineflayer fires an error event with "No data available for version X".
// We retry with this list in order until one works.
// NOTE: 26.x (Minecraft's new year-based versioning) is NOT yet in this list
// because minecraft-data doesn't have game data files for it yet. Only 1.21.x
// and below are fully supported by mineflayer. Downgrade the server to 1.21.11
// or wait for mineflayer/minecraft-data to add 26.x support.
const FALLBACK_VERSIONS = [
  '1.21.11',
  '1.21.4',
  '1.21.1',
  '1.20.4',
];

// MC_SERVER_HOST may arrive as "host:port" (Aternos copies paste that way) —
// split it, or DNS resolves the literal "host:port" string and ENOTFOUNDs.
const rawHost = process.env.MC_SERVER_HOST || 'localhost';
const hostParts = rawHost.split(':');
const hostOnly = hostParts[0];
const portFromHost = hostParts.length > 1 ? parseInt(hostParts[1], 10) : null;

const baseConfig = {
  host: hostOnly,
  port: portFromHost || parseInt(process.env.MC_SERVER_PORT || '25565', 10),
  username: process.env.MC_USERNAME || 'Koroki',
  auth: process.env.MC_AUTH || 'offline',
};

// Explicit version from env (set by /minecraft join version:x.y.z), or false for auto-detect.
const envVersion = process.env.MC_VERSION || false;

console.log(`[MC] Connecting to ${baseConfig.host}:${baseConfig.port} as ${baseConfig.username}`);
if (envVersion) {
  console.log(`[MC] Version pinned to: ${envVersion}`);
} else {
  console.log(`[MC] Version: auto-detect (fallback chain: ${FALLBACK_VERSIONS.join(' → ')})`);
}

let bot = null;
let reconnectDelay = 5000;

function connect(versionOverride) {
  const version = versionOverride || envVersion || false;
  const config = { ...baseConfig, version };

  let currentBot;
  try {
    currentBot = createBot(config);
    bot = currentBot;
  } catch (err) {
    // Synchronous failure (e.g. explicit unknown version).
    if (err.message && err.message.includes('No data available for version')) {
      return tryFallback(null, err.message);
    }
    console.error('[MC] Fatal: could not create bot:', err.message);
    return;
  }

  let versionErrorFired = false;

  currentBot.on('error', (err) => {
    if (err.message && err.message.includes('No data available for version')) {
      if (!versionErrorFired) {
        versionErrorFired = true;
        tryFallback(version, err.message);
      }
      return;
    }
    console.error('[MC] Bot error:', err.message);
  });

  currentBot.once('spawn', () => {
    reconnectDelay = 5000;
    console.log(`[MC] Spawned successfully (version: ${currentBot.version})`);
  });

  currentBot.on('end', () => {
    if (versionErrorFired) return; // tryFallback handles retry
    console.log(`[MC] Disconnected. Reconnecting in ${reconnectDelay / 1000}s...`);
    setTimeout(() => connect(versionOverride), reconnectDelay);
    reconnectDelay = Math.min(reconnectDelay * 2, 60000);
  });
}

function tryFallback(failedVersion, errorMessage) {
  console.error(`[MC] ${errorMessage}`);

  const remaining = FALLBACK_VERSIONS.filter(v => v !== failedVersion);
  if (remaining.length === 0) {
    console.error('[MC] All fallback versions exhausted. The server is likely running Minecraft 26.x.');
    console.error('[MC] mineflayer does not yet have game data for 26.x versions.');
    console.error('[MC] Fix: downgrade the Aternos server to version 1.21.11 (the newest mineflayer supports).');
    return;
  }

  const next = remaining[0];
  console.log(`[MC] Retrying with version ${next}...`);
  setTimeout(() => connect(next), 2000);
}

connect();
