'use strict';

// Layer 0 unit tests — perception integrity. Pure, no live server (mirrors
// test-decide.js). Run: node test-perceive.js

const { perceive, occupiesSelf, isLava } = require('./perceive');

// minimal Vec3 stub with the methods perceive/occupiesSelf use
const V = (x, y, z) => ({
  x, y, z,
  offset: (a, b, c) => V(x + a, y + b, z + c),
  floored: () => V(Math.floor(x), Math.floor(y), Math.floor(z)),
  distanceTo(o) { return Math.hypot(x - o.x, y - o.y, z - o.z); },
});

let pass = 0; let fail = 0;
const t = (n, c) => { (c ? pass++ : fail++); console.log(`  ${c ? 'ok ' : 'FAIL'}  ${n}`); };

// ── validity gate ─────────────────────────────────────────────────────────────
t('NaN position -> invalid (the desync crash class)',
  perceive({ entity: { position: V(NaN, 75, NaN), onGround: true }, health: 20, food: 20, blockAt: () => null }).valid === false);
t('no entity -> invalid', perceive({ entity: null }).valid === false);

const okBot = {
  entity: { position: V(10.5, 64, 20.5), onGround: true }, health: 18, food: 15,
  blockAt: (p) => ({ name: p.y < 64 ? 'grass_block' : 'air' }),
};
const p = perceive(okBot);
t('finite position -> valid', p.valid === true);
t('hp derived', p.hp === 18);
t('onGround derived', p.onGround === true);

// ── lava sense ────────────────────────────────────────────────────────────────
t('inLava true when standing in lava',
  perceive({ entity: { position: V(0, 64, 0), onGround: false }, health: 20, food: 20, blockAt: () => ({ name: 'lava' }) }).inLava === true);
t('isLava helper', isLava({ name: 'lava' }) === true && isLava({ name: 'water' }) === false);

// ── occupiesSelf (don't jam a block into her own hitbox) ──────────────────────
const selfBot = { entity: { position: V(5.5, 64, 5.5) } };
t('feet block = self', occupiesSelf(selfBot, { x: 5, y: 64, z: 5 }) === true);
t('head block = self', occupiesSelf(selfBot, { x: 5, y: 65, z: 5 }) === true);
t('beside block != self', occupiesSelf(selfBot, { x: 6, y: 64, z: 5 }) === false);
t('above-head block != self', occupiesSelf(selfBot, { x: 5, y: 66, z: 5 }) === false);
t('unknown position -> refuse (self=true)', occupiesSelf({ entity: { position: V(NaN, 1, 1) } }, { x: 0, y: 0, z: 0 }) === true);

console.log(`\nPERCEPTION: ${pass}/${pass + fail} PASS`);
process.exit(fail ? 1 : 0);
