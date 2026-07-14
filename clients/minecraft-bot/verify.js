'use strict';

// Verification layer (2026-07-13, owner ask: "verify effects"). Actions must CONFIRM
// their intended effect actually happened. She mined a block — did she get the drop
// (or did it fly away / did another player grab it)? She crafted X — does she have X?
// She placed a table — is it actually there? She ate — did food go up? Skills use
// these to detect SILENT failures and retry / report failure instead of assuming.

function invMap(bot) {
  const m = {};
  for (const i of bot.inventory.items() || []) m[i.name] = (m[i.name] || 0) + i.count;
  return m;
}
function totalItems(bot) {
  return (bot.inventory.items() || []).reduce((n, i) => n + i.count, 0);
}
function countOf(bot, name) {
  return (bot.inventory.items() || []).filter((i) => i.name === name).reduce((n, i) => n + i.count, 0);
}
function totalGained(before, after) {
  let t = 0;
  for (const name of Object.keys(after)) { const d = after[name] - (before[name] || 0); if (d > 0) t += d; }
  return t;
}

// What item a broken block drops — for verifying a specific mine. Common ones only;
// unknown → null (caller verifies via "any item gained" instead).
const DROPS = {
  stone: 'cobblestone', deepslate: 'cobbled_deepslate',
  coal_ore: 'coal', deepslate_coal_ore: 'coal',
  iron_ore: 'raw_iron', deepslate_iron_ore: 'raw_iron',
  copper_ore: 'raw_copper', deepslate_copper_ore: 'raw_copper',
  gold_ore: 'raw_gold', deepslate_gold_ore: 'raw_gold',
  diamond_ore: 'diamond', deepslate_diamond_ore: 'diamond',
  redstone_ore: 'redstone', deepslate_redstone_ore: 'redstone',
  lapis_ore: 'lapis_lazuli', deepslate_lapis_ore: 'lapis_lazuli',
  emerald_ore: 'emerald', deepslate_emerald_ore: 'emerald',
  gravel: 'gravel', sand: 'sand', grass_block: 'dirt', dirt: 'dirt',
};
function expectedDrop(blockName) {
  if (!blockName) return null;
  if (DROPS[blockName]) return DROPS[blockName];
  if (/_log$|^log$|_planks$|_wood$|bamboo/.test(blockName)) return blockName; // drop themselves
  return null;
}

// after eating: did food actually rise (or was it already full)?
function ateOk(bot, foodBefore) { return (bot.food != null ? bot.food : 20) >= foodBefore; }

// is a real solid block present at pos (optionally with a specific name)?
function blockIs(bot, pos, name) {
  try {
    const b = bot.blockAt(pos);
    if (!b) return false;
    return name ? b.name === name : (b.boundingBox === 'block' && b.name !== 'air');
  } catch (_) { return false; }
}

// did she arrive near a target position?
function arrivedNear(bot, pos, tol = 2.5) {
  const p = bot.entity && bot.entity.position;
  if (!p || !pos) return false;
  return Math.hypot(p.x - pos.x, p.z - pos.z) <= tol && Math.abs(p.y - pos.y) <= tol + 1;
}

module.exports = { invMap, totalItems, countOf, totalGained, expectedDrop, ateOk, blockIs, arrivedNear, DROPS };
