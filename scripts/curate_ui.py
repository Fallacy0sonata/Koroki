"""
Curation web UI — fast keep/drop interface for singer corpus segments.

Launches a local Flask server with a browser UI that:
  - Plays each wav with native HTML5 audio (seek, volume, replay)
  - K = keep, D = drop, Space = play/pause, J/L = skip 2s, U = undo last
  - Soft-delete: dropped files move to wavs_rejected/ — fully reversible
  - Shows audit hints (duration, silence%, f0std, flags) per file
  - Progress counter, recent-decisions panel with one-click restore

Run from Koroki root:
    .venv\\Scripts\\python.exe scripts\\curate_ui.py <singer>
    .venv\\Scripts\\python.exe scripts\\curate_ui.py yoasobi

Then open http://localhost:8765/ (auto-launches in default browser).

Soft-delete means you can change your mind. To finalize (permanently delete the
rejected files), run:
    .venv\\Scripts\\python.exe scripts\\curate_ui.py <singer> --finalize
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import threading
import time
import webbrowser
from pathlib import Path

try:
    from flask import Flask, jsonify, request, send_file
except ImportError:
    print("Flask not installed. Run: .venv\\Scripts\\python.exe -m pip install flask")
    sys.exit(1)

import numpy as np
import soundfile as sf

REPO_ROOT = Path(__file__).resolve().parents[1]
CORPUS_DIR = REPO_ROOT / "data" / "diffsinger_raw" / "koroki_singing_v2"

# ── Manifest parsing ─────────────────────────────────────────────────────

_MANIFEST_LINE = re.compile(
    r"^(?P<verdict>keep|drop)\s+(?P<file>\S+)\s+"
    r"# dur=(?P<dur>[\d.]+)s\s+sil=(?P<sil>\d+)%\s+f0std=(?P<f0>\d+)"
    r"(?:\s+\[(?P<flags>[^\]]+)\])?"
)


def parse_manifest(path: Path) -> dict[str, dict]:
    """Parse curate_manifest.txt → dict[filename] = {duration, silence_pct, f0_std, flag}."""
    out: dict[str, dict] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _MANIFEST_LINE.match(line.strip())
        if m:
            out[m["file"]] = {
                "duration": float(m["dur"]),
                "silence_pct": int(m["sil"]),
                "f0_std": int(m["f0"]),
                "flag": m["flags"] or "",
            }
    return out


# ── Flask app ────────────────────────────────────────────────────────────

app = Flask(__name__)

# Global state — set in main() before app.run
_SINGER: str = ""
_WAVS_DIR: Path = Path()
_REJECTED_DIR: Path = Path()
_ORIGINALS_DIR: Path = Path()  # pre-trim backups
_DECISIONS_PATH: Path = Path()
_DECISIONS: dict[str, str] = {}  # filename -> "keep" or "drop"
_AUDIT: dict[str, dict] = {}


def _save_decisions() -> None:
    """Persist decisions to disk so they survive UI restart."""
    try:
        _DECISIONS_PATH.write_text(json.dumps(_DECISIONS, indent=2), encoding="utf-8")
    except Exception as exc:
        print(f"Warning: could not save decisions: {exc}")


def _load_decisions() -> None:
    global _DECISIONS
    if _DECISIONS_PATH.exists():
        try:
            _DECISIONS = json.loads(_DECISIONS_PATH.read_text(encoding="utf-8"))
        except Exception:
            _DECISIONS = {}
    else:
        _DECISIONS = {}


@app.route("/")
def index() -> str:
    return _HTML


@app.route("/api/files")
def api_files():
    """List remaining (un-decided) wav files with metadata. Sorted by song then name."""
    if not _WAVS_DIR.exists():
        return jsonify({"files": [], "error": f"no wavs dir at {_WAVS_DIR}"})
    files = []
    kept_count = 0
    for w in sorted(_WAVS_DIR.glob("*.wav")):
        if w.name in _DECISIONS:
            if _DECISIONS[w.name] == "keep":
                kept_count += 1
            continue  # already decided — skip
        meta = _AUDIT.get(w.name, {})
        song = w.name.rsplit("_seg", 1)[0]
        files.append({
            "name": w.name,
            "song": song,
            "duration": meta.get("duration", 0.0),
            "silence_pct": meta.get("silence_pct", 0),
            "f0_std": meta.get("f0_std", 0),
            "flag": meta.get("flag", ""),
        })
    rejected = sorted(p.name for p in _REJECTED_DIR.glob("*.wav")) if _REJECTED_DIR.exists() else []
    return jsonify({
        "files": files,
        "kept_count": kept_count,
        "rejected_count": len(rejected),
        "rejected": rejected[-20:],  # last 20 for the undo panel
        "singer": _SINGER,
    })


@app.route("/api/audio/<path:filename>")
def api_audio(filename: str):
    """Serve a wav file from the corpus dir (or rejected, if specified via ?rejected=1)."""
    if request.args.get("rejected"):
        path = _REJECTED_DIR / filename
    else:
        path = _WAVS_DIR / filename
    if not path.exists():
        return jsonify({"error": "not found"}), 404
    return send_file(path, mimetype="audio/wav")


@app.route("/api/verdict", methods=["POST"])
def api_verdict():
    """Apply a verdict.

    keep → mark decided in _DECISIONS, file stays in wavs/ (where training needs it)
    drop → mark decided AND move to rejected/ (reversible via /api/restore)
    """
    data = request.json
    filename = data["filename"]
    verdict = data["verdict"]
    src = _WAVS_DIR / filename
    if not src.exists():
        return jsonify({"ok": False, "error": "file not found"}), 404
    if verdict == "drop":
        _REJECTED_DIR.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(_REJECTED_DIR / filename))
    _DECISIONS[filename] = verdict
    _save_decisions()
    return jsonify({"ok": True})


@app.route("/api/restore", methods=["POST"])
def api_restore():
    """Restore a previously dropped file (move back to wavs/ + clear decision)."""
    data = request.json
    filename = data["filename"]
    src = _REJECTED_DIR / filename
    if src.exists():
        shutil.move(str(src), str(_WAVS_DIR / filename))
    # Clear the decision either way (also lets the user "un-keep" a kept file
    # via this endpoint, though normal UI only triggers it from the dropped panel)
    if filename in _DECISIONS:
        del _DECISIONS[filename]
        _save_decisions()
    return jsonify({"ok": True})


@app.route("/api/unkeep", methods=["POST"])
def api_unkeep():
    """Re-open a kept file for re-decision (used by undo on K presses)."""
    data = request.json
    filename = data["filename"]
    if filename in _DECISIONS and _DECISIONS[filename] == "keep":
        del _DECISIONS[filename]
        _save_decisions()
    return jsonify({"ok": True})


@app.route("/api/trim", methods=["POST"])
def api_trim():
    """Trim a wav to [start, end] seconds. Preserves original to wavs_originals/.

    The trimmed file replaces the original at the SAME filename, so transcription
    entries still point at it (alignment will be approximate — see note below).

    Note about transcriptions: trimming changes the audio boundaries, so any
    paired transcription (ph_seq, ph_dur in transcriptions.csv) becomes slightly
    misaligned. For DiffSinger training this is usually still usable but ideally
    a SOFA re-alignment pass would fix it. That's a separate pre-training step.
    """
    data = request.json
    filename = data["filename"]
    start = float(data["start"])
    end = float(data["end"])
    src = _WAVS_DIR / filename
    if not src.exists():
        return jsonify({"ok": False, "error": "file not found"}), 404
    if end <= start:
        return jsonify({"ok": False, "error": "end must be > start"}), 400

    # Backup original if this is the first trim of this file
    _ORIGINALS_DIR.mkdir(parents=True, exist_ok=True)
    backup = _ORIGINALS_DIR / filename
    if not backup.exists():
        shutil.copy(str(src), str(backup))

    # Load (always from backup if exists — supports re-trim from clean source)
    load_from = backup if backup.exists() else src
    audio, sr = sf.read(str(load_from), always_2d=False)
    n = audio.shape[0] if audio.ndim > 0 else 0
    duration = n / sr
    if end > duration:
        end = duration
    if start < 0:
        start = 0.0

    start_sample = int(start * sr)
    end_sample = int(end * sr)
    trimmed = audio[start_sample:end_sample] if audio.ndim == 1 else audio[start_sample:end_sample, :]

    sf.write(str(src), trimmed, sr)

    # Update audit metadata
    new_duration = (end_sample - start_sample) / sr
    if filename in _AUDIT:
        _AUDIT[filename]["duration"] = new_duration

    return jsonify({"ok": True, "new_duration": new_duration})


@app.route("/api/untrim", methods=["POST"])
def api_untrim():
    """Restore the pre-trim version of a file from wavs_originals/."""
    data = request.json
    filename = data["filename"]
    backup = _ORIGINALS_DIR / filename
    if not backup.exists():
        return jsonify({"ok": False, "error": "no backup found"}), 404
    shutil.copy(str(backup), str(_WAVS_DIR / filename))
    # Recompute duration
    audio, sr = sf.read(str(_WAVS_DIR / filename))
    new_duration = audio.shape[0] / sr
    if filename in _AUDIT:
        _AUDIT[filename]["duration"] = new_duration
    return jsonify({"ok": True, "new_duration": new_duration})


# ── HTML (single-file UI) ────────────────────────────────────────────────

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Curate</title>
<style>
  * { box-sizing: border-box; }
  body { font-family: -apple-system, system-ui, sans-serif; margin: 0; padding: 20px;
         background: #1a1a1a; color: #ddd; }
  h1 { margin: 0 0 16px; font-size: 18px; color: #eee; }
  .panel { background: #2a2a2a; border: 1px solid #3a3a3a; border-radius: 6px;
           padding: 16px; margin-bottom: 14px; }
  .stats { display: flex; gap: 16px; font-size: 13px; }
  .stats span { color: #999; }
  .stats strong { color: #fff; }
  .current { font-size: 17px; word-break: break-all; }
  .meta { color: #aaa; font-size: 13px; margin-top: 4px; }
  .flag { display: inline-block; background: #b66; color: #fff; padding: 2px 8px;
          border-radius: 3px; font-size: 11px; margin-left: 8px; }
  .flag.short { background: #888; }
  .flag.silent { background: #b66; }
  .flag.flatpitch { background: #ba6; }
  audio { width: 100%; margin: 12px 0; filter: invert(0.85); }
  .buttons { display: flex; gap: 12px; margin-top: 8px; }
  button { padding: 14px 22px; font-size: 15px; border-radius: 6px; border: none;
           cursor: pointer; font-weight: 600; }
  .btn-keep { background: #2d8a2d; color: white; flex: 1; }
  .btn-keep:hover { background: #3aa83a; }
  .btn-drop { background: #b22222; color: white; flex: 1; }
  .btn-drop:hover { background: #d33; }
  .btn-skip { background: #444; color: #ddd; }
  .btn-skip:hover { background: #555; }
  .shortcuts { color: #888; font-size: 12px; margin-top: 8px; }
  .rejected-panel { font-size: 12px; }
  .rejected-item { padding: 4px 8px; background: #333; margin: 4px 0; display: flex;
                   justify-content: space-between; align-items: center; border-radius: 3px; }
  .btn-restore { background: #555; color: #ddd; padding: 4px 10px; font-size: 11px; }
  .progress-bar { height: 6px; background: #333; border-radius: 3px; overflow: hidden; margin-top: 6px; }
  .progress-fill { height: 100%; background: #4a4; transition: width 0.2s; }
  .empty { color: #888; padding: 40px; text-align: center; }
  .song-tag { color: #6af; font-size: 12px; margin-right: 8px; }
  .trim-panel { background: #2a2a2a; border: 1px solid #444; border-radius: 6px;
                padding: 12px; margin-top: 12px; }
  .trim-row { display: flex; gap: 10px; align-items: center; margin: 6px 0; }
  .trim-row input { background: #1a1a1a; border: 1px solid #444; color: #ddd;
                    padding: 6px 10px; border-radius: 4px; width: 80px; font-family: monospace; }
  .trim-row label { color: #aaa; font-size: 13px; }
  .btn-trim { background: #b67; color: white; padding: 8px 14px; font-size: 13px; }
  .btn-trim:hover { background: #c88; }
  .btn-sm { background: #444; color: #ddd; padding: 6px 12px; font-size: 12px; }
  .btn-sm:hover { background: #555; }
  .trim-hint { color: #888; font-size: 11px; margin-top: 6px; }
</style>
</head>
<body>
<h1 id="title">Curate · loading…</h1>

<div class="panel">
  <div class="stats">
    <span>Remaining: <strong id="remaining">-</strong></span>
    <span>Kept: <strong id="kept_count">-</strong></span>
    <span>Dropped: <strong id="rejected_count">-</strong></span>
    <span>Singer: <strong id="singer">-</strong></span>
  </div>
  <div class="progress-bar"><div class="progress-fill" id="progress"></div></div>
</div>

<div class="panel" id="player-panel">
  <div class="current"><span class="song-tag" id="cur_song"></span><span id="cur_name">—</span></div>
  <div class="meta"><span id="cur_meta">—</span></div>
  <audio id="player" controls></audio>
  <div class="buttons">
    <button class="btn-keep" onclick="verdict('keep')">Keep · K</button>
    <button class="btn-drop" onclick="verdict('drop')">Drop · D</button>
    <button class="btn-skip" onclick="skip(-1)">‹ Prev · J</button>
    <button class="btn-skip" onclick="skip(1)">Next › · L</button>
  </div>
  <div class="shortcuts">
    Keys: <strong>K</strong>=Keep · <strong>D</strong>=Drop · <strong>Space</strong>=Play/Pause
    · <strong>J</strong>/<strong>L</strong>=Prev/Next · <strong>R</strong>=Replay · <strong>U</strong>=Undo last drop
  </div>

  <div class="trim-panel">
    <div style="font-size: 13px; color: #aaa; margin-bottom: 6px;">Trim (optional — remove bad parts of an otherwise-good segment):</div>
    <div class="trim-row">
      <label>Start:</label>
      <input type="number" id="trim_start" step="0.1" min="0" value="0">
      <button class="btn-sm" onclick="setMarker('start')">Set Start · [</button>
      <label>End:</label>
      <input type="number" id="trim_end" step="0.1" min="0" value="0">
      <button class="btn-sm" onclick="setMarker('end')">Set End · ]</button>
      <button class="btn-sm" onclick="previewTrim()">Preview · P</button>
      <button class="btn-trim" onclick="saveTrim()">Save Trim · T</button>
      <button class="btn-sm" onclick="untrim()" title="Restore the original pre-trim version">Untrim</button>
    </div>
    <div class="trim-hint">
      Workflow: play file, press <strong>[</strong> at start of good part, <strong>]</strong> at end, <strong>P</strong> to preview, <strong>T</strong> to save.
      Original is preserved in wavs_originals/ — Untrim restores it.
    </div>
  </div>
</div>

<div class="panel">
  <div style="margin-bottom: 8px; font-size: 13px; color: #888;">Recent drops (click ↺ to restore):</div>
  <div class="rejected-panel" id="rejected_list"></div>
</div>

<script>
let files = [], idx = 0, lastAction = null;  // lastAction: {name, verdict}

async function load() {
  const r = await fetch("/api/files");
  const data = await r.json();
  files = data.files;
  document.getElementById("title").textContent = `Curate · ${data.singer}`;
  document.getElementById("singer").textContent = data.singer;
  document.getElementById("remaining").textContent = files.length;
  document.getElementById("kept_count").textContent = data.kept_count || 0;
  document.getElementById("rejected_count").textContent = data.rejected_count;
  const decided = (data.kept_count || 0) + data.rejected_count;
  const total = files.length + decided;
  const progress = total ? (decided / total * 100) : 0;
  document.getElementById("progress").style.width = progress + "%";
  renderRejected(data.rejected);
  if (files.length === 0) {
    document.getElementById("player-panel").innerHTML =
      '<div class="empty">All done! Run the script with --finalize to permanently delete rejected files.</div>';
    return;
  }
  if (idx >= files.length) idx = files.length - 1;
  if (idx < 0) idx = 0;
  showCurrent();
}

function showCurrent() {
  const f = files[idx];
  if (!f) return;
  document.getElementById("cur_song").textContent = "[" + f.song + "]";
  document.getElementById("cur_name").textContent = f.name;
  let metaText = `${f.duration.toFixed(1)}s · silence ${f.silence_pct}% · f0std ${f.f0_std}`;
  if (f.flag) metaText += ` `;
  document.getElementById("cur_meta").innerHTML = metaText.replace(/&/g, "&amp;");
  if (f.flag) {
    const flags = f.flag.split(",").map(x => x.trim().toLowerCase());
    for (const flag of flags) {
      const span = document.createElement("span");
      span.className = "flag " + flag;
      span.textContent = flag;
      document.getElementById("cur_meta").appendChild(span);
    }
  }
  const player = document.getElementById("player");
  // Cache-bust to ensure trimmed re-loads of same filename refresh
  player.src = "/api/audio/" + encodeURIComponent(f.name) + "?t=" + Date.now();
  // Reset trim markers
  document.getElementById("trim_start").value = "0";
  document.getElementById("trim_end").value = f.duration.toFixed(1);
  player.play().catch(() => {});  // autoplay may be blocked, that's fine
}

function setMarker(which) {
  const player = document.getElementById("player");
  const t = player.currentTime.toFixed(2);
  document.getElementById("trim_" + which).value = t;
}

let _previewTimer = null;
function previewTrim() {
  const start = parseFloat(document.getElementById("trim_start").value);
  const end = parseFloat(document.getElementById("trim_end").value);
  if (isNaN(start) || isNaN(end) || end <= start) {
    alert("Set valid start/end first."); return;
  }
  const player = document.getElementById("player");
  if (_previewTimer) { clearTimeout(_previewTimer); _previewTimer = null; }
  player.currentTime = start;
  player.play();
  _previewTimer = setTimeout(() => { player.pause(); _previewTimer = null; }, (end - start) * 1000 + 50);
}

async function saveTrim() {
  const f = files[idx];
  if (!f) return;
  const start = parseFloat(document.getElementById("trim_start").value);
  const end = parseFloat(document.getElementById("trim_end").value);
  if (isNaN(start) || isNaN(end) || end <= start) {
    alert("Set valid start/end first."); return;
  }
  const r = await fetch("/api/trim", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({filename: f.name, start, end}),
  });
  const data = await r.json();
  if (!data.ok) { alert("Trim failed: " + (data.error || "?")); return; }
  // Update local file duration and reload audio
  f.duration = data.new_duration;
  showCurrent();
}

async function untrim() {
  const f = files[idx];
  if (!f) return;
  const r = await fetch("/api/untrim", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({filename: f.name}),
  });
  const data = await r.json();
  if (!data.ok) { alert("Untrim failed: " + (data.error || "?")); return; }
  f.duration = data.new_duration;
  showCurrent();
}

async function verdict(v) {
  const f = files[idx];
  if (!f) return;
  await fetch("/api/verdict", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({filename: f.name, verdict: v}),
  });
  lastAction = {name: f.name, verdict: v};
  // idx stays — next file shifts up into this slot after reload
  await load();
}

function skip(d) {
  idx += d;
  if (idx < 0) idx = files.length - 1;
  if (idx >= files.length) idx = 0;
  showCurrent();
}

async function undo() {
  if (!lastAction) return;
  const endpoint = lastAction.verdict === "drop" ? "/api/restore" : "/api/unkeep";
  await fetch(endpoint, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({filename: lastAction.name}),
  });
  lastAction = null;
  await load();
}

async function restoreNamed(name) {
  await fetch("/api/restore", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({filename: name}),
  });
  await load();
}

function renderRejected(items) {
  const list = document.getElementById("rejected_list");
  list.innerHTML = "";
  if (!items.length) {
    list.innerHTML = '<div style="color:#666;">(no drops yet)</div>';
    return;
  }
  for (const name of items.slice().reverse()) {
    const row = document.createElement("div");
    row.className = "rejected-item";
    const span = document.createElement("span");
    span.style.color = "#aaa";
    span.textContent = name;
    const btn = document.createElement("button");
    btn.className = "btn-restore";
    btn.textContent = "↺ restore";
    btn.onclick = () => restoreNamed(name);
    row.appendChild(span);
    row.appendChild(btn);
    list.appendChild(row);
  }
}

document.addEventListener("keydown", (e) => {
  if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
  const key = e.key.toLowerCase();
  const player = document.getElementById("player");
  if (key === "k") { e.preventDefault(); verdict("keep"); }
  else if (key === "d") { e.preventDefault(); verdict("drop"); }
  else if (key === " ") {
    e.preventDefault();
    player.paused ? player.play() : player.pause();
  }
  else if (key === "j") { e.preventDefault(); skip(-1); }
  else if (key === "l") { e.preventDefault(); skip(1); }
  else if (key === "r") { e.preventDefault(); player.currentTime = 0; player.play(); }
  else if (key === "u") { e.preventDefault(); undo(); }
  else if (key === "[") { e.preventDefault(); setMarker("start"); }
  else if (key === "]") { e.preventDefault(); setMarker("end"); }
  else if (key === "p") { e.preventDefault(); previewTrim(); }
  else if (key === "t") { e.preventDefault(); saveTrim(); }
  else if (key === "arrowleft") { e.preventDefault(); player.currentTime = Math.max(0, player.currentTime - 2); }
  else if (key === "arrowright") { e.preventDefault(); player.currentTime = Math.min(player.duration || 0, player.currentTime + 2); }
});

load();
</script>
</body>
</html>
"""


