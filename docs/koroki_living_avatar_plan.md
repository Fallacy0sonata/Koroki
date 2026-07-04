# Koroki Living Avatar — the code-driven 2D layered puppet ("DIY Live2D")

**Status:** ACTIVE design, 2026-06-30. This is the on-screen embodiment plan for the "window into
her world" frontend, after Live2D and 3D were both ruled out (see "Why" + LEGACY 2026-06-30).
Character look is locked in `docs/koroki_character_design.md` (ash-grey hair, wine-crimson, gothic
fox girl — NOT white/magenta). This doc is the architecture; keep it current.

---

## The vision (the part summaries always lose)
Koroki is *alive in her rooms*: at any moment she's in a **different place doing a different thing**
(sleeping, scrolling her phone in bed, reading, gaming at the desk, lounging), **wearing a different
outfit** (home casual, nightgown at night, the gothic dress when she "goes out"), with a **rich,
changing face** (all basic emotions + funny ones: money-eyes, drooling, sleepy, heart-eyes…), plus
effect overlays (hearts, sweatdrops, anger-veins, zzz). Her **"mind" (the existing subsystems) drives
all of it** — she picks her pose/activity, her emotion engine drives her face, TTS drives her mouth.
She **teleports** between poses with a transition effect (no walking needed — that was the whole point
of dropping 3D). Target density: **~7–10 poses per room × 3 rooms**, many outfits, ~16 expressions.

This is **PNGtuber's face-swapping × Live2D's joint motion, built in PixiJS code** — "our own Live2D."
The owner directs (poses, outfits, taste); Claude generates the art + builds the engine.

## Why (both other paths were exhausted — see LEGACY 2026-06-30)
- **Live2D**: deformer-based bust; can't walk or free-pose; the borrowed `苹果小狐狸` `.moc3` can't be
  re-rigged (no editable source). → demoted to **mascot / pfp / Discord avatar** (and it's white-haired
  = off-model anyway).
- **3D VRM**: VRoid build + Unity/UniVRM conversion of a free model = the owner has **no 3D/modeling
  skill** and welding accessories/rigging was a wall; Hunyuan3D AI image→3D gave only rough un-riggable
  sculpts. Abandoned.
- **This (2D code puppet)**: the owner's own idea, and it's the best fit — preserves the 2D anime look
  they prefer, needs **zero rigging tools and zero 3D**, and is *more* on-philosophy (her depicted
  state = her actual mind).

---

## The ONE rule that makes "layers hell" winnable
**Composite layers at runtime; never pre-render combinations.** Generate each layer/look **once**; the
PixiJS engine stacks them live. Multiplication → addition:
`25 poses × 8 outfits × 15 expressions = 3000 baked images ❌` → generate per-pose layers once, engine
assembles any combo ✓. Her wardrobe + emotions are **data the engine stacks**, not pre-baked pictures.

