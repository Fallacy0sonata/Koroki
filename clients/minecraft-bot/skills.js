'use strict';

// Skill library — Koroki's Minecraft "hands" (2026-07-11 owner arc).
// The brain names a GOAL; a skill owns the HOW. "gather wood" isn't a script she
// recites — it's find nearest log → pathfind → chop → repeat. Each skill is a
// self-contained mineflayer routine; the decision layer (decide.js) picks which.

const { Movements, goals } = require('mineflayer-pathfinder');
const { occupiesSelf, perceive } = require('./perceive');
const { shouldRetreat, mobInfo } = require('./combat');
const { planHut } = require('./building');
const { evaluateSurroundings, createExplorer } = require('./explore');
const { bestFood, isEdible } = require('./foods');
const { countOf, expectedDrop, totalItems, ateOk, blockIs, arrivedNear } = require('./verify');
const { HOSTILE_MOBS } = require('./world-state');

// Ore → best branch-mine Y (1.21 triangular peaks, confirmed w/ DeepSeek research).
// Diamond target is Y=-54 (not the -59 peak): only 5 above peak so density loss is
// tiny, but it sits ABOVE the lava seas (Y-55~-63) → far less lava risk. -54 also
// catches redstone (peak -59) and decent deep-iron on the same trip.
const ORE_Y = {
  coal_ore: 96, copper_ore: 48, iron_ore: 16, gold_ore: -16, lapis_ore: 0,
  redstone_ore: -54, diamond_ore: -54, diamond: -54, emerald_ore: 232,
};

// Minimum pickaxe tier to actually get drops from an ore (1.21 tool gate). Digging
// an ore below tier yields NOTHING — she must skip it until she has the right pick.
const PICK_TIER = { wooden: 1, golden: 1, stone: 2, iron: 3, diamond: 4, netherite: 5 };
function currentPickTier(bot) {
  let best = 0;
  for (const it of bot.inventory.items() || []) {
    const m = it.name.match(/^(wooden|golden|stone|iron|diamond|netherite)_pickaxe$/);
    if (m) best = Math.max(best, PICK_TIER[m[1]]);
  }
  return best;
}
function requiredPickTierFor(name) {
  if (/gold_ore|redstone_ore|diamond_ore|emerald_ore/.test(name)) return 3; // iron pick
  if (/copper_ore|iron_ore|lapis_ore/.test(name)) return 2;                  // stone pick
  if (/obsidian|ancient_debris/.test(name)) return 4;                        // diamond pick
  return 1; // coal + stone/deepslate: wood pick is enough
}
function canMineOre(bot, name) { return currentPickTier(bot) >= requiredPickTierFor(name); }

// Is this ore worth STOPPING for? Copper is 1.21's fool's gold — huge veins, near
// zero use for her — and coal stops mattering once the fuel stock is deep. She was
// nearly softlocked vacuuming copper with iron tools (owner, round 18g). Deliberate
// resolver-driven 'mine copper' goals bypass this — it only gates the HABITS.
function oreWorth(bot, name) {
  const n = (name || '').replace(/^deepslate_/, '');
  if (n === 'copper_ore') return false;
  if (n === 'coal_ore') return invCount(bot, /^(coal|charcoal)$/) < 32;
  return true;
}

// nearest live hostile's position (used by flee to know when she's actually safe)
function nearestHostilePos(bot) {
  const me = bot.entity && bot.entity.position;
  if (!me) return null;
  let best = null; let bd = Infinity;
  for (const e of Object.values(bot.entities || {})) {
    if (!e || !e.position || !e.isValid) continue;
    if (!HOSTILE_MOBS.has((e.name || '').toLowerCase())) continue; // name, not e.type
    const d = e.position.distanceTo(me);
    if (d < bd) { bd = d; best = e.position; }
  }
  return best;
}

function mcData(bot) { return require('minecraft-data')(bot.version); }

// ── Cooperative interrupt (audit 2026-07-13) ──────────────────────────────────
// pathfinder.stop() only breaks skills that are PATHING. Long non-pathfinder loops
// (furnace polls, sheep hunts, boat rides) were un-interruptible: a creeper could
// prime next to her mid-smelt and she'd stand through the fuse. bot.js maintains
// bot._ctl = { gen, preempt }; skills capture g0 at start and bail from their loops
// when the generation moves on (death/reset/watchdog) or a danger preempt fires.
// Skills that ARE the danger response (fight/flee) ignore preempt but honor gen.
function ctlGen(bot) { return bot._ctl ? bot._ctl.gen : 0; }
function interrupted(bot, g0, ignorePreempt = false) {
  const c = bot._ctl;
  if (!c) return false;
  if (c.gen !== g0) return true;
  return ignorePreempt ? false : !!c.preempt;
}

// Dig with server-lag patience. Ores mine SLOW (seconds, not ticks), so Aternos
// dropping the final dig ticks hits them hardest: the client finishes, the server
// disagrees, the ore pops back (owner rounds 18d + 18g: "needs a longer hold").
// Every attempt — INCLUDING the last — gets a settle before we trust the result;
// the old version's final re-cut was verified instantly and always read as failed.
async function digPatient(bot, block) {
  const slow = /_ore$/.test(block.name || '');
  const settle = slow ? 700 : 200;
  const attempts = slow ? 4 : 2;
  for (let i = 0; i < attempts; i++) {
    const target = i === 0 ? block : bot.blockAt(block.position);
    if (!target || target.name !== block.name) return true; // already gone
    try { await bot.dig(target); } catch (_) { return false; }
    await sleep(settle); // hold the moment — let the server agree the block broke
    const still = bot.blockAt(block.position);
    if (!still || still.name !== block.name) return true;
  }
  return false;
}

// pathfinder.goto with a hard wall-clock cap — walking a long/ocean path never
// resolves on its own (she makes micro-progress so it never times out), which froze
// her mid-swim. This guarantees the skill returns and she re-decides.
// ALSO interrupt-aware (round 16c): a creeper hissed at 1m for 8+ SECONDS while she
// waited out a goto — pathfinder.stop() doesn't resolve a goto that's still
// COMPUTING a path, so the preempt never reached her legs. Poll the control channel
// every 250ms and bail out of the walk the moment danger/reset fires.
async function gotoTimed(bot, goal, ms = 15000) {
  let timer; let poll;
  const g0 = ctlGen(bot);
  // attach the catch NOW so if the timeout wins the race, the still-pending goto's
  // later rejection (GoalChanged / PathStopped) is swallowed, not left unhandled.
  const go = bot.pathfinder.goto(goal).catch(() => {});
  try {
    await Promise.race([
      go,
      new Promise((res) => { timer = setTimeout(() => { try { bot.pathfinder.stop(); } catch (_) {} res(); }, ms); }),
      new Promise((res) => {
        poll = setInterval(() => {
          if (interrupted(bot, g0)) { try { bot.pathfinder.stop(); } catch (_) {} res(); }
        }, 250);
      }),
    ]);
  } finally { clearTimeout(timer); clearInterval(poll); }
}

// Lava-safe movement: never let the pathfinder route her into lava/fire/etc. This
// is a survival instinct (Layer 2) — real lava lakes + Aternos desync make "walk
// through the shortest path" a death sentence otherwise.
const AVOID_BLOCKS = ['lava', 'fire', 'soul_fire', 'magma_block', 'campfire', 'cactus', 'sweet_berry_bush', 'wither_rose', 'powder_snow', 'cobweb'];
function setupMovements(bot, opts = {}) {
  const m = new Movements(bot, mcData(bot));
  const d = mcData(bot);
  for (const name of AVOID_BLOCKS) {
    const b = d.blocksByName[name];
    if (b && m.blocksToAvoid) m.blocksToAvoid.add(b.id);
  }
  // Towering (block-up to reach a target) is OFF for general navigation — it thrashes
  // (place→break→drop→retry) on unreachable targets. But WOOD-GATHERING turns it on so
  // she can actually reach elevated canopy logs (acacia). Any thrash is now bounded:
  // gotoTimed caps a stuck attempt at ~15s, then multi-candidate gather skips that log.
  m.allow1by1towers = !!opts.towers;
  // Water is a HAZARD, not a shortcut: prefer land routes when one exists (she
  // wandered into a flooded cave and drowned — round 16).
  if (m.liquidCost != null) m.liquidCost = 4;
  // NO BRIDGING for general travel — pricey water + free block placement made the
  // pathfinder BRIDGE ACROSS A RIVER instead of swimming 8 blocks (round 17).
  // Swim rivers, walk around lakes; scaffolding only for wood-tower gathering.
  if (!opts.towers && Array.isArray(m.scafoldingBlocks)) m.scafoldingBlocks = [];
  bot.pathfinder.setMovements(m);
  // Give up fast on a hard/unreachable path so she cycles to another candidate
  // instead of burning CPU per attempt. A* compute BLOCKS the event loop — long
  // computes on unreachable 50m canopy logs starved the keepalive and Aternos
  // kicked her (the "random disconnects", round 16c). Keep thinks short + bounded.
  bot.pathfinder.thinkTimeout = 1500;
  if ('searchRadius' in bot.pathfinder) bot.pathfinder.searchRadius = 48;
}

// ── gather: mine `count` of a block type, trying MANY candidates ──────────────
// The old version fixated on the single nearest match — fatal in an acacia savanna
// where the nearest log is an unreachable floating canopy piece, so she'd time out
// and give up forever. Now she pulls a batch of candidates and skips any she can't
// reach, so one bad log never blocks her from a reachable one nearby.
const GATHER_RETRY_MS = {
  unreachable: 5 * 60_000,
  hidden: 5 * 60_000,
  lag: 30_000,
  no_drop: 60_000,
  dig_error: 2 * 60_000,
};

function gatherKey(p) { return p ? `${p.x},${p.y},${p.z}` : '' ; }

// Goal calls are short-lived, but a bad coordinate is still bad on the next tick.
// Keep a process-lifetime retry ledger on the bot so sealed/unreachable blocks do
// not become the nearest candidate again immediately. Reconnects deliberately
// clear it because the world/chunk state may have changed.
function rememberGatherFailure(bot, p, reason = 'dig_error', now = Date.now()) {
  if (!bot || !p) return;
  if (!(bot._gatherFailures instanceof Map)) bot._gatherFailures = new Map();
  for (const [key, entry] of bot._gatherFailures) {
    if (entry.until <= now) bot._gatherFailures.delete(key);
  }
  // Bound multi-day sessions even if every failure is at a unique coordinate.
  while (bot._gatherFailures.size >= 512) {
    bot._gatherFailures.delete(bot._gatherFailures.keys().next().value);
  }
  const ttl = GATHER_RETRY_MS[reason] || GATHER_RETRY_MS.dig_error;
  bot._gatherFailures.set(gatherKey(p), { reason, until: now + ttl });
}

function gatherFailureActive(bot, p, now = Date.now()) {
  if (!bot || !(bot._gatherFailures instanceof Map) || !p) return false;
  const key = gatherKey(p);
  const entry = bot._gatherFailures.get(key);
  if (!entry) return false;
  if (entry.until <= now) { bot._gatherFailures.delete(key); return false; }
  return true;
}

