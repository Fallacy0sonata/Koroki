import asyncio
import logging
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .routes import auth, health, chat, stream, voice, log, autonomy, games, singing, preference, presence, world
from .autonomy.scheduler import autonomy_scheduler
from .body.heartbeat import run_heartbeat_loop
from .mind.activities import run_activity_loop
from .thought_generator import run_thought_loop
from .world.events import run_world_events_loop
from .mood_modifiers import apply_startup_modifier
from .nervous_system import run_loop as run_nervous_system_loop
from .telemetry.trace_context import set_request_id

# Ensure logs directory exists
_repo_root = Path(__file__).resolve().parents[2]
_log_dir = _repo_root / "data" / "logs"
_log_dir.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(str(_log_dir / "orchestrator.log"), encoding="utf-8"),
    ],
)
logger = logging.getLogger("orchestrator")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Orchestrator 2.0 starting up")
    apply_startup_modifier()
    autonomy_scheduler.start()
    thought_task = asyncio.create_task(run_thought_loop())
    ns_task = asyncio.create_task(run_nervous_system_loop())
    activity_task = asyncio.create_task(run_activity_loop())
    world_events_task = asyncio.create_task(run_world_events_loop())
    # Autonomic heartbeat — body dynamics advance even when nobody talks to her
    # (2026-07-03 night-one bug: ticks were chat-driven only; see body/heartbeat.py).
    heartbeat_task = asyncio.create_task(run_heartbeat_loop())
    # Sleep VRAM offload — her voice unloads while she sleeps, reloads at waking
    # ("like an actual human" — owner 2026-07-04; flag: sleep.vram_offload).
    try:
        from .body.sleep_offload import install as _install_sleep_offload
        _install_sleep_offload()
    except Exception:
        logger.warning("sleep offload install failed", exc_info=True)

    # OPT-O4 (2026-07-05): warm the memory-recall embedder off the request path.
    # Lazy first-use load cost 12.9 s INSIDE the first chat request after every
    # orchestrator boot (measured: "Recalled memories injected" gap 12.85 s cold
    # vs 31 ms warm). A background thread eats that cost at startup instead.
    def _warm_embedder() -> None:
        try:
            from .mind.embeddings import get_embedder

            emb = get_embedder()
            if emb.embed_query("warmup") is not None:
                logger.info("Recall embedder warmed at startup")
            else:
                logger.info("Recall embedder unavailable — recall runs degraded")
        except Exception:
            logger.warning("Recall embedder warmup failed", exc_info=True)

    threading.Thread(target=_warm_embedder, daemon=True, name="embedder-warmup").start()
    yield
    thought_task.cancel()
    ns_task.cancel()
    activity_task.cancel()
    world_events_task.cancel()
    heartbeat_task.cancel()
    await autonomy_scheduler.stop()
    logger.info("Orchestrator shutting down")


app = FastAPI(
    title="Koroki Orchestrator",
    version="2.0.0",
    description="Single entry point for the Koroki AI pipeline.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id
    set_request_id(request_id)
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


app.include_router(health.router, tags=["health"])
app.include_router(auth.router, prefix="/v1", tags=["auth"])
app.include_router(chat.router, prefix="/v1", tags=["chat"])
app.include_router(stream.router, prefix="/v1", tags=["stream"])
app.include_router(voice.router, prefix="/v1", tags=["voice"])
app.include_router(log.router, prefix="/v1", tags=["logs"])
app.include_router(autonomy.router, prefix="/v1", tags=["autonomy"])
app.include_router(games.router, prefix="/v1", tags=["games"])
app.include_router(singing.router, prefix="/v1", tags=["singing"])
app.include_router(preference.router, prefix="/v1", tags=["preference"])
app.include_router(presence.router, prefix="/v1", tags=["presence"])
app.include_router(world.router, prefix="/v1", tags=["world"])

_repo_root = Path(__file__).resolve().parents[2]
_web_dir = _repo_root / "clients" / "web"
_assets_dir = _repo_root / "assets"

if _assets_dir.exists():
    app.mount("/assets", StaticFiles(directory=str(_assets_dir)), name="assets")

if _web_dir.exists():
    app.mount("/", StaticFiles(directory=str(_web_dir), html=True), name="web")


if __name__ == "__main__":
    import uvicorn
    from shared.utils.config import get_settings

    settings = get_settings()
    svc = settings["services"]["orchestrator"]
    uvicorn.run(
        "services.orchestrator.app:app",
        host=svc["host"],
        port=svc["port"],
        reload=False,
        log_level="info",
    )
