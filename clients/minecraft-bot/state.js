'use strict';

/**
 * Koroki's Minecraft persistent state.
 *
 * Tracks what she's doing, what she's done, and what she's collected so
 * world-state.js can include this in every context block — giving her a
 * memory of her own actions that persists across reconnects.
 */

const fs = require('fs');
const path = require('path');

// Store in the repo's data/ dir alongside other persistent state.
const STATE_FILE = path.resolve(__dirname, '../../data/minecraft_state.json');

const _DEFAULTS = {
  currentGoal: null,          // { name, startedAt (ms) }
  goalQueue: [],              // next planned goals (strings)
  goalHistory: [],            // [{ goal, completedAt, success }] — last 8
  resources: {},              // { item_name: total_count_ever_collected }
  sessionStart: null,         // ms epoch when bot spawned
  totalGoalsCompleted: 0,
};

let _state = { ..._DEFAULTS };

function _load() {
  try {
    if (fs.existsSync(STATE_FILE)) {
      const raw = JSON.parse(fs.readFileSync(STATE_FILE, 'utf8'));
      _state = { ..._DEFAULTS, ...raw };
    } else {
      _state = { ..._DEFAULTS, sessionStart: Date.now() };
    }
  } catch (_) {
    _state = { ..._DEFAULTS, sessionStart: Date.now() };
  }
}

function _save() {
  try {
    fs.mkdirSync(path.dirname(STATE_FILE), { recursive: true });
    fs.writeFileSync(STATE_FILE, JSON.stringify(_state, null, 2), 'utf8');
  } catch (_) {}
}

// ── Public API ──────────────────────────────────────────────────────────────

function onSpawn() {
  _state.sessionStart = Date.now();
  _state.currentGoal = null;
  _save();
}

function setGoal(goalName) {
  _state.currentGoal = { name: goalName, startedAt: Date.now() };
  _save();
}

function completeGoal(goalName, success = true) {
  if (_state.currentGoal?.name === goalName) {
    _state.goalHistory = [
      { goal: goalName, completedAt: Date.now(), success },
      ..._state.goalHistory,
    ].slice(0, 8);
    _state.currentGoal = null;
    if (success) _state.totalGoalsCompleted++;
    _save();
  }
}

/** Call whenever items land in inventory. items = [{name, count}] */
function addResources(items) {
  for (const { name, count } of items) {
    _state.resources[name] = (_state.resources[name] || 0) + count;
  }
  _save();
}

/**
 * Returns a compact context string for world-state injection.
 * e.g. "doing:mine_wood(4m) | next:mine_stone,explore | collected:oak_logx12,stonex8"
 */
function getContext() {
  const parts = [];

  if (_state.currentGoal) {
    const elapsedMin = Math.floor((Date.now() - _state.currentGoal.startedAt) / 60_000);
    parts.push(`doing:${_state.currentGoal.name}(${elapsedMin}m)`);
  }

  if (_state.goalQueue.length) {
    parts.push(`next:${_state.goalQueue.slice(0, 3).join(',')}`);
  }

  const recent = _state.goalHistory.slice(0, 3).map(g => g.goal).join(',');
  if (recent) parts.push(`recent:${recent}`);

  const topResources = Object.entries(_state.resources)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 6)
    .map(([name, count]) => `${name}x${count}`)
    .join(',');
  if (topResources) parts.push(`collected:${topResources}`);

  return parts.join(' | ');
}

_load();

module.exports = { onSpawn, setGoal, completeGoal, addResources, getContext };
