'use strict';

// Layer 5 — Building (Phase 5, 2026-07-13). Owner's signature doctrine:
// "mathematically efficient structure, decorated interior." So the PLAN is computed
// (a clean rectangular shell sized to a block budget, a proper door, a capped roof)
// and the interior gets a decoration pass (torch, a station/bed spot). This module
// holds the PURE geometry (testable); skills.buildHut executes it with mineflayer.
//
// The 8B quality-judgment ("is this a good spot / good build") is pod-deferred; for
// now the shell is deterministic.

// Plan a small hut centered on `center` (her feet block, integer coords).
// Returns placement lists: walls (shell, minus the doorway), roof, door (the gap),
// decor ([{pos, kind}]). w/d are odd so there's a true center; h = wall height.
function planHut(center, w = 3, d = 3, h = 2) {
  const c = center;
  const hw = Math.floor(w / 2);
  const hd = Math.floor(d / 2);
  const walls = [];
  const roof = [];
  const door = [];
  const decor = [];

  // shell walls: the perimeter columns, wall-height tall
  for (let dx = -hw; dx <= hw; dx++) {
    for (let dz = -hd; dz <= hd; dz++) {
      const onPerimeter = (dx === -hw || dx === hw || dz === -hd || dz === hd);
      if (!onPerimeter) continue;
      for (let dy = 0; dy < h; dy++) walls.push({ x: c.x + dx, y: c.y + dy, z: c.z + dz });
    }
  }
  // roof: a full lid one block above the walls
  for (let dx = -hw; dx <= hw; dx++) {
    for (let dz = -hd; dz <= hd; dz++) roof.push({ x: c.x + dx, y: c.y + h, z: c.z + dz });
  }
  // doorway: a 1x2 gap on the -z face, centered
  door.push({ x: c.x, y: c.y, z: c.z - hd });
  door.push({ x: c.x, y: c.y + 1, z: c.z - hd });

  // decorated interior: a torch at center + a station/bed corner
  decor.push({ pos: { x: c.x, y: c.y, z: c.z }, kind: 'torch' });
  decor.push({ pos: { x: c.x + hw - 1 || c.x, y: c.y, z: c.z + hd - 1 || c.z }, kind: 'station' });

  // remove the doorway cells from the wall list
  const doorKeys = new Set(door.map((p) => `${p.x},${p.y},${p.z}`));
  const wallsNoDoor = walls.filter((p) => !doorKeys.has(`${p.x},${p.y},${p.z}`));

  return { walls: wallsNoDoor, roof, door, decor, size: { w, d, h } };
}

// Total solid blocks a plan needs (walls + roof) — for the "do I have enough?" check.
function blockBudget(plan) { return plan.walls.length + plan.roof.length; }

module.exports = { planHut, blockBudget };
