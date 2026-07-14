'use strict';

const axios = require('axios');

const ORCHESTRATOR_URL = process.env.ORCHESTRATOR_URL || 'http://127.0.0.1:9882';
// The routed brain (owner design 2026-07-11): the 8B does Minecraft THINKING —
// goals, logic, planning — while the 4B stays the persona mask for what she SAYS.
// Goal decisions hit GOAL_BRAIN_URL's schema-locked /v1/plan; on the pod point
// it at the 8B instance (:9883). Defaults to the 4B (:9881) so it still works
// on one card. Commentary always routes through the orchestrator = the 4B voice.
const BRAIN_URL = process.env.BRAIN_URL || 'http://127.0.0.1:9881';
const GOAL_BRAIN_URL = process.env.GOAL_BRAIN_URL || BRAIN_URL;
const BOT_USER_ID = process.env.MC_BOT_USER_ID || 'koroki_minecraft';

function uuidHex() {
  return Math.random().toString(16).slice(2, 14);
}

// Discretionary goals the brain may choose (the SAFE, proactive ones — reflexes
// like fight/flee/eat/sleep are code-owned and never offered here).
const GOAL_SCHEMA = {
  type: 'object',
  properties: {
    goal: { type: 'string', enum: ['gather', 'mine', 'build_shelter', 'hunt', 'explore', 'follow', 'place_torch'] },
    target: { type: 'string', maxLength: 30 },
    reason: { type: 'string', maxLength: 60 },
  },
  required: ['goal'],
};

const DECIDE_SYSTEM = [
  "You choose Koroki's next Minecraft goal while she is SAFE (no threats).",
  'Pick ONE goal that fits what she has and lacks — like a real player deciding what to do next.',
  'gather needs a target block word (log, stone, coal, iron_ore...); mine = go deeper for ore;',
  'follow needs a player name; build_shelter/hunt/explore/place_torch take no target.',
  'Prefer progress: get wood if low, stone next, then tools/ore; explore if well-stocked.',
  'Output JSON only.',
].join(' ');

function summarizeForBrain(snapshot) {
  const inv = snapshot.inv || {};
  const have = [];
  if (inv.logIsh || inv.plankIsh) have.push('wood');
  if (inv.stoneIsh) have.push('stone');
  if (inv.hasPickaxe) have.push('pickaxe');
  if (inv.hasWeapon) have.push('weapon');
  if (inv.foodItems > 0) have.push(`${inv.foodItems} food`);
  const players = (snapshot.players || []).map((p) => p.name).join(',') || 'none';
  return [
    `time:${snapshot.timeLabel} hp:${snapshot.hp} food:${snapshot.food}`,
    `has:${have.join('+') || 'almost nothing'}`,
    `nearby_players:${players}`,
    'She is safe. What should she do next?',
  ].join(' | ');
}

/**
 * Ask the brain to pick a discretionary goal. Returns {goal, target, reason} or
 * null (brain down / unparseable → caller falls back to the default rotation).
 */
async function decideGoal(snapshot) {
  try {
    const resp = await axios.post(`${GOAL_BRAIN_URL}/v1/plan`, {
      request_id: `mc_goal_${uuidHex()}`,
      system: DECIDE_SYSTEM,
      message: summarizeForBrain(snapshot),
      json_schema: GOAL_SCHEMA,
      max_new_tokens: 80,
    }, { timeout: 15000 });
    const plan = resp.data.plan || {};
    if (!plan.goal) return null;
    return { goal: plan.goal, target: plan.target || null, reason: plan.reason || 'brain choice' };
  } catch (err) {
    // 501 = transformers rollback (no schema filter); anything else = brain down
    console.warn('[BrainClient] goal decision failed:', err.message);
    return null;
  }
}

