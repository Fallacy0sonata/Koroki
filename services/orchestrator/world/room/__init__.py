"""
Room subsystem — Koroki's situated environment.

Phase 2D scope (per docs/koroki_subsystem_atlas.md §5):
  - lighting: light level + circadian default + user override
  - ambient:  temperature, humidity, "feel" of the room
  - weather:  outside-the-window state machine (clear/rain/snow/cloudy)
  - identity: Koroki's room aesthetic constants (her canonical purple-lit late-night space)

All four contribute fragments to the felt-state snapshot via interoception.py.
None of them perform LLM-side reasoning — captain reads what the room is, doesn't
decide what it should be.
"""
