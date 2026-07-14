/* Koroki Stage — the living web compositor (frontend arc, 2026-07-08).
 *
 * One transparent PixiJS canvas = the whole OBS-capturable broadcast scene.
 * v2 (owner: "add a bunch more interactive stuffs literally"):
 *   - hotspot polygons over PAINTED furniture (lamp/bed/plant/sky) with
 *     effect responses — lights-out toggle, bed poof, leaf rustle, stars
 *   - sprite-prop system with angle/skew placement for ADDED props (the AC
 *     slot on the free wall is ready — drops in when the art exists)
 *   - everything from v1: depth-parallax room, breathing standee with
 *     pixel-alpha petting, dust, WHEP diegetic screen
 *
 * Design: docs/frontend_compositor_verdict.md · assets: README.md
 */

const CFG = {
  room: "assets/room.png",
  depth: "assets/room_depth.png",
  puppet: {
    idle: "assets/koroki_neutral.png",
    happy: "assets/koroki_happy.png",
    smug: "assets/koroki_smug.png",
    xAnchor: 0.62,
    height: 0.78,
    show: false,   // hidden while background decor work happens (owner
                   // 2026-07-08); the real part-rig puppet replaces the
                   // standee anyway. Press K on the page to peek at her.
  },
  screen: {
    x: 0.115, y: 0.30, w: 0.26, h: 0.30,
    whep: "http://127.0.0.1:8889/koroki/whep",
  },
  parallax: { mouse: 18, drift: 5 },
  dust: 26,

  // Hotspots: polygons in PAINTING pixel coords (room.png is 1216x832) —
  // they ride the cover-scaling automatically. Tuned by eye from the art.
  hotspots: {
    lamp:  [[1010,455],[1100,455],[1105,590],[1005,590]],
    bed:   [[390,460],[1000,440],[1010,760],[350,780]],
    plant: [[140,315],[355,315],[350,665],[150,665]],
    sky:   [[10,10],[880,10],[880,330],[10,330]],
  },

  // Added sprite props: image + painting-space anchor + angle/skew for the
  // wall perspective. Missing images skip silently (art comes later).
  props: [
    { name: "aircon", img: "assets/prop_ac.png",
      x: 985, y: 150, w: 200, rotation: -0.02, skewY: -0.045,
      behavior: "aircon" },
  ],

  // Window GLASS region (painting coords) — sky effects clip to this, so
  // shooting stars streak OUTSIDE the frame, never over the room.
  glass: [[18,14],[872,14],[872,610],[600,640],[330,660],[18,700]],

  // Localized lighting (v3, owner: "not her whole room turns darker — THAT
  // area"): when a light is off, darkness falls EXCEPT inside the remaining
  // lights' circles. Painting coords + radii.
  lights: {
    ambientDark: 0.62,   // how dark un-lit areas get when lamp is off
    lamp:   { x: 1053, y: 468, r: 300, warm: true, toggleable: true },
    window: { x: 430,  y: 330, r: 560 },   // city/moon glow — always on
    shelf:  { x: 1180, y: 360, r: 190 },   // bookshelf lamp — always on
  },

  mousefx: { color: 0xd94f8e, trailLife: 0.85 },  // wine-magenta, her accent
};

window._boot='pre-init';
const app = new PIXI.Application();
await app.init({ backgroundAlpha: 0, resizeTo: window, antialias: false });
document.body.appendChild(app.canvas);

// Order matters: sky fx clip to the window glass and hide behind her;
// lighting darkens everything under it; click fx glow above the dark.
const layers = {};
for (const name of ["room", "sky", "props", "content", "puppet", "lighting", "fx", "fg", "ui"]) {
  layers[name] = new PIXI.Container();
  app.stage.addChild(layers[name]);
}
const W = () => app.screen.width, H = () => app.screen.height;

/* ── room + depth parallax ─────────────────────────────────────────── */

window._boot='room';
const roomTex = await PIXI.Assets.load(CFG.room);
const room = new PIXI.Sprite(roomTex);
room.anchor.set(0.5);
layers.room.addChild(room);

