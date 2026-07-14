"""Demonstration recorder — Limbs Stage 0 (2026-07-09).

Records the owner's gameplay as training corpus: game-window video plus a
timestamped log of every input. Each session feeds two consumers later:
skill mining (macros with visual pre/postconditions) and the VPT-style
inverse-dynamics model that will pseudo-label YouTube footage
(docs/game_limbs_verdict_2026-07-09.md).

Design notes:
- frames.jsonl is the alignment ground truth (video PTS is NOT wallclock —
  frames are dropped while the game is unfocused).
- Input logging is FOCUS-GATED: nothing is captured while the game window is
  not foreground, so alt-tabbing to Discord never leaks keystrokes. Monitor
  mode (--window "") has no gate — it warns loudly.
- Camera movement in Roblox (and most 3D games) arrives as RELATIVE raw mouse
  input while the cursor is locked/frozen; cursor-position hooks lose it
  entirely. A WM_INPUT raw-input listener captures true deltas ("mr" events)
  alongside the pynput cursor stream.
- F10 stops the session (works while the game is fullscreen); Ctrl+C too.

Usage (owner: use record_gameplay.bat):
  .venv\\Scripts\\python.exe demo_recorder.py --window Roblox --game sols_rng
"""

from __future__ import annotations

import argparse
import ctypes
import json
import queue
import shutil
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

LOCAL_ROOT = Path("data/demo_recordings")
ARCHIVE_ROOT = Path(r"G:\My Drive\Koroki Storage\datasets\limbs_demos")
SCHEMA_VERSION = 1

# Raw mouse deltas aggregate in this window before one "mr" event is emitted —
# gaming mice poll at 1000 Hz; unaggregated logs would be ~3.6M lines/hour.
RAW_DELTA_WINDOW = 0.010
MOVE_MIN_INTERVAL = 0.020  # cursor-position ("mm") throttle


# ── pure helpers (unit-tested in tests/test_demo_recorder.py) ────────────


def even_rect(left: int, top: int, width: int, height: int) -> dict:
    """Clamp capture rect to even dimensions (yuv420p requires them)."""
    return {"left": left, "top": top, "width": width - (width % 2), "height": height - (height % 2)}


def serialize_key(key) -> str:
    """pynput Key/KeyCode → stable string ('w', 'space', 'vk_255')."""
    char = getattr(key, "char", None)
    if char:
        return char
    name = getattr(key, "name", None)
    if name:
        return name
    vk = getattr(key, "vk", None)
    return f"vk_{vk}" if vk is not None else str(key)


class MoveThrottle:
    """Rate-limit cursor-move events to one per MOVE_MIN_INTERVAL."""

    def __init__(self, min_interval: float = MOVE_MIN_INTERVAL):
        self.min_interval = min_interval
        self._last = 0.0

    def accept(self, t: float) -> bool:
        if t - self._last >= self.min_interval:
            self._last = t
            return True
        return False


class DeltaAggregator:
    """Sum raw mouse deltas; flush one event per RAW_DELTA_WINDOW."""

    def __init__(self, window: float = RAW_DELTA_WINDOW):
        self.window = window
        self._dx = 0
        self._dy = 0
        self._last_flush = 0.0
        self._lock = threading.Lock()

    def add(self, dx: int, dy: int, t: float) -> Optional[dict]:
        with self._lock:
            self._dx += dx
            self._dy += dy
            if t - self._last_flush >= self.window:
                return self._flush_locked(t)
        return None

    def flush(self, t: float) -> Optional[dict]:
        with self._lock:
            return self._flush_locked(t)

    def _flush_locked(self, t: float) -> Optional[dict]:
        if self._dx == 0 and self._dy == 0:
            return None
        ev = {"t": t, "e": "mr", "dx": self._dx, "dy": self._dy}
        self._dx = 0
        self._dy = 0
        self._last_flush = t
        return ev


