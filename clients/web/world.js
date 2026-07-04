// Koroki cinematic world — standalone preview stage.
//
// Self-contained (open /world.html); does NOT touch index.html / app.js. Zero new ART —
// neon-night PLACEHOLDER geometry — but the *aliveness* is real and dense: multi-room
// navigation, an "entering her place" intro, and a thick atmosphere stack (godrays, haze,
// bokeh, film grain, vignette, sparkles, animated neon, parallax, color grading bound to
// /v1/worldstate). Replace placeholder geometry with real layered art later (P2); the
// effect engine stays.
//
// Controls: ‹ › or arrow keys = walk between rooms · 'h' = toggle HUD · space = skip intro.

const MODEL_URL = encodeURI("/assets/live2d/苹果小狐狸/苹果小狐狸.model3.json");
const POLL_MS = 4000;

// ── Audio: room tone + UI SFX + her spoken commentary ───────────────────────
// Browsers gate audio behind a user gesture; the intro "enter" click is that gesture
// (Audio.unlock). SFX files live in assets/audio/ (drop in Pixabay sounds); missing files
// no-op. Commentary calls /v1/world/voice?cue=... (live once the orchestrator has the
// new endpoint); 404 → silent.
const KAudio = (() => {
  const sounds = {};
  let muted = false, roomtone = null, unlocked = false;
  function load(name, src, opts = {}) {
    const a = new window.Audio(src);
    a.loop = !!opts.loop; a.volume = opts.volume ?? 1; a.preload = "auto";
    a.addEventListener("error", () => { sounds[name] = null; });
    sounds[name] = a; return a;
  }
  function play(name, volume) {
    const a = sounds[name]; if (!a || muted) return;
    try { a.currentTime = 0; if (volume != null) a.volume = volume; a.play().catch(() => {}); } catch (e) {}
  }
  function fade(a, to, ms) {
    const from = a.volume, t0 = performance.now();
    (function step() { const k = Math.min(1, (performance.now() - t0) / ms); a.volume = from + (to - from) * k; if (k < 1) requestAnimationFrame(step); })();
  }
  function unlock() {
    if (unlocked) return; unlocked = true;
    roomtone = load("roomtone", "./assets/audio/roomtone.mp3", { loop: true, volume: 0 });
    if (roomtone) roomtone.play().then(() => fade(roomtone, 0.3, 2000)).catch(() => {});
  }
  async function voice(cue) {
    if (muted) return;
    try {
      const r = await fetch(`/v1/world/voice?cue=${encodeURIComponent(cue)}`, { credentials: "same-origin" });
      if (!r.ok) return;
      const d = await r.json();
      const a = new window.Audio(d.audio_url); a.volume = 0.92; a.play().catch(() => {});
    } catch (e) {}
  }
  function toggleMute() { muted = !muted; if (roomtone) roomtone.muted = muted; return muted; }
  // preload UI sfx (optional files)
  load("enter", "./assets/audio/enter.mp3", { volume: 0.6 });
  load("room", "./assets/audio/room.mp3", { volume: 0.5 });
  return { play, unlock, voice, toggleMute };
})();

// "Crimson Midnight" — tuned to her (white/crimson gothic-fox). Her rose is the HERO
// accent so neon echoes her; teal kept as restrained complementary tech-glow (deeper +
// dimmer so it stops competing); base deepened to violet-black so she pops.
const C = {
  base: 0x070611, wall: 0x120b1e, window: 0x1b2746, furniture: 0x0b0813,
  monitor: 0x153a50, dust: 0xe7d6f0,
  magenta: 0xff3d72, // her crimson-rose — hero accent
  cyan: 0x32d0e6,    // restrained teal — complementary tech glow
  purple: 0x8f46f0,  // deep violet — supporting only
};

