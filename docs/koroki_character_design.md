# Koroki — Canonical Character Design (the "bible")

Source of truth: `assets/Koroki pictures/` (5 official detail sheets provided by the owner
2026-06-30). **All Koroki generation — LoRA, pose art, expressions — must match THIS.** If a
generation disagrees with this doc, the generation is wrong.

> ⚠ Corrections from earlier (wrong) assumptions: her hair is **ASH-GREY/greige, NOT white**; her
> accent is **deep WINE-MAGENTA** — it *does* read magenta (a rich pinkish-red), NOT pure fire-red,
> but also NOT the world's hot **neon** magenta `#ff3d72`. Aim for a deep, saturated wine-magenta.
> The white-haired `苹果小狐狸` Live2D model is now just the demoted **mascot/pfp** and is slightly
> off-model; do not use it as the design reference.

## Identity
Gothic-lolita fox/wolf girl, idol-ish, elegant + a little aloof. **Tall, statuesque, confident.** Her
charm is a *duality*: playful animal features + youthful ornaments (heart clip, cherry danglers,
little flowers) on a **tall, mature, sophisticated hourglass frame**, with a **knowing, smug,
cat-like** half-lidded gaze. Confident and a touch dangerous, never childish.

## Body type & proportions  (⚠ the #1 thing we kept getting wrong)
- **TALL and statuesque — NOT petite, NOT small.** `Koroki1.png` is in a **kneeling/sitting** pose,
  which makes her look short; do **not** infer her height from it. She is a fully-developed, long-limbed
  young woman (early 20s).
- **Full hourglass:** long elegant shoulders/collarbones → **narrow cinched waist** → **full, pronounced
  bust** (full, *not* gigantic/ballooned) → full curvaceous thighs, long sculpted legs.
- **Open sweetheart, low-cut neckline** with cleavage — part of the canonical design, not optional.
- The two failure poles to avoid: (a) **tall + slim + flat + modest** (under-shot — too plain), and
  (b) the **bombshell**: gigantic bust, thick/wide hips, and especially a **sharpened mature/adult face**.
  Her *face structure stays delicate*; only the *expression* is confident. Curvy body, delicate face.

## Hair
- **Color: ash-grey / warm taupe-grey (greige)** — desaturated brown-grey, mid value. NOT white, NOT silver, NOT black.
- Very long (past hips), thick, voluminous, softly wavy.
- **High side ponytail on the right**, tied with a large **black satin bow**. Bangs + face-framing side locks.

## Ears
- **Fox/wolf ears, grey fur matching hair**, with paler/white inner fur. Large, fluffy.

## Eyes / face
- **Crimson-red eyes**, large, **half-lidded / droopy (tareme)**, with floral/star light reflections;
  thick dark lashes + **reddish eyeshadow on the lower lids**.
- **Delicate clean porcelain** structure: dainty chin, soft clean lines, small nose, **small smug
  cat-like "w" pout**. Soft blush. Expression is **knowing, confident, mischievous** — *not* wide-eyed
  innocent and *not* a sharpened adult face. Confident vibe on delicate features.

## Head accessories (left bangs → right ponytail)
- **Broken-heart red hairpin** (red heart with a crack) on the left bangs — her signature.
- **Two white "X" cross hairpins** just below the broken heart.
- **Red oval gem** + cluster of **small white 5-petal flowers** near the right ear / ponytail base.
- Large **black bow** on the ponytail. A few loose small white flowers float near her.

## Outfit (gothic lolita, wine + black + white fur)
- **Bodice:** wine/crimson-red corset, black **cross-laced** center, structured.
- **Neck/chest:** black choker area with a small **black ribbon bow** + white flower + **pearl strands**; a large **wine-red ribbon bow** at the chest.
- **Shoulders:** off-shoulder; **fluffy white fur puffs** wrap the upper arms (detached fur shrug look).
- **Arms:** long **black gloves**, small red-bow accents.
- **Skirt:** layered — wine-red under a **black ruffled** layer with **black rose-lace** hem, **white petticoat** ruffles beneath; **white pearl strands** draped across bodice + skirt.

## Legs / footwear  (⚠ thigh strap is important)
- **Wine-red garter strap** high on the **right thigh** — do not omit.
- Bare upper thigh → **black** stockings/boots below.
- **Black heeled boots** with **red round bead/pom danglers** + small black bow accents.

## Tail  (⚠ don't confuse with hair — both are grey)
- Large fluffy **grey fox tail**, same greige as the hair (reads as a separate tail, not a hair lock).