## The honest constraint on modular clothing (and the free win it gives)
AI **cannot** cleanly composite arbitrary mix-any-piece clothing (separately-generated garments seam/
occlude wrong). So the wardrobe model is:
- **Complete outfit "looks"** (home-casual, nightgown, gothic dress…) = swap the whole clothed body.
  Each look is *designed*, so **the randomizer can never make an ugly mix** — it just picks a valid look.
  (This solves the owner's "not true random or there'll be ugly combos" worry for free.)
- **Over-layer items that sit on top** (open cardigan/jacket, glasses, headphones, hairclips, **held
  items**: phone/book/controller) = real toggleable overlays. This is where "accessories" modularity
  lives; held items usually define the activity.
- **Face/expression + effect overlays** = fully modular (cheap — see below).
- **Pose-lock with ControlNet** when generating a pose's outfits + expressions, so heads/bodies align →
  **one expression set works across every outfit in that pose**.

## The per-pose layer stack
```
back hair + tail  →  body+outfit (swappable "look")  →  front hair  →
FACE (expression / blink / mouth-viseme)  →  over-accessories (jacket / glasses / held item)  →
effect overlay (hearts / sweat / anger-vein / zzz / sparkles)
```
- **Expressions** = inpaint the face region of the pose-locked base → pixel-aligned face swaps.
- **Effect overlays** = generic screen-space sprites, generated **once**, reused on **every** pose →
  15 emotions don't cost 15×poses; only the face layers are per-pose, the effects are ~free.

---

## Systems to build (PixiJS engine, in `clients/web/`)
1. **Pose/activity system** — her subsystem activity → select pose (reuse `/v1/worldstate`; extend the
   existing `deriveStation` station logic into richer per-room activities). **Teleport transition**
   (dissolve / glow / glitch) on change.
2. **Wardrobe system** — a config of valid **looks** + toggleable **over-accessories**, with a
   **constrained randomizer** (rules: which looks fit which time/activity — e.g. nightgown at night,
   gothic dress for "going out"; no invalid combos). Outfit can be owner-set or auto-picked by context.
3. **Expression system** — emotion engine → face-layer swap; **blink** (timed) + **mouth visemes**
   (lip-sync from IndexTTS audio). Funny states (money-eyes/drool/etc.) are just more face layers.
4. **Effect overlay system** — generic emotion FX sprites, triggered by emotion engine.
5. **Idle animation** — subtle per-pose life: breathing (whole-image sway), small joint motion later
   (Phase 3), blink. (The "your 50px vs actual" pivot-calibration is Phase 3 polish, deliberately last.)
6. **Lip-sync** — drive mouth visemes from TTS playback (amplitude → mouth-open, or phoneme→viseme).

## Consistency — the Koroki LoRA (cold-start)
Only 5 owner refs exist (`assets/Koroki pictures/`), all similar pose → too few/narrow to train on
directly. **Bootstrap:** IP-Adapter-generate a varied on-model batch (angles/poses/expressions) from the
refs → owner+Claude curate the on-model ones → train the **Koroki LoRA** on that expanded set → all
production generation uses the LoRA (locks her to the canonical design across every pose/outfit/face).
Generation stack: **ComfyUI + Illustrious-XL + Koroki LoRA + ControlNet (pose-lock) + inpaint
(expressions/outfit regions) + rembg `isnet-anime` (clean cutouts)**, all self-hosted/free.

---

## Build order (do NOT boil the ocean)
- **Phase 0 — LoRA bootstrap:** varied gen → curate → train Koroki LoRA. (Validate likeness first.)
- **Phase 1 — Vertical slice (the proof):** Bedroom · **2 poses** (sleeping, phone-in-bed) · **2
  outfits** (casual + nightgown) · **full expression set + effects** · the PixiJS compositing engine
  (pose-select, outfit-swap, expression+emotion, blink, lip-sync, teleport, breathing idle). If this is
  alive + on-model, the architecture is proven.
- **Phase 2 — Scale content:** fill bedroom to 7–10 poses; add studio + lounge poses; more outfit looks
  + accessories; wire her "mind" to drive pose+outfit+emotion autonomously.
- **Phase 3 — Joint articulation:** cut a few joints per pose (head/arm/legs), calibrate pivots, richer
  idle motion. The fiddly boundary-calibration — last, because everything else already works by then.

## Starter content lists (owner to confirm/edit — as of 2026-06-30)
- **Bedroom poses (target 7–10):** sleeping · phone-in-bed · reading · sitting hugging knees · desk/PC
  gaming · stretching/just-woke · lounging on headboard. *(Slice = first 2.)*
- **Expressions (~16):** neutral · soft-smile · big-grin · blush/shy · pout · annoyed/angry · sad ·
  crying · surprised · sleepy · smug · money-eyes · drooling · dizzy-spiral · heart-eyes · dead-inside
  (+ blink + mouth shapes).
- **Effect overlays:** sweatdrop · anger-vein · floating hearts · sparkles · zzz · exclamation/question ·
  tears · sleepy-bubble.
- **Outfits (greige/wine/black/white scheme):** home-casual (oversized knit + shorts) · nightgown ·
  signature gothic dress · later: hoodie · cardigan · seasonal. Toggle accessories: glasses · headphones
  · hairclips · held phone/book/controller.

## Integrates with (don't rebuild these)
- `/v1/worldstate` + `deriveStation` (her time/presence/activity) — extend, don't replace.
- Emotion engine (`services/orchestrator/emotions/`, `body/endocrine.py`) → expression + effect FX.
- IndexTTS (:9000) playback → lip-sync visemes.
- PixiJS rooms in `clients/web/world.js` (rooms/atmosphere/grade stay; the **avatar layer** is the new
  build, replacing the Live2D model in the world view).

## Effort reality (said with love)
This is the **longest road** we've chosen — a content marathon (hundreds of generated layers). But it's
**automatable** (LoRA + ComfyUI batch + inpaint + rembg) and **chip-at-able** (vertical slice → scale),
and it's the only path that ends with *her*, alive, in the look the owner actually wants.
