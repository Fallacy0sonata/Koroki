'use strict';

// Food knowledge (Iron Age arc, 2026-07-13). Which foods are worth eating, which to
// COOK first, and which to avoid. Pure + testable. Values are 1.21 food-points +
// saturation; she should eat the highest-saturation SAFE food she has, and cook raw
// meat (via the furnace/smoker) rather than eating it raw (some raws poison her).

// name -> { food, sat, risky? } — cooked/reliable foods first
const FOOD = {
  golden_carrot: { food: 6, sat: 14.4 },
  cooked_beef: { food: 8, sat: 12.8 },
  cooked_porkchop: { food: 8, sat: 12.8 },
  cooked_mutton: { food: 6, sat: 9.6 },
  cooked_salmon: { food: 6, sat: 9.6 },
  cooked_rabbit: { food: 5, sat: 6 },
  cooked_chicken: { food: 6, sat: 7.2 },
  cooked_cod: { food: 5, sat: 6 },
  baked_potato: { food: 5, sat: 6 },
  bread: { food: 5, sat: 6 },
  beetroot_soup: { food: 6, sat: 7.2 },
  mushroom_stew: { food: 6, sat: 7.2 },
  rabbit_stew: { food: 10, sat: 12 },
  apple: { food: 4, sat: 2.4 },
  carrot: { food: 3, sat: 3.6 },
  cooked_beef_steak: { food: 8, sat: 12.8 }, // alias safety
  melon_slice: { food: 2, sat: 1.2 },
  sweet_berries: { food: 2, sat: 0.4 },
  glow_berries: { food: 2, sat: 0.4 },
  dried_kelp: { food: 1, sat: 0.6 },
  // raw meats — edible but low + some risky; she prefers to cook them
  beef: { food: 3, sat: 1.8, raw: true },
  porkchop: { food: 3, sat: 1.8, raw: true },
  mutton: { food: 2, sat: 1.2, raw: true },
  rabbit: { food: 3, sat: 1.8, raw: true },
  salmon: { food: 2, sat: 1.2, raw: true },
  cod: { food: 2, sat: 1.2, raw: true },
  chicken: { food: 2, sat: 1.2, raw: true, risky: true },  // 30% food poisoning
  potato: { food: 1, sat: 0.6, raw: true },
};

// never eat unless truly desperate (net-negative / heavy poison)
const AVOID = new Set(['rotten_flesh', 'spider_eye', 'poisonous_potato', 'pufferfish', 'chicken']);

// raw item -> what it smelts into
const COOKS_TO = {
  beef: 'cooked_beef',
  porkchop: 'cooked_porkchop',
  chicken: 'cooked_chicken',
  mutton: 'cooked_mutton',
  rabbit: 'cooked_rabbit',
  salmon: 'cooked_salmon',
  cod: 'cooked_cod',
  potato: 'baked_potato',
  kelp: 'dried_kelp',
};

function isEdible(name) { return !!FOOD[name]; }
// food she would ACTUALLY eat (in the table, not on the avoid list) — the count the
// reflex layer must use, or "I have food" and "I can eat" disagree and she livelocks
// spamming eat() on a poisonous potato while starving (audit 2026-07-13).
function isSafeFood(name) { return !!FOOD[name] && !AVOID.has(name); }
function isRaw(name) { return !!(FOOD[name] && FOOD[name].raw); }
function cookedForm(name) { return COOKS_TO[name] || null; }

// Pick the best food she should eat now from a list of inventory item names.
// Prefer the highest saturation, skip the avoid-list unless `desperate`.
function bestFood(itemNames, desperate = false) {
  let best = null; let bestSat = -1;
  for (const name of itemNames || []) {
    if (!FOOD[name]) continue;
    if (!desperate && AVOID.has(name)) continue;
    const sat = FOOD[name].sat;
    if (sat > bestSat) { bestSat = sat; best = name; }
  }
  // desperate + only avoid-list food (e.g. rotten flesh) → allow it
  if (!best && desperate) {
    for (const name of itemNames || []) {
      if (name === 'rotten_flesh') return 'rotten_flesh';
    }
  }
  return best;
}

module.exports = { FOOD, AVOID, COOKS_TO, isEdible, isSafeFood, isRaw, cookedForm, bestFood };