// ── The DIRECTOR (ARC M, 2026-07-13): the captain finally sits in the goal loop ──
// The gap analysis found decideGoal was never wired — goal choice was 100%
// deterministic, which is exactly why she felt robotic. The director asks the brain
// to pick her next PROJECT (not per-tick goals — Aternos + LLM latency make tick-
// level decisions impossible) plus a short spoken intention. Code keeps the veto:
// reflexes always preempt, and an invalid/infeasible pick falls back to the
// deterministic ladder. Runs every few minutes, when calm.
const DESIRE_SCHEMA = {
  type: 'object',
  properties: {
    project: {
      type: 'string',
      enum: ['wooden_tools', 'stone_tools', 'homestead', 'iron_tools', 'iron_armor',
        'diamond_tools', 'stock_food', 'explore'],
    },
    say: { type: 'string', maxLength: 90 },
  },
  required: ['project'],
};
const DESIRE_SYSTEM = [
  "You pick Koroki's next Minecraft PROJECT while she is safe — like a player deciding what today is about.",
  'Choose from: wooden_tools stone_tools homestead(bed+hoe) iron_tools iron_armor diamond_tools stock_food explore.',
  "Respect the tech tree (no diamond push without an iron pickaxe; no iron without stone tools).",
  'Balance progress with living: food when low, a bed if she has none, explore when stocked and curious.',
  '"say" = her spoken intention, <=10 casual words, first person, her voice. Output JSON only.',
].join(' ');

async function chooseDesire(contextLine) {
  try {
    const resp = await axios.post(`${GOAL_BRAIN_URL}/v1/plan`, {
      request_id: `mc_desire_${uuidHex()}`,
      system: DESIRE_SYSTEM,
      message: contextLine,
      json_schema: DESIRE_SCHEMA,
      max_new_tokens: 90,
    }, { timeout: 15000 });
    const plan = resp.data.plan || {};
    if (!plan.project) return null;
    return { project: plan.project, say: (plan.say || '').trim() || null };
  } catch (err) {
    console.warn('[BrainClient] desire pick failed:', err.message);
    return null; // deterministic ladder takes over — she never stalls on a dead brain
  }
}

// Commentary QC gate (owner design 2026-07-13): game-mode only. The 4B is the MOUTH
// (persona voice); the 8B (GOAL_BRAIN_URL) is a QC WORKER that compares what she said
// against the ground-truth event and FIXES misstated facts (wrong mob/item/action,
// invented details) while keeping her exact voice. 8B "leaking thoughts" don't matter
// here — it's a fact-checker, not the speaker. Toggle with MC_COMMENTARY_QC=off.
const QC_ON = process.env.MC_COMMENTARY_QC !== 'off';
if (QC_ON && GOAL_BRAIN_URL === BRAIN_URL) {
  console.warn('[BrainClient] QC gate DEGRADED: GOAL_BRAIN_URL == BRAIN_URL (the 4B is fact-checking ITSELF). Point GOAL_BRAIN_URL at the pod 8B for a real independent gate.');
}
const QC_SYSTEM = [
  'You are a strict fact-check gate for a Minecraft AI streamer named Koroki.',
  'You are given the GROUND TRUTH of what is actually happening and the line she just said.',
  'Check ONLY factual accuracy vs the ground truth: did she name the right mob/item/block/action,',
  'or did she claim something that did not happen or invent a detail?',
  'If accurate (or harmless flavor), return it unchanged. If a FACT is wrong, return her line with',
  'ONLY the wrong facts fixed — keep her exact casual voice, tone, and length. Never lengthen or add narration.',
  'NEVER copy the ground-truth text, coordinates, stats (hp/food), or mob-distance lists into the line.',
  'Output JSON: {"accurate": bool, "corrected": "<her line>"}.',
].join(' ');

