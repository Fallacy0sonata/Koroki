'use strict';

// The missing item-verb layer (gap analysis 2026-07-13, ARC V). Verified absent
// before this file existed: she HOARDED armor but never wore it, could deposit into
// chests but never loot one, couldn't get a bed (no wool path), couldn't make
// farmland (no hoe), no bucket, no boat. These are the verbs that make a survival
// player a survival player.

const { goals } = require('mineflayer-pathfinder');
const { countOf, totalItems, blockIs } = require('./verify');

function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }
// cooperative interrupt (see skills.js): long loops bail on danger preempt / gen bump
function ctlGen(bot) { return bot._ctl ? bot._ctl.gen : 0; }
function interrupted(bot, g0) {
  const c = bot._ctl;
  return !!(c && (c.gen !== g0 || c.preempt));
}
function vec(x, y, z) { const { Vec3 } = require('vec3'); return new Vec3(x, y, z); }
function items(bot) { return bot.inventory.items() || []; }

// ── ARMOR — wear what she owns (she was hoarding it, never wearing it) ────────

const ARMOR_SLOT = { helmet: 'head', chestplate: 'torso', leggings: 'legs', boots: 'feet' };
const ARMOR_TIER = { netherite: 6, diamond: 5, iron: 4, chainmail: 3, golden: 2, leather: 1, turtle: 4 };

// pure: tier score of an armor item name ('iron_chestplate' → 4), 0 if not armor
function armorScore(name) {
  const m = (name || '').match(/^(\w+?)_(helmet|chestplate|leggings|boots)$/);
  if (!m) return name === 'turtle_helmet' ? ARMOR_TIER.turtle : 0;
  return ARMOR_TIER[m[1]] || 0;
}
// pure: which piece kind ('helmet'...) an item name is, or null
function armorKind(name) {
  const m = (name || '').match(/_(helmet|chestplate|leggings|boots)$/);
  return m ? m[1] : null;
}
// pure: from a list of item names, the best piece name per kind
function bestArmorPieces(names) {
  const best = {};
  for (const n of names) {
    const kind = armorKind(n);
    if (!kind) continue;
    if (!best[kind] || armorScore(n) > armorScore(best[kind])) best[kind] = n;
  }
  return best;
}

// Equip the best owned piece into every armor slot that's empty or worse, and put
// a shield in the off-hand if it's free. Cheap; runs every slow tick.
async function equipArmor(bot) {
  const inv = items(bot);
  const best = bestArmorPieces(inv.map((i) => i.name));
  let changed = false;
  for (const [kind, dest] of Object.entries(ARMOR_SLOT)) {
    const wantName = best[kind];
    if (!wantName) continue;
    // what's worn in that slot now? (equipment slots: 5 head, 6 torso, 7 legs, 8 feet)
    const slotIx = { head: 5, torso: 6, legs: 7, feet: 8 }[dest];
    const worn = bot.inventory.slots ? bot.inventory.slots[slotIx] : null;
    if (worn && armorScore(worn.name) >= armorScore(wantName)) continue;
    const item = inv.find((i) => i.name === wantName);
    if (!item) continue;
    try { await bot.equip(item, dest); changed = true; } catch (_) {}
  }
  // shield lives in the off-hand permanently (player canon)
  const shield = inv.find((i) => i.name === 'shield');
  const off = bot.inventory.slots ? bot.inventory.slots[45] : null;
  if (shield && !off) { try { await bot.equip(shield, 'off-hand'); changed = true; } catch (_) {} }
  if (changed) console.log('[MC]   geared up (armor/shield)');
  return changed;
}

// ── CHEST LOOTING — the structure-loot progression channel ────────────────────

// pure: worth taking from a found chest? (progress-convertible loot only)
const LOOT_TAKE = /diamond|emerald|_ingot|raw_iron|raw_gold|raw_copper|^coal$|^iron_|^gold_|obsidian|saddle|name_tag|golden_apple|enchanted|_pickaxe|_sword|_axe$|_shovel|_hoe|_helmet|_chestplate|_leggings|_boots|shield|bread|cooked_|_seeds|wheat$|carrot|potato|apple$|string|bone$|gunpowder|ender_pearl|arrow|book|paper|emerald|torch|bed$|bucket|flint|hay_block|_wool$/;
function worthLooting(name) {
  if (/poisonous/.test(name || '')) return false; // 'potato' must not drag this in
  return LOOT_TAKE.test(name || '');
}

