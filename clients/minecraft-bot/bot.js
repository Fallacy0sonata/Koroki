'use strict';

const mineflayer = require('mineflayer');
const { pathfinder } = require('mineflayer-pathfinder');
const { requestCommentary, chooseDesire } = require('./brain-client');
const { WorldEvents, snapshotWorld, HOSTILE_MOBS } = require('./world-state');
const { survivalReflex, ADJACENT_M, CREEPER_M, RANGED_M } = require('./decide');
const { isRanged } = require('./combat');
const {
  PROJECTS, selectProject, stepFor, pickTier, isProject, feasibleProject, actionableProject,
} = require('./projects');
const { parseOwnerCommand } = require('./owner');
const { perceive, finite3 } = require('./perceive');
const { createWorldModel } = require('./world-model');
const { totalItems } = require('./verify');
const { createSocial } = require('./social');
const { createMotor } = require('./motor');
const { rollFumble, pickFumble } = require('./fumble');
const rhythm = require('./rhythm');
const verbs = require('./verbs');
const skills = require('./skills');
const state = require('./state');
const path = require('path');

const DISCORD_RELAY_URL = process.env.DISCORD_RELAY_URL || '';
// Owner identity for the co-op channel. Unset => any player can direct her (fine on
// a private single-owner server); set MC_OWNER_USERNAME to lock it to Koro-san.
const OWNER = (process.env.MC_OWNER_USERNAME || '').toLowerCase();
function isOwnerName(u) { return !OWNER || (u || '').toLowerCase() === OWNER; }

// Hard floor between any two brain calls (90 seconds).
// She plays for real — speech is rare. Owner acks use a shorter floor so co-op feels
// responsive without her narrating every ambient thing.
const COMMENTARY_FLOOR_MS = 90_000;
const OWNER_ACK_FLOOR_MS = 8_000;
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

// Returns true only when she ACTUALLY spoke — callers that must not lose their
// event on a floor-suppressed line (milestones) retry on false.
let _commentaryInFlight = false;
async function saySomething(bot, gameEventContext, opts = {}) {
  const floor = opts.floorMs != null ? opts.floorMs : COMMENTARY_FLOOR_MS;
  const now = Date.now();
  if (now - _lastCommentaryAt < floor) return false;
  if (_commentaryInFlight) return false; // don't stack overlapping brain calls
  _commentaryInFlight = true;
  let text;
  try { text = await requestCommentary({ gameEventContext, flavor: !!opts.flavor }); }
  finally { _commentaryInFlight = false; }
  if (!text) return false; // null = [silent] or error → don't burn the floor, let her try again

  _lastCommentaryAt = Date.now(); // only reset the floor when she ACTUALLY speaks
  const cleaned = text.slice(0, 256);
  bot.chat(cleaned);
  await relayToDiscord(`**[Minecraft]** ${cleaned}`);
  return true;
}

// ── Slow loop (the captain) ───────────────────────────────────────────────────
// Runs every few seconds (the fast body loop handles reflexes at ~10 Hz). Each
// tick: perceive → snapshot → intent arbiter (survival reflex > owner command >
// project) → run the matching skill. Narration reacts to what HAPPENS, never to
// goal-state.

// Captain-loop cadence — the gap between finishing one skill and picking the next.
// It's pure code now (no LLM in the decision path), so it can be snappy: skills run
// continuously while active, and this just kills the dead air between them. Urgent
// re-fires almost immediately so a threat re-decide has no lag.
const CALM_MS = 1_500;
const URGENT_MS = 600;

let _goalTimer = null;
let _lastNightNarrated = false;
let _acting = false;         // a skill is mid-run — don't overlap decide ticks
let _actingSince = 0;        // when the current skill started (watchdog)
let _lastDecideAt = 0;       // when the slow loop last ran (watchdog)
let _lastNudge = 0;          // last time the watchdog nudged a slow skill
// Loop generation — bumped on death/respawn/disconnect/hard-reset. A decide tick
// captures the gen it started under and refuses to touch shared state or reschedule
// if the gen has moved on. This is what prevents STALE ticks (from before a death or
// a forced restart) running a second loop on old coordinates — the cause of the
// "she's in the sea AND somewhere else" conflicts.
let _gen = 0;
let _preempt = false;        // danger monitor asked for an immediate re-decide
let _currentGoal = null;     // what she's doing right now (danger monitor reads it)
let _deathLoc = null;        // where she died — go back for the dropped items

function stopGoalLoop() {
  if (_goalTimer) { clearTimeout(_goalTimer); _goalTimer = null; }
}

function scheduleNextGoal(bot, delayMs = CALM_MS) {
  stopGoalLoop();
  _goalTimer = setTimeout(() => decideAndAct(bot), delayMs);
}

// ── Fast loop (the body) — ~10 Hz ─────────────────────────────────────────────
// Dual-rate design (see docs/minecraft_mind_design.md §2.0): the CAPTAIN decides
// slowly (decideAndAct, every few seconds); the BODY runs fast. Minecraft is an
// active game — reflexes need ~100 ms reactions, and an LLM can't run at that rate.
//
// This loop does two things every ~100 ms, both pure code:
//  1. PERCEIVE — validate her sense of self. On NaN/desync it does nothing (waits),
//     which is what keeps the garbage-position crash from ever reaching a skill.
//  2. DANGER REFLEX — if a hostile closes in and she isn't already defending, stop
//     the running skill (pathfinder.stop breaks its await) and force an immediate
//     re-decide, so reflexes truly preempt. This is the startle response; it does
//     not wait for the captain to finish a sentence. (Combat/movement control on
//     this same loop comes in later phases.)
const FAST_TICK_MS = 100;
let _fastTimer = null;
const DEFENSIVE = new Set(['flee', 'fight', 'eat', 'sleep', 'build_shelter', 'surface']);

function nearestThreat(bot) {
  const me = bot.entity && bot.entity.position;
  if (!me) return null;
  let best = null;
  for (const e of Object.values(bot.entities || {})) {
    if (!e || e === bot.entity || !e.isValid || !e.position) continue;
    const name = (e.name || '').toLowerCase();
    if (!HOSTILE_MOBS.has(name)) continue;
    const d = e.position.distanceTo(me);
    const range = name === 'creeper' ? CREEPER_M : (isRanged(name) ? RANGED_M : ADJACENT_M);
    if (d <= range && (!best || d < best.d)) best = { name, d };
  }
  return best;
}

let _lastMovePos = null;
let _stuckSince = 0;