async function gather(bot, keyword, count = 3, maxDistance = 64, extra = null) {
  if (!bot.entity || !bot.entity.position) return false;  // not fully spawned yet
  // Towering ONLY for wood (reach elevated canopy logs). For stone/ore/etc it just
  // thrashes and towers on general purposes — keep it off there.
  setupMovements(bot, { towers: /log/.test(keyword) });
  const me = bot.entity.position;
  // `extra` narrows candidates (e.g. only ore her pickaxe tier can actually drop —
  // digging under-tier ore DESTROYS it: the block breaks, nothing drops).
  const match = (b) => b && b.name && b.name.includes(keyword)
    && !gatherFailureActive(bot, b.position) && (!extra || extra(b));
  const k = gatherKey;
  const probe = bot.findBlock({ matching: match, maxDistance });
  console.log(`[MC]   gather(${keyword}): at (${Math.round(me.x)},${Math.round(me.y)},${Math.round(me.z)}), `
    + `nearest=${probe ? probe.name + '@' + Math.round(probe.position.distanceTo(me)) + 'm' : 'NONE'}`);

  let got = 0;
  const failed = new Set();
  const g0 = ctlGen(bot);
  let unreachableStreak = 0;
  // wall-clock cap: a marathon gather (walls + lag re-digs) was outliving the 120s
  // watchdog and getting hard-reset mid-basket — give up and re-decide sooner
  const deadline = Date.now() + 60_000;
  for (let tries = 0; tries < count * 8 && got < count; tries++) {
    if (interrupted(bot, g0)) break; // danger/reset — drop the basket, deal with it
    if (Date.now() > deadline) { console.log(`[MC]   gather(${keyword}): taking too long — re-deciding`); break; }
    // a marathon of far-off unreachable candidates burns CPU (A* blocks the event
    // loop → keepalive starves → Aternos kick) — give up the CALL after a streak;
    // she'll re-decide and come at it fresh from a better spot.
    if (unreachableStreak >= 6) {
      console.log(`[MC]   gather(${keyword}): too many unreachable in a row — moving on`);
      break;
    }
    // a batch of nearest candidates; take the closest one we haven't failed to reach
    const cands = bot.findBlocks({ matching: match, maxDistance, count: 24 });
    let block = null;
    for (const v of cands) {
      if (failed.has(k(v))) continue;
      if (bot.entity && v.distanceTo(bot.entity.position) > 40) continue; // don't chase the horizon
      block = bot.blockAt(v);
      if (block) break;
    }
    if (!block) {
      console.log(`[MC]   gather(${keyword}): no reachable ${keyword} left in range this round`);
      break;
    }
    const p = block.position;
    await gotoTimed(bot, new goals.GoalNear(p.x, p.y, p.z, 2), 15000); // capped (bounds any towering thrash)
    // if she didn't get within reach, treat the log as unreachable and move on
    if (p.distanceTo(bot.entity.position) > 4.5) {
      console.log(`[MC]   gather(${keyword}): can't reach ${block.name}@${Math.round(p.distanceTo(me))}m — trying another`);
      failed.add(k(p));
      rememberGatherFailure(bot, p, 'unreachable');
      unreachableStreak++;
      continue;
    }
    unreachableStreak = 0;
    // NO X-RAY MINING: findBlock sees ore through walls and dig() needs no line of
    // sight — she was breaking iron THROUGH stone and the drop fell sealed inside
    // the wall, unreachable ("dug but got NO drop", round 17). Ores must be visible.
    if (/_ore$/.test(block.name)) {
      let visible = true;
      try { visible = bot.canSeeBlock(block); } catch (_) {}
      if (!visible) {
        console.log(`[MC]   gather(${keyword}): ${block.name} is behind a wall — skipping`);
        failed.add(k(p));
        rememberGatherFailure(bot, p, 'hidden');
        continue;
      }
    }
    try {
      const drop = expectedDrop(block.name);
      const before = drop ? countOf(bot, drop) : totalItems(bot);
      await equipToolFor(bot, block); // right tool = actual drops (stone needs a pickaxe)
      // honor the dig verdict — ignoring it meant a lag-rejected dig still burned a
      // full drop-sweep hunting an item that never existed (round 18h)
      if (!(await digPatient(bot, block))) {
        console.log(`[MC]   gather(${keyword}): dig didn't register (lag) — skipping this one`);
        failed.add(k(p));
        rememberGatherFailure(bot, p, 'lag');
        continue;
      }
      await collectNearestDrop(bot); // step onto the drop so it doesn't rot on the floor
      // VERIFY she actually collected it (drop can fly away, land unreachable, or be
      // grabbed by another player). If not, sweep wider once before counting it.
      let after = drop ? countOf(bot, drop) : totalItems(bot);
      if (after <= before) { await collectDrops(bot, 12); after = drop ? countOf(bot, drop) : totalItems(bot); }
      if (after > before) {
        got++;
        console.log(`[MC]   gather(${keyword}): chopped + collected (${got}/${count})`);
      } else {
        console.log(`[MC]   gather(${keyword}): dug ${block.name} but got NO drop (flew off / taken)`);
        failed.add(k(p));
        rememberGatherFailure(bot, p, 'no_drop');
      }
    } catch (e) {
      console.log(`[MC]   gather(${keyword}): reached but can't dig — ${e.message}`);
      failed.add(k(p));
      rememberGatherFailure(bot, p, 'dig_error');
    }
  }
  if (got > 0) await collectDrops(bot); // final sweep for any strays
  return got > 0;
}

// ── goto: walk to coords or an entity's position (capped + arrival-verified) ──
async function goto(bot, target) {
  setupMovements(bot);
  const p = target?.position || target;
  if (!p) return false;
  await gotoTimed(bot, new goals.GoalNear(p.x, p.y, p.z, 2), 20000); // capped so it can't hang
  return arrivedNear(bot, p, 3); // VERIFY she actually got there
}

// ── follow: stay near a player (runs until cleared) ──────────────────────────
function follow(bot, playerName) {
  setupMovements(bot);
  const target = bot.players[playerName]?.entity;
  if (!target) return false;
  bot.pathfinder.setGoal(new goals.GoalFollow(target, 3), true); // dynamic
  return true;
}
function stopFollow(bot) { bot.pathfinder.setGoal(null); }

// ── fight: engage a hostile using its per-mob doctrine ────────────────────────
// Dispatches by mob style (combat.mobInfo): creepers get hit-and-run (never linger
// in blast range), everything else gets enhanced melee (close the gap, jump-crits,
// break off when hurt). Ranged mobs (rush style) are handled by melee closing fast.
async function fight(bot, targetInfo, timeoutMs = 15000) {
  const style = mobInfo(targetInfo && targetInfo.name).style;
  if (style === 'hitrun') return fightHitRun(bot, targetInfo, timeoutMs);
  return fightMelee(bot, targetInfo, timeoutMs);
}

function findMob(bot, name) {
  // Match by NAME, not e.type — in 1.21 mineflayer hostiles may be type 'hostile'
  // and animals 'animal', so gating on 'mob' silently found nothing.
  return bot.nearestEntity((e) => e && e.isValid && e.position && (e.name || '').toLowerCase() === name);
}
// is a live mob of this name still within `r` blocks? (to tell "killed it" from
// "it walked away" — findMob returning null is NOT a confirmed kill).
function mobStillNearby(bot, name, r = 24) {
  const me = bot.entity && bot.entity.position;
  if (!me) return false;
  return !!bot.nearestEntity((e) => e && e.isValid && e.position
    && (e.name || '').toLowerCase() === name && e.position.distanceTo(me) < r);
}

async function equipShield(bot) {
  const shield = (bot.inventory.items() || []).find((i) => i.name === 'shield');
  if (!shield) return false;
  try { await bot.equip(shield, 'off-hand'); return true; } catch (_) { return false; }
}

async function fightMelee(bot, targetInfo, timeoutMs) {
  setupMovements(bot);
  await equipBestWeapon(bot);
  const ranged = require('./combat').isRanged(targetInfo && targetInfo.name);
  const hasShield = ranged ? await equipShield(bot) : false; // shield up vs arrows
  const deadline = Date.now() + timeoutMs;
  const g0 = ctlGen(bot);
  while (Date.now() < deadline) {
    if (interrupted(bot, g0, true)) return false; // gen moved on (death/reset) — stop
    if (shouldRetreat(bot.health)) { try { bot.deactivateItem(); } catch (_) {} return false; }
    const mob = findMob(bot, targetInfo && targetInfo.name);
    if (!mob) {
      try { bot.deactivateItem(); } catch (_) {}
      // gone from the query — killed ONLY if no such mob remains nearby (else it fled)
      return !mobStillNearby(bot, targetInfo && targetInfo.name);
    }
    const d = mob.position.distanceTo(bot.entity.position);
    if (d > 3) {
      if (hasShield) { try { bot.activateItem(true); } catch (_) {} } // raise shield while closing
      // WALL-CLIMBER (spider up a cliff): unreachable moving target = pathfinder
      // recompute storm that BLOCKS the event loop until the connection times out
      // (the mid-fight "crashes", round 17d). Hold ground, let it come down.
      if (mob.position.y > bot.entity.position.y + 3) { await sleep(400); continue; }
      // STATIC position snapshot, not GoalFollow — one path compute per approach;
      // the loop re-snapshots the mob every iteration anyway.
      await gotoTimed(bot, new goals.GoalNear(mob.position.x, mob.position.y, mob.position.z, 2), 3000);
    } else {
      if (hasShield) { try { bot.deactivateItem(); } catch (_) {} } // drop shield to strike
      // FORCE look (instant) — smooth look waits on physics ticks, and explosion
      // desync stalls physics: the await never resolved = the combat freeze (r18c)
      await bot.lookAt(mob.position.offset(0, mob.height * 0.9, 0), true);
      if (bot.entity.onGround) { // jump-crit: strike while falling = critical damage
        try { bot.setControlState('jump', true); await sleep(150); bot.setControlState('jump', false); } catch (_) {}
      }
      try { await bot.attack(mob); } catch (_) {}
      await sleep(600); // attack cooldown
    }
  }
  try { bot.deactivateItem(); } catch (_) {}
  return false;
}

// Creeper tactic: dart in, land ONE hit, immediately retreat out of blast range so
// its fuse resets, then repeat. Never stand next to a primed creeper.
async function fightHitRun(bot, targetInfo, timeoutMs) {
  setupMovements(bot);
  await equipBestWeapon(bot);
  const deadline = Date.now() + timeoutMs;
  const g0 = ctlGen(bot);
  while (Date.now() < deadline) {
    if (interrupted(bot, g0, true)) return false;
    if (shouldRetreat(bot.health)) return false;
    let mob = findMob(bot, targetInfo && targetInfo.name);
    if (!mob) return !mobStillNearby(bot, targetInfo && targetInfo.name);
    await gotoTimed(bot, new goals.GoalNear(mob.position.x, mob.position.y, mob.position.z, 2), 4000);
    mob = findMob(bot, targetInfo && targetInfo.name);
    if (!mob) return !mobStillNearby(bot, targetInfo && targetInfo.name);
    if (mob.position.distanceTo(bot.entity.position) <= 3.5) {
      await bot.lookAt(mob.position.offset(0, mob.height * 0.9, 0), true); // force: never physics-wait in combat
      try { await bot.attack(mob); } catch (_) {}
      await backOff(bot, mob.position, 5); // clear blast range immediately
      await sleep(1000);                   // let the fuse reset before re-approaching
    }
  }
  return false;
}

// sprint from `pos` roughly `blocks` away along the flee vector
async function backOff(bot, pos, blocks = 6) {
  const me = bot.entity.position;
  const dx = me.x - pos.x; const dz = me.z - pos.z;
  const len = Math.hypot(dx, dz) || 1;
  const dest = { x: Math.floor(me.x + (dx / len) * blocks), z: Math.floor(me.z + (dz / len) * blocks) };
  await gotoTimed(bot, new goals.GoalXZ(dest.x, dest.z), 5000);
}

// ── flee: a COMMITTED escape — keep running until she's actually safe ─────────
// The old flee ran a fixed ~20 blocks then stopped, so the mob (16-block detection)
// caught right back up → run/stop/run softlock. This commits: it re-derives the
// nearest threat each leg and sprints away in bursts until she's clear (> SAFE_M)
// or the threat is gone, up to a timeout. Because _currentGoal stays 'flee', the
// fast-loop danger reflex won't re-preempt her mid-escape.
const FLEE_SAFE_M = 22;
async function flee(bot, threatInfo, blocks = 30) {
  setupMovements(bot);
  const deadline = Date.now() + 12000;
  const g0 = ctlGen(bot);
  while (Date.now() < deadline) {
    if (interrupted(bot, g0, true)) return true; // superseded — the new loop owns her now
    if (!bot.entity || !bot.entity.position) return true;
    const me = bot.entity.position;
    const t = nearestHostilePos(bot) || threatInfo?.position || threatInfo;
    if (!t) return true; // nothing chasing → safe
    const gap = Math.hypot(me.x - t.x, me.z - t.z);
    if (gap > FLEE_SAFE_M) return true; // clear
    const dx = me.x - t.x; const dz = me.z - t.z;
    const len = Math.hypot(dx, dz) || 1;
    const dest = { x: Math.floor(me.x + (dx / len) * blocks), z: Math.floor(me.z + (dz / len) * blocks) };
    await gotoTimed(bot, new goals.GoalXZ(dest.x, dest.z), 6000); // per-leg cap so flee can't hang
  }
  return true;
}

