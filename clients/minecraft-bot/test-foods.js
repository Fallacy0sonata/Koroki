'use strict';
// Food knowledge tests (pure).

const assert = require('assert');
const { bestFood, isRaw, cookedForm, isEdible } = require('./foods');

let pass = 0;
const t = (name, cond) => { assert.ok(cond, name); console.log(`  ok  ${name}`); pass++; };

// bestFood picks highest saturation
t('prefers steak over bread', bestFood(['bread', 'cooked_beef']) === 'cooked_beef');
t('golden carrot beats steak (saturation)', bestFood(['cooked_beef', 'golden_carrot']) === 'golden_carrot');
t('skips rotten flesh when better exists', bestFood(['rotten_flesh', 'bread']) === 'bread');
t('skips risky raw chicken when better exists', bestFood(['chicken', 'carrot']) === 'carrot');
t('no food -> null', bestFood(['cobblestone', 'stick']) === null);
t('desperate eats rotten flesh', bestFood(['rotten_flesh'], true) === 'rotten_flesh');

// raw / cooking
t('raw beef is raw', isRaw('beef') === true);
t('cooked beef is not raw', isRaw('cooked_beef') === false);
t('beef cooks to cooked_beef', cookedForm('beef') === 'cooked_beef');
t('potato cooks to baked_potato', cookedForm('potato') === 'baked_potato');
t('cobblestone does not cook', cookedForm('cobblestone') === null);
t('bread is edible', isEdible('bread') === true);

console.log(`\nFOODS: ${pass} PASS`);
