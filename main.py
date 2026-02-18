from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

from ln_template_editor_ui import MainWindow


APP_TITLE = "DBMEditor"


def _base_dir() -> Path:
    # Dev: folder containing this file. Frozen: exe folder.
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _workspace_root() -> Path:
    # Expected layout:
    # <root>/DBMBuilder
    # <root>/DBMEditor (this)
    return _base_dir().parent


def _find_builder_command(root: Path) -> list[str] | None:
    proj = root / "DBMBuilder"
    if not proj.exists():
        return None

    # Prefer packaged exe (onefile then onedir)
    candidates = [
        proj / "dist" / "DBMBuilder.exe",
        proj / "dist" / "DBMBuilder" / "DBMBuilder.exe",
    ]
    for exe in candidates:
        if exe.is_file():
            return [os.fspath(exe)]

    # Fallback to running source with venv pythonw/python
    pyw = proj / ".venv" / "Scripts" / "pythonw.exe"
    py = proj / ".venv" / "Scripts" / "python.exe"
    entry = proj / "main.py"
    if entry.is_file() and pyw.is_file():
        return [os.fspath(pyw), os.fspath(entry)]
    if entry.is_file() and py.is_file():
        return [os.fspath(py), os.fspath(entry)]

    return None


def _spawn(cmd: list[str]) -> None:
    # Spawn detached so launcher stays responsive.
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
            subprocess, "DETACHED_PROCESS", 0
        )

    subprocess.Popen(
        cmd,
        cwd=None,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )


def main() -> None:
    workspace_root = _workspace_root()

    def open_builder() -> None:
        cmd = _find_builder_command(workspace_root)
        if not cmd:
            messagebox.showerror(
                "Not found",
                "Could not find DBMBuilder runnable.\n\n"
                "Expected either:\n"
                "- DBMBuilder/dist/DBMBuilder.exe (onefile)\n"
                "- DBMBuilder/dist/DBMBuilder/DBMBuilder.exe (onedir)\n"
                "- DBMBuilder/.venv/Scripts/python(w).exe + DBMBuilder/main.py",
            )
            return
        _spawn(cmd)

    MainWindow(workspace_root=workspace_root, open_builder_callback=open_builder).mainloop()


if __name__ == "__main__":
    main()