// ── eat: consume the BEST food she has (highest saturation, skip risky/rotten) ─
async function eat(bot) {
  const names = (bot.inventory.items() || []).map((i) => i.name);
  const desperate = (bot.food != null ? bot.food : 20) <= 2;
  const pick = bestFood(names, desperate);
  if (!pick) return false;
  const food = (bot.inventory.items() || []).find((i) => i.name === pick);
  if (!food) return false;
  const foodBefore = bot.food != null ? bot.food : 20;
  try {
    await bot.equip(food, 'hand');
    await bot.consume();
  } catch (_) { return false; }
  return ateOk(bot, foodBefore); // verify hunger actually went up (or was already full)
}

// ── hunt: find a passive animal and kill it for food — SEARCHING if none near ──
// The old version returned instantly when no animal was in sight, so a starving bot
// just spammed "hunt, no food" in place forever. Now, when nothing's nearby, she
// WANDERS to look (this is why exploration matters), and she collects the meat drop.
async function hunt(bot, timeoutMs = 30000) {
  setupMovements(bot);
  const PREY = new Set(['cow', 'pig', 'chicken', 'sheep', 'rabbit', 'mooshroom']);
  await equipBestWeapon(bot);
  // ANIMALS LIVE ON THE SURFACE. Hungry in a cave = go UP first — she used to
  // "hunt" by wandering tunnels until she starved and went AFK (round 17).
  if (bot.entity && bot.entity.position && bot.entity.position.y < 50) {
    console.log('[MC]   hunt: underground — heading up top first');
    await goSurface(bot);
  }
  const deadline = Date.now() + timeoutMs;
  const g0 = ctlGen(bot);
  let wanders = 0;
  while (Date.now() < deadline) {
    if (interrupted(bot, g0)) return false;
    const prey = bot.nearestEntity((e) => e && e.isValid && e.position && PREY.has((e.name || '').toLowerCase()));
    if (!prey) {
      if (wanders++ >= 4) return false; // searched a fair bit — end this call (she'll resume)
      console.log('[MC]   hunt: no animals in sight — wandering to find some');
      await explore(bot);               // go look instead of spamming in place
      continue;
    }
    const d = prey.position.distanceTo(bot.entity.position);
    if (d > 2.5) {
      // static snapshot — GoalFollow on fleeing prey is a path-recompute storm
      await gotoTimed(bot, new goals.GoalNear(prey.position.x, prey.position.y, prey.position.z, 1), 4000);
    } else {
      await bot.attack(prey);
      await sleep(600);
      if (!prey.isValid) {
        // VERIFY the meat actually landed in her pack — a kill with no pickup is not food
        const before = totalItems(bot);
        await collectDrops(bot, 6);
        return totalItems(bot) > before;
      }
    }
  }
  return false;
}

// ── forage: gather directly-edible wild plants (berries, ripe crops) ──────────
// A food source when there are no animals around (owner: "no food mob or plant").
async function forage(bot) {
  setupMovements(bot);
  // 1. sweet berry bushes — right-click to harvest berries
  const bush = bot.findBlock({ matching: (b) => b && b.name === 'sweet_berry_bush', maxDistance: 32 });
  if (bush) {
    try {
      const before = totalItems(bot);
      await bot.pathfinder.goto(new goals.GoalNear(bush.position.x, bush.position.y, bush.position.z, 1));
      await bot.activateBlock(bush);
      await collectDrops(bot, 5);
      if (totalItems(bot) > before) return true; // VERIFY berries actually gained
    } catch (_) {}
  }
  // 2. mature crops (carrots/potatoes/beetroot are edible; break them)
  const crop = bot.findBlock({
    maxDistance: 32,
    matching: (b) => {
      if (!b || !/^(carrots|potatoes|beetroots)$/.test(b.name)) return false;
      const age = b.getProperties ? b.getProperties().age : undefined;
      const ripe = b.name === 'beetroots' ? 3 : 7;
      return age === undefined || Number(age) >= ripe;
    },
  });
  if (crop) {
    try {
      const before = totalItems(bot);
      await bot.pathfinder.goto(new goals.GoalNear(crop.position.x, crop.position.y, crop.position.z, 1));
      await bot.dig(crop);
      await collectDrops(bot, 5);
      return totalItems(bot) > before; // VERIFY the harvest was collected
    } catch (_) {}
  }
  return false;
}

// ── farmCrops: harvest mature crops + replant — sustainable food ──────────────
async function farmCrops(bot) {
  setupMovements(bot);
  let did = false;
  const mature = (b) => {
    if (!b) return false;
    const age = b.getProperties ? Number(b.getProperties().age) : undefined;
    if (b.name === 'wheat' && age === 7) return true;
    if ((b.name === 'carrots' || b.name === 'potatoes') && age === 7) return true;
    if (b.name === 'beetroots' && age === 3) return true;
    return false;
  };
  for (let i = 0; i < 6; i++) {
    const crop = bot.findBlock({ matching: mature, maxDistance: 24 });
    if (!crop) break;
    try {
      await bot.pathfinder.goto(new goals.GoalNear(crop.position.x, crop.position.y, crop.position.z, 1));
      await bot.dig(crop); await collectNearestDrop(bot, 4); did = true;
    } catch (_) { break; }
  }
  // replant on bare farmland if she has something to sow
  const seed = (bot.inventory.items() || []).find((i) => /wheat_seeds|^carrot$|^potato$|beetroot_seeds/.test(i.name));
  if (seed) {
    const land = bot.findBlock({
      matching: (b) => b && b.name === 'farmland' && (bot.blockAt(b.position.offset(0, 1, 0)) || {}).name === 'air',
      maxDistance: 16,
    });
    if (land) {
      try {
        await bot.pathfinder.goto(new goals.GoalNear(land.position.x, land.position.y, land.position.z, 1));
        await bot.equip(seed, 'hand'); await bot.placeBlock(land, vec(0, 1, 0)); did = true;
      } catch (_) {}
    }
  }
  await collectDrops(bot, 8);
  return did;
}

// ── breedAnimals: feed two of the same animal to make more (future food) ───────
async function breedAnimals(bot) {
  setupMovements(bot);
  const BREED = { cow: 'wheat', sheep: 'wheat', mooshroom: 'wheat', pig: 'carrot', rabbit: 'carrot', chicken: 'wheat_seeds' };
  const items = bot.inventory.items() || [];
  for (const [species, foodName] of Object.entries(BREED)) {
    const foodItem = items.find((i) => i.name === foodName);
    if (!foodItem) continue;
    const near = Object.values(bot.entities || {}).filter((e) => e && e.name === species && e.position && e.isValid);
    if (near.length < 2) continue;
    let fed = 0;
    for (const animal of near.slice(0, 2)) {
      try {
        await gotoTimed(bot, new goals.GoalNear(animal.position.x, animal.position.y, animal.position.z, 1), 6000);
        await bot.equip(foodItem, 'hand');
        await bot.activateEntity(animal); fed++; await sleep(400);
      } catch (_) {}
    }
    if (fed >= 2) { console.log(`[MC]   bred ${species}`); return true; }
  }
  return false;
}

// ── hungerReset: last-resort — set spawn at a bed, then die to reset hunger ────
// Owner strat (Normal difficulty): drop a bed to set respawn EXACTLY here, then die
// from a short fall (she's already at ~½ heart from starving). She respawns on this
// spot with full food + health; death-recovery grabs the items she dropped. Only
// runs when truly stuck (gated in bot.js: no food, no prey found, low HP, coast
// clear, has a bed). EXPERIMENTAL — self-death timing needs live tuning.
async function hungerReset(bot) {
  const bedItem = (bot.inventory.items() || []).find((i) => i.name.endsWith('_bed'));
  if (!bedItem) return false;
  // place the bed here (2-block placement; carve a spot if nature won't give one)
  let bed = await placeBed(bot, bedItem);
  if (!bed && await makeBedSpot(bot)) bed = await placeBed(bot, bedItem);
  if (!bed) return false;
  try { await bot.activateBlock(bed); } catch (_) {} // sets respawn point at the bed
  await sleep(700);
  // die by a short fall — lethal because starvation already drained her health
  await pillarUp(bot, 6);
  bot.clearControlStates();
  const me = bot.entity.position;
  try { await bot.pathfinder.goto(new goals.GoalXZ(Math.floor(me.x) + 2, Math.floor(me.z))); } catch (_) {}
  return true;
}

// Make a 2x1 bed spot when nature doesn't offer one (owner, round 18f): carve the
// obstructions out (grass, leaves, stone — she has tools), patch missing ground,
// and clear headroom over both cells so waking up on the bed is safe. Returns true
// when at least one direction is now bed-ready.
async function makeBedSpot(bot) {
  if (!perceive(bot).valid) return false;
  const base = bot.entity.position.floored();
  const g0 = ctlGen(bot);
  for (const [dx, dz] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
    if (interrupted(bot, g0)) return false;
    const cells = [base.offset(dx, 0, dz), base.offset(dx * 2, 0, dz * 2)];
    let ok = true;
    for (const c of cells) {
      // ground under the cell: patch a hole with a block if needed
      const gpos = c.offset(0, -1, 0);
      const g = bot.blockAt(gpos);
      if (!g) { ok = false; break; }
      if (g.boundingBox !== 'block') {
        if (!(await placeAt(bot, gpos))) { ok = false; break; }
      }
      // the cell itself + headroom above: carve out anything solid
      for (const dy of [0, 1]) {
        const s = bot.blockAt(c.offset(0, dy, 0));
        if (!s) { ok = false; break; }
        if (s.name === 'air' || REPLACEABLE.test(s.name)) continue;
        if (/_bed$/.test(s.name) || s.name === 'water' || s.name === 'lava') { ok = false; break; }
        if (s.boundingBox !== 'block') { ok = false; break; }
        try { await equipToolFor(bot, s); await digPatient(bot, s); } catch (_) { ok = false; }
        if (!ok) break;
      }
      if (!ok) break;
    }
    if (ok) return true;
  }
  return false;
}

// A bed is TWO blocks long — placeBlockBeside only checked one free block, so she
// tried to cram beds into 1-block nooks (round 17). This finds a spot with head+
// foot space, faces along the bed axis (orientation follows the placer's look),
// and verifies the bed actually exists afterward.
async function placeBed(bot, bedItem) {
  if (!bot.entity || !bot.entity.position) return null;
  const base = bot.entity.position.floored();
  for (const [dx, dz] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
    const g1 = bot.blockAt(base.offset(dx, -1, dz));
    const s1 = bot.blockAt(base.offset(dx, 0, dz));
    const g2 = bot.blockAt(base.offset(dx * 2, -1, dz * 2));
    const s2 = bot.blockAt(base.offset(dx * 2, 0, dz * 2));
    if (!g1 || g1.boundingBox !== 'block' || !s1 || !REPLACEABLE.test(s1.name)) continue;
    if (!g2 || g2.boundingBox !== 'block' || !s2 || !REPLACEABLE.test(s2.name)) continue;
    if (occupiesSelf(bot, s1.position) || occupiesSelf(bot, s2.position)) continue;
    try {
      await bot.equip(bedItem, 'hand');
      await bot.lookAt(base.offset(dx * 2, 0.5, dz * 2), true); // head extends the way she faces
      await bot.placeBlock(g1, vec(0, 1, 0));
      await sleep(250);
      const placed = bot.findBlock({ matching: (b) => b && /_bed$/.test(b.name || ''), maxDistance: 4 });
      if (placed) return placed;
    } catch (_) { /* try another direction */ }
  }
  return null;
}

