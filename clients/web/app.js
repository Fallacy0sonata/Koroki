const serviceStatus = document.getElementById("service-status");
const modelStatus = document.getElementById("model-status");
const voiceStatus = document.getElementById("voice-status");
const stageMode = document.getElementById("stage-mode");
const emotionReadout = document.getElementById("emotion-readout");
const transcript = document.getElementById("transcript");
const userIdInput = document.getElementById("user-id");
const relationshipSlider = document.getElementById("relationship-score");
const relationshipValue = document.getElementById("relationship-score-value");
const ownerCheckbox = document.getElementById("is-owner");
const messageInput = document.getElementById("message-input");
const sendButton = document.getElementById("send-button");
const clearButton = document.getElementById("clear-transcript");
const latestAudio = document.getElementById("latest-audio");
const playLatestAudioButton = document.getElementById("play-latest-audio");
const voiceDetail = document.getElementById("voice-detail");
const avatarFrame = document.getElementById("avatar-frame");
const live2dCanvas = document.getElementById("live2d-canvas");
const composerForm = document.getElementById("composer-form");
const menuToggle = document.getElementById("menu-toggle");
const menuDrawer = document.getElementById("menu-drawer");
const menuClose = document.getElementById("menu-close");
const guestMenu = document.getElementById("guest-menu");
const memberMenu = document.getElementById("member-menu");
const menuTitle = document.getElementById("menu-title");
const loginButton = document.getElementById("login-button");
const signupButton = document.getElementById("signup-button");
const logoutButton = document.getElementById("logout-button");
const btnProfile = document.getElementById("btn-profile");
const btnVoiceHistory = document.getElementById("btn-voice-history");
const btnRelationship = document.getElementById("btn-relationship");
const loginForm = document.getElementById("login-form");
const signupForm = document.getElementById("signup-form");
const authFeedback = document.getElementById("auth-feedback");
const profileName = document.getElementById("profile-name");
const profileNote = document.getElementById("profile-note");
const responseArea = document.getElementById("response-area");
const responseText = document.getElementById("response-text");
const emotionChip = document.getElementById("emotion-chip");
const presenceEnergyText = document.getElementById("presence-energy");
const presenceEmotionText = document.getElementById("presence-emotion-text");
const relationshipDisplay = document.getElementById("relationship-display");

const state = {
  latestAudioUrl: "",
  latestEmotion: "idle",
  latestTtsRequest: null,
  live2dApp: null,
  live2dModel: null,
  analyser: null,
  audioContext: null,
  mediaSource: null,
  lipSyncLevel: 0,
  voiceBusy: false,
  drawerOpen: false,
  isLoggedIn: false,
  pointerX: 0,
  pointerY: 0,
  pointerActive: false,
  pointerClientX: 0,
  pointerClientY: 0,
  sessionUser: null,
  thinking: false,
  speaking: false,
};

const MODEL_URL = encodeURI("/assets/live2d/苹果小狐狸/苹果小狐狸.model3.json");

const MODEL_EMOTION_MAP = {
  neutral:    { mouthForm: 0.05,  brow: 0.0,   angleX: 0,   angleY: 0,  angleZ: 0    },
  caring:     { mouthForm: 0.45,  brow: -0.2,  angleX: -2,  angleY: -1, angleZ: -1.5 },
  playful:    { mouthForm: 0.75,  brow: -0.15, angleX: 4,   angleY: -1, angleZ: 4    },
  thoughtful: { mouthForm: -0.08, brow: 0.28,  angleX: -5,  angleY: 2,  angleZ: -2   },
  firm:       { mouthForm: -0.2,  brow: 0.4,   angleX: 2,   angleY: 1,  angleZ: 1.5  },
  annoyed:    { mouthForm: -0.45, brow: 0.62,  angleX: 5,   angleY: 3,  angleZ: 3.5  },
  cold:       { mouthForm: -0.55, brow: 0.36,  angleX: -3,  angleY: 1,  angleZ: -2   },
  curious:    { mouthForm: 0.18,  brow: 0.1,   angleX: 5,   angleY: -2, angleZ: 1.5  },
  worried:    { mouthForm: -0.18, brow: 0.34,  angleX: -2,  angleY: 4,  angleZ: -2   },
  protective: { mouthForm: -0.12, brow: 0.46,  angleX: 1.5, angleY: 1,  angleZ: 1.5  },
  tired:      { mouthForm: -0.16, brow: -0.08, angleX: -1,  angleY: -3, angleZ: -2   },
};