function fastTick(bot) {
  const now = Date.now();
  // 0. WATCHDOG FIRST — before the perception gate. The combat freeze (round 18c):
  // explosion knockback → NaN desync → physics stalls → a smooth lookAt in the
  // fight never resolves → skill hangs; and the perceive gate below returned early
  // on the SAME NaN, so the watchdog never ran. The safety net must not sit behind
  // the door that's jammed.
  if (_acting) {
    if (now - _actingSince > 15_000 && now - _lastNudge > 5_000) {
      _lastNudge = now;
      try { bot.pathfinder.stop(); } catch (_) {} // break a hung goto; skill returns & reschedules
    }
    if (now - _actingSince > 120_000) {
      console.log('[MC] watchdog: hard reset (skill hung ~120s)');
      _gen++; _acting = false; _currentGoal = null;
      if (bot._ctl) bot._ctl.gen = _gen; // cancel the zombie skill cooperatively
      try { bot.pathfinder.stop(); } catch (_) {}
      stopGoalLoop(); scheduleNextGoal(bot, 500);
    }
  } else if (!_goalTimer && now - _lastDecideAt > 12_000) {
    console.log('[MC] watchdog: loop idle — kicking it');
    scheduleNextGoal(bot, 300);
  }

  // 1. Proprioception gate — act on nothing while her position is untrustworthy.
  const per = perceive(bot);
  if (!per.valid) {
    // Desynced = targetable but paralyzed — the most dangerous state there is
    // (owner round 18e: "she can take damage but can't move"). Stop held inputs
    // immediately, and if it doesn't heal FAST, relog — that's the same move a
    // human player makes when the server rubber-bands them. 12s, not 30.
    if (!bot._invalidSince) {
      bot._invalidSince = now;
      try { bot.clearControlStates(); } catch (_) {} // don't sprint into walls while blind
      try { bot.pathfinder.stop(); } catch (_) {}
    } else if (now - bot._invalidSince > 12_000) {
      console.log('[MC] position desync stuck >12s — relogging (the human fix for rubber-banding)');
      bot._invalidSince = null;
      try { bot.end(); } catch (_) {}
    }
    return;
  }
  bot._invalidSince = null;
  // remember recent peril so death-cause learning can attribute a death
  if (per.inLava) bot._recentPeril = { cause: 'lava', at: now };
  // Breadcrumb: the last spot she could BREATHE. In a flooded cave "swim up" hits
  // the ceiling — the escape is back the way she came (water-cave drowning death,
  // live round 16). Cheap: one assignment per tick.
  if (!per.headInWater) bot._lastAirPos = per.pos;
  else if (bot.oxygenLevel != null && bot.oxygenLevel < 15) bot._recentPeril = { cause: 'drowning', at: now };

  // 1a2. DON'T DROWN — only FORCE swim-up when she's actually low on air AND the
  //      pathfinder isn't already moving her. Holding jump every tick fought the
  //      pathfinder's own swim controls and left her bobbing in place (ocean freeze).
  const pathing = bot.pathfinder && bot.pathfinder.isMoving && bot.pathfinder.isMoving();
  const drowning = per.headInWater && bot.oxygenLevel != null && bot.oxygenLevel <= 6;
  if (drowning && !pathing) {
    bot._swimming = true;
    try { bot.setControlState('jump', true); } catch (_) {}
  } else if (bot._swimming) {
    bot._swimming = false;
    try { bot.setControlState('jump', false); } catch (_) {}
  }
  // sprint-SWIM: hold sprint while swimming so she does the fast swim (not the slow
  // "swim-walk" that let her crawl 500 blocks across open ocean).
  const inWater = per.feetInWater || per.headInWater;
  if (inWater && pathing) { try { bot.setControlState('sprint', true); bot._forcedSprint = true; } catch (_) {} }
  else if (bot._forcedSprint && !inWater) { try { bot.setControlState('sprint', false); } catch (_) {} bot._forcedSprint = false; }

  // 1a2b. COBWEB — being webbed reads as a softlock: the pathfinder can't move her
  //       and the anti-stuck hop does nothing. A player just CUTS the web (round 18).
  const webBlock = (per.feetBlock && per.feetBlock.name === 'cobweb') ? per.feetBlock
    : (per.headBlock && per.headBlock.name === 'cobweb') ? per.headBlock : null;
  if (webBlock && !bot._cuttingWeb) {
    bot._cuttingWeb = true;
    (async () => {
      try {
        console.log('[MC] webbed — cutting myself out');
        await skills.equipBestWeapon(bot); // swords shred webs
        const b = bot.blockAt(webBlock.position);
        if (b && b.name === 'cobweb') await bot.dig(b);
      } catch (_) {}
      finally { setTimeout(() => { bot._cuttingWeb = false; }, 1500); }
    })();
  }

  // 1a2c. MLG WATER CLUTCH — falling far with a water bucket: look down, pour just
  //       before impact, scoop it back after (owner ask, round 18). Best-effort —
  //       server lag can eat the timing, but a fail costs nothing vs certain damage.
  if (per.onGround) {
    bot._fallStartY = null;
  } else if (bot.entity.velocity && bot.entity.velocity.y < -0.4) {
    if (bot._fallStartY == null) bot._fallStartY = per.pos.y;
    const fallen = bot._fallStartY - per.pos.y;
    if (fallen > 4 && !bot._mlgBusy) {
      const wb = (bot.inventory.items() || []).find((i) => i.name === 'water_bucket');
      if (wb) {
        bot._mlgBusy = true;
        (async () => {
          try {
            await bot.equip(wb, 'hand');
            await bot.look(bot.entity.yaw, -Math.PI / 2, true); // straight down
            for (let i = 0; i < 40; i++) {
              if (bot.entity.onGround) break;
              const b2 = bot.blockAt(bot.entity.position.offset(0, -2, 0));
              const b3 = bot.blockAt(bot.entity.position.offset(0, -3, 0));
              if ((b2 && b2.boundingBox === 'block') || (b3 && b3.boundingBox === 'block')) {
                bot.activateItem(); // pour — the landing becomes a splash
                console.log('[MC] ! MLG water clutch');
                break;
              }
              await new Promise((r) => setTimeout(r, 50));
            }
            // scoop it back once she's down
            await new Promise((r) => setTimeout(r, 900));
            const placed = bot.findBlock({ matching: (b) => b && b.name === 'water', maxDistance: 3 });
            const empty = (bot.inventory.items() || []).find((i) => i.name === 'bucket');
            if (placed && empty) {
              await bot.equip(empty, 'hand');
              await bot.lookAt(placed.position.offset(0.5, 0.5, 0.5), true);
              await bot.activateItem();
            }
          } catch (_) {}
          finally { bot._mlgBusy = false; bot._fallStartY = null; }
        })();
      }
    }
  }

  // 1a3. ON FIRE / IN LAVA — the water-bucket save (canon: lava insurance). Fire
  //      and forget, self-throttled; must never block the reflex tick.
  const burning = per.inLava || (per.feetBlock && (per.feetBlock.name === 'fire' || per.feetBlock.name === 'soul_fire'));
  if (burning && !bot._pouring) {
    bot._pouring = true;
    verbs.pourAtFeet(bot)
      .then((ok) => { if (ok) console.log('[MC] ! water bucket save'); })
      .catch(() => {})
      .finally(() => setTimeout(() => { bot._pouring = false; }, 8000));
  }

  // 1a4. MOTOR HUMANIZATION — the body language layer. A live head while idle, a
  //      playful hop while traveling. Calm only; danger keeps full priority below.
  const calmNow = !nearestThreat(bot);
  if (bot._motor && calmNow) {
    if (pathing) bot._motor.travelHop(bot.entity.onGround, inWater, true);
    else if (!_acting) bot._motor.idleTick().catch(() => {});
  }

  // (watchdog moved to the TOP of this tick — see section 0: it must run even when
  // perception is invalid, because the NaN that jams perception is the same NaN
  // that hangs the skill it needs to reset.)

  // 1c. Projectile dodge — a basic sidestep when an arrow/fireball is incoming.
  dodgeProjectiles(bot, per.pos);

  // 2. Anti-stuck: if she's supposed to be pathing but hasn't moved for ~2.5 s,
  //    she's caught on geometry (the tree incident) — stop and hop to break free.
  const moving = bot.pathfinder && bot.pathfinder.isMoving && bot.pathfinder.isMoving();
  if (moving) {
    const p = bot.entity.position;
    if (_lastMovePos && p.distanceTo(_lastMovePos) < 0.15) {
      if (now - _stuckSince > 2500) {
        console.log('[MC] anti-stuck: not moving while pathing — nudging free');
        try { bot.pathfinder.stop(); } catch (_) {}
        try {
          bot.setControlState('jump', true);
          setTimeout(() => { try { bot.setControlState('jump', false); } catch (_) {} }, 400);
        } catch (_) {}
        _preempt = true;
        if (!_acting) { stopGoalLoop(); scheduleNextGoal(bot, 500); }
        _lastMovePos = null; _stuckSince = now;
      }
    } else { _lastMovePos = p.clone(); _stuckSince = now; }
  } else { _lastMovePos = null; _stuckSince = now; }

  // 3. Danger reflex (preempt the slow loop / running skill).
  const threat = nearestThreat(bot);
  if (!threat || DEFENSIVE.has(_currentGoal)) return;
  if (now - (bot._lastDangerLogAt || 0) > 2000) { // it fires at 10 Hz — log at 0.5 Hz
    bot._lastDangerLogAt = now;
    console.log(`[MC] ! danger: ${threat.name}@${Math.round(threat.d)}m — `
      + `interrupting ${_currentGoal || 'idle'}`);
  }
  _preempt = true;
  if (bot._ctl) bot._ctl.preempt = true;        // cooperative interrupt: non-pathfinder
  try { bot.pathfinder.stop(); } catch (_) {}   // loops (smelt poll, wool hunt) bail too
  // MICRO-EVADE — the spinal reflex. The captain needs a beat to re-decide; a mob
  // already in her face gets an immediate physical backpedal so a creeper can't
  // just sit at 1m and cook (16-second standstill death window, round 16c).
  if (threat.d <= 3.5 && now - (bot._lastEvadeAt || 0) > 1200) {
    bot._lastEvadeAt = now;
    try {
      bot.setControlState('back', true);
      bot.setControlState('sprint', true);
      setTimeout(() => {
        try { bot.setControlState('back', false); bot.setControlState('sprint', false); } catch (_) {}
      }, 500);
    } catch (_) {}
  }
  if (!_acting) { stopGoalLoop(); scheduleNextGoal(bot, 0); }
}

