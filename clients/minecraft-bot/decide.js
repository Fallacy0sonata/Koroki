'use strict';

// Layer 2 — Survival reflexes (the "instincts" layer, 2026-07-11 owner arc, split
// out from the goal policy in the Phase 1 mind rebuild 2026-07-13).
//
// This is the BODY's startle/survival response: given a plain snapshot it returns a
// hard override when her life or basic needs are at stake, else null. It is pure +
// testable without a live server. A reflex ALWAYS wins over discretionary work —
// she never mines coal while a creeper eats her. When it returns null, the intent
// arbiter (bot.js) is free to run an owner command or a project (Layer 3). The
// tech-tree progression that used to live here now lives in projects.js.

const { shouldEngage, isRanged } = require('./combat');

const ADJACENT_M = 4;      // a hostile this close = immediate threat
const THREAT_M = 12;       // a hostile this close at night = get safe
const CREEPER_M = 7;       // creepers DETONATE at range — back off well before contact
const RANGED_M = 12;       // a ranged attacker (guardian/skeleton/blaze) hits from afar
const LOW_HP_FLEE_M = 16;  // when hurt, only FLEE if a mob is within this — else heal
const CRIT_HP = 6;
const HEAL_HP = 14;        // hurt (not critical): eat to enable regen when it's safe
const STARVING = 6;