let depthSprite = null;
try {
  const depthTex = await PIXI.Assets.load(CFG.depth);
  depthSprite = new PIXI.Sprite(depthTex);
  depthSprite.anchor.set(0.5);
  depthSprite.renderable = false;
  layers.room.addChild(depthSprite);
  const disp = new PIXI.DisplacementFilter({ sprite: depthSprite, scale: 0 });
  room.filters = [disp];
  room._disp = disp;
} catch { console.warn("no depth map — room is static"); }

function roomScale() {
  return Math.max(W() / roomTex.width, H() / roomTex.height) * 1.04;
}
function layoutRoom() {
  const s = roomScale();
  room.scale.set(s); room.position.set(W() / 2, H() / 2);
  if (depthSprite) { depthSprite.scale.set(s); depthSprite.position.set(W() / 2, H() / 2); }
}
// painting pixel coords -> stage coords (rides cover-scale + parallax home pos)
function paintToStage(px, py) {
  const s = roomScale();
  return { x: W() / 2 + (px - roomTex.width / 2) * s,
           y: H() / 2 + (py - roomTex.height / 2) * s };
}
function stageToPaint(sx, sy) {
  const s = roomScale();
  return { x: (sx - W() / 2) / s + roomTex.width / 2,
           y: (sy - H() / 2) / s + roomTex.height / 2 };
}

/* ── added sprite props (angle/skew placement for the wall) ────────── */

window._boot='props';
const propSprites = [];
for (const p of CFG.props) {
  try {
    const tex = await PIXI.Assets.load(p.img);
    const sp = new PIXI.Sprite(tex);
    sp.anchor.set(0.5);
    sp.rotation = p.rotation || 0;
    sp.skew.set(p.skewX || 0, p.skewY || 0);
    sp._cfg = p;
    layers.props.addChild(sp);
    propSprites.push(sp);
    console.log(`prop loaded: ${p.name}`);
  } catch { console.log(`prop art not present yet: ${p.name} (${p.img})`); }
}
function layoutProps() {
  const s = roomScale();
  for (const sp of propSprites) {
    const c = sp._cfg;
    const pos = paintToStage(c.x, c.y);
    sp.position.set(pos.x, pos.y);
    sp.scale.set((c.w * s) / sp.texture.width);
  }
}

/* ── diegetic screen (WHEP) ────────────────────────────────────────── */

window._boot='pane';
const pane = new PIXI.Container();
layers.content.addChild(pane);
const paneBg = new PIXI.Graphics();
pane.addChild(paneBg);
let paneVideo = null;
const offAir = new PIXI.Text({ text: "· off air ·",
  style: { fontFamily: "monospace", fontSize: 18, fill: 0x8a7a8a } });
offAir.anchor.set(0.5);
pane.addChild(offAir);

function layoutPane() {
  const x = CFG.screen.x * W(), y = CFG.screen.y * H();
  const w = CFG.screen.w * W(), h = CFG.screen.h * H();
  pane.position.set(x, y);
  paneBg.clear().roundRect(0, 0, w, h, 6).fill({ color: 0x0b0812, alpha: 0.92 })
        .stroke({ color: 0x2b2135, width: 2 });
  offAir.position.set(w / 2, h / 2);
  if (paneVideo) { paneVideo.width = w - 8; paneVideo.height = h - 8; paneVideo.position.set(4, 4); }
}

async function tryWhep() {
  try {
    const pc = new RTCPeerConnection();
    pc.addTransceiver("video", { direction: "recvonly" });
    pc.addTransceiver("audio", { direction: "recvonly" });
    const video = document.createElement("video");
    video.muted = true; video.autoplay = true; video.playsInline = true;
    pc.ontrack = (ev) => { video.srcObject = ev.streams[0]; };
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    const resp = await fetch(CFG.screen.whep, {
      method: "POST", headers: { "Content-Type": "application/sdp" }, body: offer.sdp });
    if (!resp.ok) throw new Error(`whep ${resp.status}`);
    await pc.setRemoteDescription({ type: "answer", sdp: await resp.text() });
    await new Promise((res, rej) => { video.onloadeddata = res; setTimeout(rej, 8000); });
    paneVideo = new PIXI.Sprite(PIXI.Texture.from(video));
    pane.addChild(paneVideo);
    offAir.visible = false;
    layoutPane();
    console.log("diegetic screen: live via WHEP");
  } catch (e) {
    setTimeout(tryWhep, 15000);
  }
}
tryWhep();

/* ── her standee ───────────────────────────────────────────────────── */

