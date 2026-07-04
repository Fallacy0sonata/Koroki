# Koroki Frontend — "Window Into Her World" (Vision + Architecture Draft)

**Status:** drafting (2026-06-28). Decisions locked where marked 🔒; open forks at the bottom.
**Goal:** the frontend is not a chatbot page — it's a **cinematic window into a life**. Extravagant
first impression; she lives and wanders; the world is real (driven by `/v1/worldstate`).

---

## 1. North star

You don't land on a page — you **stumble into her room** like the cold-open of an anime episode.
The screen opens on a detail (light through the window, dust in the air, graded to her *real* time of
day + weather), the camera drifts across the space with parallax depth, and **finds her** — not posed
waiting, but living: at the window in the morning, curled in bed at 3am, at her stream setup mid-day,
pacing when she's restless. You're a quiet observer who wandered in. Then you can explore her space, and
talk to her.

**Why it fits the project:** strengthens the sentient-being framing (she has a place she lives, a life
off-"stream"), not a portrait of a bot. All client-side → **zero server GPU** (the 12GB stays for
Brain/TTS). Reuses her existing Live2D model.

## 2. Locked decisions 🔒

| Decision | Choice |
|---|---|
| Rendering | 🔒 **PixiJS 2.5D** — Live2D Koroki composited over depth-layered room art; WebGL camera + particles + color-grading filters. |
| Movement | 🔒 **Both** — she wanders autonomously (driven by her real state) AND the visitor can pan/explore to find her. |
| v1 scope | 🔒 **One hero room, fully cinematic** — nail the "wow" in a single space, then expand to multi-room. |
| Room aesthetic | 🔒 **Neon-accented night** — dark base, saturated neon accents (signage/RGB), city glow + rain on the window, magenta/cyan haze. Max first-impression; plays perfectly with bloom + rain particles + color grading. |
| Live2D poses | 🔒 **Minimal for v1** — stations conveyed via camera framing, lighting, and position + her EXISTING expressions/motions. No new full-body rigging now; wander system architected so poses drop in later. |

## 3. Tech architecture (grounded in the CURRENT stack)

Current: **PixiJS v6.5.10** + **live2dcubismcore** + **pixi-live2d-display/cubism4**, model in a
`PIXI.Application` with custom param control, lipsync, killed focus-controller, `applyStageMotion`
ticker. **We extend this app — we do not replace it.** Stay on Pixi v6 (pixi-live2d-display is not v8-
compatible; v6 has everything we need: filters, particles, graphics, containers).

```
PIXI.Application (existing)
└─ world: PIXI.Container         ← THE CAMERA. We translate/scale/rotate this.
   ├─ layer_sky      (parallax 0.1)   window light, sky, time-of-day gradient
   ├─ layer_back     (parallax 0.3)   back wall, posters, shelves
   ├─ layer_mid      (parallax 0.6)   desk, bed, furniture (her "stations")
   ├─ KOROKI (Live2D)(parallax 0.7)   ← existing model, reparented here
   ├─ layer_fore     (parallax 1.0)   foreground plants/props (most camera travel)
   └─ fx: particles + filters         dust, weather, bloom, ColorMatrix grade
```

- **Camera rig:** `world` container is the camera. Pan = translate; zoom/dolly = scale; subtle idle
  drift (Ken Burns) + mouse-parallax (layers offset by their parallax factor × pointer delta).
- **Color grading:** one `PIXI.filters.ColorMatrixFilter` on `world`, driven by time-of-day + weather
  (golden warm midday, blue cool night, desaturated overcast). This single filter does most of the
  "cinematic" lift cheaply.
- **Particles:** procedural sprites (no art assets) — dust motes always; rain/snow gated by weather.
  `@pixi/particle-emitter` (v6-compatible) or a tiny hand-rolled pool.
- **Bloom / rack-focus:** `@pixi/filter-advanced-bloom` + a blur filter swap on focus pulls. Polish phase.
- **worldstate binding:** poll `GET /v1/worldstate` every few seconds (mirror the existing
  `refreshHealth` pattern) → drives grade, particles, and which station she's at.

## 4. The hero room — her bedroom-studio

One space, four **stations** she moves between (this is how "wandering" works inside a single room
for v1, and what the visitor pans to find):

| Station | When she's there (from worldstate) | Pose/activity |
|---|---|---|
| **window** | morning; awake; calm/curious | leaning, looking out — light on her face |
| **bed** | late-night / asleep; low energy | sitting on the edge, or sleeping (away) |
| **desk** (stream setup) | midday; engaged; "streaming" context | at monitors, glow on her |
| **center** (floor) | high restlessness / idle | standing, pacing, turning toward you |

The visitor pans the camera across the room; if she's at a station off-screen, you travel to her (or
just watch the camera follow when she moves on her own).

## 5. Her wander state machine (worldstate → station)

Driven by fields `/v1/worldstate` already returns: `presence.sleep_state/awake`, `time.label`,
`body.energy`, `nervous.state` (restlessness/social_battery), `room.weather`.

```
asleep                         → bed (sleeping, "away" treatment)
late_night + awake + low energy→ bed (sitting)
morning + awake                → window
midday + engaged / streaming   → desk
high restlessness              → center (pacing)
default                        → center (idle, turns toward visitor)
```

**Decision needed (see forks):** derive the station **frontend-side** from these fields (no backend
change, fine for v1), OR add an authoritative `location`/`activity` field to `worldstate` (cleaner long-
term, small backend add). Recommend: derive frontend for v1, promote to backend later.

## 6. Opening cinematic (the first impression) — beat sheet

