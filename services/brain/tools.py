"""
Koroki agent tool definitions — internal and external capabilities Koroki can invoke.

Internal tools (invisible to user, executed by orchestrator):
  set_emotion       — Koroki sets her own emotional state
  store_memory      — Koroki explicitly decides to remember something
  update_relationship — Koroki adjusts her relationship with a user

External tools are handled at the Discord layer (singing, chess) — not defined here.
recall_memory is a two-pass operation (query → inject → generate) — deferred to Phase 2.
"""

KOROKI_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "set_emotion",
            "description": (
                "Set your current emotional state. Use when you feel something specific "
                "that should color your voice — not every response, only when something "
                "genuinely shifts. The emotion affects how you sound, not what you say."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "emotion": {
                        "type": "string",
                        "enum": [
                            "happy", "sad", "angry", "excited", "calm",
                            "shy", "surprised", "gentle", "annoyed", "playful",
                            "nostalgic", "fond", "nervous", "proud",
                        ],
                        "description": "Your emotional state for this response",
                    },
                    "intensity": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                        "description": "How strongly you feel it (1=barely, 100=overwhelming)",
                    },
                },
                "required": ["emotion"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "store_memory",
            "description": (
                "Deliberately remember something about this person or conversation. "
                "Use sparingly — only for things that genuinely matter and should "
                "surface in future sessions. Not a log, a choice."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "What to remember (concise, factual)",
                    },
                    "importance": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                        "description": "How important this is (50=notable, 80+=core memory)",
                    },
                },
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_relationship",
            "description": (
                "Adjust your relationship with this person based on what just happened. "
                "Use only when something genuinely moved the needle — kindness, rudeness, "
                "a real moment of connection or friction. Bounded to ±5 per response."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "delta": {
                        "type": "integer",
                        "minimum": -5,
                        "maximum": 5,
                        "description": "Score change: positive = closer, negative = colder",
                    },
                    "reason": {
                        "type": "string",
                        "description": "One sentence: why the relationship changed",
                    },
                },
                "required": ["delta", "reason"],
            },
        },
    },
]

# Names of all defined tools for quick lookup
TOOL_NAMES: set[str] = {t["function"]["name"] for t in KOROKI_TOOLS}