window._boot='puppet-tex';
const pupTex = {
  idle: await PIXI.Assets.load(CFG.puppet.idle),
  happy: await PIXI.Assets.load(CFG.puppet.happy),
  smug: await PIXI.Assets.load(CFG.puppet.smug),
};
const puppet = new PIXI.Sprite(pupTex.idle);
puppet.anchor.set(0.5, 1.0);
puppet.visible = CFG.puppet.show !== false;
layers.puppet.addChild(puppet);

window._boot='hitcanvas';
const hitCanvas = document.createElement("canvas");
const hitCtx = hitCanvas.getContext("2d", { willReadFrequently: true });
try {
  // createImageBitmap over Image.decode(): decode() silently rejected on the
  // rembg-cut PNG and a top-level await death kills the whole module (live
  // 2026-07-08). Fail-soft: without the map she still works, hits are boxy.
  const resp = await fetch(CFG.puppet.idle);
  const bmp = await createImageBitmap(await resp.blob());
  hitCanvas.width = bmp.width; hitCanvas.height = bmp.height;
  hitCtx.drawImage(bmp, 0, 0);
} catch (e) {
  console.warn("hit canvas failed — puppet hits fall back to bounds", e);
}
function puppetAlphaAt(gx, gy) {
  const local = puppet.toLocal({ x: gx, y: gy });
  const tx = Math.floor(local.x + puppet.texture.width * puppet.anchor.x);
  const ty = Math.floor(local.y + puppet.texture.height * puppet.anchor.y);
  if (tx < 0 || ty < 0 || tx >= hitCanvas.width || ty >= hitCanvas.height) return 0;
  return hitCtx.getImageData(tx, ty, 1, 1).data[3];
}
function layoutPuppet() {
  const s = (CFG.puppet.height * H()) / pupTex.idle.height;
  puppet.scale.set(s);
  puppet.position.set(CFG.puppet.xAnchor * W(), H() + 4);
}

/* ── localized lighting (v3) ───────────────────────────────────────── */
/* A darkness sheet with ERASE-blend radial holes at each live light. The
 * AlphaFilter flattens the container into its own render group so erase
 * only cuts the darkness, not the room behind it. Lamp ON = darkness sheet
 * fully transparent (painting is authoritative). Lamp OFF = sheet fades in,
 * window/shelf keep their glow circles — only the lamp's corner goes dark. */

function radialTexture(radius, inner = 0.15) {
  const c = document.createElement("canvas");
  c.width = c.height = radius * 2;
  const ctx = c.getContext("2d");
  const g = ctx.createRadialGradient(radius, radius, radius * inner, radius, radius, radius);
  g.addColorStop(0, "rgba(255,255,255,1)");
  g.addColorStop(0.55, "rgba(255,255,255,0.65)");
  g.addColorStop(1, "rgba(255,255,255,0)");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, radius * 2, radius * 2);
  return PIXI.Texture.from(c);
}

const darkness = new PIXI.Container();
darkness.filters = [new PIXI.AlphaFilter({ alpha: 1 })]; // flatten -> erase stays local
darkness.alpha = 0;                                       // lamp on by default
layers.lighting.addChild(darkness);
const darkSheet = new PIXI.Graphics();
darkness.addChild(darkSheet);
const lightHoles = {};
for (const [name, L] of Object.entries(CFG.lights)) {
  if (name === "ambientDark") continue;
  const hole = new PIXI.Sprite(radialTexture(256));
  hole.anchor.set(0.5);
  hole.blendMode = "erase";
  hole._cfg = L;
  darkness.addChild(hole);
  lightHoles[name] = hole;
}

function layoutLighting() {
  darkSheet.clear().rect(0, 0, W(), H()).fill({ color: 0x060410, alpha: CFG.lights.ambientDark });
  const s = roomScale();
  for (const hole of Object.values(lightHoles)) {
    const pos = paintToStage(hole._cfg.x, hole._cfg.y);
    hole.position.set(pos.x, pos.y);
    hole.width = hole.height = hole._cfg.r * 2 * s;
  }
}

let lightsOn = true;
let darkTarget = 0;
function toggleLights() {
  lightsOn = !lightsOn;
  lightHoles.lamp.visible = lightsOn;   // lamp's glow circle dies with it
  darkTarget = lightsOn ? 0 : 1;        // sheet fades in only when lamp is off
  console.log(lightsOn ? "lamp on" : "lamp off — corner goes dark");
}

