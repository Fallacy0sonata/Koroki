'use strict';

const state = require('./state');
const { MOB } = require('./combat');
const { finite3 } = require('./perceive');

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

// Derived from the combat table so detection can NEVER drift out of sync with the
// mobs we have tactics for (the guardian death was a mob missing from this set).
const HOSTILE_MOBS = new Set(Object.keys(MOB));

// Instruction appended to every context block.
// Keeps her from flooding chat or acting like an assistant.
// Kept short so the whole context stays under the orchestrator's 512-char cap.
const PLAYER_INSTRUCTION =
  "You're a Minecraft player, not a chatbot. React to what just happened in "
  + '<=8 words. Never narrate your own actions/movement, never invent things not '
  + 'in the event. Not worth saying? reply exactly [silent].';

function buildWorldState(bot, eventHint) {
  const pos = bot.entity?.position;
  if (!finite3(pos)) return `minecraft_event=unknown | ${PLAYER_INSTRUCTION}`; // NaN-safe

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

// Structured snapshot for the decision policy (decide.chooseGoal). Mirrors the
// data in buildWorldState but as a plain object, not a prompt string.
function snapshotWorld(bot) {
  const pos = bot.entity?.position;
  if (!finite3(pos)) return null; // NaN/desync — caller (perceive gate) should have caught it
  const inv = bot.inventory?.items() || [];
  const has = (re) => inv.some((i) => re.test(i.name));
  const countMatch = (re) => inv.filter((i) => re.test(i.name)).reduce((n, i) => n + i.count, 0);
  // foodItems counts what she would ACTUALLY eat (foods.js table minus avoid-list).
  // The old loose regex counted poisonous_potato as food → permanent eat-livelock.
  const { isSafeFood } = require('./foods');
  const safeFoodCount = inv.filter((i) => isSafeFood(i.name)).reduce((n, i) => n + i.count, 0);

  // a real usable bed within reach (block, not inventory) — sleep/shelter decisions
  let bedNearby = false;
  try { bedNearby = !!bot.findBlock({ matching: (b) => b && /_bed$/.test(b.name || ''), maxDistance: 16 }); } catch (_) {}
  // ENCLOSED = solid on all 4 sides at feet AND head, solid overhead — i.e. the
  // panic wall worked. Without this the night reflex re-fired build_shelter every
  // 1.5s all night INSIDE her own shelter (live round 16 log flood).
  let enclosed = false;
  try {
    const feet = pos.floored();
    const solid = (dx, dy, dz) => {
      const b = bot.blockAt(feet.offset(dx, dy, dz));
      return !!b && b.boundingBox === 'block';
    };
    enclosed = solid(1, 0, 0) && solid(-1, 0, 0) && solid(0, 0, 1) && solid(0, 0, -1)
      && solid(1, 1, 0) && solid(-1, 1, 0) && solid(0, 1, 1) && solid(0, 1, -1)
      && solid(0, 2, 0);
  } catch (_) {}
  // a bed placed SOMEWHERE she remembers (homestead done-predicate — a slept-in bed
  // at base must not read as "no bed, grind wool again")
  let bedPlaced = bedNearby;
  let nearHome = false;
  try {
    if (bot._wm) {
      if (!bedPlaced) bedPlaced = !!bot._wm.nearest('landmarks', pos, (e) => e.tag === 'bed');
      const base = bot._wm.getBase && bot._wm.getBase();
      if (base && base.committed) nearHome = Math.hypot(base.x - pos.x, base.z - pos.z) <= 12;
    }
  } catch (_) {}

  const ticks = bot.time?.timeOfDay ?? -1;
  const timeLabel = ticks < 0 ? 'unknown'
    : ticks < 1000 ? 'dawn' : ticks < 6000 ? 'morning'
    : ticks < 12000 ? 'afternoon' : ticks < 13000 ? 'dusk' : 'night';

  const nearby = Object.values(bot.entities || {})
    .filter((e) => e !== bot.entity && e.position && e.isValid && pos)
    .map((e) => ({
      name: (e.username || e.name || '').toLowerCase(),
      dist: Math.floor(dist3d(pos, e.position)),
      dir: getCardinalDirection(pos, e.position),
      type: e.type,
      position: e.position,
    }))
    .filter((e) => e.dist < 30)
    .sort((a, b) => a.dist - b.dist);

  // drowning awareness — the reflex layer needs air state, not just hp
  let headInWater = false;
  try {
    const headBlock = bot.blockAt(pos.offset(0, 1, 0));
    headInWater = !!(headBlock && headBlock.name === 'water');
  } catch (_) {}

  return {
    hp: Math.floor(bot.health ?? 20),
    food: Math.floor(bot.food ?? 20),
    oxygen: bot.oxygenLevel != null ? bot.oxygenLevel : 20,
    headInWater,
    timeLabel,
    raining: !!bot.isRaining,
    hostiles: nearby.filter((e) => HOSTILE_MOBS.has(e.name)),
    players: nearby.filter((e) => e.type === 'player'),
    inv: {
      logIsh: has(/_log|^log/),
      plankIsh: has(/planks/),
      stoneIsh: has(/cobblestone|^stone|deepslate/),
      foodItems: safeFoodCount,
      hasWeapon: has(/sword|_axe/),
      hasPickaxe: has(/pickaxe/),
      hasShield: has(/shield/),
      hasBed: has(/bed/),
      torches: countMatch(/torch/),
      // rhythm-layer stock (kit checks, night cooking, torch-craft feasibility)
      rawFood: countMatch(/^(beef|porkchop|chicken|mutton|cod|salmon|potato)$/),
      sticks: countMatch(/^stick$/),
      coal: countMatch(/^(coal|charcoal)$/),
      logs: countMatch(/_log$|^log$/),
      wool: countMatch(/_wool$/),
      ironIngots: countMatch(/^iron_ingot$/),
      slotsUsed: inv.length,
    },
    y: Math.floor(pos.y),
    // Raw item names so projects (Layer 3) can check specifics like which pickaxe
    // tier she has, without every predicate re-querying the live inventory.
    invNames: inv.map((i) => i.name),
    bedNearby,
    bedPlaced,
    enclosed,
    // Sheltered = near her own bed, her committed home, or actually WALLED IN.
    // The old hardcoded `false` made reflex rule 6 fire every dusk/night tick,
    // starving the whole rhythm layer (audit 2026-07-13 + live round 16).
    hasShelter: bedNearby || nearHome || enclosed,
  };
}

const WorldEvents = {
  spawn: (bot) => buildWorldState(bot, 'just_spawned'),

  // Event-REACTIONS (owner 2026-07-11: react to what happens, don't narrate the
  // goal machine — telemetry never sounds human). She reacts to the world, not
  // to her own task-state.
  foundOre: (bot, ore) =>
    buildWorldState(bot, `found_${ore}`) +
    ` | You just hit ${ore}. React like a player who found it — short, real.`,
  killedMob: (bot, mob) =>
    buildWorldState(bot, `killed_${mob}`) +
    ` | You just killed a ${mob}. Brief, dry reaction or [silent].`,
  nightFalling: (bot) =>
    buildWorldState(bot, 'night_falling') +
    ' | Night is coming and mobs will spawn. React like a wary player or [silent].',
  lowHealth: (bot) =>
    buildWorldState(bot, 'low_health') +
    ' | You are badly hurt. One short, tense line or [silent].',

  death: (bot, cause) =>
    buildWorldState(bot, `died_from_${cause || 'unknown'}`) +
    ' | You just died. Say one short thing like a real player would — salty, dry, or nothing.',

  playerChat: (bot, username, message) =>
    buildWorldState(bot, 'player_spoke') +
    ` | ${username} said: "${message}" | React IF it feels natural — 1 sentence max. Otherwise [silent].`,

  // Owner co-op: she got a direct order from Koro-san and is starting on it. A quick
  // teammate-style acknowledgement, not narration of the goal machine.
  ownerCommand: (bot, phrase, who) =>
    buildWorldState(bot, 'owner_command') +
    ` | ${who || 'Koro-san'} told you to ${phrase}. Acknowledge in <=6 words like a co-op teammate, or [silent].`,

  // Social — someone tossed her an item.
  gift: (bot, who) =>
    buildWorldState(bot, 'received_gift') +
    ` | ${who || 'Koro-san'} just handed you an item. Warm, short thanks or a happy reaction.`,

  // Social — a player keeps whacking her for fun (roughhousing, not a real threat).
  trolled: (bot, who) =>
    buildWorldState(bot, 'being_trolled') +
    ` | ${who || 'Koro-san'} keeps hitting you to mess with you. React playfully annoyed / tease back in <=8 words. You would never actually fight ${who || 'them'}.`,

  // A rare, harmless fumble — she noticed herself do something dumb.
  fumble: (bot, kind) =>
    buildWorldState(bot, 'small_fumble') +
    ` | You just ${kind} — nothing serious. One short, self-aware or flustered line, or [silent].`,

  // A real progression milestone (verified — she actually has the item).
  milestone: (bot, what) =>
    buildWorldState(bot, 'milestone') +
    ` | You just ${what}. React with genuine, short excitement (a real gamer moment), or [silent].`,

  mobAttack: (bot, mobName) =>
    buildWorldState(bot, `attacked_by_${mobName}`) +
    ' | You are being attacked right now.',

  goalStart: (bot, goal) =>
    buildWorldState(bot, `starting_goal_${goal}`) +
    ` | You decided to ${goal}. Say what you're doing in ≤8 words, or [silent].`,

  goalDone: (bot, goal) =>
    buildWorldState(bot, `finished_goal_${goal}`) +
    ` | You just finished: ${goal}. Brief reaction or [silent].`,

  // Narrated intention (research: goal verbalization ρ=0.8 with structured play,
  // and it's the streamer core) — she says what she's setting OUT to do. Verified
  // ground truth = the plan itself, so QC has real facts to check against.
  newPlan: (bot, planLabel) =>
    buildWorldState(bot, 'new_plan') +
    ` | You just decided your next project: ${planLabel}. Announce it in <=8 casual words (what you're gonna do), or [silent].`,

  // She found a chest in the world (village/shipwreck/mineshaft) and looted it.
  lootedChest: (bot, gotAnything) =>
    buildWorldState(bot, gotAnything ? 'looted_found_chest' : 'found_empty_chest') +
    (gotAnything
      ? ' | You just looted a chest you FOUND in the world. React like a player who scored free loot.'
      : ' | The chest you found was empty/junk. One dry disappointed beat or [silent].'),

  // Heard something (cave sound / mob through a wall / creeper hiss).
  heardSound: (bot, what) =>
    buildWorldState(bot, `heard_${what}`) +
    ` | You just HEARD ${what} nearby (didn't see it). One wary/curious beat like a player hearing it, or [silent].`,

  // Home rhythm beats.
  headingHome: (bot, why) =>
    buildWorldState(bot, 'heading_home') +
    ` | You're heading back to your base (${why}). One homey/tired line or [silent].`,

  // Dungeon play: she found a mob spawner and torched it out (real gamer move).
  foundSpawner: (bot) =>
    buildWorldState(bot, 'neutralized_spawner') +
    ' | You just found a DUNGEON MOB SPAWNER and torched it so it stops spawning. React like a player who scored a dungeon — short, hyped or smug.',
};

module.exports = { buildWorldState, snapshotWorld, WorldEvents, HOSTILE_MOBS };
