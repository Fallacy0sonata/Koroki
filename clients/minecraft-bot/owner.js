'use strict';

// Owner co-op channel (Phase 1, 2026-07-13). Koro-san plays IN the world with her,
// on stream — so his words are a FIRST-CLASS intent that preempts her autonomous
// projects (but never her survival reflexes). This parses his chat into a directive
// the intent arbiter can act on. Pure + testable; NL misses can fall back to the 8B
// later. Owner identity is set by MC_OWNER_USERNAME (any player if unset — fine on a
// private single-owner server).

const MATERIALS = [
  [/\b(wood|logs?|trees?|timber)\b/, 'log'],
  [/\b(cobble(stone)?|stone|rock)\b/, 'stone'],
  [/\bcoal\b/, 'coal'],
  [/\biron\b/, 'iron_ore'],
  [/\bgold\b/, 'gold_ore'],
  [/\bdiamonds?\b/, 'diamond_ore'],
  [/\bdirt\b/, 'dirt'],
  [/\bsand\b/, 'sand'],
];

function materialIn(text) {
  for (const [re, kw] of MATERIALS) if (re.test(text)) return kw;
  return null;
}

/**
 * Parse an owner chat line into a directive, or null if it isn't a command.
 * kinds: follow | release | come | wait | gather(+target) | mine | build
 * Order matters — more specific / higher-priority phrasings are tested first
 * (e.g. "stop following" is release, not "stop" -> wait; "come with me" is follow,
 * not "come here" -> come).
 */
function parseOwnerCommand(raw) {
  if (!raw) return null;
  const t = String(raw).toLowerCase().trim();

  // 0. CANCEL / negation — "stop mining", "don't follow me", "never mind" cancel a
  //    task (bare "stop"/"wait" with no task = hold position, handled by rule 7).
  //    Without this, "stop mining" hit the gather/mine rule and "don't follow" the
  //    follow rule — the exact OPPOSITE of what was said.
  if (/\b(never ?mind|nvm|cancel|forget it)\b/.test(t)
    || /\b(don'?t|do not|stop|quit)\s+(follow|come|coming|mine|mining|dig|digging|gather|gathering|chop|chopping|build|building|wait|going)\b/.test(t)) {
    return { kind: 'release' };
  }

  // 1. release / dismiss — must beat "stop" (wait) and "follow".
  if (/\b(stop follow(ing)?|dismiss|go (do )?your own|you'?re free|leave me|go away|on your own|do your own thing)\b/.test(t)) {
    return { kind: 'release' };
  }
  // 2. follow (incl. "come with me").
  if (/\b(follow me|follow|come with( me)?|stick with( me)?|with me|stay with me|tag along)\b/.test(t)) {
    return { kind: 'follow' };
  }
  // 3. gather a specific material / 4. generic mine.
  if (/\b(gather|get|chop|collect|mine|dig|grab|farm|harvest)\b/.test(t)) {
    const mat = materialIn(t);
    if (mat) return { kind: 'gather', target: mat };
    if (/\b(mine|dig|mining)\b/.test(t)) return { kind: 'mine' };
  }
  // 5. build.
  if (/\b(build|make|construct)\b/.test(t) && /\b(shelter|house|hut|home|base|wall|fort)\b/.test(t)) {
    return { kind: 'build' };
  }
  // 6. come here / to me.
  if (/\b(come here|over here|to me|this way|come to me|get over here|here koroki)\b/.test(t)) {
    return { kind: 'come' };
  }
  // 7. wait / stay / stop / hold.
  if (/\b(wait|hold on|hold up|stay( (here|put))?|stop|freeze|don'?t move|chill|pause)\b/.test(t)) {
    return { kind: 'wait' };
  }
  return null;
}

// Persistent intents keep running until changed/released; one-shots clear when done
// or after a timeout so a stale "come here" doesn't trap her forever.
const PERSISTENT = new Set(['follow', 'wait']);
const ONE_SHOT_TTL_MS = 60_000;

function isPersistent(kind) { return PERSISTENT.has(kind); }

module.exports = { parseOwnerCommand, materialIn, isPersistent, ONE_SHOT_TTL_MS };
