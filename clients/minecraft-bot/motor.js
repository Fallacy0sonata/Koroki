'use strict';

// Motor humanization (owner ask 2026-07-13: "we haven't added any human behavior at
// all — she walks always straight, robotic"). Real players' heads and bodies are
// ALIVE under their goals: they glance at whoever's talking, watch a passing sheep,
// scan the horizon when they stop, hop while sprinting, pause mid-journey to look
// around. None of that changes WHAT she does — it changes how it reads. This module
// owns those micro-behaviors; bot.js wires the triggers.
//
// Design rules:
//  - Never fight the pathfinder's steering: no head overrides while pathing/digging
//    (mineflayer re-aims every physics tick — a glance would just stutter). Gaze
//    happens at natural pause points: idle, leg boundaries, reactions.
//  - Never touch survival: no artificial latency on reflexes, no fidgets in combat.
//  - Pure helpers are rng-injected and unit-testable; the driver is thin.

// ── pure helpers ──────────────────────────────────────────────────────────────

// Humans don't hold a compass line — jitter a travel heading by up to ±maxRad.
function jitterHeading(heading, rng = Math.random, maxRad = 0.22) {
  return heading + (rng() * 2 - 1) * maxRad;
}

// Should she take a curiosity pause at this leg boundary? (~1 in 4, calm only)
function shouldPause(calm, rng = Math.random) {
  return !!calm && rng() < 0.25;
}

// Should she hop this fast-tick while sprinting on open ground? Tuned so it happens
// every ~20-30 s of continuous running — playful, not seizure-y.
function shouldHop(calm, onGround, inWater, rng = Math.random) {
  return !!calm && !!onGround && !inWater && rng() < 0.005;
}

// Pick what an idle person looks at: a player if one is close (people are the most
// interesting thing in the world), else an animal, else a random horizon point.
// Returns { kind: 'entity'|'horizon', ... } (pure; entities passed in as plain data).
function pickIdleGaze(players, animals, rng = Math.random) {
  if (players && players.length && rng() < 0.65) return { kind: 'entity', which: 'player' };
  if (animals && animals.length && rng() < 0.5) return { kind: 'entity', which: 'animal' };
  return { kind: 'horizon', yaw: rng() * Math.PI * 2 };
}

// ── live driver ───────────────────────────────────────────────────────────────

const ANIMALS = new Set(['cow', 'pig', 'chicken', 'sheep', 'rabbit', 'mooshroom', 'cat', 'wolf', 'fox', 'horse', 'donkey', 'bee', 'axolotl', 'frog']);

function createMotor(bot, rng = Math.random) {
  let lastGazeAt = 0;
  let lastHopAt = 0;

  const busy = () => {
    try {
      if (bot.targetDigBlock) return true;                                   // mid-dig — head is the tool
      if (bot.pathfinder && bot.pathfinder.isMoving && bot.pathfinder.isMoving()) return true; // pathing steers the head
    } catch (_) {}
    return false;
  };

  // Smooth look at a world point (never force-snap — snapping is the robot tell).
  async function glanceAt(point) {
    if (!point || busy()) return false;
    try { await bot.lookAt(point, false); return true; } catch (_) { return false; }
  }

  // Look at an entity's head — the human "I see you" beat for chat/gifts/arrivals.
  async function glanceAtEntity(ent) {
    if (!ent || !ent.position) return false;
    return glanceAt(ent.position.offset(0, (ent.height || 1.6) * 0.9, 0));
  }

  async function glanceAtPlayer(username) {
    const rec = bot.players && bot.players[username];
    return glanceAtEntity(rec && rec.entity);
  }

  // Turn toward a position even without an entity (sound sources).
  async function glanceToward(pos) { return glanceAt(pos); }

  // Idle gaze — called from the fast loop; self-limits to every ~2.5-5 s and only
  // when she's genuinely idle (no skill, no path, no dig, no threat handled upstream).
  async function idleTick() {
    const now = Date.now();
    if (now - lastGazeAt < 2500 + rng() * 2500) return;
    if (busy()) return;
    lastGazeAt = now;
    const me = bot.entity && bot.entity.position;
    if (!me) return;
    const near = Object.values(bot.entities || {}).filter((e) =>
      e && e !== bot.entity && e.isValid && e.position && e.position.distanceTo(me) < 16);
    const players = near.filter((e) => e.type === 'player');
    const animals = near.filter((e) => ANIMALS.has((e.name || '').toLowerCase()));
    const pick = pickIdleGaze(players, animals, rng);
    if (pick.kind === 'entity') {
      const pool = pick.which === 'player' ? players : animals;
      const ent = pool[Math.floor(rng() * pool.length)];
      await glanceAtEntity(ent);
    } else {
      // gaze at a horizon point in a random direction (eye-height, far away)
      const p = me.offset(Math.cos(pick.yaw) * 24, 1.2, Math.sin(pick.yaw) * 24);
      await glanceAt(p);
    }
  }

  // Curiosity pause at a travel-leg boundary: stop a beat, look one way, then the
  // other — the classic "player taking in the landscape" beat. Caller gates on calm.
  async function curiosityPause() {
    const me = bot.entity && bot.entity.position;
    if (!me) return;
    const yaw = rng() * Math.PI * 2;
    await glanceAt(me.offset(Math.cos(yaw) * 20, 1.5, Math.sin(yaw) * 20));
    await new Promise((r) => setTimeout(r, 500 + rng() * 800));
    const yaw2 = yaw + Math.PI * (0.4 + rng() * 0.5);
    await glanceAt(me.offset(Math.cos(yaw2) * 20, 1.0, Math.sin(yaw2) * 20));
    await new Promise((r) => setTimeout(r, 300 + rng() * 600));
  }

  // Playful sprint-hop — fast-loop calls this while she's traveling and calm.
  function travelHop(onGround, inWater, calm) {
    const now = Date.now();
    if (now - lastHopAt < 6000) return;
    if (!shouldHop(calm, onGround, inWater, rng)) return;
    lastHopAt = now;
    try {
      bot.setControlState('jump', true);
      setTimeout(() => { try { bot.setControlState('jump', false); } catch (_) {} }, 180);
    } catch (_) {}
  }

  return { glanceAt, glanceAtEntity, glanceAtPlayer, glanceToward, idleTick, curiosityPause, travelHop };
}

module.exports = { createMotor, jitterHeading, shouldPause, shouldHop, pickIdleGaze, ANIMALS };
