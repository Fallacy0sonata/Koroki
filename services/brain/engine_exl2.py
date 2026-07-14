"""ExLlamaV2 generation engine for the brain service (OPT-O1, 2026-07-05).

Runs an EXL2-quantized Qwen3 with the koroki LoRA permanently attached.
Requires the .venv_brain2 interpreter (torch 2.9 + exllamav2 0.3.2); the
transformers engine in the main .venv stays available as the rollback path.
Selection: models.brain.engine in settings.yaml (or BRAIN_ENGINE env).

Measured on the 4070 Ti vs transformers+bnb NF4 (Qwen3-4B):
TTFT 0.7-5.7 s -> 0.02-0.12 s warm, ~54 tok/s with LoRA attached.

Traps this module encodes (do not "clean up"):
  - config.no_sdpa = True is REQUIRED: without flash-attn, exllamav2 0.3.2
    falls back to torch SDPA, which emits degenerate token loops for Qwen3
    on Windows/torch 2.9. The matmul fallback is correct and measured faster.
  - tokenizer.encode needs encode_special_tokens=True or ChatML markers are
    tokenized as literal text and the model outputs garbage.
  - Never call generator.set_loras(None) after attaching: 0.3.2 stores [],
    and embedding.forward indexes loras[0] -> IndexError. The LoRA stays
    attached for the process lifetime (single-persona production reality).
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Iterator

logger = logging.getLogger("brain.engine_exl2")


class ExLlamaV2Engine:
    """Owns the EXL2 model, cache, tokenizer, dynamic generator, and LoRA."""

    def __init__(
        self,
        model_dir: str,
        lora_dir: str | None = None,
        lora_name: str = "koroki_4b",
        max_seq_len: int = 3072,
    ) -> None:
        self.model_dir = str(model_dir)
        self.lora_dir = str(lora_dir) if lora_dir else None
        self.lora_name = lora_name
        self.max_seq_len = int(max_seq_len)
        self._lock = threading.Lock()
        self._model = None
        self._cache = None
        self._tokenizer = None
        self._generator = None
        self._lora = None
        self._stop_conditions: list = []
        self._lmfe_tok_data = None  # lm-format-enforcer tokenizer data, built once

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def load(self) -> None:
        from exllamav2 import (
            ExLlamaV2,
            ExLlamaV2Cache,
            ExLlamaV2Config,
            ExLlamaV2Tokenizer,
        )
        from exllamav2.generator import ExLlamaV2DynamicGenerator

        started = time.perf_counter()
        config = ExLlamaV2Config(self.model_dir)
        config.max_seq_len = self.max_seq_len
        config.no_sdpa = True  # see module docstring: SDPA path breaks Qwen3 here

        self._model = ExLlamaV2(config)
        self._cache = ExLlamaV2Cache(
            self._model, max_seq_len=self.max_seq_len, lazy=True
        )
        self._model.load_autosplit(self._cache, progress=False)
        self._tokenizer = ExLlamaV2Tokenizer(config)

        # paged=False: no flash-attn on this box; unpaged mode is single-batch,
        # which matches the adapter-lock-serialized brain anyway.
        self._generator = ExLlamaV2DynamicGenerator(
            model=self._model,
            cache=self._cache,
            tokenizer=self._tokenizer,
            paged=False,
        )
        self._generator.warmup()

        self._stop_conditions = [self._tokenizer.eos_token_id, "<|im_end|>"]

        if self.lora_dir:
            self._attach_lora()

        logger.info(
            "ExLlamaV2 engine ready: model=%s lora=%s max_seq_len=%d load_s=%.1f",
            self.model_dir,
            self.lora_name if self._lora is not None else "none",
            self.max_seq_len,
            time.perf_counter() - started,
        )

    def _attach_lora(self) -> None:
        from exllamav2.lora import ExLlamaV2Lora

        lora_path = Path(self.lora_dir)
        if not lora_path.exists():
            logger.warning("EXL2 LoRA dir not found: %s — running base", lora_path)
            return
        try:
            self._lora = ExLlamaV2Lora.from_directory(self._model, str(lora_path))
            self._generator.set_loras([self._lora])
            logger.info("EXL2 LoRA attached: %s (%s)", self.lora_name, lora_path)
        except Exception as exc:
            # e.g. rank-shape mismatch when running the 8B test profile with the
            # 4B-shaped adapter — degrade to base, same policy as the PEFT path.
            self._lora = None
            logger.warning(
                "EXL2 LoRA failed to attach (%s) — running base model: %s",
                lora_path,
                exc,
            )

    @property
    def is_loaded(self) -> bool:
        return self._generator is not None

    @property
    def lora_attached(self) -> bool:
        return self._lora is not None

    @property
    def lock(self) -> threading.Lock:
        return self._lock

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------

    def generate_stream(
        self,
        prompt: str,
        *,
        max_new_tokens: int = 512,
        temperature: float = 0.7,
        top_p: float = 0.8,
        repetition_penalty: float = 1.15,
        do_sample: bool = True,
        banned_strings: list[str] | None = None,
    ) -> Iterator[str]:
        """Blocking token-text generator. Caller runs it on a worker thread.

        Serialized by the engine lock: the unpaged dynamic generator handles
        one job at a time.
        """
        from exllamav2.generator import ExLlamaV2DynamicJob, ExLlamaV2Sampler

        with self._lock:
            ids = self._tokenizer.encode(
                prompt, add_bos=False, encode_special_tokens=True
            )
            budget = self.max_seq_len - max_new_tokens - 8
            if ids.shape[-1] > budget:
                logger.warning(
                    "Prompt too long for EXL2 cache (%d tokens) — truncating to %d",
                    ids.shape[-1],
                    budget,
                )
                ids = ids[:, :budget]

            settings = ExLlamaV2Sampler.Settings(
                temperature=float(temperature),
                top_p=float(top_p),
                token_repetition_penalty=float(repetition_penalty),
            )
            if not do_sample:
                settings.top_k = 1

            job = ExLlamaV2DynamicJob(
                input_ids=ids,
                max_new_tokens=int(max_new_tokens),
                gen_settings=settings,
                stop_conditions=list(self._stop_conditions),
                banned_strings=list(banned_strings) if banned_strings else None,
            )
            self._generator.enqueue(job)
            try:
                while self._generator.num_remaining_jobs():
                    for result in self._generator.iterate():
                        text = result.get("text", "")
                        if text:
                            yield text
            finally:
                # Consumer may abort mid-stream (client disconnect) — never
                # leave a live job in the queue.
                if self._generator.num_remaining_jobs():
                    self._generator.clear_queue()

    def generate_json(
        self,
        prompt: str,
        *,
        json_schema: dict,
        max_new_tokens: int = 256,
        temperature: float = 0.3,
    ) -> str:
        """Schema-locked generation (LIMBS W1.2, the motor-planner's mouth).

        lm-format-enforcer masks logits at every step so the output is valid
        against json_schema BY CONSTRUCTION — a plan that doesn't parse cannot
        be emitted. Non-streaming: a plan is atomic. Callers must build the
        prompt with thinking OFF (a <think> block cannot start under a JSON
        filter). Low temperature: plans want determinism, not flair.
        """
        from exllamav2.generator import ExLlamaV2DynamicJob, ExLlamaV2Sampler
        from lmformatenforcer import JsonSchemaParser
        from lmformatenforcer.integrations.exllamav2 import (
            ExLlamaV2TokenEnforcerFilter,
            build_token_enforcer_tokenizer_data,
        )

        # VERSION-SKEW SHIM (live-hit 2026-07-09): lmfe 0.11's filter targets
        # the old begin/feed/next API and does NOT inherit exllamav2 0.3.2's
        # ExLlamaV2Filter base, whose dynamic generator calls background_* and
        # can_mask_logits on every filter. These are the base-class defaults,
        # inlined verbatim, pinned to the SYNC path (no background worker —
        # lmfe's parser state is not audited for thread-safety).
        class _EnforcerFilter(ExLlamaV2TokenEnforcerFilter):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.background_result = None

            def use_background_worker(self) -> bool:
                return False

            def can_mask_logits(self) -> bool:
                return False

            def prepare_logit_mask(self):
                raise NotImplementedError  # unreachable: can_mask_logits is False

            def background_drop(self):
                if self.background_result is not None:
                    self.background_result.result()
                    self.background_result = None

            def background_next(self, pool):
                assert self.background_result is None
                self.background_result = pool.submit(self.next)

            def background_prepare_logit_mask(self, pool):
                assert self.background_result is None
                self.background_result = pool.submit(self.prepare_logit_mask)

            def get_next(self, mask: bool = False) -> tuple:
                if self.background_result is None:
                    return self.prepare_logit_mask() if mask else self.next()
                r = self.background_result.result()
                self.background_result = None
                return r

        with self._lock:
            if self._lmfe_tok_data is None:
                self._lmfe_tok_data = build_token_enforcer_tokenizer_data(self._tokenizer)
            filt = _EnforcerFilter(
                JsonSchemaParser(json_schema), self._lmfe_tok_data
            )

            ids = self._tokenizer.encode(
                prompt, add_bos=False, encode_special_tokens=True
            )
            budget = self.max_seq_len - max_new_tokens - 8
            if ids.shape[-1] > budget:
                logger.warning(
                    "Planner prompt too long (%d tokens) — truncating to %d",
                    ids.shape[-1], budget,
                )
                ids = ids[:, :budget]

            settings = ExLlamaV2Sampler.Settings(
                temperature=float(temperature),
                top_p=0.9,
                token_repetition_penalty=1.05,
            )
            job = ExLlamaV2DynamicJob(
                input_ids=ids,
                max_new_tokens=int(max_new_tokens),
                gen_settings=settings,
                stop_conditions=list(self._stop_conditions),
                filters=[filt],
                filter_prefer_eos=True,
            )
            self._generator.enqueue(job)
            chunks: list[str] = []
            try:
                while self._generator.num_remaining_jobs():
                    for result in self._generator.iterate():
                        text = result.get("text", "")
                        if text:
                            chunks.append(text)
            finally:
                if self._generator.num_remaining_jobs():
                    self._generator.clear_queue()
            return "".join(chunks)