// Scan surroundings into the world model — resources to come back for, hazards to
// avoid. Called every few slow ticks (cheap findBlocks, not every 100 ms).
function observe(bot) {
  const wm = bot._wm;
  if (!wm || !bot.entity) return;
  try {
    const ores = bot.findBlocks({ matching: (b) => b && /_ore$/.test(b.name || ''), maxDistance: 20, count: 6 });
    for (const v of ores) { const b = bot.blockAt(v); if (b) wm.noteResource(v, b.name); }
    const lavas = bot.findBlocks({ matching: (b) => b && (b.name === 'lava' || b.name === 'flowing_lava'), maxDistance: 14, count: 5 });
    for (const v of lavas) wm.noteHazard(v, 'lava');
    // remember crafting stations she passes so she reuses them, not spawns new ones
    const tables = bot.findBlocks({ matching: (b) => b && b.name === 'crafting_table', maxDistance: 20, count: 4 });
    for (const v of tables) wm.noteLandmark(v, 'crafting_table');
    // FOUND chests (village/shipwreck/mineshaft — not hers, not yet looted) = the
    // structure-loot channel. Flag the nearest as a loot opportunity for the arbiter.
    const chests = bot.findBlocks({ matching: (b) => b && (b.name === 'chest' || b.name === 'barrel'), maxDistance: 24, count: 4 });
    for (const v of chests) {
      if (!verbs.isKnownOwnChest(wm, v)) { bot._lootCandidate = { pos: v, at: Date.now() }; break; }
    }
    // MOB SPAWNER (owner strat, round 17d): torch the cage BEFORE fighting the
    // spawns — killing mobs while the cage keeps pumping is a losing fight.
    const spawner = bot.findBlock({ matching: (b) => b && b.name === 'spawner', maxDistance: 16 });
    if (spawner) {
      const already = wm.nearest('landmarks', spawner.position, (e) => e.tag === 'spawner_lit');
      const lit = already && Math.hypot(already.pos.x - spawner.position.x,
        already.pos.y - spawner.position.y, already.pos.z - spawner.position.z) < 2;
      if (!lit) bot._spawnerCandidate = { pos: spawner.position, at: Date.now() };
    }
  } catch (_) {}
}

// Basic projectile evasion: if an arrow/fireball is close and moving toward her,
// sidestep. (Ghast fireball DEFLECTION — hitting it back — is a future upgrade.)
let _lastDodgeAt = 0;
function dodgeProjectiles(bot, me) {
  const now = Date.now();
  if (now - _lastDodgeAt < 700) return; // don't jitter
  for (const e of Object.values(bot.entities || {})) {
    if (!e || !e.position || !e.velocity) continue;
    const n = (e.name || '').toLowerCase();
    if (n !== 'arrow' && n !== 'spectral_arrow' && !n.includes('fireball')) continue;
    if (e.position.distanceTo(me) > 8) continue;
    // heading toward her? velocity pointed at her position
    const tox = me.x - e.position.x; const toz = me.z - e.position.z;
    if (e.velocity.x * tox + e.velocity.z * toz <= 0) continue; // moving away
    const dir = Math.random() < 0.5 ? 'left' : 'right';
    _lastDodgeAt = now;
    try {
      bot.setControlState(dir, true);
      setTimeout(() => { try { bot.setControlState(dir, false); } catch (_) {} }, 300);
    } catch (_) {}
    return;
  }
}

function startFastLoop(bot) {
  stopFastLoop();
  _fastTimer = setInterval(() => { try { fastTick(bot); } catch (_) {} }, FAST_TICK_MS);
}
function stopFastLoop() {
  if (_fastTimer) { clearInterval(_fastTimer); _fastTimer = null; }
}

// ── Intent arbiter ────────────────────────────────────────────────────────────
// When no survival reflex fires, something must decide what she pursues. Priority
// (design §3.5): survival reflex > OWNER command > autonomous project. Reflexes are
// handled first in decideAndAct; this resolves owner-vs-project.
//
// _ownerIntent is Koro-san's standing directive (persistent for follow/wait; one-shot
// for come/gather/mine/build). _activeProject is what she's autonomously working on —
// kept stable across ticks so she finishes a project instead of re-deciding from
// scratch every few seconds. (The 8B will pick the project in a later phase; for now
// selectProject follows the deterministic progression.)
let _ownerIntent = null;
let _activeProject = null;
let _observeTick = 0;
// Interrupt → handle → RESUME (FDG 2015: fights are embedded episodes inside a
// persisting task). The last discretionary decision is saved when a reflex preempts
// it, and resumed once the coast is clear — instead of re-deriving from scratch.
let _taskFrame = null;
let _lastDiscretionary = null;
const TASK_FRAME_TTL = 4 * 60_000;
// The DIRECTOR (8B) picks her next project every few minutes when calm; the
// deterministic ladder is the always-there fallback + feasibility veto.
let _lastDirectorAt = 0;
const DIRECTOR_MS = 4 * 60_000;
// kit-check redirect cap so a barren spawn can't loop on prep forever
let _prepAttempts = 0;
// Per-project failure budget: a project whose steps keep failing (no sheep anywhere
// for the bed…) gets PARKED for a while so it can't gate the whole ladder.
let _projFailCount = 0;
const _parkedProjects = new Map(); // name -> parked-until (ms)
let _projectAdoptedAt = 0;
function parkedSet() {
  const now = Date.now();
  const s = new Set();
  for (const [name, until] of _parkedProjects) {
    if (until > now) s.add(name); else _parkedProjects.delete(name);
  }
  return s;
}
// Stall breaker (DEPS-style): if a discretionary goal makes zero progress (no items
// gained, no movement) for several ticks running, she's looping — relocate to break it.
let _stallKey = '';
let _stallCount = 0;
let _forceRelocate = false;
function progressSig(bot) {
  const p = (bot.entity && bot.entity.position) || { x: 0, z: 0 };
  return `${totalItems(bot)}|${Math.round(p.x)}|${Math.round(p.z)}`;
}
let _failedFoodSearches = 0; // consecutive hunt+forage failures → triggers hunger reset

// Last-resort hunger reset: only when she genuinely cannot eat and is being drained.
// Requires a bed (to respawn on the spot), a clear coast, and repeated failed food
// searches, so it never fires casually.
function shouldHungerReset(snap) {
  return snap.hp != null && snap.hp <= 4
    && snap.food != null && snap.food <= 1
    && (snap.inv && snap.inv.foodItems === 0)
    && snap.inv.hasBed
    && (!snap.hostiles || snap.hostiles.length === 0)
    && _failedFoodSearches >= 3;
}