// Emotion → model expression parameters (Param IDs from exp3.json files)
// 脸红=Param104 blush, 流泪=Param105 tears, 星星=Param106 star-eyes,
// 爱心=Param108 heart-eyes, 生气=Param109 angry, 脸黑=Param110 dark-face
const MODEL_EXPRESSION_MAP = {
  neutral:    { Param104: 0,  Param105: 0, Param106: 0, Param108: 0, Param109: 0,   Param110: 0  },
  caring:     { Param104: 30, Param105: 0, Param106: 0, Param108: 0, Param109: 0,   Param110: 0  },
  playful:    { Param104: 0,  Param105: 0, Param106: 1, Param108: 0, Param109: 0,   Param110: 0  },
  thoughtful: { Param104: 0,  Param105: 0, Param106: 0, Param108: 0, Param109: 0,   Param110: 0  },
  firm:       { Param104: 0,  Param105: 0, Param106: 0, Param108: 0, Param109: 1,   Param110: 0  },
  annoyed:    { Param104: 0,  Param105: 0, Param106: 0, Param108: 0, Param109: 1,   Param110: 0  },
  cold:       { Param104: 0,  Param105: 0, Param106: 0, Param108: 0, Param109: 0,   Param110: 30 },
  curious:    { Param104: 0,  Param105: 0, Param106: 1, Param108: 0, Param109: 0,   Param110: 0  },
  worried:    { Param104: 0,  Param105: 1, Param106: 0, Param108: 0, Param109: 0,   Param110: 0  },
  protective: { Param104: 0,  Param105: 0, Param106: 0, Param108: 0, Param109: 0.5, Param110: 0  },
  tired:      { Param104: 0,  Param105: 1, Param106: 0, Param108: 0, Param109: 0,   Param110: 0  },
};

// Emotion → atmosphere glow color and label
const EMOTION_ATMOSPHERE = {
  neutral:    { glow: "rgba(138, 182, 255, 0.12)", chip: "#8ab6ff",  label: "Neutral"    },
  caring:     { glow: "rgba(244, 207, 215, 0.20)", chip: "#f4cfd7",  label: "Caring"     },
  playful:    { glow: "rgba(255, 216, 162, 0.18)", chip: "#ffd8a2",  label: "Playful"    },
  thoughtful: { glow: "rgba(175, 195, 255, 0.14)", chip: "#afc3ff",  label: "Thoughtful" },
  firm:       { glow: "rgba(138, 182, 255, 0.10)", chip: "#8ab6ff",  label: "Firm"       },
  annoyed:    { glow: "rgba(255, 177, 191, 0.16)", chip: "#ffb1bf",  label: "Annoyed"    },
  cold:       { glow: "rgba(200, 232, 255, 0.18)", chip: "#c8e8ff",  label: "Cold"       },
  curious:    { glow: "rgba(175, 210, 255, 0.16)", chip: "#afd2ff",  label: "Curious"    },
  worried:    { glow: "rgba(244, 207, 215, 0.12)", chip: "#e8b8c8",  label: "Worried"    },
  protective: { glow: "rgba(138, 182, 255, 0.14)", chip: "#8ab6ff",  label: "Protective" },
  tired:      { glow: "rgba(100, 130, 180, 0.10)", chip: "#8090b0",  label: "Tired"      },
  idle:       { glow: "rgba(138, 182, 255, 0.07)", chip: "#6080a0",  label: "Idle"       },
};

// Face tracking: position derived from model's own PIXI transform, not screen estimates.
// faceFraction = where the face is within the model's bounding box (0=top, 1=bottom).
// rangePixels = screen pixels of cursor travel to reach max look angle.
const TRACKING_CONFIG = {
  faceFraction: 0.12,
  rangePixels: 260,
};

// ─── Atmosphere & State ──────────────────────────────────────────────────────

