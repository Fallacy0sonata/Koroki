'use strict';
// Recipe resolver tests — the material-resolution logic (baseAcquire, smelt path,
// resolveMaterial) which needs no minecraft-data. The recipe-delta parsing in
// nextStepFor is exercised live (needs the real recipe DB).

const assert = require('assert');
const { baseAcquire, resolveMaterial, SMELT_FROM } = require('./recipe');

let pass = 0;
const t = (name, cond) => { assert.ok(cond, name); console.log(`  ok  ${name}`); pass++; };

// ── baseAcquire (pure) — how each raw material is obtained ─────────────────────
t('log -> gather log', baseAcquire('oak_log').do === 'gather' && baseAcquire('oak_log').arg === 'log');
t('cobblestone -> mine stone (the stone_sword loop fix)', baseAcquire('cobblestone').do === 'mine' && baseAcquire('cobblestone').arg === 'stone');
t('raw_iron -> mine iron_ore', baseAcquire('raw_iron').arg === 'iron_ore');
t('diamond -> mine diamond', baseAcquire('diamond').arg === 'diamond');
t('coal -> mine coal_ore', baseAcquire('coal').arg === 'coal_ore');
t('planks are NOT base (craftable)', baseAcquire('oak_planks') === null);
t('stone_sword is NOT base (craftable)', baseAcquire('stone_sword') === null);

// ── smelt path via resolveMaterial (uses inventory, not minecraft-data) ───────
t('iron_ingot smelts from raw_iron', SMELT_FROM.iron_ingot === 'raw_iron');
const botWithRaw = { inventory: { items: () => [{ name: 'raw_iron', count: 3 }] } };
const botNoRaw = { inventory: { items: () => [] } };
t('want iron_ingot, have raw_iron -> smelt it',
  (() => { const s = resolveMaterial(botWithRaw, 'iron_ingot', 1, 0); return s.do === 'smelt' && s.arg === 'raw_iron'; })());
t('want iron_ingot, no raw_iron -> mine iron_ore first',
  (() => { const s = resolveMaterial(botNoRaw, 'iron_ingot', 1, 0); return s.do === 'mine' && s.arg === 'iron_ore'; })());
t('cobblestone resolves straight to mining stone',
  (() => { const s = resolveMaterial(botNoRaw, 'cobblestone', 2, 0); return s.do === 'mine' && s.arg === 'stone'; })());

// ── wood is species-flexible (round 16: infinite acacia hoarding for an "oak" pickaxe) ─
const botAcacia = { inventory: { items: () => [{ name: 'acacia_log', count: 8 }] } };
const botStripped = { inventory: { items: () => [{ name: 'stripped_birch_log', count: 2 }] } };
t('want oak_planks, holding ACACIA logs -> craft acacia_planks (not chase oak)',
  (() => { const s = resolveMaterial(botAcacia, 'oak_planks', 3, 0); return s.do === 'craft' && s.arg === 'acacia_planks'; })());
t('stripped logs count as wood too',
  (() => { const s = resolveMaterial(botStripped, 'oak_planks', 3, 0); return s.do === 'craft' && s.arg === 'birch_planks'; })());
t('want planks with NO logs at all -> gather log',
  (() => { const s = resolveMaterial(botNoRaw, 'birch_planks', 3, 0); return s.do === 'gather' && s.arg === 'log'; })());

console.log(`\nRECIPE: ${pass} PASS`);
