from __future__ import annotations

import argparse
import json
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

import httpx


def query_gpu_used_mib() -> dict[str, int] | None:
    try:
        cmd = [
            "nvidia-smi",
            "--query-gpu=memory.used,memory.total",
            "--format=csv,noheader,nounits",
        ]
        raw = subprocess.check_output(cmd, text=True).strip().splitlines()[0]
        used, total = [int(x.strip()) for x in raw.split(",")[:2]]
        return {"used_mib": used, "total_mib": total}
    except Exception:
        return None


def run_chat(orchestrator_url: str, user_id: str, message: str) -> dict[str, Any]:
    payload = {
        "request_id": f"webbench_{uuid.uuid4().hex[:8]}",
        "message": message,
        "defer_tts": True,
        "user_context": {
            "user_id": user_id,
            "relationship_score": 60,
            "is_owner": False,
            "mode": "auto",
            "platform": "web",
        },
    }
    with httpx.Client(timeout=180.0) as client:
        response = client.post(f"{orchestrator_url}/v1/chat", json=payload)
        response.raise_for_status()
        return response.json()


def run_voice(orchestrator_url: str, tts_request: dict[str, Any]) -> tuple[dict[str, Any], float]:
    started = time.perf_counter()
    with httpx.Client(timeout=180.0) as client:
        response = client.post(f"{orchestrator_url}/v1/voice", json=tts_request)
        response.raise_for_status()
        payload = response.json()
    elapsed_ms = round((time.perf_counter() - started) * 1000.0, 1)
    return payload, elapsed_ms


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark Koroki web chat + voice stack")
    parser.add_argument("--orchestrator-url", default="http://127.0.0.1:9882")
    parser.add_argument("--user-id", default="web_benchmark_user")
    parser.add_argument(
        "--messages",
        nargs="+",
        default=[
            "hello there",
            "do you like rainy nights",
            "what do you think about quiet music",
        ],
    )
    parser.add_argument(
        "--label",
        default="web_stack_trial",
        help="Short experiment label for the output file",
    )
    args = parser.parse_args()

    out_dir = Path("data/logs")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{args.label}.json"

    run_results: list[dict[str, Any]] = []
    gpu_before = query_gpu_used_mib()

    for message in args.messages:
        item: dict[str, Any] = {
            "message": message,
        }
        chat_started = time.perf_counter()
        chat_payload = run_chat(args.orchestrator_url, args.user_id, message)
        chat_elapsed_ms = round((time.perf_counter() - chat_started) * 1000.0, 1)

        item["chat_elapsed_ms"] = chat_elapsed_ms
        item["chat_timings"] = chat_payload.get("timings", {})
        item["text_preview"] = str(chat_payload.get("text", ""))[:180]
        item["tts_deferred"] = bool(chat_payload.get("tts_deferred"))

        if chat_payload.get("tts_request"):
            voice_payload, voice_elapsed_ms = run_voice(args.orchestrator_url, chat_payload["tts_request"])
            item["voice_elapsed_ms"] = voice_elapsed_ms
            item["voice_audio_url"] = voice_payload.get("audio_url")

        gpu_snapshot = query_gpu_used_mib()
        if gpu_snapshot:
            item["gpu_snapshot"] = gpu_snapshot

        run_results.append(item)

    report = {
        "label": args.label,
        "orchestrator_url": args.orchestrator_url,
        "gpu_before": gpu_before,
        "runs": run_results,
        "gpu_after": query_gpu_used_mib(),
    }

    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