## Palette (approx hex — for LoRA captions & prompts)
| Part | Hex |
|------|-----|
| Hair / ears / tail (greige) | `#857A72` (range `#6f655d`–`#9a8f86`) |
| Eyes (crimson) | `#C81E3A` |
| Dress wine-red | `#8E1F35` |
| Black (skirt/gloves/bow) | `#1A1518` |
| White (fur / pearls / petticoat) | `#F2EEF0` |
| Thigh strap / shoe beads | `#9E1F33` |
| Skin | `#F6E5DD` |

## Prompt skeleton (Illustrious/anime)
`1girl, solo, Koroki, ash grey hair, long wavy hair, high side ponytail, large black bow, grey fox
ears, fluffy grey fox tail, crimson red eyes, half-lidded, broken heart hairpin, white x hairpins,
wine red and black gothic lolita dress, white fur shoulder puffs, black corset lacing, wine red ribbon
bow, pearl strands, black gloves, black ruffled skirt with black lace, white petticoat, red thigh
garter strap, black heeled boots, pale skin`

## Negative (avoid the wrong-Koroki failure modes)
`white hair, silver hair, blonde, black hair, blue eyes, magenta, hot pink, neon pink, missing fox
ears, missing tail, no thigh strap, modern clothes, plain dress`

## Art style — "the pen"  (must match her ROOMS, not generic AI anime)
Her on-screen art must look **drawn by the same hand as her rooms** (`assets/world/*_back.png`) so she
doesn't look pasted in. The rooms read as **indie visual-novel / otome-game CG**: painterly digital
painting, **visible brushstrokes**, semi-realistic painted rendering, soft cinematic lighting + bloom,
crimson/magenta ambiance. **NOT** flat-cel, glossy, plastic, or generic-AI-anime.
- The rooms were made with **Illustrious-XL-v1.0**, cfg 5.0, dpmpp_2m/karras, painterly style tokens,
  **no IP-Adapter** (verified from their PNG metadata).
- Why our early Koroki looked "generic glossy AI": the **IP-Adapter** (reference = her clean-anime art)
  drags that glossy cel rendering onto every gen, overpowering painterly tokens. Defeat it by weakening
  the IP's *style* grip while keeping identity.

## Generation recipe (locked 2026-06-30) — for all Koroki sprite/pose art
- Model `Illustrious-XL-v1.0`, **cfg 5.0**, steps 30, dpmpp_2m + karras, 832×1216.
- IP-Adapter `ip-adapter-plus_sdxl_vit-h` on `koroki_ref_full.png`, **weight_type `ease out`**
  (carries her *color* early, fades for painterly late steps), weight ~0.50, end_at ~0.65,
  embeds_scaling "V only". (`composition` also keeps identity but loses the magenta → brown.)
- **Heavy painterly tokens** (indie VN CG, visible brushstrokes, painterly digital painting) to set the pen.
- **Flat solid background** (`plain flat dark grey background, no scenery`) so **rembg isnet-anime** cuts
  clean sprites. Atmospheric/cloud backgrounds break the cutout (leaves a rectangle). Cut, then composite
  onto the room; `world.js` applies the per-room cosy grade at runtime.

### Gremlins (real bugs hit, with fixes)
- **Magenta bodice → brown/black.** Caused by over-negating magenta hues + "corset"→leather/black default.
  Fix: push `(rich deep magenta corset bodice:1.45)`, and negate **brown/leather/black corset** instead of
  negating magenta. Still inconsistent (~1/3 land) — curate for it; the 5 real refs anchor it in the LoRA.
- **Literal hourglass prop.** The word "hourglass figure" makes it draw an actual hourglass object. Imply
  the shape via "narrow cinched waist + full bust + wide hips"; negative "hourglass, sand timer, prop".
- **Pink fur.** "rose-magenta glow" tints the white fur boa pink. Lock "snow white fur"; negative "pink fur".
- **Petite/short drift.** Lead with "tall statuesque, long legs"; negative "petite, short, chibi".

## Common mistakes (seen in our own past gens)
- White/silver hair (she's greige). · Magenta/hot-pink accent (she's wine-crimson). · Forgetting the
  grey **tail** (it blends into the grey hair — keep it distinct). · Dropping the **thigh garter strap**.
  · Over-poofy or wrong skirt. · Floating wing/emblem/shadow clutter behind her (negative-prompt it).
