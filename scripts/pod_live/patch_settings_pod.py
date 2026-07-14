"""Rewrite the pod copy of settings.yaml with /workspace paths.

Runs ON THE POD (called by pod_setup.sh). Text-level patching — the yaml keeps
its comments, only the specific path values change. Idempotent.
"""
from __future__ import annotations

import re
from pathlib import Path

CFG = Path("/workspace/koroki/config/settings.yaml")

REPLACEMENTS = [
    # brain: exl2 quant + LoRA live on the workspace volume
    (r'(\bmodel_dir:\s*")tools/models/Qwen3-4B-exl2(")',
     r"\g<1>/workspace/models/Qwen3-4B-exl2\g<2>"),
    (r'(\bproduction:\s*")tools/models/Qwen3-4B-exl2(")',
     r"\g<1>/workspace/models/Qwen3-4B-exl2\g<2>"),
    # Follow whichever adapter the local production config selected. The old
    # v3-only regex silently stopped matching after v4 became production.
    (r'(\blora_dir:\s*")adapters/([^"]+)(")',
     r"\g<1>/workspace/private/\g<2>\g<3>"),
    # vision: fp16 on 24GB — skip the int4/tinygemm path entirely, stay resident
    (r'(\bmodel_dir:\s*")tools/models/moondream2-2025-06-21(")',
     r"\g<1>/workspace/models/moondream2-2025-06-21\g<2>"),
    (r'(\bquant:\s*")int4(")', r"\g<1>none\g<2>"),
    (r"(\bunload_after_describe:\s*)true", r"\g<1>false"),
]


def main() -> None:
    text = CFG.read_text(encoding="utf-8")
    changed = 0
    for pat, rep in REPLACEMENTS:
        text, n = re.subn(pat, rep, text)
        changed += n
    CFG.write_text(text, encoding="utf-8")
    print(f"settings patched for pod ({changed} substitutions)")


if __name__ == "__main__":
    main()
