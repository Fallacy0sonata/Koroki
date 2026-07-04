"""
Token generation for the brain service.

stream_tokens() runs model.generate() on a background thread
(because HuggingFace generate() is blocking) and yields tokens
to the async caller via a TextIteratorStreamer.

When ML libraries are unavailable (no GPU / dev machine), falls back
to a stub that yields a canned response — so the rest of the pipeline
can still be tested end-to-end.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import TYPE_CHECKING, AsyncIterator

import httpx

from shared.utils.config import get_settings

if TYPE_CHECKING:
    from .adapters import AdapterManager

logger = logging.getLogger("brain.generation")

# ────────────────────────────────────────────────────────────────────
# Day 3 Hardening: VRAM Safety
# ────────────────────────────────────────────────────────────────────
# With the brain back on 4-bit quantization, we can afford a larger prompt window
# without forcing CPU spillover. 2048 restores room for memory/context injection
# while still keeping the cache bounded.
MAX_KV_CACHE_TOKENS = 2048

try:
    from transformers import TextIteratorStreamer

    HAS_ML = True
except ImportError:
    HAS_ML = False

_STUB_RESPONSE = (
    "*tilts head* Hm? Oh— I'm Koroki. My model isn't loaded right now so you're getting the dev stub version of me. "
    "A little embarrassing honestly~ Load a GPU and the real me shows up. 💜"
)

OLLAMA_URL = "http://127.0.0.1:11434/api/generate"


def _prepare_ollama_qwen_prompt(prompt: str, enable_thinking: bool = False) -> str:
    """Prepare ChatML prompt for Ollama's Qwen3.

    When enable_thinking=False (default): appends /no_think to the final user turn
    and seeds an empty <think></think> block so Ollama skips reasoning entirely.
    When enable_thinking=True: leaves the prompt as-is so Ollama activates /think mode.
    """
    marker = "<|im_end|>\n<|im_start|>assistant\n"
    if marker not in prompt:
        return prompt
    prefix, suffix = prompt.rsplit(marker, 1)
    if not enable_thinking:
        if not prefix.rstrip().endswith("/no_think<|im_end|>"):
            prefix = prefix.rstrip()
            if prefix.endswith("<|im_end|>"):
                prefix = prefix[: -len("<|im_end|>")].rstrip() + " /no_think<|im_end|>"
        return f"{prefix}{marker}<think>\n\n</think>\n{suffix}"
    # Thinking enabled — Ollama will generate a think block naturally
    return f"{prefix}{marker}{suffix}"


def _build_bad_words_ids(tokenizer, terms: list[str]) -> list[list[int]]:
    """Convert forbidden words/phrases into token-id sequences for generate()."""
    out: list[list[int]] = []
    for term in terms:
        token_ids = tokenizer.encode(term, add_special_tokens=False)
        if token_ids:
            out.append(token_ids)
    return out


async def stream_tokens(
    adapter_manager: "AdapterManager",
    prompt: str,
    max_new_tokens: int | None = None,
    request_id: str | None = None,
    adapter_name: str | None = None,
    is_owner: bool = False,
    enable_thinking: bool = False,
) -> AsyncIterator[str]:
    """
    Async generator that yields text tokens from the model.
    Caller owns the adapter lock state — call set_adapter() before this.
    """
    settings = get_settings()
    model_cfg = settings["models"]["brain"]
    owner_decode_cfg = model_cfg.get("owner_decode", {})
    owner_path = bool(is_owner) or str(adapter_name or "").strip().lower() == "owner"
    max_new_tokens = max_new_tokens or model_cfg.get("max_new_tokens", 512)
    temperature = float(
        owner_decode_cfg.get("temperature", model_cfg.get("temperature", 0.8))
        if owner_path
        else model_cfg.get("temperature", 0.8)
    )
    top_p = float(
        owner_decode_cfg.get("top_p", model_cfg.get("top_p", 0.9))
        if owner_path
        else model_cfg.get("top_p", 0.9)
    )
    repetition_penalty = float(
        owner_decode_cfg.get("repetition_penalty", model_cfg.get("repetition_penalty", 1.05))
        if owner_path
        else model_cfg.get("repetition_penalty", 1.05)
    )
    do_sample = bool(owner_decode_cfg.get("do_sample", True) if owner_path else True)
    anti_terms = model_cfg.get(
        "anti_assistant_terms",
        [
            "assist",
            "assistance",
            "how may i assist",
            "how can i help",
            "i'd be happy to help",
            "customer service",
        ],
    )

    if adapter_manager.ollama_model_name:
        rid = request_id or "brain_ollama"
        prompt_started = asyncio.get_event_loop().time()
        logger.info(
            "[%s] Brain Ollama generate: model=%s owner_path=%s max_new_tokens=%s",
            rid,
            adapter_manager.ollama_model_name,
            owner_path,
            max_new_tokens,
        )
        payload = {
            "model": adapter_manager.ollama_model_name,
            "prompt": _prepare_ollama_qwen_prompt(prompt, enable_thinking=enable_thinking),
            "raw": True,
            "stream": True,
            "options": {
                "temperature": temperature,
                "top_p": top_p,
                "repeat_penalty": repetition_penalty,
                "repeat_last_n": -1,  # apply penalty across full context, not just last 64 tokens
                "num_predict": max_new_tokens,
            },
        }

        first_token_ms: float | None = None
        token_chunks = 0
        last_token_time: float | None = None
        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream("POST", OLLAMA_URL, json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    data = json.loads(line)
                    token = data.get("response", "")
                    if token:
                        now = asyncio.get_event_loop().time()
                        if first_token_ms is None:
                            first_token_ms = round((now - prompt_started) * 1000.0, 1)
                        last_token_time = now
                        token_chunks += 1
                        yield token
                    if data.get("done"):
                        break

        total_gen_ms = round((asyncio.get_event_loop().time() - prompt_started) * 1000.0, 1)
        tpot_ms: float | None = None
        if first_token_ms is not None and token_chunks > 1 and last_token_time is not None:
            generation_after_first_ms = (last_token_time - prompt_started) * 1000.0 - first_token_ms
            tpot_ms = round(generation_after_first_ms / max(token_chunks - 1, 1), 1)
        logger.info(
            "[%s] Brain Ollama generation: ttft_ms=%s token_chunks=%d tpot_ms=%s total_gen_ms=%s",
            rid,
            first_token_ms,
            token_chunks,
            tpot_ms,
            total_gen_ms,
        )
        return

    # Stub path — no model loaded
    if not HAS_ML or adapter_manager.model is None:
        logger.warning("Brain stub mode: yielding canned response")
        for word in _STUB_RESPONSE.split():
            yield word + " "
            await asyncio.sleep(0.03)
        return

    tokenizer = adapter_manager.tokenizer
    model = adapter_manager.model
    rid = request_id or "brain_local"

    prep_started = asyncio.get_event_loop().time()
    inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
    
    # ─ KV-Cache limiting: truncate prompt if it exceeds MAX_KV_CACHE_TOKENS ─
    input_length = inputs["input_ids"].shape[1]
    truncated = False
    if input_length > MAX_KV_CACHE_TOKENS:
        logger.warning(
            "Input prompt too long (%d tokens) — truncating to %d to protect KV-Cache",
            input_length,
            MAX_KV_CACHE_TOKENS,
        )
        # Re-encode with truncation
        inputs = tokenizer(
            prompt,
            return_tensors="pt",
            max_length=MAX_KV_CACHE_TOKENS,
            truncation=True,
        ).to(model.device)
        input_length = inputs["input_ids"].shape[1]
        truncated = True

    prep_ms = round((asyncio.get_event_loop().time() - prep_started) * 1000.0, 1)
    logger.info(
        "[%s] Brain prep: prompt_tokens=%d truncated=%s prep_ms=%s owner_path=%s temp=%s top_p=%s rep_pen=%s do_sample=%s",
        rid,
        input_length,
        truncated,
        prep_ms,
        owner_path,
        temperature,
        top_p,
        repetition_penalty,
        do_sample,
    )

    streamer = TextIteratorStreamer(
        tokenizer,
        skip_prompt=True,
        skip_special_tokens=True,
    )

    gen_kwargs = {
        **inputs,
        "streamer": streamer,
        "max_new_tokens": max_new_tokens,
        "temperature": temperature,
        "top_p": top_p,
        "do_sample": do_sample,
        "repetition_penalty": repetition_penalty,
        "use_cache": True,  # Set to False if VRAM becomes critical
    }

    # Track 2: persona prefix KV-cache reuse.
    # If the prompt's leading tokens match the cached prefix, deepcopy the cache
    # and hand it to generate() so prefill on those tokens is skipped entirely.
    # On token mismatch (e.g. tokenizer change) we silently fall back to fresh KV.
    prefix_cache_used = False
    try:
        prefix_kv = getattr(adapter_manager, "prefix_cache", None)
        prefix_ids = getattr(adapter_manager, "prefix_token_ids", None)
        if prefix_kv is not None and prefix_ids:
            input_token_list = inputs["input_ids"][0].tolist()
            if len(input_token_list) > len(prefix_ids) and input_token_list[: len(prefix_ids)] == prefix_ids:
                import copy as _copy
                gen_kwargs["past_key_values"] = _copy.deepcopy(prefix_kv)
                prefix_cache_used = True
    except Exception as exc:
        logger.warning("[%s] Prefix cache application failed: %s — using fresh KV", rid, exc)

    # Brute-force suppression for assistant-speak during Day 3 hardening.
    bad_words_ids = _build_bad_words_ids(tokenizer, anti_terms)
    if bad_words_ids:
        gen_kwargs["bad_words_ids"] = bad_words_ids

    logger.info("[%s] Brain gen kwargs: prefix_cache=%s", rid, prefix_cache_used)

    # Run generation on a daemon thread — holds the adapter lock for the duration
    loop = asyncio.get_event_loop()
    token_queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=512)

    def _generate() -> None:
        try:
            with adapter_manager.lock:
                # Re-arm adapter inside the lock on the same thread as generate().
                # PEFT+bnb can silently reset adapter state after a generate() call;
                # setting it here (not in the async handler) ensures it's active.
                if adapter_name and adapter_name != "base" and adapter_manager.has_adapter(adapter_name):
                    adapter_manager.model.set_adapter(adapter_name)
                model.generate(**gen_kwargs)
        except Exception as exc:
            logger.error("Generation thread error: %s", exc)
        finally:
            # Signal end-of-stream
            loop.call_soon_threadsafe(token_queue.put_nowait, None)

    gen_thread = threading.Thread(target=_generate, daemon=True)
    gen_started = asyncio.get_event_loop().time()
    gen_thread.start()

    # Feed streamer output into the async queue
    async def _drain_streamer() -> None:
        for token in streamer:
            await token_queue.put(token)
        await token_queue.put(None)

    drain_task = asyncio.create_task(_drain_streamer())
    first_token_ms: float | None = None
    last_token_time: float | None = None
    token_chunks = 0

    try:
        while True:
            token = await token_queue.get()
            if token is None:
                break
            now = asyncio.get_event_loop().time()
            if first_token_ms is None:
                first_token_ms = round((now - gen_started) * 1000.0, 1)
            last_token_time = now
            token_chunks += 1
            yield token
    finally:
        await drain_task
        total_gen_ms = round((asyncio.get_event_loop().time() - gen_started) * 1000.0, 1)
        tpot_ms: float | None = None
        if first_token_ms is not None and token_chunks > 1 and last_token_time is not None:
            generation_after_first_ms = (last_token_time - gen_started) * 1000.0 - first_token_ms
            tpot_ms = round(generation_after_first_ms / max(token_chunks - 1, 1), 1)
        logger.info(
            "[%s] Brain generation: ttft_ms=%s token_chunks=%d tpot_ms=%s total_gen_ms=%s",
            rid,
            first_token_ms,
            token_chunks,
            tpot_ms,
            total_gen_ms,
        )
        # VRAM hygiene: each request deepcopies the persona prefix KV (~100-250 MB)
        # and grows generation KV; the caching allocator hoards those freed blocks
        # forever (measured: brain resident 2.1 GB at boot → 3.4 GB after a day).
        # Return them to the driver — at single-user cadence the re-malloc cost on
        # the next request is negligible.
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass
