"""Koroki Vision Service — her eyes.

Moondream2 VLM (int4, ~2.5 GB VRAM while looking) behind a small FastAPI adapter
on port 9005. Lazy-load + idle-unload: the model only occupies VRAM for the
seconds she is actually looking at something, so the resident stack keeps its
headroom. Transport-agnostic by design — Discord image attachments today,
VM screen capture for game sessions later (see docs/master_queue.md, Sight).
"""
