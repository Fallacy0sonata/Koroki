// Koroki layered room scene — data-driven engine.
// Design law (owner, 2026-07-02): MANY layers, each with tiny DESYNCED micro-motion —
// random phase + ±15% period jitter per instance, so nothing ever beats in unison.
// Room = JSON config (layers, z, motions, spots). Room #2 onward is config + art, no code.
const PIXI = window.PIXI;
const SPRITES = "/assets/koroki_sprites";
const POSE_PREFIX = { sit: "koroki", stand: "stand" };
const EXPR = ["neutral", "happy", "smug", "sleepy", "surprised", "pout"];

const app = new PIXI.Application({ resizeTo: window, backgroundColor: 0x07060a, antialias: true });
document.getElementById("stage").appendChild(app.view);

const $ = (id) => document.getElementById(id);
const rand = (a, b) => a + Math.random() * (b - a);

// ── motion runtime ───────────────────────────────────────────────────────────
// Every motion gets its own randomized phase and jittered period at load time.
function instantiateMotions(motions) {
  return (motions || []).map((m) => ({
    ...m,
    period: (m.period || 10) * rand(0.85, 1.15),
    phase: rand(0, Math.PI * 2),
    _fl: { target: 1, cur: 1, next: 0 },   // flicker state
  }));
}
function applyMotions(node, t, dt) {
  let ox = 0, oy = 0, rot = 0, alphaMul = 1;
  for (const m of node.__motions) {
    const w = (t * Math.PI * 2) / m.period + m.phase;
    switch (m.type) {
      case "swayX": case "drift": ox += m.amp * Math.sin(w); break;
      case "swayY": oy += m.amp * Math.sin(w); break;
      case "rock": rot += (m.amp * Math.PI / 180) * Math.sin(w); break;
      case "tremble": rot += (m.amp * Math.PI / 180) * Math.sin(w * 3.1) * 0.6
                          + (m.amp * Math.PI / 180) * Math.sin(w * 7.3) * 0.4; break;
      case "pulse": alphaMul *= 1 + m.amp * Math.sin(w); break;
      case "flicker": {
        const f = m._fl;
        f.next -= dt;
        if (f.next <= 0) { f.next = rand(0.6, 3.2); f.target = rand(1 - m.amp, 1); }
        f.cur += (f.target - f.cur) * (1 - Math.pow(0.02, dt));
        alphaMul *= f.cur;
        break;
      }
    }
  }
  node.position.set(node.__base.x + ox + node.__par.x, node.__base.y + oy + node.__par.y);
  node.rotation = node.__baseRot + rot;
  node.alpha = node.__baseAlpha * alphaMul;
}

// ── textures ─────────────────────────────────────────────────────────────────
function glowTexture() {
  const s = 256, cv = document.createElement("canvas"); cv.width = cv.height = s;
  const ctx = cv.getContext("2d");
  const g = ctx.createRadialGradient(s / 2, s / 2, 0, s / 2, s / 2, s / 2);
  g.addColorStop(0, "rgba(255,255,255,1)"); g.addColorStop(0.35, "rgba(255,255,255,0.45)");
  g.addColorStop(1, "rgba(255,255,255,0)");
  ctx.fillStyle = g; ctx.fillRect(0, 0, s, s);
  return PIXI.Texture.from(cv);
}
function vignetteTexture(w, h) {
  const cv = document.createElement("canvas"); cv.width = w; cv.height = h;
  const ctx = cv.getContext("2d");
  const g = ctx.createRadialGradient(w / 2, h / 2, Math.min(w, h) * 0.3, w / 2, h / 2, Math.max(w, h) * 0.74);
  g.addColorStop(0, "rgba(255,255,255,1)"); g.addColorStop(1, "rgba(26,26,40,1)");
  ctx.fillStyle = g; ctx.fillRect(0, 0, w, h);
  return PIXI.Texture.from(cv);
}
function noiseTexture(s = 200) {
  const cv = document.createElement("canvas"); cv.width = cv.height = s;
  const ctx = cv.getContext("2d"); const img = ctx.createImageData(s, s);
  for (let i = 0; i < img.data.length; i += 4) {
    const v = Math.random() * 255;
    img.data[i] = img.data[i + 1] = img.data[i + 2] = v; img.data[i + 3] = 255;
  }
  ctx.putImageData(img, 0, 0); return PIXI.Texture.from(cv);
}

// ── scene build ──────────────────────────────────────────────────────────────
let root, cfg, animated = [], koroki, body, curSpot = null, curExpr = "neutral";
const pointer = { x: 0, y: 0 };

