'use strict';
// Durability + deposit logic tests (pure helpers from skills.js).

const assert = require('assert');
const { toolHealthFrac, depositAmount } = require('./skills');

let pass = 0;
const t = (name, cond) => { assert.ok(cond, name); console.log(`  ok  ${name}`); pass++; };

// durability fraction
t('75% durability', toolHealthFrac(100, 25) === 0.75);
t('full when no max', toolHealthFrac(0, 0) === 1);
t('broken = 0', toolHealthFrac(100, 100) === 0);

// deposit rules — keep tools/food/valuables, stash bulk/junk over buffer
const dep = (name, count) => depositAmount({ name, count });
t('keep pickaxe', dep('iron_pickaxe', 1) === 0);
t('keep food', dep('bread', 20) === 0);
t('keep iron ingots', dep('iron_ingot', 30) === 0);
t('keep diamonds', dep('diamond', 5) === 0);
t('keep coal (fuel)', dep('coal', 30) === 0);
t('stash cobblestone over 64', dep('cobblestone', 200) === 136);
t('keep cobblestone under buffer', dep('cobblestone', 30) === 0);
t('stash all dirt', dep('dirt', 100) === 100);
t('stash junk rotten flesh', dep('rotten_flesh', 5) === 5);
t('keep some planks (buffer 32)', dep('oak_planks', 40) === 8);
// bed materials + farm stock ride along (stashing wool as fast as she sheared it
// livelocked the bed craft — audit 2026-07-13)
t('keep wool up to 6', dep('white_wool', 5) === 0);
t('stash wool beyond 6', dep('white_wool', 10) === 4);
t('keep seeds up to 24', dep('wheat_seeds', 20) === 0);
t('keep arrows up to 24', dep('arrow', 12) === 0);

console.log(`\nINVENTORY: ${pass} PASS`);
