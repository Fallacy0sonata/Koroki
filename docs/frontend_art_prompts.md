# Koroki World — AI-Gen Art Prompt Pack

Prompts + method to generate the neon-night room layers and the intro key-art, all in one
consistent anime style tuned to her ("Crimson Midnight" palette). Zero-budget → local/free
image-gen (SDXL / Flux). Drop the results into the existing parallax slots; the effect engine
doesn't change.

> **Key simplification:** rooms have **NO character in them** — she's the live Live2D model
> composited on top, plus the intro key-art. So room backgrounds are pure *environment* art
> (easy to keep consistent). Character likeness only matters for the **intro key-art**.

---

## 0. The constants (use on EVERY generation)

**Palette (Crimson Midnight):** hero crimson-rose `#ff3d72`, complementary electric teal `#32d0e6`,
supporting deep violet `#8f46f0`, base violet-black `#070611`.

**Style suffix** (append to every prompt):
```
anime key visual, painterly digital illustration, neon-night aesthetic, cinematic lighting,
strong rim light, soft bloom, volumetric haze, crimson-rose (#ff3d72) and electric teal
(#32d0e6) neon accents over a deep violet-black base, moody, atmospheric, depth of field,
high detail, 4k
```

**Negative prompt** (rooms — note "no people"):
```
people, person, character, girl, boy, text, watermark, signature, logo, ui, lowres, blurry,
jpeg artifacts, flat lighting, daytime, sunny, oversaturated, washed out, deformed, extra limbs
```

**Consistency rules (so all rooms feel like ONE place):**
- Reuse the **same style suffix + palette hexes** verbatim every time.
- Lock a **style reference**: either a fixed seed, a style LoRA, or one IP-Adapter reference image
  carried across all rooms. Generate all rooms in one session.
- Same **camera height / perspective** for every room (eye-level, straight-on, slight wide lens).
- **Leave the center of frame open** — she stands there. Push furniture to the sides / lower third.

---

## 1. Tooling + layer-separation method

We need each room as **depth layers** (transparent PNGs) to drop into the parallax slots.

**Approach A — transparent generation (recommended).** Use **LayerDiffuse** (SDXL transparent-image
extension) or Flux transparency workflows to generate each element as its own transparent PNG
(window, furniture, foreground props), plus one opaque back-wall layer. Cleanest result.

**Approach B — full scene + cut (no transparency tool).** Generate the full room once (composition
ref), then:
- Back/wall + window: generate full-frame.
- Furniture: regenerate "on solid black background," then **screen/add blend** in-engine (black → transparent), or key it out.
- Foreground: same, props on black.
Inpaint anything occluded so layers are complete behind each other.

**Output size:** 16:9, generate ≥ 2048×1152 (higher is better). Make each layer **~20% wider** than
the screen (e.g. 2560×1152 content in a wider canvas) so the parallax has travel room.

---

## 2. Room layers

Each room = 4 layers: **far** (window/city) · **wall** (back wall + signage) · **furniture** (mid) ·
**near** (foreground). Center stays open for her.

### 2.1 Studio (her stream room) — build this FIRST (she's mostly at the desk)

**far / window+city:**
```
view through a large window into a nighttime cyberpunk cityscape, distant glowing skyscrapers,
teal and crimson-rose neon signs in the far city, light rain on the glass, soft bokeh, deep
violet-black sky, [style suffix]
```
**wall / back wall + signage:**
```
dark studio back wall, a glowing crimson-rose neon sign, framed posters in shadow, floating
shelves with small figures, thin teal LED strip lighting along the edges, fairy lights, center
of wall left empty, [style suffix]
```
**furniture / desk (lower-third, sides):**
```
a streamer's desk seen straight on, dual widescreen monitors glowing teal, RGB mechanical
keyboard, a condenser microphone on a boom arm, a gaming chair silhouette, tangled cables, crimson
and teal glow spilling onto the desk, occupying the lower third and right side of frame, center
open, [style suffix]
```
**near / foreground props (deep blur):**
```
out-of-focus foreground, dark silhouettes of a potted plant's leaves and a desk corner framing the
bottom corners, heavy bokeh, near-black, [style suffix]
```