function setAtmosphere(emotion) {
  const key = String(emotion || "idle").toLowerCase().split("_")[0];
  const atm = EMOTION_ATMOSPHERE[key] || EMOTION_ATMOSPHERE.neutral;

  document.documentElement.style.setProperty("--atmosphere-glow", atm.glow);

  if (emotionChip) {
    emotionChip.textContent = atm.label;
    emotionChip.style.color = atm.chip;
  }
  if (presenceEmotionText) {
    presenceEmotionText.textContent = atm.label;
    presenceEmotionText.style.color = atm.chip;
  }
  if (emotionReadout) emotionReadout.textContent = `Emotion: ${atm.label}`;

  state.latestEmotion = emotion || "idle";
}

function setThinking(isThinking) {
  state.thinking = isThinking;
  if (avatarFrame) avatarFrame.classList.toggle("is-thinking", isThinking);
  if (stageMode) stageMode.textContent = isThinking ? "Thinking…" : "Stage online";
}

function setSpeaking(isSpeaking) {
  state.speaking = isSpeaking;
  if (avatarFrame) avatarFrame.classList.toggle("is-speaking", isSpeaking);
}

// ─── Response Display ────────────────────────────────────────────────────────

let _typewriterTimer = null;

function showKorokiResponse(text) {
  if (!responseArea || !responseText) return;
  if (_typewriterTimer) {
    clearInterval(_typewriterTimer);
    _typewriterTimer = null;
  }
  responseText.textContent = "";
  responseArea.classList.add("has-content");

  const chars = Array.from(String(text || ""));
  let i = 0;
  _typewriterTimer = setInterval(() => {
    if (i < chars.length) {
      responseText.textContent += chars[i];
      i++;
    } else {
      clearInterval(_typewriterTimer);
      _typewriterTimer = null;
    }
  }, 14);
}

// ─── Mood Display ────────────────────────────────────────────────────────────

function updateMoodDisplay(data) {
  const emotion = data?.tts_request?.emotion || "neutral";
  const intensity = Number(data?.tts_request?.emotion_intensity ?? 50);
  const warmth = Number(data?.tts_request?.emotion_intensity ?? 50);
  const arousal = emotion === "playful" || emotion === "annoyed" ? Math.min(100, intensity + 15)
                : emotion === "tired"   || emotion === "cold"    ? Math.max(10, intensity - 20)
                : intensity;
  const relScore = Number(data?.tts_request?.relationship_score ?? null);

  const barWarmth = document.getElementById("bar-warmth");
  const barIntensity = document.getElementById("bar-intensity");
  const barArousal = document.getElementById("bar-arousal");
  const barRel = document.getElementById("bar-relationship");

  if (barWarmth)    barWarmth.style.width    = `${Math.round(warmth)}%`;
  if (barIntensity) barIntensity.style.width = `${Math.round(intensity)}%`;
  if (barArousal)   barArousal.style.width   = `${Math.round(arousal)}%`;

  if (!isNaN(relScore) && relScore !== null) {
    if (relationshipDisplay) relationshipDisplay.textContent = `${relScore} / 100`;
    if (barRel) barRel.style.width = `${relScore}%`;
  }
}

function updatePresenceEnergy(data) {
  if (!presenceEnergyText) return;
  const emotion = data?.tts_request?.emotion || "neutral";
  const intensity = Number(data?.tts_request?.emotion_intensity ?? 50);

  let energyText = "Present";
  if (intensity >= 75) energyText = "Heightened";
  else if (intensity >= 55) energyText = "Engaged";
  else if (intensity <= 30) energyText = "Withdrawn";
  else if (emotion === "tired") energyText = "Drained";
  else if (emotion === "cold") energyText = "Guarded";
  else if (emotion === "caring" || emotion === "playful") energyText = "Warm";

  presenceEnergyText.textContent = energyText;
}

// ─── UI Sounds ───────────────────────────────────────────────────────────────

function ensureSoundContext() {
  if (!state.audioContext) {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtx) return null;
    state.audioContext = new AudioCtx();
  }
  if (state.audioContext.state === "suspended") {
    state.audioContext.resume().catch(() => {});
  }
  return state.audioContext;
}

