from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
import ctypes
from ctypes import wintypes
from pathlib import Path
from tkinter import messagebox

from ln_template_editor_ui import MainWindow


APP_TITLE = "DBMEditor"


def _monitor_workarea_top_left(x: int, y: int) -> tuple[int, int] | None:
    """Return the work-area top-left of the monitor containing (x, y) on Windows."""

    if os.name != "nt":
        return None

    try:
        MONITOR_DEFAULTTONEAREST = 2

        class POINT(ctypes.Structure):
            _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", wintypes.LONG),
                ("top", wintypes.LONG),
                ("right", wintypes.LONG),
                ("bottom", wintypes.LONG),
            ]

        class MONITORINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", RECT),
                ("rcWork", RECT),
                ("dwFlags", wintypes.DWORD),
            ]

        user32 = ctypes.windll.user32
        user32.MonitorFromPoint.argtypes = [POINT, wintypes.DWORD]
        user32.MonitorFromPoint.restype = wintypes.HMONITOR
        user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]
        user32.GetMonitorInfoW.restype = wintypes.BOOL

        hmon = user32.MonitorFromPoint(POINT(x=x, y=y), MONITOR_DEFAULTTONEAREST)
        if not hmon:
            return None

        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        ok = user32.GetMonitorInfoW(hmon, ctypes.byref(info))
        if not ok:
            return None

        return (int(info.rcWork.left), int(info.rcWork.top))
    except Exception:
        return None


def _install_toplevel_parent_placement() -> None:
    """Place Toplevel windows at top-left of the parent's monitor."""

    if getattr(tk.Toplevel, "_dbmeditor_parent_place_patched", False):
        return

    _orig_init = tk.Toplevel.__init__

    def _patched_init(self, master=None, cnf=None, **kw):
        # tkinter.Toplevel expects cnf to be a mapping when provided.
        # Passing cnf=None raises TypeError in stdlib internals.
        if cnf is None:
            _orig_init(self, master=master, **kw)
        else:
            _orig_init(self, master=master, cnf=cnf, **kw)

        parent = master if isinstance(master, tk.Misc) else getattr(self, "master", None)
        if not isinstance(parent, tk.Misc):
            return

        def _place_near_parent() -> None:
            try:
                if (not self.winfo_exists()) or (not parent.winfo_exists()):
                    return

                self.update_idletasks()
                parent.update_idletasks()

                px = int(parent.winfo_rootx())
                py = int(parent.winfo_rooty())
                # Anchor to parent window top-left, with a tiny offset.
                x = px + 8
                y = py + 8

                self.geometry(f"+{x}+{y}")
            except Exception:
                # Never break dialog creation because of placement math.
                return

        self.after_idle(_place_near_parent)

    tk.Toplevel.__init__ = _patched_init
    setattr(tk.Toplevel, "_dbmeditor_parent_place_patched", True)


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
    _install_toplevel_parent_placement()

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
