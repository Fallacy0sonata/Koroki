"""Run Koroki's two independent test gates from one command."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str]) -> None:
    printable = " ".join(command)
    print(f"\n> {printable}", flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--python-only", action="store_true")
    scope.add_argument("--minecraft-only", action="store_true")
    parser.add_argument("--skip-secrets", action="store_true")
    args = parser.parse_args()

    try:
        if not args.skip_secrets:
            run([sys.executable, "scripts/secret_scan.py"])

        if not args.minecraft_only:
            run([sys.executable, "-m", "pytest", "-q", "tests"])

        if not args.python_only:
            npm = shutil.which("npm.cmd") or shutil.which("npm")
            if npm is None:
                raise RuntimeError("npm was not found on PATH")
            run([npm, "--prefix", "clients/minecraft-bot", "test"])
    except (subprocess.CalledProcessError, RuntimeError) as exc:
        print(f"\nCHECK FAILED: {exc}", file=sys.stderr)
        return 1

    print("\nAll selected checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