// Rooms (placeholder). station→room index mapping drives where SHE is.
const ROOMS = [
  { name: "Bedroom", accent: C.purple },
  { name: "Studio", accent: C.magenta },
  { name: "Lounge", accent: C.cyan },
];
const STATION_ROOM = { bed: 0, center: 1, desk: 1, window: 2 };
// Where she stands for each station, as (fx, fy) WITHIN her station's room. Drives
// procedural walking: she eases to her station's anchor (+ an idle-wander offset).
const STATION_ANCHOR = {
  bed:    { fx: 0.30, fy: 0.70 },
  center: { fx: 0.45, fy: 0.66 },
  desk:   { fx: 0.62, fy: 0.64 },
  window: { fx: 0.80, fy: 0.60 },
};

// NOTE: per-room floating decor (particles/embers/petals/fireflies/bokeh/glow hotspots) and the
// fairy-light garland sprites were REMOVED — over the real AI room art they read as noisy
// "random sparkles" and the garland became a hard-edged glowing rectangle. The room art carries
// the look; aliveness now comes from the subtle camera grade + vignette + faint grain + parallax.

const $ = (id) => document.getElementById(id);
const setStatus = (m) => { const e = $("status"); if (e) e.textContent = m; };
const lerp = (a, b, t) => a + (b - a) * t;
const W = (app) => app.screen.width;
const H = (app) => app.screen.height;

// ── canvas-generated textures (soft circle / vignette / noise) ───────────────
function softCircleTexture() {
  const s = 256, cv = document.createElement("canvas"); cv.width = cv.height = s;
  const ctx = cv.getContext("2d");
  const g = ctx.createRadialGradient(s / 2, s / 2, 0, s / 2, s / 2, s / 2);
  g.addColorStop(0, "rgba(255,255,255,1)"); g.addColorStop(0.4, "rgba(255,255,255,0.5)");
  g.addColorStop(1, "rgba(255,255,255,0)");
  ctx.fillStyle = g; ctx.fillRect(0, 0, s, s);
  return window.PIXI.Texture.from(cv);
}
function vignetteTexture(w, h) {
  const cv = document.createElement("canvas"); cv.width = w; cv.height = h;
  const ctx = cv.getContext("2d");
  const g = ctx.createRadialGradient(w / 2, h / 2, Math.min(w, h) * 0.25, w / 2, h / 2, Math.max(w, h) * 0.72);
  g.addColorStop(0, "rgba(255,255,255,1)"); g.addColorStop(1, "rgba(20,24,40,1)");
  ctx.fillStyle = g; ctx.fillRect(0, 0, w, h);
  return window.PIXI.Texture.from(cv);
}
function noiseTexture(s = 220) {
  const cv = document.createElement("canvas"); cv.width = cv.height = s;
  const ctx = cv.getContext("2d"); const img = ctx.createImageData(s, s);
  for (let i = 0; i < img.data.length; i += 4) {
    const v = Math.random() * 255; img.data[i] = img.data[i + 1] = img.data[i + 2] = v; img.data[i + 3] = 255;
  }
  ctx.putImageData(img, 0, 0); return window.PIXI.Texture.from(cv);
}

