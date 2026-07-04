from __future__ import annotations

import argparse
import json
import subprocess
import time
from typing import Any

import httpx


def query_gpu() -> dict[str, Any]:
    raw = subprocess.check_output(
        [
            "nvidia-smi",
            "--query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu",
            "--format=csv,noheader,nounits",
        ],
        text=True,
    ).strip().splitlines()[0]
    name, used, total, util, temp = [part.strip() for part in raw.split(",")[:5]]
    return {
        "name": name,
        "used_mib": int(used),
        "total_mib": int(total),
        "gpu_util_percent": int(util),
        "temperature_c": int(temp),
    }


def fetch_json(url: str) -> dict[str, Any] | None:
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(url)
            response.raise_for_status()
            return response.json()
    except Exception:
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Profile current Koroki resident capacity")
    parser.add_argument("--brain-url", default="http://127.0.0.1:9881")
    parser.add_argument("--tts-url", default="http://127.0.0.1:9880")
    parser.add_argument("--orchestrator-url", default="http://127.0.0.1:9882")
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--interval", type=float, default=1.0)
    args = parser.parse_args()

    report: dict[str, Any] = {
        "captured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "brain_version": fetch_json(f"{args.brain_url}/version"),
        "brain_ready": fetch_json(f"{args.brain_url}/ready"),
        "tts_ready": fetch_json(f"{args.tts_url}/ready"),
        "orchestrator_health": fetch_json(f"{args.orchestrator_url}/health"),
        "samples": [],
    }

    for _ in range(max(1, args.samples)):
        report["samples"].append(query_gpu())
        time.sleep(max(0.1, args.interval))

    used_values = [sample["used_mib"] for sample in report["samples"]]
    report["summary"] = {
        "used_mib_min": min(used_values),
        "used_mib_max": max(used_values),
        "used_mib_avg": round(sum(used_values) / len(used_values), 1),
        "headroom_mib_avg": round(report["samples"][0]["total_mib"] - (sum(used_values) / len(used_values)), 1),
    }

    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