function ownerDecision(bot, intent, snap) {
  const who = intent.who;
  const seeOwner = () => (bot.players[who] && bot.players[who].entity) || null;
  switch (intent.kind) {
    case 'follow': {
      const ent = seeOwner();
      if (!ent) return { goal: 'wait', target: null, reason: "waiting — can't see Koro-san", urgent: false };
      return { goal: 'follow', target: { name: who }, reason: 'following Koro-san', urgent: false };
    }
    case 'wait':
      return { goal: 'wait', target: null, reason: 'holding for Koro-san', urgent: false };
    case 'come': {
      const ent = seeOwner();
      if (!ent || ent.position.distanceTo(bot.entity.position) <= 4) { _ownerIntent = null; return null; }
      return { goal: 'goto', target: ent.position, reason: 'heading to Koro-san', urgent: false };
    }
    // one-shots: do one cycle, then hand back to autonomy
    case 'gather':
      _ownerIntent = null;
      return { goal: 'gather', target: intent.target, reason: `getting ${intent.target} for Koro-san`, urgent: false };
    case 'mine':
      _ownerIntent = null;
      return { goal: 'mine', target: 'ore', reason: 'mining for Koro-san', urgent: false };
    case 'build':
      _ownerIntent = null;
      return { goal: 'build_hut', target: null, reason: 'building a hut for Koro-san', urgent: false };
    default:
      _ownerIntent = null;
      return null;
  }
}

function projectDecision(bot, snap) {
  // a director-adopted 'explore' never completes — expire it so the ladder can run
  // again even if the 8B goes down (audit: permanent progression bypass)
  if (_activeProject === 'explore' && Date.now() - _projectAdoptedAt > 8 * 60_000) {
    _activeProject = null;
  }
  // a project she can no longer work (died and lost the pickaxe tier, or it was
  // adopted through a since-fixed hole) gets dropped, not ground against
  if (_activeProject && !feasibleProject(_activeProject, snap)) {
    console.log(`[MC] project ${_activeProject} no longer feasible — re-selecting`);
    _activeProject = null;
  }
  if (!_activeProject || stepFor(_activeProject, snap).done) {
    _activeProject = selectProject(snap, parkedSet());
    _projectAdoptedAt = Date.now();
  }
  const step = stepFor(_activeProject, snap);
  if (step.done) {
    return { goal: 'explore', target: null, reason: 'nothing pressing, wandering', urgent: false };
  }
  // feed "next:" so her commentary knows where the day is going (was always empty)
  try {
    const proj = PROJECTS[_activeProject];
    const upcoming = proj.steps.filter((st) => !st.done(snap)).slice(1, 4).map((st) => st.label);
    state.setQueue(upcoming);
  } catch (_) {}
  return {
    goal: step.goal,
    target: step.target,
    reason: `${step.projectLabel}: ${step.label}`,
    urgent: false,
    project: _activeProject,
  };
}

// ── The director: the 8B picks WHAT TODAY IS ABOUT (not per-tick goals) ────────
// Runs every few minutes when calm. An infeasible/invalid pick is vetoed by code;
// a dead brain just means the deterministic ladder keeps playing. On adoption she
// SAYS the intention — narrated intention is both the human tell and the stream.
async function maybeConsultDirector(bot, snap) {
  const now = Date.now();
  if (now - _lastDirectorAt < DIRECTOR_MS) return;
  if ((snap.hostiles || []).length) return; // not while something's stalking her
  _lastDirectorAt = now;
  const line = [
    `time:${snap.timeLabel} hp:${snap.hp} food:${snap.food}`,
    `pick_tier:${pickTier(snap)} weapon:${!!snap.inv.hasWeapon} shield:${!!snap.inv.hasShield} bed:${!!snap.inv.hasBed}`,
    `stock: food=${snap.inv.foodItems} torches=${snap.inv.torches} iron_ingots=${snap.inv.ironIngots || 0} wool=${snap.inv.wool || 0}`,
    `current_project:${_activeProject || 'none'}`,
    'Pick her next project.',
  ].join(' | ');
  const desire = await chooseDesire(line);
  if (!desire || !isProject(desire.project)) return;
  if (!feasibleProject(desire.project, snap)) {
    console.log(`[MC] director wanted ${desire.project} — not feasible yet, vetoed`);
    return;
  }
  if (!actionableProject(desire.project, snap)) {
    console.log(`[MC] director wanted ${desire.project} — already complete, vetoed`);
    return;
  }
  if (desire.project === _activeProject) return;
  if (parkedSet().has(desire.project)) return; // it's on a failure timeout — no
  console.log(`[MC] director: new project → ${desire.project}`);
  _activeProject = desire.project;
  _projectAdoptedAt = Date.now();
  const label = (PROJECTS[desire.project] || {}).label || desire.project;
  await saySomething(bot, WorldEvents.newPlan(bot, label)); // she says what she's setting out to do
}

// Context for the rhythm layer (pure decisions live in rhythm.js).
function rhythmCtx(bot, snap) {
  const base = bot._wm && bot._wm.getBase();
  const me = bot.entity.position;
  const distToBase = base ? Math.hypot(base.x - me.x, base.z - me.z) : Infinity;
  let hasFurnaceNear = false;
  try {
    const known = bot._wm && bot._wm.nearest('landmarks', me, (e) => e.tag === 'furnace');
    hasFurnaceNear = !!(known && Math.hypot(known.pos.x - me.x, known.pos.z - me.z) < 24);
  } catch (_) {}
  return {
    timeLabel: snap.timeLabel,
    y: snap.y,
    baseSet: !!base,
    distToBase,
    atBase: distToBase <= 16,
    slotsUsed: snap.inv.slotsUsed || 0,
    torches: snap.inv.torches || 0,
    foodUnits: snap.inv.foodItems || 0,
    hunger: snap.food,
    rawFood: snap.inv.rawFood || 0,
    hasBed: !!snap.inv.hasBed,
    bedNearby: !!snap.bedNearby,
    enclosed: !!snap.enclosed,
    hasFurnaceNear,
    sticks: snap.inv.sticks || 0,
    coal: snap.inv.coal || 0,
    logs: snap.inv.logs || 0,
  };
}

// map a parsed command to a short human phrase for her verbal acknowledgement
function phraseFor(cmd) {
  switch (cmd.kind) {
    case 'follow': return 'follow you';
    case 'come': return 'come to you';
    case 'wait': return 'wait here';
    case 'gather': return `gather ${cmd.target}`;
    case 'mine': return 'go mining';
    case 'build': return 'build a shelter';
    default: return cmd.kind;
  }
}

async function applyOwnerCommand(bot, username, cmd) {
  if (cmd.kind === 'release') {
    _ownerIntent = null;
    try { bot.pathfinder.stop(); } catch (_) {}
    console.log('[MC] owner: released — back to autonomy');
    await saySomething(bot, WorldEvents.ownerCommand(bot, 'go back to your own thing', username),
      { floorMs: OWNER_ACK_FLOOR_MS });
    return;
  }
  _ownerIntent = { kind: cmd.kind, target: cmd.target || null, who: username, at: Date.now() };
  console.log(`[MC] owner command: ${cmd.kind}${cmd.target ? ' ' + cmd.target : ''} (from ${username})`);
  // preempt whatever she's doing and re-decide immediately
  _preempt = true;
  try { bot.pathfinder.stop(); } catch (_) {}
  if (!_acting) { stopGoalLoop(); scheduleNextGoal(bot, 0); }
  await saySomething(bot, WorldEvents.ownerCommand(bot, phraseFor(cmd), username),
    { floorMs: OWNER_ACK_FLOOR_MS });
}