// ── room placeholder geometry ────────────────────────────────────────────────
function buildRoom(app, idx, meta, softTex) {
  const PIXI = window.PIXI, w = W(app), h = H(app);
  const room = new PIXI.Container();
  room.x = idx * w;
  // Clip to the room rect: cover-scaled art overflows horizontally whenever the window
  // is squarer than the art's aspect, bleeding ~190px into the neighboring room.
  const clip = new PIXI.Graphics();
  clip.beginFill(0xffffff).drawRect(0, 0, w, h).endFill();
  room.addChild(clip); room.mask = clip;
  const far = new PIXI.Container(); far.pfac = 0.12;
  const mid = new PIXI.Container(); mid.pfac = 0.5;
  const near = new PIXI.Container(); near.pfac = 1.0;
  room.addChild(far, mid, near);
  room.layers = { far, mid, near };
  room.neon = []; room.glows = [];

  // far: wall + window with city glow
  const wall = new PIXI.Graphics();
  wall.beginFill(C.wall).drawRect(0, 0, w, h).endFill();
  wall.beginFill(meta.accent, 0.04).drawRect(0, 0, w, h * 0.5).endFill();
  far.addChild(wall);
  const winX = w * 0.6, winY = h * 0.1, winW = w * 0.3, winH = h * 0.55;
  const halo = new PIXI.Sprite(softTex);
  halo.anchor.set(0.5); halo.tint = C.cyan; halo.alpha = 0.12; halo.blendMode = PIXI.BLEND_MODES.ADD;
  halo.position.set(winX + winW / 2, winY + winH / 2); halo.width = winW * 2.4; halo.height = winH * 2.2;
  far.addChild(halo);
  const win = new PIXI.Graphics();
  win.beginFill(C.window, 0.95).drawRoundedRect(winX, winY, winW, winH, 12).endFill();
  for (let i = 0; i < 18; i++) {
    win.beginFill(i % 2 ? C.magenta : C.cyan, 0.08 + Math.random() * 0.2);
    win.drawRect(winX + Math.random() * winW, winY + winH * (0.4 + Math.random() * 0.55), 3 + Math.random() * 9, winH * (0.08 + Math.random() * 0.32));
    win.endFill();
  }
  far.addChild(win);

  // mid: neon signage (accent) + furniture silhouettes
  const signs = [
    { x: w * 0.08, y: h * 0.14, w: 150, h: 56 },
    { x: w * 0.3, y: h * 0.1, w: 84, h: 84 },
  ];
  for (const s of signs) {
    const glow = new PIXI.Graphics(); glow.lineStyle(16, meta.accent, 0.12).drawRoundedRect(s.x, s.y, s.w, s.h, 12);
    const line = new PIXI.Graphics(); line.lineStyle(3, meta.accent, 0.95).drawRoundedRect(s.x, s.y, s.w, s.h, 12);
    mid.addChild(glow, line); room.neon.push(line);
  }
  // room-specific furniture
  const f = new PIXI.Graphics();
  if (idx === 0) { // bedroom: bed
    f.beginFill(C.furniture).drawRoundedRect(w * 0.08, h * 0.6, w * 0.42, h * 0.3, 18).endFill();
    f.beginFill(meta.accent, 0.06).drawRoundedRect(w * 0.08, h * 0.6, w * 0.42, h * 0.08, 18).endFill();
  } else if (idx === 1) { // studio: desk + monitors
    f.beginFill(C.furniture).drawRect(w * 0.46, h * 0.52, w * 0.46, h * 0.42).endFill();
    const m1 = new PIXI.Graphics(); m1.beginFill(C.monitor, 0.9).drawRoundedRect(w * 0.5, h * 0.34, w * 0.16, h * 0.16, 6).endFill();
    const m2 = new PIXI.Graphics(); m2.beginFill(C.monitor, 0.7).drawRoundedRect(w * 0.68, h * 0.36, w * 0.13, h * 0.14, 6).endFill();
    mid.addChild(m1, m2); room.glows.push(m1, m2);
  } else { // lounge: couch
    f.beginFill(C.furniture).drawRoundedRect(w * 0.1, h * 0.64, w * 0.5, h * 0.22, 26).endFill();
  }
  mid.addChild(f);

  // No floating decor/dust over the art — the room art carries the look; depth comes from
  // parallax + the subtle grade/vignette. (placeholder geometry above only shows if art 404s.)
  return room;
}

// ── global atmosphere overlays (screen-space, above the world) ───────────────
function buildAtmosphere(app, softTex) {
  const PIXI = window.PIXI, w = W(app), h = H(app);
  const fx = {};

  // (godrays / drifting haze / foreground bokeh / sparkles REMOVED — over the real room art
  //  they read as floating-light noise, not a cosy filter. Only the grade + vignette + faint
  //  grain remain as the camera filter; rain stays because it's weather-driven.)

  // rain (weather-gated)
  fx.rain = new PIXI.Container(); fx.rainItems = []; fx.rainActive = false;
  for (let i = 0; i < 260; i++) {
    const g = new PIXI.Graphics(); g.lineStyle(1.3, 0xbfe6ff, 0.32).moveTo(0, 0).lineTo(-3, 17); g.visible = false;
    fx.rain.addChild(g); fx.rainItems.push({ g, sp: 9 + Math.random() * 10, x: Math.random() * w, y: Math.random() * h });
  }

  // vignette (multiply) + film grain (tiling noise)
  fx.vignette = new PIXI.Sprite(vignetteTexture(w, h));
  fx.vignette.width = w; fx.vignette.height = h; fx.vignette.blendMode = PIXI.BLEND_MODES.MULTIPLY;
  fx.grain = new PIXI.TilingSprite(noiseTexture(), w, h);
  fx.grain.alpha = 0.045; fx.grain.blendMode = PIXI.BLEND_MODES.ADD;

  // screen-space, above the world: rain, then the cosy filter (vignette + grain)
  app.stage.addChild(fx.rain, fx.vignette, fx.grain);
  return fx;
}