def build_manifest(
    *, game: str, window_title: str, rect: dict, fps: int, codec: str,
    started: float, ended: float, frames: int, events: int, paused_seconds: float,
) -> dict:
    return {
        "schema": SCHEMA_VERSION,
        "game": game,
        "window_title": window_title,
        "rect": rect,
        "fps": fps,
        "codec": codec,
        "started": datetime.fromtimestamp(started).isoformat(timespec="seconds"),
        "ended": datetime.fromtimestamp(ended).isoformat(timespec="seconds"),
        "wall_seconds": round(ended - started, 1),
        "paused_seconds": round(paused_seconds, 1),
        "frames": frames,
        "events": events,
        "coordinate_space": "absolute screen px; normalize with rect",
    }


# ── WM_INPUT raw mouse listener (camera deltas survive pointer lock) ─────

WM_INPUT = 0x00FF
WM_QUIT_MSG = 0x0012
RIM_TYPEMOUSE = 0
RID_INPUT = 0x10000003
RIDEV_INPUTSINK = 0x00000100
MOUSE_MOVE_ABSOLUTE = 0x0001
HWND_MESSAGE = ctypes.c_void_p(-3 & (2**64 - 1))

_user32 = ctypes.windll.user32 if sys.platform == "win32" else None
_LRESULT = ctypes.c_ssize_t
_WNDPROC = ctypes.WINFUNCTYPE(_LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

if _user32 is not None:
    # x64 trap: without explicit prototypes ctypes truncates handles to int32
    # (CreateWindowExW arg 11 OverflowError, live-hit 2026-07-09).
    _user32.CreateWindowExW.restype = wintypes.HWND
    _user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
    ]
    _user32.DefWindowProcW.restype = _LRESULT
    _user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.GetRawInputData.restype = wintypes.UINT
    _user32.GetRawInputData.argtypes = [
        wintypes.LPARAM, wintypes.UINT, ctypes.c_void_p,
        ctypes.POINTER(wintypes.UINT), wintypes.UINT,
    ]


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [
        ("usUsagePage", wintypes.USHORT),
        ("usUsage", wintypes.USHORT),
        ("dwFlags", wintypes.DWORD),
        ("hwndTarget", wintypes.HWND),
    ]


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [
        ("dwType", wintypes.DWORD),
        ("dwSize", wintypes.DWORD),
        ("hDevice", wintypes.HANDLE),
        ("wParam", wintypes.WPARAM),
    ]


class _RAWMOUSE_BUTTONS(ctypes.Structure):
    _fields_ = [("usButtonFlags", wintypes.USHORT), ("usButtonData", wintypes.USHORT)]


class _RAWMOUSE_U(ctypes.Union):
    _fields_ = [("ulButtons", wintypes.ULONG), ("s", _RAWMOUSE_BUTTONS)]


class RAWMOUSE(ctypes.Structure):
    _fields_ = [
        ("usFlags", wintypes.USHORT),
        ("u", _RAWMOUSE_U),
        ("ulRawButtons", wintypes.ULONG),
        ("lLastX", wintypes.LONG),
        ("lLastY", wintypes.LONG),
        ("ulExtraInformation", wintypes.ULONG),
    ]


class RAWINPUT(ctypes.Structure):
    _fields_ = [("header", RAWINPUTHEADER), ("mouse", RAWMOUSE)]


