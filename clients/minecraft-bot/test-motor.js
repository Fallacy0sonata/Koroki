'use strict';
// Motor humanization — pure-helper tests (no server). The live driver (gaze,
// pauses) is exercised in live tests; these lock the decision math.

const assert = require('assert');
const { jitterHeading, shouldPause, shouldHop, pickIdleGaze, ANIMALS } = require('./motor');

let pass = 0;
const t = (name, cond) => { assert.ok(cond, name); console.log(`  ok  ${name}`); pass++; };

// jitterHeading: bounded wobble, deterministic under injected rng
t('jitter rng=0.5 -> unchanged heading', jitterHeading(1.0, () => 0.5) === 1.0);
t('jitter rng=1 -> +maxRad', Math.abs(jitterHeading(1.0, () => 1) - 1.22) < 1e-9);
t('jitter rng=0 -> -maxRad', Math.abs(jitterHeading(1.0, () => 0) - 0.78) < 1e-9);
t('jitter stays within ±maxRad over many rolls', (() => {
  for (let i = 0; i < 200; i++) {
    const h = jitterHeading(0, Math.random, 0.22);
    if (h < -0.22 || h > 0.22) return false;
  }
  return true;
})());

// shouldPause: calm-gated, ~25%
t('no pause when not calm (even lucky roll)', shouldPause(false, () => 0.0) === false);
t('pause on calm + low roll', shouldPause(true, () => 0.1) === true);
t('no pause on calm + high roll', shouldPause(true, () => 0.9) === false);

// shouldHop: calm + on ground + dry only
t('no hop in water', shouldHop(true, true, true, () => 0.0) === false);
t('no hop mid-air', shouldHop(true, false, false, () => 0.0) === false);
t('no hop when threatened', shouldHop(false, true, false, () => 0.0) === false);
t('hop when calm + grounded + dry + lucky', shouldHop(true, true, false, () => 0.001) === true);
t('hop is RARE (high roll -> no)', shouldHop(true, true, false, () => 0.5) === false);

// pickIdleGaze: players are the most interesting thing in the world
t('players present + low roll -> watch the player',
  pickIdleGaze([{}], [{}], () => 0.1).which === 'player');
t('no players, animals present -> watch the animal',
  pickIdleGaze([], [{}], () => 0.1).which === 'animal');
t('nothing around -> gaze at the horizon',
  pickIdleGaze([], [], () => 0.9).kind === 'horizon');
t('horizon gaze has a direction',
  typeof pickIdleGaze([], [], () => 0.9).yaw === 'number');

// the animal watch-list knows the classics
t('sheep are watchable', ANIMALS.has('sheep'));
t('creepers are NOT idle-watching material', !ANIMALS.has('creeper'));

console.log(`\nMOTOR: ${pass} PASS`);
