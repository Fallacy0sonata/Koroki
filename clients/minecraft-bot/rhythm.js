'use strict';

// Rhythm layer — the human day-structure real players run on (gap analysis
// 2026-07-13, docs/minecraft_player_gap_analysis.md ARC H). Players live in loops:
// kit up → expedition → "time to go home" triggers → return → dump → smelt →
// out again, with "be home by dusk" as the daily heartbeat. This module is the PURE
// decision core: given a plain context it returns a rhythm decision or null. The
// arbiter (bot.js) consults it AFTER reflexes/owner commands and BEFORE projects —
// rhythm shapes the day, reflexes guard the seconds, projects fill the hours.
//
// ctx = {
//   timeLabel, y,                 // 'dawn'|'morning'|...|'night', her altitude
//   baseSet, distToBase, atBase,  // home info (atBase = within ~16 blocks)
//   slotsUsed,                    // inventory slots occupied (0..36)
//   torches, foodUnits,           // stock counts
//   rawFood,                      // uncooked meat/potato count (smeltable food)
//   hasBed, hasFurnaceNear,       // capabilities at hand
//   sticks, coal, logs,           // torch-craft feasibility
// }

const UNDERGROUND_Y = 50;   // below this ≈ caving/mining (no dusk pull underground)
const HOME_FULL_SLOTS = 30; // "inventory full of keepables" → haul home
const HOME_MAX_DIST = 150;  // don't cross the world to deposit — beyond this, field-chest
const DUSK_MIN_DIST = 12;   // already home-ish → no dusk trip (matches hasShelter radius)

function underground(ctx) { return ctx.y != null && ctx.y < UNDERGROUND_Y; }

// The daily rhythm decision (or null = no rhythm pressure, let projects run).
function rhythmDecision(ctx) {
  if (!ctx) return null;

  // 1. DUSK — be home before dark (the most human-visible habit in the game).
  //    Only from the surface: underground, night is irrelevant — keep mining.
  if (ctx.timeLabel === 'dusk' && ctx.baseSet && !underground(ctx)
      && ctx.distToBase > DUSK_MIN_DIST && ctx.distToBase <= HOME_MAX_DIST * 2) {
    return { goal: 'go_home', reason: 'dusk — heading home before dark', urgent: false };
  }

  // 2. HAUL HOME — inventory is full of keepables: take it home if home is near
  //    enough, else the every-tick nearest-chest deposit (skills) will handle it.
  if (ctx.slotsUsed >= HOME_FULL_SLOTS && ctx.baseSet
      && ctx.distToBase > DUSK_MIN_DIST && ctx.distToBase <= HOME_MAX_DIST) {
    return { goal: 'go_home', reason: 'pack is full — hauling it home', urgent: false };
  }

  // 3. NIGHT AT HOME with a bed (placed or carried) → go to bed. The shelter
  //    reflex no longer fires at home (hasShelter is true there), so the rhythm
  //    layer owns the bedtime habit.
  if (ctx.timeLabel === 'night' && ctx.atBase && (ctx.hasBed || ctx.bedNearby)) {
    return { goal: 'sleep', reason: 'night at home — going to bed', urgent: false };
  }

  // 4. NIGHT INDOOR SHIFT — at home at night with no bed: do useful indoor work
  //    (cook the raw food) instead of wandering into the dark.
  if (ctx.timeLabel === 'night' && ctx.atBase
      && ctx.rawFood > 0 && ctx.hasFurnaceNear) {
    return { goal: 'smelt_food', reason: 'night at home — cooking the raw food', urgent: false };
  }

  // 5. HOLED UP in a panic shelter away from home at night → stay put until dawn.
  //    Without this she'd break OUT of her own wall to chase a project into the dark.
  if (ctx.timeLabel === 'night' && !ctx.atBase && ctx.enclosed) {
    return { goal: 'wait', reason: 'holed up until morning', urgent: false };
  }

  // 6. PANTRY EMPTY — restock BEFORE the starving reflex forces it (round 17: she
  //    only ever looked for food at the starvation wall). Not at night (suicide),
  //    and hunger must have started dipping so a fresh spawn isn't instantly bossy.
  if (ctx.foodUnits === 0 && ctx.hunger != null && ctx.hunger <= 14
      && ctx.timeLabel !== 'night' && ctx.timeLabel !== 'dusk') {
    return { goal: 'farm', reason: 'out of food — restocking before it bites', urgent: false };
  }

  return null;
}

// Can she craft torches right now-ish? (coal+sticks direct, or logs → the resolver
// can charcoal-and-stick its way there.)
function canMakeTorches(ctx) {
  return (ctx.coal > 0 && ctx.sticks > 0) || ctx.logs > 0;
}

// Pre-expedition kit gate (the caving-checklist habit). Called when the next goal
// is a DEEP mining trip; returns a prep decision or null (kit is fine / can't prep).
// `attempts` caps redirects so a barren start can never hard-block progression.
const KIT_TORCHES = 12;
const KIT_FOOD = 5;
function prepForMining(ctx, attempts = 0) {
  if (!ctx || attempts >= 3) return null;
  if (ctx.torches < KIT_TORCHES && canMakeTorches(ctx)) {
    return { goal: 'craft', target: 'torch', reason: `kit check: only ${ctx.torches} torches — making more first`, urgent: false };
  }
  if (ctx.foodUnits < KIT_FOOD && ctx.rawFood > 0 && ctx.hasFurnaceNear) {
    return { goal: 'smelt_food', reason: 'kit check: cooking food before the trip', urgent: false };
  }
  if (ctx.foodUnits < 2) {
    return { goal: 'farm', reason: 'kit check: no food at all — stocking up first', urgent: false };
  }
  return null;
}

// "Time to go home" mid-expedition triggers (any one suffices) — the field version,
// evaluated while a mining trip is running. Pure so the thresholds are testable.
function shouldHeadHome(ctx) {
  if (!ctx || !ctx.baseSet || ctx.distToBase > HOME_MAX_DIST) return false;
  if (ctx.slotsUsed >= HOME_FULL_SLOTS) return true;
  if (underground(ctx) && ctx.torches === 0 && !canMakeTorches(ctx)) return true;
  if (ctx.foodUnits === 0 && ctx.rawFood === 0) return true;
  return false;
}

module.exports = {
  rhythmDecision, prepForMining, shouldHeadHome, canMakeTorches,
  UNDERGROUND_Y, HOME_FULL_SLOTS, HOME_MAX_DIST,
};