async function decideAndAct(bot) {
  // A skill is still running (the danger monitor may re-trigger us) — don't
  // stack two skills on top of each other.
  if (_acting) return;
  const myGen = _gen;         // this tick belongs to the current generation
  _lastDecideAt = Date.now(); // watchdog heartbeat
  // Proprioception gate — the SLOW loop, like the fast loop, refuses to act on an
  // untrustworthy body state. This catches NaN (the old `!position` check did NOT:
  // a NaN Vec3 is still an object, so garbage flowed straight into the skills and
  // crashed them). On invalid state she waits and re-checks shortly.
  if (!perceive(bot).valid) return scheduleNextGoal(bot, 3_000);

  _acting = true;
  _actingSince = Date.now();
  let decision = null;
  try {
    let snapshot;
    try {
      snapshot = snapshotWorld(bot);
    } catch (err) {
      console.warn('[MC] snapshot failed:', err.message);
      return scheduleNextGoal(bot, 8_000);
    }
    if (!snapshot) return scheduleNextGoal(bot, 3_000); // NaN/desync guard

    // Spatial memory + tool upkeep every few ticks (the findBlocks scan is costly).
    _observeTick = (_observeTick + 1) % 4;
    if (_observeTick === 0) {
      observe(bot);
      if (bot._wm) { bot._wm.decay(); bot._wm.save(); }
      try { await skills.maintainTools(bot); } catch (_) {} // craft a spare before a tool breaks
    }
    // ORE ON SIGHT — an always-on habit, not an explore-only one. She walked past
    // visible (deepslate) iron all cave long because grabbing it was only "her job"
    // while a mining project was active (round 17c). If she can see it and her pick
    // can drop it, she takes the vein — whatever she was otherwise doing.
    if (!snapshot.hostiles.length && !_ownerIntent
        && Date.now() - (bot._lastOreGrabAt || 0) > 30_000) {
      const ore = bot.findBlock({
        matching: (b) => b && /_ore$/.test(b.name || '') && skills.canMineOre(bot, b.name)
          && skills.oreWorth(bot, b.name), // no copper treadmill (round 18g)
        maxDistance: 8,
        useExtraInfo: (b) => { try { return bot.canSeeBlock(b); } catch (_) { return true; } },
      });
      if (ore) {
        bot._lastOreGrabAt = Date.now();
        console.log(`[MC]   (ore in sight: ${ore.name} — grabbing the vein)`);
        const kw = ore.name.replace(/^deepslate_/, '').replace('_ore', '');
        try { await skills.gather(bot, kw, 3, 12); } catch (_) {}
      }
    }
    // Stray drops: she NOTICED un-collected items but never went back for them
    // (owner, round 16). A player who sees an item lying around grabs it — quick,
    // calm-gated, cooled down so it can't derail a task into a chase.
    if (!snapshot.hostiles.length && Date.now() - (bot._lastStrayGrabAt || 0) > 20_000) {
      const stray = bot.nearestEntity((e) => e && e.position
        && (e.name === 'item' || e.displayName === 'Item')
        && e.position.distanceTo(bot.entity.position) < 8);
      if (stray) {
        bot._lastStrayGrabAt = Date.now();
        console.log('[MC]   (stray item nearby — grabbing it)');
        try { await skills.goto(bot, stray.position); } catch (_) {}
      }
    }
    // Inventory management EVERY tick (it self-gates on near-full) so she stashes/tosses
    // surplus before her pack fills and she can't pick up ore/diamond.
    try { const stored = await skills.depositSurplus(bot); if (!stored) await skills.tidyInventory(bot); } catch (_) {}
    // WEAR what she owns — she used to hoard armor and walk around naked (verified
    // gap). Early-exits when nothing better is in the pack, so it's cheap every tick.
    try { await verbs.equipArmor(bot); } catch (_) {}
    try { await checkMilestones(bot); } catch (_) {} // verified progression reactions

    // ambient reaction: night is coming (once per night)
    if (snapshot.timeLabel === 'dusk' && !_lastNightNarrated) {
      _lastNightNarrated = true;
      await saySomething(bot, WorldEvents.nightFalling(bot), { flavor: true });
    } else if (snapshot.timeLabel === 'morning') {
      _lastNightNarrated = false;
    }

    // Intent arbiter (rebuilt 2026-07-13 with the human layers): urgent reflex >
    // owner command > RHYTHM (dusk/haul-home/night shift — also supersedes the
    // NON-urgent shelter reflex, else rule 6 starves the rhythm layer every night) >
    // non-urgent reflex > RESUME the interrupted task > loot opportunity > project
    // (with the 8B director steering).
    const reflex = survivalReflex(snapshot);
    let beat = null;
    let rctx = null;
    if (!_ownerIntent) {
      rctx = rhythmCtx(bot, snapshot);
      if (!reflex || !reflex.urgent) {
        beat = rhythm.rhythmDecision(rctx);
        // one home trip per cooldown — go_home re-firing every tick was a tether
        if (beat && beat.goal === 'go_home' && Date.now() - (bot._lastHomeTripAt || 0) < 180_000) beat = null;
      }
    }
    // Death recovery — reclaim the drops, but NEVER through the thing that killed
    // her. It used to run BEFORE the reflex layer, so a husk camping the corpse got
    // free hits every tick while she pathed at it (the 12-deaths-in-3-minutes
    // spiral, round 16d). Now: reflexes outrank it, and a camped corpse waits.
    if (!reflex && _deathLoc) {
      const campers = (snapshot.hostiles || []).some((h) => h.dist <= 12);
      if (!campers) {
        const loc = _deathLoc; _deathLoc = null;
        decision = { goal: 'recover', target: loc, reason: 'recovering my drops', urgent: false };
      } else if (Date.now() - (bot._lastCampLogAt || 0) > 10_000) {
        bot._lastCampLogAt = Date.now();
        console.log('[MC] corpse is camped — clearing the area first');
      }
    }
    if (decision) {
      // corpse run adopted — nothing else to arbitrate this tick
    } else if (reflex && !beat) {
      decision = reflex;
      // If she's told to hunt but can't find any food and is starving to death,
      // fall back to the bed-reset strat instead of hunting forever.
      if (decision.goal === 'hunt' && shouldHungerReset(snapshot)) {
        decision = { goal: 'hunger_reset', target: null, reason: 'starving + stuck, reset at bed', urgent: true };
      }
      // save what she was doing so she RETURNS to it after the interruption (human
      // pattern: a fight is an episode inside a task, not the end of the task)
      if (_lastDiscretionary && !_taskFrame) {
        _taskFrame = { ..._lastDiscretionary, savedAt: Date.now() };
      }
    } else if (_ownerIntent) {
      decision = ownerDecision(bot, _ownerIntent, snapshot) || projectDecision(bot, snapshot);
    } else if (beat) {
      decision = beat;
      if (beat.goal === 'go_home') {
        await saySomething(bot, WorldEvents.headingHome(bot, beat.reason), { flavor: true });
      }
    } else if (_taskFrame && Date.now() - _taskFrame.savedAt < TASK_FRAME_TTL) {
      decision = { ..._taskFrame, reason: `back to it — ${_taskFrame.reason}` };
      _taskFrame = null; // resume once; if it stalls again the ladder re-derives
    } else if (bot._spawnerCandidate && Date.now() - bot._spawnerCandidate.at < 60_000
        && (snapshot.inv.torches || 0) > 0 && (snapshot.hostiles || []).length <= 1) {
      // spawner beats the chest — the dungeon loot is next to it anyway, and every
      // second the cage runs is another mob to fight
      bot._spawnerCandidate = null;
      decision = { goal: 'spawner', target: null, reason: 'mob spawner — torching it out first', urgent: false };
    } else if (bot._lootCandidate && Date.now() - bot._lootCandidate.at < 60_000) {
      bot._lootCandidate = null;
      decision = { goal: 'loot', target: null, reason: 'spotted a chest — checking it', urgent: false };
    } else {
      await maybeConsultDirector(bot, snapshot); // the captain steers projects
      decision = projectDecision(bot, snapshot);
      // KIT GATE (caving checklist): a deep mining trip with no torches/food gets
      // redirected to prep first — capped so a barren start can't block progress.
      if (decision.goal === 'mine') {
        const prep = rhythm.prepForMining(rctx, _prepAttempts);
        if (prep) { _prepAttempts++; decision = prep; }
        else _prepAttempts = 0;
      } else {
        _prepAttempts = 0;
      }
    }
    // remember discretionary work so an interruption can resume it (not explore/wait)
    if (!reflex && !_ownerIntent && decision && !/^(explore|wait|go_home|sleep|smelt_food)$/.test(decision.goal)) {
      _lastDiscretionary = { goal: decision.goal, target: decision.target, reason: decision.reason, urgent: false };
    }
    // Stall breaker: if the last few discretionary ticks made no progress, override
    // with a relocate so she stops grinding the same unresolvable spot.
    if (_forceRelocate && !reflex && !_ownerIntent) {
      _forceRelocate = false;
      _taskFrame = null; _lastDiscretionary = null; // a stalled task must not resume
      decision = { goal: 'explore', target: null, reason: 'stalled — relocating to break the loop', urgent: false };
    }
    // Shelter failsafe: if walling up keeps FAILING (out of blocks, bad terrain),
    // stop hammering it every tick — hunker down instead (motor keeps her alive-
    // looking). Resets on any success or a different goal.
    if (decision.goal === 'build_shelter' && (bot._shelterFails || 0) >= 4) {
      decision = { goal: 'wait', target: null, reason: "can't wall up — lying low", urgent: false };
    }
    // A death/reset while the arbiter awaited (commentary, the director) means this
    // tick's snapshot is a ghost — bow out BEFORE touching shared state or acting.
    if (myGen !== _gen) return;
    // log on goal CHANGE only — the identical line every 1.5s flooded the console
    // AND the Discord relay all night (round 16)
    const goalLine = `goal=${decision.goal} target=${decision.target?.name || decision.target || '-'} (${decision.reason}${decision.urgent ? ', URGENT' : ''})`;
    if (goalLine !== bot._lastGoalLine) {
      bot._lastGoalLine = goalLine;
      console.log(`[MC] ${goalLine}`);
    }
    _currentGoal = decision.goal;
    state.setGoal(decision.goal);
    const sigBefore = progressSig(bot);
    const stallKey = `${decision.goal}:${decision.target?.name || decision.target || ''}`;

    // Rare, harmless fumble — only when calm and safe (never mid-crisis). Adds the
    // "strong but occasionally goofs" contrast that makes her feel alive.
    if (!decision.urgent && !snapshot.hostiles.length && rollFumble()) {
      await maybeFumble(bot);
    }
    if (myGen !== _gen) return; // superseded during the fumble/commentary awaits

    // Hand the SKILL the control channel and start its watchdog clock HERE — the
    // housekeeping/director awaits above don't count against the skill's budget.
    if (bot._ctl) { bot._ctl.gen = myGen; bot._ctl.preempt = false; }
    _actingSince = Date.now();
    let skillOk = false;
    try {
      skillOk = (await runSkill(bot, decision)) !== false;
    } catch (err) {
      console.warn(`[MC] skill ${decision.goal} failed:`, err.message);
    }
    // Close the loop in her own memory — "recent:" context finally populates, so
    // commentary knows what she's been doing (was permanently empty: zero callers).
    try { state.completeGoal(decision.goal, skillOk); } catch (_) {}

    // Project failure budget: a project that keeps failing (no sheep for the bed
    // ANYWHERE) gets parked so the ladder moves on instead of livelocking on it.
    if (!reflex && !_ownerIntent && decision.project) {
      if (!skillOk) {
        _projFailCount++;
        if (_projFailCount >= 8) {
          console.log(`[MC] project ${decision.project} keeps failing — parking it for 10 min`);
          _parkedProjects.set(decision.project, Date.now() + 10 * 60_000);
          _activeProject = null; _projFailCount = 0;
        }
      } else {
        _projFailCount = 0;
      }
    }

    // did this discretionary tick actually make progress (items or movement)?
    // Reflex ticks are excluded — a night of build_shelter no-ops used to bank a
    // stale relocate that fired at dawn and wiped the task frame.
    if (!reflex && !decision.urgent && decision.goal !== 'explore' && decision.goal !== 'wait') {
      const noProgress = progressSig(bot) === sigBefore && stallKey === _stallKey;
      _stallCount = noProgress ? _stallCount + 1 : 0;
      _stallKey = stallKey;
      if (_stallCount >= 4) {
        console.log(`[MC] stalled on ${stallKey} x${_stallCount} — will relocate to break the loop`);
        _forceRelocate = true; _stallCount = 0;
      }
    } else {
      _stallKey = stallKey;
    }
  } finally {
    // Only clear shared state if THIS tick is still the current generation. If a
    // death/hard-reset happened while we were awaiting, a fresh loop already owns
    // _acting — leave it alone so we don't clobber it and cause overlap.
    if (myGen === _gen) { _acting = false; _currentGoal = null; }
  }

  if (myGen !== _gen) return; // superseded (death/reset during this tick) — bow out
  // A danger preempt jumps the queue; otherwise urgent re-decides fast, calm rests.
  const delay = _preempt ? 0 : (decision && decision.urgent ? URGENT_MS : CALM_MS);
  _preempt = false;
  scheduleNextGoal(bot, delay);
}

