"""
Brain service — FastAPI application entry point.

Exposes:
  GET  /health
  GET  /ready
  GET  /version
  POST /v1/generate   — blocking, returns full text
    WS   /ws/stream     — internal WebSocket stream to orchestrator
"""

import json
import logging
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from .adapters import AdapterManager
from .generation import stream_tokens
from .prompt_builder import build_prompt
from .tools import KOROKI_TOOLS, TOOL_NAMES
from services.orchestrator.guards.guillotine import StreamingGuillotine, GuillotineViolation
from shared.utils.config import get_settings

# Ensure logs directory exists
_log_dir = Path(__file__).resolve().parents[1] / "data" / "logs"
_log_dir.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(str(_log_dir / "brain.log"), encoding="utf-8"),
    ],
)
logger = logging.getLogger("brain")

_adapter_manager: AdapterManager | None = None
_start_time = time.monotonic()

_THINK_BLOCK_PATTERN = re.compile(
    r"<\s*(?:think|thinking)\s*>[\s\S]*?<\s*/\s*(?:think|thinking)\s*>",
    re.IGNORECASE,
)
_THINK_UNCLOSED_PATTERN = re.compile(
    r"<\s*(?:think|thinking)\s*>[\s\S]*$",
    re.IGNORECASE,
)
_THINK_TAG_PATTERN = re.compile(r"<\s*/?\s*(?:think|thinking)\s*>", re.IGNORECASE)


def _assistant_phrase_rewrite_enabled() -> bool:
    try:
        return bool(
            get_settings()
            .get("models", {})
            .get("brain", {})
            .get("repair_layers", {})
            .get("assistant_phrase_rewrite_enabled", True)
        )
    except Exception:
        return True


class _ThinkStreamFilter:
    """
    Stateful streaming vault filter — suppresses <think>…</think> reasoning
    blocks across chunk boundaries so no thinking content reaches the user.

    Works even when <think> and </think> span multiple chunks (partial tags
    are buffered until resolved).  Thread-safe for single-writer use.
    """

    _OPEN_RE   = re.compile(r"<\s*(?:think|thinking)\s*>",  re.IGNORECASE)
    _CLOSE_RE  = re.compile(r"<\s*/\s*(?:think|thinking)\s*>", re.IGNORECASE)
    # Detects a possible partial opening tag dangling at the end of a chunk
    _PARTIAL_RE = re.compile(r"<[a-zA-Z/\s]{0,12}$")

    def __init__(self) -> None:
        self._inside:  bool = False
        self._pending: str  = ""

    def feed(self, chunk: str) -> str:
        """
        Process one token chunk.  Returns the text that should be forwarded
        downstream (may be empty if the chunk was inside a think block).
        """
        text   = self._pending + chunk
        self._pending = ""
        output: list[str] = []

        while text:
            if self._inside:
                m = self._CLOSE_RE.search(text)
                if m:
                    # Found the closing tag — exit suppression, continue with remainder
                    self._inside = False
                    text = text[m.end():]
                else:
                    # Still inside; keep last few chars buffered in case the closing
                    # tag spans the next chunk boundary (</thinking> = 11 chars)
                    if len(text) > 14:
                        text = text[-14:]
                    self._pending = text
                    text = ""
            else:
                # Strip orphan </think> close tags (LoRA closes the think block
                # immediately with </think> as its first token — no opening tag).
                close_m = self._CLOSE_RE.search(text)
                if close_m:
                    output.append(text[: close_m.start()])
                    text = text[close_m.end():]
                    continue

                m = self._OPEN_RE.search(text)
                if m:
                    # Opening tag found — save everything before it, then suppress
                    output.append(text[: m.start()])
                    self._inside = True
                    text = text[m.end():]
                else:
                    # No opening tag.  Buffer the tail in case a partial tag spans
                    # chunk boundaries (e.g. "<thi" arriving now, "nk>" next chunk).
                    pm = self._PARTIAL_RE.search(text)
                    if pm:
                        output.append(text[: pm.start()])
                        self._pending = text[pm.start():]
                        text = ""
                    else:
                        output.append(text)
                        text = ""

        return "".join(output)

    @property
    def inside_think(self) -> bool:
        return self._inside


