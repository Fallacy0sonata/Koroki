'use strict';
// Exploration strategy tests (pure, no server).

const assert = require('assert');
const { evaluateSurroundings, frontierHeading, cellKey, createExplorer } = require('./explore');

let pass = 0;
const t = (name, cond) => { assert.ok(cond, name); console.log(`  ok  ${name}`); pass++; };

// evaluateSurroundings
t('lots of water, little land -> water', evaluateSurroundings({ water: 40, grass: 5 }).verdict === 'water');
t('trees -> good', evaluateSurroundings({ logs: 6, grass: 20 }).verdict === 'good');
t('sandy, treeless -> poor', evaluateSurroundings({ sand: 30, grass: 2, logs: 0 }).verdict === 'poor');
t('plain land -> ok', evaluateSurroundings({ grass: 20, logs: 0 }).verdict === 'ok');
t('nothing -> poor', evaluateSurroundings({}).verdict === 'poor');

// cellKey buckets by 16
t('cellKey buckets by 16', cellKey(0, 0) === cellKey(15, 15) && cellKey(0, 0) !== cellKey(16, 0));

// frontierHeading avoids a visited direction
const visited = new Map();
const from = { x: 0, z: 0 };
// mark the +x cell (heading 0) as heavily visited
visited.set(cellKey(30, 0), 5);
const h = frontierHeading(visited, from, 30);
t('frontier avoids the visited +x direction', Math.abs(Math.cos(h) * 30 - 30) > 1);

// explorer commits then re-aims; visited grows; not stuck in one cell
const ex = createExplorer(() => 0); // deterministic heading start (0 rad = +x)
let p = { x: 0, z: 0 };
const dests = [];
for (let i = 0; i < 6; i++) { const r = ex.plan(p, 'ok'); dests.push(r.dest); p = { x: r.dest.x, z: r.dest.z }; }
t('explorer travels outward (not a box)', Math.hypot(dests[5].x, dests[5].z) > 60);
t('explorer records visited cells', ex.visited.size >= 3);

// hitting water retreats toward the last known land, not deeper into ocean
const ex2 = createExplorer(() => 0);
ex2.plan({ x: 0, z: 0 }, 'ok');        // she was on land at origin
const wr = ex2.plan({ x: 50, z: 0 }, 'water'); // swam east into water at x=50
t('water -> heads back toward land (west of current)', wr.dest.x < 50);
t('remembers last land pos', ex2.lastLandPos && ex2.lastLandPos.x === 0);

console.log(`\nEXPLORE: ${pass} PASS`);
