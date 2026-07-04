"""
Koroki Dev Console — standalone floating terminal for log monitoring.

Usage:
    python scripts/dev_console.py

Reads log files directly from data/logs/ and logs/ directories.
No web server needed — works for both Discord and Web modes.
"""

import os
import sys
import time
import threading
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import ttk
except ImportError:
    print("ERROR: tkinter not available. Install python3-tk or use a different Python build.")
    sys.exit(1)

# ────────────────────────────────────────────────────────────────────
# Paths
# ────────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parent.parent
LOG_DATA_DIR = REPO_ROOT / "data" / "logs"
LOG_TRAIN_DIR = REPO_ROOT / "logs"

LOG_SERVICES = {
    "orchestrator": LOG_DATA_DIR / "orchestrator.log",
    "brain":        LOG_DATA_DIR / "brain.log",
    "tts":          LOG_DATA_DIR / "tts.log",
    "discord":      LOG_DATA_DIR / "discord.log",
}

TRAIN_LOGS = {
    "train_owner":    LOG_TRAIN_DIR / "train_owner.log",
    "train_tsundere": LOG_TRAIN_DIR / "train_tsundere.log",
    "train_peasant":  LOG_TRAIN_DIR / "train_peasant.log",
}


# ────────────────────────────────────────────────────────────────────
# Colors
# ────────────────────────────────────────────────────────────────────

COLORS = {
    "bg":           "#060e0b",
    "fg":           "#c8e6d8",
    "fg_dim":       "#6a9a82",
    "fg_error":     "#ff6b6b",
    "fg_warn":      "#ffd43b",
    "fg_info":      "#c8e6d8",
    "fg_debug":     "#4a7a62",
    "accent":       "#64dcb4",
    "accent_dim":   "#2a5a42",
    "tab_bg":       "#0a1a14",
    "tab_active":   "#64dcb4",
    "title_bg":     "#0d221a",
    "input_bg":     "#0a1610",
    "border":       "#1a3a2a",
    "scrollbar":    "#2a5a42",
}


# ────────────────────────────────────────────────────────────────────
# Log reader
# ────────────────────────────────────────────────────────────────────

def read_log_tail(filepath: Path, max_lines: int = 500) -> str:
    """Read the last N lines from a log file."""
    if not filepath.exists():
        return f"(file not found: {filepath.name})"
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
            tail = lines[-max_lines:] if len(lines) > max_lines else lines
            return "".join(tail)
    except Exception as e:
        return f"(error reading {filepath.name}: {e})"


def colorize_line(line: str) -> tuple[str, str]:
    """Return (colored_line, tag) for a log line based on level detection."""
    lower = line.lower()
    if "error" in lower or "[error]" in lower:
        return line, "error"
    elif "warning" in lower or "[warn" in lower:
        return line, "warning"
    elif "[debug]" in lower:
        return line, "debug"
    else:
        return line, "info"


# ────────────────────────────────────────────────────────────────────
# App
# ────────────────────────────────────────────────────────────────────

