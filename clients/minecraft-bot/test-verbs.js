'use strict';
// Item-verb layer — pure-part tests (no server): armor choice, loot whitelist,
// bed color logic. The live verbs (loot/boat/bucket/farm) are live-test items.

const assert = require('assert');
const { armorScore, armorKind, bestArmorPieces, worthLooting, bedColorFor } = require('./verbs');

let pass = 0;
const t = (name, cond) => { assert.ok(cond, name); console.log(`  ok  ${name}`); pass++; };

// ── armor scoring ─────────────────────────────────────────────────────────────
t('iron chestplate outranks leather', armorScore('iron_chestplate') > armorScore('leather_chestplate'));
t('diamond outranks iron', armorScore('diamond_helmet') > armorScore('iron_helmet'));
t('netherite is top', armorScore('netherite_boots') > armorScore('diamond_boots'));
t('a sword is not armor', armorScore('iron_sword') === 0);
t('turtle helmet counts', armorScore('turtle_helmet') > 0);
t('kind: helmet', armorKind('golden_helmet') === 'helmet');
t('kind: non-armor -> null', armorKind('cobblestone') === null);

// bestArmorPieces picks the best per slot from a mixed bag
const bag = ['leather_helmet', 'iron_helmet', 'iron_chestplate', 'golden_boots', 'diamond_boots', 'stone_sword'];
const best = bestArmorPieces(bag);
t('best helmet = iron', best.helmet === 'iron_helmet');
t('best boots = diamond', best.boots === 'diamond_boots');
t('chestplate found', best.chestplate === 'iron_chestplate');
t('no leggings in bag -> none picked', best.leggings === undefined);

// ── loot whitelist: progress-convertible only ─────────────────────────────────
t('take iron ingots', worthLooting('iron_ingot'));
t('take diamonds', worthLooting('diamond'));
t('take bread', worthLooting('bread'));
t('take an enchanted book', worthLooting('enchanted_book'));
t('take a saddle', worthLooting('saddle'));
t('take hay bales (village food)', worthLooting('hay_block'));
t('skip junk: cobblestone', !worthLooting('cobblestone'));
t('skip junk: rotten flesh', !worthLooting('rotten_flesh'));
t('skip junk: dirt', !worthLooting('dirt'));
t('skip poisonous potato (potato regex must not drag it in)', !worthLooting('poisonous_potato'));

// ── bed color: needs 3 MATCHING wool ──────────────────────────────────────────
t('3 white wool -> white bed', bedColorFor({ white_wool: 3 }) === 'white');
t('2+1 mixed wool -> no bed yet', bedColorFor({ white_wool: 2, black_wool: 1 }) === null);
t('4 gray wool -> gray bed', bedColorFor({ gray_wool: 4 }) === 'gray');
t('no wool -> null', bedColorFor({ oak_planks: 12 }) === null);
t('wool-adjacent junk does not count', bedColorFor({ wool_fake: 5 }) === null);

console.log(`\nVERBS: ${pass} PASS`);