// ── sleep: find a bed (or PLACE the one she carries — bed nomad), sleep in it ──
// Sleeping skips the night AND sets her respawn point — the canon habit. If no bed
// block is nearby but she has one in her pack, she places it right here first.
async function sleep_in_bed(bot) {
  // /_bed$/, NOT includes('bed') — that matched BEDROCK and livelocked sleep at
  // diamond depth while a skeleton shot her (audit 2026-07-13).
  let bed = bot.findBlock({ matching: (b) => b && /_bed$/.test(b.name || ''), maxDistance: 16 });
  if (!bed) {
    const bedItem = (bot.inventory.items() || []).find((i) => i.name.endsWith('_bed'));
    if (!bedItem) return false;
    bed = await placeBed(bot, bedItem); // needs 2 free blocks, not 1
    if (!bed && await makeBedSpot(bot)) {
      // no natural 2x1 around — she just CARVED one (owner ask, round 18f)
      console.log('[MC]   sleep: cleared a spot for the bed');
      bed = await placeBed(bot, bedItem);
    }
    if (!bed) return false;
    console.log('[MC]   sleep: placed my bed here for the night');
  }
  try {
    await goto(bot, bed.position);
    await bot.sleep(bed);
    if (bot._wm) { bot._wm.noteLandmark(bed.position, 'bed'); bot._wm.save(); }
    // TEMP-CAMP bed (owner ask, round 18c): a bed slept in AWAY from home gets
    // packed back up on wake — night-skip anywhere, bed rides along. The bed at
    // her committed base STAYS (that one is home).
    const base = bot._wm && bot._wm.getBase();
    const isHomeBed = base && base.committed
      && Math.hypot(base.x - bed.position.x, base.z - bed.position.z) <= 16;
    if (!isHomeBed) {
      const bpos = bed.position.clone ? bed.position.clone() : bed.position;
      bot.once('wake', () => {
        (async () => {
          try {
            const b = bot.blockAt(bpos);
            if (b && /_bed$/.test(b.name || '')) {
              await bot.dig(b);
              await collectNearestDrop(bot, 6);
              if (bot._wm) { bot._wm.removeLandmark(bpos); bot._wm.save(); }
              console.log('[MC]   (packed the bed back up)');
            }
          } catch (_) {}
        })();
      });
    }
    return true;
  } catch (_) { return false; }
}

// ── goHome: walk back to base (the missing "return" half of the expedition loop) ─
// Long hauls take several capped legs; verifies real arrival, and makes sure home
// actually has a chest to dump into once she's there.
async function goHome(bot) {
  const base = bot._wm && bot._wm.getBase();
  if (!base || !bot.entity || !bot.entity.position) return false;
  setupMovements(bot);
  const g0 = ctlGen(bot);
  for (let leg = 0; leg < 4; leg++) {
    if (interrupted(bot, g0)) break;
    const me = bot.entity.position;
    const d = Math.hypot(base.x - me.x, base.z - me.z);
    if (d <= 12) break;
    await gotoTimed(bot, new goals.GoalNear(base.x, base.y, base.z, 4), 20000);
    const after = bot.entity.position;
    if (Math.hypot(after.x - me.x, after.z - me.z) < 2) break; // no progress — stop burning legs
  }
  bot._lastHomeTripAt = Date.now(); // one trip per cooldown — no rubber-band tether
  const home = arrivedNear(bot, base, 14);
  if (home) {
    // home ritual: make sure the dump chest exists, then stash the haul
    try { await ensureChest(bot); } catch (_) {}
    try { await depositSurplus(bot); } catch (_) {}
  }
  return home;
}

// ── smeltFood: cook whatever raw food she's carrying (night/kit-prep chore) ────
const RAW_FOODS = ['beef', 'porkchop', 'chicken', 'mutton', 'cod', 'salmon', 'potato'];
async function smeltFood(bot) {
  const raw = (bot.inventory.items() || []).find((i) => RAW_FOODS.includes(i.name));
  if (!raw) return false;
  return smelt(bot, raw.name, Math.min(raw.count, 8));
}

// ── surface: ESCAPE WATER before the air runs out (water-cave death, round 16) ─
// Three escapes in order of speed:
//  1. open water above → swim straight up (hold jump until the head clears)
//  2. ceiling overhead (flooded cave) → retreat to the last spot she could breathe
//     (bot._lastAirPos breadcrumb, laid by the fast loop every tick on dry ticks)
//  3. no breadcrumb → nearest 2-tall air pocket she can reach
async function surface(bot) {
  if (!bot.entity || !bot.entity.position) return false;
  const g0 = ctlGen(bot);
  const breathing = () => { const p = perceive(bot); return p.valid && !p.headInWater; };
  if (breathing()) return true;

  // 1. anything solid between her and the sky? scan the column above her head
  let openAbove = false;
  for (let dy = 1; dy <= 24; dy++) {
    const b = bot.blockAt(bot.entity.position.offset(0, dy, 0));
    if (!b) break; // unloaded = unknown = treat as ceiling
    if (b.name === 'water') continue;
    openAbove = b.name === 'air' || b.name === 'cave_air' || REPLACEABLE.test(b.name);
    break;
  }
  if (openAbove) {
    try { bot.pathfinder.stop(); } catch (_) {}
    try { bot.setControlState('jump', true); bot.setControlState('sprint', true); } catch (_) {}
    const deadline = Date.now() + 12000;
    while (Date.now() < deadline && !interrupted(bot, g0, true)) {
      if (breathing()) break;
      await sleep(200);
    }
    try { bot.setControlState('jump', false); } catch (_) {}
    if (breathing()) return true;
  }

  // 2. back to the last place she had air (the way she came in)
  if (bot._lastAirPos) {
    setupMovements(bot);
    const a = bot._lastAirPos;
    await gotoTimed(bot, new goals.GoalNear(a.x, a.y, a.z, 1), 15000);
    if (breathing()) return true;
  }

  // 3. nearest 2-tall air pocket (only cheap underground — everything else is stone/water)
  const pocket = bot.findBlock({
    maxDistance: 20,
    matching: (b) => {
      if (!b || (b.name !== 'air' && b.name !== 'cave_air')) return false;
      const up = bot.blockAt(b.position.offset(0, 1, 0));
      return !!up && (up.name === 'air' || up.name === 'cave_air');
    },
  });
  if (pocket) {
    setupMovements(bot);
    await gotoTimed(bot, new goals.GoalNear(pocket.position.x, pocket.position.y, pocket.position.z, 0), 12000);
  }
  return breathing();
}

// ── neutralizeSpawner: the dungeon play (owner strat, round 17d) ──────────────
// Torching a spawner's cage + room stops it spawning (light gate), THEN the fight
// reflex cleans up whatever already spawned. Torch first — killing mobs while the
// cage keeps pumping out more is a losing fight.
async function neutralizeSpawner(bot) {
  const sp = bot.findBlock({ matching: (b) => b && b.name === 'spawner', maxDistance: 16 });
  if (!sp) return false;
  const torch = (bot.inventory.items() || []).find((i) => i.name === 'torch');
  if (!torch) return false;
  setupMovements(bot);
  const g0 = ctlGen(bot);
  await gotoTimed(bot, new goals.GoalNear(sp.position.x, sp.position.y, sp.position.z, 2), 12000);
  if (interrupted(bot, g0)) return false;
  // torch ON TOP of the cage first (kills the source), then flood the room
  let litTop = false;
  try {
    await bot.equip(torch, 'hand');
    await bot.placeBlock(sp, vec(0, 1, 0));
    litTop = blockIs(bot, sp.position.offset(0, 1, 0), 'torch');
  } catch (_) {}
  try { await lightArea(bot, 5); } catch (_) {}
  if (bot._wm) { bot._wm.noteLandmark(sp.position, 'spawner_lit'); bot._wm.save(); }
  console.log(`[MC]   spawner: cage ${litTop ? 'torched' : 'room lit (top blocked)'} — it's out of business`);
  return true;
}

// ── goSurface: climb back to the overworld surface (food lives up there) ──────
// Pathfinder digs/staircases upward as needed (canDig is on). Multi-leg, capped,
// interrupt-aware; gives up gracefully if she stops gaining height.
async function goSurface(bot, targetY = 63) {
  setupMovements(bot);
  const g0 = ctlGen(bot);
  for (let leg = 0; leg < 4; leg++) {
    if (interrupted(bot, g0)) return false;
    if (!bot.entity || !bot.entity.position) return false;
    const y0 = bot.entity.position.y;
    if (y0 >= targetY - 2) return true;
    await gotoTimed(bot, new goals.GoalY(targetY), 20000);
    if (bot.entity.position.y <= y0 + 1) break; // not gaining height — stop burning legs
  }
  return !!(bot.entity && bot.entity.position && bot.entity.position.y >= targetY - 2);
}

// ── reachLand: get out of water onto dry, solid ground ───────────────────────
// She was drowning by walling herself in while standing in water. This finds the
// nearest dry footing (a solid block with air above, not water) and walks onto it.
async function reachLand(bot) {
  setupMovements(bot);
  const land = bot.findBlock({
    maxDistance: 48,
    count: 1,
    matching: (b) => {
      if (!b || b.boundingBox !== 'block' || b.name === 'water' || b.name === 'lava') return false;
      const above = bot.blockAt(b.position.offset(0, 1, 0));
      return above && above.name === 'air';
    },
  });
  if (!land) return false;
  await gotoTimed(bot, new goals.GoalNear(land.position.x, land.position.y + 1, land.position.z, 1), 12000);
  const p = perceive(bot);
  return !!(p.valid && p.onSolidGround && !p.feetInWater);
}

// ── pillarUp: place blocks under herself to rise, one clean hop at a time ──────
// Efficient single-jump pillaring: stop any pathing (so the pathfinder can't fight
// us by scaffolding+breaking), look straight down, then for each level do ONE hop
// and place a block under her at the apex. Bails the moment she can't gain height
// (deep water with no solid floor to build on) instead of thrashing forever.
async function pillarUp(bot, times = 3) {
  if (!perceive(bot).valid) return false; // no blind building on a desynced body
  const block = (bot.inventory.items() || []).find((i) => isPlaceable(i.name));
  if (!block) return false;
  try { bot.pathfinder.stop(); } catch (_) {}
  bot.clearControlStates();
  try { await bot.equip(block, 'hand'); } catch (_) { return false; }
  try { await bot.look(bot.entity.yaw, -Math.PI / 2, true); } catch (_) {} // face straight down

  let placed = 0;
  for (let i = 0; i < times; i++) {
    const ref = bot.blockAt(bot.entity.position.offset(0, -1, 0));
    if (!ref || ref.boundingBox !== 'block') break; // no solid floor to build on
    const startY = Math.floor(bot.entity.position.y);
    try {
      bot.setControlState('jump', true);
      await sleep(60);
      bot.setControlState('jump', false); // a SINGLE hop, not a held bounce
      await sleep(260);                    // ~apex
      await bot.placeBlock(ref, vec(0, 1, 0));
    } catch (_) {
      try { bot.setControlState('jump', false); } catch (_) {}
    }
    await sleep(120);
    if (Math.floor(bot.entity.position.y) <= startY) break; // didn't rise → stop thrashing
    placed++;
  }
  return placed > 0;
}

// ── build_shelter: panic-wall — box herself in with whatever blocks she has ───
async function buildShelter(bot) {
  // NEVER build blind: a null/NaN position mid-desync turned this into
  // "Cannot read properties of null" throws (round 18e). Wait out the desync.
  const per = perceive(bot);
  if (!per.valid) return false;
  // NEVER shelter in water — she'll wall herself in and drown. Get to dry footing
  // first (reach land, or pillar up out of open water), then wall up.
  if (per.feetInWater || per.headInWater || !per.onSolidGround) {
    if (!(await reachLand(bot))) await pillarUp(bot);
  }
  const block = (bot.inventory.items() || []).find((i) => isPlaceable(i.name));
  if (!block) return false;
  await bot.equip(block, 'hand');
  const me = bot.entity.position.floored();
  // wall the 4 cardinal sides at foot + head height, then cap the top
  const offsets = [
    [1, 0, 0], [-1, 0, 0], [0, 0, 1], [0, 0, -1],
    [1, 1, 0], [-1, 1, 0], [0, 1, 1], [0, 1, -1],
    [0, 2, 0],
  ];
  let placed = 0;
  for (const [dx, dy, dz] of offsets) {
    const target = me.offset(dx, dy, dz);
    if (occupiesSelf(bot, target)) continue; // never wall a block into her own body
    const ref = bot.blockAt(target.offset(0, -1, 0)) || bot.blockAt(target.offset(-dx || 1, 0, 0));
    try {
      const refBlock = bot.blockAt(me.offset(dx, dy - 1 >= 0 ? dy - 1 : 0, dz));
      if (refBlock && refBlock.name !== 'air') {
        await bot.placeBlock(refBlock, vec(0, 1, 0));
        placed++;
      }
    } catch (_) { /* face blocked — skip */ }
    await sleep(120);
  }
  return placed > 0;
}

