'use strict';

// Layer 1 — World model (spatial memory, Phase 2, 2026-07-13).
//
// She builds a DECAYING map of the world from perception over time: her base, the
// stations she placed, resources & hazards she has seen, cave mouths. This is what
// lets higher layers ask "nearest known cave", "take me back to base", "there's
// lava over there, avoid it" — none of which are possible from a snapshot of *now*.
// Persisted per-server so it survives reconnects.
//
// The store is plain data (no live block handles) so it serializes cleanly and is
// unit-testable without a server. bot.js feeds it via observe() each slow tick.

const fs = require('fs');
const path = require('path');

const DECAY_MS = 20 * 60 * 1000; // forget unseen things after ~20 min

function rkey(p) { return `${Math.round(p.x)},${Math.round(p.y)},${Math.round(p.z)}`; }
function rpos(p) { return { x: Math.round(p.x), y: Math.round(p.y), z: Math.round(p.z) }; }
function dist(a, b) { return Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z); }

const CATS = ['landmarks', 'resources', 'hazards', 'caves'];

function createWorldModel(saveFile, now = Date.now) {
  const store = { base: null, landmarks: {}, resources: {}, hazards: {}, caves: {} };

  function load() {
    try {
      if (saveFile && fs.existsSync(saveFile)) {
        const raw = JSON.parse(fs.readFileSync(saveFile, 'utf8'));
        Object.assign(store, { base: null, landmarks: {}, resources: {}, hazards: {}, caves: {} }, raw);
      }
    } catch (_) { /* corrupt file → start fresh */ }
  }
  function save() {
    try {
      if (!saveFile) return;
      fs.mkdirSync(path.dirname(saveFile), { recursive: true });
      fs.writeFileSync(saveFile, JSON.stringify(store));
    } catch (_) {}
  }

  function note(cat, pos, tag) {
    if (!CATS.includes(cat) || !pos) return;
    store[cat][rkey(pos)] = { pos: rpos(pos), tag: tag || cat, at: now() };
  }
  // committed=true marks a DELIBERATE home (first real shelter/bed spot) — the
  // spawn-point default base yields to it and it never gets overwritten by accident.
  function setBase(pos, committed = false) {
    if (store.base && store.base.committed && !committed) return; // don't demote a real home
    store.base = { ...rpos(pos), at: now(), committed: !!committed };
    save();
  }
  function getBase() { return store.base; }

  // nearest live (non-decayed) entry in a category to fromPos, optional filter.
  // LANDMARKS never decay: they are placed infrastructure (her chest, furnace,
  // table, bed). The 20-min TTL made her forget her OWN base chest and then loot
  // it as "found treasure" (audit 2026-07-13). Sightings (resources/hazards/caves)
  // still decay — the world changes; her furniture doesn't walk away.
  function nearest(cat, fromPos, filter) {
    let best = null; let bd = Infinity; const t = now();
    for (const k of Object.keys(store[cat] || {})) {
      const e = store[cat][k];
      if (cat !== 'landmarks' && t - e.at > DECAY_MS) { delete store[cat][k]; continue; }
      if (filter && !filter(e)) continue;
      const d = dist(e.pos, fromPos);
      if (d < bd) { bd = d; best = e; }
    }
    return best;
  }

  function decay() {
    const t = now();
    for (const cat of CATS) {
      if (cat === 'landmarks') continue; // infrastructure is permanent
      for (const k of Object.keys(store[cat])) {
        if (t - store[cat][k].at > DECAY_MS) delete store[cat][k];
      }
    }
  }

  function count(cat) { return Object.keys(store[cat] || {}).length; }

  // landmarks never decay, so REPOSSESSED infrastructure (a bed she dug up and
  // took along) must be explicitly forgotten or done-predicates see a ghost.
  function removeLandmark(pos) { delete store.landmarks[rkey(pos)]; }

  return {
    store, load, save, note, setBase, getBase, nearest, decay, count, removeLandmark,
    noteResource: (pos, tag) => note('resources', pos, tag),
    noteHazard: (pos, tag) => note('hazards', pos, tag),
    noteCave: (pos) => note('caves', pos, 'cave'),
    noteLandmark: (pos, tag) => note('landmarks', pos, tag),
  };
}

module.exports = { createWorldModel, dist, DECAY_MS };
