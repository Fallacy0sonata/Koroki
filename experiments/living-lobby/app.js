import * as PIXI from 'https://cdnjs.cloudflare.com/ajax/libs/pixi.js/7.3.2/pixi.mjs';

// Setup Application
const app = new PIXI.Application({
    resizeTo: window,
    backgroundColor: 0x111111,
    resolution: window.devicePixelRatio || 1,
    autoDensity: true,
});
document.body.appendChild(app.view);

// Assets paths
const ASSETS = {
    room: '../../clients/stage/assets/room.png',
    depth: '../../clients/stage/assets/room_depth.png',
    head: 'assets/koroki_head.png',
    torso: 'assets/koroki_torso.png',
    arm_l: 'assets/koroki_arm_l.png',
    arm_r: 'assets/koroki_arm_r.png',
    leg_l: 'assets/koroki_leg_l.png',
    leg_r: 'assets/koroki_leg_r.png'
};

const sceneContainer = new PIXI.Container();
app.stage.addChild(sceneContainer);

let roomSprite, depthSprite, displacementFilter, avatarContainer;
let puppetRig; 
let dustMotes = [];

// Hotspots
const HOTSPOTS = {
    bed: { x: 1200, y: 650, scale: 0.9 },
    desk: { x: 400, y: 700, scale: 0.8 },
    window: { x: 800, y: 550, scale: 0.75 }
};

let currentHotspot = 'desk';

// Load Assets
PIXI.Assets.load([
    ASSETS.room, ASSETS.depth, 
    ASSETS.head, ASSETS.torso, 
    ASSETS.arm_l, ASSETS.arm_r, 
    ASSETS.leg_l, ASSETS.leg_r
]).then(setupScene);

function createImageLimb(texture, scale = 0.5, anchorY = 0.1) {
    const container = new PIXI.Container();
    const sprite = new PIXI.Sprite(texture);
    sprite.anchor.set(0.5, anchorY); 
    sprite.scale.set(scale);
    container.addChild(sprite);
    return { container, sprite };
}

function buildPuppet(resources) {
    const rig = { root: new PIXI.Container() };

    // Hips (Root)
    rig.hips = new PIXI.Container();
    rig.hips.y = 50; 
    rig.root.addChild(rig.hips);

    // Left Leg (Behind torso for kneeling)
    const lLeg = createImageLimb(resources[ASSETS.leg_l], 0.35, 0.2);
    rig.legL = lLeg.container;
    rig.legL.position.set(-25, -20);
    rig.legL.rotation = -Math.PI / 4; // bent back for kneeling
    rig.hips.addChild(rig.legL);

    // Right Leg (Behind torso for kneeling)
    const rLeg = createImageLimb(resources[ASSETS.leg_r], 0.35, 0.2);
    rig.legR = rLeg.container;
    rig.legR.position.set(25, -20);
    rig.legR.rotation = Math.PI / 6; // bent differently
    rig.hips.addChild(rig.legR);

    // Torso
    const torso = createImageLimb(resources[ASSETS.torso], 0.4, 0.8);
    rig.torso = torso.container;
    rig.torso.y = 0; // anchor to hips
    rig.hips.addChild(rig.torso);

    // Head
    rig.neck = new PIXI.Container();
    rig.neck.y = -120; // top of torso
    rig.torso.addChild(rig.neck);

    const head = createImageLimb(resources[ASSETS.head], 0.35, 0.9);
    rig.headSprite = head.sprite;
    rig.neck.addChild(head.container);

    // Left Arm (In front of torso)
    const lArm = createImageLimb(resources[ASSETS.arm_l], 0.35, 0.1);
    rig.armL = lArm.container;
    rig.armL.position.set(-50, -100); // shoulder joint
    rig.torso.addChild(rig.armL);

    // Right Arm (In front of torso)
    const rArm = createImageLimb(resources[ASSETS.arm_r], 0.35, 0.1);
    rig.armR = rArm.container;
    rig.armR.position.set(50, -100); // shoulder joint
    rig.torso.addChild(rig.armR);

    return rig;
}

