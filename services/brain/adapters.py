"""
Adapter manager for the brain service.

Loads all three LoRA adapters (owner / tsundere / peasant) at startup
into a single PeftModel so runtime swapping is a cheap set_adapter() call
with no re-loading from disk.

Adapter selection logic:
  is_owner=True               → owner
  relationship_score >= 50    → tsundere
  else                        → peasant

4-bit quantization is gated behind the settings.yaml flag
models.brain.load_in_4bit. Phase 7 requires this to stay enabled on a 12GB GPU,
otherwise Brain starves TTS and the whole Discord pipeline stalls.
"""

from __future__ import annotations

import logging
import threading
import importlib
import os
from pathlib import Path

from huggingface_hub import snapshot_download as _hf_snapshot_download
from shared.utils.config import get_settings

logger = logging.getLogger("brain.adapters")

# Tier-specific ego neuron dampening targets from post-LoRA profiling.
# These replace the old blanket hook strategy with surgical targets only.

OWNER_DAMPENING_TARGETS: dict[int, list[int]] = {
    12: [1824, 1874, 545, 1374, 702, 1631, 1540, 1143, 1181, 866],
    18: [1824, 1631, 545, 1540, 1985, 1619, 702, 1480, 1819, 1219],
}

TSUNDERE_DAMPENING_TARGETS: dict[int, list[int]] = {
    12: [1874, 1824, 545, 1374, 1631, 1540, 702, 866, 1670, 1143],
    18: [1824, 1631, 545, 1540, 1985, 702, 1619, 1819, 1219, 1091],
}

PEASANT_DAMPENING_TARGETS: dict[int, list[int]] = {
    12: [1824, 1874, 545, 1374, 1540, 1631, 702, 866, 1181, 1143],
    18: [1824, 1631, 545, 1540, 1985, 702, 1619, 1480, 1117, 440],
}

# Default fallback (currently Owner until all tiers are profiled)
EGO_NEURON_TARGETS = OWNER_DAMPENING_TARGETS

try:
    torch = importlib.import_module("torch")
    transformers_mod = importlib.import_module("transformers")
    peft_mod = importlib.import_module("peft")

    AutoModelForCausalLM = transformers_mod.AutoModelForCausalLM
    AutoTokenizer = transformers_mod.AutoTokenizer
    BitsAndBytesConfig = transformers_mod.BitsAndBytesConfig
    PeftModel = peft_mod.PeftModel
    HAS_ML = True
except Exception:
    logger.warning("ML libraries not available — brain will run in stub mode")
    HAS_ML = False