// ── buildHut: deliberate build — efficient shell + a lit, decorated interior ──
// The owner's doctrine ("mathematically efficient structure, decorated interior").
// planHut computes the geometry; this lays the shell (walls minus a doorway, roof)
// then a decoration pass (torch inside). Best-effort placement — live testing will
// surface reach/terrain edge cases (that's the plan).
async function buildHut(bot) {
  if (!bot.entity || !bot.entity.position) return false;
  setupMovements(bot);
  const pickBlock = () => (bot.inventory.items() || []).find((i) => isPlaceable(i.name) && !/torch/.test(i.name));
  const haveBlocks = () => (pickBlock() ? pickBlock().count : 0);
  if (haveBlocks() < 8) { console.log('[MC]   buildHut: not enough blocks'); return false; }

  const c = bot.entity.position.floored();
  const plan = planHut(c, 3, 3, 2);
  const order = [...plan.walls, ...plan.roof];
  let placed = 0;
  for (const tp of order) {
    if (haveBlocks() <= 0) break;
    const target = vec(tp.x, tp.y, tp.z);
    const existing = bot.blockAt(target);
    if (existing && !REPLACEABLE.test(existing.name)) continue; // already solid (grass is replaceable)
    if (occupiesSelf(bot, tp)) continue;                        // never wall herself in
    const blk = pickBlock();
    if (!blk) break;
    try { await bot.equip(blk, 'hand'); } catch (_) {}
    if (await placeAt(bot, target)) placed++;
  }
  // decorated interior + mob-proof the surrounding area
  try { await placeTorch(bot); } catch (_) {}
  try { await lightArea(bot, 6); } catch (_) {}
  console.log(`[MC]   buildHut: placed ${placed}/${order.length}`);
  return placed > 0;
}

// Place a block at `target` by building against a solid neighbor face.
async function placeAt(bot, target) {
  const NEIGH = [[0, -1, 0], [1, 0, 0], [-1, 0, 0], [0, 0, 1], [0, 0, -1], [0, 1, 0]];
  for (const [dx, dy, dz] of NEIGH) {
    const ref = bot.blockAt(target.offset(dx, dy, dz));
    if (!ref || ref.boundingBox !== 'block' || ref.name === 'air') continue;
    try {
      if (ref.position.distanceTo(bot.entity.position) > 4) {
        await bot.pathfinder.goto(new goals.GoalNear(ref.position.x, ref.position.y, ref.position.z, 3));
      }
      await bot.placeBlock(ref, vec(-dx, -dy, -dz)); // face points ref -> target
      return blockIs(bot, target, null); // VERIFY the block is actually there now
    } catch (_) { /* try the next neighbor face */ }
  }
  return false;
}

// ── lightArea: mob-proof the area — place torches so nothing spawns nearby ─────
// Mobs spawn at light level 0 (1.18+). A torch lights ~7 blocks, so a torch every
// ~10 blocks keeps the area lit. She places on dark ground spots around her.
async function lightArea(bot, radius = 8) {
  if (!perceive(bot).valid) return false;
  const torch = (bot.inventory.items() || []).find((i) => i.name.includes('torch'));
  if (!torch) return false;
  const base = bot.entity.position.floored();
  let placed = 0;
  // a coarse grid of candidate spots ~10 apart within radius
  const step = 5;
  for (let dx = -radius; dx <= radius && placed < 6; dx += step) {
    for (let dz = -radius; dz <= radius && placed < 6; dz += step) {
      const ground = bot.blockAt(base.offset(dx, -1, dz));
      const spot = bot.blockAt(base.offset(dx, 0, dz));
      if (!ground || ground.boundingBox !== 'block' || !spot || !REPLACEABLE.test(spot.name)) continue;
      const dark = (spot.light != null ? spot.light : 0) < 8;
      if (!dark) continue;
      try {
        setupMovements(bot);
        await bot.pathfinder.goto(new goals.GoalNear(base.x + dx, base.y, base.z + dz, 2));
        const stillTorch = (bot.inventory.items() || []).find((i) => i.name.includes('torch'));
        if (!stillTorch) break;
        await bot.equip(stillTorch, 'hand');
        await bot.placeBlock(ground, vec(0, 1, 0));
        placed++;
      } catch (_) {}
    }
  }
  if (placed) console.log(`[MC]   lightArea: placed ${placed} torches`);
  return placed > 0;
}

// ── place_torch: light up the area if she has torches ────────────────────────
async function placeTorch(bot) {
  if (!perceive(bot).valid) return false;
  const torch = (bot.inventory.items() || []).find((i) => i.name.includes('torch'));
  if (!torch) return false;
  const ref = bot.blockAt(bot.entity.position.offset(0, -1, 0));
  if (!ref || ref.name === 'air') return false;
  try {
    await bot.equip(torch, 'hand');
    await bot.placeBlock(ref, vec(0, 1, 0));
    return true;
  } catch (_) { return false; }
}

// ── craft: the tech-tree bootstrap (logs → planks → sticks + table → tools) ───
// A real player can't mine stone with bare hands, so before she "gathers stone"
// she must craft a pickaxe. This owns the whole chain: turn logs into planks,
// planks into sticks + a crafting table, then craft the requested tool at it.
// name = 'wooden_pickaxe' | 'wooden_sword' | 'stone_pickaxe' | 'stone_sword' | ...
async function craft(bot, name = 'wooden_pickaxe') {
  const data = mcData(bot);
  const item = data.itemsByName[name];
  if (!item) { console.log(`[MC]   craft: unknown item ${name}`); return false; }

  // 1. Ensure a stock of planks (needed for sticks, the table, and wooden tools).
  while (invCount(bot, /planks/) < 6) {
    if (!(await craftPlanks(bot))) break;
  }
  // 2. Sticks (every tool needs 2).
  if (invCount(bot, /^stick$/) < 2) await craftInInventory(bot, 'stick');
  // 3. A crafting table within reach — craft one and place it if there's none.
  const table = await ensureCraftingTable(bot);
  if (!table) { console.log('[MC]   craft: no crafting table available'); return false; }
  // 4. The tool itself. recipesFor only returns it if she actually has the mats
  //    (e.g. cobblestone for stone tools), so a null result = "not enough yet".
  const recipe = bot.recipesFor(item.id, null, 1, table)[0];
  if (!recipe) { console.log(`[MC]   craft: can't make ${name} yet (missing materials)`); return false; }
  const before = countOf(bot, name);
  try {
    await bot.craft(recipe, 1, table);
  } catch (e) {
    console.log(`[MC]   craft: ${name} failed — ${e.message}`);
    return false;
  }
  // VERIFY it actually appeared (bot.craft can silently no-op on desync/full inv).
  if (countOf(bot, name) > before) { console.log(`[MC]   craft: made ${name}`); return true; }
  console.log(`[MC]   craft: ${name} — craft ran but item didn't appear`);
  return false;
}

// turn one log stack's worth into planks (matches the wood type she's holding)
async function craftPlanks(bot) {
  const data = mcData(bot);
  const log = (bot.inventory.items() || []).find((i) => /_log$|^log$/.test(i.name));
  if (!log) return false;
  const plankName = log.name.replace('stripped_', '').replace(/_log$/, '_planks').replace(/^log$/, 'oak_planks');
  const plankItem = data.itemsByName[plankName] || data.itemsByName.oak_planks;
  const recipe = bot.recipesFor(plankItem.id, null, 1, null)[0];
  if (!recipe) return false;
  try { await bot.craft(recipe, 1, null); return true; } catch (_) { return false; }
}

// ── smartCraft: resolve prerequisites, then craft ────────────────────────────
// Instead of failing when a material is missing, ask the recipe resolver what to do
// NEXT (mine stone, smelt iron, craft planks, or finally craft the target) and do
// that one step. The loop re-runs it until the target is made — no more looping on a
// craft she can't afford (owner: "look up how the missing items get/crafted").
async function smartCraft(bot, target) {
  const { nextStepFor } = require('./recipe');
  const step = nextStepFor(bot, target);
  if (!step) { console.log(`[MC]   craft ${target}: can't resolve a path — trying direct`); return craft(bot, target); }
  if (step.do !== 'craft' || step.arg !== target) {
    console.log(`[MC]   craft ${target}: need ${step.arg} first → ${step.do}`);
  }
  switch (step.do) {
    case 'craft':
      if (step.arg === 'bed') return require('./verbs').craftBed(bot); // color-generic bed
      return craft(bot, step.arg);
    case 'gather': return gather(bot, step.arg, 8);
    case 'mine': return mineDeeper(bot, step.arg);
    case 'smelt': return smelt(bot, step.arg, 8);
    case 'wool': return require('./verbs').getWool(bot); // kill sheep for bed wool
    default: return craft(bot, target);
  }
}

// craft a table-free recipe (planks, sticks, crafting_table) from inventory
async function craftInInventory(bot, name, times = 1) {
  const data = mcData(bot);
  const it = data.itemsByName[name];
  if (!it) return false;
  const recipe = bot.recipesFor(it.id, null, 1, null)[0];
  if (!recipe) return false;
  try { await bot.craft(recipe, times, null); return true; } catch (_) { return false; }
}

// Get a crafting table to use — reusing one she already knows about instead of
// littering the world with new ones (the "7 tables in 50 blocks" bug). Order:
// (1) one in immediate reach, (2) walk back to a REMEMBERED table, (3) craft+place
// a new one and remember it.
async function ensureCraftingTable(bot) {
  const wm = bot._wm;
  // 1. already right next to one
  let table = bot.findBlock({ matching: (b) => b && b.name === 'crafting_table', maxDistance: 5 });
  if (table) { if (wm) wm.noteLandmark(table.position, 'crafting_table'); return table; }

  // 2. a table she placed earlier and remembers — go back to it
  if (wm && bot.entity) {
    const known = wm.nearest('landmarks', bot.entity.position, (e) => e.tag === 'crafting_table');
    if (known) {
      const me = bot.entity.position;
      const d = Math.hypot(known.pos.x - me.x, known.pos.y - me.y, known.pos.z - me.z);
      if (d < 48) {
        try {
          setupMovements(bot);
          await bot.pathfinder.goto(new goals.GoalNear(known.pos.x, known.pos.y, known.pos.z, 2));
          table = bot.findBlock({ matching: (b) => b && b.name === 'crafting_table', maxDistance: 5 });
          if (table) return table; // reused the old one — no new table placed
        } catch (_) { /* couldn't get there; fall through to make one */ }
      }
    }
  }

  // 3. none reachable → craft one, place it, and REMEMBER it
  let held = (bot.inventory.items() || []).find((i) => i.name === 'crafting_table');
  if (!held) {
    await craftInInventory(bot, 'crafting_table');
    held = (bot.inventory.items() || []).find((i) => i.name === 'crafting_table');
  }
  if (!held) return null;
  if (!(await placeBlockBeside(bot, held))) return null;
  table = bot.findBlock({ matching: (b) => b && b.name === 'crafting_table', maxDistance: 5 });
  if (table && wm) wm.noteLandmark(table.position, 'crafting_table');
  return table;
}

// blocks a placed block can occupy (you can place INTO grass/ferns/snow — they're
// replaceable). Treating only literal 'air' as free was why she couldn't set down a
// crafting table in tall grass → craft failed → she hoarded wood forever.
const REPLACEABLE = /^(air|cave_air|void_air|short_grass|tall_grass|grass|fern|large_fern|dead_bush|seagrass|tall_seagrass|snow|vine|hanging_roots|light)$/;

// place a block in an open spot on the ground next to her (tries all 4 sides)
async function placeBlockBeside(bot, item) {
  if (!perceive(bot).valid) return false; // desynced position = null.floored() throws
  try { await bot.equip(item, 'hand'); } catch (_) { return false; }
  const base = bot.entity.position.floored();
  for (const [dx, dz] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
    const ground = bot.blockAt(base.offset(dx, -1, dz));
    const space = bot.blockAt(base.offset(dx, 0, dz));
    if (space && occupiesSelf(bot, space.position)) continue; // don't place onto herself
    if (ground && ground.boundingBox === 'block' && space && REPLACEABLE.test(space.name)) {
      try {
        await bot.lookAt(ground.position.offset(0.5, 1, 0.5));
        await bot.placeBlock(ground, vec(0, 1, 0));
        return true;
      } catch (_) { /* try the next spot */ }
    }
  }
  return false;
}

// ── smelt: cook/smelt an item in a furnace (raw_iron→ingot, raw meat→cooked) ───
// The Iron Age gate: iron ore drops raw_iron which must be SMELTED. Also cooks food.
// mineflayer furnace API — put fuel + input, poll the output slot, take it.
// lava bucket first — ONE bucket smelts 100 items (owner ask, round 18)
const FUEL_ORDER = ['lava_bucket', 'coal', 'charcoal', 'coal_block', 'blaze_rod', 'dried_kelp_block'];
function pickFuel(bot) {
  const items = bot.inventory.items() || [];
  for (const f of FUEL_ORDER) { const it = items.find((i) => i.name === f); if (it) return it; }
  // fall back to any wood-ish fuel (planks/log/stick)
  return items.find((i) => /planks|_log$|^log$|stick/.test(i.name)) || null;
}

