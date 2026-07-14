"""Start GitHub MCP read-only using the token stored by GitHub CLI."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

LOCAL_BIN = Path.home() / ".local" / "bin"
GH = LOCAL_BIN / "gh.exe"
GITHUB_MCP = LOCAL_BIN / "github-mcp-server.exe"


def main() -> int:
    if not GH.exists() or not GITHUB_MCP.exists():
        print("GitHub tooling is incomplete; rerun the Koroki workspace setup.", file=sys.stderr)
        return 2

    auth = subprocess.run(
        [str(GH), "auth", "token"],
        capture_output=True,
        text=True,
        check=False,
    )
    token = auth.stdout.strip()
    if auth.returncode != 0 or not token:
        print(
            "GitHub login required. Run: gh auth login --web --git-protocol https",
            file=sys.stderr,
        )
        return 3

    env = os.environ.copy()
    env["GITHUB_PERSONAL_ACCESS_TOKEN"] = token
    command = [
        str(GITHUB_MCP),
        "stdio",
        "--read-only",
        "--lockdown-mode",
        "--toolsets=context,repos,issues,pull_requests,actions",
    ]
    try:
        return subprocess.call(command, env=env)
    finally:
        env.pop("GITHUB_PERSONAL_ACCESS_TOKEN", None)
        token = ""


if __name__ == "__main__":
    raise SystemExit(main())
