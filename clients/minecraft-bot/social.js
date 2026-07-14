'use strict';

// Layer 4 — Social model (Phase 4, 2026-07-13). Turns raw player interactions into
// typed social INTENT so the captain can react like a teammate: a gift earns warmth;
// repeated hits from Koro-san read as playful roughhousing (never retaliation — she
// does NOT fight the owner); a single tap is a poke. Pure classify() is tested; the
// tracker keeps short-lived per-player tallies.

const HIT_WINDOW_MS = 6000;
const TROLL_HITS = 2;
const GIFT_FRESH_MS = 4000;

// rec = { hits: [timestamps], giftAt } -> 'gift' | 'troll' | 'poke' | null
function classify(rec, now = Date.now()) {
  if (!rec) return null;
  if (rec.giftAt && now - rec.giftAt < GIFT_FRESH_MS) return 'gift';
  const recentHits = (rec.hits || []).filter((t) => now - t < HIT_WINDOW_MS);
  if (recentHits.length >= TROLL_HITS) return 'troll';
  if (recentHits.length === 1) return 'poke';
  return null;
}

function createSocial() {
  const byPlayer = {};
  const rec = (who) => (byPlayer[who] = byPlayer[who] || { hits: [], giftAt: 0 });
  return {
    noteHit(who, now = Date.now()) {
      const r = rec(who);
      r.hits.push(now);
      r.hits = r.hits.filter((t) => now - t < HIT_WINDOW_MS);
    },
    noteGift(who, now = Date.now()) { rec(who).giftAt = now; },
    intentFor(who, now = Date.now()) { return classify(byPlayer[who], now); },
    clear(who) { delete byPlayer[who]; },
  };
}

module.exports = { classify, createSocial, HIT_WINDOW_MS, TROLL_HITS };