// Telemetry-leak guard (live round 16): the raw world-state context escaped into
// her chat ("(20,42,268) night clear weather 18/20 hp… spiders@16meast"). These are
// STRUCTURAL signatures of the context format, not character filtering — a line
// carrying them is a pipeline leak, never a thing she'd say.
const LEAK_PATTERNS = [
  /\d+\/20/,                      // "18/20 hp" stat fractions
  /@\d+m/,                        // "spider@16meast" entity lists
  /\b\d+m (east|west|north|south)/, // "at 13m west" telemetry-speak
  /\(-?\d+,-?\d+,-?\d+\)/,        // raw coordinates
  /players:|HOSTILES:|minecraft_event|looking_at:|inv:/,
];
function leakedTelemetry(text) { return LEAK_PATTERNS.some((re) => re.test(text || '')); }
const QC_SCHEMA = {
  type: 'object',
  properties: {
    accurate: { type: 'boolean' },
    corrected: { type: 'string', maxLength: 220 },
  },
  required: ['accurate', 'corrected'],
};

async function qcCommentary(groundTruth, draft) {
  try {
    const resp = await axios.post(`${GOAL_BRAIN_URL}/v1/plan`, {
      request_id: `mc_qc_${uuidHex()}`,
      system: QC_SYSTEM,
      message: `GROUND TRUTH: ${(groundTruth || '').slice(0, 420)}\nKOROKI SAID: "${(draft || '').slice(0, 200)}"`,
      json_schema: QC_SCHEMA,
      max_new_tokens: 120,
    }, { timeout: 12000 });
    const plan = resp.data.plan || {};
    const fixed = typeof plan.corrected === 'string' ? plan.corrected.trim() : '';
    if (!fixed) return draft;                     // gate returned nothing usable → keep draft
    if (fixed.toLowerCase() === '[silent]') return null;
    // the gate must never make things WORSE: a "correction" that dumps ground truth
    // or balloons the line is a leak, not a fix — keep the original draft.
    if (leakedTelemetry(fixed) || fixed.length > draft.length * 1.6 + 24) {
      console.log('[BrainClient] QC output leaked telemetry/ballooned — keeping the draft');
      return draft;
    }
    if (plan.accurate === false) console.log(`[BrainClient] QC fixed commentary → "${fixed}"`);
    return fixed;
  } catch (err) {
    // 8B down / QC unavailable → never block her voice; pass the draft through.
    return draft;
  }
}

/**
 * Ask Koroki's brain for a reaction to a Minecraft event, then (game mode) run it
 * through the 8B fact-check gate. Returns the gated text, or null if silent/empty.
 */
async function requestCommentary({ gameEventContext, relationshipScore = 0, isOwner = false, flavor = false }) {
  let draft;
  try {
    const resp = await axios.post(`${ORCHESTRATOR_URL}/v1/chat`, {
      request_id: `mc_${uuidHex()}`,
      message: '.',
      defer_tts: true,
      proactive: true,
      game_event_context: (gameEventContext || '').slice(0, 510), // orchestrator caps at 512
      user_context: {
        user_id: BOT_USER_ID,
        relationship_score: relationshipScore,
        is_owner: isOwner,
        mode: 'auto',
        platform: 'minecraft',
      },
    }, { timeout: 20000 });
    draft = (resp.data.text || '').trim();
  } catch (err) {
    console.warn('[BrainClient] Commentary request failed:', err.message);
    return null;
  }
  if (!draft || draft.toLowerCase() === '[silent]' || draft === '.') return null;
  // a draft that echoes the context block is a pipeline leak — never say it
  if (leakedTelemetry(draft)) {
    console.log('[BrainClient] draft leaked telemetry — suppressed');
    return null;
  }
  // 4B = mouth, 8B = QC gate. Verify FACTUAL lines against ground truth; pure flavor
  // (fumble, ambient) has nothing to fact-check, so skip the gate (and its latency).
  return (QC_ON && !flavor) ? qcCommentary(gameEventContext, draft) : draft;
}

module.exports = { requestCommentary, qcCommentary, decideGoal, summarizeForBrain, GOAL_SCHEMA, chooseDesire, DESIRE_SCHEMA };
