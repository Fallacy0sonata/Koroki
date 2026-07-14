'use strict';
// Mining policy tests — exact target discipline + cross-tick failed-block memory.

const assert = require('assert');
const {
  miningTargetKeyword, rememberGatherFailure, gatherFailureActive,
} = require('./skills');

let pass = 0;
const t = (name, cond) => { assert.ok(cond, name); console.log(`  ok  ${name}`); pass++; };

t('diamond_ore normalizes to exact diamond target', miningTargetKeyword('diamond_ore') === 'diamond');
t('plain diamond remains exact diamond target', miningTargetKeyword('diamond') === 'diamond');
t('no ore hint means generic/opportunistic mining', miningTargetKeyword(null) === null);

const bot = {};
const sealed = { x: 10, y: -54, z: 20 };
const other = { x: 11, y: -54, z: 20 };
rememberGatherFailure(bot, sealed, 'hidden', 1_000);
t('sealed ore stays blocked across goal calls', gatherFailureActive(bot, sealed, 1_001) === true);
t('a different coordinate remains eligible', gatherFailureActive(bot, other, 1_001) === false);
t('sealed-ore cooldown expires after five minutes', gatherFailureActive(bot, sealed, 301_000) === false);
t('expired coordinates are pruned from the ledger', bot._gatherFailures.size === 0);

console.log(`\nMINING: ${pass} PASS`);
