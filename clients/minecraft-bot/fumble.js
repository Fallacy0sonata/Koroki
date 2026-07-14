'use strict';

// Bounded fumble generator (Phase 4, 2026-07-13). Owner: "genuinely strong but
// fumbles something stupid sometimes" — the contrast makes her read as ALIVE, not
// robotic. Strictly rare, non-fatal, cosmetic: a misstep, a wrong-way glance. Never
// survival-critical self-sabotage — the caller only rolls when she's safe and calm.

const FUMBLES = [
  'missed a step',
  'looked the wrong way',
  'blanked on what she was doing',
  'walked into the doorframe',
  'fumbled the jump',
];

// low per-tick chance so it's an occasional garnish, not a personality
function rollFumble(rng = Math.random, chance = 0.015) { return rng() < chance; }
function pickFumble(rng = Math.random) { return FUMBLES[Math.floor(rng() * FUMBLES.length)]; }

module.exports = { rollFumble, pickFumble, FUMBLES };
