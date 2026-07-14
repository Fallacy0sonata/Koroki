"""Koroki supervisor — she must survive without a human watching.

Probes every service's /health on a loop and revives the dead AND the wedged.
The wedge (2026-07-05: four occurrences in one day) is the case process
monitors miss: the process is alive but its socket accept-loop is dead
(WinError 64), so only an HTTP probe sees it. Revival = kill whatever owns
the port + any process matching the service's command line, then respawn in
a titled console window (same convention as the launcher).

Rules:
  - per-service startup grace (models take minutes to load)
  - N consecutive failed probes before declaring death (no flapping)
  - max restarts/hour per service, then FATAL + stop trying (no crash loops
    chewing the GPU)
  - Discord bot has no /health: liveness = its process exists

Run: started by scripts/launch_koroki.ps1 in its own window, or manually:
  .venv\\Scripts\\python.exe supervisor.py [--mode discord|web|both]
"""
from __future__ import annotations

import argparse
import logging
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent
LOG = ROOT / "data" / "logs" / "supervisor.log"
LOG.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG, encoding="utf-8"), logging.StreamHandler()],
)
log = logging.getLogger("supervisor")

PROBE_INTERVAL_S = 20
FAILS_TO_DECLARE_DEAD = 3
MAX_RESTARTS_PER_HOUR = 3

# Deploy mode (2026-07-06, twice-earned): manual kills during deploys/training
# used to burn the revival budget -> FATAL latch -> silent unwatched services.
# Create this file to pause revivals: empty file = pause ALL services; or write
# space/newline-separated service names to pause only those ("brain voice").
# Delete the file to resume. The supervisor still probes and logs while paused.
PAUSE_FILE = ROOT / "data" / "supervisor_pause"


def _paused_for(name: str) -> bool:
    try:
        if not PAUSE_FILE.exists():
            return False
        names = PAUSE_FILE.read_text(encoding="utf-8").split()
        return not names or name in names
    except Exception:
        return False

VENV = str(ROOT / ".venv" / "Scripts" / "python.exe")
VENV_CV = str(ROOT / ".venv_cosyvoice" / "Scripts" / "python.exe")
VENV_BRAIN2 = str(ROOT / ".venv_brain2" / "Scripts" / "python.exe")


def _brain_python() -> str:
    """OPT-O1: models.brain.engine decides the brain venv — exllamav2 code only
    imports in .venv_brain2. Read once at startup; changing the engine needs a
    supervisor restart (like every other service definition here)."""
    try:
        import yaml

        cfg = yaml.safe_load(
            (ROOT / "config" / "settings.yaml").read_text(encoding="utf-8")
        )
        engine = str(
            cfg.get("models", {}).get("brain", {}).get("engine", "transformers")
        ).strip().lower()
    except Exception as exc:
        log.warning("settings.yaml engine read failed (%s) — assuming transformers", exc)
        engine = "transformers"
    return VENV_BRAIN2 if engine == "exllamav2" else VENV


@dataclass
class Service:
    name: str
    health_url: str | None            # None = process-liveness only
    port: int | None
    cmdline_marker: str               # substring identifying its process
    start_cmd: str                    # full command for `cmd /k`
    grace_s: int = 120                # post-(re)start silence before probing
    fails: int = 0
    last_start_ts: float = field(default_factory=time.time)
    restarts: list[float] = field(default_factory=list)
    fatal: bool = False


def services_for(mode: str) -> list[Service]:
    svcs = [
        Service("brain", "http://127.0.0.1:9881/health", 9881,
                "services.brain.app",
                f'"{_brain_python()}" -m uvicorn services.brain.app:app --host 127.0.0.1 --port 9881 --no-access-log',
                grace_s=240),
        # Bare relative paths, NO quotes: `cmd /k "a" "b"` strips quotes when the
        # command has two quoted args (same bug that killed bot revivals
        # 2026-07-05; voice hit it on its first-ever revival, same night, when
        # the TRT restart exercised this path). cwd is ROOT so relatives work.
        Service("voice", "http://127.0.0.1:9004/health", 9004,
                "cosyvoice\\adapter.py",
                r".venv_cosyvoice\Scripts\python.exe experiments\cosyvoice\adapter.py --port 9004",
                grace_s=240),
        Service("vision", "http://127.0.0.1:9005/health", 9005,
                "services.vision.main",
                f'"{VENV}" -m uvicorn services.vision.main:app --host 127.0.0.1 --port 9005 --no-access-log',
                grace_s=180),
        Service("orchestrator", "http://127.0.0.1:9882/health", 9882,
                "services.orchestrator.app",
                f'"{VENV}" -m uvicorn services.orchestrator.app:app --host 0.0.0.0 --port 9882 --no-access-log',
                grace_s=120),
    ]
    if mode in ("discord", "both"):
        # Single-quoted-arg form: `cmd /k "a" "b"` strips quotes catastrophically
        # (the bot respawned into instant death 3x and tripped FATAL, 2026-07-05).
        # cwd is ROOT, so the script path can be bare.
        svcs.append(Service("discord-bot", None, None, "discord_bot.py",
                            f'"{VENV}" discord_bot.py', grace_s=90))
    return svcs


