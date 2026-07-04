'use strict';

const mineflayer = require('mineflayer');
const { pathfinder, Movements, goals } = require('mineflayer-pathfinder');
const { requestCommentary } = require('./brain-client');
const { WorldEvents } = require('./world-state');
const state = require('./state');

const DISCORD_RELAY_URL = process.env.DISCORD_RELAY_URL || '';

// Hard floor between any two brain calls (90 seconds).
// She plays for real — speech is rare.
const COMMENTARY_FLOOR_MS = 90_000;
let _lastCommentaryAt = 0;

// ── Relay to Discord ────────────────────────────────────────────────────────

async function relayToDiscord(text) {
  if (!DISCORD_RELAY_URL || !text) return;
  try {
    const axios = require('axios');
    await axios.post(DISCORD_RELAY_URL, { content: text }, { timeout: 5000 });
  } catch (_) {}
}

// ── Speech gate ─────────────────────────────────────────────────────────────

async function saySomething(bot, gameEventContext) {
  const now = Date.now();
  if (now - _lastCommentaryAt < COMMENTARY_FLOOR_MS) return;
  _lastCommentaryAt = now;

  const text = await requestCommentary({ gameEventContext });
  if (!text) return; // null = [silent] or error

  const cleaned = text.slice(0, 256);
  bot.chat(cleaned);
  await relayToDiscord(`**[Minecraft]** ${cleaned}`);
}

// ── Goal system ─────────────────────────────────────────────────────────────
// Simple sequential goal loop: wander → gather wood → gather stone → repeat.
// Goals are delayed so spawn commentary fires first.

const GOAL_CYCLE = ['explore', 'mine_wood', 'mine_stone', 'mine_coal'];
let _goalIndex = 0;
let _goalTimer = null;

function stopGoalLoop() {
  if (_goalTimer) { clearTimeout(_goalTimer); _goalTimer = null; }
}

function scheduleNextGoal(bot, delayMs = 30_000) {
  stopGoalLoop();
  _goalTimer = setTimeout(() => runNextGoal(bot), delayMs);
}

async function runNextGoal(bot) {
  if (!bot.entity) return;
  const goal = GOAL_CYCLE[_goalIndex % GOAL_CYCLE.length];
  _goalIndex++;

  console.log(`[MC] Starting goal: ${goal}`);
  state.setGoal(goal);
  await saySomething(bot, WorldEvents.goalStart(bot, goal));

  let success = false;
  try {
    if (goal === 'explore') {
      await bot.wander(40);
    } else if (goal === 'mine_wood') {
      await mineNearby(bot, 'log', 3);
    } else if (goal === 'mine_stone') {
      await mineNearby(bot, 'stone', 5);
    } else if (goal === 'mine_coal') {
      await mineNearby(bot, 'coal_ore', 3);
    }
    success = true;
  } catch (err) {
    console.warn(`[MC] Goal ${goal} failed:`, err.message);
  }

  state.completeGoal(goal, success);
  await saySomething(bot, WorldEvents.goalDone(bot, goal));
  scheduleNextGoal(bot, 20_000);
}

async function mineNearby(bot, blockKeyword, count) {
  const mcData = require('minecraft-data')(bot.version);
  const movements = new Movements(bot, mcData);
  bot.pathfinder.setMovements(movements);

  let found = 0;
  for (let attempts = 0; attempts < 20 && found < count; attempts++) {
    // Find nearest block whose name contains the keyword
    const block = bot.findBlock({
      matching: (b) => b.name.includes(blockKeyword),
      maxDistance: 32,
    });
    if (!block) break;

    await bot.pathfinder.goto(new goals.GoalBlock(block.position.x, block.position.y, block.position.z));
    await bot.dig(block);
    found++;
  }
}

// ── Bot factory ─────────────────────────────────────────────────────────────

function createBot(config) {
  const bot = mineflayer.createBot({
    host: config.host,
    port: config.port || 25565,
    username: config.username || 'Koroki',
    version: config.version || false,
    auth: config.auth || 'offline',
  });

  bot.loadPlugin(pathfinder);

  bot.once('spawn', async () => {
    console.log(`[MC] Spawned (version: ${bot.version})`);
    state.onSpawn();
    await saySomething(bot, WorldEvents.spawn(bot));
    // Start goal loop after 45s — give her time to say something first.
    scheduleNextGoal(bot, 45_000);
  });

  bot.on('death', async () => {
    stopGoalLoop();
    await saySomething(bot, WorldEvents.death(bot, 'unknown'));
  });

  bot.on('respawn', () => {
    console.log('[MC] Respawned.');
    scheduleNextGoal(bot, 30_000);
  });

  bot.on('chat', async (username, message) => {
    if (username === bot.username) return;
    // Only react to chat — floor still applies so she won't spam back.
    await saySomething(bot, WorldEvents.playerChat(bot, username, message));
  });

  bot.on('entityHurt', async (entity) => {
    if (entity !== bot.entity) return;
    const attacker = bot.nearestEntity(e =>
      e.type === 'mob' && e.position?.distanceTo(bot.entity.position) < 8
    );
    await saySomething(bot, WorldEvents.mobAttack(bot, attacker?.name || 'something'));
  });

  bot.on('playerCollect', (collector, itemEntity) => {
    if (collector !== bot.entity) return;
    // itemEntity.name is the block/item name mineflayer gives us
    const name = itemEntity?.name || itemEntity?.displayName;
    if (name) state.addResources([{ name, count: 1 }]);
  });

  bot.on('kicked', (reason) => {
    let readable = reason;
    try {
      const parsed = JSON.parse(reason);
      readable = parsed.text || parsed.translate || reason;
      if (parsed.extra) readable += parsed.extra.map(e => e.text || '').join('');
    } catch (_) {}
    console.error('[MC] Kicked:', readable);
    stopGoalLoop();
  });

  bot.on('error', (err) => {
    console.error('[MC] Bot error:', err.message);
  });

  bot.on('end', (reason) => {
    if (reason) console.log('[MC] Disconnect reason:', reason);
    console.log('[MC] Disconnected.');
    stopGoalLoop();
  });

  // ── Navigation ────────────────────────────────────────────────────────────

  bot.wander = async function (radius = 20) {
    const mcData = require('minecraft-data')(bot.version);
    const movements = new Movements(bot, mcData);
    bot.pathfinder.setMovements(movements);
    const x = bot.entity.position.x + (Math.random() - 0.5) * radius * 2;
    const z = bot.entity.position.z + (Math.random() - 0.5) * radius * 2;
    await bot.pathfinder.goto(new goals.GoalXZ(Math.floor(x), Math.floor(z)));
  };

  return bot;
}

module.exports = { createBot };
