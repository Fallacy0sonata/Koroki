'use strict';
// Layer 3 unit tests — projects engine (no server). Covers the tech-tree
// progression that moved out of decide.js, plus project selection + resumption.

const assert = require('assert');
const { selectProject, stepFor, actionableProject } = require('./projects');

// snapshot builder: inv flags + invNames must be kept consistent by the caller.
function snap(over = {}) {
  const inv = {
    logIsh: false, plankIsh: false, stoneIsh: false, foodItems: 5,
    hasWeapon: false, hasPickaxe: false, hasBed: false, torches: 0, ...(over.inv || {}),
  };
  return { inv, invNames: over.invNames || [] };
}
let pass = 0;
const t = (name, cond) => { assert.ok(cond, name); console.log(`  ok  ${name}`); pass++; };

// ── selectProject: the natural progression ────────────────────────────────────
t('no tools -> wooden_tools', selectProject(snap()) === 'wooden_tools');
t('pickaxe+sword but no stone tools -> stone_tools',
  selectProject(snap({ inv: { hasPickaxe: true, hasWeapon: true }, invNames: ['wooden_pickaxe', 'wooden_sword'] })) === 'stone_tools');
// canon (2026-07-13 rhythm arc): a bed comes BEFORE the deep iron push — spawn
// discipline is the day 1-2 priority for real players.
t('stone-tooled, no bed -> homestead (bed first)',
  selectProject(snap({ inv: { hasPickaxe: true, hasWeapon: true }, invNames: ['stone_pickaxe', 'stone_sword'] })) === 'homestead');
t('stone-tooled WITH bed -> iron_tools next',
  selectProject(snap({ inv: { hasPickaxe: true, hasWeapon: true, hasBed: true }, invNames: ['stone_pickaxe', 'stone_sword', 'red_bed'] })) === 'iron_tools');
t('iron-tooled -> diamond_tools next',
  selectProject(snap({ inv: { hasPickaxe: true, hasWeapon: true, hasBed: true }, invNames: ['iron_pickaxe', 'iron_sword'] })) === 'diamond_tools');
t('iron-tooled + banked iron, no chestplate -> iron_armor',
  selectProject(snap({ inv: { hasPickaxe: true, hasWeapon: true, hasBed: true, ironIngots: 16 }, invNames: ['iron_pickaxe', 'iron_sword'] })) === 'iron_armor');
t('diamond-tooled but low food -> stock_food',
  selectProject(snap({ inv: { hasPickaxe: true, hasWeapon: true, hasBed: true, foodItems: 0 },
    invNames: ['diamond_pickaxe', 'diamond_sword'] })) === 'stock_food');
t('fully kitted -> explore',
  selectProject(snap({ inv: { hasPickaxe: true, hasWeapon: true, hasBed: true, foodItems: 5 },
    invNames: ['diamond_pickaxe', 'diamond_sword'] })) === 'explore');
t('diamond-tooled but NO bed -> homestead still wins',
  selectProject(snap({ inv: { hasPickaxe: true, hasWeapon: true, foodItems: 5 },
    invNames: ['diamond_pickaxe', 'diamond_sword'] })) === 'homestead');

// iron_tools step order: furnace -> mine -> smelt -> pickaxe -> sword
t('iron step 1: no furnace -> craft furnace',
  (() => { const x = stepFor('iron_tools', snap({ invNames: ['stone_pickaxe'] })); return x.goal === 'craft' && x.target === 'furnace'; })());
t('iron step 2: furnace, no iron -> mine iron_ore',
  (() => { const x = stepFor('iron_tools', snap({ invNames: ['stone_pickaxe', 'furnace'] })); return x.goal === 'mine' && x.target === 'iron_ore'; })());
t('iron step 3: raw_iron -> smelt',
  (() => { const x = stepFor('iron_tools', snap({ invNames: ['furnace', 'raw_iron'] })); return x.goal === 'smelt' && x.target === 'raw_iron'; })());
t('iron step 4: iron_ingot -> craft iron_pickaxe',
  (() => { const x = stepFor('iron_tools', snap({ invNames: ['furnace', 'iron_ingot'] })); return x.goal === 'craft' && x.target === 'iron_pickaxe'; })());

// ── wooden_tools step order (resume-from-state) ───────────────────────────────
let s = snap();
t('wooden_tools step 1: no wood -> gather log',
  (() => { const x = stepFor('wooden_tools', s); return x.goal === 'gather' && x.target === 'log'; })());
s = snap({ inv: { logIsh: true }, invNames: ['oak_log'] });
t('wooden_tools step 2: have wood, no pick -> craft wooden_pickaxe',
  (() => { const x = stepFor('wooden_tools', s); return x.goal === 'craft' && x.target === 'wooden_pickaxe'; })());
s = snap({ inv: { logIsh: true, hasPickaxe: true }, invNames: ['oak_log', 'wooden_pickaxe'] });
t('wooden_tools step 3: have pick, no weapon -> craft wooden_sword',
  (() => { const x = stepFor('wooden_tools', s); return x.goal === 'craft' && x.target === 'wooden_sword'; })());
s = snap({ inv: { logIsh: true, hasPickaxe: true, hasWeapon: true }, invNames: ['wooden_pickaxe', 'wooden_sword'] });
t('wooden_tools: has pick+sword, no axe -> craft axe',
  (() => { const x = stepFor('wooden_tools', s); return x.goal === 'craft' && x.target === 'wooden_axe'; })());
s = snap({ inv: { logIsh: true, hasPickaxe: true, hasWeapon: true }, invNames: ['wooden_pickaxe', 'wooden_sword', 'wooden_axe'] });
t('wooden_tools complete (pick+sword+axe) -> done',
  (() => { const x = stepFor('wooden_tools', s); return x.done === true; })());

// resumption: a half-done project picks up at the right step, never step 1
s = snap({ inv: { plankIsh: true, hasPickaxe: true }, invNames: ['oak_planks', 'wooden_pickaxe'] });
t('resume: planks+pickaxe -> jumps to the sword step (not re-gathering)',
  (() => { const x = stepFor('wooden_tools', s); return x.goal === 'craft' && x.target === 'wooden_sword'; })());

// ── stone_tools: sword tier follows cobble availability ───────────────────────
s = snap({ inv: { hasPickaxe: true, hasWeapon: true, stoneIsh: true }, invNames: ['stone', 'wooden_pickaxe'] });
t('stone_tools step: have stone, no stone pick -> craft stone_pickaxe',
  (() => { const x = stepFor('stone_tools', s); return x.goal === 'craft' && x.target === 'stone_pickaxe'; })());

// ── explore never completes (always-available idle) ───────────────────────────
t('explore is a wander that never finishes', stepFor('explore', snap()).goal === 'explore');

// The 8B director may suggest a valid project whose steps are already done.
// It should not be adopted or announced as a new plan.
t('director may adopt an unfinished feasible project',
  actionableProject('iron_armor', snap({
    invNames: ['iron_pickaxe', 'iron_chestplate'],
  })) === true);
t('director cannot re-adopt an already complete project',
  actionableProject('iron_armor', snap({
    invNames: ['iron_pickaxe', 'iron_chestplate', 'iron_helmet'],
  })) === false);

console.log(`\nPROJECTS: ${pass} PASS`);
