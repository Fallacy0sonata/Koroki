"""Scan the staged Git snapshot for secrets without exposing secret values."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCAL_GITLEAKS = Path.home() / ".local" / "bin" / "gitleaks.exe"


def main() -> int:
    gitleaks = shutil.which("gitleaks")
    if gitleaks is None and LOCAL_GITLEAKS.exists():
        gitleaks = str(LOCAL_GITLEAKS)
    if gitleaks is None:
        print("Gitleaks is not installed; staged secret scan cannot run.", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="koroki-staged-") as temp_dir:
        snapshot = Path(temp_dir) / "snapshot"
        snapshot.mkdir()
        prefix = f"{snapshot}{os.sep}"
        checkout = subprocess.run(
            ["git", "checkout-index", "--all", "--force", f"--prefix={prefix}"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if checkout.returncode != 0:
            print(checkout.stderr.strip() or "Could not materialize staged snapshot.", file=sys.stderr)
            return checkout.returncode

        report = Path(temp_dir) / "gitleaks.json"
        scan = subprocess.run(
            [
                gitleaks,
                "dir",
                str(snapshot),
                "--redact=100",
                "--no-banner",
                "--no-color",
                "--log-level=error",
                "--max-target-megabytes=20",
                "--report-format=json",
                f"--report-path={report}",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if scan.returncode == 0:
            print("Staged secret scan passed.")
            return 0
        if scan.returncode != 1:
            print(scan.stderr.strip() or "Gitleaks failed unexpectedly.", file=sys.stderr)
            return scan.returncode

        findings = json.loads(report.read_text(encoding="utf-8")) if report.exists() else []
        print(f"Staged secret scan found {len(findings)} potential secret(s):", file=sys.stderr)
        for finding in findings:
            raw_path = Path(finding.get("File", "unknown"))
            try:
                path = raw_path.relative_to(snapshot)
            except ValueError:
                path = raw_path
            rule = finding.get("RuleID", "unknown-rule")
            line = finding.get("StartLine", "?")
            print(f"- {path}:{line} ({rule})", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