function setupScene(resources) {
    // 1. Room Background
    roomSprite = new PIXI.Sprite(resources[ASSETS.room]);
    roomSprite.anchor.set(0.5);
    roomSprite.x = app.screen.width / 2;
    roomSprite.y = app.screen.height / 2;
    
    const scale = Math.max(app.screen.width / 1920, app.screen.height / 1080);
    roomSprite.scale.set(scale);
    sceneContainer.addChild(roomSprite);

    // 2. Parallax Depth Map
    depthSprite = new PIXI.Sprite(resources[ASSETS.depth]);
    depthSprite.anchor.set(0.5);
    depthSprite.x = roomSprite.x;
    depthSprite.y = roomSprite.y;
    depthSprite.scale.copyFrom(roomSprite.scale);
    depthSprite.renderable = false; 
    sceneContainer.addChild(depthSprite);

    displacementFilter = new PIXI.DisplacementFilter(depthSprite);
    displacementFilter.scale.x = 0;
    displacementFilter.scale.y = 0;
    sceneContainer.filters = [displacementFilter];

    // 3. Environmental FX (Dust Motes)
    const fxContainer = new PIXI.Container();
    roomSprite.addChild(fxContainer);
    for(let i = 0; i < 60; i++) {
        let mote = new PIXI.Graphics();
        mote.beginFill(0xFFDAB9, 0.4); // soft warm dust
        mote.drawCircle(0, 0, Math.random() * 2 + 1);
        mote.endFill();
        mote.x = (Math.random() - 0.5) * 1920;
        mote.y = (Math.random() - 0.5) * 1080;
        mote.vy = (Math.random() * -0.5) - 0.1;
        mote.vx = (Math.random() - 0.5) * 0.5;
        dustMotes.push(mote);
        fxContainer.addChild(mote);
    }

    // 4. Avatar Container & Image-Based Skeletal Rig
    avatarContainer = new PIXI.Container();
    puppetRig = buildPuppet(resources);
    avatarContainer.addChild(puppetRig.root);
    roomSprite.addChild(avatarContainer);

    teleportAvatar('desk', true);

    // 5. Mouse Tracking for Parallax & Gaze
    app.stage.eventMode = 'static';
    app.stage.hitArea = new PIXI.Rectangle(0, 0, 10000, 10000);
    
    let targetParallaxX = 0;
    let targetParallaxY = 0;
    let pointerX = 0;
    
    app.stage.on('pointermove', (e) => {
        const x = (e.global.x / app.screen.width) * 2 - 1;
        const y = (e.global.y / app.screen.height) * 2 - 1;
        targetParallaxX = x * 20; 
        targetParallaxY = y * 15;
        pointerX = x; // for head tracking
    });

    // 6. Animation Loop
    let time = 0;
    app.ticker.add((delta) => {
        // Parallax
        displacementFilter.scale.x += (targetParallaxX - displacementFilter.scale.x) * 0.1 * delta;
        displacementFilter.scale.y += (targetParallaxY - displacementFilter.scale.y) * 0.1 * delta;
        
        // Environmental FX
        dustMotes.forEach(mote => {
            mote.y += mote.vy * delta;
            mote.x += mote.vx * delta;
            mote.alpha = 0.3 + Math.sin(time + mote.x) * 0.2;
            if (mote.y < -540) mote.y = 540;
            if (mote.x < -960) mote.x = 960;
            if (mote.x > 960) mote.x = -960;
        });

        // FK Skeletal Animation (Idle Breathing / Sway)
        time += 0.03 * delta;
        
        // Torso breathing (scales slightly, rotates up/down)
        puppetRig.torso.scale.y = 1 + Math.sin(time) * 0.015;
        puppetRig.torso.rotation = Math.sin(time * 0.8) * 0.02;
        
        // Hips gentle sway
        puppetRig.hips.x = Math.sin(time * 0.5) * 3;

        // Head tracking mouse + counter-rotation for breathing
        const headTargetRot = (pointerX * 0.2) - (puppetRig.torso.rotation * 0.8);
        puppetRig.neck.rotation += (headTargetRot - puppetRig.neck.rotation) * 0.1 * delta;

        // Arms subtle idle sway
        puppetRig.armL.rotation = Math.sin(time * 1.1) * 0.05 + 0.1;
        puppetRig.armR.rotation = Math.sin(time * 1.1 + Math.PI) * 0.05 - 0.1;

        // Legs idle shift (kneeling adjustments)
        puppetRig.legL.rotation = (-Math.PI / 4) + Math.sin(time * 0.4) * 0.02;
        puppetRig.legR.rotation = (Math.PI / 6) + Math.sin(time * 0.4 + 1) * 0.02;
    });

    setupUI();
}

function teleportAvatar(hotspotId, instant = false) {
    const spot = HOTSPOTS[hotspotId];
    if (!spot) return;
    
    currentHotspot = hotspotId;
    const roomW = roomSprite.texture.width;
    const roomH = roomSprite.texture.height;
    
    const targetX = spot.x - (roomW / 2);
    const targetY = spot.y - (roomH / 2);

    if (instant) {
        avatarContainer.x = targetX;
        avatarContainer.y = targetY;
        avatarContainer.scale.set(spot.scale);
        avatarContainer.alpha = 1;
    } else {
        let fadeOut = () => {
            avatarContainer.alpha -= 0.1;
            if (avatarContainer.alpha <= 0) {
                app.ticker.remove(fadeOut);
                avatarContainer.x = targetX;
                avatarContainer.y = targetY;
                avatarContainer.scale.set(spot.scale);
                app.ticker.add(fadeIn);
            }
        };
        let fadeIn = () => {
            avatarContainer.alpha += 0.1;
            if (avatarContainer.alpha >= 1) {
                avatarContainer.alpha = 1;
                app.ticker.remove(fadeIn);
            }
        };
        app.ticker.add(fadeOut);
    }
}

function setupUI() {
    document.getElementById('btn-bed').addEventListener('click', () => teleportAvatar('bed'));
    document.getElementById('btn-desk').addEventListener('click', () => teleportAvatar('desk'));
    document.getElementById('btn-window').addEventListener('click', () => teleportAvatar('window'));
    
    const btnExpr = document.getElementById('btn-expression');
    btnExpr.textContent = 'Reach Out';
    btnExpr.addEventListener('click', () => {
        let reachTime = 0;
        let reachAnim = (delta) => {
            reachTime += 0.1 * delta;
            puppetRig.armR.rotation = -Math.PI/3 + Math.sin(reachTime) * 0.2;
            if(reachTime > Math.PI * 3) {
                app.ticker.remove(reachAnim);
            }
        };
        app.ticker.add(reachAnim);
    });
}

window.addEventListener('resize', () => {
    if (roomSprite) {
        roomSprite.x = app.screen.width / 2;
        roomSprite.y = app.screen.height / 2;
        const scale = Math.max(app.screen.width / 1920, app.screen.height / 1080);
        roomSprite.scale.set(scale);
        if (depthSprite) {
            depthSprite.x = roomSprite.x;
            depthSprite.y = roomSprite.y;
            depthSprite.scale.copyFrom(roomSprite.scale);
        }
    }
});