/* ── universal mouse fx (Blue Archive-style, v3) ───────────────────── */

/* Click fx redesign (owner, 2026-07-08 — Blue Archive reference): a sweeping
 * partial ARC + outward triangle accents + a core flash + tiny stars, and her
 * signature — a little hairpin heart that floats up. All in her wine-magenta. */

const sparkles = [];   // {g, kind, life, ...per-kind state}

function heartPath(g, s, color, alpha) {
  // small bezier heart, size s
  g.moveTo(0, s * 0.35)
    .bezierCurveTo(-s, -s * 0.45, -s * 0.42, -s * 1.05, 0, -s * 0.35)
    .bezierCurveTo(s * 0.42, -s * 1.05, s, -s * 0.45, 0, s * 0.35)
    .fill({ color, alpha });
}

function clickSparkle(x, y) {
  const C = CFG.mousefx.color, SOFT = 0xffe9f4;

  // core flash — first impression, dies fast
  const flash = new PIXI.Graphics().circle(0, 0, 5).fill({ color: 0xffffff, alpha: 0.9 });
  flash.position.set(x, y);
  sparkles.push({ g: flash, kind: "flash", life: 1 });
  layers.fx.addChild(flash);

  // sweeping arc — grows, sweep shortens, rotates (the BA move)
  const arc = new PIXI.Graphics();
  arc.position.set(x, y);
  arc.rotation = Math.random() * Math.PI * 2;
  sparkles.push({ g: arc, kind: "arc", life: 1, r: 7 });
  layers.fx.addChild(arc);

  // triangle accents — three, pointed outward, staggered angles
  const base = Math.random() * Math.PI * 2;
  for (let i = 0; i < 3; i++) {
    const tri = new PIXI.Graphics().poly([0, -4.6, 3.6, 3.4, -3.6, 3.4]).fill({ color: C, alpha: 0.95 });
    const a = base + i * (Math.PI * 2 / 3) + (Math.random() - 0.5) * 0.5;
    tri.position.set(x + Math.cos(a) * 9, y + Math.sin(a) * 9);
    tri.rotation = a + Math.PI / 2;
    sparkles.push({ g: tri, kind: "tri", life: 1, vx: Math.cos(a) * 2.1, vy: Math.sin(a) * 2.1 });
    layers.fx.addChild(tri);
  }

  // tiny stars — just a few, soft
  for (let i = 0; i < 3; i++) {
    const st = new PIXI.Graphics().star(0, 0, 4, 2.8, 1.1).fill({ color: SOFT, alpha: 0.9 });
    st.position.set(x, y);
    const a = Math.random() * Math.PI * 2, v = 0.9 + Math.random() * 1.4;
    sparkles.push({ g: st, kind: "star", life: 1, vx: Math.cos(a) * v, vy: Math.sin(a) * v,
                    spin: (Math.random() - 0.5) * 0.3 });
    layers.fx.addChild(st);
  }

  // her hairpin heart — one, floats up, occasionally the black one
  const heart = new PIXI.Graphics();
  heartPath(heart, 5, Math.random() < 0.15 ? 0x2b2130 : 0xe0314b, 0.95);
  heart.position.set(x + (Math.random() - 0.5) * 10, y - 6);
  heart.scale.set(0.7);
  sparkles.push({ g: heart, kind: "heart", life: 1, wob: Math.random() * Math.PI * 2 });
  layers.fx.addChild(heart);
}

const trail = [];      // {x, y, t}
const trailG = new PIXI.Graphics();
layers.fx.addChild(trailG);
let dragging = false;

/* ── effects toolbox ───────────────────────────────────────────────── */

window._boot='effects';
const bursts = [];   // {g, vx, vy, life, decay, gravity}
function burst(x, y, { n = 8, color = 0xf5e9d5, size = 2.4, speed = 1.6,
                       gravity = 0.05, life = 1.0 } = {}) {
  for (let i = 0; i < n; i++) {
    const g = new PIXI.Graphics().circle(0, 0, size * (0.5 + Math.random()))
      .fill({ color, alpha: 0.85 });
    g.position.set(x, y);
    const a = Math.random() * Math.PI * 2, v = speed * (0.4 + Math.random());
    bursts.push({ g, vx: Math.cos(a) * v, vy: Math.sin(a) * v - speed * 0.6,
                  life, decay: 0.018 + Math.random() * 0.02, gravity });
    layers.fx.addChild(g);
  }
}

