'use strict';

// Recursive crafting / acquisition resolver (owner ask 2026-07-13). When she wants
// to craft X but lacks an ingredient, figure out HOW to get that ingredient — mine
// it, smelt it, or craft it from its own ingredients — instead of looping on a craft
// she can't afford. This is the "look up the recipe and go get the missing materials"
// capability. It returns the single NEXT concrete action; the loop re-runs it each
// tick, making one step of progress until the target is craftable.

function mcData(bot) { return require('minecraft-data')(bot.version); }
function invById(bot, id) {
  return (bot.inventory.items() || []).filter((i) => i.type === id).reduce((n, i) => n + i.count, 0);
}
function invByName(bot, name) {
  return (bot.inventory.items() || []).filter((i) => i.name === name).reduce((n, i) => n + i.count, 0);
}

// Base materials → the concrete action that yields them (PURE, testable). Anything
// not here and not smeltable is assumed craftable and resolved via its recipe.
function baseAcquire(name) {
  if (/_log$|^log$/.test(name)) return { do: 'gather', arg: 'log' };
  if (/_wool$|^wool$/.test(name)) return { do: 'wool', arg: 'wool' }; // kill/shear sheep (verbs.getWool)
  if (name === 'cobblestone' || name === 'stone') return { do: 'mine', arg: 'stone' };
  if (name === 'cobbled_deepslate' || name === 'deepslate') return { do: 'mine', arg: 'deepslate' };
  if (name === 'raw_iron' || name === 'iron_ore') return { do: 'mine', arg: 'iron_ore' };
  if (name === 'raw_gold' || name === 'gold_ore') return { do: 'mine', arg: 'gold_ore' };
  if (name === 'raw_copper' || name === 'copper_ore') return { do: 'mine', arg: 'copper_ore' };
  if (name === 'diamond' || name === 'diamond_ore') return { do: 'mine', arg: 'diamond' };
  if (name === 'coal' || name === 'coal_ore') return { do: 'mine', arg: 'coal_ore' };
  if (name === 'redstone' || name === 'redstone_ore') return { do: 'mine', arg: 'redstone_ore' };
  if (name === 'lapis_lazuli' || name === 'lapis_ore') return { do: 'mine', arg: 'lapis_ore' };
  if (name === 'gravel' || name === 'flint') return { do: 'mine', arg: 'gravel' };
  if (name === 'sand') return { do: 'mine', arg: 'sand' };
  if (name === 'dirt') return { do: 'mine', arg: 'dirt' };
  return null;
}

// Smelting outputs → their raw input (PURE). Drives the smelt path.
const SMELT_FROM = {
  iron_ingot: 'raw_iron', gold_ingot: 'raw_gold', copper_ingot: 'raw_copper',
  glass: 'sand', stone: 'cobblestone', charcoal: 'oak_log',
  cooked_beef: 'beef', cooked_porkchop: 'porkchop', cooked_chicken: 'chicken',
  cooked_mutton: 'mutton', cooked_cod: 'cod', cooked_salmon: 'salmon', baked_potato: 'potato',
};

// Ingredients still missing to craft `item` once, using recipe deltas + inventory.
// Returns [] if craftable now, null if it has no recipe (base/smelt material).
// Checks EVERY recipe variant and returns the one closest to satisfied — checking
// only recipes[0] species-locked tools to OAK planks, so in an acacia savanna she
// hoarded infinite wood while "missing" oak (live round 16: "nobody needs that
// much wood for a wooden pickaxe").
function missingIngredients(bot, item) {
  const data = mcData(bot);
  let recipes = [];
  try { recipes = bot.recipesAll(item.id, null, true) || []; } catch (_) {}
  if (!recipes.length) return null;
  let best = null;
  for (const r of recipes) {
    const miss = [];
    let missTotal = 0;
    for (const d of (r.delta || [])) {
      if (d.count < 0) { // a consumed ingredient
        const needed = -d.count;
        const have = invById(bot, d.id);
        if (have < needed) {
          miss.push({ id: d.id, name: data.items[d.id] && data.items[d.id].name, count: needed - have });
          missTotal += needed - have;
        }
      }
    }
    if (!best || missTotal < best.missTotal) best = { miss, missTotal, requiresTable: !!r.requiresTable };
    if (missTotal === 0) break; // fully craftable variant found
  }
  best.miss.requiresTable = best.requiresTable; // ride along for nextStepFor
  return best.miss;
}

