// Koroki puppet trial — canonical base + ALIGNED expression swap, composited on a room.
// Each expression sprite is the same body with only the face changed (face-inpaint), so swapping
// reads as pure emotion change. Koroki is a PIXI.Container (body/face layer today; effects later).
const PIXI = window.PIXI;
// Both live under the orchestrator's /assets mount (repo-root assets/). The web-local
// clients/web/assets/ dir is shadowed by that mount — do not put art there.
const SPRITES = "/assets/koroki_sprites";
const ROOMS = "/assets/world";

// canonical Koroki, one consistent size. Expression -> full sprite (identical body, swapped face).
// Poses: each pose is its own aligned expression set, sharing the same expression names.
const EXPR = ["neutral", "happy", "smug", "sleepy", "surprised", "pout"];
const POSES = { sit: "koroki", stand: "stand" };   // pose name -> sprite filename prefix
const HEIGHT_FRAC = 0.62;   // her on-screen height (consistent across expressions)
const FOOT_Y = 0.99;        // her bottom sits near the floor edge
const ROOMLIST = { "lounge": "lounge_back.png", "studio": "studio_back.png", "bedroom": "bedroom_back.png" };

const app = new PIXI.Application({ resizeTo: window, backgroundColor: 0x0b0a0d, antialias: true });
document.getElementById("stage").appendChild(app.view);

const roomSprite = new PIXI.Sprite(PIXI.Texture.EMPTY);
roomSprite.anchor.set(0.5);
app.stage.addChild(roomSprite);

// soft cosy vignette over the room (matches the world's grade)
const vignette = new PIXI.Graphics();
app.stage.addChild(vignette);

const koroki = new PIXI.Container();
const body = new PIXI.Sprite(PIXI.Texture.EMPTY);
body.anchor.set(0.5, 1.0);
koroki.addChild(body);
app.stage.addChild(koroki);

let curExpr = "neutral", curRoom = "lounge", curPose = "sit";
const texCache = {};
const tex = (p) => (texCache[p] || (texCache[p] = PIXI.Texture.from(p)));

function layoutRoom() {
  const t = roomSprite.texture; if (!t || !t.valid) return;
  const s = Math.max(app.screen.width / t.width, app.screen.height / t.height);
  roomSprite.scale.set(s);
  roomSprite.position.set(app.screen.width / 2, app.screen.height / 2);
  drawVignette();
}
function drawVignette() {
  const w = app.screen.width, h = app.screen.height;
  vignette.clear();
  vignette.beginFill(0x000000, 0.0);
  // simple darkened edges via stacked rectangles (cheap vignette)
  const pad = Math.min(w, h) * 0.16;
  for (let i = 0; i < 6; i++) {
    const a = 0.05 * (i + 1);
    vignette.beginFill(0x0a0810, a);
    vignette.drawRect(0, 0, w, pad * (1 - i / 6));
    vignette.drawRect(0, h - pad * (1 - i / 6), w, pad);
    vignette.drawRect(0, 0, pad * (1 - i / 6), h);
    vignette.drawRect(w - pad * (1 - i / 6), 0, pad, h);
    vignette.endFill();
  }
}
function layoutKoroki() {
  const t = body.texture; if (!t || !t.valid) return;
  const targetH = app.screen.height * HEIGHT_FRAC;
  body.scale.set(targetH / t.height);
  koroki.position.set(app.screen.width / 2, app.screen.height * FOOT_Y);
}
function setExpr(name) {
  curExpr = name; const t = tex(`${SPRITES}/${POSES[curPose]}_${name}.png`);
  body.texture = t;
  t.valid ? layoutKoroki() : t.once("update", layoutKoroki);
  markActive("poseRow", name);
}
function setPose(name) {
  curPose = name;
  setExpr(curExpr);           // reload current expression in the new pose
  markActive("poseSetRow", name);
}
function setRoom(name) {
  curRoom = name; const t = tex(`${ROOMS}/${ROOMLIST[name]}`);
  roomSprite.texture = t;
  t.valid ? layoutRoom() : t.once("update", layoutRoom);
  markActive("roomRow", name);
}

// breathing idle
let tms = 0;
app.ticker.add((dt) => {
  tms += dt / 60;
  const breathe = Math.sin(tms * 1.4) * 0.006;
  const bob = Math.sin(tms * 1.4) * 2.0;
  koroki.scale.set(1, 1 + breathe);
  koroki.y = app.screen.height * FOOT_Y + bob;
});

function btnRow(rowId, items, onClick) {
  const row = document.getElementById(rowId); row.innerHTML = "";
  items.forEach((name) => {
    const b = document.createElement("button"); b.textContent = name; b.dataset.name = name;
    b.onclick = () => onClick(name); row.appendChild(b);
  });
}
const markActive = (rowId, name) =>
  document.querySelectorAll(`#${rowId} button`).forEach((b) => b.classList.toggle("active", b.dataset.name === name));

btnRow("poseRow", EXPR, setExpr);
btnRow("poseSetRow", Object.keys(POSES), setPose);
btnRow("roomRow", Object.keys(ROOMLIST), setRoom);
markActive("poseSetRow", curPose);
// PIXI's resizeTo applies asynchronously — listen to the renderer, not the window,
// so the first deferred resize (and any later one) re-runs layout with real dimensions.
app.renderer.on("resize", () => { layoutRoom(); layoutKoroki(); });
setRoom("lounge");
setExpr("neutral");
window.koroki = { setExpr, setRoom, setPose };