// A tiny, harmless goof: a hop/misstep, and once in a while a flustered line (rare,
// on the long floor, so it never spams). Purely cosmetic — never touches survival.
async function maybeFumble(bot) {
  const kind = pickFumble();
  console.log(`[MC] (fumble: ${kind})`);
  try {
    bot.setControlState('jump', true);
    setTimeout(() => { try { bot.setControlState('jump', false); } catch (_) {} }, 200);
  } catch (_) {}
  if (Math.random() < 0.25) await saySomething(bot, WorldEvents.fumble(bot, kind), { flavor: true });
}

async function runSkill(bot, d) {
  switch (d.goal) {
    case 'gather': {
      const before = countInv(bot);
      const got = await skills.gather(bot, d.target || 'log', 4);
      if (!got) {
        // nothing of that type in range — go looking instead of standing still
        console.log(`[MC]   no ${d.target || 'log'} nearby, wandering to find some`);
        await skills.explore(bot);
      }
      await reactToHaul(bot, before);
      return got;
    }
    case 'craft': {
      // smartCraft resolves & acquires the missing materials itself (mine stone,
      // smelt iron, craft planks…), one step per tick, instead of failing in a loop.
      const ok = await skills.smartCraft(bot, d.target || 'wooden_pickaxe');
      if (!ok) console.log('[MC]   craft step made no progress — will re-decide');
      return ok;
    }
    case 'mine':          return skills.mineDeeper(bot, d.target);
    case 'smelt': {
      const ok = await skills.smelt(bot, d.target || 'raw_iron', 8);
      if (!ok) console.log('[MC]   smelt fell short (fuel/furnace/input) — retry next tick');
      return ok;
    }
    case 'smelt_food':    return skills.smeltFood(bot);
    case 'goto':          return skills.goto(bot, d.target);
    case 'recover': {
      // death scatters drops over several blocks — walking to the spot and hoping
      // auto-pickup catches them left half her gear on the floor (round 17e).
      await skills.goto(bot, d.target);
      const before = totalItems(bot);
      await skills.collectDrops(bot, 16, 24); // wide, patient sweep
      const got = totalItems(bot) > before;
      console.log(`[MC] recovery sweep: ${got ? 'gear reclaimed' : 'nothing left to grab'}`);
      return got;
    }
    case 'follow':        return skills.follow(bot, d.target?.name);
    case 'go_home':       return skills.goHome(bot);
    case 'loot': {
      const before = totalItems(bot);
      const ok = await verbs.lootChest(bot);
      await saySomething(bot, WorldEvents.lootedChest(bot, totalItems(bot) > before));
      return ok;
    }
    case 'wool':          return verbs.getWool(bot);
    case 'spawner': {
      const ok = await skills.neutralizeSpawner(bot);
      if (ok) await saySomething(bot, WorldEvents.foundSpawner(bot));
      return ok;
    }
    case 'fight': {
      const ok = await skills.fight(bot, d.target);
      if (ok) await saySomething(bot, WorldEvents.killedMob(bot, d.target?.name || 'it'));
      return ok;
    }
    case 'flee':          return skills.flee(bot, d.target);
    case 'surface':       return skills.surface(bot);
    case 'wait': {
      // hold position — stop pathing and stand by (owner said wait / can't see him)
      try { bot.pathfinder.stop(); } catch (_) {}
      try { bot.clearControlStates(); } catch (_) {}
      return true;
    }
    case 'eat':           return skills.eat(bot);
    case 'hunt': {
      // find food: hunt animals, else forage plants. Track failures so the
      // last-resort hunger reset can kick in when she genuinely can't eat.
      let ok = await skills.hunt(bot);
      if (!ok) ok = await skills.forage(bot);
      _failedFoodSearches = ok ? 0 : _failedFoodSearches + 1;
      return ok;
    }
    case 'farm': {
      // sustainable food first (harvest/replant crops, MAKE a farm if she can,
      // breed animals), then hunt/forage
      let ok = await skills.farmCrops(bot);
      if (!ok) ok = await verbs.createFarm(bot); // till by water + plant (new verb)
      if (!ok) ok = await skills.breedAnimals(bot);
      if (!ok) ok = await skills.hunt(bot);
      if (!ok) ok = await skills.forage(bot);
      return ok;
    }
    case 'hunger_reset': {
      console.log('[MC] hunger reset: bedding here + dying to reset (last resort)');
      const ok = await skills.hungerReset(bot);
      _failedFoodSearches = 0;
      return ok;
    }
    case 'sleep':         return skills.sleep_in_bed(bot);
    case 'build_shelter': {
      const ok = await skills.buildShelter(bot);
      bot._shelterFails = ok ? 0 : (bot._shelterFails || 0) + 1;
      return ok;
    }
    case 'build_hut': {
      const ok = await skills.buildHut(bot);
      if (ok) {
        // her first REAL build commits this spot as home (base was just the spawn
        // default before — now go_home/dusk/deposits have somewhere true to aim)
        try {
          if (bot._wm && !(bot._wm.getBase() || {}).committed) {
            bot._wm.setBase(bot.entity.position, true);
            console.log('[MC] home committed at the new hut');
          }
        } catch (_) {}
      } else {
        await skills.buildShelter(bot); // fall back to a panic wall
      }
      return ok;
    }
    case 'place_torch': {
      const lit = await skills.lightArea(bot); // mob-proof the area
      if (!lit) return skills.placeTorch(bot);   // fall back to a single torch
      return lit;
    }
    case 'explore':
    default:              return skills.explore(bot);
  }
}