function playUiSound(type) {
  try {
    const ctx = ensureSoundContext();
    if (!ctx) return;

    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);

    if (type === "send") {
      // Brief rising pluck
      osc.type = "sine";
      osc.frequency.setValueAtTime(660, ctx.currentTime);
      osc.frequency.exponentialRampToValueAtTime(880, ctx.currentTime + 0.08);
      gain.gain.setValueAtTime(0, ctx.currentTime);
      gain.gain.linearRampToValueAtTime(0.055, ctx.currentTime + 0.01);
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.2);
      osc.start();
      osc.stop(ctx.currentTime + 0.22);
    } else if (type === "receive") {
      // Soft descending chime — two tones
      const osc2 = ctx.createOscillator();
      const gain2 = ctx.createGain();
      osc2.connect(gain2);
      gain2.connect(ctx.destination);

      osc.type = "sine";
      osc.frequency.setValueAtTime(880, ctx.currentTime);
      osc.frequency.exponentialRampToValueAtTime(660, ctx.currentTime + 0.28);
      gain.gain.setValueAtTime(0, ctx.currentTime);
      gain.gain.linearRampToValueAtTime(0.05, ctx.currentTime + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.45);
      osc.start();
      osc.stop(ctx.currentTime + 0.48);

      osc2.type = "sine";
      osc2.frequency.setValueAtTime(660, ctx.currentTime + 0.15);
      osc2.frequency.exponentialRampToValueAtTime(440, ctx.currentTime + 0.45);
      gain2.gain.setValueAtTime(0, ctx.currentTime + 0.15);
      gain2.gain.linearRampToValueAtTime(0.035, ctx.currentTime + 0.18);
      gain2.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.55);
      osc2.start(ctx.currentTime + 0.15);
      osc2.stop(ctx.currentTime + 0.58);
    }
  } catch (_e) {}
}

// ─── Status helpers ──────────────────────────────────────────────────────────

function setServiceStatus(kind, text) {
  serviceStatus.textContent = text;
  serviceStatus.className = `status-pill status-${kind}`;
}

function setModelStatus(kind, text) {
  modelStatus.textContent = text;
  modelStatus.className = `status-pill status-${kind}`;
}

function setVoiceStatus(kind, text) {
  voiceStatus.textContent = text;
  voiceStatus.className = `status-pill status-${kind}`;
}

// ─── Auth ────────────────────────────────────────────────────────────────────

async function playLatestAudioWithFeedback(reason = "voice") {
  if (!latestAudio.src) return false;
  try {
    await ensureAudioAnalyser();
    latestAudio.currentTime = 0;
    await latestAudio.play();
    if (reason === "voice") {
      setVoiceStatus("online", "Voice playing");
      if (voiceDetail) voiceDetail.textContent = "Koroki is speaking now.";
    }
    return true;
  } catch (_error) {
    if (reason === "voice") {
      setVoiceStatus("degraded", "Tap Play Again");
      if (voiceDetail) voiceDetail.textContent = "Voice is ready — tap Play Again.";
    }
    return false;
  }
}

function syncMenuState() {
  menuDrawer.classList.toggle("menu-open", state.drawerOpen);
  menuDrawer.setAttribute("aria-hidden", String(!state.drawerOpen));
  menuToggle.setAttribute("aria-expanded", String(state.drawerOpen));
  guestMenu.classList.toggle("menu-section-hidden", state.isLoggedIn);
  memberMenu.classList.toggle("menu-section-hidden", !state.isLoggedIn);
  menuTitle.textContent = state.isLoggedIn ? "Court profile" : "Guest access";
  if (state.isLoggedIn && state.sessionUser) {
    profileName.textContent = state.sessionUser.username;
    profileNote.textContent = `Relationship ${state.sessionUser.relationship_score}/100`;
  }
}

function setAuthFeedback(text, kind = "info") {
  authFeedback.textContent = text;
  authFeedback.dataset.kind = kind;
}

function extractErrorMessage(data, fallback = "Authentication failed.") {
  if (!data) return fallback;
  if (typeof data === "string") return data;
  if (typeof data.detail === "string") return data.detail;
  if (Array.isArray(data.detail)) {
    const firstIssue = data.detail[0];
    if (typeof firstIssue === "string") return firstIssue;
    if (firstIssue && typeof firstIssue.msg === "string") return firstIssue.msg;
  }
  return fallback;
}