class _ToolCallStreamFilter:
    """
    Stateful streaming filter — suppresses <tool_call>…</tool_call> blocks
    across chunk boundaries so tool call JSON is never forwarded as user-visible
    text.  The raw (unfiltered) chunks must still be accumulated separately so
    _parse_tool_calls() can extract them at stream-complete time.
    """

    _OPEN_RE    = re.compile(r"<tool_call>",  re.IGNORECASE)
    _CLOSE_RE   = re.compile(r"</tool_call>", re.IGNORECASE)
    _PARTIAL_RE = re.compile(r"<[a-zA-Z/_\s]{0,14}$")

    def __init__(self) -> None:
        self._inside:  bool = False
        self._pending: str  = ""

    def feed(self, chunk: str) -> str:
        """Process one token chunk; returns only non-tool-call text."""
        text = self._pending + chunk
        self._pending = ""
        output: list[str] = []

        while text:
            if self._inside:
                m = self._CLOSE_RE.search(text)
                if m:
                    self._inside = False
                    text = text[m.end():]
                else:
                    # Buffer tail to catch closing tag spanning chunk boundary
                    # (</tool_call> = 12 chars; keep a bit more for safety)
                    if len(text) > 15:
                        text = text[-15:]
                    self._pending = text
                    text = ""
            else:
                m = self._OPEN_RE.search(text)
                if m:
                    output.append(text[: m.start()])
                    self._inside = True
                    text = text[m.end():]
                else:
                    pm = self._PARTIAL_RE.search(text)
                    if pm:
                        output.append(text[: pm.start()])
                        self._pending = text[pm.start():]
                        text = ""
                    else:
                        output.append(text)
                        text = ""

        return "".join(output)

    @property
    def inside_tool_call(self) -> bool:
        return self._inside


def _strip_thinking_artifacts(
    text: str,
    normalize_whitespace: bool = True,
    trim_edges: bool = True,
) -> tuple[str, bool]:
    removed = False
    out = text
    # 1. Strip complete <think>…</think> blocks
    no_blocks = _THINK_BLOCK_PATTERN.sub("", out)
    if no_blocks != out:
        removed = True
        out = no_blocks
    # 2. Strip unclosed blocks (token budget truncated generation mid-think)
    no_unclosed = _THINK_UNCLOSED_PATTERN.sub("", out)
    if no_unclosed != out:
        removed = True
        out = no_unclosed
    # 3. Strip any remaining orphan tags
    no_tags = _THINK_TAG_PATTERN.sub("", out)
    if no_tags != out:
        removed = True
        out = no_tags
    if normalize_whitespace:
        out = re.sub(r"\s{2,}", " ", out)
    if trim_edges:
        out = out.strip()
    return out, removed


def _sanitize_assistant_speak(text: str, stream_chunk: bool = False) -> str:
    """
    Day 3 hard guardrail:
    rewrite assistant/corporate phrasing at the final response edge.
    """
    patterns: list[tuple[str, str]] = [
        (r"(?i)\bhow\s+(?:may|might|can)\s+i\s+assist\s+you(?:\s+today|\s+tonight)?\??", "Tell me what you need."),
        (r"(?i)\bi(?:'|\u2019)?d\s+be\s+happy\s+to\s+help\b", "If you insist, I can handle it."),
        (r"(?i)\bcustomer\s+service\b", "errand desk"),
        (r"(?i)\bassistance\b", "support"),
        (r"(?i)\bassist\b", "handle"),
    ]
    out, think_removed = _strip_thinking_artifacts(
        text,
        normalize_whitespace=not stream_chunk,
        trim_edges=not stream_chunk,
    )
    if think_removed:
        logger.warning("Think leak guard removed hidden-thinking artifacts")

    # Never do phrase-level rewrites on streaming chunks; chunks can split words
    # and removing boundary spaces causes glued text in downstream joins.
    if stream_chunk:
        return out

    if not _assistant_phrase_rewrite_enabled():
        return out

    for pattern, replacement in patterns:
        out = re.sub(pattern, replacement, out)
    return out