// Is this chest one of HERS (deposit chest at a landmark) or already looted?
function isKnownOwnChest(wm, pos) {
  if (!wm) return false;
  const near = wm.nearest('landmarks', pos, (e) => e.tag === 'chest' || e.tag === 'chest_looted');
  if (!near) return false;
  const d = Math.hypot(near.pos.x - pos.x, near.pos.y - pos.y, near.pos.z - pos.z);
  return d < 3;
}

// Loot the nearest FOUND chest (village/shipwreck/dungeon/mineshaft). Takes only
// progress loot, verifies the gain, and remembers the chest so she never re-opens it.
async function lootChest(bot, maxDistance = 32) {
  if (!bot.entity || !bot.entity.position) return false;
  // Manners: don't rummage through chests with other players standing there —
  // on a shared server that chest is probably THEIRS (round 16: she ninja-looted
  // a chest with a player 4m away).
  const bystander = bot.nearestEntity((e) => e && e.type === 'player' && e.username
    && e.username !== bot.username && e.position
    && e.position.distanceTo(bot.entity.position) < 12);
  if (bystander) {
    console.log(`[MC]   loot: ${bystander.username} is right there — not going through chests now`);
    return false;
  }
  const wm = bot._wm;
  const candidates = bot.findBlocks({
    matching: (b) => b && (b.name === 'chest' || b.name === 'barrel'),
    maxDistance, count: 8,
  });
  let target = null;
  for (const v of candidates) {
    if (!isKnownOwnChest(wm, v)) { target = bot.blockAt(v); if (target) break; }
  }
  if (!target) return false;
  const p = target.position;
  console.log(`[MC]   loot: found a chest at (${p.x},${p.y},${p.z}) — checking it`);
  try {
    await bot.pathfinder.goto(new goals.GoalNear(p.x, p.y, p.z, 2));
  } catch (_) { return false; }
  let chest;
  try { chest = await bot.openContainer(target); } catch (_) { return false; }
  const before = totalItems(bot);
  let took = 0;
  try {
    for (const it of chest.containerItems()) {
      if (!worthLooting(it.name)) continue;
      try { await chest.withdraw(it.type, null, it.count); took += it.count; } catch (_) {}
    }
    chest.close();
  } catch (_) { try { chest.close(); } catch (_) {} }
  // remember it as looted either way, so she doesn't ping-pong back to an empty box
  if (wm) { wm.noteLandmark(p, 'chest_looted'); wm.save(); }
  const gained = totalItems(bot) > before;
  if (gained) console.log(`[MC]   loot: took ${took} items from the chest`);
  else console.log('[MC]   loot: chest had nothing worth taking');
  return gained;
}

// ── WOOL → BED — the spawn-discipline path (she could never GET a bed before) ──

// pure: which bed color she can craft from an inventory count map (needs 3 same wool)
function bedColorFor(invCounts) {
  for (const [name, n] of Object.entries(invCounts || {})) {
    const m = name.match(/^(\w+)_wool$/);
    if (m && n >= 3) return m[1];
  }
  return null;
}

// Kill sheep until she has 3 matching wool (sheep drop 1 wool + mutton).
async function getWool(bot, timeoutMs = 40000) {
  const skills = require('./skills');
  const { invMap } = require('./verify');
  const deadline = Date.now() + timeoutMs;
  const g0 = ctlGen(bot);
  while (Date.now() < deadline) {
    if (interrupted(bot, g0)) return false;
    if (bedColorFor(invMap(bot))) return true;
    const sheep = bot.nearestEntity((e) => e && e.isValid && e.position && (e.name || '').toLowerCase() === 'sheep');
    if (!sheep) {
      console.log('[MC]   wool: no sheep in sight — wandering to find some');
      await skills.explore(bot);
      continue;
    }
    try {
      await skills.equipBestWeapon(bot);
      // RE-APPROACH inside the loop — sheep flee when hit; a fixed-position swing
      // loop landed ~1 hit per call (audit 2026-07-13). Static snapshots per leg —
      // GoalFollow on a fleeing animal is a path-recompute storm (round 17d).
      while (sheep.isValid && Date.now() < deadline && !interrupted(bot, g0)) {
        const d = sheep.position.distanceTo(bot.entity.position);
        if (d > 2.5) {
          try {
            await bot.pathfinder.goto(new goals.GoalNear(sheep.position.x, sheep.position.y, sheep.position.z, 1));
          } catch (_) {}
          continue;
        }
        await bot.lookAt(sheep.position.offset(0, 0.5, 0), true); // force: no physics-wait mid-swing
        try { await bot.attack(sheep); } catch (_) {}
        await sleep(600);
      }
    } catch (_) {}
    await collectNear(bot, 8); // sweep up the wool + mutton drops
  }
  return !!bedColorFor(require('./verify').invMap(bot));
}

