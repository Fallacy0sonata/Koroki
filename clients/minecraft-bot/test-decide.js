'use strict';
// Pure-logic test for the survival-reflex layer (no server needed).
// Validates the BODY's overrides: life/needs win, and null when nothing's urgent
// (so the arbiter is free to run an owner command or a project). The tech-tree
// progression moved to projects.js — see test-projects.js.

const assert = require('assert');
const { survivalReflex } = require('./decide');

function snap(over = {}) {
  return {
    hp: 20, food: 20, timeLabel: 'morning', raining: false,
    hostiles: [], players: [],
    inv: { logIsh: true, plankIsh: true, stoneIsh: true, foodItems: 3,
           hasWeapon: true, hasPickaxe: true, hasShield: false, hasBed: false, torches: 4 },
    hasShelter: false, ...over,
  };
}
let pass = 0;
function check(name, got, wantGoal) {
  const goal = got && got.goal ? got.goal : String(got);
  assert.strictEqual(goal, wantGoal, `${name}: got ${goal} (${got && got.reason}), want ${wantGoal}`);
  console.log(`  ok  ${name} -> ${goal}${got && got.reason ? ` (${got.reason})` : ''}`);
  pass++;
}

// 0. DROWNING beats everything — even a creeper next to her (water-cave death, r16)
check('drowning -> surface', survivalReflex(snap({ headInWater: true, oxygen: 8 })), 'surface');
check('drowning beats mobs', survivalReflex(snap({ headInWater: true, oxygen: 4,
  hostiles: [{ name: 'creeper', dist: 3 }] })), 'surface');
// swimming with plenty of air is NOT a reflex
assert.strictEqual(survivalReflex(snap({ headInWater: true, oxygen: 18 })), null,
  'full air while swimming should not fire');
console.log('  ok  swimming-with-air -> null'); pass++;
// head above water, low oxygen field stale -> no surface goal
assert.strictEqual(survivalReflex(snap({ headInWater: false, oxygen: 8 })), null,
  'not underwater = no surface reflex');
console.log('  ok  dry-low-oxygen -> null'); pass++;

// 1. critical hp + a mob near -> flee
check('crit-hp+threat', survivalReflex(snap({ hp: 4, hostiles: [{ name: 'zombie', dist: 6 }] })), 'flee');
// 2. critical hp, safe, has food (not full) -> EAT to heal (not run)
check('crit-hp+food', survivalReflex(snap({ hp: 4, food: 16, hostiles: [] })), 'eat');
// 2b. critical hp, safe, food FULL (can't eat) -> lie low and regen, don't flee
check('crit-hp+fullfood', survivalReflex(snap({ hp: 4, food: 20, hostiles: [] })), 'wait');
// 2c. critical hp but the mob is FAR (>16m) -> heal, don't flee pointlessly
check('crit-hp+mob-far', survivalReflex(snap({ hp: 4, food: 16, hostiles: [{ name: 'zombie', dist: 25 }] })), 'eat');
// 2d. hurt (not critical) + safe -> eat to heal up
check('hurt-safe-heal', survivalReflex(snap({ hp: 12, food: 15, hostiles: [] })), 'eat');
// 2e. hurt + a mob within 16m -> do NOT stop to heal
assert.strictEqual(survivalReflex(snap({ hp: 12, food: 15, hostiles: [{ name: 'zombie', dist: 10 }] })), null,
  'do not heal while a mob is near');
console.log('  ok  hurt-mob-near -> null'); pass++;
// 3. hostile adjacent + weapon -> fight
check('adjacent+armed', survivalReflex(snap({ hostiles: [{ name: 'zombie', dist: 2 }] })), 'fight');
// 4. hostile adjacent, unarmed + weak -> flee
check('adjacent+weak', survivalReflex(snap({ hp: 8, hostiles: [{ name: 'creeper', dist: 2 }],
  inv: { ...snap().inv, hasWeapon: false } })), 'flee');