~6–9s, skippable after first visit (localStorage flag):
1. **Black**, faint room-tone audio (graded to time of day).
2. **Fade up on a detail** — light through the window, dust motes, real time/weather grade.
3. **Slow dolly/pan** across the room; parallax depth sells the space (past posters, desk glow…).
4. **Camera finds her** — rack-focus from foreground onto Koroki at her current station, doing her
   current activity.
5. **Title/logo** treatment eases in then settles; **UI** (chat composer, menu) fades in last.

## 7. Asset pipeline — the real bottleneck (zero-budget)

The rig isn't the constraint; **assets are.** Two asset classes:
- **Layered room art** (sky/window · back wall · mid furniture · foreground props). Plan: AI-generate
  the room in a consistent style, then separate into parallax layers (generate layers with a shared
  seed/prompt, or cut one render into depth bands + inpaint what's behind each layer). No cost.
- **Live2D poses/motions** for sit / sleep / window-lean / stand-pace. This is **human rigging labor**
  on her existing Cubism model — the genuine time sink, and it gates how rich the stations look.
- **Particles/grading:** procedural, no assets.

## 7b. Direction update (2026-06-28, after first preview)

User saw the P0/P1 preview and set the bar: **maximal, dense, "alive and complicated" — not a simple
site.** Pulled the intro and navigation forward, and asked for a thick effects layer with *placeholder*
art. So the standalone preview (`world.html`/`world.js`) now also carries early versions of P4/P5/P6:
- **Intro "entering her place"** — doors part + camera push-in + title (skippable; remembers via
  localStorage).
- **Multi-room navigation** — 3 placeholder rooms (Bedroom / Studio / Lounge); ‹ › or arrow keys walk
  between them; she's in the room her station maps to ("● she's here"); you can wander to find her.
- **Atmosphere stack** — godrays, drifting haze, foreground bokeh, film grain, vignette, sparkles,
  animated neon + monitor flicker, per-layer mouse parallax, rain (weather-gated), all over the
  worldstate color-grade. This is the "aliveness" engine; real art (P2) drops into it later.

## 7c. Bespoke direction (2026-06-28) — art, palette, custom FX + sound

User wants it to feel decorated, real, attentive — NOT default/simple. Locked intent:
- **Palette:** "Crimson Midnight" — tuned to her white/crimson gothic-fox design. Her rose is the
  hero accent; teal is a restrained complementary tech-glow; deep violet-black base. (Live in v4.)
- **AI-gen art (anime style, matching her):**
  - **Intro key-art** — a full illustration of Koroki for the title/cold-open (not just text), parallax-
    layered. Anime/drawn style consistent with her Live2D design (white hair, fox ears, crimson lolita).
  - **Layered room backgrounds** per room — generate in one consistent neon-night anime style, separated
    into depth layers (window/city · back wall · furniture · foreground) that drop into the existing
    parallax slots. Zero-budget → local/free image-gen; we draft prompts + the layer-separation method.
- **Custom effects + sound (so it's not the default look/feel):**
  - **Audio layer** — the intro "enter" click is the perfect moment to UNLOCK browser audio (autoplay is
    gated behind a user gesture; the click IS that gesture). Then: ambient room tone + soft synth pad,
    per-room ambience, subtle UI whooshes (enter, room-change). Free sources: freesound.org, pixabay,
    zapsplat (or generate).
  - **Better FX assets** — replace the procedural soft-circle with sourced light-leak / bokeh / dust /
    lens-dirt sprites + a film-grain texture, dropped into the existing FX slots. Free sources.

## 8. Build phases (each independently testable, no art needed until P2)

| Phase | Deliverable | Needs art? |
|---|---|---|
| **P0** | ✅ **BUILT 2026-06-28** — `clients/web/world.html` + `world.js` (standalone preview, doesn't touch index.html). Camera/`world` container, 6 parallax layers (neon-night placeholders), mouse-parallax + idle drift, Live2D reparented + framed. | No (color blocks) |
| **P1** | ✅ **BUILT 2026-06-28** — polls `/v1/worldstate`: ColorMatrix grade by time-of-day + weather, rain/dust particles, neon flicker, and the wander state machine (station derived from sleep/time/energy/restlessness; camera follows + she moves to it). Debug HUD ('h' to toggle). | No |
| **P2** | Hero-room art: real layered background for the room. | **Yes** (the lift) |
| **P3** | Wander state machine: stations + camera-follow + pose transitions. | Poses |
| **P4** | Opening cinematic choreography + title treatment + skip flag. | No |
| **P5** | Visitor navigation: pan/drag to explore, click-to-travel to a station. | No |
| **P6** | Polish: bloom, rack-focus, idle camera drift, ambient room-tone audio. | Audio (opt) |

**P0 + P1 need zero new art** — we can build and *see* the camera, parallax, live grading, and weather
this week, using her existing model + placeholder layers. The art (P2) and Live2D poses (P3) are where
your input/labor decide the ceiling.

## 9. Open forks (decide before P2; P0/P1 can start regardless)

1. **Room aesthetic** — what's her room *feel*? (cozy dark gamer den / bright airy minimal / warm
   lived-in clutter / neon-accented?) This sets the whole art direction.
2. **Live2D pose labor** — can you rig new poses/motions on her Cubism model, or should v1 keep poses
   minimal (lean on camera + expressions rather than full body poses)? This gates station richness.
3. **Station source** — derive frontend-side for v1 (recommended) vs add `location` to backend worldstate.
4. **Opening audio** — ambient room-tone / light score in the cold-open? Big cinematic payoff, but it's
   an asset to source.