# ── Main ─────────────────────────────────────────────────────────────────

def open_browser(url: str) -> None:
    time.sleep(1.0)
    webbrowser.open(url)


def main() -> None:
    global _SINGER, _WAVS_DIR, _REJECTED_DIR, _ORIGINALS_DIR, _DECISIONS_PATH, _AUDIT

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("singer", help="singer name (subdir under the corpus dir)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--corpus-dir", default=None,
                         help="corpus root (default: data/diffsinger_raw/koroki_singing_v2)")
    parser.add_argument("--finalize", action="store_true",
                         help="permanently delete rejected files and exit")
    args = parser.parse_args()

    singer = args.singer
    corpus = Path(args.corpus_dir) if args.corpus_dir else CORPUS_DIR
    if not corpus.is_absolute():
        corpus = REPO_ROOT / corpus
    singer_dir = corpus / singer
    wavs = singer_dir / "wavs"
    rejected = singer_dir / "wavs_rejected"

    if not wavs.exists():
        sys.exit(f"No wavs dir at {wavs}. Run singer_pipeline.py convert {singer} first.")

    if args.finalize:
        if not rejected.exists():
            print(f"No rejected directory at {rejected}. Nothing to finalize.")
            return
        rejected_files = list(rejected.glob("*.wav"))
        if not rejected_files:
            print(f"No rejected files. Nothing to delete.")
            shutil.rmtree(rejected)
            return
        print(f"This will permanently delete {len(rejected_files)} files from {rejected}.")
        ans = input("Type 'yes' to confirm: ").strip().lower()
        if ans != "yes":
            print("Aborted.")
            return
        shutil.rmtree(rejected)
        print(f"Deleted {len(rejected_files)} rejected files.")
        return

    # Set globals
    _SINGER = singer
    _WAVS_DIR = wavs
    _REJECTED_DIR = rejected
    _ORIGINALS_DIR = singer_dir / "wavs_originals"
    _DECISIONS_PATH = singer_dir / "curate_decisions.json"
    manifest_path = singer_dir / "curate_manifest.txt"
    _AUDIT = parse_manifest(manifest_path)
    _load_decisions()
    kept_existing = sum(1 for v in _DECISIONS.values() if v == "keep")
    dropped_existing = sum(1 for v in _DECISIONS.values() if v == "drop")
    print(f"Loaded audit metadata for {len(_AUDIT)} files from {manifest_path.name}")
    print(f"Existing decisions: {kept_existing} kept, {dropped_existing} dropped (resumable)")
    print(f"Singer: {singer}")
    print(f"Wavs dir: {wavs} ({len(list(wavs.glob('*.wav')))} files)")
    print(f"Rejected dir: {rejected}")
    print()
    url = f"http://localhost:{args.port}/"
    print(f"Starting server at {url} — opening browser in 1s")
    threading.Thread(target=open_browser, args=(url,), daemon=True).start()
    app.run(host="127.0.0.1", port=args.port, debug=False)


if __name__ == "__main__":
    main()