// 5. night threat at a sleepable distance (>8m), has bed -> sleep
check('night-threat+bed', survivalReflex(snap({ timeLabel: 'night', hostiles: [{ name: 'skeleton', dist: 10 }],
  inv: { ...snap().inv, hasBed: true } })), 'sleep');
// 5b. night threat CLOSE (<=8m): Minecraft rejects the sleep — wall up instead
check('night-threat-close+bed', survivalReflex(snap({ timeLabel: 'night', hostiles: [{ name: 'skeleton', dist: 6 }],
  inv: { ...snap().inv, hasBed: true } })), 'build_shelter');
// 5c. a bed BLOCK nearby counts like a carried bed
check('night-threat+bed-nearby', survivalReflex(snap({ timeLabel: 'night', bedNearby: true,
  hostiles: [{ name: 'skeleton', dist: 10 }] })), 'sleep');
// 6. night threat, no bed -> build_shelter
check('night-threat', survivalReflex(snap({ timeLabel: 'night', hostiles: [{ name: 'skeleton', dist: 10 }] })), 'build_shelter');
// 7. starving + food -> eat
check('starving+food', survivalReflex(snap({ food: 4 })), 'eat');
// 8. starving, no food -> hunt
check('starving-nofood', survivalReflex(snap({ food: 4, inv: { ...snap().inv, foodItems: 0 } })), 'hunt');
// 9. calm night, no shelter -> build_shelter
check('night-exposed', survivalReflex(snap({ timeLabel: 'night', hostiles: [] })), 'build_shelter');
// 9b. calm night but SHELTERED (near home/bed) -> no reflex; rhythm layer owns the night
assert.strictEqual(survivalReflex(snap({ timeLabel: 'night', hostiles: [], hasShelter: true })), null,
  'sheltered night should not fire the shelter reflex');
console.log('  ok  night-sheltered -> null (rhythm owns bedtime)'); pass++;
// 10. creeper at range, well-equipped + healthy -> FIGHT (hit-and-run, confidence)
check('creeper-confident', survivalReflex(snap({ hostiles: [{ name: 'creeper', dist: 6 }] })), 'fight');
// 10b. creeper at range, hurt/underequipped -> flee (not confident)
check('creeper-outmatched', survivalReflex(snap({ hp: 8, hostiles: [{ name: 'creeper', dist: 6 }],
  inv: { ...snap().inv, hasWeapon: false } })), 'flee');
// 11. SAFE + fed + day -> null (no reflex; arbiter takes over)
assert.strictEqual(survivalReflex(snap()), null, 'safe daytime should yield no reflex');
console.log('  ok  safe-daytime -> null (arbiter runs owner/project)'); pass++;
// 12. daytime zombie at 6m (not adjacent, not creeper) -> null (she keeps working)
assert.strictEqual(survivalReflex(snap({ hostiles: [{ name: 'zombie', dist: 6 }] })), null,
  'distant daytime zombie is not a reflex');
console.log('  ok  distant-day-zombie -> null'); pass++;
// 13. soft need: food dipping + safe + has food -> proactive eat
check('proactive-eat', survivalReflex(snap({ food: 11 })), 'eat');
// 14. food dipping but a hostile is near -> no proactive eat (she deals with the threat/works)
assert.strictEqual(survivalReflex(snap({ food: 11, hostiles: [{ name: 'zombie', dist: 6 }] })), null,
  'do not stop to eat with a hostile around');
console.log('  ok  no-eat-when-threatened -> null'); pass++;

// 15. daytime ranged attacker (the guardian death) — ungeared -> flee out of range
check('guardian-ranged-flee', survivalReflex(snap({ hostiles: [{ name: 'guardian', dist: 10 }],
  inv: { ...snap().inv, hasWeapon: false } })), 'flee');
// 16. distant daytime guardian (>12m) -> not yet a reflex (she keeps going)
assert.strictEqual(survivalReflex(snap({ hostiles: [{ name: 'guardian', dist: 20 }],
  inv: { ...snap().inv, hasWeapon: false } })), null, 'far guardian not yet urgent');
console.log('  ok  far-guardian -> null'); pass++;

console.log(`\nSURVIVAL REFLEX: ${pass} PASS`);
