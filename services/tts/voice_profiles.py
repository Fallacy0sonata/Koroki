"""
Voice profile selection for TTS synthesis.

Two profiles, selected by relationship_score threshold defined in settings.yaml:
  sultry_sexy_flirty  — low, sultry, seductive tone  (relationship_score >= 50)
  sassy_regal         — sharp, confident, regal style (default / fallback)
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha1

from shared.utils.config import get_settings


@dataclass(frozen=True)
class VoiceProfile:
    name: str
    description: str
    # Placeholder fields for when Qwen3-TTS reference audio / voice tokens are wired in
    speaker_id: str = ""


@dataclass(frozen=True)
class VoiceStyle:
    emotion: str
    intensity: int
    variant: str
    instruct_suffix: str
    temperature_delta: float = 0.0
    top_p_delta: float = 0.0
    repetition_penalty_delta: float = 0.0
    speed_delta: float = 0.0


_PROFILES: dict[str, VoiceProfile] = {
    "sultry_sexy_flirty": VoiceProfile(
        name="sultry_sexy_flirty",
        description="Low, sultry, seductive voice. Used at high relationship scores.",
        speaker_id="sultry_sexy_flirty",
    ),
    "sassy_regal": VoiceProfile(
        name="sassy_regal",
        description="Sharp, confident, regal voice. Default for low relationship scores.",
        speaker_id="sassy_regal",
    ),
}


def get_voice_profile(relationship_score: int) -> VoiceProfile:
    settings = get_settings()
    vp_cfg = settings.get("voice_profiles", {})
    threshold = vp_cfg.get("sultry_sexy_flirty", {}).get("min_relationship_score", 50)

    if relationship_score >= threshold:
        return _PROFILES["sultry_sexy_flirty"]
    return _PROFILES["sassy_regal"]


def list_profiles() -> list[str]:
    return list(_PROFILES.keys())


def build_voice_style(
    request_id: str,
    emotion: str | None = None,
    intensity: int = 50,
    variant: str | None = None,
) -> VoiceStyle:
    normalized_emotion = str(emotion or "neutral").strip().lower() or "neutral"
    clamped_intensity = max(0, min(100, int(intensity)))

    variants_by_emotion: dict[str, tuple[str, ...]] = {
        "neutral": ("neutral_soft", "neutral_regal", "neutral_still"),
        "caring": ("caring_warm", "caring_tender", "caring_gentle"),
        "playful": ("playful_bright", "playful_teasing", "playful_light"),
        "protective": ("protective_firm", "protective_low", "protective_watchful"),
        "curious": ("curious_gentle", "curious_regal", "curious_soft"),
        "thoughtful": ("thoughtful_hushed", "thoughtful_regal", "thoughtful_soft"),
        "whisper": ("whisper_close", "whisper_hushed", "whisper_soft"),
        "sad": ("sad_soft", "sad_breathy", "sad_low"),
        "annoyed": ("annoyed_cool", "annoyed_sharp", "annoyed_flat"),
        "frustrated": ("frustrated_tense", "frustrated_clipped", "frustrated_low"),
        "cold": ("cold_even", "cold_detached", "cold_regal"),
        "tired": ("tired_soft", "tired_sleepy", "tired_low"),
        "proud": ("proud_regal", "proud_poised", "proud_smooth"),
        "firm": ("firm_even", "firm_low", "firm_regal"),
    }
    if normalized_emotion not in variants_by_emotion:
        normalized_emotion = "neutral"

    available_variants = variants_by_emotion[normalized_emotion]
    if variant and variant in available_variants:
        chosen_variant = variant
    else:
        digest = sha1(f"{request_id}:{normalized_emotion}:{clamped_intensity}".encode("utf-8")).hexdigest()
        chosen_variant = available_variants[int(digest[:8], 16) % len(available_variants)]

    suffix_map: dict[str, str] = {
        "neutral_soft": "Speak with soft poise and unhurried elegance.",
        "neutral_regal": "Speak with composed regal balance and steady pacing.",
        "neutral_still": "Speak evenly and calmly, with restrained elegance.",
        "caring_warm": "Let the warmth come through clearly, gentle and close.",
        "caring_tender": "Speak with tender affection and soft reassuring warmth.",
        "caring_gentle": "Keep the tone delicate, calm, and sincerely caring.",
        "playful_bright": "Add a light playful sparkle without becoming bubbly.",
        "playful_teasing": "Use a teasing, amused tone with elegant control.",
        "playful_light": "Keep the tone light and coy, with a faint smile in the voice.",
        "protective_firm": "Sound protective and steady, grounded rather than harsh.",
        "protective_low": "Use a lower, reassuringly firm protective tone.",
        "protective_watchful": "Sound watchful and composed, careful but affectionate.",
        "curious_gentle": "Sound gently curious, attentive, and quietly engaged.",
        "curious_regal": "Use a poised inquisitive tone, elegant and alert.",
        "curious_soft": "Keep the curiosity soft, warm, and lightly wondering.",
        "thoughtful_hushed": "Speak as though turning over a private thought, soft and reflective.",
        "thoughtful_regal": "Keep the tone reflective and composed, like a quiet royal observation.",
        "thoughtful_soft": "Use a soft introspective tone with gentle pauses.",
        "whisper_close": "Speak intimately and softly, close to the listener.",
        "whisper_hushed": "Keep the tone hushed and delicate, almost secretive.",
        "whisper_soft": "Use a soft close whisper-like tone without sounding weak.",
        "sad_soft": "Sound subdued and gentle, with softened emotional weight.",
        "sad_breathy": "Use a slightly breathy, fragile sadness.",
        "sad_low": "Keep the tone low, quiet, and emotionally restrained.",
        "annoyed_cool": "Sound coolly annoyed, controlled and slightly icy.",
        "annoyed_sharp": "Add a sharper edge, clipped but elegant.",
        "annoyed_flat": "Keep the annoyance restrained, flat, and unimpressed.",
        "frustrated_tense": "Carry a restrained tension, like patience wearing thin.",
        "frustrated_clipped": "Use a clipped, taut delivery without shouting.",
        "frustrated_low": "Lower the tone and add pressure, but stay controlled.",
        "cold_even": "Speak with detached calm and emotional distance.",
        "cold_detached": "Keep the delivery distant, measured, and cool.",
        "cold_regal": "Sound aloof and regal, with polished detachment.",
        "tired_soft": "Sound a little tired and soft, slower and gentler.",
        "tired_sleepy": "Use a slightly sleepy softness with smooth pacing.",
        "tired_low": "Lower the energy and keep the tone quiet and worn.",
        "proud_regal": "Sound openly regal and self-assured.",
        "proud_poised": "Keep the voice poised, polished, and proud.",
        "proud_smooth": "Use a smooth, confident elegance with a subtle proud lift.",
        "firm_even": "Speak with measured firmness, composed and unwavering.",
        "firm_low": "Use a lower steady firmness, protective rather than harsh.",
        "firm_regal": "Keep the voice firm, regal, and impeccably controlled.",
    }

    deltas: dict[str, tuple[float, float, float, float]] = {
        "neutral": (0.0, 0.0, 0.0, 0.0),
        "caring": (-0.04, -0.02, 0.0, -0.03),
        "playful": (0.05, 0.02, -0.01, 0.02),
        "protective": (-0.03, -0.01, 0.02, -0.02),
        "curious": (0.01, 0.02, 0.0, 0.0),
        "thoughtful": (-0.05, -0.03, 0.01, -0.04),
        "whisper": (-0.06, -0.04, 0.02, -0.05),
        "sad": (-0.05, -0.03, 0.02, -0.04),
        "annoyed": (-0.02, -0.02, 0.04, 0.01),
        "frustrated": (-0.01, -0.02, 0.05, 0.01),
        "cold": (-0.06, -0.05, 0.04, -0.01),
        "tired": (-0.07, -0.05, 0.01, -0.06),
        "proud": (-0.01, 0.01, 0.02, 0.0),
        "firm": (-0.02, -0.01, 0.04, -0.01),
    }
    temp_delta, top_p_delta, rep_delta, speed_delta = deltas[normalized_emotion]

    intensity_scale = clamped_intensity / 100.0
    return VoiceStyle(
        emotion=normalized_emotion,
        intensity=clamped_intensity,
        variant=chosen_variant,
        instruct_suffix=suffix_map.get(chosen_variant, suffix_map["neutral_regal"]),
        temperature_delta=temp_delta * intensity_scale,
        top_p_delta=top_p_delta * intensity_scale,
        repetition_penalty_delta=rep_delta * intensity_scale,
        speed_delta=speed_delta * intensity_scale,
    )
