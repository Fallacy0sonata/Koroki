'use strict';
// Verification layer tests (pure / mock-inventory).

const assert = require('assert');
const { countOf, totalItems, totalGained, expectedDrop, ateOk, arrivedNear } = require('./verify');

let pass = 0;
const t = (name, cond) => { assert.ok(cond, name); console.log(`  ok  ${name}`); pass++; };

const bot = (items, food) => ({ inventory: { items: () => items }, food });

// inventory counting
t('countOf sums stacks', countOf(bot([{ name: 'cobblestone', count: 40 }, { name: 'cobblestone', count: 22 }]), 'cobblestone') === 62);
t('totalItems sums all', totalItems(bot([{ name: 'a', count: 5 }, { name: 'b', count: 3 }])) === 8);
t('totalGained counts positive deltas', totalGained({ cobblestone: 10 }, { cobblestone: 15, dirt: 2 }) === 7);

// expected drops (verification targets)
t('stone -> cobblestone', expectedDrop('stone') === 'cobblestone');
t('diamond_ore -> diamond', expectedDrop('diamond_ore') === 'diamond');
t('deepslate_iron_ore -> raw_iron', expectedDrop('deepslate_iron_ore') === 'raw_iron');
t('oak_log -> itself', expectedDrop('oak_log') === 'oak_log');
t('unknown block -> null (verify by any-gain)', expectedDrop('mysterious_block') === null);

// eating verification
t('ate when food rose', ateOk(bot([], 18), 14) === true);
t('ate ok when already full', ateOk(bot([], 20), 20) === true);
t('ate FAILED when food unchanged low', ateOk(bot([], 10), 12) === false);

// arrival verification
t('arrived near target', arrivedNear({ entity: { position: { x: 10, y: 64, z: 10 } } }, { x: 11, y: 64, z: 11 }) === true);
t('not arrived when far', arrivedNear({ entity: { position: { x: 0, y: 64, z: 0 } } }, { x: 20, y: 64, z: 20 }) === false);

console.log(`\nVERIFY: ${pass} PASS`);
