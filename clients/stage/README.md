# Koroki Stage — the living web compositor

One transparent web page = the whole broadcast scene. OBS captures it as a
single browser source. Architecture: `docs/frontend_compositor_verdict.md`.

## Run
Double-click `start_stage.bat` (starts MediaMTX + the stage server + opens
http://127.0.0.1:9770). Press **F** on the page for the fps meter.

## What's on the stage (steps 1-3 of the arc, built 2026-07-08)
- **The room**: `A_bedroom_night` painting + a Depth-Anything depth map driving
  a DisplacementFilter — mouse parallax + slow autonomous drift. Regenerate for
  any painting: `.venv\Scripts\python.exe scripts\gen_depth_map.py <image>`
- **Her standee**: sprite with 3.8s breathing, pixel-alpha hit-testing (her
  hitbox IS her silhouette — click through the transparent box hits the room).
  Petting flips her happy; spam-petting earns smug. Placeholder for the DIY
  part-rig puppet (minimal rig approved).
- **The diegetic screen**: an "off air" pane that becomes a LIVE view of the
  owner's OBS feed via WHEP. Feed it: OBS Settings > Stream > Service WHIP,
  server `http://127.0.0.1:8889/koroki/whip`, no token > Start Streaming.
  The pane auto-connects (retries every 15s while off-air).
- **Ambience**: drifting dust motes + rare ambient shooting stars. Cheap, effective.

## Interactions (v2, 2026-07-08 — click things)
| Target | What happens |
|---|---|
| Her | happy; spam-pet -> smug (pixel-alpha silhouette hitbox) |
| The lamp | lights out / lights on (whole-room night tint) |
| The bed | soft poomf particles + a blanket shiver through the depth map |
| The plant | leaf rustle |
| The night sky | a shooting star from your click (plus rare ambient ones) |
| The aircon | *slot ready* — cool-breeze wisps toggle once `assets/prop_ac.png` exists |

Hotspots are polygons in painting coords (`CFG.hotspots` in stage.js) — retune
freely. Added props (`CFG.props`) support rotation + skew for wall perspective;
the AC slot targets the free wall at painting (985,150), width 200.

## OBS as the broadcast scene
Add Browser Source: URL `http://127.0.0.1:9770`, size = canvas, check
"Shutdown source when not visible". Page background is transparent — layer it
over anything, or use it AS the scene.
Windows gotcha (verdict doc): OBS's browser process gets throttled to 1-2fps
by Efficiency Mode when unfocused — run OBS as admin + enable Browser Hardware
Acceleration in OBS settings.

## Effects policy (owner asked about free effect libraries, 2026-07-08)
Rule: effects must render INSIDE our compositor (respect layers/lighting/masks).
- **pixi-filters vendored** (vendor/pixi-filters.min.js, MIT, exposes PIXI.filters):
  GPU shaders on our own layers. In use: GlowFilter on the sky. On the shelf for
  later: GodrayFilter (window light shafts), CRTFilter (the diegetic screen!),
  AdjustmentFilter (day/night palette), ShockwaveFilter, GlitchFilter.
- Drop-in particle libs (tsparticles etc.): REJECTED — own render loops that
  ignore our lighting/layer order. Effect sites = recipe books to hand-port.
- QUEUED SPECIAL: rain-on-glass shader driven by HER SIMULATED WEATHER
  (world/events.py already rains in her world — the stage should show it).

## Sprite pipeline
Alpha-cut new sprites with rembg's anime model:
`.venv\Scripts\python.exe -c "from rembg import remove,new_session; from PIL import Image; s=new_session('isnet-anime'); Image.open('in.png').save; remove(Image.open('in.png'),session=s).save('out.png')"`
(originals live in assets/koroki_sprites/, cut copies in clients/stage/assets/)

## Next (queued in the arc — owner's room roadmap 2026-07-08)
1. **Bedroom decor pass** (after v4 training frees the GPU): AC art + more
   props/elements + interactions, richer lighting moments. Puppet stays hidden
   (CFG.puppet.show / press K) until the background is where he wants it.
2. **Studio room** — second scene (needs art-farm session).
3. **Lounge** — third scene (A_lounge_evening.png exists in the pool).
   Multi-scene = scene configs (room art + depth + hotspots + lights per room)
   with a switcher; the compositor already treats all of these as data.
Then: minimal part-rig puppet (6-8 parts) replacing the standee; viseme mouth
from TTS; felt-state expressions via orchestrator WS; "her room her opinions"
reactions; weather sync (her simulated rain on the window).