class RawMouseListener:
    """Message-only window registered for raw mouse input (RIDEV_INPUTSINK)."""

    def __init__(self, on_delta):
        self._on_delta = on_delta
        self._thread: Optional[threading.Thread] = None
        self._tid: Optional[int] = None
        self._ready = threading.Event()
        self.ok = False

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="rawmouse", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=3.0)

    def stop(self) -> None:
        if self._tid is not None:
            _user32.PostThreadMessageW(self._tid, WM_QUIT_MSG, 0, 0)
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        kernel32 = ctypes.windll.kernel32
        self._tid = kernel32.GetCurrentThreadId()

        @_WNDPROC
        def wnd_proc(hwnd, msg, wparam, lparam):
            if msg == WM_INPUT:
                self._handle_input(lparam)
            return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [
                ("style", wintypes.UINT), ("lpfnWndProc", _WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
            ]

        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        wc = WNDCLASSW()
        wc.lpfnWndProc = wnd_proc
        wc.lpszClassName = "KorokiDemoRecorderRawInput"
        wc.hInstance = kernel32.GetModuleHandleW(None)
        if not _user32.RegisterClassW(ctypes.byref(wc)):
            self._ready.set()
            return
        hwnd = _user32.CreateWindowExW(
            0, wc.lpszClassName, None, 0, 0, 0, 0, 0, HWND_MESSAGE, None, wc.hInstance, None
        )
        if not hwnd:
            self._ready.set()
            return

        rid = RAWINPUTDEVICE(0x01, 0x02, RIDEV_INPUTSINK, hwnd)  # generic desktop / mouse
        if not _user32.RegisterRawInputDevices(ctypes.byref(rid), 1, ctypes.sizeof(RAWINPUTDEVICE)):
            self._ready.set()
            return

        self.ok = True
        self._ready.set()
        msg = wintypes.MSG()
        while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            _user32.TranslateMessage(ctypes.byref(msg))
            _user32.DispatchMessageW(ctypes.byref(msg))

    def _handle_input(self, lparam) -> None:
        size = wintypes.UINT(0)
        header_size = ctypes.sizeof(RAWINPUTHEADER)
        _user32.GetRawInputData(lparam, RID_INPUT, None, ctypes.byref(size), header_size)
        if size.value == 0:
            return
        buf = ctypes.create_string_buffer(size.value)
        got = _user32.GetRawInputData(lparam, RID_INPUT, buf, ctypes.byref(size), header_size)
        if got != size.value:
            return
        ri = ctypes.cast(buf, ctypes.POINTER(RAWINPUT)).contents
        if ri.header.dwType != RIM_TYPEMOUSE:
            return
        m = ri.mouse
        if (m.usFlags & MOUSE_MOVE_ABSOLUTE) == 0 and (m.lLastX or m.lLastY):
            self._on_delta(int(m.lLastX), int(m.lLastY))


# ── the recorder ─────────────────────────────────────────────────────────


@dataclass
class Session:
    game: str
    window_title: str
    rect: dict
    fps: int
    out_dir: Path
    hwnd: Optional[int] = None  # focus gate target; None = monitor mode, no gate
    codec: str = "?"
    frames: int = 0
    events: int = 0
    paused_seconds: float = 0.0
    started: float = field(default_factory=time.time)


def find_window(title_substring: str) -> Optional[tuple[int, str]]:
    import win32gui

    needle = title_substring.lower()
    hits: list[tuple[int, str]] = []

    def _cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if title and needle in title.lower():
                hits.append((hwnd, title))

    win32gui.EnumWindows(_cb, None)
    return hits[0] if hits else None


def nvenc_available() -> bool:
    try:
        out = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=15
        )
        return "h264_nvenc" in out.stdout
    except Exception:
        return False


def start_ffmpeg(rect: dict, fps: int, out_path: Path, codec: str) -> subprocess.Popen:
    if codec == "h264_nvenc":
        enc = ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "26"]
    else:
        enc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23"]
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "bgra",
        "-s", f"{rect['width']}x{rect['height']}", "-r", str(fps), "-i", "pipe:0",
        *enc, "-pix_fmt", "yuv420p", str(out_path),
    ]
    return subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)