const stars = [];    // shooting stars: {g, x, y, vx, vy, life}
function shootingStar(x, y) {
  const g = new PIXI.Graphics();
  stars.push({ g, x, y, vx: 3.2 + Math.random() * 2, vy: 1.1 + Math.random(), life: 1 });
  layers.sky.addChild(g);  // sky layer: masked to the glass, renders behind her
}

// clip the sky layer to the window glass so stars streak OUTSIDE, and vanish
// behind frames/her instead of drawing over the room
const skyMaskG = new PIXI.Graphics();
app.stage.addChild(skyMaskG);
skyMaskG.renderable = false;
layers.sky.mask = skyMaskG;
function layoutSkyMask() {
  const pts = CFG.glass.flatMap(([x, y]) => { const p = paintToStage(x, y); return [p.x, p.y]; });
  skyMaskG.clear().poly(pts).fill(0xffffff);
}

// pixi-filters (vendored, MIT): shaders that live INSIDE our compositor.
// First tasteful use — soft glow on sky effects so stars bloom against the
// night. Defensive: stage runs fine if the lib is ever absent.
try {
  if (PIXI.filters?.GlowFilter) {
    layers.sky.filters = [new PIXI.filters.GlowFilter({
      distance: 8, outerStrength: 1.6, color: 0xfff3d0, quality: 0.2 })];
  }
} catch (e) { console.warn("pixi-filters glow skipped:", e); }

/* aircon behavior: click toggles a cool breeze (drifting wisps from the unit) */
let airconOn = false;
function toggleAircon(sp) {
  airconOn = !airconOn;
  sp._breeze = airconOn;
  console.log(airconOn ? "aircon on" : "aircon off");
}

/* ── input: puppet petting > props > painted hotspots ──────────────── */

function pointInPoly(px, py, poly) {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i], [xj, yj] = poly[j];
    if ((yi > py) !== (yj > py) && px < ((xj - xi) * (py - yi)) / (yj - yi) + xi)
      inside = !inside;
  }
  return inside;
}

let expressionTimer = null;
window.addEventListener("pointerdown", (ev) => {
  const gx = ev.clientX, gy = ev.clientY;

  // universal click fx + drag-trail arming (Blue Archive grammar) — always
  clickSparkle(gx, gy);
  dragging = true;
  trail.push({ x: gx, y: gy, t: performance.now() });

  // 1) her silhouette wins (only while she's on stage)
  if (puppet.visible && puppetAlphaAt(gx, gy) > 40) {
    const spam = expressionTimer !== null;
    puppet.texture = spam ? pupTex.smug : pupTex.happy;
    clearTimeout(expressionTimer);
    expressionTimer = setTimeout(() => { puppet.texture = pupTex.idle; expressionTimer = null; }, 1400);
    return;
  }
  // 2) added props
  for (const sp of propSprites) {
    if (sp.getBounds().containsPoint(gx, gy)) {
      if (sp._cfg.behavior === "aircon") toggleAircon(sp);
      return;
    }
  }
  // 3) painted furniture hotspots
  const p = stageToPaint(gx, gy);
  if (pointInPoly(p.x, p.y, CFG.hotspots.lamp)) { toggleLights(); return; }
  if (pointInPoly(p.x, p.y, CFG.hotspots.bed)) {
    burst(gx, gy, { n: 10, color: 0xf5e9d5, speed: 1.8 });   // poomf
    if (room._disp) room._breathe = 2.2;                      // blanket shiver
    return;
  }
  if (pointInPoly(p.x, p.y, CFG.hotspots.plant)) {
    burst(gx, gy, { n: 7, color: 0x7fae6a, size: 2, speed: 1.1, gravity: 0.10 });
    return;
  }
  if (pointInPoly(p.x, p.y, CFG.hotspots.sky)) { shootingStar(gx, gy); return; }
});

