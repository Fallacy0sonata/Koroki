# Local Image-Gen Setup (free, unlimited, on your rig)

> **✅ INSTALLED 2026-06-28** at `tools/ComfyUI/` (own venv, Python 3.12, torch 2.11.0+cu128).
> Validated: boots, GUI at http://127.0.0.1:8188, all custom nodes import clean.
> - **Launch:** `.\scripts\start_comfyui.ps1` → open http://127.0.0.1:8188
> - **Installed:** ComfyUI + Manager + ComfyUI-layerdiffuse + ComfyUI_IPAdapter_plus.
> - **Models:** Illustrious-XL v1.0 checkpoint + LayerDiffuse SDXL (`layer_xl_transparent_attn` +
>   `vae_transparent_decoder`) downloading to `models/checkpoints` and `models/layer_model`.
>   IP-Adapter (for her intro key-art) pending.
> - **SSL note:** the machine's VPN/TLS interception breaks Python HTTPS cert verification to
>   huggingface.co, so models are fetched via `curl -kL` and pre-placed locally. ComfyUI generation
>   runs offline once models are in place. If a node tries a runtime HF fetch and SSL-fails,
>   pre-download that file with `curl -kL <url> -o <models path>`.
> - Gitignored (`tools/ComfyUI/`) so it never bloats the Koroki repo.


**The answer to "which website / credit limits":** don't use a credit-limited site. You have a 12GB
GPU — run image gen **locally**: free, unlimited, no caps, fully offline. This matches the zero-budget
rule and gives consistent style control the web tools won't.

> Install this as its OWN thing — **do not** touch the Koroki venvs (`.venv*`). ComfyUI ships/with its
> own portable Python. See `docs/environment_matrix.md`.

## The stack

| Piece | What | Where (free) |
|---|---|---|
| **ComfyUI** | Node-based local image-gen UI. Lighter + more controllable than A1111. | github.com/comfyanonymous/ComfyUI (portable Windows build) |
| **Anime SDXL checkpoint** | The base model for the art style. | Civitai — **Illustrious-XL** or **NoobAI-XL** (current best anime), or **Animagine XL 4.0**. Free. |
| **LayerDiffuse node** | Generates **transparent** PNGs directly (per-layer room art). | ComfyUI Manager → "ComfyUI-LayerDiffuse" (layerdiffusion) |
| **IP-Adapter node** | Her likeness in the intro key-art from a reference image. | ComfyUI Manager → "ComfyUI_IPAdapter_plus" + the SDXL IP-Adapter model |
| **(optional) ControlNet** | Compose rooms precisely (depth/lineart). | ComfyUI Manager → ControlNet SDXL models |

12GB is comfortable for SDXL. (Flux is heavier — possible with quantization but tighter; SDXL-anime is
the sweet spot for your rig.)

## Steps

1. Download the **ComfyUI portable** Windows zip → extract → run `run_nvidia_gpu.bat`.
2. Install **ComfyUI-Manager** (one folder drop) → restart. Use it to install LayerDiffuse + IP-Adapter
   nodes from the "Install Custom Nodes" menu.
3. Put the anime SDXL checkpoint in `ComfyUI/models/checkpoints/`, IP-Adapter model in its folder.
4. **Room layers** (no character): use a LayerDiffuse "transparent generation" workflow. Feed each layer
   prompt from `docs/frontend_art_prompts.md` §2 with the shared style suffix. Output transparent PNG.
5. **Intro key-art** (her): standard SDXL workflow + IP-Adapter referencing a clean image of her
   (Live2D screenshot / her art). Prompt = her design anchor from `frontend_art_prompts.md` §3.
6. Save into **`assets/world/`** (repo-root `assets/`, the dir the server mounts at `/assets` — NOT
   `clients/web/assets/`) with the names in §4 of the prompt pack. Tell me when one or two exist —
   I wire the incremental loader (PNG if present, else placeholder).

## Consistency (the one thing that matters)

Pick a checkpoint + style suffix + a fixed seed (or a style LoRA / IP-Adapter style ref) and **reuse
them across every generation**. That — not the tool — is what makes all the rooms feel like one place.

## If you'd rather not install yet

A web fallback exists (free SDXL on HF Spaces / civitai gen), but they cap you and break style
consistency. Local ComfyUI is the real answer for a multi-room, one-style world. I can write a one-shot
PowerShell installer script if you want it scripted.
