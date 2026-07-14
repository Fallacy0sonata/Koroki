"""PostToolUse hook: syntax-check any edited/written .py instantly.

Hooks are guarantees where CLAUDE.md is a request (vibecoding upgrade,
2026-07-09). Reads the hook event JSON from stdin, compiles the touched file;
exit 2 feeds the error back to Claude as blocking feedback so the bad edit
gets fixed immediately instead of being discovered at the next manual check.
"""
import json
import py_compile
import sys


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0  # malformed event — never block on hook plumbing
    fp = (data.get("tool_input") or {}).get("file_path", "")
    if not fp.endswith(".py"):
        return 0
    try:
        py_compile.compile(fp, doraise=True)
    except py_compile.PyCompileError as exc:
        print(f"SYNTAX ERROR in {fp}: {exc.msg}", file=sys.stderr)
        return 2
    except FileNotFoundError:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