class DevConsole:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Koroki Dev Console")
        self.root.geometry("900x560")
        self.root.configure(bg=COLORS["bg"])
        self.root.attributes("-topmost", True)

        self.current_service = tk.StringVar(value="orchestrator")
        self.auto_scroll = tk.BooleanVar(value=True)
        self.auto_refresh = tk.BooleanVar(value=True)
        self.tail_lines = tk.IntVar(value=500)
        self.refresh_interval_ms = 3000

        self._build_ui()
        self._refresh_logs()

    # ── UI Construction ──────────────────────────────────────────

    def _build_ui(self):
        # Title bar
        title = tk.Frame(self.root, bg=COLORS["title_bg"], height=28)
        title.pack(fill="x")
        title.pack_propagate(False)

        tk.Label(
            title, text="koroki@dev ~ logs",
            bg=COLORS["title_bg"], fg=COLORS["accent"],
            font=("Consolas", 9),
        ).pack(side="left", padx=8)

        # Tab bar
        tab_frame = tk.Frame(self.root, bg=COLORS["tab_bg"])
        tab_frame.pack(fill="x")

        all_services = list(LOG_SERVICES.keys()) + list(TRAIN_LOGS.keys())
        self.tab_buttons = {}
        for svc in all_services:
            btn = tk.Label(
                tab_frame, text=svc, padx=12, pady=4,
                bg=COLORS["tab_bg"], fg=COLORS["fg_dim"],
                font=("Consolas", 9), cursor="hand2",
            )
            btn.pack(side="left")
            btn.bind("<Button-1>", lambda e, s=svc: self._select_tab(s))
            self.tab_buttons[svc] = btn

        self._update_tab_styles()

        # Toolbar
        toolbar = tk.Frame(self.root, bg=COLORS["input_bg"], height=30)
        toolbar.pack(fill="x")
        toolbar.pack_propagate(False)

        tk.Label(toolbar, text="$", bg=COLORS["input_bg"], fg=COLORS["accent"],
                 font=("Consolas", 9, "bold")).pack(side="left", padx=(8, 2))
        tk.Label(toolbar, text="tail -n", bg=COLORS["input_bg"], fg=COLORS["fg_dim"],
                 font=("Consolas", 8)).pack(side="left")

        lines_entry = tk.Entry(
            toolbar, textvariable=self.tail_lines, width=6,
            bg=COLORS["input_bg"], fg=COLORS["accent"],
            insertbackground=COLORS["accent"],
            font=("Consolas", 9), relief="flat", bd=4,
        )
        lines_entry.pack(side="left", padx=4)

        tk.Button(
            toolbar, text="refresh", command=self._refresh_logs,
            bg=COLORS["accent_dim"], fg=COLORS["accent"],
            font=("Consolas", 8), relief="flat", padx=8, pady=1,
            cursor="hand2",
        ).pack(side="left", padx=4)

        auto_cb = tk.Checkbutton(
            toolbar, text="auto", variable=self.auto_refresh,
            bg=COLORS["input_bg"], fg=COLORS["fg_dim"],
            selectcolor=COLORS["input_bg"], activebackground=COLORS["input_bg"],
            font=("Consolas", 8), relief="flat",
            command=self._schedule_refresh,
        )
        auto_cb.pack(side="left", padx=4)

        scroll_cb = tk.Checkbutton(
            toolbar, text="scroll", variable=self.auto_scroll,
            bg=COLORS["input_bg"], fg=COLORS["fg_dim"],
            selectcolor=COLORS["input_bg"], activebackground=COLORS["input_bg"],
            font=("Consolas", 8), relief="flat",
        )
        scroll_cb.pack(side="left", padx=4)

        self.status_label = tk.Label(
            toolbar, text="ready", bg=COLORS["input_bg"], fg=COLORS["accent"],
            font=("Consolas", 8),
        )
        self.status_label.pack(side="right", padx=8)

        # Log output
        output_frame = tk.Frame(self.root, bg=COLORS["bg"])
        output_frame.pack(fill="both", expand=True)

        self.log_text = tk.Text(
            output_frame, wrap="word",
            bg=COLORS["bg"], fg=COLORS["fg"],
            font=("Consolas", 9), relief="flat", bd=8,
            insertbackground=COLORS["fg"],
            selectbackground=COLORS["accent_dim"],
            state="disabled",
        )

        scrollbar = tk.Scrollbar(
            output_frame, command=self.log_text.yview,
            bg=COLORS["bg"], troughcolor=COLORS["bg"],
            activebackground=COLORS["scrollbar"],
        )
        self.log_text.configure(yscrollcommand=scrollbar.set)

        scrollbar.pack(side="right", fill="y")
        self.log_text.pack(side="left", fill="both", expand=True)

        # Color tags
        self.log_text.tag_configure("error", foreground=COLORS["fg_error"])
        self.log_text.tag_configure("warning", foreground=COLORS["fg_warn"])
        self.log_text.tag_configure("debug", foreground=COLORS["fg_debug"])
        self.log_text.tag_configure("info", foreground=COLORS["fg_info"])

    # ── Tab Management ───────────────────────────────────────────

    def _select_tab(self, service: str):
        self.current_service.set(service)
        self._update_tab_styles()
        self._refresh_logs()

    def _update_tab_styles(self):
        active = self.current_service.get()
        for svc, btn in self.tab_buttons.items():
            if svc == active:
                btn.configure(fg=COLORS["tab_active"], bg=COLORS["border"])
            else:
                btn.configure(fg=COLORS["fg_dim"], bg=COLORS["tab_bg"])

    # ── Log Refresh ──────────────────────────────────────────────

    def _get_log_path(self, service: str) -> Path:
        if service in LOG_SERVICES:
            return LOG_SERVICES[service]
        if service in TRAIN_LOGS:
            return TRAIN_LOGS[service]
        return LOG_DATA_DIR / f"{service}.log"

    def _refresh_logs(self):
        service = self.current_service.get()
        path = self._get_log_path(service)
        max_lines = max(10, min(5000, self.tail_lines.get()))

        self.status_label.configure(text="reading...", fg=COLORS["fg_warn"])
        self.root.update_idletasks()

        content = read_log_tail(path, max_lines)

        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")

        for line in content.splitlines(keepends=True):
            text, tag = colorize_line(line)
            self.log_text.insert("end", text, tag)

        self.log_text.configure(state="disabled")

        if self.auto_scroll.get():
            self.log_text.see("end")

        size_kb = path.stat().st_size / 1024 if path.exists() else 0
        self.status_label.configure(
            text=f"{service}  ({size_kb:.1f} KB)",
            fg=COLORS["accent"],
        )

        self._schedule_refresh()

    def _schedule_refresh(self):
        if hasattr(self, "_refresh_job"):
            self.root.after_cancel(self._refresh_job)
        if self.auto_refresh.get():
            self._refresh_job = self.root.after(
                self.refresh_interval_ms, self._refresh_logs
            )

    # ── Run ──────────────────────────────────────────────────────

    def run(self):
        self.root.mainloop()


# ────────────────────────────────────────────────────────────────────
# Entry
# ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = DevConsole()
    app.run()