def _run_startup_warmup(adapter_manager: AdapterManager) -> None:
    cfg = get_settings().get("models", {}).get("brain", {})
    if not bool(cfg.get("startup_warmup_enabled", True)):
        logger.info("Brain warmup disabled by config")
        return

    tokenizer = adapter_manager.tokenizer
    model = adapter_manager.model
    if tokenizer is None or model is None:
        logger.warning("Skipping startup warmup: tokenizer/model unavailable")
        return

    prompt = str(cfg.get("startup_warmup_prompt", "Say hello.")).strip() or "Say hello."
    warmup_tokens = max(1, int(cfg.get("startup_warmup_tokens", 8)))
    requested = cfg.get("startup_warmup_adapters", ["owner"])
    if not isinstance(requested, list):
        requested = ["owner"]

    warm_targets: list[str] = []
    for name in requested:
        adapter_name = str(name).strip()
        if not adapter_name:
            continue
        if adapter_manager.has_adapter(adapter_name):
            warm_targets.append(adapter_name)

    if not warm_targets:
        warm_targets = ["base"]

    logger.info(
        "Brain warmup starting: adapters=%s max_new_tokens=%d",
        warm_targets,
        warmup_tokens,
    )
    warm_started = time.perf_counter()

    try:
        inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
        with adapter_manager.lock:
            for adapter_name in warm_targets:
                adapter_manager.set_adapter(adapter_name)
                model.generate(
                    **inputs,
                    max_new_tokens=warmup_tokens,
                    do_sample=False,
                    use_cache=True,
                )
    except Exception as exc:
        logger.warning("Brain warmup skipped due to error: %s", exc)
        return

    logger.info(
        "Brain warmup complete: adapters=%s elapsed_ms=%.1f",
        warm_targets,
        (time.perf_counter() - warm_started) * 1000.0,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _adapter_manager
    logger.info("Brain service starting — loading model and adapters...")
    _adapter_manager = AdapterManager()
    _adapter_manager.load()
    _run_startup_warmup(_adapter_manager)

    # Explicitly log active attention backend so FA2 activation is visible at startup.
    attention_backend = "unknown"
    try:
        model = _adapter_manager.model if _adapter_manager else None
        config = getattr(model, "config", None)
        attention_backend = (
            getattr(config, "_attn_implementation", None)
            or getattr(config, "attn_implementation", None)
            or "unknown"
        )
    except Exception:
        attention_backend = "unknown"
    logger.info("Brain attention backend: %s", attention_backend)

    app.state.adapter_manager = _adapter_manager
    logger.info("Brain service ready.")
    yield
    logger.info("Brain service shutting down.")


app = FastAPI(title="Koroki Brain", version="2.0.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Request / Response schemas (brain-local, no shared import needed)
# ---------------------------------------------------------------------------

class TurnIn(BaseModel):
    role: str
    content: str


class UserContextIn(BaseModel):
    user_id: str
    relationship_score: int = Field(ge=0, le=100)
    is_owner: bool
    mode: str = "auto"
    platform: str = "discord"
    last_summary: Optional[str] = None
    core_facts: Optional[list[str]] = None
    recent_turns: Optional[list[TurnIn]] = None


# Regex to extract <tool_call>…</tool_call> blocks Qwen3 emits when tools are provided.
_TOOL_CALL_RE = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL)


def _parse_tool_calls(text: str) -> tuple[str, list[dict]]:
    """Extract tool call JSON blocks from generated text.

    Returns (clean_text, tool_calls) where clean_text has the tool_call blocks
    removed and tool_calls is a list of parsed dicts with {name, arguments}.
    Only tool calls matching known TOOL_NAMES are returned; unknown ones are left
    in the text so they don't silently disappear.
    """
    tool_calls: list[dict] = []
    consumed_spans: list[tuple[int, int]] = []

    for m in _TOOL_CALL_RE.finditer(text):
        try:
            parsed = json.loads(m.group(1))
        except (json.JSONDecodeError, ValueError):
            continue
        name = parsed.get("name", "")
        if name not in TOOL_NAMES:
            continue
        tool_calls.append({
            "name": name,
            "arguments": parsed.get("arguments", parsed.get("parameters", {})),
        })
        consumed_spans.append((m.start(), m.end()))

    if not consumed_spans:
        return text, []

    # Rebuild text with tool_call blocks removed
    clean_parts: list[str] = []
    prev = 0
    for start, end in consumed_spans:
        clean_parts.append(text[prev:start])
        prev = end
    clean_parts.append(text[prev:])
    clean_text = re.sub(r"\n{3,}", "\n\n", "".join(clean_parts)).strip()
    return clean_text, tool_calls


class GenerateRequest(BaseModel):
    request_id: str
    message: str = Field(min_length=1, max_length=2000)
    user_context: UserContextIn
    max_new_tokens: Optional[int] = Field(default=None, ge=16, le=512)
    enable_thinking: bool = True
    enable_tools: bool = True
    forbidden_phrases: list[str] = []
    # Phase 1 endocrine integration: natural-language felt-state snapshot from
    # services/orchestrator/body/interoception.py. Injected into system prompt
    # so the captain LLM reads body sensations, not labels. See
    # docs/koroki_subsystem_atlas.md §10 (Felt-State Layer).
    felt_state: Optional[str] = None


# ---------------------------------------------------------------------------
# Health endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "service": "brain",
        "version": "2.0.0",
        "uptime_seconds": round(time.monotonic() - _start_time, 2),
    }


