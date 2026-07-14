'use strict';
// Social model + fumble tests (pure, no server).

const assert = require('assert');
const { classify, createSocial } = require('./social');
const { rollFumble, pickFumble } = require('./fumble');

let pass = 0;
const t = (name, cond) => { assert.ok(cond, name); console.log(`  ok  ${name}`); pass++; };

const NOW = 1_000_000;

// classify
t('no record -> null', classify(null, NOW) === null);
t('fresh gift -> gift', classify({ giftAt: NOW - 1000, hits: [] }, NOW) === 'gift');
t('stale gift -> null', classify({ giftAt: NOW - 9000, hits: [] }, NOW) === null);
t('one hit -> poke', classify({ hits: [NOW - 500] }, NOW) === 'poke');
t('two recent hits -> troll', classify({ hits: [NOW - 500, NOW - 2000] }, NOW) === 'troll');
t('old hits expire -> null', classify({ hits: [NOW - 9000, NOW - 8000] }, NOW) === null);

// tracker
const soc = createSocial();
soc.noteHit('Koro', NOW);
t('single tracked hit -> poke', soc.intentFor('Koro', NOW) === 'poke');
soc.noteHit('Koro', NOW + 1000);
t('second hit -> troll', soc.intentFor('Koro', NOW + 1000) === 'troll');
soc.noteGift('Koro', NOW + 1500);
t('gift overrides -> gift', soc.intentFor('Koro', NOW + 1500) === 'gift');
t('unknown player -> null', soc.intentFor('Nobody', NOW) === null);

// fumble (deterministic rng)
t('rollFumble true on tiny rng', rollFumble(() => 0.001) === true);
t('rollFumble false on high rng', rollFumble(() => 0.9) === false);
t('pickFumble returns a string', typeof pickFumble(() => 0) === 'string');

console.log(`\nSOCIAL + FUMBLE: ${pass} PASS`);