// hover cursor feedback + drag trail collection
window.addEventListener("pointermove", (ev) => {
  mouseX = ev.clientX / W(); mouseY = ev.clientY / H();
  if (dragging) trail.push({ x: ev.clientX, y: ev.clientY, t: performance.now() });
  const p = stageToPaint(ev.clientX, ev.clientY);
  const hot = puppetAlphaAt(ev.clientX, ev.clientY) > 40
    || Object.values(CFG.hotspots).some(poly => pointInPoly(p.x, p.y, poly))
    || propSprites.some(sp => sp.getBounds().containsPoint(ev.clientX, ev.clientY));
  document.body.style.cursor = hot ? "pointer" : "default";
});
window.addEventListener("pointerup", () => { dragging = false; });

/* ── dust ──────────────────────────────────────────────────────────── */

window._boot='dust';
const motes = [];
for (let i = 0; i < CFG.dust; i++) {
  const alpha = 0.05 + Math.random() * 0.12;
  const m = new PIXI.Graphics().circle(0, 0, 1 + Math.random() * 1.6)
    .fill({ color: 0xfff2d8, alpha });
  m._baseAlpha = alpha;
  m._vx = (Math.random() - 0.5) * 0.12;
  m._vy = -0.05 - Math.random() * 0.12;
  m._x = Math.random(); m._y = Math.random();
  layers.fg.addChild(m);
  motes.push(m);
}

/* ── ui ────────────────────────────────────────────────────────────── */

const fps = new PIXI.Text({ text: "", style: { fontFamily: "monospace", fontSize: 13, fill: 0x66ff99 } });
fps.position.set(8, 6); fps.visible = false;
layers.ui.addChild(fps);
window.addEventListener("keydown", (e) => {
  const k = e.key.toLowerCase();
  if (k === "f") fps.visible = !fps.visible;
  if (k === "k") puppet.visible = !puppet.visible;   // peek at her / hide her
});

/* ── the living loop ───────────────────────────────────────────────── */

let mouseX = 0.5, mouseY = 0.5, t = 0, nextAmbientStar = 20 + Math.random() * 60;

