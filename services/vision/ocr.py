"""Game-UI OCR — exact text/numbers her VLM eyes can't reliably read.

Born from the 2026-07-05 bench: RapidOCR (CPU, onnx) read 8/8 Paperclips-style
UI metrics at 100% in ~300 ms full-frame, while the moondream path took 31 s
per single question (cold-load-per-ask) and is the documented weak spot for
numbers. Division of labor: OCR reads WHAT the screen says (metrics, labels),
moondream stays for WHAT the screen means (semantics) and WHERE things are
(pointing).

CPU-only — zero VRAM, no game-session coupling, safe to call every look.
"""

from __future__ import annotations

import io
import logging
import re
import threading
import time

logger = logging.getLogger("koroki.vision.ocr")


class OcrEngine:
    """RapidOCR singleton. Lazy-loaded (~1 s first call), thread-serialized."""

    def __init__(self) -> None:
        self._engine = None
        self._lock = threading.Lock()

    def _ensure(self):
        if self._engine is None:
            from rapidocr_onnxruntime import RapidOCR

            t0 = time.time()
            self._engine = RapidOCR()
            logger.info("RapidOCR ready in %.1fs (cpu)", time.time() - t0)
        return self._engine

    def read(self, image: bytes) -> list[dict]:
        """Full-frame OCR. Returns [{text, conf, box:[x0,y0,x1,y1]}, ...]."""
        import numpy as np
        from PIL import Image

        img = Image.open(io.BytesIO(image)).convert("RGB")
        arr = np.array(img)
        with self._lock:
            engine = self._ensure()
            t0 = time.time()
            result, _ = engine(arr)
            dt = (time.time() - t0) * 1000
        lines: list[dict] = []
        for row in result or []:
            box, text, conf = row[0], row[1], float(row[2])
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            lines.append(
                {
                    "text": str(text),
                    "conf": round(conf, 3),
                    "box": [int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))],
                }
            )
        logger.info("ocr: %d lines in %.0fms", len(lines), dt)
        return lines


_INSTANCE: OcrEngine | None = None
_INSTANCE_LOCK = threading.Lock()


def get_ocr() -> OcrEngine:
    global _INSTANCE
    if _INSTANCE is None:
        with _INSTANCE_LOCK:
            if _INSTANCE is None:
                _INSTANCE = OcrEngine()
    return _INSTANCE


# ── metric extraction (pure — unit-tested) ──────────────────────────

_NUM_RE = re.compile(r"[-+]?[$¥€£]?\s?\d[\d,.]*\s?%?")


def extract_metric(lines: list[dict], labels: list[str]) -> str | None:
    """Find the value string for a labeled metric in OCR lines.

    Strategy: find the first line containing any label (case-insensitive);
    take the number-ish token in THAT line after the label, else the whole
    line's last number, else the nearest line to the right on the same row.
    Returns the raw matched string (e.g. "$25.71", "32%") or None.
    """
    labels_l = [l.lower() for l in labels if l]
    for ln in lines:
        text = ln["text"]
        low = text.lower()
        hit = next((l for l in labels_l if l in low), None)
        if hit is None:
            continue
        after = text[low.index(hit) + len(hit):]
        m = _NUM_RE.search(after) or _NUM_RE.search(text)
        if m:
            return m.group(0).strip()
        # Label-only line — value likely in the nearest box to the right.
        y_mid = (ln["box"][1] + ln["box"][3]) / 2
        candidates = [
            o
            for o in lines
            if o is not ln
            and o["box"][0] >= ln["box"][2] - 8
            and abs((o["box"][1] + o["box"][3]) / 2 - y_mid) < (ln["box"][3] - ln["box"][1])
        ]
        candidates.sort(key=lambda o: o["box"][0])
        for c in candidates:
            m = _NUM_RE.search(c["text"])
            if m:
                return m.group(0).strip()
    return None
