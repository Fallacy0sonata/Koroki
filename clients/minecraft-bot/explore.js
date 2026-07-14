'use strict';

// Exploration strategy (live bugfix 2026-07-13). The old explore was a random walk
// that kept her wandering in a box. A real player COMMITS to a heading, pushes into
// unexplored ground, and READS the terrain — leaves a bad spot (island/desert),
// lingers in a good one. This module holds that state + the value evaluation. The
// pure helpers are unit-tested; the explore skill drives them with live block scans.
//
// (Boat-building to cross open ocean is a future upgrade; for now a "water" verdict
// makes her commit to a long frontier heading and swim/path across toward land.)

const DIRS8 = [];
for (let i = 0; i < 8; i++) DIRS8.push((i * Math.PI) / 4);
const CELL = 16; // coarse grid cell for the "have I been here" frontier map

function cellKey(x, z) { return `${Math.floor(x / CELL)},${Math.floor(z / CELL)}`; }

// Judge the immediate surroundings from block counts within a scan radius →
// verdict the explorer uses to decide "settle here / travel on / flee the water".
function evaluateSurroundings(c) {
  const water = c.water || 0;
  const land = (c.grass || 0) + (c.dirt || 0) + (c.stone || 0) + (c.sand || 0);
  const trees = c.logs || 0;
  if (water > 20 && water > land * 2) return { verdict: 'water', score: -2 };
  if (trees >= 3) return { verdict: 'good', score: 2 };
  if (land > 0 && trees === 0 && (c.sand || 0) > land * 0.5) return { verdict: 'poor', score: -1 };
  if (land > 0) return { verdict: 'ok', score: 1 };
  return { verdict: 'poor', score: -1 };
}

// The compass heading (radians) whose destination cell is least visited — pushes
// her toward the frontier instead of re-treading the same box.
function frontierHeading(visited, from, hop) {
  let best = 0; let bestVisits = Infinity;
  for (const h of DIRS8) {
    const tx = from.x + Math.cos(h) * hop;
    const tz = from.z + Math.sin(h) * hop;
    const v = visited.get(cellKey(tx, tz)) || 0;
    if (v < bestVisits) { bestVisits = v; best = h; }
  }
  return best;
}

function createExplorer(rng = Math.random) {
  const visited = new Map();
  let heading = rng() * Math.PI * 2;
  let legs = 0;
  let lastLandPos = null; // last spot she was on solid ground — retreat target over water
  let waterRetreats = 0;  // consecutive water-retreat legs — high = stuck on an island
  const MAX_LEGS = 4;     // commit to a heading for a few hops before reconsidering

  return {
    visited,
    get heading() { return heading; },
    get lastLandPos() { return lastLandPos; },
    get waterRetreats() { return waterRetreats; },
    // Plan the next travel leg from her current pos + the terrain verdict.
    plan(from, verdict) {
      const here = cellKey(from.x, from.z);
      visited.set(here, (visited.get(here) || 0) + 1);
      const water = verdict === 'water';
      if (!water) lastLandPos = { x: from.x, z: from.z };
      if (verdict === 'good') waterRetreats = 0; // found real land — island frustration over
      const hop = water ? 40 : 30;

      if (water && lastLandPos && (Math.abs(lastLandPos.x - from.x) > 1 || Math.abs(lastLandPos.z - from.z) > 1)) {
        // she swam into ocean — head BACK toward the land she came from, don't go
        // deeper (this is why she crossed 500 blocks of water before).
        heading = Math.atan2(lastLandPos.z - from.z, lastLandPos.x - from.x);
        legs = 0;
        waterRetreats++;
      } else {
        const aheadCell = cellKey(from.x + Math.cos(heading) * hop, from.z + Math.sin(heading) * hop);
        const aheadVisits = visited.get(aheadCell) || 0;
        // re-aim toward the frontier if committed long enough or the way ahead is
        // well-trodden (or she's in open ocean with no known land to retreat to).
        if (water || legs >= MAX_LEGS || aheadVisits >= 2) { heading = frontierHeading(visited, from, hop); legs = 0; }
        else { legs++; }
      }
      // human touch: nobody walks a compass line — each leg wobbles a few degrees
      const walked = heading + (rng() * 2 - 1) * 0.22;
      const dest = {
        x: Math.round(from.x + Math.cos(walked) * hop),
        z: Math.round(from.z + Math.sin(walked) * hop),
      };
      return { heading, hop, dest, verdict };
    },
  };
}

module.exports = { evaluateSurroundings, frontierHeading, cellKey, createExplorer, DIRS8 };