class AdapterManager:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._model = None
        self._tokenizer = None
        self._current_adapter: str | None = None
        self._lock = threading.Lock()
        self._adapter_names: list[str] = []
        self._runtime_model_name: str | None = None
        self._runtime_model_profile: str | None = None
        self._ollama_model_name: str | None = None
        # Track 2: prefix cache — pre-filled KV state for the static persona prompt.
        # Populated by _prepare_prefix_cache() after model load.
        self._prefix_cache = None
        self._prefix_token_ids: list[int] | None = None

    def _resolve_model_name(self, model_cfg: dict) -> tuple[str, str]:
        """Resolve model name from env override, profile map, or legacy `name` key."""
        env_override = os.environ.get("BRAIN_MODEL_OVERRIDE", "").strip()
        if env_override:
            return env_override, "env_override"

        profile = os.environ.get("BRAIN_MODEL_PROFILE", "").strip() or str(
            model_cfg.get("model_profile", "production")
        )
        profiles = model_cfg.get("model_profiles", {})
        if isinstance(profiles, dict):
            profiled = str(profiles.get(profile, "")).strip()
            if profiled:
                return profiled, profile

        legacy = str(model_cfg.get("name", "")).strip()
        if not legacy:
            raise ValueError("models.brain.name is empty and no model profile/override resolved")
        return legacy, "legacy_name"

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    def load(self) -> None:
        ollama_model = os.environ.get("BRAIN_OLLAMA_MODEL", "").strip()
        if ollama_model:
            self._ollama_model_name = ollama_model
            self._runtime_model_name = ollama_model
            self._runtime_model_profile = "ollama_proxy"
            logger.info("Brain configured for Ollama runtime: %s", ollama_model)
            return

        if not HAS_ML:
            logger.warning("Skipping model load: ML libraries unavailable (stub mode)")
            return

        model_cfg = self._settings["models"]["brain"]
        adapter_cfg = self._settings["adapters"]
        adapters_enabled = bool(adapter_cfg.get("enabled", True))
        model_name, resolved_profile = self._resolve_model_name(model_cfg)
        self._runtime_model_name = model_name
        self._runtime_model_profile = resolved_profile

        logger.info(
            "Brain model resolved: name=%s profile=%s",
            model_name,
            resolved_profile,
        )

        # Resolve HF model ID → local snapshot path. Passing a directory path makes
        # AutoTokenizer treat it as local (_is_local=True), bypassing the is_base_mistral()
        # network check that fails when HF_HUB_OFFLINE=1.
        try:
            model_path = _hf_snapshot_download(model_name, local_files_only=True)
            logger.info("Resolved model to local snapshot: %s", model_path)
        except Exception:
            model_path = model_name

        logger.info("Loading tokenizer: %s", model_path)
        self._tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True)

        # --- Build load kwargs ---
        load_kwargs: dict = {"device_map": "auto", "low_cpu_mem_usage": True}

        # Keep headroom for the co-resident TTS model on single-GPU setups.
        # Without an explicit cap, Accelerate may reserve ~90% VRAM for Brain,
        # which can starve TTS and cause massive synthesis latency spikes.
        max_memory_gib = model_cfg.get("max_memory_gib")
        if torch.cuda.is_available() and isinstance(max_memory_gib, (int, float)) and max_memory_gib > 0:
            mem_gib = round(float(max_memory_gib), 2)
            load_kwargs["max_memory"] = {0: f"{mem_gib}GiB", "cpu": "48GiB"}
            logger.info(
                "Brain max_memory cap enabled: %s GiB on GPU 0 (preserve VRAM for TTS)",
                mem_gib,
            )

        if model_cfg.get("load_in_4bit", False):
            # Use Flash Attention 2 if available; reduces VRAM and speeds up attention.
            try:
                import flash_attn  # noqa: F401
                load_kwargs["attn_implementation"] = "flash_attention_2"
                logger.info("Flash Attention 2 enabled (flash_attn %s)", flash_attn.__version__)
            except ImportError:
                logger.info("flash_attn not installed — using default attention")

            load_kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_compute_dtype=torch.float16,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_use_double_quant=True,
            )
            logger.info("Loading brain model in 4-bit quantization (NF4)")
        else:
            load_kwargs["dtype"] = torch.float16
            logger.info("Loading brain model in float16")

        load_kwargs["local_files_only"] = True
        base_model = AutoModelForCausalLM.from_pretrained(model_path, **load_kwargs)

        if not adapters_enabled:
            self._model = base_model
            self._model.eval()
            logger.info("Adapter loading disabled by config; running base model only")
            return

        # --- Load all adapters at startup ---
        profiles: dict[str, str] = adapter_cfg.get("profiles", {})
        first = True

        _repo_root = Path(__file__).resolve().parents[2]
        for adapter_name, adapter_path_str in profiles.items():
            adapter_path = Path(adapter_path_str)
            if not adapter_path.is_absolute():
                adapter_path = _repo_root / adapter_path_str

            if not adapter_path.exists():
                logger.warning(
                    "Adapter '%s' not found at %s — skipping", adapter_name, adapter_path
                )
                continue

            try:
                if first:
                    self._model = PeftModel.from_pretrained(
                        base_model, str(adapter_path), adapter_name=adapter_name
                    )
                    first = False
                else:
                    self._model.load_adapter(str(adapter_path), adapter_name=adapter_name)

                self._adapter_names.append(adapter_name)
                logger.info("Loaded adapter '%s' from %s", adapter_name, adapter_path)
            except Exception as exc:
                # Staging model A/B can intentionally use a different base model where
                # legacy adapters are incompatible. Skip bad adapters and continue.
                logger.warning(
                    "Adapter '%s' failed to load on model %s: %s (continuing without it)",
                    adapter_name,
                    model_name,
                    exc,
                )

        if self._model is None:
            logger.warning("No adapters loaded — using base model directly")
            self._model = base_model

        self._model.eval()
        logger.info("Brain ready. Adapters: %s", self._adapter_names)

        # Track 2: pre-fill KV cache for the static persona prefix.
        # Saves ~70-80ms of prefill compute per request. Non-fatal if it fails.
        self._prepare_prefix_cache()

    def _prepare_prefix_cache(self) -> None:
        """Pre-compute KV cache for the static system-prompt prefix.

        The chat template wraps every system prompt as:
            <|im_start|>system\\n{_KOROKI_AGENT_CORE}\\n\\n{rel_line}{...}<|im_end|>...

        The leading portion (`<|im_start|>system\\n{_KOROKI_AGENT_CORE}`) is identical
        across every request. Its KV state is computed once at load time and reused
        per request, eliminating ~200 tokens of prefill compute every turn.

        Safety: per-request prompts are token-prefix-checked against the cached prefix
        IDs before the cache is applied. On mismatch we fall back to fresh KV compute.
        """
        if self._model is None or self._tokenizer is None:
            return

        cfg = self._settings.get("models", {}).get("brain", {}).get("prefix_cache", {})
        if not cfg.get("enabled", False):
            logger.info("Prefix cache disabled via config")
            return

        try:
            from .prompt_builder import _KOROKI_AGENT_CORE
            prefix_str = f"<|im_start|>system\n{_KOROKI_AGENT_CORE}"

            inputs = self._tokenizer(
                prefix_str, return_tensors="pt", add_special_tokens=False
            ).to(self._model.device)
            prefix_ids = inputs["input_ids"][0].tolist()

            with torch.no_grad():
                outputs = self._model(**inputs, use_cache=True)

            self._prefix_cache = outputs.past_key_values
            self._prefix_token_ids = prefix_ids
            logger.info(
                "Prefix cache prepared: %d tokens (saves prefill compute per request)",
                len(prefix_ids),
            )
        except Exception as exc:
            logger.warning("Prefix cache preparation failed: %s — disabled for session", exc)
            self._prefix_cache = None
            self._prefix_token_ids = None

    @property
    def prefix_cache(self):
        """Read-only access to the pre-filled prefix KV cache. None when disabled."""
        return self._prefix_cache

    @property
    def prefix_token_ids(self) -> list[int] | None:
        """Token IDs of the cached prefix, for prompt prefix validation."""
        return self._prefix_token_ids

    # ------------------------------------------------------------------
    # Runtime adapter selection
    # ------------------------------------------------------------------

    def select_adapter(self, _is_owner: bool, _relationship_score: int) -> str:
        if not self._adapter_names:
            return "base"
        if "koroki_4b" in self._adapter_names:
            return "koroki_4b"
        if "koroki_v1" in self._adapter_names:
            return "koroki_v1"
        return "base"

    def set_adapter(self, adapter_name: str) -> None:
        """
        Hot-swap to the named adapter.
        CRITICAL: This operation is O(1) and does NOT move adapters to/from CPU.
        It simply changes which adapter's weights are used in the forward pass.
        """
        if self._model is None or not hasattr(self._model, "set_adapter"):
            return
        if not self._adapter_names or adapter_name == "base":
            self._current_adapter = "base"
            return
        if adapter_name not in self._adapter_names:
            logger.warning("Adapter '%s' not loaded — skipping swap", adapter_name)
            return
        prev = self._current_adapter
        self._model.set_adapter(adapter_name)
        self._current_adapter = adapter_name
        if prev != adapter_name:
            logger.info("Adapter swap: %s → %s", prev, adapter_name)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def model(self):
        return self._model

    @property
    def tokenizer(self):
        return self._tokenizer

    @property
    def lock(self) -> threading.Lock:
        return self._lock

    def get_hook_targets(self) -> dict[int, list[int]]:
        settings_targets = (
            self._settings
            .get("features", {})
            .get("h_neurons", {})
            .get("profiled_targets")
        )
        if isinstance(settings_targets, dict) and settings_targets:
            return {int(layer): [int(neuron) for neuron in neurons] for layer, neurons in settings_targets.items()}
        return EGO_NEURON_TARGETS

    def is_ready(self) -> bool:
        if self._ollama_model_name:
            return True
        if not HAS_ML:
            return False
        return self._model is not None and self._tokenizer is not None

    @property
    def ollama_model_name(self) -> str | None:
        return self._ollama_model_name

    def has_adapter(self, adapter_name: str) -> bool:
        return str(adapter_name).strip() in self._adapter_names

    @property
    def loaded_adapters(self) -> tuple[str, ...]:
        return tuple(self._adapter_names)

    @property
    def runtime_model_name(self) -> str | None:
        return self._runtime_model_name

    @property
    def runtime_model_profile(self) -> str | None:
        return self._runtime_model_profile