function authCueFromMessage(message) {
  const text = String(message || "").toLowerCase();
  if (text.includes("email already")) return "duplicate_email";
  if (text.includes("username is already") || text.includes("username already")) return "duplicate_username";
  if (text.includes("password is wrong")) return "wrong_password";
  if (text.includes("username doesn't exist") || text.includes("username does not exist")) return "missing_username";
  return null;
}

async function playAuthVoiceCue(cue) {
  if (!cue) return;
  try {
    const response = await fetch("/v1/auth/voice", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ cue }),
    });
    const data = await response.json();
    if (!response.ok || !data.audio_url) return;
    state.latestAudioUrl = data.audio_url;
    latestAudio.pause();
    latestAudio.src = data.audio_url;
    latestAudio.load();
    if (playLatestAudioButton) playLatestAudioButton.disabled = false;
    await playLatestAudioWithFeedback("auth");
  } catch (_error) {}
}

function openMenu() {
  state.drawerOpen = true;
  syncMenuState();
}

function closeMenu() {
  state.drawerOpen = false;
  syncMenuState();
}

function applySession(session) {
  state.isLoggedIn = Boolean(session?.authenticated && session?.user);
  state.sessionUser = state.isLoggedIn ? session.user : null;
  userIdInput.value = state.sessionUser?.user_id || "web_guest";
  relationshipSlider.value = String(state.sessionUser?.relationship_score ?? 60);
  if (relationshipValue) relationshipValue.textContent = relationshipSlider.value;
  ownerCheckbox.value = state.sessionUser?.is_owner ? "true" : "false";
  syncMenuState();
}

async function refreshSession() {
  try {
    const response = await fetch("/v1/auth/session", { credentials: "same-origin" });
    if (!response.ok) throw new Error(`session ${response.status}`);
    const session = await response.json();
    applySession(session);
  } catch (_error) {
    applySession({ authenticated: false, user: null });
  }
}

async function submitAuth(path, payload, successMessage) {
  try {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(extractErrorMessage(data));
    applySession(data);
    setAuthFeedback(successMessage, "success");
    await playAuthVoiceCue(path.includes("signup") ? "signup_success" : "login_success");
    closeMenu();
  } catch (error) {
    const message = error.message || "Authentication failed.";
    setAuthFeedback(message, "error");
    await playAuthVoiceCue(authCueFromMessage(message));
  }
}

// ─── Live2D ──────────────────────────────────────────────────────────────────

function addMessage(role, content, meta = "") {
  if (transcript.classList.contains("sr-only")) return;
  const card = document.createElement("article");
  card.className = `message ${role === "user" ? "message-user" : "message-koroki"}`;
  const metaEl = document.createElement("span");
  metaEl.className = "message-meta";
  metaEl.textContent = meta || (role === "user" ? "You" : "Koroki");
  const body = document.createElement("div");
  body.textContent = content;
  card.append(metaEl, body);
  transcript.append(card);
  transcript.scrollTop = transcript.scrollHeight;
}

async function refreshHealth() {
  try {
    const response = await fetch("/health");
    if (!response.ok) throw new Error(`health ${response.status}`);
    const data = await response.json();
    setServiceStatus("online", `Service online • ${Math.round(data.uptime_seconds || 0)}s`);
  } catch (_error) {
    setServiceStatus("offline", "Service offline");
  }
}

function updateRelationshipValue() {
  if (relationshipValue) relationshipValue.textContent = relationshipSlider.value || "60";
}

function getEmotionKey() {
  const value = (state.latestEmotion || "neutral").toLowerCase();
  const direct = value.split("_")[0];
  return MODEL_EMOTION_MAP[direct] ? direct : "neutral";
}

function getCoreModel() {
  return (
    state.live2dModel?.internalModel?.coreModel ||
    state.live2dModel?.internalModel?.model ||
    null
  );
}

function setModelParameter(id, value, weight = 1) {
  const coreModel = getCoreModel();
  if (!coreModel?.setParameterValueById) return;
  try {
    coreModel.setParameterValueById(id, value, weight);
  } catch (_error) {}
}

