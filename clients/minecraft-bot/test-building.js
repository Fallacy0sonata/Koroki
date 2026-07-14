'use strict';
// Building geometry tests (pure, no server).

const assert = require('assert');
const { planHut, blockBudget } = require('./building');

let pass = 0;
const t = (name, cond) => { assert.ok(cond, name); console.log(`  ok  ${name}`); pass++; };

const c = { x: 0, y: 64, z: 0 };
const plan = planHut(c, 3, 3, 2);

// 3x3 perimeter = 8 cells/layer * 2 layers = 16 wall cells, minus 2 door = 14
t('walls = 14 (shell minus doorway)', plan.walls.length === 14);
t('roof = 9 (full 3x3 lid)', plan.roof.length === 9);
t('door is a 1x2 gap', plan.door.length === 2);
t('doorway is on the -z face', plan.door.every((p) => p.z === c.z - 1));
t('decor has a torch', plan.decor.some((d) => d.kind === 'torch'));
t('decor has a station spot', plan.decor.some((d) => d.kind === 'station'));

// doorway cells are NOT in the walls (she can actually walk in)
const wallKeys = new Set(plan.walls.map((p) => `${p.x},${p.y},${p.z}`));
t('doorway not walled over', plan.door.every((p) => !wallKeys.has(`${p.x},${p.y},${p.z}`)));

// roof sits above the walls
t('roof is above wall height', plan.roof.every((p) => p.y === c.y + 2));

// budget
t('blockBudget = walls + roof', blockBudget(plan) === 14 + 9);

// bigger hut scales
const big = planHut(c, 5, 5, 3);
t('5x5x3 perimeter walls = 16*3 - 2 door = 46', big.walls.length === 16 * 3 - 2);

console.log(`\nBUILDING: ${pass} PASS`);
