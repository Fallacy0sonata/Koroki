# Layered Room Scene — Spec (bedroom first, template for all rooms)

Owner design law (2026-07-02): **many layers, tiny desynced micro-motions.** The aliveness
comes from lots of small independent movements, never one big block moving in unison.
Prefer MORE layers with SMALLER motion; when in doubt, split the layer.
(memory: `frontend-scene-motion-philosophy`)

## Layer stack (bedroom night) — back to front

| z | layer | source | motion |
|---|-------|--------|--------|
| 0.0 | sky base (skyline + city glow) | `sky_*.png` plate, scaled > window for parallax margin | ultra-slow x drift loop (~0.5px/s), tiny alpha breathe |
| 0.1 | cloud plate | `clouds_*.png`, SCREEN blend | slow wrap drift (~2px/s), different direction/period than sky |
| 0.2 | city light flickers | procedural: tiny ADD-blend dots over skyline band | per-dot random flicker (period 2–9s, random phase) |
| 0.3 | weather FX (behind glass) | procedural rain/snow, weather-gated from `/v1/worldstate` | falling, wind-angled |
| 1.0 | room shell | chosen `shell_*.png` with window panes hand-cut to alpha (pane polygons like NECK_POLY) | none (the anchor — everything else moves around it) |
| 1.5 | curtains | `furniture/cut/curtains.png` at window edges | gentle skew sway, ~7s period |
| 2.x | back furniture (bed, nightstand, rug, bookshelf, plants…) | `furniture/cut/*.png`, one layer EACH | plants: leaf tremble (±0.3° around base pivot, 3–5s); others static |
| 2.8 | lamp glow(s) | procedural soft radial sprite, ADD blend | alpha breathe 3–8%, ~6s, one clock per lamp |
| 3.0 | Koroki | pose sprite (sit/stand sets, more poses via THE POSE PIPELINE) | breath scale + bob (existing), tiny rotation osc ±0.15° |
| 4.x | front occluders (foreground furniture, near plant) | `furniture/cut/*.png` | same micro-rules as 2.x |
| 5.0 | in-room FX: dust motes in lamp/window light | procedural, few, ADD, low alpha | slow individual float paths |
| 9.x | screen-space grade: vignette + grain (+ worldstate color grade) | port from world.js | grain shimmer; grade lerps with time-of-day |

## Motion system rules

- Every motion = `{type, amp, period, phase}`; **phase randomized per instance at load,
  period jittered ±15%** so no two layers ever beat in sync.
- Types: `swayX/swayY` (position sin), `rock` (rotation sin), `pulse` (alpha sin),
  `drift` (constant velocity + wrap), `flicker` (random-walk alpha), `tremble`
  (high-freq low-amp rotation).
- Amplitudes tiny: 0.5–3px position, 0.1–0.5° rotation, 1–4% alpha.
- ALL dt-corrected (LEGACY 2026-07-02: no fixed per-frame factors).
- Parallax on pointer stays, but per-layer factors differ slightly so even parallax
  is not uniform.

## Teleport spots (pose-per-spot)

`spots` config: `{ name, x, y, scale, pose, flip?, behind?: [layerIds] }`
- `pose` selects the sprite set (`koroki_` sit, `stand_`, future: `lie_`, `desk_`…)
- `behind` lists front-occluder layers that render OVER her at that spot
  (e.g. at `bed` spot the duvet-edge occluder covers her legs → "she's IN the bed").
- Teleport = fade/sparkle transition (no walking — settled decision).
- Worldstate `deriveStation` logic (from world.js) maps her actual state → spot.

## Room config schema (JSON per room — engine is 100% data-driven)

```json
{
  "id": "bedroom",
  "layers": [
    { "id": "sky", "src": "sky_42001.png", "z": 0, "pos": [0.62, 0.38], "scale": 1.3,
      "motions": [{ "type": "drift", "vx": -0.5 }, { "type": "pulse", "amp": 0.02, "period": 11 }] },
    { "id": "bed", "src": "furniture/cut/bed.png", "z": 2.1, "pos": [0.30, 0.78], "scale": 0.9 }
  ],
  "spots": [
    { "name": "bed", "pos": [0.32, 0.74], "scale": 0.9, "pose": "sit", "behind": ["duvet_edge"] },
    { "name": "window", "pos": [0.78, 0.70], "scale": 1.0, "pose": "stand" }
  ]
}
```

## Navigation (10+ rooms planned)

Direct-jump only: a room dock/mini-map — click any room, plus a "go to her" shortcut.
NO linear arrow-walking between rooms (owner: user must never click through room 1→10).

## Build order

1. ✅ Art plates: empty shell + sky base + cloud plates (`koroki_bedroom_scene_art.py`)
2. Window-pane alpha cut on the chosen shell (hand polygons, one-time)
3. `clients/web/scene.js`: data-driven layer engine + motion system + spots
4. Bedroom config JSON + furniture placement pass (user eyes on composition)
5. Worldstate binding (grade, weather, station→spot) — port the good parts of world.js
6. Template the whole thing for room #2 onward