// walk onto nearby drops so auto-pickup grabs them (local mini-sweep)
async function collectNear(bot, r = 8) {
  const me = () => bot.entity && bot.entity.position;
  for (let i = 0; i < 4; i++) {
    const item = bot.nearestEntity((e) => e && e.position && (e.name === 'item' || e.displayName === 'Item')
      && me() && e.position.distanceTo(me()) < r);
    if (!item) return;
    try { await bot.pathfinder.goto(new goals.GoalNear(item.position.x, item.position.y, item.position.z, 0)); } catch (_) {}
    await sleep(150);
  }
}

// Craft whichever bed her wool allows (called by smartCraft's 'bed' special case).
async function craftBed(bot) {
  const { invMap } = require('./verify');
  const color = bedColorFor(invMap(bot));
  if (!color) return false;
  const skills = require('./skills');
  return skills.craft(bot, `${color}_bed`);
}

// ── HOE + FARMLAND — she could only replant EXISTING farmland before ──────────

// Create a small farm near water: till dirt/grass within reach of a water block and
// plant whatever seeds she has. Returns true if at least one crop went in.
async function createFarm(bot) {
  const inv = items(bot);
  const hoe = inv.find((i) => /_hoe$/.test(i.name));
  const seed = inv.find((i) => /wheat_seeds|^carrot$|^potato$|beetroot_seeds/.test(i.name));
  if (!hoe || !seed) return false;
  const water = bot.findBlock({ matching: (b) => b && b.name === 'water', maxDistance: 24 });
  if (!water) return false;
  const w = water.position;
  try { await bot.pathfinder.goto(new goals.GoalNear(w.x, w.y, w.z, 3)); } catch (_) { return false; }
  let planted = 0;
  // till a few tillable blocks within 4 of the water (hydration range)
  for (const [dx, dz] of [[1, 0], [-1, 0], [0, 1], [0, -1], [1, 1], [-1, -1], [2, 0], [0, 2]]) {
    if (planted >= 4) break;
    const ground = bot.blockAt(w.offset(dx, 0, dz));
    if (!ground || !/^(grass_block|dirt)$/.test(ground.name)) continue;
    const above = bot.blockAt(ground.position.offset(0, 1, 0));
    if (!above || !/^(air|short_grass|tall_grass|grass)$/.test(above.name)) continue;
    try {
      await bot.pathfinder.goto(new goals.GoalNear(ground.position.x, ground.position.y, ground.position.z, 2));
      await bot.equip(hoe, 'hand');
      await bot.activateBlock(ground);                       // till → farmland
      if (!blockIs(bot, ground.position, 'farmland')) continue; // VERIFY the till took
      const s = items(bot).find((i) => i.name === seed.name);
      if (!s) break;
      await bot.equip(s, 'hand');
      await bot.placeBlock(bot.blockAt(ground.position), vec(0, 1, 0)); // sow
      planted++;
    } catch (_) {}
  }
  if (planted) {
    console.log(`[MC]   farm: tilled + planted ${planted} crops by the water`);
    if (bot._wm) { bot._wm.noteLandmark(w, 'farm'); bot._wm.save(); }
  }
  return planted > 0;
}

// ── BUCKET — lava insurance / self-extinguish ─────────────────────────────────

// Fill an empty bucket from the nearest water (kit prep).
async function fillWaterBucket(bot) {
  const bucket = items(bot).find((i) => i.name === 'bucket');
  if (!bucket) return false;
  if (countOf(bot, 'water_bucket') > 0) return true;
  const water = bot.findBlock({ matching: (b) => b && b.name === 'water', maxDistance: 24 });
  if (!water) return false;
  try {
    await bot.pathfinder.goto(new goals.GoalNear(water.position.x, water.position.y, water.position.z, 2));
    await bot.equip(bucket, 'hand');
    await bot.lookAt(water.position.offset(0.5, 0.9, 0.5), true);
    await bot.activateItem();
    await sleep(300);
  } catch (_) {}
  return countOf(bot, 'water_bucket') > 0; // VERIFY the scoop
}