// The next concrete action toward crafting `target`.
// → { do:'craft'|'gather'|'mine'|'smelt', arg } or null (can't resolve).
function nextStepFor(bot, target, depth = 0) {
  if (depth > 8) return null;
  // 'bed' is color-generic: resolve to wool-gathering until 3 matching wool exist,
  // then the caller (smartCraft) crafts whichever color she can (verbs.craftBed).
  if (target === 'bed') {
    const wool = (bot.inventory.items() || []).filter((i) => /_wool$/.test(i.name));
    const byColor = {};
    for (const w of wool) byColor[w.name] = (byColor[w.name] || 0) + w.count;
    const enough = Object.values(byColor).some((n) => n >= 3);
    if (!enough) return { do: 'wool', arg: 'wool' };
    const planks = (bot.inventory.items() || []).some((i) => /_planks$/.test(i.name));
    if (!planks) return { do: 'craft', arg: 'oak_planks' };
    return { do: 'craft', arg: 'bed' };
  }
  const data = mcData(bot);
  const item = data.itemsByName[target];
  if (!item) return null;
  const miss = missingIngredients(bot, item);
  if (miss === null) return resolveMaterial(bot, target, 1, depth); // not craftable → get it
  if (!miss.length) {
    // ingredients ready — but a TABLE recipe with no table in reach and not enough
    // planks to make one loops "no crafting table available" forever (round 17:
    // that loop got iron_tools parked underground with zero wood on her).
    if (miss.requiresTable && target !== 'crafting_table') {
      const hasTableItem = invByName(bot, 'crafting_table') > 0;
      let tableNear = false;
      try { tableNear = !!bot.findBlock({ matching: (b) => b && b.name === 'crafting_table', maxDistance: 16 }); } catch (_) {}
      if (!hasTableItem && !tableNear) {
        const planks = (bot.inventory.items() || []).filter((i) => /_planks$/.test(i.name))
          .reduce((n, i) => n + i.count, 0);
        if (planks >= 4) return { do: 'craft', arg: 'crafting_table' };
        return resolveMaterial(bot, 'oak_planks', 4, depth); // species-flexible → logs
      }
    }
    return { do: 'craft', arg: target };                            // ready to craft now
  }
  return resolveMaterial(bot, miss[0].name, miss[0].count, depth);  // fetch first missing
}

function smeltStep(bot, target, depth) {
  // charcoal: ANY log smelts into it — demanding literal oak_log made her chop
  // birch forever on a non-oak island (audit 2026-07-13)
  if (target === 'charcoal') {
    const anyLog = (bot.inventory.items() || []).find((i) => /_log$/.test(i.name));
    if (anyLog) return { do: 'smelt', arg: anyLog.name };
    return { do: 'gather', arg: 'log' };
  }
  const raw = SMELT_FROM[target];
  if (invByName(bot, raw) > 0) return { do: 'smelt', arg: raw };
  return resolveMaterial(bot, raw, 1, depth + 1); // get the raw material first
}

function resolveMaterial(bot, name, count, depth) {
  if (!name) return null;
  // wood is SPECIES-FLEXIBLE: any planks satisfy tool/stick recipes — craft from
  // the logs she actually holds instead of chasing the recipe's listed species
  if (/_planks$/.test(name)) {
    const log = (bot.inventory.items() || []).find((i) => /_log$/.test(i.name));
    if (log) return { do: 'craft', arg: log.name.replace('stripped_', '').replace(/_log$/, '_planks') };
    return { do: 'gather', arg: 'log' };
  }
  const base = baseAcquire(name);
  if (base) return base;
  if (SMELT_FROM[name]) return smeltStep(bot, name, depth);
  return nextStepFor(bot, name, depth + 1) || { do: 'craft', arg: name }; // craftable intermediate
}

module.exports = { nextStepFor, resolveMaterial, baseAcquire, missingIngredients, SMELT_FROM };
