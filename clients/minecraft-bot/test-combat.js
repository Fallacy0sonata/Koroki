'use strict';
// Combat decision-logic tests (pure, no server).

const assert = require('assert');
const { bestTarget, shouldRetreat, isRanged, mobInfo, confidence, shouldEngage } = require('./combat');

let pass = 0;
const t = (name, cond) => { assert.ok(cond, name); console.log(`  ok  ${name}`); pass++; };

t('no hostiles -> null', bestTarget([]) === null);
t('single -> that one', bestTarget([{ name: 'zombie', dist: 5 }]).name === 'zombie');
t('nearest among melee', bestTarget([{ name: 'zombie', dist: 8 }, { name: 'spider', dist: 3 }]).name === 'spider');
t('ranged prioritized over closer melee',
  bestTarget([{ name: 'zombie', dist: 2 }, { name: 'skeleton', dist: 9 }]).name === 'skeleton');
t('two ranged -> nearest ranged',
  bestTarget([{ name: 'skeleton', dist: 9 }, { name: 'stray', dist: 4 }]).name === 'stray');

t('retreat when hp low', shouldRetreat(6) === true);
t('no retreat when healthy', shouldRetreat(16) === false);
t('retreat handles null hp', shouldRetreat(null) === false);

t('skeleton is ranged', isRanged('skeleton') === true);
t('zombie is melee', isRanged('zombie') === false);

// mob strategy
t('creeper style = hitrun', mobInfo('creeper').style === 'hitrun');
t('skeleton style = rush', mobInfo('skeleton').style === 'rush');
t('enderman style = avoid', mobInfo('enderman').style === 'avoid');
t('warden style = avoid (never fight)', mobInfo('warden').style === 'avoid');
t('ravager too strong early -> avoid', mobInfo('ravager').style === 'avoid');
t('piglin brute -> avoid', mobInfo('piglin_brute').style === 'avoid');
t('ender dragon -> avoid', mobInfo('ender_dragon').style === 'avoid');
t('zombie -> melee', mobInfo('zombie').style === 'melee');
t('truly unknown mob -> melee default', mobInfo('made_up_mob').style === 'melee');
t('warden never engaged even fully geared',
  shouldEngage({ hp: 20, inv: { hasWeapon: true, hasShield: true } }, { name: 'warden' }) === false);

// confidence-based engage
const gearedHealthy = { hp: 20, inv: { hasWeapon: true, hasShield: true } };
const barehandHealthy = { hp: 20, inv: { hasWeapon: false, hasShield: false } };
const gearedHurt = { hp: 7, inv: { hasWeapon: true, hasShield: false } };
t('geared+healthy engages a creeper', shouldEngage(gearedHealthy, { name: 'creeper' }) === true);
t('barehand flees a creeper (danger>=3 unarmed)', shouldEngage(barehandHealthy, { name: 'creeper' }) === false);
t('barehand can still swat a zombie at full hp', shouldEngage(barehandHealthy, { name: 'zombie' }) === true);
t('geared but hurt flees a creeper', shouldEngage(gearedHurt, { name: 'creeper' }) === false);
t('never melee an enderman', shouldEngage(gearedHealthy, { name: 'enderman' }) === false);
t('confidence rises with gear', confidence(gearedHealthy) > confidence(barehandHealthy));

console.log(`\nCOMBAT: ${pass} PASS`);
