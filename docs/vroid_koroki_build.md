# Building Koroki as a 3D VRM in VRoid Studio

**Why VRoid:** Live2D can't walk or free-pose (it's a deformer-based bust). AI image→3D
(Hunyuan3D, self-hosted) only produces rough un-riggable sculpts for a design this complex
(see `assets/koroki_3d_ref/koroki_hy3d_v1.glb` / `_v2.glb` — kept as 3D shape reference).
VRoid Studio (free) exports a **clean, already-rigged, walkable VRM** natively — no Mixamo,
no Blender, no retopo. That VRM drops into the `three-vrm` renderer (built separately) and
composites over the existing 2.5D rooms. Her body's locomotion stays procedural (glide/lean/
bob already in `world.js`); the VRM gives real limbs, free poses, blendshape emotion + lip-sync.

## Get the tool
VRoid Studio — free: https://vroid.com/en/studio (Windows desktop or Steam).

## Reference pack (already in repo)
| File | Use |
|------|-----|
| `assets/koroki_3d_ref/clean_2.png`, `clean_3.png` | Clean A-pose full-body refs (best overall) |
| `assets/koroki_3d_ref/front_2.png`, `back_1.png` | Front + back design reference |
| `assets/live2d/苹果小狐狸/a41b2cb8b0fee8e6320ea95b6731ba43.png` | Canonical face/portrait reference |
| `assets/koroki_3d_ref/koroki_hy3d_v2.glb` | Rough 3D sculpt — open in any GLB viewer for silhouette/volume reference |

## Koroki canonical palette (curated — match these in VRoid)
| Part | Hex | Notes |
|------|-----|-------|
| Hair (main) | `#ECEAF1` | white / silver-white, faint cool tint |
| Hair (shadow) | `#C9C4D6` | |
| Eyes (iris) | `#C81E3A` | crimson red — her signature |
| Skin | `#F6E7E0` | pale |
| Dress crimson | `#8E1F2E` | deep crimson-red primary |
| Dress black | `#1C1820` | near-black, cool tint |
| Fur trim | `#F0ECF2` | off-white |
| Accent (hairpin/neon) | `#FF3D72` | the "Crimson Midnight" rose — broken-heart hairpin + bows |
| Thighhighs / boots | `#17141B` | black |

## Build steps (Koroki-specific)
1. **New model → female base.** Slim build.
2. **Face/eyes:** anime round-ish face; set iris to crimson `#C81E3A`; calm/neutral default.
3. **Hair:** long straight back hair + bangs + side locks; main color white `#ECEAF1`. (Hair
   editor → procedural guides; keep it long, past the waist, matching the refs.)
4. **Fox ears:** VRoid v1.27+ → **built-in animal-ear accessory** (recolor white), OR sculpt
   from the hair editor, OR import a free BOOTH fox-ear preset (Edit Hairstyle → import
   `.vroidcustomitem` into "Side" hair). White `#ECEAF1`.
5. **Outfit (gothic):** use the outfit editor (top/skirt/dress) + **texture paint** to build the
   crimson `#8E1F2E` + black `#1C1820` corset dress with white fur trim `#F0ECF2`; add black
   thighhighs + boots `#17141B`. The clean_2 ref shows the silhouette.
6. **Fox tail:** built-in **Fox Tail** accessory (v1.27+) or free BOOTH fox-tail preset; recolor
   white. (Tails float on spring bones by default — fine for us; secure-attach needs Unity, skip.)
7. **Accessories:** broken-heart hairpin + red bow in accent `#FF3D72` (accessory slot or painted).
8. **Spring bones:** VRoid auto-adds them to hair/tail/skirt — leave on (gives secondary motion).

## Export (settings that matter for our renderer)
- Menu → **Export → VRM**.
- Keep **MToon toon shading** (anime look).
- Keep the standard **expressions/blendshapes** VRoid generates: `happy / angry / sorrow / fun /
  surprised`, `blink`, and the `A I U E O` **visemes** — these map directly to our emotion engine
  + TTS lip-sync. Do not strip them.
- Polygon/material reduction: default/light is fine (single avatar, cheap on the 12GB GPU).
- VRM 0.x is safest for `three-vrm` compatibility (VRM 1.0 also supported); if unsure, pick 0.x.

## Hand-off
Save the exported file to **`assets/vrm/koroki.vrm`** and tell me. I'll load it in the `three-vrm`
renderer, fit it into the rooms, and wire: walk/idle/pose animations (`.vrma` / Mixamo), blendshape
**emotion** (from the orchestrator emotion engine) and **lip-sync** (from IndexTTS), reusing the
procedural locomotion already in `world.js`.
