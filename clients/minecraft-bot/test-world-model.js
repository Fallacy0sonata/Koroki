'use strict';
// Layer 1 unit tests — world model store (no server, no file).

const assert = require('assert');
const { createWorldModel, dist } = require('./world-model');

let pass = 0;
const t = (name, cond) => { assert.ok(cond, name); console.log(`  ok  ${name}`); pass++; };

// controllable clock so we can test decay deterministically
let clock = 1_000_000;
const wm = createWorldModel(null, () => clock);

// base set/get
wm.setBase({ x: 10.4, y: 64, z: -5.6 });
t('base rounds + stores', wm.getBase().x === 10 && wm.getBase().z === -6);

// notes + nearest
wm.noteResource({ x: 20, y: 12, z: 0 }, 'iron_ore');
wm.noteResource({ x: 5, y: 12, z: 0 }, 'coal_ore');
wm.noteHazard({ x: 3, y: 11, z: 0 }, 'lava');
wm.noteCave({ x: -30, y: 40, z: 10 });

const from = { x: 0, y: 12, z: 0 };
t('nearest resource is coal (closer)', wm.nearest('resources', from).tag === 'coal_ore');
t('nearest with filter finds iron', wm.nearest('resources', from, (e) => e.tag === 'iron_ore').tag === 'iron_ore');
t('nearest hazard is the lava', wm.nearest('hazards', from).tag === 'lava');
t('nearest cave found', wm.nearest('caves', from) !== null);
t('counts', wm.count('resources') === 2 && wm.count('caves') === 1);

// re-noting the same spot updates, doesn't duplicate
wm.noteResource({ x: 20, y: 12, z: 0 }, 'iron_ore');
t('re-note same spot = no dupe', wm.count('resources') === 2);

// decay: advance clock past DECAY_MS -> entries vanish, base persists (no decay)
clock += 21 * 60 * 1000;
t('decayed resource is gone', wm.nearest('resources', from) === null);
wm.decay();
t('decay() clears caves too', wm.count('caves') === 0);
t('base survives decay', wm.getBase() !== null);

// dist helper
t('dist helper', dist({ x: 0, y: 0, z: 0 }, { x: 3, y: 0, z: 4 }) === 5);

console.log(`\nWORLD MODEL: ${pass} PASS`);
