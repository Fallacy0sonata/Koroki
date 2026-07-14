'use strict';

// Combat helpers (Phase 3, 2026-07-13). The PURE decision logic lives here so it's
// testable without a server; the motor routine (approach, jump-crits, strafe,
// retreat) lives in skills.fight and calls these. Owner target: "genuinely strong"
// combat — a bot has perfect entity tracking, so the craft here is picking the right
// fight and knowing when to break off, not aim.

const RANGED = new Set([
  'skeleton', 'stray', 'pillager', 'witch',
  'guardian', 'elder_guardian', 'blaze', 'ghast', 'shulker', // attack from a distance
]);

// Per-mob combat doctrine. danger = how much fighting power she needs to take it on
// (see confidence()); style = how skills.fight approaches it.
//   melee  — stand in and trade with jump-crits
//   rush   — close the gap fast (ranged mobs are weak up close; don't eat arrows)
//   hitrun — creeper: hit once, back off out of blast range, let the fuse reset, repeat
//   avoid  — never melee (enderman/ghast) → shouldEngage returns false, she flees/dodges
const MOB = {
  // overworld
  creeper: { danger: 4, style: 'hitrun' },
  skeleton: { danger: 3, style: 'rush' },  // rush + shield (ranged)
  stray: { danger: 3, style: 'rush' },
  zombie: { danger: 2, style: 'melee' },
  husk: { danger: 2, style: 'melee' },
  zombie_villager: { danger: 2, style: 'melee' },
  drowned: { danger: 3, style: 'melee' },
  spider: { danger: 2, style: 'melee' },
  cave_spider: { danger: 3, style: 'melee' },
  witch: { danger: 4, style: 'rush' },
  slime: { danger: 2, style: 'melee' },
  phantom: { danger: 3, style: 'melee' },
  silverfish: { danger: 2, style: 'melee' },
  pillager: { danger: 3, style: 'rush' },
  vindicator: { danger: 4, style: 'melee' },
  evoker: { danger: 5, style: 'rush' },
  ravager: { danger: 7, style: 'avoid' },      // too strong early
  guardian: { danger: 4, style: 'rush' },
  elder_guardian: { danger: 7, style: 'avoid' },
  enderman: { danger: 5, style: 'avoid' },     // don't provoke — flee
  warden: { danger: 10, style: 'avoid' },      // NEVER fight — sneak away
  // nether / end
  blaze: { danger: 4, style: 'rush' },
  ghast: { danger: 5, style: 'avoid' },        // fireball — deflect is future; flee
  magma_cube: { danger: 3, style: 'melee' },
  wither_skeleton: { danger: 5, style: 'melee' },
  piglin: { danger: 3, style: 'melee' },
  piglin_brute: { danger: 6, style: 'avoid' },
  hoglin: { danger: 4, style: 'melee' },
  zoglin: { danger: 5, style: 'melee' },
  shulker: { danger: 3, style: 'rush' },
  ender_dragon: { danger: 10, style: 'avoid' },
  wither: { danger: 10, style: 'avoid' },
};
const DEFAULT_MOB = { danger: 2, style: 'melee' };
function mobInfo(name) { return MOB[name] || DEFAULT_MOB; }

function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

// Her fighting power right now (0 ≈ helpless, ~5 ≈ well-equipped and healthy).
function confidence(snap) {
  const inv = (snap && snap.inv) || {};
  let c = 0;
  if (inv.hasWeapon) c += 2;
  if (inv.hasShield) c += 1;
  if (snap && snap.hp != null) c += clamp((snap.hp - 10) / 5, -2, 2);
  return c;
}

// Fight this mob, or flee? Fight only if strong enough FOR THIS mob; never brawl a
// dangerous mob bare-handed, never melee an "avoid" mob.
function shouldEngage(snap, mob) {
  const info = mobInfo(mob && mob.name);
  if (info.style === 'avoid') return false;
  const inv = (snap && snap.inv) || {};
  if (info.danger >= 3 && !inv.hasWeapon) return false;
  return confidence(snap) >= info.danger;
}

// Which hostile to engage: a ranged mob shooting from range is prioritized (it
// whittles you while you swing at something else); otherwise the nearest.
function bestTarget(hostiles) {
  const live = (hostiles || []).filter((h) => h && h.dist != null);
  if (!live.length) return null;
  return live.slice().sort((a, b) => {
    const ar = RANGED.has(a.name) ? 1 : 0;
    const br = RANGED.has(b.name) ? 1 : 0;
    if (ar !== br) return br - ar;   // ranged first
    return a.dist - b.dist;          // then nearest
  })[0];
}

// Break off when badly hurt — living to fight again beats a trade she loses.
function shouldRetreat(hp) { return hp != null && hp <= 7; }

function isRanged(name) { return RANGED.has(name); }

module.exports = { bestTarget, shouldRetreat, isRanged, RANGED, mobInfo, confidence, shouldEngage, MOB };