@app.get("/ready")
async def ready() -> dict:
    is_ready = _adapter_manager is not None and _adapter_manager.is_ready()
    return {
        "ready": is_ready,
        "checks": {"model_loaded": is_ready},
        "runtime": {
            "model_name": _adapter_manager.runtime_model_name if _adapter_manager else None,
            "model_profile": _adapter_manager.runtime_model_profile if _adapter_manager else None,
        },
    }


@app.get("/version")
async def version() -> dict:
    return {
        "service": "brain",
        "version": "2.0.0",
        "build": "2.0.0",
        "runtime_model_name": _adapter_manager.runtime_model_name if _adapter_manager else None,
        "runtime_model_profile": _adapter_manager.runtime_model_profile if _adapter_manager else None,
    }


# ---------------------------------------------------------------------------
# Generation endpoints
# ---------------------------------------------------------------------------

def _build_prompt_from_request(req: GenerateRequest, adapter_name: str) -> str:
    uctx = req.user_context
    return build_prompt(
        message=req.message,
        adapter_name=adapter_name,
        is_owner=uctx.is_owner,
        relationship_score=uctx.relationship_score,
        mode=uctx.mode,
        recent_turns=[t.model_dump() for t in uctx.recent_turns] if uctx.recent_turns else None,
        last_summary=uctx.last_summary,
        core_facts=uctx.core_facts,
        tokenizer=_adapter_manager.tokenizer if _adapter_manager else None,
        platform=uctx.platform,
        enable_thinking=req.enable_thinking,
        forbidden_phrases=req.forbidden_phrases or [],
        tools=KOROKI_TOOLS if req.enable_tools else None,
        felt_state=req.felt_state,
    )


@app.post("/v1/generate")
async def generate(req: GenerateRequest) -> dict:
    """Blocking generation — returns complete response text as JSON."""
    if _adapter_manager is None:
        raise HTTPException(status_code=503, detail="Brain not ready")

    uctx = req.user_context
    adapter_name = _adapter_manager.select_adapter(uctx.is_owner, uctx.relationship_score)
    _adapter_manager.set_adapter(adapter_name)

    prompt_build_started = time.perf_counter()
    prompt = _build_prompt_from_request(req, adapter_name)
    prompt_build_ms = round((time.perf_counter() - prompt_build_started) * 1000.0, 1)

    prompt_tokens = None
    try:
        if _adapter_manager and _adapter_manager.tokenizer is not None:
            tok = _adapter_manager.tokenizer(prompt, add_special_tokens=False)
            prompt_tokens = len(tok.get("input_ids", []))
    except Exception:
        prompt_tokens = None

    logger.info(
        "[%s] Brain prompt: build_ms=%s prompt_chars=%d prompt_tokens=%s",
        req.request_id,
        prompt_build_ms,
        len(prompt),
        prompt_tokens,
    )

    full_text = ""
    async for chunk in stream_tokens(
        _adapter_manager,
        prompt,
        max_new_tokens=req.max_new_tokens,
        request_id=req.request_id,
        adapter_name=adapter_name,
        is_owner=uctx.is_owner,
        enable_thinking=req.enable_thinking,
    ):
        full_text += chunk

    sanitized = _sanitize_assistant_speak(full_text.strip())
    clean_text, tool_calls = _parse_tool_calls(sanitized)

    return {
        "request_id": req.request_id,
        "text": clean_text,
        "adapter_used": adapter_name,
        "tool_calls": tool_calls,
    }


