'use strict';
// Rhythm layer — pure decision tests (no server): dusk homing, haul-home triggers,
// night indoor shift, the pre-expedition kit gate.

const assert = require('assert');
const { rhythmDecision, prepForMining, shouldHeadHome, canMakeTorches } = require('./rhythm');

let pass = 0;
const t = (name, cond) => { assert.ok(cond, name); console.log(`  ok  ${name}`); pass++; };

const base = {
  timeLabel: 'afternoon', y: 70, baseSet: true, distToBase: 60, atBase: false,
  slotsUsed: 10, torches: 20, foodUnits: 8, hunger: 18, rawFood: 0, hasBed: false, bedNearby: false,
  hasFurnaceNear: false, sticks: 4, coal: 4, logs: 2, enclosed: false,
};
const ctx = (over = {}) => ({ ...base, ...over });

// ── dusk homing ───────────────────────────────────────────────────────────────
t('dusk on the surface, away from home -> go_home',
  (rhythmDecision(ctx({ timeLabel: 'dusk' })) || {}).goal === 'go_home');
t('dusk but UNDERGROUND -> keep mining (night is irrelevant below)',
  rhythmDecision(ctx({ timeLabel: 'dusk', y: -20 })) === null);
t('dusk but already home-ish -> no trip',
  rhythmDecision(ctx({ timeLabel: 'dusk', distToBase: 10 })) === null);
t('dusk with no base set -> nothing to head to',
  rhythmDecision(ctx({ timeLabel: 'dusk', baseSet: false })) === null);
t('dusk but home is absurdly far -> no cross-world trek',
  rhythmDecision(ctx({ timeLabel: 'dusk', distToBase: 900 })) === null);

// ── haul home when full ───────────────────────────────────────────────────────
t('pack full near home -> go_home to deposit',
  (rhythmDecision(ctx({ slotsUsed: 31 })) || {}).goal === 'go_home');
t('pack full but home too far -> field-chest handles it (null)',
  rhythmDecision(ctx({ slotsUsed: 31, distToBase: 400 })) === null);
t('pack fine -> no rhythm pressure', rhythmDecision(ctx()) === null);

// ── night at home ─────────────────────────────────────────────────────────────
t('night at home, raw food + furnace, no bed -> cook (indoor shift)',
  (rhythmDecision(ctx({ timeLabel: 'night', atBase: true, distToBase: 5, rawFood: 4, hasFurnaceNear: true })) || {}).goal === 'smelt_food');
t('night at home WITH a bed in pack -> go to bed',
  (rhythmDecision(ctx({ timeLabel: 'night', atBase: true, distToBase: 5, rawFood: 4, hasFurnaceNear: true, hasBed: true })) || {}).goal === 'sleep');
t('night at home, bed PLACED nearby -> go to bed',
  (rhythmDecision(ctx({ timeLabel: 'night', atBase: true, distToBase: 5, bedNearby: true })) || {}).goal === 'sleep');
t('night AWAY from home with bed -> rhythm quiet (reflex handles the field)',
  rhythmDecision(ctx({ timeLabel: 'night', atBase: false, distToBase: 80, hasBed: true })) === null);
t('night, walled into a panic shelter away from home -> hold until dawn',
  (rhythmDecision(ctx({ timeLabel: 'night', atBase: false, distToBase: 80, enclosed: true })) || {}).goal === 'wait');
t('DAY while enclosed -> no hold (break out and play)',
  rhythmDecision(ctx({ timeLabel: 'morning', enclosed: true })) === null);

// ── pantry management: restock BEFORE the starvation wall ─────────────────────
t('no food + hunger dipping (day) -> restock now',
  (rhythmDecision(ctx({ foodUnits: 0, hunger: 12 })) || {}).goal === 'farm');
t('no food but hunger still fine -> not yet',
  rhythmDecision(ctx({ foodUnits: 0, hunger: 18 })) === null);
t('food in the pack -> no restock pressure',
  rhythmDecision(ctx({ foodUnits: 4, hunger: 12 })) === null);
t('no food + hungry but NIGHT -> wait for daylight to forage',
  rhythmDecision(ctx({ timeLabel: 'night', foodUnits: 0, hunger: 12 })) === null);

// ── kit gate (the caving checklist) ───────────────────────────────────────────
t('low torches + can craft -> make torches first',
  (prepForMining(ctx({ torches: 3 })) || {}).target === 'torch');
t('low torches, NOTHING to make them from -> no redirect (mine anyway)',
  prepForMining(ctx({ torches: 3, coal: 0, sticks: 0, logs: 0, foodUnits: 8 })) === null);
t('kit fine -> no prep', prepForMining(ctx()) === null);
t('no food at all -> stock up first',
  (prepForMining(ctx({ foodUnits: 0, rawFood: 0 })) || {}).goal === 'farm');
t('raw food + furnace near -> cook before the trip',
  (prepForMining(ctx({ foodUnits: 2, rawFood: 5, hasFurnaceNear: true })) || {}).goal === 'smelt_food');
t('prep attempts capped (never blocks progression forever)',
  prepForMining(ctx({ torches: 0 }), 3) === null);

// ── mid-trip go-home triggers ─────────────────────────────────────────────────
t('inventory full -> head home', shouldHeadHome(ctx({ slotsUsed: 31 })) === true);
t('out of torches underground, can’t craft -> head home',
  shouldHeadHome(ctx({ y: -30, torches: 0, coal: 0, sticks: 0, logs: 0 })) === true);
t('out of food entirely -> head home',
  shouldHeadHome(ctx({ foodUnits: 0, rawFood: 0 })) === true);
t('kit healthy -> stay out', shouldHeadHome(ctx()) === false);
t('no base -> nowhere to head', shouldHeadHome(ctx({ baseSet: false, slotsUsed: 33 })) === false);

// ── torch feasibility ─────────────────────────────────────────────────────────
t('coal+sticks -> torches yes', canMakeTorches(ctx()) === true);
t('logs alone -> torches possible via charcoal', canMakeTorches(ctx({ coal: 0, sticks: 0, logs: 3 })) === true);
t('nothing -> no torches', canMakeTorches(ctx({ coal: 0, sticks: 0, logs: 0 })) === false);

console.log(`\nRHYTHM: ${pass} PASS`);