// Verified progression milestones — she only celebrates when she ACTUALLY has the
// item (checked against real inventory, once each). Fixes hollow/false commentary.
const _milestonesHit = new Set();
async function checkMilestones(bot) {
  const names = (bot.inventory.items() || []).map((i) => i.name);
  const has = (s) => names.some((n) => n.includes(s));
  const MILES = [
    ['stone_pickaxe', 'upgraded to stone tools'],
    ['_bed', 'got a bed — a real spawn point now'],
    ['iron_ingot', 'smelted your first iron'],
    ['iron_pickaxe', 'got iron tools'],
    ['shield', 'made a shield'],
    ['_chestplate', 'put on real armor'],
    ['diamond', 'found a diamond'],
    ['diamond_pickaxe', 'got diamond tools'],
  ];
  for (const [item, what] of MILES) {
    if (has(item) && !_milestonesHit.has(item)) {
      // only burn the milestone when she ACTUALLY got to say it — the speech floor
      // used to swallow first-diamond celebrations forever
      const spoke = await saySomething(bot, WorldEvents.milestone(bot, what));
      if (spoke) _milestonesHit.add(item);
      return; // one milestone per tick
    }
  }
}

// react when a gather turns up something worth reacting to (ore/valuables)
async function reactToHaul(bot, before) {
  const after = countInv(bot);
  for (const ore of ['diamond', 'gold', 'iron', 'coal', 'redstone', 'emerald']) {
    if ((after[ore] || 0) > (before[ore] || 0)) {
      await saySomething(bot, WorldEvents.foundOre(bot, ore));
      return;
    }
  }
}