// ── worldstate → grade + station ─────────────────────────────────────────────
function gradeTargets(ws) {
  const label = (ws?.time?.label || "").toLowerCase(), weather = (ws?.room?.weather || "").toLowerCase();
  let b = 0.95, s = 1.15, h = 8;
  if (/morning|dawn/.test(label)) { b = 1.05; s = 1.06; h = -4; }
  else if (/mid|noon|after/.test(label)) { b = 1.1; s = 1.0; h = 0; }
  else if (/even|dusk/.test(label)) { b = 0.98; s = 1.2; h = 10; }
  else if (/night|late/.test(label)) { b = 0.84; s = 1.2; h = 13; }
  if (/rain|storm|drizzle/.test(weather)) { b *= 0.9; s *= 0.9; }
  else if (/snow/.test(weather)) { b *= 1.05; s *= 0.85; }
  return { b, s, h };
}
function deriveStation(ws) {
  const sleep = (ws?.presence?.sleep_state || "").toLowerCase(), awake = ws?.presence?.awake;
  const label = (ws?.time?.label || "").toLowerCase();
  const energy = ws?.body?.energy ?? 0.6, restless = ws?.nervous?.state?.restlessness ?? 0;
  if (awake === false || /asleep|sleep/.test(sleep)) return "bed";
  if (/late|night/.test(label) && energy < 0.4) return "bed";
  if (/morning|dawn/.test(label)) return "window";
  if (/mid|noon|after/.test(label)) return "desk";
  if (restless > 0.6) return "center";
  return "center";
}

// ── room art loader ─────────────────────────────────────────────────────────
// If generated PNG layers exist (assets/world/<room>_back.png / _furniture.png), swap
// them in for the placeholder geometry. Missing files → keep the placeholder (graceful).
async function tryTex(url) {
  try { return await window.PIXI.Texture.fromURL(url); } catch (e) { return null; }
}
function coverSprite(tex, w, h) {
  const s = new window.PIXI.Sprite(tex);
  const sc = Math.max(w / tex.width, h / tex.height);
  s.width = tex.width * sc; s.height = tex.height * sc;
  s.anchor.set(0.5); s.position.set(w / 2, h / 2);
  return s;
}
const ASSET_VER = "11";  // bump to force the browser to refetch regenerated room art
async function loadRoomArt(app, rooms) {
  const PIXI = window.PIXI, w = W(app), h = H(app);
  for (let i = 0; i < rooms.length; i++) {
    const prefix = ROOMS[i].name.toLowerCase();
    const back = await tryTex(`assets/world/${prefix}_back.png?v=${ASSET_VER}`);
    if (!back) continue;                              // no art -> keep placeholder room
    const far = rooms[i].layers.far;
    far.removeChildren();                             // drop placeholder window/wall
    far.addChild(coverSprite(back, w, h));
    const furn = await tryTex(`assets/world/${prefix}_furniture.png?v=${ASSET_VER}`);
    if (furn) far.addChild(coverSprite(furn, w, h));  // transparent desk over the back
    rooms[i].layers.mid.removeChildren();             // drop placeholder neon/desk (model re-added after)
    rooms[i].neon = []; rooms[i].glows = [];          // stop flicker on removed graphics
  }
}

