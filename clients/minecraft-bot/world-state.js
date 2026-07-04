'use strict';

const state = require('./state');

const DIRECTION_NAMES = ['north', 'northeast', 'east', 'southeast', 'south', 'southwest', 'west', 'northwest'];

function getCardinalDirection(from, to) {
  const dx = to.x - from.x;
  const dz = to.z - from.z;
  const angle = (Math.atan2(dz, dx) * 180 / Math.PI + 360 + 90) % 360;
  return DIRECTION_NAMES[Math.round(angle / 45) % 8];
}

function dist3d(a, b) {
  const dx = a.x - b.x, dy = a.y - b.y, dz = a.z - b.z;
  return Math.sqrt(dx * dx + dy * dy + dz * dz);
}

const HOSTILE_MOBS = new Set([
  'creeper', 'zombie', 'skeleton', 'spider', 'witch', 'enderman',
  'blaze', 'ghast', 'wither_skeleton', 'pillager', 'ravager',
  'phantom', 'drowned', 'husk', 'stray', 'vindicator', 'evoker',
]);

// Instruction appended to every context block.
// Keeps her from flooding chat or acting like an assistant.
const PLAYER_INSTRUCTION = [
  'You are a Minecraft player, not a chatbot.',
  'Real players speak sparingly — only when something notable happens.',
  'Max 10 words. No questions. No offers to help. No filler.',
  'Do NOT start conversations. Do NOT comment on weather or silence.',
  'If nothing notable is happening: respond with exactly [silent].',
].join(' ');

function buildWorldState(bot, eventHint) {
  const pos = bot.entity?.position;
  if (!pos) return `minecraft_event=unknown | ${PLAYER_INSTRUCTION}`;

  const parts = [`minecraft_event=${eventHint || 'observation'}`];

  const biome = bot.entity?.biome?.name || 'unknown_biome';
  parts.push(`biome:${biome} pos:(${Math.floor(pos.x)},${Math.floor(pos.y)},${Math.floor(pos.z)})`);

  const ticks = bot.time?.timeOfDay ?? -1;
  const timeLabel = ticks < 0 ? 'unknown'
    : ticks < 1000 ? 'dawn' : ticks < 6000 ? 'morning'
    : ticks < 12000 ? 'afternoon' : ticks < 13000 ? 'dusk' : 'night';
  const weather = bot.isRaining ? 'rain' : 'clear';
  parts.push(`time:${timeLabel} weather:${weather}`);

  const hp = Math.floor(bot.health ?? 20);
  const food = Math.floor(bot.food ?? 20);
  const danger = hp < 6 ? 'CRITICAL_HEALTH' : hp < 12 ? 'low_health' : null;
  const hungry = food < 6 ? 'STARVING' : food < 12 ? 'hungry' : null;
  parts.push(`hp:${hp}/20${danger ? `(${danger})` : ''} food:${food}/20${hungry ? `(${hungry})` : ''}`);

  const entities = Object.values(bot.entities || {});
  const nearby = entities
    .filter(e => e !== bot.entity && e.position && e.isValid)
    .map(e => ({
      name: e.username || e.displayName || e.name || 'unknown',
      dist: Math.floor(dist3d(pos, e.position)),
      dir: getCardinalDirection(pos, e.position),
      type: e.type,
      hostile: HOSTILE_MOBS.has((e.name || '').toLowerCase()),
    }))
    .filter(e => e.dist < 30)
    .sort((a, b) => a.dist - b.dist);

  const players = nearby.filter(e => e.type === 'player');
  const hostiles = nearby.filter(e => e.hostile);

  if (players.length) parts.push(`players:${players.map(p => `${p.name}@${p.dist}m${p.dir}`).join(',')}`);
  else parts.push('players:none');
  if (hostiles.length) parts.push(`HOSTILES:${hostiles.map(m => `${m.name}@${m.dist}m${m.dir}`).join(',')}`);

  try {
    const block = bot.blockAtCursor(4);
    if (block && block.name !== 'air') parts.push(`looking_at:${block.name}`);
  } catch (_) {}

  const items = (bot.inventory?.items() || []).slice(0, 5)
    .map(i => `${i.name}x${i.count}`).join(',');
  if (items) parts.push(`inv:${items}`);

  const stateCtx = state.getContext();
  if (stateCtx) parts.push(stateCtx);

  parts.push(PLAYER_INSTRUCTION);
  return parts.join(' | ');
}

const WorldEvents = {
  spawn: (bot) => buildWorldState(bot, 'just_spawned'),

  death: (bot, cause) =>
    buildWorldState(bot, `died_from_${cause || 'unknown'}`) +
    ' | You just died. Say one short thing like a real player would — salty, dry, or nothing.',

  playerChat: (bot, username, message) =>
    buildWorldState(bot, 'player_spoke') +
    ` | ${username} said: "${message}" | React IF it feels natural — 1 sentence max. Otherwise [silent].`,

  mobAttack: (bot, mobName) =>
    buildWorldState(bot, `attacked_by_${mobName}`) +
    ' | You are being attacked right now.',

  goalStart: (bot, goal) =>
    buildWorldState(bot, `starting_goal_${goal}`) +
    ` | You decided to ${goal}. Say what you're doing in ≤8 words, or [silent].`,

  goalDone: (bot, goal) =>
    buildWorldState(bot, `finished_goal_${goal}`) +
    ` | You just finished: ${goal}. Brief reaction or [silent].`,
};

module.exports = { buildWorldState, WorldEvents };