function countInv(bot) {
  const c = {};
  for (const i of bot.inventory?.items() || []) {
    for (const ore of ['diamond', 'gold', 'iron', 'coal', 'redstone', 'emerald']) {
      if (i.name.includes(ore)) c[ore] = (c[ore] || 0) + i.count;
    }
  }
  return c;
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

  // Spatial memory, one map per server (survives reconnects).
  const safeHost = String(config.host || 'server').replace(/[^a-z0-9._-]/gi, '_');
  bot._wm = createWorldModel(path.resolve(__dirname, `../../data/minecraft_world_${safeHost}.json`));
  bot._wm.load();
  bot._social = createSocial();
  bot._motor = createMotor(bot); // body language: gaze, hops, curiosity pauses
  // control channel for cooperative skill interruption (skills.interrupted reads it)
  bot._ctl = { gen: 0, preempt: false };
  // remember when she last dug so a collect right after = her own mining, not a gift
  bot.on('diggingCompleted', () => { bot._lastDigAt = Date.now(); });

  // ── EARS (ARC S): sound perception — she reacts to what she HEARS, not just sees.
  // Aternos may deliver named or hardcoded (numeric) sound packets; handle both and
  // fail silent on anything unmappable.
  const soundName = (idOrName) => {
    if (typeof idOrName === 'string') return idOrName;
    try {
      const snd = require('minecraft-data')(bot.version).sounds[idOrName];
      return (snd && snd.name) || '';
    } catch (_) { return ''; }
  };
  let _lastSoundReactAt = 0;
  async function onSoundHeard(raw, position) {
    const name = soundName(raw);
    if (!name) return;
    const now = Date.now();
    // CREEPER FUSE — the one sound that must interrupt everything, instantly.
    if (name.includes('creeper.primed') || name.includes('tnt.primed')) {
      console.log(`[MC] ! HEARD ${name} — evasive`);
      _preempt = true;
      if (bot._ctl) bot._ctl.preempt = true; // break non-pathfinder loops too (smelt!)
      try { bot.pathfinder.stop(); } catch (_) {}
      if (position && bot._motor) bot._motor.glanceToward(position).catch(() => {});
      if (!_acting) { stopGoalLoop(); scheduleNextGoal(bot, 0); }
      return;
    }
    if (now - _lastSoundReactAt < 20_000) return; // everything below is throttled
    // mob sounds through a wall while underground → the wary "did you hear that"
    const mobSound = /entity\.(zombie|skeleton|spider|witch)\.(ambient|hurt)/.test(name);
    const caveSound = name.includes('ambient.cave');
    if ((mobSound || caveSound) && bot.entity && bot.entity.position && bot.entity.position.y < 50) {
      _lastSoundReactAt = now;
      if (position && bot._motor) bot._motor.glanceToward(position).catch(() => {});
      const what = caveSound ? 'something strange in the caves'
        : `a ${(name.match(/entity\.(\w+)\./) || [])[1] || 'mob'} through the wall`;
      await saySomething(bot, WorldEvents.heardSound(bot, what), { flavor: true });
    }
  }
  bot.on('soundEffectHeard', (name, position) => { onSoundHeard(name, position).catch(() => {}); });
  bot.on('hardcodedSoundEffectHeard', (id, category, position) => { onSoundHeard(id, position).catch(() => {}); });

  bot.once('spawn', async () => {
    console.log(`[MC] Spawned (version: ${bot.version})`);
    // Fresh connection — hard-reset ALL loop state. On a reconnect the previous
    // connection could leave _acting stuck true (an in-flight skill never finished),
    // which blocked the new loop forever ("she got kicked and won't come back").
    _gen++; _acting = false; _currentGoal = null; _preempt = false; _deathLoc = null;
    _lastNudge = 0; _actingSince = 0; _failedFoodSearches = 0; _stallCount = 0; _forceRelocate = false;
    _taskFrame = null; _lastDiscretionary = null; _prepAttempts = 0; _lastDirectorAt = 0;
    _projFailCount = 0; _parkedProjects.clear();
    bot._ctl = { gen: _gen, preempt: false };
    stopGoalLoop(); stopFastLoop();
    state.onSpawn();
    // Wait for chunks to actually stream in before acting (fixes the "spawned
    // but world is empty" desync), then report what she can really see.
    try {
      if (typeof bot.waitForChunksToLoad === 'function') {
        await Promise.race([bot.waitForChunksToLoad(), new Promise((r) => setTimeout(r, 15000))]);
      }
    } catch (_) {}
    setTimeout(() => {
      try {
        const below = bot.blockAt(bot.entity.position.offset(0, -1, 0));
        const log = bot.findBlock({ matching: (b) => b && b.name && b.name.includes('log'), maxDistance: 64 });
        const solids = bot.findBlocks({ matching: (b) => b && b.boundingBox === 'block', maxDistance: 12, count: 5 });
        console.log(`[MC] WORLD CHECK: standing_on=${below ? below.name : 'NULL'} | `
          + `nearest_log=${log ? log.name + '@' + Math.round(log.position.distanceTo(bot.entity.position)) + 'm' : 'NONE'} | `
          + `solids_within_12=${solids.length} | physics=${bot.physicsEnabled} | gamemode=${bot.game && bot.game.gameMode}`);
      } catch (e) { console.log('[MC] WORLD CHECK failed:', e.message); }
    }, 3000);
    // First time she sees this world, mark home where she stands.
    try {
      if (bot._wm && !bot._wm.getBase() && bot.entity && bot.entity.position) {
        bot._wm.setBase(bot.entity.position);
        console.log('[MC] home base set at spawn');
      }
    } catch (_) {}
    await saySomething(bot, WorldEvents.spawn(bot));
    // Start the fast body loop (perception + reflexes) and the slow captain loop.
    startFastLoop(bot);
    scheduleNextGoal(bot, 4_000); // chunks are already loaded above — get moving
  });

  bot.on('death', async () => {
    // Invalidate any in-flight tick (it's operating on her pre-death position) and
    // halt the loop cleanly so no stale loop keeps "playing" the dead body.
    _gen++; _acting = false; _currentGoal = null;
    _taskFrame = null; _lastDiscretionary = null; // never "resume" a pre-death task
    if (bot._ctl) bot._ctl.gen = _gen;            // cancel any in-flight skill loop
    stopGoalLoop();
    try { bot.pathfinder.stop(); } catch (_) {}
    // Remember the corpse spot so she can go reclaim her gear after respawn.
    // finite3 guard: position often reads NaN at the death instant on Aternos —
    // an unguarded clone gave "recovering drops at (NaN,69,NaN)" and wedged the
    // recovery loop while the killer camped her (round 16d death spiral).
    _deathLoc = bot.entity && finite3(bot.entity.position) ? bot.entity.position.clone() : null;
    // death-loop breaker: 3 deaths in 3 minutes = something is CAMPING the spot.
    // A real player abandons the corpse and regroups; she should too.
    const nowD = Date.now();
    bot._recentDeaths = (bot._recentDeaths || []).filter((t) => nowD - t < 3 * 60_000);
    bot._recentDeaths.push(nowD);
    if (bot._recentDeaths.length >= 3) {
      console.log('[MC] death spiral detected — abandoning the corpse run, relocating on respawn');
      _deathLoc = null;
      _forceRelocate = true;
    }
    // Death-cause learning: if she just died in/near lava, mark that spot lethal so
    // the world model steers her away from it next time.
    if (bot._wm && _deathLoc && bot._recentPeril
        && bot._recentPeril.cause === 'lava' && Date.now() - bot._recentPeril.at < 8000) {
      bot._wm.noteHazard(_deathLoc, 'lava'); bot._wm.save();
      console.log('[MC] learned: died to lava — marked that spot as a hazard');
    }
    // Use the derived cause if we have a recent one (was thrown away before).
    const cause = (bot._recentPeril && Date.now() - bot._recentPeril.at < 8000) ? bot._recentPeril.cause : 'unknown';
    await saySomething(bot, WorldEvents.death(bot, cause));
  });

  bot.on('respawn', () => {
    console.log('[MC] Respawned.');
    // Fresh generation — the new body owns the loop now.
    _gen++; _acting = false; _currentGoal = null;
    if (bot._ctl) bot._ctl.gen = _gen;
    stopGoalLoop();
    scheduleNextGoal(bot, 5_000); // she has a corpse to go loot
  });

  bot.on('chat', async (username, message) => {
    if (username === bot.username) return;
    // the human "I see you" beat — turn toward whoever is talking (when not busy)
    if (bot._motor) bot._motor.glanceAtPlayer(username).catch(() => {});
    // Owner co-op: if Koro-san gave a directive, act on it (preempts her project).
    if (isOwnerName(username)) {
      const cmd = parseOwnerCommand(message);
      if (cmd) { await applyOwnerCommand(bot, username, cmd); return; }
    }
    // Otherwise just react to chat — floor still applies so she won't spam back.
    await saySomething(bot, WorldEvents.playerChat(bot, username, message));
  });

  bot.on('entityHurt', async (entity) => {
    if (entity !== bot.entity) return;
    const me = bot.entity.position;
    // A player hit her? On this co-op server that's roughhousing, not combat — she
    // reacts socially and NEVER retaliates against a person.
    const player = bot.nearestEntity(e =>
      e.type === 'player' && e.username && e.position?.distanceTo(me) < 4);
    if (player) {
      bot._social.noteHit(player.username);
      if (bot._social.intentFor(player.username) === 'troll') {
        await saySomething(bot, WorldEvents.trolled(bot, player.username), { floorMs: OWNER_ACK_FLOOR_MS });
      }
      return;
    }
    // Otherwise: only claim "attacked by X" if there's an ACTUAL hostile in range.
    // Fall/drown/cactus/fire/starve fire entityHurt too — don't fabricate an attacker
    // (the QC/verification principle: don't assert what didn't happen).
    const attacker = bot.nearestEntity(e =>
      e && HOSTILE_MOBS.has((e.name || '').toLowerCase())
      && e.position?.distanceTo(me) < 6);
    if (attacker) await saySomething(bot, WorldEvents.mobAttack(bot, attacker.name));
    // else: environmental damage — no false "something hit me" line.
  });

  bot.on('playerCollect', async (collector, itemEntity) => {
    if (collector !== bot.entity) return;
    // record the ITEM TYPE, not the entity name (always 'item' — her "collected:"
    // memory was permanently 'itemx37')
    let name = null;
    try {
      const dropped = itemEntity && itemEntity.getDroppedItem && itemEntity.getDroppedItem();
      name = dropped && dropped.name;
    } catch (_) {}
    if (name) state.addResources([{ name, count: 1 }]);
    // Gift heuristic: she collected an item, a player is right there, and she didn't
    // just mine it herself → someone tossed it to her.
    const recentlyDug = bot._lastDigAt && Date.now() - bot._lastDigAt < 2500;
    if (recentlyDug) return;
    const giver = bot.nearestEntity(e =>
      e.type === 'player' && e.username && e.position?.distanceTo(bot.entity.position) < 6);
    if (giver) {
      bot._social.noteGift(giver.username);
      if (bot._motor) bot._motor.glanceAtEntity(giver).catch(() => {}); // look at who gave it
      await saySomething(bot, WorldEvents.gift(bot, giver.username), { floorMs: OWNER_ACK_FLOOR_MS });
    }
  });

  bot.on('kicked', (reason) => {
    // reason can be a string OR an object — stringify so the log shows the REAL
    // cause instead of "[MC] Kicked: {" (round 16: the kick cause was unreadable)
    let readable;
    try {
      const parsed = typeof reason === 'string' ? JSON.parse(reason) : reason;
      readable = parsed.text || parsed.translate || JSON.stringify(parsed);
      if (parsed.extra) readable += parsed.extra.map(e => e.text || '').join('');
    } catch (_) { readable = String(reason); }
    console.error('[MC] Kicked:', readable);
    if (/duplicate_login|logged in from another location/i.test(readable)) {
      console.error('[MC] ^ duplicate login — ANOTHER process is connecting with her username. Kill the extra bot instance.');
    }
    _gen++; _acting = false; _currentGoal = null;
    _taskFrame = null; _lastDiscretionary = null;
    if (bot._ctl) bot._ctl.gen = _gen;
    stopGoalLoop();
    stopFastLoop();
  });

  bot.on('error', (err) => {
    console.error('[MC] Bot error:', err.message);
  });

  bot.on('end', (reason) => {
    if (reason) console.log('[MC] Disconnect reason:', reason);
    console.log('[MC] Disconnected.');
    // Invalidate in-flight ticks + free the loop so the reconnected bot starts clean
    // (a stuck _acting from a mid-skill disconnect was blocking the new connection).
    _gen++; _acting = false; _currentGoal = null;
    if (bot._ctl) bot._ctl.gen = _gen;
    stopGoalLoop();
    stopFastLoop();
  });

  return bot;
}

module.exports = { createBot };
