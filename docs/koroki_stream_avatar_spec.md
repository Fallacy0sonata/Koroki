# Stream Avatar — "Her PC's Webcam" (Stage 5 spec, owner vision 2026-07-04)

## The camera concept
The website is *us viewing her life* (worldstate window). When she games/streams,
**the camera shifts to the angle of her own PC's webcam**: her at her desk, her room
behind her. The overlay is not a mascot — it's a diegetic camera inside her world.
- Background = her room **at the actual time of day her world is in** (morning light,
  evening lamps, night city glow — the worldstate already knows).
- She sits at the desk in art-style render; the room behind matches the established
  room art (bridging the open "Koroki-vs-room style match" issue).

## Art style direction (owner, 2026-07-04 — verbatim intent)
Current LoRA render quality = **7/10: "looks beautiful but doesn't feel relaxing,
soft, almost effortless — more generic AI art than hand-drawn feeling."**
Target (reference pics saved this session): **soft, muted/pastel flats, loose sketchy
lineart, minimal rendering, cozy, hand-drawn charm — effortless.** Her canonical
DESIGN is right (ash-grey hair, wine-magenta accent, tall, cat-pout face — see
docs/koroki_character_design.md); the PEN must soften. Blend, don't replace.

## The "artificial L2D" (code-driven layered puppet — extends the existing plan)
Layered PNG composition, PixiJS or overlay renderer, per the scene-motion philosophy
(MANY tiny desynced micro-motions, never one block):
1. **Base plate**: her seated-at-desk pose × room × time-of-day (start: 3-4 time
   variants of ONE pose; more poses later per activity).
2. **Face expression overlays**: swap by emotion-engine state (calm/happy/tired/
   teasing/focused…) — same head anchor across all plates.
3. **Mouth states**: 3-4 shapes (closed/mid/open/smile) driven by the TTS audio
   envelope at playback time (RMS → mouth openness). No phoneme lipsync needed —
   envelope flapping reads as speech at stream size.
4. **Micro-motion layers**: hair strands, chest breathing, blink cycle, steam from a
   mug, window light — each with random phase/period.
5. **Game-mode gaze**: eyes-down-at-screen variant (she's looking at the game, not
   the viewer) + occasional glance-at-camera as an EVENT (reactive charm).

## Build path
1. Style probe: prompt/LoRA-tag matrix on the existing Illustrious + koroki_lora_v2
   until the soft-effortless pen lands (owner picks from grids).
2. Generate the base plates + expression/mouth sheets in the locked style.
3. Cut layers (SAM-assisted where useful, precedent exists).
4. Overlay renderer (OBS browser source — a small local page reading worldstate +
   TTS playback events; PixiJS micro-motions).
5. NOT Fable-5 territory per owner: heavy frontend polish — but the art pipeline,
   layer cutting, and the OBS-source scaffold are in scope.

## LOCKED (2026-07-04 probe session, 4 rounds — owner-rated 7.0 → 8.2+)
- **THE PEN (round-3 recipe, exactly):** Illustrious-XL + koroki_lora_v2 @ **0.72**
  + sketch_chaotic_lineart @ **0.5**, **CFG 3.8**, euler_ancestral, tags:
  `flat color, pale color palette, pastel colors, white background, minimal
  shading, light blush, thin delicate lineart, sketch` + negatives
  `shiny skin, glossy, high contrast, hdr, intricate, ornate, detailed shading,
  3d, render`. Reference plate: `life_chin_rest_52001.png` (owner: right model).
  ⚠ DO NOT add framing tags like "close to camera, head and shoulders large" —
  round 4 proved they crush the body into moe/chibi drift.
- **THE FRAMING = COMPOSITED LAYERS, never one generation:** her plate (facing
  camera, desk-world sketch behind) + keyboard-foreground part at bottom edge
  + room background. Proof: `composite_mock.png` (owner: "yes something like
  that") — but parts must be REAL alpha cutouts (hard edges), never strip-paste
  with blend bands (owner rejected the seam).
- **Part-cutting lessons:** generate parts ALONE on white (flood-cut keeps
  everything non-background — a "keyboard on desk" smuggles the desk in); OR
  SAM-box-cut a well-drawn in-context part. Best keyboard source so far: the
  big pink one in `webcam_lock_53004.png` → SAM-cut it (koroki_sam_cutout.py,
  .venv_diffsinger).
- **Canon addition (owner-endorsed accident): a little red heart on her
  headphones/gear.** Appeared consistently across rounds; keep it.
- All probe assets: `data/art_previews/webcam_style_probe/` (r1-r4 sheets,
  singles, keyboard parts, composites). Script:
  `tools/art_pipeline/koroki_webcam_style_probe.py` (rounds via CLI arg).

## Production checklist (next art session)
1. SAM-cut the 53004 keyboard → `kbd_part.png` (the foreground layer).
2. Plate set in THE PEN, seed-fished from 52001's family: idle, chin-rest,
   typing (hands low near frame bottom), mug-sip, stretch — same head anchor.
3. Expression sheet (calm/happy/tired/teasing/focused) + 3-4 mouth states —
   face-inpaint on the winning plate (koroki_stand_faces.py precedent).
4. Time-of-day room backgrounds (reuse flux room art or the sketch pen).
5. Overlay scaffold: OBS browser source, layers + envelope mouth + blink/breath
   micro-motions, emotion-driven expression swap from worldstate.

## VRAM note
Art generation (ComfyUI + Illustrious) needs ~7 GB → she naps during art sessions
(brain+vision+voice paused), same as training windows. On the 3090 future: no napping.