### 2.2 Bedroom (her intimate space — lean warmer, more rose)

**far / window:** `large window at night, calmer city glow, crimson-rose dominant with soft teal,
sheer curtains catching neon, rain on glass, [style suffix]`
**wall:** `cozy dark bedroom wall, string fairy lights, a crimson neon heart sign glowing soft,
posters and plushies in shadow, soft violet ambient, center empty, [style suffix]`
**furniture:** `a low bed with crumpled blankets and pillows, plush toys, a small nightstand with a
glowing lamp, occupying the lower-left, crimson-rose rim light, center open, [style suffix]`
**near:** `out-of-focus foreground, blurred bed canopy fabric and fairy-light bokeh framing the
edges, near-black, [style suffix]`

### 2.3 Lounge (cooler, more teal — the "public/window" room)

**far / window:** `floor-to-ceiling window, wide neon city panorama at night, teal dominant with
crimson accents, reflections, light rain, [style suffix]`
**wall:** `minimal lounge wall, a teal neon line accent, a large abstract artwork in shadow, shelf
with vinyl records, center empty, [style suffix]`
**furniture:** `a low modern couch with cushions, a small coffee table, a tall floor lamp glowing,
occupying the lower third, teal and crimson glow, center open, [style suffix]`
**near:** `out-of-focus foreground, blurred large monstera plant leaves framing the bottom-left,
near-black silhouette, heavy bokeh, [style suffix]`

---

## 3. Intro key-art (HER — likeness matters)

This is the static illustration behind the parting doors in the cold-open. **Use her actual design
for likeness** — reference her Live2D textures via **IP-Adapter / reference-only**, or a trained
character **LoRA**. Text-only won't reproduce her reliably.

**Character anchor (her):**
```
Koroki: anime girl, long flowing white hair, white fox ears and a large fluffy fox tail, crimson
red eyes, a crimson-and-black gothic lolita dress with white fur trim, pearl accessories, pink
ribbons, a heart-shaped hairpin
```
**Intro composition:**
```
[character anchor], standing in a neon-night room, three-quarter view looking at the viewer,
dramatic rim lighting from crimson-rose and teal neon, soft bloom, volumetric haze, cinematic
anime key visual, full body, she is off-center (left third), the right side open for the title,
[style suffix without "no people" negative]
```
**Negative (key-art):** `text, watermark, logo, lowres, blurry, extra limbs, deformed hands, bad
anatomy, daytime`

Generate a few; pick the one most on-model. (Optionally a second, tighter portrait crop for a
title-screen close-up.)

---

## 4. Drop-in convention (so I can wire it incrementally)

Save as transparent PNGs here (⚠ repo-root `assets/`, NOT `clients/web/assets/` — the server
mounts `/assets` → repo-root `assets/`, same place the Live2D model lives):
```
assets/world/
  studio_far.png   studio_wall.png   studio_furniture.png   studio_near.png
  bedroom_far.png  bedroom_wall.png  bedroom_furniture.png  bedroom_near.png
  lounge_far.png   lounge_wall.png   lounge_furniture.png   lounge_near.png
  intro_koroki.png
```
When you've generated even one or two, tell me — I'll add a loader to `world.js` that **uses the PNG
if present, else falls back to the current placeholder geometry**, so we swap art in **incrementally**
(no big-bang). Start with `studio_*` + `intro_koroki.png` for the biggest immediate payoff.

---

## 5. If you don't have a local gen setup handy

Free options that run on your rig or free tiers: a local **SDXL/Flux** (ComfyUI/Automatic1111) with
**LayerDiffuse** for transparency; or free web gens for the non-transparent approach (then cut layers).
Whatever you use, keep the **style suffix + palette hexes + one style reference** constant across all
of it — consistency comes from reusing those, not from any single tool.
