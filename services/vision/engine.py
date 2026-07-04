"""Vision engine — moondream2 lifecycle + inference.

Lazy-load / idle-unload: images arrive sporadically, and the resident stack
(Brain + IndexTTS ≈ 9 GB) leaves ~3 GB of headroom on the 12 GB card. The model
(~2.5 GiB at int4) is loaded on first look and dropped after idle_unload_seconds
so her overnight/idle VRAM stays clean. A look that finds the model cold pays
~11 s once; warm looks cost ~0.5 s encode + ~3 s generation.
"""

from __future__ import annotations

import gc
import io
import logging
import threading
import time

logger = logging.getLogger("koroki.vision.engine")


class VisionEngine:
    def __init__(
        self,
        model_dir: str,
        quant: str = "int4",
        max_edge: int = 1536,
        idle_unload_seconds: int = 300,
        max_answer_tokens: int = 192,
    ):
        self.model_dir = model_dir
        self.quant = quant
        self.max_edge = max_edge
        self.idle_unload_seconds = idle_unload_seconds
        self.max_answer_tokens = max_answer_tokens

        self._model = None
        self._lock = threading.Lock()
        self._last_used = 0.0
        self._watchdog_started = False
        self.load_count = 0

    @property
    def model_loaded(self) -> bool:
        return self._model is not None

    def _ensure_loaded(self):
        """Caller must hold self._lock."""
        if self._model is not None:
            return
        from .moondream_loader import load_moondream

        t0 = time.time()
        logger.info("loading moondream2 (%s) from %s ...", self.quant, self.model_dir)
        self._model = load_moondream(self.model_dir, quant=self.quant)
        self.load_count += 1
        logger.info("moondream2 loaded in %.1fs (load #%d)", time.time() - t0, self.load_count)
        if not self._watchdog_started:
            self._watchdog_started = True
            threading.Thread(target=self._watchdog, daemon=True, name="vision-idle").start()

    def unload(self) -> bool:
        with self._lock:
            if self._model is None:
                return False
            self._model = None
        gc.collect()
        try:
            import torch

            torch.cuda.empty_cache()
        except Exception:
            pass
        logger.info("moondream2 unloaded (idle) — VRAM released")
        return True

    def _watchdog(self):
        while True:
            time.sleep(30)
            if self._model is None:
                continue
            if time.time() - self._last_used > self.idle_unload_seconds:
                self.unload()

    def describe(
        self,
        images: list[bytes],
        question: str | None = None,
        max_tokens: int | None = None,
    ) -> list[str]:
        """Blocking — run via asyncio.to_thread. One inference at a time."""
        import torch
        from PIL import Image

        pil_images = []
        for raw in images:
            img = Image.open(io.BytesIO(raw)).convert("RGB")
            if max(img.size) > self.max_edge:
                scale = self.max_edge / max(img.size)
                img = img.resize(
                    (int(img.width * scale), int(img.height * scale)), Image.LANCZOS
                )
            pil_images.append(img)

        # Low temperature: percepts should be stable, not creative. max_tokens
        # caps the small-VLM repetition-loop failure mode on list-style asks.
        # "variant" must be present: encode_image() hard-indexes settings["variant"]
        # (vendored moondream.py:242) despite the TypedDict being total=False.
        sampling = {
            "max_tokens": int(max_tokens or self.max_answer_tokens),
            "temperature": 0.2,
            "top_p": 0.3,
            "variant": None,
        }

        results: list[str] = []
        with self._lock:
            self._ensure_loaded()
            self._last_used = time.time()
            with torch.inference_mode():
                for img in pil_images:
                    if question:
                        out = self._model.query(img, question, settings=sampling)["answer"]
                    else:
                        out = self._model.caption(img, "normal", settings=sampling)["caption"]
                    results.append(str(out).strip())
            self._last_used = time.time()
        return results

    def point(self, image: bytes, target: str) -> list[dict]:
        """Locate `target` in the image — moondream's native pointing skill.

        Returns normalized coordinates [{"x": 0..1, "y": 0..1}, ...] (possibly
        empty). This is the intent→click bridge for game hands: the captain says
        "click the buy button", point() says where that is. Blocking — run via
        asyncio.to_thread. NOTE: no max_edge resize here — coords are normalized
        to the ORIGINAL frame, callers scale to the window rect.
        """
        import torch
        from PIL import Image

        img = Image.open(io.BytesIO(image)).convert("RGB")
        with self._lock:
            self._ensure_loaded()
            self._last_used = time.time()
            with torch.inference_mode():
                out = self._model.point(img, target)
            self._last_used = time.time()
        points = out.get("points") or []
        return [
            {"x": float(p.get("x", 0.0)), "y": float(p.get("y", 0.0))}
            for p in points
        ]