async function main() {
  if (!window.PIXI?.Application || !window.PIXI?.live2d?.Live2DModel) {
    setStatus("Live2D runtime missing (CDN blocked?)"); return;
  }
  const PIXI = window.PIXI;
  const app = new PIXI.Application({
    view: $("world-canvas"), resizeTo: window, antialias: true, backgroundColor: C.base, autoStart: true,
  });
  const softTex = softCircleTexture();

  // world = rooms; graded; camera via pivot/scale.
  const world = new PIXI.Container();
  const grade = new PIXI.filters.ColorMatrixFilter();
  world.filters = [grade];
  app.stage.addChild(world);
  // Clamp the grade filter to the visible screen — the world spans 3 rooms wide, so
  // without this the filter would allocate a 3-screen-wide texture each frame.
  world.filterArea = app.screen;

  let rooms = ROOMS.map((m, i) => buildRoom(app, i, m, softTex));
  rooms.forEach((r) => world.addChild(r));
  const fx = buildAtmosphere(app, softTex);
  // debug handle (console-only; harmless in production)
  window.__world = { app, world, cam: null, rooms: () => rooms };

  // Koroki
  let model = null;
  try {
    setStatus("loading Koroki…");
    const j = await (await fetch(MODEL_URL)).json(); j.url = MODEL_URL;
    model = await PIXI.live2d.Live2DModel.from(j);
    model.autoInteract = false;
    const fc = model.internalModel?.focusController; if (fc) fc.update = () => {};
    fitModel(app, model);
    $("status").classList.add("hidden");
  } catch (e) { console.error(e); setStatus("Koroki failed to load — world still alive"); }

  // state
  const cam = { pivotX: W(app) / 2, scale: 1.6, targetScale: 1.6 };
  window.__world.cam = cam;
  const g = { b: 0.95, s: 1.15, h: 8, tb: 0.95, ts: 1.15, th: 8 };
  const pointer = { x: 0, y: 0 };
  let activeRoom = 1, herRoom = 1, herStation = "center";

  // ── Koroki locomotion (procedural — her model has no walk motion) ───────────
  // She lives in WORLD space (not parented to a room) so she can walk ACROSS the
  // 3-room layout. Target = her station's anchor + an idle-wander offset; the ticker
  // eases her there with a bob + a param-lean toward travel (no mirror — her hairpin
  // is asymmetric). The camera follows the VISITOR's room, so you only watch her when
  // you share her room — she has her own life off-view.
  const her = { x: 0, y: 0, tx: 0, ty: 0, lean: 0, leanT: 0, walking: false, bob: 0,
                station: "center", wander: { x: 0, y: 0 }, wanderT: 6 + Math.random() * 8 };
  function anchorWorld(station, off) {
    const ri = STATION_ROOM[station] ?? 1;
    const a = STATION_ANCHOR[station] || STATION_ANCHOR.center;
    return { x: ri * W(app) + (a.fx + (off?.x || 0)) * W(app), y: (a.fy + (off?.y || 0)) * H(app) };
  }
  function setHerTarget() { const p = anchorWorld(her.station, her.wander); her.tx = p.x; her.ty = p.y; }
  function snapHer() {
    const p = anchorWorld(her.station, her.wander); her.x = her.tx = p.x; her.y = her.ty = p.y;
    if (model) model.position.set(her.x, her.y);
  }
  if (model) world.addChild(model);   // in front of the rooms; atmosphere still overlays her
  await loadRoomArt(app, rooms);
  snapHer();

  function updateNav() {
    $("room-name").innerHTML = `<b>${ROOMS[activeRoom].name}</b>`;
    $("she-here").textContent = activeRoom === herRoom ? "● she's here" : "";
  }
  const go = (d) => {
    const prev = activeRoom;
    activeRoom = Math.max(0, Math.min(rooms.length - 1, activeRoom + d));
    if (activeRoom !== prev) {
      updateNav();
      if (introStarted) { KAudio.play("room"); KAudio.voice("room_" + ROOMS[activeRoom].name.toLowerCase()); }
    }
  };
  $("nav-left").onclick = () => go(-1);
  $("nav-right").onclick = () => go(1);
  window.addEventListener("pointermove", (e) => {
    pointer.x = (e.clientX / window.innerWidth - 0.5) * 2;
    pointer.y = (e.clientY / window.innerHeight - 0.5) * 2;
  });
  window.addEventListener("keydown", (e) => {
    if (e.key === "ArrowLeft") go(-1); else if (e.key === "ArrowRight") go(1);
    else if (e.key === "h") $("hud").classList.toggle("hidden");
    else if (e.key === "m") { const muted = KAudio.toggleMute(); const el = $("hud-mute"); if (el) el.textContent = muted ? "muted" : "on"; }
    else if (e.code === "Space" || e.code === "Enter") enterPlace();
  });
  // PIXI's resizeTo applies asynchronously — hook the renderer, not the window, so the
  // first deferred resize also rebuilds (window-only listener left rooms sized for the
  // pre-resize canvas on initial load: misaligned rooms / neighbor-room bleed).
  app.renderer.on("resize", () => rebuild());
  function rebuild() {
    rooms.forEach((r) => world.removeChild(r));
    rooms = ROOMS.map((m, i) => buildRoom(app, i, m, softTex));
    rooms.forEach((r) => world.addChild(r));
    fx.vignette.texture = vignetteTexture(W(app), H(app));
    fx.vignette.width = W(app); fx.vignette.height = H(app);
    fx.grain.width = W(app); fx.grain.height = H(app);
    loadRoomArt(app, rooms).then(() => { if (model) { fitModel(app, model); world.addChild(model); snapHer(); } });
  }

  // ── intro: "entering her place" — plays EVERY load, waits for the visitor to enter ──
  let introStarted = false;
  const intro = $("intro");
  // Held title screen: camera zoomed in, doors shut (room hidden behind them), title +
  // prompt up. Nothing auto-advances — the visitor's click/keypress is the act of entering.
  cam.scale = 1.75; cam.targetScale = 1.75;
  $("title").classList.add("show");
  function enterPlace() {
    if (introStarted) return; introStarted = true;
    KAudio.unlock(); KAudio.play("enter"); KAudio.voice("enter");
    cam.targetScale = 1.0;                  // camera pushes in
    intro.classList.add("open");            // doors part
    $("title").classList.remove("show");    // title fades
    window.removeEventListener("pointerdown", enterPlace);
    setTimeout(() => { intro.classList.add("hidden"); $("title").classList.add("hidden"); $("skip").classList.add("hidden"); }, 1500);
  }
  $("skip").onclick = enterPlace;
  window.addEventListener("pointerdown", enterPlace);
  updateNav();

  // ── worldstate poll ──
  async function poll() {
    try { const r = await fetch("/v1/worldstate", { credentials: "same-origin" }); if (r.ok) apply(await r.json()); }
    catch (_) {}
  }
  function apply(ws) {
    const t = gradeTargets(ws); g.tb = t.b; g.ts = t.s; g.th = t.h;
    const weather = (ws?.room?.weather || "").toLowerCase();
    fx.rainActive = /rain|storm|drizzle|snow/.test(weather);
    fx.rainItems.forEach((d) => (d.g.visible = fx.rainActive));
    herStation = deriveStation(ws);
    if (herStation !== her.station) {                 // station changed → she walks there
      her.station = herStation; herRoom = STATION_ROOM[herStation] ?? 1;
      her.wander = { x: 0, y: 0 }; setHerTarget(); updateNav();
    }
    const set = (id, v) => { const e = $(id); if (e) e.textContent = v; };
    set("hud-time", ws?.time?.label || "—"); set("hud-weather", ws?.room?.weather || "—");
    set("hud-presence", ws?.presence ? (ws.presence.awake ? "awake" : "asleep") : "—");
    set("hud-station", `${herStation} → ${ROOMS[herRoom].name}`);
    set("hud-mood", ws?.felt?.mood || (ws?.body?.endocrine?.felt?.mood || [])[0] || "—");
  }
  poll(); setInterval(poll, POLL_MS);

  // ── render loop ──
  let t = 0;
  app.ticker.add(() => {
    const dt = app.ticker.deltaMS / 1000; t += dt;
    const w = W(app), h = H(app);
    // dt-corrected easing: a fixed per-frame lerp factor stalls at low fps (intro crawl
    // on slow machines / throttled tabs). Same feel as the old factors at 60fps.
    const ease = (f) => 1 - Math.pow(1 - f, app.ticker.deltaMS / 16.667);

    // camera pan (pivot to active room) + intro zoom + idle drift
    cam.pivotX = lerp(cam.pivotX, activeRoom * w + w / 2, ease(0.06));
    cam.scale = lerp(cam.scale, cam.targetScale, ease(0.045));
    world.pivot.set(cam.pivotX, h / 2);
    world.position.set(w / 2 + Math.sin(t * 0.17) * w * 0.008, h / 2 + Math.sin(t * 0.12) * h * 0.006);
    world.scale.set(cam.scale);

    // per-room parallax (mouse) + placeholder-neon flicker (only present if art 404s)
    for (const room of rooms) {
      for (const key of ["far", "mid", "near"]) {
        const L = room.layers[key]; if (!L) continue;
        L.position.set(pointer.x * L.pfac * 22, pointer.y * L.pfac * 12);
      }
      for (const n of room.neon) n.alpha = 0.82 + Math.sin(t * 6 + n.x) * 0.12 + (Math.random() < 0.008 ? -0.45 : 0);
      for (const gl of room.glows) gl.alpha = 0.75 + Math.sin(t * 3.3 + gl.y) * 0.18;
    }

    // Koroki locomotion: ease toward target, bob, lean into travel (params, no mirror)
    if (model) {
      const dx = her.tx - her.x, dy = her.ty - her.y, dist = Math.hypot(dx, dy);
      if (dist > 4) {
        const step = Math.min(w * 0.16 * dt, dist);
        her.x += (dx / dist) * step; her.y += (dy / dist) * step;
        her.bob += dt * 6; her.walking = true; her.leanT = dx >= 0 ? 1 : -1;
      } else { her.walking = false; her.leanT = 0; }
      her.lean = lerp(her.lean, her.leanT, ease(0.05));
      const bobY = her.walking ? Math.abs(Math.sin(her.bob)) * h * 0.012 : 0;
      const tilt = her.walking ? Math.sin(her.bob) * 3 : 0;
      model.position.set(her.x, her.y - bobY);
      try {
        const cm = model.internalModel.coreModel;
        cm.setParameterValueById("ParamAngleX", her.lean * 14);
        cm.setParameterValueById("ParamBodyAngleX", her.lean * 7);
        cm.setParameterValueById("ParamAngleZ", tilt * 0.5);
        cm.setParameterValueById("ParamBodyAngleZ", tilt);
      } catch (e) {}
      her.wanderT -= dt;                                 // occasional self-initiated drift
      if (her.wanderT <= 0 && !her.walking) {
        her.wanderT = 7 + Math.random() * 9;
        her.wander = { x: (Math.random() - 0.5) * 0.14, y: (Math.random() - 0.5) * 0.05 };
        setHerTarget();
      }
    }

    // grade lerp
    const gk = ease(0.03);
    g.b = lerp(g.b, g.tb, gk); g.s = lerp(g.s, g.ts, gk); g.h = lerp(g.h, g.th, gk);
    grade.reset(); grade.brightness(g.b, false); grade.saturate(g.s - 1, true); grade.hue(g.h, true);

    // rain
    if (fx.rainActive) for (const d of fx.rainItems) { d.g.y = (d.y += d.sp); d.g.x = (d.x -= d.sp * 0.18); d.g.position.set(d.x, d.y); if (d.y > h) { d.y = -20; d.x = Math.random() * w * 1.3; } }
    // grain shimmer
    fx.grain.tilePosition.x = Math.random() * 50; fx.grain.tilePosition.y = Math.random() * 50;
  });
}

function fitModel(app, model) {
  const w = W(app), h = H(app), b = model.getLocalBounds();
  const mw = Math.max(b.width, 1), mh = Math.max(b.height, 1);
  const scale = Math.min((w * 0.34) / mw, (h * 0.82) / mh);
  model.scale.set(scale);
  model.pivot.set(b.x + mw / 2, b.y + mh * 0.3);
}

main().catch((e) => { console.error(e); setStatus("world init failed: " + e.message); });