async function ensureFurnace(bot) {
  const wm = bot._wm;
  let f = bot.findBlock({ matching: (b) => b && b.name === 'furnace', maxDistance: 5 });
  if (f) { if (wm) wm.noteLandmark(f.position, 'furnace'); return f; }
  if (wm && bot.entity) {
    const known = wm.nearest('landmarks', bot.entity.position, (e) => e.tag === 'furnace');
    if (known) {
      const me = bot.entity.position;
      const d = Math.hypot(known.pos.x - me.x, known.pos.y - me.y, known.pos.z - me.z);
      if (d < 48) {
        try {
          setupMovements(bot);
          await bot.pathfinder.goto(new goals.GoalNear(known.pos.x, known.pos.y, known.pos.z, 2));
          f = bot.findBlock({ matching: (b) => b && b.name === 'furnace', maxDistance: 5 });
          if (f) return f;
        } catch (_) {}
      }
    }
  }
  // craft one (needs 8 cobblestone + a table, which craft() ensures)
  let held = (bot.inventory.items() || []).find((i) => i.name === 'furnace');
  if (!held) {
    if (invCount(bot, /cobblestone/) < 8) return null;
    await craft(bot, 'furnace');
    held = (bot.inventory.items() || []).find((i) => i.name === 'furnace');
  }
  if (!held) return null;
  if (!(await placeBlockBeside(bot, held))) return null;
  f = bot.findBlock({ matching: (b) => b && b.name === 'furnace', maxDistance: 5 });
  if (f && wm) wm.noteLandmark(f.position, 'furnace');
  return f;
}

async function smelt(bot, inputName, count = 8) {
  const data = mcData(bot);
  const inputDef = data.itemsByName[inputName];
  if (!inputDef) { console.log(`[MC]   smelt: unknown item ${inputName}`); return false; }
  const have = invCount(bot, new RegExp(`^${inputName}$`));
  if (have < 1) { console.log(`[MC]   smelt: nothing to smelt (${inputName})`); return false; }
  const furnaceBlock = await ensureFurnace(bot);
  if (!furnaceBlock) { console.log('[MC]   smelt: no furnace available'); return false; }

  let furnace;
  try { furnace = await bot.openFurnace(furnaceBlock); } catch (e) { console.log('[MC]   smelt: open failed —', e.message); return false; }
  try {
    const fuel = pickFuel(bot);
    if (!fuel) { furnace.close(); console.log('[MC]   smelt: no fuel'); return false; }
    const need = Math.min(count, have);
    const isCoal = fuel.name === 'coal' || fuel.name === 'charcoal';
    // what this input smelts INTO (invert recipe.SMELT_FROM) so the result is
    // verifiable by name — "takeOutput ran" is not "the ingot is in her pack".
    const { SMELT_FROM } = require('./recipe');
    const outName = Object.keys(SMELT_FROM).find((k) => SMELT_FROM[k] === inputName) || null;
    const outBefore = outName ? countOf(bot, outName) : 0;
    const fuelCount = fuel.name === 'lava_bucket' ? 1 // one bucket = 100 smelts
      : isCoal ? Math.max(1, Math.ceil(need / 8)) : Math.min(fuel.count, need);
    await furnace.putFuel(fuel.type, null, fuelCount);
    await furnace.putInput(inputDef.id, null, need);
    const deadline = Date.now() + need * 11000 + 10000; // ~10s per item + margin
    const g0 = ctlGen(bot);
    let taken = 0;
    while (Date.now() < deadline) {
      if (interrupted(bot, g0)) break; // creeper primed mid-smelt: LEAVE the furnace
      const out = furnace.outputItem();
      if (out && out.count > 0) {
        try { await furnace.takeOutput(); taken += out.count; } catch (_) {}
        if (taken >= need) break;
      }
      await sleep(1200);
    }
    furnace.close();
    // VERIFY by the output item itself when we know its name; else trust the counter.
    const gained = outName ? countOf(bot, outName) > outBefore : taken > 0;
    console.log(`[MC]   smelt: ${inputName} → ${taken} smelted${gained ? '' : ' (UNVERIFIED — output missing)'}`);
    return gained;
  } catch (e) {
    try { furnace.close(); } catch (_) {}
    console.log('[MC]   smelt: failed —', e.message);
    return false;
  }
}

// ── mineToY: descend safely to a target depth, then branch-mine for ore ───────
async function mineToY(bot, targetY) {
  setupMovements(bot);
  let guard = 60;
  while (bot.entity && bot.entity.position && bot.entity.position.y > targetY + 1 && guard-- > 0) {
    const before = Math.floor(bot.entity.position.y);
    await mineStaircase(bot, 4);
    if (Math.floor(bot.entity.position.y) >= before) break; // not descending → stop
  }
  return branchMine(bot, 14);
}

// ── branchMine: dig a 1x2 tunnel forward, grabbing exposed ore, torching, no lava ─
async function branchMine(bot, length = 14) {
  setupMovements(bot);
  if (!bot.entity || !bot.entity.position) return false;
  // field triage first: a full pack mid-tunnel means dropped diamonds — make room now
  if ((bot.inventory.items() || []).length >= 32) { try { await tidyInventory(bot); } catch (_) {} }
  const yaw = bot.entity.yaw;
  const fx = -Math.sin(yaw); const fz = -Math.cos(yaw);
  const dir = Math.abs(fx) > Math.abs(fz) ? { x: Math.sign(fx) || 1, z: 0 } : { x: 0, z: Math.sign(fz) || 1 };
  // NULL = UNKNOWN = UNSAFE: on a lagged chunk blockAt returns null, and the old
  // check read that as "not lava". Fail CLOSED on the safety guard.
  const isFluid = (b) => !b || (b.name === 'lava' || b.name === 'flowing_lava' || b.name === 'water');
  let dug = 0;
  const g0 = ctlGen(bot);
  for (let i = 0; i < length; i++) {
    if (interrupted(bot, g0)) break;
    const base = bot.entity.position.floored();
    const ahead = base.offset(dir.x, 0, dir.z);
    const aheadHead = base.offset(dir.x, 1, dir.z);
    // fluid safety (DeepSeek): check the blocks we'd breach AND what sits just beyond
    // and above them — digging can expose/pour lava from behind or overhead.
    const guardCells = [
      ahead, aheadHead, base.offset(dir.x, -1, dir.z),
      base.offset(dir.x * 2, 0, dir.z * 2), base.offset(dir.x * 2, 1, dir.z * 2), // beyond
      ahead.offset(0, 1, 0),                                                       // above ahead
    ];
    if (guardCells.some((tp) => isFluid(bot.blockAt(tp)))) {
      console.log('[MC]   branch: lava/water near — backing off'); await collectDrops(bot, 6); return dug > 0;
    }
    for (const tp of [aheadHead, ahead]) {
      const b = bot.blockAt(tp);
      if (b && b.name !== 'air' && b.boundingBox === 'block' && b.name !== 'bedrock') {
        try { await equipToolFor(bot, b); await bot.dig(b); dug++; } catch (_) {}
      }
    }
    // grab exposed ore within reach — but ONLY ore her pickaxe can actually drop,
    // worth her time (no copper treadmill), and visible (no x-ray sealed drops)
    const ore = bot.findBlock({
      matching: (b) => b && /_ore$/.test(b.name || '') && canMineOre(bot, b.name) && oreWorth(bot, b.name),
      maxDistance: 4,
      useExtraInfo: (b) => { try { return bot.canSeeBlock(b); } catch (_) { return true; } },
    });
    if (ore) {
      try {
        const drop = expectedDrop(ore.name);
        const before = drop ? countOf(bot, drop) : totalItems(bot);
        await equipToolFor(bot, ore); await digPatient(bot, ore); await collectNearestDrop(bot);
        // verify the ore drop was actually collected (esp. valuable ore)
        const after = drop ? countOf(bot, drop) : totalItems(bot);
        if (after <= before) await collectDrops(bot, 8);
        dug++;
      } catch (_) {}
    }
    try { await bot.pathfinder.goto(new goals.GoalNear(base.x + dir.x, base.y, base.z + dir.z, 0)); } catch (_) {}
    if (i % 10 === 0) { try { await placeTorch(bot); } catch (_) {} } // light≥7 stops spawns (~every 10)
    await collectNearestDrop(bot, 4);
  }
  await collectDrops(bot, 8);
  return dug > 0;
}

// ── mineStaircase: descend by carving a 1-wide staircase, NEVER straight down ──
// Digging the block under your own feet is how players fall into lava/caves and
// die. A real player cuts a staircase: break the blocks ahead-and-below, step down
// into them, repeat. She refuses to break the block directly beneath her, stops at
// a drop (and remembers it as a possible cave), and won't dig next to lava.
async function mineStaircase(bot, depth = 8) {
  if (!bot.entity || !bot.entity.position) return false;
  setupMovements(bot);
  const yaw = bot.entity.yaw;
  const fx = -Math.sin(yaw); const fz = -Math.cos(yaw);
  const dir = Math.abs(fx) > Math.abs(fz)
    ? { x: Math.sign(fx) || 1, z: 0 }
    : { x: 0, z: Math.sign(fz) || 1 };

  const isLavaNear = (p) => {
    for (const [dx, dy, dz] of [[1, 0, 0], [-1, 0, 0], [0, 0, 1], [0, 0, -1], [0, 1, 0]]) {
      const b = bot.blockAt(p.offset(dx, dy, dz));
      if (!b) return true; // unloaded chunk = unknown = don't dig into it
      if (b.name === 'lava' || b.name === 'flowing_lava') return true;
    }
    return false;
  };

  let dug = 0;
  const g0 = ctlGen(bot);
  for (let i = 0; i < depth; i++) {
    if (interrupted(bot, g0)) break;
    const base = bot.entity.position.floored();
    // the block she'd step onto next — if it's a void/air, that's a drop/cave: stop.
    const floorAhead = bot.blockAt(base.offset(dir.x, -2, dir.z));
    if (floorAhead && floorAhead.name === 'air') {
      if (bot._wm) bot._wm.noteCave(base.offset(dir.x, -1, dir.z));
      console.log('[MC]   staircase: drop/cave ahead — stopping descent');
      break;
    }
    const targets = [
      base.offset(dir.x, 1, dir.z),   // head-height ahead
      base.offset(dir.x, 0, dir.z),   // foot-height ahead
      base.offset(dir.x, -1, dir.z),  // the step down
    ];
    let broke = false;
    for (const tp of targets) {
      const b = bot.blockAt(tp);
      if (!b || b.name === 'air' || b.boundingBox !== 'block' || b.name === 'bedrock') continue;
      if (isLavaNear(tp)) { console.log('[MC]   staircase: lava adjacent — stopping'); return dug > 0; }
      try { await equipToolFor(bot, b); await bot.dig(b); dug++; broke = true; } catch (_) {}
    }
    try { await bot.pathfinder.goto(new goals.GoalNear(base.x + dir.x, base.y - 1, base.z + dir.z, 0)); } catch (_) {}
    if (broke) await collectNearestDrop(bot, 4);
    if (!broke) break;
    // torch-as-you-go: the descent is where she gets ambushed from behind — light it
    if (i > 0 && i % 8 === 0) { try { await placeTorch(bot); } catch (_) {} }
  }
  await collectDrops(bot, 8);
  return dug > 0;
}