// snapshot = {
//   hp, food, timeLabel ('night'|'dusk'|...), raining,
//   hostiles: [{name, dist, dir}], players: [{name, dist, dir}],
//   inv: { logIsh, plankIsh, stoneIsh, foodItems, hasWeapon, hasPickaxe, hasBed, torches },
//   hasShelter (bool)
// }
// returns { goal, target, reason, urgent } when a reflex fires, else null.
function survivalReflex(snapshot) {
  const s = snapshot || {};
  const inv = s.inv || {};
  const hostiles = (s.hostiles || []).slice().sort((a, b) => a.dist - b.dist);
  const nearHostile = hostiles[0] || null;
  const night = s.timeLabel === 'night' || s.timeLabel === 'dusk';

  // 0. DROWNING — beats everything. Air runs out faster than any mob kills, and
  //    in a flooded cave "swim up" hits the ceiling — the surface skill knows the
  //    escapes (open water up / back to last air / nearest air pocket).
  //    (water-cave drowning death, live round 16)
  if (s.headInWater && s.oxygen != null && s.oxygen <= 12) {
    return { goal: 'surface', target: null, reason: 'running out of air', urgent: true };
  }

  // 1. CRITICAL HEALTH — survive. Flee ONLY if a mob is actually near; otherwise
  //    running is pointless — the fix is to HEAL (eat → natural regen), not to sprint
  //    around. If she can't eat (food full) and nothing's chasing, she lies low.
  if (s.hp != null && s.hp <= CRIT_HP) {
    if (nearHostile && nearHostile.dist <= LOW_HP_FLEE_M) {
      return { goal: 'flee', target: nearHostile, reason: 'critical hp + mob near', urgent: true };
    }
    if (inv.foodItems > 0 && (s.food == null || s.food < 20)) {
      return { goal: 'eat', target: null, reason: 'critical hp, eat to heal', urgent: true };
    }
    if (s.food != null && s.food < 18) {
      return { goal: 'hunt', target: null, reason: 'critical hp, need food to regen', urgent: true };
    }
    return { goal: 'wait', target: null, reason: 'critical hp, lying low to regen', urgent: false };
  }

  // 2. CREEPER — decide by confidence. Strong enough → hit-and-run (skills.fight
  //    handles the back-off-so-it-can't-detonate dance); outmatched → back off.
  const creeper = hostiles.find((h) => h.name === 'creeper' && h.dist <= CREEPER_M);
  if (creeper) {
    return shouldEngage(s, creeper)
      ? { goal: 'fight', target: creeper, reason: 'creeper — hit and run', urgent: true }
      : { goal: 'flee', target: creeper, reason: 'creeper, not confident — back off', urgent: true };
  }

  // 3. IMMEDIATE THREAT — a hostile is on top of her. Confidence decides: fight if
  //    she's strong enough for THIS mob, else flee (don't trade into a death).
  if (nearHostile && nearHostile.dist <= ADJACENT_M) {
    return shouldEngage(s, nearHostile)
      ? { goal: 'fight', target: nearHostile, reason: 'confident, engage', urgent: true }
      : { goal: 'flee', target: nearHostile, reason: 'outmatched, flee', urgent: true };
  }

  // 4. NIGHT DANGER — hostile approaching in the dark: get safe (handles ranged too).
  //    Minecraft REJECTS sleeping with a monster within ~8 blocks — picking 'sleep'
  //    there just spam-clicks the bed while a skeleton shoots her (audit 2026-07-13).
  if (night && nearHostile && nearHostile.dist <= THREAT_M) {
    if ((inv.hasBed || s.bedNearby) && nearHostile.dist > 8) {
      return { goal: 'sleep', target: null, reason: 'night threat, sleep it away', urgent: true };
    }
    return { goal: 'build_shelter', target: null, reason: 'night threat, wall up', urgent: true };
  }

  // 4b. RANGED ATTACKER (daytime) — a guardian/skeleton/blaze hits her from afar
  //     (the guardian death: it lasered her from ~15m while she swam, oblivious).
  //     At night rule 4 already handled it; by day, react before she's in the beam.
  const rangedThreat = hostiles.find((h) => isRanged(h.name) && h.dist <= RANGED_M);
  if (rangedThreat) {
    return shouldEngage(s, rangedThreat)
      ? { goal: 'fight', target: rangedThreat, reason: 'ranged attacker, close in', urgent: true }
      : { goal: 'flee', target: rangedThreat, reason: 'ranged attacker, get out of range', urgent: true };
  }

  // 5. STARVING — eat if she can, else go find food.
  if (s.food != null && s.food <= STARVING) {
    if (inv.foodItems > 0) return { goal: 'eat', target: null, reason: 'starving', urgent: true };
    return { goal: 'hunt', target: null, reason: 'starving, no food', urgent: true };
  }

  // 6. NIGHTFALL, exposed — shelter/sleep before the mobs come. (Important but not
  //    "urgent": it should still beat discretionary projects, which the arbiter
  //    guarantees by consulting reflexes first.)
  if (night && !s.hasShelter) {
    if (inv.hasBed || s.bedNearby) return { goal: 'sleep', target: null, reason: 'night, sleep', urgent: false };
    return { goal: 'build_shelter', target: null, reason: 'night, no shelter', urgent: false };
  }

  // 7. HURT (not critical) + safe → eat to enable regen instead of pressing on while
  //    bleeding. Only when no mob is close (a near mob is handled by the rules above).
  const mobNear = nearHostile && nearHostile.dist <= LOW_HP_FLEE_M;
  if (!mobNear && s.hp != null && s.hp <= HEAL_HP && inv.foodItems > 0 && (s.food == null || s.food < 18)) {
    return { goal: 'eat', target: null, reason: 'hurt — eat to heal up', urgent: false };
  }

  // 8. SOFT NEED — top up food when safe and it's dipping (not starving yet), so
  //    hunger never creeps to critical mid-task. Only when nothing hostile is near.
  if (!nearHostile && s.food != null && s.food <= 12 && inv.foodItems > 0) {
    return { goal: 'eat', target: null, reason: 'topping up food', urgent: false };
  }

  return null; // no reflex — the arbiter runs an owner command or a project
}

const VALID_GOALS = new Set([
  'gather', 'goto', 'follow', 'fight', 'flee', 'build_shelter', 'build_hut',
  'eat', 'hunt', 'forage', 'farm', 'hunger_reset', 'sleep', 'explore', 'mine', 'smelt', 'place_torch', 'craft', 'wait',
  'go_home', 'loot', 'smelt_food', 'wool', 'surface', 'spawner', 'recover',
]);
function isValidGoal(g) { return VALID_GOALS.has(g); }

module.exports = { survivalReflex, isValidGoal, VALID_GOALS, ADJACENT_M, THREAT_M, CREEPER_M, RANGED_M };