def record(session: Session, duration: Optional[float], archive: bool) -> Path:
    from pynput import keyboard, mouse

    session.out_dir.mkdir(parents=True, exist_ok=True)
    inputs_path = session.out_dir / "inputs.jsonl"
    frames_path = session.out_dir / "frames.jsonl"
    video_path = session.out_dir / "video.mp4"

    stop = threading.Event()
    recording_active = threading.Event()
    recording_active.set()
    ev_queue: "queue.Queue[dict]" = queue.Queue()
    aggregator = DeltaAggregator()
    move_throttle = MoveThrottle()
    down_keys: set[str] = set()

    def emit(ev: dict) -> None:
        # focus gate: while unfocused NOTHING is logged (privacy guarantee)
        if recording_active.is_set():
            ev_queue.put(ev)

    # writer thread
    def writer() -> None:
        with open(inputs_path, "a", encoding="utf-8") as f:
            n = 0
            while not (stop.is_set() and ev_queue.empty()):
                try:
                    ev = ev_queue.get(timeout=0.25)
                except queue.Empty:
                    f.flush()
                    continue
                f.write(json.dumps(ev, separators=(",", ":")) + "\n")
                session.events += 1
                n += 1
                if n % 50 == 0:
                    f.flush()

    # pynput hooks
    def on_press(key):
        if key == keyboard.Key.f10:
            stop.set()
            return
        k = serialize_key(key)
        if k not in down_keys:  # suppress OS auto-repeat
            down_keys.add(k)
            emit({"t": time.time(), "e": "kd", "k": k})

    def on_release(key):
        k = serialize_key(key)
        down_keys.discard(k)
        emit({"t": time.time(), "e": "ku", "k": k})

    def on_move(x, y):
        t = time.time()
        if move_throttle.accept(t):
            emit({"t": t, "e": "mm", "x": x, "y": y})

    def on_click(x, y, button, pressed):
        emit({"t": time.time(), "e": "md" if pressed else "mu", "b": button.name, "x": x, "y": y})

    def on_scroll(x, y, dx, dy):
        emit({"t": time.time(), "e": "sc", "dx": dx, "dy": dy, "x": x, "y": y})

    def on_raw_delta(dx: int, dy: int):
        ev = aggregator.add(dx, dy, time.time())
        if ev:
            emit(ev)

    raw = RawMouseListener(on_raw_delta)
    raw.start()
    if not raw.ok:
        print("[recorder] WARNING: raw mouse listener failed — camera deltas will be missing")

    codec = "h264_nvenc" if nvenc_available() else "libx264"
    proc = start_ffmpeg(session.rect, session.fps, video_path, codec)
    session.codec = codec

    writer_t = threading.Thread(target=writer, name="evwriter", daemon=True)
    writer_t.start()
    kb_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    ms_listener = mouse.Listener(on_move=on_move, on_click=on_click, on_scroll=on_scroll)
    kb_listener.start()
    ms_listener.start()

    import mss

    frame_bytes = session.rect["width"] * session.rect["height"] * 4
    session.started = time.time()  # setup (nvenc probe etc.) doesn't count as wall time
    deadline = time.monotonic() + duration if duration else None
    interval = 1.0 / session.fps
    next_t = time.monotonic()
    pause_started: Optional[float] = None
    print(f"[recorder] session {session.out_dir.name}: {session.rect['width']}x"
          f"{session.rect['height']} @ {session.fps} fps ({codec}). F10 or Ctrl+C stops.")

    try:
        with open(frames_path, "a", encoding="utf-8") as ff, mss.mss() as sct:
            while not stop.is_set():
                now = time.monotonic()
                if deadline and now >= deadline:
                    break
                if now < next_t:
                    time.sleep(min(next_t - now, 0.05))
                    continue
                next_t += interval
                if next_t < now - 1.0:
                    next_t = now  # fell behind (window drag etc.) — resync

                if session.hwnd is not None:
                    focused = _user32.GetForegroundWindow() == session.hwnd
                    if not focused:
                        if pause_started is None:
                            pause_started = time.time()
                            ev_queue.put({"t": pause_started, "e": "pause", "reason": "focus"})
                            recording_active.clear()
                        continue
                    if pause_started is not None:
                        session.paused_seconds += time.time() - pause_started
                        pause_started = None
                        recording_active.set()
                        ev_queue.put({"t": time.time(), "e": "resume"})

                shot = sct.grab(session.rect)
                data = shot.bgra
                if len(data) != frame_bytes:
                    continue  # rect drifted (DPI/monitor change) — skip, keep timeline honest
                if proc.poll() is not None and codec == "h264_nvenc":
                    codec = "libx264"  # nvenc died at runtime — restart on CPU
                    proc = start_ffmpeg(session.rect, session.fps, video_path, codec)
                    session.codec = codec
                    print("[recorder] nvenc failed at runtime, fell back to libx264")
                try:
                    proc.stdin.write(data)
                except (BrokenPipeError, OSError):
                    print("[recorder] ffmpeg pipe broke — stopping")
                    break
                ff.write(json.dumps({"i": session.frames, "t": time.time()},
                                    separators=(",", ":")) + "\n")
                session.frames += 1
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        if pause_started is not None:
            session.paused_seconds += time.time() - pause_started
        tail = aggregator.flush(time.time())
        if tail and recording_active.is_set():
            ev_queue.put(tail)
        kb_listener.stop()
        ms_listener.stop()
        raw.stop()
        writer_t.join(timeout=5.0)
        try:
            proc.stdin.close()
            proc.wait(timeout=20)
        except Exception:
            proc.kill()

    manifest = build_manifest(
        game=session.game, window_title=session.window_title, rect=session.rect,
        fps=session.fps, codec=session.codec, started=session.started, ended=time.time(),
        frames=session.frames, events=session.events, paused_seconds=session.paused_seconds,
    )
    (session.out_dir / "session.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"[recorder] done: {session.frames} frames, {session.events} events, "
          f"{manifest['wall_seconds']}s wall ({manifest['paused_seconds']}s paused)")

    if archive:
        target = ARCHIVE_ROOT / session.out_dir.name
        try:
            ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
            shutil.move(str(session.out_dir), str(target))
            print(f"[recorder] archived to {target}")
            return target
        except Exception as exc:  # Drive offline etc. — keep local, never lose data
            print(f"[recorder] archive to G: failed ({exc}) — session kept at {session.out_dir}")
    return session.out_dir