function fitLive2DModel() {
  if (!state.live2dApp || !state.live2dModel) return;
  const width = avatarFrame.clientWidth - 40;
  const height = avatarFrame.clientHeight - 40;
  state.live2dApp.renderer.resize(Math.max(width, 1), Math.max(height, 1));
  const bounds = state.live2dModel.getLocalBounds();
  const modelWidth = Math.max(bounds.width, 1);
  const modelHeight = Math.max(bounds.height, 1);
  const scale = Math.max((width * 1.55) / modelWidth, (height * 1.75) / modelHeight);
  state.live2dModel.scale.set(scale);
  state.live2dModel.pivot.set(bounds.x + modelWidth / 2, bounds.y + modelHeight * 0.28);
  state.live2dModel.position.set(width / 2, height * 0.52);
}

function getFaceScreenPos() {
  if (!state.live2dModel || !state.live2dApp || !live2dCanvas) return null;
  const bounds = state.live2dModel.getLocalBounds();
  const faceLocal = {
    x: bounds.x + bounds.width * 0.5,
    y: bounds.y + bounds.height * TRACKING_CONFIG.faceFraction,
  };
  const faceGlobal = state.live2dModel.toGlobal(faceLocal);
  const rect = live2dCanvas.getBoundingClientRect();
  const sx = rect.width / state.live2dApp.renderer.width;
  const sy = rect.height / state.live2dApp.renderer.height;
  return {
    x: rect.left + faceGlobal.x * sx,
    y: rect.top + faceGlobal.y * sy,
  };
}

function updatePointerTracking(event) {
  const face = getFaceScreenPos();
  if (!face) return;
  const r = TRACKING_CONFIG.rangePixels;
  state.pointerX = Math.max(-1, Math.min(1, (event.clientX - face.x) / r));
  state.pointerY = Math.max(-1, Math.min(1, (event.clientY - face.y) / r));
  state.pointerActive = true;
}

function resetPointerTracking() {
  state.pointerActive = false;
}

function applyStageMotion(now) {
  const config = MODEL_EMOTION_MAP[getEmotionKey()] || MODEL_EMOTION_MAP.neutral;
  const expr = MODEL_EXPRESSION_MAP[getEmotionKey()] || MODEL_EXPRESSION_MAP.neutral;
  const t = now / 1000;
  const breath = 0.5 + Math.sin(t * 1.5) * 0.5;
  const sway = Math.sin(t * 0.9) * 1.4;
  const lip = state.lipSyncLevel;
  const pointerX = state.pointerActive ? state.pointerX : Math.sin(t * 0.55) * 0.16;
  const pointerY = state.pointerActive ? state.pointerY : Math.cos(t * 0.65) * 0.08;

  setModelParameter("ParamBreath", breath);
  setModelParameter("ParamMouthOpenY", Math.min(1, lip * 1.15));
  setModelParameter("ParamMouthForm", config.mouthForm, 0.8);
  setModelParameter("ParamBrowLForm", config.brow, 0.65);
  setModelParameter("ParamBrowRForm", config.brow, 0.65);
  setModelParameter("ParamAngleX", config.angleX + sway + pointerX * 4.0, 0.6);
  setModelParameter("ParamAngleY", config.angleY - pointerY * 4.0, 0.6);
  setModelParameter("ParamAngleZ", config.angleZ + Math.sin(t * 1.1) * 0.9, 0.6);
  setModelParameter("ParamEyeBallX", pointerX * 0.55, 0.65);
  setModelParameter("ParamEyeBallY", -pointerY * 0.55, 0.65);
  setModelParameter("ParamEyeLSmile", config.mouthForm > 0.4 ? 0.3 : 0.0, 0.4);
  setModelParameter("ParamEyeRSmile", config.mouthForm > 0.4 ? 0.3 : 0.0, 0.4);
  for (const [id, value] of Object.entries(expr)) {
    setModelParameter(id, value, 0.5);
  }
}