async function build() {
  cfg = await (await fetch("./rooms/bedroom.json")).json();
  const [DW, DH] = cfg.design;
  root = new PIXI.Container();
  app.stage.addChild(root);
  const skyTint = parseInt(cfg.skyTint || "0xffffff", 16);
  const glowTex = glowTexture();

  const byId = {};
  for (const L of cfg.layers) {
    let node;
    if (L.glow) {
      node = new PIXI.Sprite(glowTex);
      node.anchor.set(0.5);
      node.blendMode = PIXI.BLEND_MODES.ADD;
    } else {
      node = PIXI.Sprite.from(L.src);
      node.anchor.set(0.5, L.anchorY != null ? L.anchorY : 0.5);
      if (L.z < 1) node.tint = skyTint;   // glass dimming for everything behind the panes
    }
    if (L.tint) node.tint = parseInt(L.tint, 16);
    node.scale.set(L.scale || 1);
    node.zIndex = L.z;
    node.__base = { x: L.pos[0], y: L.pos[1] };
    node.__baseRot = 0;
    node.__baseAlpha = L.alpha != null ? L.alpha : 1;
    node.__par = { x: 0, y: 0 };
    node.__parF = L.par || 0;
    node.__motions = instantiateMotions(L.motions);
    root.addChild(node);
    byId[L.id] = node;
    animated.push(node);
  }

  // city lights: tiny ADD dots, children of the sky (they drift with the skyline),
  // each flickering on its own clock
  if (cfg.cityLights && byId[cfg.cityLights.parent]) {
    const sky = byId[cfg.cityLights.parent];
    for (const [x, y] of cfg.cityLights.dots) {
      const d = new PIXI.Sprite(glowTex);
      d.anchor.set(0.5);
      d.width = d.height = rand(7, 13);
      d.tint = Math.random() < 0.3 ? 0xffe9b8 : 0xffb45e;
      d.blendMode = PIXI.BLEND_MODES.ADD;
      // sky sprite is centered — convert texture coords to local
      d.position.set(x - sky.texture.width / 2, y - sky.texture.height / 2);
      d.__base = { x: d.x, y: d.y };
      d.__baseRot = 0; d.__baseAlpha = rand(0.5, 0.9); d.__par = { x: 0, y: 0 }; d.__parF = 0;
      d.__motions = instantiateMotions([{ type: "flicker", amp: rand(0.3, 0.7), period: 5 }]);
      sky.addChild(d);
      animated.push(d);
    }
  }

  // Koroki — pose-per-spot
  koroki = new PIXI.Container();
  body = new PIXI.Sprite(PIXI.Texture.EMPTY);
  body.anchor.set(0.5, 1.0);
  koroki.addChild(body);
  root.addChild(koroki);
  root.sortableChildren = true;

  // screen-space cosy grade
  const vg = new PIXI.Sprite(vignetteTexture(app.screen.width, app.screen.height));
  vg.blendMode = PIXI.BLEND_MODES.MULTIPLY;
  const grain = new PIXI.TilingSprite(noiseTexture(), app.screen.width, app.screen.height);
  grain.alpha = 0.04; grain.blendMode = PIXI.BLEND_MODES.ADD;
  app.stage.addChild(vg, grain);
  app.__fx = { vg, grain };

  layout();
  setSpot(cfg.spots[0].name);
  buildUI();
}

function layout() {
  const [DW, DH] = cfg.design;
  const s = Math.max(app.screen.width / DW, app.screen.height / DH);
  root.scale.set(s);
  root.position.set((app.screen.width - DW * s) / 2, (app.screen.height - DH * s) / 2);
  if (app.__fx) {
    app.__fx.vg.texture = vignetteTexture(app.screen.width, app.screen.height);
    app.__fx.vg.width = app.screen.width; app.__fx.vg.height = app.screen.height;
    app.__fx.grain.width = app.screen.width; app.__fx.grain.height = app.screen.height;
  }
}
app.renderer.on("resize", layout);

function setSpot(name) {
  const spot = cfg.spots.find((sp) => sp.name === name);
  if (!spot) return;
  curSpot = spot;
  koroki.position.set(spot.pos[0], spot.pos[1]);
  koroki.zIndex = spot.z != null ? spot.z : 3;
  setExpr(curExpr);
  markActive("spotRow", name);
}
function setExpr(name) {
  curExpr = name;
  const prefix = POSE_PREFIX[curSpot.pose] || "koroki";
  const t = PIXI.Texture.from(`${SPRITES}/${prefix}_${name}.png`);
  body.texture = t;
  const fit = () => { body.scale.set(curSpot.h / t.height); };
  t.valid ? fit() : t.once("update", fit);
  markActive("exprRow", name);
}

// ── idle + motion tick ───────────────────────────────────────────────────────
let t = 0;
app.ticker.add(() => {
  const dt = app.ticker.deltaMS / 1000; t += dt;
  for (const node of animated) {
    node.__par.x = pointer.x * node.__parF * 6;
    node.__par.y = pointer.y * node.__parF * 2.5;
    applyMotions(node, t, dt);
  }
  if (koroki && curSpot) {
    const breathe = Math.sin(t * 1.35) * 0.006;
    koroki.scale.set(1, 1 + breathe);
    koroki.rotation = Math.sin(t * 0.62) * 0.0022;
    koroki.y = curSpot.pos[1] + Math.sin(t * 1.35) * 1.6;
  }
  if (app.__fx) {
    app.__fx.grain.tilePosition.x = Math.random() * 40;
    app.__fx.grain.tilePosition.y = Math.random() * 40;
  }
});
window.addEventListener("pointermove", (e) => {
  pointer.x = (e.clientX / window.innerWidth - 0.5) * 2;
  pointer.y = (e.clientY / window.innerHeight - 0.5) * 2;
});

// ── UI ───────────────────────────────────────────────────────────────────────
function btnRow(rowId, items, onClick) {
  const row = $(rowId); row.innerHTML = "";
  items.forEach((name) => {
    const b = document.createElement("button");
    b.textContent = name; b.dataset.name = name;
    b.onclick = () => onClick(name); row.appendChild(b);
  });
}
const markActive = (rowId, name) =>
  document.querySelectorAll(`#${rowId} button`).forEach((b) => b.classList.toggle("active", b.dataset.name === name));
function buildUI() {
  btnRow("spotRow", cfg.spots.map((sp) => sp.name), setSpot);
  btnRow("exprRow", EXPR, setExpr);
  if (curSpot) markActive("spotRow", curSpot.name);
  markActive("exprRow", curExpr);
}

build().catch((e) => { console.error(e); const s = $("status"); if (s) s.textContent = "scene init failed: " + e.message; });
window.scene = { setSpot, setExpr, app: () => app };
