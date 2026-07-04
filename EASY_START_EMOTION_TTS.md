# Koroki Ascension 2.0 — Emotion-Driven TTS & Easy Startup Guide

## Quick Start

### Standard Stack (One Command)
```powershell
.\scripts\easy_start.ps1
```
Starts all services (Orchestrator, Brain, TTS) on Python 3.12.

### Full Stack with IndexTTS Adapter
```powershell
.\scripts\easy_start_full.ps1
```
Starts main stack + separate IndexTTS adapter (Python 3.11) for faster multi-sentence TTS throughput.

### IndexTTS Adapter Only
```powershell
.\scripts\easy_start_tts_adapter.ps1
```
Runs IndexTTS HTTP adapter on port 9000 (requires Python 3.11 venv + model checkpoints).

---

## Emotion-Driven TTS Integration

Koroki's emotional state is now directly fed to the TTS system, allowing voice delivery to match computed emotions. The emotion vector (playfulness, trust, warmth, irritation, etc.) is converted to TTS-friendly tags that shape vocal prosody, tone, and pacing.

### How It Works

1. **Orchestrator computes emotional state** (services/orchestrator/emotions/engine.py)
   - Tracks affect_vector (multi-dimensional emotions)
   - Updates based on user interaction, time, game state
   - Drifts slowly toward baseline to feel human-like

2. **Emotion tags generated** (services/orchestrator/emotions/tts_integration.py)
   - `vector_to_emotion_tags()` maps affect_vector → TTS hints
   - Examples: `[emo:playful2]`, `[emo:caring1]`, `[emo:annoyed3]`
   - Intensity levels 1-4 control voice expression strength

3. **TTS receives emotion tags**
   - Standard TTS (Qwen3-TTS): tags injected via `emotion_hints` in config
   - IndexTTS adapter: tags embedded in synthesis request for emotional prosody control

### Example Flow
```
User: "tell me a sad story"
        ↓
Brain generates: "Here's something melancholic..."
        ↓
Emotion vector: { sadness: 75, trust: 65, curiosity: 40 }
        ↓
TTS tags: [emo:reflective3] [emo:tender1]
        ↓
Voice: slower, softer, melancholic delivery
```

### Configuring Emotion Tags

Edit `services/orchestrator/emotions/tts_integration.py` → `vector_to_emotion_tags()`:
- Adjust thresholds (e.g., "if irritation > 60:" → "if irritation > 50:")
- Add new emotion colors (e.g., "excited", "mysterious")
- Link relationship_score to voice warmth

### Testing Emotion Delivery

```python
from services.orchestrator.emotions.tts_integration import (
    vector_to_emotion_tags,
    emotion_tags_to_prompt_hints
)

# Example: Owner at high relationship, playful mood
affect_vector = {
    "playfulness": 80,
    "warmth": 75,
    "trust": 85,
    "curiosity": 60
}

tags = vector_to_emotion_tags(
    affect_vector=affect_vector,
    mood_state="elevated",
    relationship_score=92
)

hints = emotion_tags_to_prompt_hints(tags)
print(hints)  # ["[emo:affectionate4]", "[emo:playful3]"]
```

---

## Comparing TTS Outputs

### Standard Qwen3-TTS (9880)
- **Pro**: Already integrated, fast, works with current setup
- **Con**: Limited emotional expression control
- **Use**: Discord replies, web responses

### IndexTTS Adapter (9000)
- **Pro**: Significantly better emotion control, multi-sentence batching, artistic prosody
- **Con**: Requires Python 3.11, additional setup (model download)
- **Use**: High-quality voice samples, long-form content, experimental emotional responses

### Side-by-Side Testing

Once IndexTTS models are downloaded:

```bash
# Terminal 1: Start main stack
.\scripts\easy_start.ps1

# Terminal 2: Start IndexTTS (in separate venv)
.\scripts\easy_start_tts_adapter.ps1

# Use orchestrator with emotion tags → both TTS endpoints respond
# Compare outputs via /evaluate endpoint or manual listening
```

---

## Future Additions

- [ ] Real-time voice cloning tuning per emotion state
- [ ] Contradiction injection (soft disagreements) in synthesized voice
- [ ] Micro-expressions (e.g., slight laugh before serious statement)
- [ ] Long-form narrative with natural emotion arcs (crescendo/decrescendo)

---

## Setup Checklist

- [x] Main Python 3.12 venv with Koroki installed
- [ ] Python 3.11 venv for IndexTTS: `py -3.11 -m venv .venv_indextts`
- [ ] IndexTTS models downloaded to `experiments/index-tts/checkpoints/`
- [ ] Test emotion tags: `pytest tests/contract/test_emotion_*.py`
- [ ] Run easy startup: `.\scripts\easy_start.ps1` or `.\scripts\easy_start_full.ps1`
