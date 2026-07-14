'use strict';

// Layer 3 — Projects & planner (Phase 1 of the Minecraft mind rebuild, 2026-07-13).
//
// The old model made "craft a pickaxe" ONE atomic skill that had to fully succeed
// in a single call — so any interrupt/desync lost all progress and restarted from
// zero. A PROJECT fixes that: it's a named ordered plan, and its progress lives in
// WORLD STATE (what she has, what's placed), not in a fragile in-memory counter. So
// `stepFor` just re-derives "which step is next" every time — resuming after an
// interrupt or reconnect is free, and there is nothing to corrupt.
//
// A project = { label, steps: [{ label, done(snap), act(snap) -> {goal, target} }] }.
// The engine advances to the first step whose `done` predicate is false.
//
// This is the SAFE, discretionary layer. Survival reflexes (decide.js) preempt it;
// the intent arbiter (bot.js) runs a project only when no reflex and no owner
// command is active. The captain (8B, later phase) will pick WHICH project; for now
// selectProject follows the natural survival-game progression deterministically.

function has(snap, sub) {
  return (snap.invNames || []).some((n) => n.includes(sub));
}

const PROJECTS = {
  wooden_tools: {
    label: 'get wooden tools',
    steps: [
      { label: 'get wood', done: (s) => s.inv.logIsh || s.inv.plankIsh, act: () => ({ goal: 'gather', target: 'log' }) },
      { label: 'craft a pickaxe', done: (s) => s.inv.hasPickaxe, act: () => ({ goal: 'craft', target: 'wooden_pickaxe' }) },
      // a sword SPECIFICALLY (an axe also reads as a weapon, so check for 'sword')
      { label: 'craft a sword', done: (s) => has(s, 'sword'), act: (s) => ({ goal: 'craft', target: s.inv.stoneIsh ? 'stone_sword' : 'wooden_sword' }) },
      // an axe — the right tool for chopping wood fast (also a strong weapon)
      { label: 'craft an axe', done: (s) => has(s, '_axe'), act: (s) => ({ goal: 'craft', target: s.inv.stoneIsh ? 'stone_axe' : 'wooden_axe' }) },
    ],
  },
  stone_tools: {
    label: 'upgrade to stone',
    steps: [
      { label: 'mine stone', done: (s) => s.inv.stoneIsh, act: () => ({ goal: 'gather', target: 'stone' }) },
      { label: 'stone pickaxe', done: (s) => has(s, 'stone_pickaxe'), act: () => ({ goal: 'craft', target: 'stone_pickaxe' }) },
      { label: 'stone sword', done: (s) => has(s, 'stone_sword'), act: () => ({ goal: 'craft', target: 'stone_sword' }) },
      { label: 'stone axe', done: (s) => has(s, 'stone_axe'), act: () => ({ goal: 'craft', target: 'stone_axe' }) },
    ],
  },
  iron_tools: {
    label: 'get iron gear',
    steps: [
      { label: 'set up a furnace', done: (s) => has(s, 'furnace'), act: () => ({ goal: 'craft', target: 'furnace' }) },
      { label: 'mine iron ore', done: (s) => has(s, 'raw_iron') || has(s, 'iron_ingot'), act: () => ({ goal: 'mine', target: 'iron_ore' }) },
      { label: 'smelt the iron', done: (s) => has(s, 'iron_ingot'), act: () => ({ goal: 'smelt', target: 'raw_iron' }) },
      { label: 'craft an iron pickaxe', done: (s) => has(s, 'iron_pickaxe'), act: () => ({ goal: 'craft', target: 'iron_pickaxe' }) },
      { label: 'craft an iron sword', done: (s) => has(s, 'iron_sword'), act: () => ({ goal: 'craft', target: 'iron_sword' }) },
      // SHIELD before the axe — defense is why she keeps dying, one ingot buys it
      { label: 'craft a shield', done: (s) => has(s, 'shield'), act: () => ({ goal: 'craft', target: 'shield' }) },
      { label: 'craft an iron axe', done: (s) => has(s, 'iron_axe'), act: () => ({ goal: 'craft', target: 'iron_axe' }) },
      { label: 'craft a bucket', done: (s) => has(s, 'bucket') || has(s, 'water_bucket'), act: () => ({ goal: 'craft', target: 'bucket' }) },
    ],
  },
  // The homestead — a bed means a spawn point, skipped nights, and the hunger-reset
  // strat actually being available. Runs once stone tools exist (canon: bed early).
  homestead: {
    label: 'set up a home',
    steps: [
      // a bed she PLACED (and slept in) counts — beds live at home, they are not
      // consumables to re-grind wool for every day (audit 2026-07-13)
      { label: 'get a bed', done: (s) => s.inv.hasBed || !!s.bedPlaced, act: () => ({ goal: 'craft', target: 'bed' }) },
      { label: 'get a hoe', done: (s) => has(s, '_hoe'), act: () => ({ goal: 'craft', target: 'stone_hoe' }) },
    ],
  },
  // Iron armor once she has spare ingots — she used to HOARD armor without wearing
  // it; now she also MAKES it (equipArmor auto-wears whatever exists).
  iron_armor: {
    label: 'forge some armor',
    steps: [
      { label: 'iron chestplate', done: (s) => has(s, 'chestplate'), act: () => ({ goal: 'craft', target: 'iron_chestplate' }) },
      { label: 'iron helmet', done: (s) => has(s, 'helmet'), act: () => ({ goal: 'craft', target: 'iron_helmet' }) },
    ],
  },
  diamond_tools: {
    label: 'get diamond gear',
    steps: [
      { label: 'mine diamonds', done: (s) => has(s, 'diamond'), act: () => ({ goal: 'mine', target: 'diamond' }) },
      { label: 'craft a diamond pickaxe', done: (s) => has(s, 'diamond_pickaxe'), act: () => ({ goal: 'craft', target: 'diamond_pickaxe' }) },
      { label: 'craft a diamond sword', done: (s) => has(s, 'diamond_sword'), act: () => ({ goal: 'craft', target: 'diamond_sword' }) },
    ],
  },
  stock_food: {
    label: 'stock some food',
    steps: [
      { label: 'get food (farm/hunt)', done: (s) => (s.inv.foodItems || 0) >= 3, act: () => ({ goal: 'farm' }) },
    ],
  },
  // A never-completing "wander" project so the arbiter always has something to run.
  explore: {
    label: 'explore around',
    steps: [
      { label: 'wander', done: () => false, act: () => ({ goal: 'explore' }) },
    ],
  },
};