window._boot='ticker';
app.ticker.add((ticker) => {
 try {
  const dt = ticker.deltaMS / 16.6;
  t += ticker.deltaMS / 1000;
  window._tick = { n: (window._tick?.n || 0) + 1, t: +t.toFixed(2), dt: +dt.toFixed(2),
                   dark: +darkness.alpha.toFixed(3), target: darkTarget };

  if (room._disp) {
    const kick = room._breathe || 0;
    if (kick > 0) room._breathe = Math.max(0, kick - 0.03 * dt);
    const dx = (mouseX - 0.5) * 2 * CFG.parallax.mouse + Math.sin(t * 0.23) * (CFG.parallax.drift + kick * 4);
    const dy = (mouseY - 0.5) * 2 * (CFG.parallax.mouse * 0.55) + Math.cos(t * 0.19) * (CFG.parallax.drift * 0.6 + kick * 3);
    room._disp.scale.x = dx; room._disp.scale.y = dy;
  }

  const breath = Math.sin(t * (Math.PI * 2 / 3.8));
  puppet.scale.y = puppet.scale.x * (1 + breath * 0.006);
  puppet.y = H() + 4 + breath * 1.5;

  for (const m of motes) {
    m._x += m._vx * dt / 60; m._y += m._vy * dt / 60;
    if (m._y < -0.02) { m._y = 1.02; m._x = Math.random(); }
    if (m._x < -0.02) m._x = 1.02; if (m._x > 1.02) m._x = -0.02;
    m.position.set(m._x * W(), m._y * H());
  }

  // particle bursts
  for (let i = bursts.length - 1; i >= 0; i--) {
    const b = bursts[i];
    b.g.x += b.vx * dt; b.g.y += b.vy * dt; b.vy += b.gravity * dt;
    b.life -= b.decay * dt; b.g.alpha = Math.max(0, b.life);
    if (b.life <= 0) { b.g.destroy(); bursts.splice(i, 1); }
  }

  // shooting stars (ambient + clicked)
  if (t > nextAmbientStar) {
    nextAmbientStar = t + 35 + Math.random() * 70;
    const skyPos = paintToStage(80 + Math.random() * 600, 30 + Math.random() * 180);
    shootingStar(skyPos.x, skyPos.y);
  }
  for (let i = stars.length - 1; i >= 0; i--) {
    const s = stars[i];
    s.x += s.vx * dt; s.y += s.vy * dt; s.life -= 0.02 * dt;
    s.g.clear().moveTo(s.x - s.vx * 6, s.y - s.vy * 6).lineTo(s.x, s.y)
      .stroke({ color: 0xfff8e0, width: 1.6, alpha: Math.max(0, s.life) });
    if (s.life <= 0) { s.g.destroy(); stars.splice(i, 1); }
  }

  // localized darkness fades toward its target (lamp toggle)
  darkness.alpha += (darkTarget - darkness.alpha) * Math.min(1, 0.08 * dt);

  // click fx: per-kind choreography (flash < arc < tris < stars < heart)
  for (let i = sparkles.length - 1; i >= 0; i--) {
    const s = sparkles[i];
    switch (s.kind) {
      case "flash":
        s.life -= 0.14 * dt;
        s.g.scale.set(s.g.scale.x + 0.22 * dt);
        s.g.alpha = Math.max(0, s.life) * 0.9;
        break;
      case "arc": {
        s.life -= 0.05 * dt;
        s.r += 1.1 * dt;
        s.g.rotation += 0.10 * dt;
        const sweep = (Math.PI * 1.6) * Math.max(0.25, s.life); // shrinking sweep
        s.g.clear()
          .arc(0, 0, s.r, 0, sweep).stroke({ color: CFG.mousefx.color, width: 1 + 1.6 * s.life, alpha: s.life })
          .arc(0, 0, s.r * 0.72, 0.6, 0.6 + sweep * 0.5)
          .stroke({ color: 0xffe9f4, width: 1, alpha: s.life * 0.7 });
        break;
      }
      case "tri":
        s.life -= 0.05 * dt;
        s.g.x += s.vx * dt; s.g.y += s.vy * dt;
        s.vx *= 0.94; s.vy *= 0.94;
        s.g.alpha = Math.max(0, s.life);
        break;
      case "star":
        s.life -= 0.045 * dt;
        s.g.x += s.vx * dt; s.g.y += s.vy * dt; s.g.rotation += s.spin * dt;
        s.g.alpha = Math.max(0, s.life);
        break;
      case "heart":
        s.life -= 0.028 * dt;
        s.wob += 0.11 * dt;
        s.g.y -= 0.55 * dt;
        s.g.x += Math.sin(s.wob) * 0.25 * dt;
        s.g.scale.set(Math.min(1.05, s.g.scale.x + 0.02 * dt));
        s.g.alpha = Math.max(0, s.life);
        break;
    }
    if (s.life <= 0) { s.g.destroy(); sparkles.splice(i, 1); }
  }

  // drag trail: lingering fading line (her wine-magenta)
  {
    const now = performance.now(), lifeMs = CFG.mousefx.trailLife * 1000;
    while (trail.length && now - trail[0].t > lifeMs) trail.shift();
    trailG.clear();
    for (let i = 1; i < trail.length; i++) {
      const a = 1 - (now - trail[i].t) / lifeMs;
      trailG.moveTo(trail[i - 1].x, trail[i - 1].y).lineTo(trail[i].x, trail[i].y)
        .stroke({ color: CFG.mousefx.color, width: 1 + a * 2.2, alpha: a * 0.8 });
    }
  }

  // aircon breeze wisps
  if (airconOn && Math.random() < 0.15) {
    const src = propSprites.find(sp => sp._cfg.behavior === "aircon");
    if (src) burst(src.x + (Math.random() - 0.5) * src.width * 0.6, src.y + src.height * 0.55,
                   { n: 1, color: 0xcfe8ff, size: 1.6, speed: 0.5, gravity: -0.005, life: 0.8 });
  }

  if (fps.visible) fps.text = `${Math.round(ticker.FPS)} fps`;
 } catch (e) {
  if (!window._tickErr) { window._tickErr = String(e && e.stack || e); console.error("ticker died:", e); }
 }
});

function layout() {
  layoutRoom(); layoutProps(); layoutPane(); layoutPuppet();
  layoutLighting(); layoutSkyMask();
}
layout();
window.addEventListener("resize", layout);

window._stage = { app, puppet, puppetAlphaAt, pupTex, layers, toggleLights,
                  stageToPaint, paintToStage, CFG };
console.log("Koroki stage v2 up — click: her / lamp / bed / plant / sky. F = fps.");