@app.websocket("/ws/stream")
async def stream(websocket: WebSocket) -> None:
    """Internal WebSocket token stream. Guillotine runs at the transport edge."""
    await websocket.accept()

    if _adapter_manager is None:
        await websocket.send_json({"type": "error", "error": "brain_not_ready"})
        await websocket.close(code=1013, reason="brain_not_ready")
        return

    try:
        payload = await websocket.receive_json()
        req = GenerateRequest.model_validate(payload)
    except Exception as exc:
        await websocket.send_json({"type": "error", "error": f"bad_request: {exc}"})
        await websocket.close(code=1003, reason="bad_request")
        return

    uctx = req.user_context
    adapter_name = _adapter_manager.select_adapter(uctx.is_owner, uctx.relationship_score)
    _adapter_manager.set_adapter(adapter_name)
    prompt_build_started = time.perf_counter()
    prompt = _build_prompt_from_request(req, adapter_name)
    prompt_build_ms = round((time.perf_counter() - prompt_build_started) * 1000.0, 1)

    prompt_tokens = None
    try:
        if _adapter_manager and _adapter_manager.tokenizer is not None:
            tok = _adapter_manager.tokenizer(prompt, add_special_tokens=False)
            prompt_tokens = len(tok.get("input_ids", []))
    except Exception:
        prompt_tokens = None

    logger.info(
        "[%s] Brain prompt: build_ms=%s prompt_chars=%d prompt_tokens=%s",
        req.request_id,
        prompt_build_ms,
        len(prompt),
        prompt_tokens,
    )
    guillotine = StreamingGuillotine()
    think_filter = _ThinkStreamFilter()
    tool_filter  = _ToolCallStreamFilter()
    streamed_text = ""  # raw accumulated text — includes tool_call blocks for _parse_tool_calls

    try:
        async for chunk in stream_tokens(
            _adapter_manager,
            prompt,
            max_new_tokens=req.max_new_tokens,
            request_id=req.request_id,
            adapter_name=adapter_name,
            is_owner=uctx.is_owner,
            enable_thinking=req.enable_thinking,
        ):
            # Accumulate raw chunk BEFORE any filtering so tool_call blocks are
            # preserved in streamed_text for _parse_tool_calls() at stream end.
            streamed_text += chunk

            # ── Vault filter 1: suppress <think>…</think> reasoning blocks.
            filtered = think_filter.feed(chunk)
            if not filtered:
                continue

            # ── Vault filter 2: suppress <tool_call>…</tool_call> blocks.
            #    Tool call JSON must never appear as visible text in Discord.
            filtered = tool_filter.feed(filtered)
            if not filtered:
                continue

            filtered = _sanitize_assistant_speak(filtered, stream_chunk=True)
            try:
                safe_chunk = guillotine.scan(filtered)
            except GuillotineViolation as violation:
                await websocket.send_json(
                    {
                        "type": "terminate",
                        "code": "GUILLOTINE_STOP_SEQUENCE",
                        "pattern": violation.pattern,
                        "request_id": req.request_id,
                    }
                )
                await websocket.close(code=4409, reason="guillotine_stop_sequence")
                return

            await websocket.send_json(
                {
                    "type": "text_chunk",
                    "text": safe_chunk,
                    "request_id": req.request_id,
                    "adapter_used": adapter_name,
                }
            )

        _, tool_calls = _parse_tool_calls(streamed_text)
        await websocket.send_json({
            "type": "complete",
            "request_id": req.request_id,
            "adapter_used": adapter_name,
            "tool_calls": tool_calls,
        })
        await websocket.close(code=1000)
    except WebSocketDisconnect:
        logger.info("[%s] Orchestrator disconnected from brain stream", req.request_id)


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    svc = settings["services"]["brain"]
    uvicorn.run(
        "services.brain.app:app",
        host=svc["host"],
        port=svc["port"],
        reload=False,
        log_level="info",
    )