// Scoop LAVA — one bucket smelts 100 items, the best fuel in the game (owner ask,
// round 18; also the future nether-portal material). Safety rules: never overhead
// lava, approach to 2 blocks only, look-and-pour from solid ground.
async function fillLavaBucket(bot) {
  const empty = items(bot).find((i) => i.name === 'bucket');
  if (!empty || countOf(bot, 'lava_bucket') > 0) return false;
  const lava = bot.findBlock({ matching: (b) => b && b.name === 'lava', maxDistance: 8 });
  if (!lava) return false;
  if (lava.position.y > bot.entity.position.y) return false; // NEVER scoop overhead lava
  try {
    await bot.pathfinder.goto(new goals.GoalNear(lava.position.x, lava.position.y + 1, lava.position.z, 2));
    await bot.equip(empty, 'hand');
    await bot.lookAt(lava.position.offset(0.5, 0.5, 0.5), true);
    await bot.activateItem();
    await sleep(300);
  } catch (_) {}
  const got = countOf(bot, 'lava_bucket') > 0;
  if (got) console.log('[MC]   scooped a lava bucket (100 smelts of fuel)');
  return got;
}

// EMERGENCY: she's burning/in lava — dump water at her feet, then re-scoop it.
// Called from the fast loop; must not path anywhere.
async function pourAtFeet(bot) {
  const wb = items(bot).find((i) => i.name === 'water_bucket');
  if (!wb) return false;
  try {
    const ground = bot.blockAt(bot.entity.position.offset(0, -1, 0));
    if (!ground) return false;
    await bot.equip(wb, 'hand');
    await bot.lookAt(ground.position.offset(0.5, 1, 0.5), true); // force-look: emergency
    await bot.activateItem();
    await sleep(600);
    // try to scoop it back so the bucket is reusable
    const placed = bot.findBlock({ matching: (b) => b && b.name === 'water', maxDistance: 3 });
    const empty = items(bot).find((i) => i.name === 'bucket');
    if (placed && empty) {
      await bot.equip(empty, 'hand');
      await bot.lookAt(placed.position.offset(0.5, 0.5, 0.5), true);
      await bot.activateItem();
    }
    return true;
  } catch (_) { return false; }
}

// ── BOAT — cross open water like a person, not a 500-block swimmer ────────────
// EXPERIMENTAL (needs live tuning): place boat at the water edge, mount, bang-bang
// steer toward (x,z), dismount near land/shallows.

function boatItem(bot) { return items(bot).find((i) => /_boat$/.test(i.name)); }

async function crossWater(bot, dest, timeoutMs = 60000) {
  const boat = boatItem(bot);
  if (!boat || !dest) return false;
  const water = bot.findBlock({ matching: (b) => b && b.name === 'water', maxDistance: 12 });
  if (!water) return false;
  try {
    await bot.pathfinder.goto(new goals.GoalNear(water.position.x, water.position.y + 1, water.position.z, 1));
    await bot.equip(boat, 'hand');
    await bot.lookAt(water.position.offset(0.5, 1, 0.5), true);
    await bot.activateItem(); // place the boat
    await sleep(600);
  } catch (_) { return false; }
  const boatEnt = bot.nearestEntity((e) => e && e.position && /boat/.test((e.name || '').toLowerCase())
    && e.position.distanceTo(bot.entity.position) < 6);
  if (!boatEnt) return false;
  try { await bot.mount(boatEnt); } catch (_) { return false; }
  console.log(`[MC]   boat: paddling toward (${Math.round(dest.x)},${Math.round(dest.z)})`);
  const deadline = Date.now() + timeoutMs;
  const g0 = ctlGen(bot);
  try {
    while (Date.now() < deadline) {
      if (interrupted(bot, g0)) break; // danger/reset — get off the boat
      const me = bot.entity.position;
      const dx = dest.x - me.x; const dz = dest.z - me.z;
      if (Math.hypot(dx, dz) < 6) break;
      // steer: aim the view at the destination, paddle forward; bang-bang left/right
      await bot.lookAt(vec(dest.x, me.y, dest.z), true);
      try { bot.moveVehicle(0, 1); } catch (_) {}
      // arrived at shallow/land? water under boat gone → get off
      const under = bot.blockAt(me.offset(0, -1, 0));
      if (under && under.name !== 'water') break;
      await sleep(400);
    }
  } finally {
    try { await bot.dismount(); } catch (_) {}
  }
  return true;
}

module.exports = {
  // armor
  equipArmor, armorScore, armorKind, bestArmorPieces,
  // looting
  lootChest, worthLooting, isKnownOwnChest,
  // bed path
  getWool, craftBed, bedColorFor,
  // farming
  createFarm,
  // bucket
  fillWaterBucket, pourAtFeet, fillLavaBucket,
  // boat
  crossWater, boatItem,
};