async function initLive2D() {
  if (!window.PIXI?.Application || !window.PIXI?.live2d?.Live2DModel) {
    if (stageMode) stageMode.textContent = "Live2D runtime missing";
    setModelStatus("degraded", "Live2D runtime unavailable");
    return;
  }

  try {
    state.live2dApp = new window.PIXI.Application({
      view: live2dCanvas,
      autoStart: true,
      transparent: true,
      antialias: true,
      backgroundAlpha: 0,
    });

    const { Live2DModel } = window.PIXI.live2d;
    const response = await fetch(MODEL_URL);
    if (!response.ok) throw new Error(`model ${response.status}`);
    const modelJson = await response.json();
    modelJson.url = MODEL_URL;

    const model = await Live2DModel.from(modelJson);
    state.live2dModel = model;
    model.interactive = false;
    model.autoInteract = false;
    // Kill the built-in focus controller so it stops pulling params back to center
    // every tick and fighting our manual tracking.
    const fc = model.internalModel?.focusController;
    if (fc) fc.update = () => {};
    state.live2dApp.stage.addChild(model);
    fitLive2DModel();
    window.addEventListener("resize", fitLive2DModel);
    state.live2dApp.ticker.add(() => applyStageMotion(performance.now()));

    if (stageMode) stageMode.textContent = "Stage online";
    setModelStatus("online", "Live2D model active");
  } catch (error) {
    console.error("Live2D init failed:", error);
    if (stageMode) stageMode.textContent = "Live2D failed";
    setModelStatus("degraded", "Live2D load failed");
  }
}

// ─── Audio & Lipsync ─────────────────────────────────────────────────────────

async function ensureAudioAnalyser() {
  if (state.analyser) return;
  const ctx = ensureSoundContext();
  if (!ctx) return;

  if (!state.mediaSource) {
    state.mediaSource = ctx.createMediaElementSource(latestAudio);
    state.analyser = ctx.createAnalyser();
    state.analyser.fftSize = 256;
    state.mediaSource.connect(state.analyser);
    state.analyser.connect(ctx.destination);
  }
}

function startLipSyncLoop() {
  if (!state.analyser) return;
  const bins = new Uint8Array(state.analyser.frequencyBinCount);
  const tick = () => {
    if (!state.analyser) return;
    state.analyser.getByteFrequencyData(bins);
    const sum = bins.reduce((total, value) => total + value, 0);
    state.lipSyncLevel = Math.min(1, sum / (bins.length * 110));
    if (!latestAudio.paused && !latestAudio.ended) {
      requestAnimationFrame(tick);
    } else {
      state.lipSyncLevel = 0;
    }
  };
  requestAnimationFrame(tick);
}

// ─── Voice (deferred TTS) ────────────────────────────────────────────────────

async function requestDeferredVoice(ttsRequest) {
  state.voiceBusy = true;
  state.latestTtsRequest = ttsRequest;
  setVoiceStatus("voice", "Voice rendering…");
  if (voiceDetail) voiceDetail.textContent = "Koroki is shaping the delayed voice reply…";

  try {
    const response = await fetch("/v1/voice", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(ttsRequest),
    });
    if (!response.ok) throw new Error(`voice ${response.status}`);

    const data = await response.json();
    state.latestAudioUrl = data.audio_url;
    latestAudio.src = data.audio_url;
    latestAudio.load();
    if (playLatestAudioButton) playLatestAudioButton.disabled = false;
    setVoiceStatus("online", "Voice ready");
    if (voiceDetail) voiceDetail.textContent = `Voice ready — ${ttsRequest.emotion_variant || ttsRequest.emotion || "neutral"}.`;

    playUiSound("receive");
    await playLatestAudioWithFeedback("voice");
  } catch (_error) {
    state.latestAudioUrl = "";
    if (playLatestAudioButton) playLatestAudioButton.disabled = true;
    setVoiceStatus("degraded", "Voice failed");
    if (voiceDetail) voiceDetail.textContent = "Voice synthesis failed for this turn.";
  } finally {
    state.voiceBusy = false;
  }
}

// ─── Main chat flow ──────────────────────────────────────────────────────────