// ── mineDeeper: get ore — grab exposed ore, else descend to the right Y + branch mine ──
async function mineDeeper(bot, oreHint) {
  if (!bot.entity || !bot.entity.position) return false;
  setupMovements(bot);
  // NON-ORE materials (cobble for a furnace, sand, gravel, dirt) are gathered from
  // the surface, not ore-hunted — 'mine stone' used to chase coal_ore or descend to
  // Y=-10 and never bring cobble back (audit 2026-07-13).
  if (oreHint && /^(stone|cobblestone|deepslate|sand|gravel|dirt)$/.test(oreHint)) {
    const kw = oreHint === 'cobblestone' ? 'stone' : oreHint;
    return gather(bot, kw, 8);
  }
  // exposed ore in reach she can actually mine (right pickaxe tier)? go get it.
  // WORTH-gated: the ungated version kept her at Y30 vacuuming endless exposed
  // copper while "mining diamonds" — the copper literally blocked the descent
  // (round 18h: "walks ON the iron to skip it and mine more copper").
  const wantKw = miningTargetKeyword(oreHint);
  const mineable = (b) => b && /_ore$/.test(b.name || '') && canMineOre(bot, b.name);
  if (wantKw) {
    const targetOre = bot.findBlock({
      matching: (b) => mineable(b) && b.name.includes(wantKw) && !gatherFailureActive(bot, b.position),
      maxDistance: 24,
    });
    // Stay on assignment. The generic fallback used to turn "mine diamonds" into
    // a 64-block coal/iron chase at Y70 and could prevent descent indefinitely.
    if (targetOre) {
      return gather(bot, wantKw, 4, 32,
        (b) => canMineOre(bot, b.name) && b.name.includes(wantKw));
    }
  } else {
    const opportunisticOre = bot.findBlock({
      matching: (b) => mineable(b) && oreWorth(bot, b.name) && !gatherFailureActive(bot, b.position),
      maxDistance: 24,
    });
    if (opportunisticOre) {
      return gather(bot, '_ore', 4, 32,
        (b) => canMineOre(bot, b.name) && oreWorth(bot, b.name));
    }
  }
  // SPATIAL MEMORY: she may have WALKED PAST this ore earlier — go back to the
  // remembered spot before blind-descending (the world model finally gets read).
  if (wantKw && bot._wm) {
    const known = bot._wm.nearest('resources', bot.entity.position,
      (e) => (e.tag || '').includes(wantKw));
    if (known) {
      const d = Math.hypot(known.pos.x - bot.entity.position.x, known.pos.z - bot.entity.position.z);
      if (d > 6 && d < 64) {
        console.log(`[MC]   mine: remembered ${known.tag} ~${Math.round(d)}m away — heading back to it`);
        await gotoTimed(bot, new goals.GoalNear(known.pos.x, known.pos.y, known.pos.z, 3), 20000);
        const there = bot.findBlock({ matching: (b) => mineable(b) && b.name.includes(wantKw), maxDistance: 8 });
        if (there) return gather(bot, wantKw, 4, 16,
          (b) => canMineOre(bot, b.name) && b.name.includes(wantKw));
      }
    }
  }
  // none visible → go to the depth this ore lives at, then branch-mine
  const targetY = ORE_Y[oreHint] != null ? ORE_Y[oreHint] : (ORE_Y[`${oreHint}_ore`] != null ? ORE_Y[`${oreHint}_ore`] : -10);
  if (bot.entity.position.y > targetY + 3) return mineToY(bot, targetY);
  return branchMine(bot, 14);
}

function miningTargetKeyword(oreHint) {
  return oreHint ? String(oreHint).replace(/_ore$/, '') : null;
}

// ── explore: strategic travel — commit to a heading, read terrain, seek frontier ─
// Replaces the old random walk that kept her in a box. She scans the terrain, judges
// it (good/ok/poor/water), and the explorer picks a committed heading toward
// unexplored ground — pushing far to escape water, settling toward good biomes.
async function explore(bot) {
  setupMovements(bot);
  if (!bot.entity || !bot.entity.position) return false;
  const me = bot.entity.position;
  const { verdict } = evaluateSurroundings(scanTerrain(bot));
  if (!bot._explorer) bot._explorer = createExplorer();

  // Island-locked? (kept retreating from water) → cross by BOAT like a person,
  // toward the least-visited frontier, instead of swim-thrashing at the shoreline.
  if (verdict === 'water' && bot._explorer.waterRetreats >= 3) {
    const verbs = require('./verbs');
    if (!verbs.boatItem(bot)) {
      // craft the boat of whatever wood SHE HAS — 'oak_boat' hardcoded on a birch
      // island looped chopping birch forever (species-exact recipe, audit).
      const plankStack = (bot.inventory.items() || []).find((i) =>
        /_planks$/.test(i.name) && invCount(bot, new RegExp(`^${i.name}$`)) >= 5);
      if (plankStack) {
        const species = plankStack.name.replace('_planks', '');
        console.log(`[MC]   explore: island-locked — building a ${species} boat`);
        try { await smartCraft(bot, `${species}_boat`); } catch (_) {}
      }
    }
    if (verbs.boatItem(bot)) {
      // aim at the least-visited frontier — explorer.heading points BACK at the
      // island she's stuck on (it was just set by the water-retreat logic).
      const { frontierHeading } = require('./explore');
      const h = frontierHeading(bot._explorer.visited, me, 150);
      const far = { x: Math.round(me.x + Math.cos(h) * 150), z: Math.round(me.z + Math.sin(h) * 150) };
      console.log('[MC]   explore: paddling across the water');
      try { if (await verbs.crossWater(bot, far)) return true; } catch (_) {}
    }
  }

  const { dest, hop } = bot._explorer.plan(me, verdict);
  console.log(`[MC]   explore: terrain=${verdict}, heading to (${dest.x},${dest.z}) ~${hop}m`);
  await gotoTimed(bot, new goals.GoalXZ(dest.x, dest.z), 15000); // capped so ocean swims can't freeze her

  // Opportunism — the "while I'm here" layer real players run constantly (VPT).
  // Zero-detour grabs at the leg boundary, self-cooldown so it never derails travel.
  try { await opportunisticGrab(bot); } catch (_) {}
  // Human beat: sometimes stop and take in the view at a leg boundary (calm only).
  const { shouldPause } = require('./motor');
  const calm = !bot.nearestEntity((e) => e && e.position && HOSTILE_MOBS.has((e.name || '').toLowerCase())
    && e.position.distanceTo(bot.entity.position) < 16);
  if (bot._motor && shouldPause(calm)) { try { await bot._motor.curiosityPause(); } catch (_) {} }
  return true;
}

// One quick "passing by" grab: berries/sugarcane/pumpkins on the route, or a surface
// ore her pick can mine. Capped to one grab per 45s so it seasons travel, not stalls it.
async function opportunisticGrab(bot) {
  const now = Date.now();
  if (bot._lastOppAt && now - bot._lastOppAt < 45000) return false;
  if (!bot.entity || !bot.entity.position) return false;
  // BED RECLAIM — she kept abandoning her bed when she moved on (round 17). A
  // wandering player passing their own bed with none in the pack scoops it up.
  const hasBedItem = (bot.inventory.items() || []).some((i) => i.name.endsWith('_bed'));
  if (!hasBedItem) {
    const bedBlock = bot.findBlock({ matching: (b) => b && /_bed$/.test(b.name || ''), maxDistance: 12 });
    // ...but the HOME bed stays home — only reclaim field beds
    const base = bot._wm && bot._wm.getBase();
    const isHomeBed = bedBlock && base && base.committed
      && Math.hypot(base.x - bedBlock.position.x, base.z - bedBlock.position.z) <= 16;
    if (bedBlock && !isHomeBed) {
      bot._lastOppAt = now;
      console.log('[MC]   (taking my bed with me)');
      try {
        await gotoTimed(bot, new goals.GoalNear(bedBlock.position.x, bedBlock.position.y, bedBlock.position.z, 2), 8000);
        await bot.dig(bot.blockAt(bedBlock.position));
        await collectNearestDrop(bot, 6);
        if (bot._wm) { bot._wm.removeLandmark(bedBlock.position); bot._wm.save(); } // no ghost bed
        return true;
      } catch (_) { return false; }
    }
  }
  // plants first (free food/farm seeds — the canon reflexive pickups)
  const plant = bot.findBlock({
    matching: (b) => b && /^(sugar_cane|pumpkin|melon)$/.test(b.name),
    maxDistance: 10,
  });
  if (plant) {
    bot._lastOppAt = now;
    console.log(`[MC]   (passing by: grabbing ${plant.name})`);
    try {
      await gotoTimed(bot, new goals.GoalNear(plant.position.x, plant.position.y, plant.position.z, 1), 8000);
      await bot.dig(bot.blockAt(plant.position));
      await collectNearestDrop(bot, 6);
      return true;
    } catch (_) { return false; }
  }
  const berry = bot.findBlock({ matching: (b) => b && b.name === 'sweet_berry_bush', maxDistance: 10 });
  if (berry) {
    bot._lastOppAt = now;
    console.log('[MC]   (passing by: picking berries)');
    try {
      await gotoTimed(bot, new goals.GoalNear(berry.position.x, berry.position.y, berry.position.z, 1), 8000);
      await bot.activateBlock(bot.blockAt(berry.position));
      await collectNearestDrop(bot, 5);
      return true;
    } catch (_) { return false; }
  }
  // LAVA FUEL on sight — an empty bucket + a lava pool + a coal shortage = the
  // 100-smelt jackpot. Same-level-or-below lava only (fillLavaBucket enforces it).
  if (invCount(bot, /^(coal|charcoal)$/) < 4) {
    const verbs = require('./verbs');
    try {
      if (await verbs.fillLavaBucket(bot)) { bot._lastOppAt = now; return true; }
    } catch (_) {}
  }
  // ore on sight — mine-on-sight is how players bank ore. ANY ore her pick can
  // drop, including deepslate variants (the old ^iron_ore$ whitelist made her walk
  // straight past deepslate iron all cave long — round 17c).
  if (currentPickTier(bot) >= 1) {
    const ore = bot.findBlock({
      matching: (b) => b && /_ore$/.test(b.name || '') && canMineOre(bot, b.name) && oreWorth(bot, b.name),
      maxDistance: 12,
      useExtraInfo: (b) => { try { return bot.canSeeBlock(b); } catch (_) { return true; } },
    });
    if (ore) {
      bot._lastOppAt = now;
      console.log(`[MC]   (passing by: ${ore.name})`);
      const kw = ore.name.replace(/^deepslate_/, '').replace('_ore', '');
      try { return await gather(bot, kw, 3, 16); } catch (_) { return false; }
    }
  }
  return false;
}

// Count terrain-defining blocks around her for the value evaluation.
function scanTerrain(bot) {
  const cnt = (matcher, r, c) => {
    try { return bot.findBlocks({ matching: matcher, maxDistance: r, count: c }).length; } catch (_) { return 0; }
  };
  return {
    water: cnt((b) => b && b.name === 'water', 20, 60),
    logs: cnt((b) => b && /_log$/.test(b.name || ''), 24, 20),
    grass: cnt((b) => b && b.name === 'grass_block', 16, 40),
    sand: cnt((b) => b && b.name === 'sand', 16, 40),
    // evaluateSurroundings sums land = grass+dirt+stone+sand — omitting dirt/stone
    // made dry riverbanks read as 'water' (land=0) and fired false boat escapes.
    dirt: cnt((b) => b && /^(dirt|coarse_dirt|podzol)$/.test(b.name || ''), 16, 40),
    stone: cnt((b) => b && /^(stone|andesite|diorite|granite|deepslate)$/.test(b.name || ''), 16, 40),
  };
}

// ── helpers ──────────────────────────────────────────────────────────────────
async function equipBestWeapon(bot) {
  const items = bot.inventory.items() || [];
  const order = ['netherite_sword', 'diamond_sword', 'iron_sword', 'stone_sword', 'wooden_sword', 'axe'];
  for (const w of order) {
    const it = items.find((i) => i.name.includes(w));
    if (it) { try { await bot.equip(it, 'hand'); return; } catch (_) {} }
  }
}

// remaining durability as 0..1 (non-tools read as "full")
function toolHealthFrac(maxDur, used) {
  if (!maxDur || maxDur <= 0) return 1;
  return Math.max(0, (maxDur - (used || 0)) / maxDur);
}
function itemHealthFrac(item) {
  if (!item || !item.maxDurability) return 1;
  return toolHealthFrac(item.maxDurability, item.durabilityUsed || 0);
}

// Equip the RIGHT tool for a block before digging it. This is not just speed —
// mining stone/ore bare-handed drops NOTHING, so gathering silently yielded nothing
// before. Prefers the best tier that isn't about to break (>5% durability).
const TIERS = ['netherite', 'diamond', 'iron', 'stone', 'golden', 'wooden'];
async function equipToolFor(bot, block) {
  const name = (block && block.name) || '';
  let tool = null;
  if (/log|planks|wood|fence|_door|crafting_table|chest|bookshelf/.test(name)) tool = 'axe';
  else if (/_ore$|stone|cobble|deepslate|furnace|andesite|granite|diorite|tuff|obsidian|concrete/.test(name)) tool = 'pickaxe';
  else if (/dirt|sand|gravel|grass_block|clay|soul_|podzol|mud|snow/.test(name)) tool = 'shovel';
  if (!tool) return;
  const items = bot.inventory.items() || [];
  const matches = TIERS.map((t) => items.find((i) => i.name === `${t}_${tool}`)).filter(Boolean); // best-tier first
  if (!matches.length) return;
  const chosen = matches.find((it) => itemHealthFrac(it) > 0.05) || matches[0]; // skip about-to-break if we can
  try { await bot.equip(chosen, 'hand'); } catch (_) {}
}

