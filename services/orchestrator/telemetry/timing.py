import time
import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Optional

logger = logging.getLogger("orchestrator.telemetry")


@dataclass
class RequestTiming:
    """
    Tracks per-stage latency for a single pipeline execution.

    Call mark("stage_name") after each stage completes.
    Call finalize() at the end to emit the JSON log and get the full dict.

    Stage names map to t_<name>_ms fields. All times are milliseconds
    relative to when this object was instantiated.
    """

    request_id: str
    _t_start: float = field(default_factory=time.monotonic, repr=False)

    t_validate_ms: Optional[float] = None
    t_memory_fetch_ms: Optional[float] = None
    t_cognitive_observe_ms: Optional[float] = None
    t_cognitive_evaluate_ms: Optional[float] = None
    t_cognitive_plan_ms: Optional[float] = None
    t_cognitive_reflect_ms: Optional[float] = None
    t_prompt_build_ms: Optional[float] = None
    t_brain_first_token_ms: Optional[float] = None
    t_tts_first_chunk_ms: Optional[float] = None
    m_cognitive_coherence: Optional[float] = None
    m_affect_stability: Optional[float] = None
    m_memory_coherence: Optional[float] = None
    m_intent_strength: Optional[float] = None
    m_attention_entropy: Optional[float] = None
    m_initiative_drive: Optional[float] = None
    m_reflection_score: Optional[float] = None
    m_proactive_eligible: Optional[bool] = None
    m_proactive_cooldown_s: Optional[int] = None
    tbt_avg_ms: Optional[float] = None
    tbt_max_ms: Optional[float] = None
    token_count: int = 0
    t_total_ms: Optional[float] = None
    _last_token_time: Optional[float] = field(default=None, repr=False)
    _token_gaps_ms: list[float] = field(default_factory=list, repr=False)

    def mark(self, stage: str) -> float:
        """Record elapsed time for a named stage. Returns the recorded ms value."""
        elapsed_ms = round((time.monotonic() - self._t_start) * 1000, 2)
        field_name = f"t_{stage}_ms"
        if hasattr(self, field_name):
            setattr(self, field_name, elapsed_ms)
        else:
            logger.warning("RequestTiming: unknown stage %r — field not stored", stage)
        return elapsed_ms

    def mark_token(self) -> float:
        """Record token arrival for TTFT/TBT metrics. Returns elapsed ms from request start."""
        now = time.monotonic()
        elapsed_ms = round((now - self._t_start) * 1000, 2)
        if self._last_token_time is None:
            self.t_brain_first_token_ms = elapsed_ms
        else:
            gap_ms = round((now - self._last_token_time) * 1000, 2)
            self._token_gaps_ms.append(gap_ms)
        self._last_token_time = now
        self.token_count += 1
        return elapsed_ms

    def set_cognitive_metrics(
        self,
        *,
        coherence: float,
        affect_stability: float,
        memory_coherence: float,
        intent_strength: float,
        attention_entropy: float,
        initiative_drive: float,
        reflection_score: float,
        proactive_eligible: bool,
        proactive_cooldown_s: int,
    ) -> None:
        self.m_cognitive_coherence = round(float(coherence), 4)
        self.m_affect_stability = round(float(affect_stability), 4)
        self.m_memory_coherence = round(float(memory_coherence), 4)
        self.m_intent_strength = round(float(intent_strength), 4)
        self.m_attention_entropy = round(float(attention_entropy), 4)
        self.m_initiative_drive = round(float(initiative_drive), 4)
        self.m_reflection_score = round(float(reflection_score), 4)
        self.m_proactive_eligible = bool(proactive_eligible)
        self.m_proactive_cooldown_s = int(proactive_cooldown_s)

    def finalize(self) -> dict:
        """Set t_total_ms, emit a JSON log line, and return the full timing dict."""
        if self._token_gaps_ms:
            self.tbt_avg_ms = round(sum(self._token_gaps_ms) / len(self._token_gaps_ms), 2)
            self.tbt_max_ms = round(max(self._token_gaps_ms), 2)
        self.t_total_ms = round((time.monotonic() - self._t_start) * 1000, 2)
        payload = {k: v for k, v in asdict(self).items() if not k.startswith("_")}
        if telemetry_enabled():
            logger.info("TIMING %s", json.dumps(payload))
        return payload


def telemetry_enabled() -> bool:
    try:
        from shared.utils.config import get_settings
        return get_settings().get("telemetry", {}).get("enabled", True)
    except Exception:
        return True