// Pick a project when the captain hasn't chosen one: the natural progression a
// real player follows. Returns a project NAME (always a valid key of PROJECTS).
// best pickaxe tier she owns: 0 none, 1 wood/gold, 2 stone, 3 iron, 4 diamond+
function pickTier(snap) {
  if (has(snap, 'netherite_pickaxe') || has(snap, 'diamond_pickaxe')) return 4;
  if (has(snap, 'iron_pickaxe')) return 3;
  if (has(snap, 'stone_pickaxe')) return 2;
  if (has(snap, 'wooden_pickaxe') || has(snap, 'golden_pickaxe')) return 1;
  return 0;
}

// Progression gated by MINING tier (the real bottleneck): each tier's pickaxe
// unlocks the next ore. A weapon is required before leaving the wood stage.
// `parked` = projects that kept failing (no sheep for the bed, etc.) and are on a
// timeout — skip them so a dead-end can't gate the whole ladder (audit 2026-07-13).
function selectProject(snap, parked) {
  const inv = (snap && snap.inv) || {};
  const pt = pickTier(snap);
  const ok = (name) => !(parked && parked.has(name));
  if ((pt < 1 || !inv.hasWeapon) && ok('wooden_tools')) return 'wooden_tools';
  if (pt < 2 && ok('stone_tools')) return 'stone_tools';
  // a bed before the deep-iron push (canon: day 1-2 priority; spawn discipline).
  // A bed placed at home counts — don't re-grind wool after sleeping in it.
  if (pt >= 1 && !inv.hasBed && !snap.bedPlaced && ok('homestead')) return 'homestead';
  if (pt < 3 && ok('iron_tools')) return 'iron_tools';   // stone pick mines iron ore
  // armor before diamond caving IF the iron is already banked (never grind for it)
  if (pt >= 3 && !has(snap, 'chestplate') && (inv.ironIngots || 0) >= 13 && ok('iron_armor')) return 'iron_armor';
  // pt >= 3 REQUIRED: when iron_tools got parked, the bare `pt < 4` fell through to
  // a diamond hunt WITHOUT an iron pickaxe (round 17: "finding diamond with no iron
  // pick"). No tier-skipping — parked iron means food/explore until it unparks.
  if (pt >= 3 && pt < 4 && ok('diamond_tools')) return 'diamond_tools';
  if ((inv.foodItems || 0) < 3 && ok('stock_food')) return 'stock_food';
  return 'explore';
}

// The next action toward a project. Returns { goal, target, label, project,
// projectLabel } for the first unsatisfied step, or { done: true, project } when
// the whole project is complete (unknown project => done:true).
function stepFor(projectName, snap) {
  const proj = PROJECTS[projectName];
  if (!proj) return { done: true, project: projectName };
  for (const step of proj.steps) {
    if (!step.done(snap)) {
      const a = step.act(snap) || {};
      return {
        goal: a.goal,
        target: a.target || null,
        label: step.label,
        project: projectName,
        projectLabel: proj.label,
      };
    }
  }
  return { done: true, project: projectName };
}

function isProject(name) { return Object.prototype.hasOwnProperty.call(PROJECTS, name); }

// Can this project be worked at her current tier? The director (8B) proposes;
// this is the code-side veto that keeps a hallucinated pick from wedging her.
function feasibleProject(name, snap) {
  const pt = pickTier(snap);
  switch (name) {
    case 'wooden_tools': case 'stock_food': case 'explore': return true;
    case 'stone_tools': case 'homestead': return pt >= 1;
    case 'iron_tools': return pt >= 2;
    case 'iron_armor': case 'diamond_tools': return pt >= 3;
    default: return false;
  }
}

// A director suggestion must be both reachable and unfinished.  Without the
// completion check the brain can repeatedly "adopt" an already-finished
// project, announce it in chat, and have the deterministic ladder discard it
// again on the very next tick.
function actionableProject(name, snap) {
  return isProject(name) && feasibleProject(name, snap) && !stepFor(name, snap).done;
}

module.exports = {
  PROJECTS, selectProject, stepFor, isProject, pickTier, feasibleProject, actionableProject,
};