// Keep a working tool: if her ONLY pickaxe/sword is badly worn and she can build a
// replacement, craft a spare so she's never left tool-less mid-task.
async function maintainTools(bot) {
  for (const tool of ['pickaxe', 'sword']) {
    const owned = (bot.inventory.items() || []).filter((i) => i.name.endsWith(`_${tool}`));
    if (!owned.length) continue; // none → the tech-tree projects handle it
    const best = owned.slice().sort((a, b) => itemHealthFrac(b) - itemHealthFrac(a))[0];
    if (owned.length === 1 && itemHealthFrac(best) < 0.25) {
      const tier = best.name.split('_')[0];
      const target = `${tier}_${tool}`;
      console.log(`[MC]   tools: only ${target} left at ${Math.round(itemHealthFrac(best) * 100)}% — crafting a spare`);
      try { await craft(bot, target); } catch (_) {}
    }
  }
  // SHIELD SAFETY NET — the project path can get parked/skipped (round 17: iron_tools
  // parked underground and the shield step never ran). If the materials are on hand,
  // make the shield regardless of what project is active. One ingot buys defense.
  const items = bot.inventory.items() || [];
  const hasShield = items.some((i) => i.name === 'shield');
  if (!hasShield && Date.now() - (bot._lastShieldTryAt || 0) > 90_000) {
    const iron = items.filter((i) => i.name === 'iron_ingot').reduce((n, i) => n + i.count, 0);
    if (iron >= 1 && woodUnits(bot) >= 6) {
      bot._lastShieldTryAt = Date.now();
      console.log('[MC]   loadout: materials for a shield on hand — making one');
      try { await smartCraft(bot, 'shield'); } catch (_) {}
    }
  }
}
// ── storage: stash surplus in HER OWN chest — never someone else's ─────────────
// "Nearest chest within 5" made every chest in the world her wardrobe: she was
// caught organizing her surplus INTO a dungeon chest (round 17e). A found chest
// only counts as storage if SHE placed it (a 'chest' landmark sits on it).
function isOwnStorage(wm, pos) {
  if (!wm) return false;
  const own = wm.nearest('landmarks', pos, (e) => e.tag === 'chest');
  return !!(own && Math.hypot(own.pos.x - pos.x, own.pos.y - pos.y, own.pos.z - pos.z) < 2);
}

async function ensureChest(bot) {
  const wm = bot._wm;
  let c = bot.findBlock({ matching: (b) => b && b.name === 'chest', maxDistance: 5 });
  if (c && !isOwnStorage(wm, c.position)) c = null; // dungeon/player chest ≠ her storage
  if (c) { if (wm) wm.noteLandmark(c.position, 'chest'); return c; }
  if (wm && bot.entity) {
    const known = wm.nearest('landmarks', bot.entity.position, (e) => e.tag === 'chest');
    if (known) {
      const me = bot.entity.position;
      const d = Math.hypot(known.pos.x - me.x, known.pos.y - me.y, known.pos.z - me.z);
      if (d < 48) {
        try {
          setupMovements(bot);
          await bot.pathfinder.goto(new goals.GoalNear(known.pos.x, known.pos.y, known.pos.z, 2));
          c = bot.findBlock({ matching: (b) => b && b.name === 'chest', maxDistance: 5 });
          if (c) return c;
        } catch (_) {}
      }
    }
  }
  let held = (bot.inventory.items() || []).find((i) => i.name === 'chest');
  if (!held) {
    if (invCount(bot, /planks/) < 8) return null;
    await craft(bot, 'chest');
    held = (bot.inventory.items() || []).find((i) => i.name === 'chest');
  }
  if (!held) return null;
  if (!(await placeBlockBeside(bot, held))) return null;
  c = bot.findBlock({ matching: (b) => b && b.name === 'chest', maxDistance: 5 });
  if (c && wm) wm.noteLandmark(c.position, 'chest');
  return c;
}

// How much of an item's TOTAL count to deposit (0 = keep). Keeps tools/food/
// valuables/fuel and a working buffer of blocks; stashes the bulk + junk.
// IMPORTANT: `it.count` here is the PER-NAME TOTAL, not a single stack — stacks cap
// at 64, so per-stack math meant a 64-buffer could never free anything (audit).
function depositAmount(it) {
  const n = it.name;
  if (/pickaxe|sword|_axe$|shovel|hoe|torch|bed$|crafting_table|furnace|chest|bucket|shield|_helmet|_chestplate|_leggings|_boots|flint_and_steel/.test(n)) return 0;
  if (isEdible(n)) return 0;
  if (/diamond|emerald|_ingot|raw_iron|raw_gold|raw_copper|netherite|redstone|lapis|^coal$|stick$/.test(n)) return 0;
  if (/rotten_flesh|string|spider_eye|poisonous_potato/.test(n)) return it.count; // junk → all
  const buf = /planks|_log$/.test(n) ? 32
    : /cobblestone|^stone$|andesite|diorite|granite|tuff|deepslate|sand$/.test(n) ? 64
    : /_wool$/.test(n) ? 6            // bed materials ride along until crafted
    : /_seeds$|^arrow$/.test(n) ? 24  // farm + future-bow stock stays on her
    : 0;
  return Math.max(0, it.count - buf);
}

async function depositSurplus(bot) {
  // a chest that made no room recently is FULL — stop walking back to it every tick
  if (bot._chestFullUntil && Date.now() < bot._chestFullUntil) return false;
  const items = bot.inventory.items() || [];
  if (items.length < 27) return false; // act before her 36 slots are packed
  // per-NAME totals → how much of each to stash
  const totals = {};
  for (const it of items) totals[it.name] = (totals[it.name] || 0) + it.count;
  const toStash = {};
  for (const [name, total] of Object.entries(totals)) {
    const dep = depositAmount({ name, count: total });
    if (dep > 0) toStash[name] = dep;
  }
  if (!Object.keys(toStash).length) return false;
  const chestBlock = await ensureChest(bot);
  if (!chestBlock) return false;
  let chest;
  try { chest = await bot.openContainer(chestBlock); } catch (_) { return false; }
  const slotsBefore = (bot.inventory.items() || []).length; // AFTER ensureChest (it may craft)
  try {
    for (const it of bot.inventory.items() || []) {
      const want = toStash[it.name];
      if (!want) continue;
      const dep = Math.min(want, it.count);
      try { await chest.deposit(it.type, null, dep); toStash[it.name] -= dep; } catch (_) {}
    }
    chest.close();
  } catch (_) { try { chest.close(); } catch (_) {} return false; }
  // VERIFY slots actually freed — a full chest silently swallows nothing, and then
  // "stored surplus" would be a lie the QC gate can't catch.
  const freed = (bot.inventory.items() || []).length < slotsBefore;
  if (freed) console.log('[MC]   stored surplus in the chest');
  else {
    bot._chestFullUntil = Date.now() + 5 * 60_000;
    console.log('[MC]   deposit made no room (chest full?) — leaving it alone for a while');
  }
  return freed;
}

// Conservative inventory tidy: only when nearly full, toss obvious junk and excess
// filler — while KEEPING a couple stacks of cobblestone/dirt for building.
// Caps compare against per-NAME TOTALS (a single stack can never exceed 64).
async function tidyInventory(bot) {
  const items = bot.inventory.items() || [];
  if (items.length < 30) return; // toss junk before the 36 slots are full
  const JUNK = /rotten_flesh|poisonous_potato|spider_eye/;
  const EXCESS = { cobblestone: 128, dirt: 64, gravel: 64, granite: 64, diorite: 64, andesite: 64, tuff: 64, cobbled_deepslate: 128 };
  const totals = {};
  for (const it of items) totals[it.name] = (totals[it.name] || 0) + it.count;
  const toToss = {};
  for (const [name, total] of Object.entries(totals)) {
    if (JUNK.test(name)) { toToss[name] = total; continue; }
    const cap = EXCESS[name];
    if (cap && total > cap) toToss[name] = total - cap;
  }
  for (const it of bot.inventory.items() || []) {
    const want = toToss[it.name];
    if (!want) continue;
    const n = Math.min(want, it.count);
    try { await bot.toss(it.type, null, n); toToss[it.name] -= n; } catch (_) {}
  }
}

// dropped-item entities vary in naming across versions — match them broadly
function isDrop(e) {
  // prismarine-entity keeps objectType only as a deprecated getter that emits a
  // full console.trace on every read. Use the canonical fields its own
  // getDroppedItem() implementation recognizes so a pickup sweep cannot flood logs.
  return e && e.position && (
    e.name === 'item' || e.name === 'Item' || e.name === 'item_stack' || e.displayName === 'Item'
  );
}

// walk onto the nearest dropped item so it's actually picked up (mineflayer only
// collects items she physically stands over). `skip` holds drops she already failed
// to reach so a stranded drop (flew onto a ledge) doesn't trap the sweep.
async function collectNearestDrop(bot, maxDist = 8, skip = null) {
  if (!bot.entity || !bot.entity.position) return false;
  const me = bot.entity.position;
  const item = bot.nearestEntity((e) =>
    isDrop(e) && e.position.distanceTo(me) < maxDist && !(skip && skip.has(e.id)));
  if (!item) return false;
  // VERIFY pickup by inventory, NOT by "arrived at the drop". Arriving where a drop
  // WAS doesn't mean she got it — it can despawn or another player grabs it first.
  const before = totalItems(bot);
  const id = item.id;
  try { await gotoTimed(bot, new goals.GoalNear(item.position.x, item.position.y, item.position.z, 0), 8000); } catch (_) {}
  await sleep(150); // let the collect packet register
  if (totalItems(bot) > before) return true; // actually gained an item
  if (skip) skip.add(id); // couldn't get it (unreachable / taken) — don't chase it again
  return false;
}

// sweep up every reachable drop after a gathering burst — generous radius because
// item trajectories are random and can fling a drop several blocks away.
async function collectDrops(bot, maxDist = 18, tries = 16) {
  const skip = new Set();
  let any = false;
  for (let i = 0; i < tries; i++) {
    const got = await collectNearestDrop(bot, maxDist, skip);
    if (!got && !bot.nearestEntity((e) => isDrop(e) && !skip.has(e.id)
        && e.position.distanceTo(bot.entity.position) < maxDist)) break;
    if (got) any = true;
  }
  return any;
}

function isFood(name) {
  return /(apple|bread|cooked_|_cooked|beef|porkchop|chicken|mutton|carrot|potato|melon|berries|stew|steak|cod|salmon)/.test(name);
}
function isPlaceable(name) {
  return /(dirt|cobblestone|stone|planks|log|netherrack|sand|gravel|deepslate)/.test(name);
}
function vec(x, y, z) { const { Vec3 } = require('vec3'); return new Vec3(x, y, z); }
function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }
function invCount(bot, re) {
  return (bot.inventory.items() || []).filter((i) => re.test(i.name)).reduce((n, i) => n + i.count, 0);
}
// total wood as plank-equivalents (a log crafts into 4 planks). Used to tell a real
// wood shortage from a craft that failed for some OTHER reason (no table, etc.).
function woodUnits(bot) {
  return invCount(bot, /planks/) + invCount(bot, /_log$|^log$/) * 4;
}

module.exports = {
  gather, goto, follow, stopFollow, fight, flee, eat, hunt,
  sleep_in_bed, buildShelter, buildHut, placeTorch, lightArea, explore, craft, smartCraft, forage, hungerReset,
  farmCrops, breedAnimals,
  smelt, mineStaircase, mineDeeper, mineToY, branchMine, reachLand, pillarUp,
  tidyInventory, depositSurplus, depositAmount, maintainTools, toolHealthFrac, woodUnits,
  equipToolFor, equipBestWeapon, isFood,
  goHome, smeltFood, opportunisticGrab, surface, goSurface, placeBed, neutralizeSpawner,
  collectDrops,
  ctlGen, interrupted, canMineOre, oreWorth,
  miningTargetKeyword, rememberGatherFailure, gatherFailureActive,
};