def main() -> int:
    ap = argparse.ArgumentParser(description="Record gameplay (video + inputs) as limbs corpus.")
    ap.add_argument("--window", default="Roblox",
                    help='window title substring; "" = full primary monitor (NO privacy gate)')
    ap.add_argument("--game", default=None, help="game slug for the session name/manifest")
    ap.add_argument("--fps", type=int, default=10)
    ap.add_argument("--duration", type=float, default=None, help="auto-stop after N seconds")
    ap.add_argument("--no-archive", action="store_true", help="keep session local, skip G: move")
    args = ap.parse_args()

    if args.window:
        hit = find_window(args.window)
        if not hit:
            print(f"[recorder] no visible window matching {args.window!r} — start the game first")
            return 1
        hwnd, title = hit
        import win32gui

        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        rect = even_rect(left, top, right - left, bottom - top)
        if rect["width"] < 64 or rect["height"] < 64:
            print("[recorder] window is minimized — restore it first")
            return 1
    else:
        import mss

        with mss.mss() as sct:
            mon = sct.monitors[1]
        hwnd, title = None, "(primary monitor)"
        rect = even_rect(mon["left"], mon["top"], mon["width"], mon["height"])
        print("[recorder] MONITOR MODE: everything on screen + ALL input is recorded "
              "(no focus gate). Close private windows first.")

    game = args.game or (args.window.lower() if args.window else "desktop")
    name = f"{game}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    session = Session(game=game, window_title=title, rect=rect, fps=args.fps,
                      out_dir=LOCAL_ROOT / name, hwnd=hwnd)
    record(session, duration=args.duration, archive=not args.no_archive)
    return 0


if __name__ == "__main__":
    sys.exit(main())