async function sendChat() {
  const message = messageInput.value.trim();
  if (!message) return;

  addMessage("user", message);
  messageInput.value = "";
  sendButton.disabled = true;
  playUiSound("send");
  setThinking(true);

  const relationshipScore = Number(relationshipSlider.value);
  const isOwner = ownerCheckbox.value === "true";

  try {
    const response = await fetch("/v1/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        request_id: `web_${Date.now()}`,
        message,
        defer_tts: true,
        user_context: {
          user_id: userIdInput.value.trim() || "web_guest",
          relationship_score: isOwner ? 100 : relationshipScore,
          is_owner: isOwner,
          mode: isOwner ? "owner" : "auto",
          platform: "web",
        },
      }),
    });

    if (!response.ok) throw new Error(`chat ${response.status}`);

    const data = await response.json();
    setThinking(false);

    const emotion = data?.tts_request?.emotion_variant || data?.tts_request?.emotion || "neutral";
    setAtmosphere(emotion);
    updateMoodDisplay(data);
    updatePresenceEnergy(data);

    if (data.text) {
      showKorokiResponse(data.text);
      addMessage("koroki", data.text);
    }

    if (data.tts_request) {
      latestAudio.pause();
      latestAudio.removeAttribute("src");
      latestAudio.load();
      if (playLatestAudioButton) playLatestAudioButton.disabled = true;
      state.latestAudioUrl = "";
      requestDeferredVoice(data.tts_request);
    } else {
      setVoiceStatus("muted", "Voice unavailable");
      if (voiceDetail) voiceDetail.textContent = "This reply did not produce voice.";
    }
  } catch (_error) {
    setThinking(false);
    if (voiceDetail) voiceDetail.textContent = "Something went wrong while reaching Koroki.";
    setVoiceStatus("degraded", "Unreachable");
  } finally {
    sendButton.disabled = false;
  }
}

// ─── Event Listeners ─────────────────────────────────────────────────────────

relationshipSlider.addEventListener("input", updateRelationshipValue);
composerForm.addEventListener("submit", (event) => {
  event.preventDefault();
  sendChat();
});

menuToggle.addEventListener("click", () => {
  if (state.drawerOpen) closeMenu(); else openMenu();
});
menuClose.addEventListener("click", closeMenu);
menuDrawer.addEventListener("click", (event) => {
  if (event.target === menuDrawer) closeMenu();
});

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const formData = new FormData(loginForm);
  await submitAuth("/v1/auth/login", {
    username: String(formData.get("username") || ""),
    password: String(formData.get("password") || ""),
  }, "Welcome back to Koroki's court.");
});

signupForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const formData = new FormData(signupForm);
  await submitAuth("/v1/auth/signup", {
    username: String(formData.get("username") || ""),
    email: String(formData.get("email") || ""),
    password: String(formData.get("password") || ""),
  }, "Your place in Koroki's court is ready.");
});

logoutButton.addEventListener("click", async () => {
  await fetch("/v1/auth/logout", { method: "POST", credentials: "same-origin" }).catch(() => {});
  applySession({ authenticated: false, user: null });
  setAuthFeedback("You stepped out of Koroki's court.", "info");
  await playAuthVoiceCue("logout");
  closeMenu();
});

clearButton.addEventListener("click", () => {
  transcript.innerHTML = "";
});

if (playLatestAudioButton) {
  playLatestAudioButton.addEventListener("click", () => {
    if (state.latestAudioUrl) playLatestAudioWithFeedback("voice");
  });
}

latestAudio.addEventListener("play", async () => {
  await ensureAudioAnalyser();
  setSpeaking(true);
  startLipSyncLoop();
});

latestAudio.addEventListener("ended", () => {
  state.lipSyncLevel = 0;
  setSpeaking(false);
  if (state.latestAudioUrl) {
    setVoiceStatus("online", "Voice ready");
    if (voiceDetail) voiceDetail.textContent = "Voice clip is ready to replay.";
  }
});

btnProfile?.addEventListener("click", () => {
  setAuthFeedback("Profile settings are not yet available.", "info");
});
btnVoiceHistory?.addEventListener("click", () => {
  setAuthFeedback("Voice history is not yet available.", "info");
});
btnRelationship?.addEventListener("click", () => {
  setAuthFeedback(`Your relationship score is ${state.sessionUser?.relationship_score || 0}.`, "info");
});

if (avatarFrame) {
  avatarFrame.addEventListener("mousemove", updatePointerTracking);
  avatarFrame.addEventListener("mouseleave", resetPointerTracking);
}

// ─── Init ────────────────────────────────────────────────────────────────────

updateRelationshipValue();
setAtmosphere("idle");
refreshHealth();
refreshSession();
setInterval(refreshHealth, 15000);
setVoiceStatus("muted", "Voice idle");
syncMenuState();
initLive2D();