def _pids_matching(marker: str) -> list[int]:
    """PIDs whose command line contains the marker (python processes only)."""
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
        "ForEach-Object { \"$($_.ProcessId)|$($_.CommandLine)\" }"
    )
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return []
    pids = []
    for line in out.splitlines():
        pid, _, cmd = line.partition("|")
        if marker.lower() in (cmd or "").lower() and pid.strip().isdigit():
            pids.append(int(pid))
    return pids


def _port_owner(port: int) -> int | None:
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True,
                             text=True, timeout=30).stdout
    except Exception:
        return None
    for line in out.splitlines():
        if f":{port} " in line and "LISTENING" in line:
            pid = line.split()[-1]
            if pid.isdigit() and pid != "0":
                return int(pid)
    return None


def _kill(pid: int) -> None:
    subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=30)


def alive(svc: Service) -> bool:
    if svc.health_url:
        try:
            r = httpx.get(svc.health_url, timeout=6.0)
            return r.status_code == 200
        except Exception:
            return False
    return bool(_pids_matching(svc.cmdline_marker))


def restart(svc: Service) -> None:
    now = time.time()
    svc.restarts = [t for t in svc.restarts if now - t < 3600]
    if len(svc.restarts) >= MAX_RESTARTS_PER_HOUR:
        svc.fatal = True
        log.error("FATAL: %s exceeded %d restarts/hour — giving up on it "
                  "(manual intervention needed)", svc.name, MAX_RESTARTS_PER_HOUR)
        return
    svc.restarts.append(now)

    victims = set(_pids_matching(svc.cmdline_marker))
    if svc.port:
        owner = _port_owner(svc.port)
        if owner:
            victims.add(owner)
    for pid in victims:
        log.info("%s: killing pid %d", svc.name, pid)
        _kill(pid)
    time.sleep(2)

    log.info("%s: respawning", svc.name)
    subprocess.Popen(
        f'start "[Koroki] {svc.name} (revived)" cmd /k {svc.start_cmd}',
        shell=True, cwd=str(ROOT),
    )
    svc.last_start_ts = time.time()
    svc.fails = 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="discord", choices=["discord", "web", "both"])
    args = ap.parse_args()
    svcs = services_for(args.mode)
    log.info("supervisor up — watching: %s (probe %ds, %d fails = dead, "
             "max %d restarts/h)", ", ".join(s.name for s in svcs),
             PROBE_INTERVAL_S, FAILS_TO_DECLARE_DEAD, MAX_RESTARTS_PER_HOUR)

    while True:
        time.sleep(PROBE_INTERVAL_S)
        for svc in svcs:
            if time.time() - svc.last_start_ts < svc.grace_s:
                continue  # still booting — leave it alone
            if alive(svc):
                if svc.fatal:
                    # It came back (manual fix / deploy finished) — self-clear
                    # the latch instead of ignoring the service forever.
                    log.error("%s: alive again — clearing FATAL latch", svc.name)
                    svc.fatal = False
                    svc.restarts = []
                if svc.fails:
                    log.info("%s: recovered on its own", svc.name)
                svc.fails = 0
                continue
            svc.fails += 1
            if _paused_for(svc.name):
                if svc.fails == FAILS_TO_DECLARE_DEAD or svc.fails % 15 == 0:
                    log.warning("%s: DOWN but PAUSED (deploy mode, %s exists) — "
                                "not reviving", svc.name, PAUSE_FILE.name)
                continue
            if svc.fatal:
                # Never go silent: a latched-dead service screams every ~5 min.
                if svc.fails % 15 == 0:
                    log.error("%s: still DOWN and FATAL-latched — manual action "
                              "needed (it self-clears if you fix it)", svc.name)
                continue
            log.warning("%s: probe failed (%d/%d)", svc.name, svc.fails,
                        FAILS_TO_DECLARE_DEAD)
            if svc.fails >= FAILS_TO_DECLARE_DEAD:
                log.warning("%s: declaring dead/wedged — reviving", svc.name)
                restart(svc)


if __name__ == "__main__":
    main()
