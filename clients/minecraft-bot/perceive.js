'use strict';

// Layer 0 — Perception integrity. The single gate every decision passes through.
//
// This server (Aternos free/offline) desyncs her physics: her position can go
// NaN, knockback can fail to apply, chunks lag in. When that happens EVERY spatial
// calc downstream inherits the garbage — findBlock/pathfinder return nonsense
// ("no crafting table" while one is in her hand, `gather at (NaN,75,NaN)`). The
// old guard `!bot.entity.position` did NOT catch this: a NaN Vec3 is still an
// object, so the check passed and the NaN flowed straight into the skills.
//
// The rule here: trust nothing until it's validated. Invalid state => the loops
// WAIT instead of acting on lies. This is proprioception — the captain must never
// be handed a garbage sense of where the body is.

function finite3(p) {
  return !!p && Number.isFinite(p.x) && Number.isFinite(p.y) && Number.isFinite(p.z);
}

function isLava(b) { return !!b && (b.name === 'lava'); }
function isLiquid(b) { return !!b && (b.name === 'water' || b.name === 'lava'); }

/**
 * Validate + derive the low-level body state. Returns { valid:false, reason } when
 * her sense of self is untrustworthy (NaN position, NaN vitals), else a snapshot of
 * derived facts every higher layer can read without re-deriving from raw fields.
 */
function perceive(bot) {
  const ent = bot && bot.entity;
  const pos = ent && ent.position;
  if (!ent || !finite3(pos)) {
    return { valid: false, reason: 'no/NaN position' };
  }
  // vitals can also read NaN mid-desync — treat as unknown, not as "full".
  const hp = Number.isFinite(bot.health) ? bot.health : null;
  const food = Number.isFinite(bot.food) ? bot.food : null;

  let feetBlock = null;
  let headBlock = null;
  let groundBlock = null;
  try { feetBlock = bot.blockAt(pos); } catch (_) {}
  try { headBlock = bot.blockAt(pos.offset(0, 1, 0)); } catch (_) {}
  try { groundBlock = bot.blockAt(pos.offset(0, -1, 0)); } catch (_) {}

  return {
    valid: true,
    pos: pos.clone ? pos.clone() : { x: pos.x, y: pos.y, z: pos.z }, // snapshot, not the live ref
    hp,
    food,
    onGround: !!ent.onGround,
    onSolidGround: !!(groundBlock && groundBlock.boundingBox === 'block'),
    inLiquid: isLiquid(feetBlock) || isLiquid(headBlock),
    inLava: isLava(feetBlock) || isLava(headBlock),
    feetInWater: !!(feetBlock && feetBlock.name === 'water'),
    headInWater: !!(headBlock && headBlock.name === 'water'), // drowning risk
    feetBlock,
    headBlock,
    groundBlock,
  };
}

/**
 * Would placing a block at blockPos land inside her own hitbox? She occupies the
 * block at her feet and the one above her head. Placing there jams her (this is
 * literally what stuck her in the tree while trying to wall up). Refuse on unknown
 * position — safer to skip a placement than to jam.
 */
function occupiesSelf(bot, blockPos) {
  const p = bot.entity && bot.entity.position;
  if (!finite3(p) || !blockPos) return true;
  const feet = p.floored();
  return blockPos.x === feet.x
    && blockPos.z === feet.z
    && (blockPos.y === feet.y || blockPos.y === feet.y + 1);
}

module.exports = { perceive, occupiesSelf, isLava, isLiquid, finite3 };
