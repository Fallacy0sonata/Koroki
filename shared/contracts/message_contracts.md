# Koroki 2.0 — Message API Contracts

Reference document. All payload shapes are enforced by Pydantic schemas
in `services/orchestrator/schemas.py` and by local schemas in each service.

---

## Orchestrator → Client

### POST /v1/chat (request)
```json
{
  "request_id": "uuid-v4-string",
  "message": "string (1..2000 chars)",
  "user_context": {
    "user_id": "string (1..64 chars)",
    "relationship_score": 0,
    "is_owner": false,
    "mode": "auto | casual | roleplay",
    "platform": "discord | desktop",
    "last_summary": "optional string (max 1200 chars)",
    "core_facts": ["optional", "list", "max 20 items"],
    "recent_turns": [
      { "role": "user | assistant", "content": "string (max 2000)" }
    ]
  }
}
```

### POST /v1/chat (response)
```json
{
  "request_id": "uuid-v4-string",
  "text": "Koroki's response text",
  "adapter_used": "owner | tsundere | peasant",
  "has_audio": true,
  "audio_length_bytes": 12345,
  "timings": {
    "request_id": "uuid-v4-string",
    "t_validate_ms": 1.2,
    "t_memory_fetch_ms": 2.1,
    "t_prompt_build_ms": 3.4,
    "t_brain_first_token_ms": 320.5,
    "t_tts_first_chunk_ms": 1850.2,
    "t_total_ms": 2100.8
  }
}
```

---

## POST /v1/stream — SSE Events

Each event is `data: <json>\n\n`. Event types:

| Event        | Payload fields                            |
|--------------|-------------------------------------------|
| `text_chunk` | `{ request_id, text }`                    |
| `tts_chunk`  | `{ request_id, audio_b64 }` (Phase B)     |
| `complete`   | `{ request_id, timings }`                 |
| `error`      | `{ request_id, error }`                   |

---

## Orchestrator → Brain

### POST /v1/generate (request)
```json
{
  "request_id": "uuid",
  "message": "user message",
  "user_context": { ... same as above ... }
}
```

### POST /v1/generate (response)
```json
{
  "request_id": "uuid",
  "text": "generated response",
  "adapter_used": "tsundere"
}
```

### POST /v1/stream — SSE
Same as orchestrator SSE but `text_chunk` events only, no tts_chunk.

---

## Orchestrator → TTS

### POST /v1/synthesize (request)
```json
{
  "request_id": "uuid",
  "text": "text to synthesize (max 4000 chars)",
  "relationship_score": 30
}
```

### POST /v1/synthesize (response)
```
Content-Type: audio/wav
X-Request-ID: <uuid>
X-Voice-Profile: sassy_regal | asmr_flirty
X-Sample-Rate: 24000
<binary WAV data>
```

---

## Adapter Selection Logic

| Condition                      | Adapter   | Voice Profile  |
|-------------------------------|-----------|----------------|
| `is_owner = true`              | owner     | asmr_flirty    |
| `relationship_score >= 50`     | tsundere  | asmr_flirty    |
| `relationship_score < 50`      | peasant   | sassy_regal    |

---

## Day 1 Latency Targets

| Stage              | Target    |
|--------------------|-----------|
| First text token   | < 1500 ms |
| First audio chunk  | < 4000 ms |
| Total              | < 5000 ms |
