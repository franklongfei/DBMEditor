from __future__ import annotations

import os
import re
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox
from tkinter import ttk
import xml.etree.ElementTree as ET

from iec61850_scanner import (
    DOItem,
    LNodeTypeInfo,
    LNodeTypeModel,
    PrivateItem,
    TypeCatalog,
    load_lnode_type,
    save_lnode_type,
    scan_type_catalog,
)

from ln_instance_editor_ui import LNInstanceEditorFrame
from ln_instance_scanner import save_execution_scheme_root
from ln_instance_scanner import load_ln_instance_document


APP_TITLE = "DBMEditor"


class _NewApplicationChoiceDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc):
        super().__init__(parent)
        self.title("New application")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self._result: str | None = None

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="Create a new application file:").pack(anchor="w")

        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(12, 0))

        ttk.Button(btns, text="Create from LN instance", command=lambda: self._set("from_instance")).pack(
            side="top", fill="x"
        )
        ttk.Button(btns, text="Copy existing files", command=lambda: self._set("copy")).pack(
            side="top", fill="x", pady=(8, 0)
        )

        ttk.Button(frm, text="Cancel", command=self._cancel).pack(anchor="e", pady=(12, 0))

        self.bind("<Escape>", lambda _e: self._cancel())

    def _set(self, value: str) -> None:
        self._result = value
        self.destroy()

    def _cancel(self) -> None:
        self._result = None
        self.destroy()

    def show(self) -> str | None:
        self.wait_window(self)
        return self._result


class _PickFromListDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        title: str,
        label: str,
        items: list[str],
        initial: str = "",
    ):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self._items_all = list(items)
        self._result: str | None = None

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text=label).grid(row=0, column=0, sticky="w", pady=4)

        self.var_filter = tk.StringVar(value="")
        frow = ttk.Frame(frm)
        frow.grid(row=0, column=1, sticky="we", pady=4)
        frow.columnconfigure(1, weight=1)
        ttk.Label(frow, text="Filter").grid(row=0, column=0, sticky="w")
        ent_filter = ttk.Entry(frow, textvariable=self.var_filter)
        ent_filter.grid(row=0, column=1, sticky="we", padx=(8, 0))

        self.var_value = tk.StringVar(value=(initial or ""))
        self.cb = ttk.Combobox(frm, textvariable=self.var_value, values=self._items_all, width=84)
        self.cb.grid(row=1, column=1, sticky="we", pady=(0, 4))
        ttk.Label(frm, text="").grid(row=1, column=0)

        btns = ttk.Frame(frm)
        btns.grid(row=2, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Cancel", command=self._cancel).pack(side="right")
        ttk.Button(btns, text="OK", command=self._ok).pack(side="right", padx=(0, 8))

        def apply_filter(*_args) -> None:
            raw = (self.var_filter.get() or "").strip().lower()
            if not raw:
                filtered = list(self._items_all)
            else:
                tokens = [t for t in raw.split() if t]

                def ok(v: str) -> bool:
                    lv = (v or "").lower()
                    return all(t in lv for t in tokens)

                filtered = [x for x in self._items_all if ok(x)]

            cur = (self.var_value.get() or "").strip()
            if cur and cur not in filtered:
                filtered = [cur] + filtered
            self.cb["values"] = filtered[:2000]

        self.var_filter.trace_add("write", apply_filter)
        apply_filter()

        self.cb.bind("<Return>", lambda _e: self._ok())
        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Control-f>", lambda _e: ent_filter.focus_set())
        ent_filter.focus_set()

    def _ok(self) -> None:
        value = (self.var_value.get() or "").strip()
        if not value:
            messagebox.showerror("Missing", "Selection is required", parent=self)
            return
        self._result = value
        self.destroy()

    def _cancel(self) -> None:
        self._result = None
        self.destroy()

    def show(self) -> str | None:
        self.wait_window(self)
        return self._result


class _OverwriteDuplicateCancelDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, *, title: str, message: str):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self._result: str | None = None  # "overwrite" | "duplicate" | None

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text=message, justify="left").pack(anchor="w")

        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(12, 0))

        ttk.Button(btns, text="Overwrite", command=lambda: self._set("overwrite")).pack(side="left")
        ttk.Button(btns, text="Duplicate", command=lambda: self._set("duplicate")).pack(side="left", padx=(8, 0))
        ttk.Button(btns, text="Cancel", command=self._cancel).pack(side="right")

        self.bind("<Escape>", lambda _e: self._cancel())

        # Center on parent (or screen) for better UX.
        try:
            self.update_idletasks()
            self._center_on_parent(parent)
        except Exception:
            pass

    def _center_on_parent(self, parent: tk.Misc) -> None:
        try:
            pw = int(parent.winfo_width())
            ph = int(parent.winfo_height())
            px = int(parent.winfo_rootx())
            py = int(parent.winfo_rooty())
        except Exception:
            pw = ph = px = py = 0

        try:
            ww = int(self.winfo_reqwidth())
            wh = int(self.winfo_reqheight())
        except Exception:
            ww = wh = 200

        if pw > 1 and ph > 1:
            x = px + (pw - ww) // 2
            y = py + (ph - wh) // 2
        else:
            sw = int(self.winfo_screenwidth())
            sh = int(self.winfo_screenheight())
            x = (sw - ww) // 2
            y = (sh - wh) // 2

        x = max(0, x)
        y = max(0, y)
        self.geometry(f"+{x}+{y}")

    def _set(self, value: str) -> None:
        self._result = value
        self.destroy()

    def _cancel(self) -> None:
        self._result = None
        self.destroy()

    def show(self) -> str | None:
        self.wait_window(self)
        return self._result


class _FunBlockDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        initial_name: str,
        initial_class: str,
        initial_seqNb: str,
        initial_lnref: str,
        initial_desc: str,
    ):
        super().__init__(parent)
        self.title("funBlock")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self._result: dict[str, str] | None = None

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        self.var_name = tk.StringVar(value=(initial_name or ""))
        self.var_class = tk.StringVar(value=(initial_class or ""))
        self.var_seq = tk.StringVar(value=(initial_seqNb or "50"))
        self.var_lnref = tk.StringVar(value=(initial_lnref or ""))
        self.var_desc = tk.StringVar(value=(initial_desc or ""))

        ttk.Label(frm, text="name").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=self.var_name, width=56).grid(row=0, column=1, sticky="we", pady=4)

        ttk.Label(frm, text="class").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=self.var_class, width=56).grid(row=1, column=1, sticky="we", pady=4)

        ttk.Label(frm, text="seqNb").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=self.var_seq, width=16).grid(row=2, column=1, sticky="w", pady=4)

        ttk.Label(frm, text="LnRef").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=self.var_lnref, width=56).grid(row=3, column=1, sticky="we", pady=4)

        ttk.Label(frm, text="desc").grid(row=4, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=self.var_desc, width=56).grid(row=4, column=1, sticky="we", pady=4)

        btns = ttk.Frame(frm)
        btns.grid(row=5, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Cancel", command=self._cancel).pack(side="right")
        ttk.Button(btns, text="OK", command=self._ok).pack(side="right", padx=(0, 8))

        self.bind("<Escape>", lambda _e: self._cancel())

    def _ok(self) -> None:
        name = (self.var_name.get() or "").strip()
        cls = (self.var_class.get() or "").strip()
        seq = (self.var_seq.get() or "").strip() or "50"
        lnref = (self.var_lnref.get() or "").strip()
        desc = (self.var_desc.get() or "")
        if not name:
            messagebox.showerror("Missing", "name is required", parent=self)
            return
        if not cls:
            messagebox.showerror("Missing", "class is required", parent=self)
            return
        if not seq.isdigit():
            messagebox.showerror("Invalid", "seqNb must be digits", parent=self)
            return
        self._result = {"name": name, "class": cls, "seqNb": seq, "LnRef": lnref, "desc": desc}
        self.destroy()

    def _cancel(self) -> None:
        self._result = None
        self.destroy()

    def show(self) -> str | None:
        self.wait_window(self)
        return self._result


class _CreateFromLnInstanceDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, *, lndm_dir: Path, items: list[str]):
        super().__init__(parent)
        self.title("Create from LN instance")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self._lndm_dir = Path(lndm_dir)
        self._items_all = list(items)
        self._result: dict[str, str] | None = None

        self._last_auto_name = ""
        self._last_auto_class = ""
        self._last_auto_lnref = ""
        self._last_auto_desc = ""

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text="LN instance").grid(row=0, column=0, sticky="w", pady=4)

        self.var_filter = tk.StringVar(value="")
        frow = ttk.Frame(frm)
        frow.grid(row=0, column=1, sticky="we", pady=4)
        frow.columnconfigure(1, weight=1)
        ttk.Label(frow, text="Filter").grid(row=0, column=0, sticky="w")
        ent_filter = ttk.Entry(frow, textvariable=self.var_filter)
        ent_filter.grid(row=0, column=1, sticky="we", padx=(8, 0))

        self.var_ln = tk.StringVar(value="")
        self.cb_ln = ttk.Combobox(frm, textvariable=self.var_ln, values=self._items_all, width=84)
        self.cb_ln.grid(row=1, column=1, sticky="we", pady=(0, 8))
        ttk.Label(frm, text="").grid(row=1, column=0)

        self.var_name = tk.StringVar(value="")
        self.var_class = tk.StringVar(value="")
        self.var_seq = tk.StringVar(value="50")
        self.var_lnref = tk.StringVar(value="")
        self.var_desc = tk.StringVar(value="")

        ttk.Label(frm, text="name").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=self.var_name, width=56).grid(row=2, column=1, sticky="we", pady=4)

        ttk.Label(frm, text="class").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=self.var_class, width=56).grid(row=3, column=1, sticky="we", pady=4)

        ttk.Label(frm, text="seqNb").grid(row=4, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=self.var_seq, width=16).grid(row=4, column=1, sticky="w", pady=4)

        ttk.Label(frm, text="LnRef").grid(row=5, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=self.var_lnref, width=56).grid(row=5, column=1, sticky="we", pady=4)

        ttk.Label(frm, text="desc").grid(row=6, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=self.var_desc, width=56).grid(row=6, column=1, sticky="we", pady=4)

        btns = ttk.Frame(frm)
        btns.grid(row=7, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Cancel", command=self._cancel).pack(side="right")
        ttk.Button(btns, text="OK", command=self._ok).pack(side="right", padx=(0, 8))

        def apply_filter(*_args) -> None:
            raw = (self.var_filter.get() or "").strip().lower()
            if not raw:
                filtered = list(self._items_all)
            else:
                tokens = [t for t in raw.split() if t]

                def ok(v: str) -> bool:
                    lv = (v or "").lower()
                    return all(t in lv for t in tokens)

                filtered = [x for x in self._items_all if ok(x)]

            cur = (self.var_ln.get() or "").strip()
            if cur and cur not in filtered:
                filtered = [cur] + filtered
            self.cb_ln["values"] = filtered[:2000]

        def on_select(*_args) -> None:
            self._apply_autofill_from_selected_ln()

        self.var_filter.trace_add("write", apply_filter)
        self.var_ln.trace_add("write", on_select)
        apply_filter()

        self.cb_ln.bind("<Return>", lambda _e: self._ok())
        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Control-f>", lambda _e: ent_filter.focus_set())
        ent_filter.focus_set()

        # No default selection: only autofill after user selects an LN instance.

    def _apply_autofill_from_selected_ln(self) -> None:
        rel = (self.var_ln.get() or "").strip()
        if not rel:
            return

        path = self._lndm_dir / rel
        try:
            doc = load_ln_instance_document(path)
            ln = doc.ln_elements[0]
            prefix = (ln.attrib.get("prefix") or "").strip()
            ln_class = (ln.attrib.get("lnClass") or "").strip()
            ln_desc = (ln.attrib.get("desc") or "").strip()
        except Exception:
            return

        auto_lnref = f"{prefix}{ln_class}#" if (prefix or ln_class) else "#"
        auto_desc = ln_desc
        auto_name = f"A{prefix}{ln_class}".strip() or "Application"
        auto_class = auto_name

        cur_name = (self.var_name.get() or "")
        cur_class = (self.var_class.get() or "")
        cur_lnref = (self.var_lnref.get() or "")
        cur_desc = (self.var_desc.get() or "")

        if (not cur_name) or (cur_name == self._last_auto_name):
            self.var_name.set(auto_name)
        if (not cur_class) or (cur_class == self._last_auto_class):
            self.var_class.set(auto_class)
        if (not cur_lnref) or (cur_lnref == self._last_auto_lnref):
            self.var_lnref.set(auto_lnref)
        if (not cur_desc) or (cur_desc == self._last_auto_desc):
            self.var_desc.set(auto_desc)

        self._last_auto_name = auto_name
        self._last_auto_class = auto_class
        self._last_auto_lnref = auto_lnref
        self._last_auto_desc = auto_desc

    def _ok(self) -> None:
        rel = (self.var_ln.get() or "").strip()
        name = (self.var_name.get() or "").strip()
        cls = (self.var_class.get() or "").strip()
        seq = (self.var_seq.get() or "").strip() or "50"
        lnref = (self.var_lnref.get() or "").strip()
        desc = (self.var_desc.get() or "")

        if not rel:
            messagebox.showerror("Missing", "LN instance is required", parent=self)
            return
        if not name:
            messagebox.showerror("Missing", "name is required", parent=self)
            return
        if not cls:
            messagebox.showerror("Missing", "class is required", parent=self)
            return
        if not seq.isdigit():
            messagebox.showerror("Invalid", "seqNb must be digits", parent=self)
            return

        self._result = {"ln_instance_rel": rel, "name": name, "class": cls, "seqNb": seq, "LnRef": lnref, "desc": desc}
        self.destroy()

    def _cancel(self) -> None:
        self._result = None
        self.destroy()

    def show(self) -> tuple[str, PrivateItem] | None:
        self.wait_window(self)
        return self._result


class _CopyApplicationDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, *, app_dir: Path, items: list[str]):
        super().__init__(parent)
        self.title("Copy application")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self._app_dir = Path(app_dir)
        self._items_all = list(items)
        self._result: dict[str, str] | None = None
        self._last_auto_new_name = ""

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text="Source file").grid(row=0, column=0, sticky="w", pady=4)
        self.var_filter = tk.StringVar(value="")
        frow = ttk.Frame(frm)
        frow.grid(row=0, column=1, sticky="we", pady=4)
        frow.columnconfigure(1, weight=1)
        ttk.Label(frow, text="Filter").grid(row=0, column=0, sticky="w")
        ent_filter = ttk.Entry(frow, textvariable=self.var_filter)
        ent_filter.grid(row=0, column=1, sticky="we", padx=(8, 0))

        self.var_src = tk.StringVar(value="")
        self.cb_src = ttk.Combobox(frm, textvariable=self.var_src, values=self._items_all, width=84)
        self.cb_src.grid(row=1, column=1, sticky="we", pady=(0, 8))
        ttk.Label(frm, text="").grid(row=1, column=0)

        ttk.Label(frm, text="New file name").grid(row=2, column=0, sticky="w", pady=4)
        self.var_new = tk.StringVar(value="")
        ent_new = ttk.Entry(frm, textvariable=self.var_new, width=56)
        ent_new.grid(row=2, column=1, sticky="we", pady=4)

        hint = f"Saved under: {os.fspath(self._app_dir)}"
        ttk.Label(frm, text=hint).grid(row=3, column=0, columnspan=2, sticky="w", pady=(0, 6))

        btns = ttk.Frame(frm)
        btns.grid(row=4, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Cancel", command=self._cancel).pack(side="right")
        ttk.Button(btns, text="OK", command=self._ok).pack(side="right", padx=(0, 8))

        def apply_filter(*_args) -> None:
            raw = (self.var_filter.get() or "").strip().lower()
            if not raw:
                filtered = list(self._items_all)
            else:
                tokens = [t for t in raw.split() if t]

                def ok(v: str) -> bool:
                    lv = (v or "").lower()
                    return all(t in lv for t in tokens)

                filtered = [x for x in self._items_all if ok(x)]

            cur = (self.var_src.get() or "").strip()
            if cur and cur not in filtered:
                filtered = [cur] + filtered
            self.cb_src["values"] = filtered[:2000]

        def on_src_change(*_args) -> None:
            src = (self.var_src.get() or "").strip()
            if not src:
                return
            base = Path(src).name
            auto = base
            if auto.lower().endswith(".xml"):
                auto = auto[:-4] + "_copy.xml"
            else:
                auto = auto + "_copy.xml"
            cur_new = (self.var_new.get() or "")
            if (not cur_new) or (cur_new == self._last_auto_new_name):
                self.var_new.set(auto)
            self._last_auto_new_name = auto

        self.var_filter.trace_add("write", apply_filter)
        self.var_src.trace_add("write", on_src_change)
        apply_filter()

        self.cb_src.bind("<Return>", lambda _e: self._ok())
        ent_new.bind("<Return>", lambda _e: self._ok())
        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Control-f>", lambda _e: ent_filter.focus_set())

        ent_new.focus_set()
        ent_new.selection_range(0, tk.END)

    def _ok(self) -> None:
        src = (self.var_src.get() or "").strip()
        new_name = (self.var_new.get() or "").strip()
        if not src:
            messagebox.showerror("Missing", "Source file is required", parent=self)
            return
        if not new_name:
            messagebox.showerror("Missing", "New file name is required", parent=self)
            return
        if any(sep in new_name for sep in ("/", "\\")):
            messagebox.showerror("Invalid", "New file name must not contain path separators", parent=self)
            return
        if not new_name.lower().endswith(".xml"):
            new_name = new_name + ".xml"

        # Very small Windows-invalid check (keep simple)
        invalid = set('<>:"/\\|?*')
        if any(ch in invalid for ch in new_name):
            messagebox.showerror("Invalid", "New file name contains invalid characters", parent=self)
            return

        self._result = {"src_rel": src, "new_name": new_name}
        self.destroy()

    def _cancel(self) -> None:
        self._result = None
        self.destroy()

    def show(self) -> dict[str, str] | None:
        self.wait_window(self)
        return self._result


class _EditApplicationInputDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        title: str,
        input_types: list[str],
        initial: dict[str, str],
    ):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self._result: dict[str, str] | None = None

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        self.var_name = tk.StringVar(value=(initial.get("name") or ""))
        self.var_type = tk.StringVar(value=(initial.get("type") or ""))
        self.var_desc = tk.StringVar(value=(initial.get("desc") or ""))
        self.var_src = tk.StringVar(value=(initial.get("src") or ""))
        self.var_doRef = tk.StringVar(value=(initial.get("doRef") or ""))
        self.var_soft = tk.BooleanVar(value=((initial.get("softlink") or "").lower() == "true"))
        self.var_conf = tk.BooleanVar(value=((initial.get("confpin") or "").lower() == "true"))

        ttk.Label(frm, text="name").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=self.var_name, width=56).grid(row=0, column=1, sticky="we", pady=4)

        ttk.Label(frm, text="type").grid(row=1, column=0, sticky="w", pady=4)
        cb_type = ttk.Combobox(frm, textvariable=self.var_type, values=input_types, width=54)
        cb_type.grid(row=1, column=1, sticky="we", pady=4)

        ttk.Label(frm, text="desc").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=self.var_desc, width=56).grid(row=2, column=1, sticky="we", pady=4)

        ttk.Label(frm, text="src").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=self.var_src, width=56).grid(row=3, column=1, sticky="we", pady=4)

        ttk.Label(frm, text="doRef").grid(row=4, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=self.var_doRef, width=56).grid(row=4, column=1, sticky="we", pady=4)

        flags = ttk.Frame(frm)
        flags.grid(row=5, column=0, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Checkbutton(flags, text="Soft link", variable=self.var_soft).pack(side="left")
        ttk.Checkbutton(flags, text="Confpin", variable=self.var_conf).pack(side="left", padx=(16, 0))

        btns = ttk.Frame(frm)
        btns.grid(row=6, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Cancel", command=self._cancel).pack(side="right")
        ttk.Button(btns, text="OK", command=self._ok).pack(side="right", padx=(0, 8))

        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Return>", lambda _e: self._ok())
        cb_type.bind("<Return>", lambda _e: self._ok())

    def _ok(self) -> None:
        name = (self.var_name.get() or "").strip()
        if not name:
            messagebox.showerror("Missing", "name is required", parent=self)
            return

        self._result = {
            "name": name,
            "type": (self.var_type.get() or "").strip(),
            "desc": (self.var_desc.get() or ""),
            "src": (self.var_src.get() or ""),
            "doRef": (self.var_doRef.get() or "").strip(),
            "softlink": "true" if bool(self.var_soft.get()) else "",
            "confpin": "true" if bool(self.var_conf.get()) else "",
        }
        self.destroy()

    def _cancel(self) -> None:
        self._result = None
        self.destroy()

    def show(self) -> dict[str, str] | None:
        self.wait_window(self)
        return self._result


class DOEditDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        title: str,
        do_types: list[str],
        initial: DOItem | None,
        edit_name: bool = True,
    ):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self._result: DOItem | None = None

        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)

        self._all_do_types = list(do_types)

        ttk.Label(frm, text="DO name").grid(row=0, column=0, sticky="w", pady=4)
        self.var_name = tk.StringVar(value=(initial.name if initial else ""))
        ent_name = ttk.Entry(frm, textvariable=self.var_name, width=50)
        ent_name.grid(row=0, column=1, sticky="we", padx=(8, 0), pady=4)
        if not edit_name:
            try:
                ent_name.configure(state="disabled")
            except Exception:
                pass

        ttk.Label(frm, text="DO type").grid(row=1, column=0, sticky="w", pady=4)
        self.var_type = tk.StringVar(value=(initial.do_type if initial else ""))

        type_box = ttk.Frame(frm)
        type_box.grid(row=1, column=1, sticky="we", padx=(8, 0), pady=4)
        type_box.columnconfigure(0, weight=1)

        self.var_filter = tk.StringVar(value="")
        ent_filter = ttk.Entry(type_box, textvariable=self.var_filter)
        ent_filter.grid(row=0, column=0, sticky="we")
        ttk.Label(type_box, text="Filter").grid(row=0, column=1, sticky="e", padx=(8, 0))

        self.lbl_match = ttk.Label(type_box, text="")
        self.lbl_match.grid(row=1, column=1, sticky="e", padx=(8, 0), pady=(4, 0))

        self.cb = ttk.Combobox(type_box, textvariable=self.var_type, values=self._all_do_types, width=48)
        self.cb.grid(row=1, column=0, sticky="we", pady=(4, 0))

        def apply_filter(*_args) -> None:
            raw = self.var_filter.get().strip().lower()
            if not raw:
                filtered = self._all_do_types
            else:
                tokens = [t for t in raw.split() if t]

                def ok(v: str) -> bool:
                    lv = v.lower()
                    return all(t in lv for t in tokens)

                filtered = [v for v in self._all_do_types if ok(v)]

            # Keep current value available even if it doesn't match filter
            cur = self.var_type.get().strip()
            if cur and cur not in filtered:
                filtered = [cur] + filtered

            # Avoid huge UI lag if matches are extremely large
            max_show = 1500
            shown = filtered[:max_show]
            self.cb["values"] = shown
            suffix = "" if len(filtered) <= max_show else f" (showing first {max_show})"
            self.lbl_match.configure(text=f"{len(filtered)} match{'' if len(filtered)==1 else 'es'}{suffix}")

        self.var_filter.trace_add("write", apply_filter)
        apply_filter()

        frm.columnconfigure(1, weight=1)

        btns = ttk.Frame(frm)
        btns.grid(row=2, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Cancel", command=self._cancel).pack(side="right")
        ttk.Button(btns, text="OK", command=self._ok).pack(side="right", padx=(0, 8))

        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Control-f>", lambda _e: ent_filter.focus_set())

        ent_filter.focus_set()

    def _ok(self) -> None:
        name = self.var_name.get().strip()
        do_type = self.var_type.get().strip()
        if not name:
            messagebox.showerror("Missing", "DO name is required", parent=self)
            return
        if not do_type:
            messagebox.showerror("Missing", "DO type is required", parent=self)
            return
        self._result = DOItem(name=name, do_type=do_type)
        self.destroy()

    def _cancel(self) -> None:
        self._result = None
        self.destroy()

    def show(self) -> DOItem | None:
        self.wait_window(self)
        return self._result


class DOTable(ttk.Frame):
    def __init__(self, parent: tk.Misc, *, do_types: list[str]):
        super().__init__(parent)
        self.do_types = do_types
        self.rows: list[DOItem] = []
        self._clipboard: DOItem | None = None
        self._undo_stack: list[list[DOItem]] = []
        self._undo_max = 50

        # Optional callback: invoked when user chooses Rules... on a selected DO.
        # Signature: callback(selected_index: int) -> None
        self.on_rules = None

        self._inline: ttk.Entry | None = None
        self._inline_iid: str | None = None

        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x", pady=(6, 4))
        ttk.Button(toolbar, text="Add", command=self._add).pack(side="left")
        ttk.Button(toolbar, text="Insert", command=self._insert).pack(side="left", padx=(6, 0))
        ttk.Button(toolbar, text="Copy", command=self.copy_selected).pack(side="left", padx=(6, 0))
        ttk.Button(toolbar, text="Cut", command=self.cut_selected).pack(side="left", padx=(6, 0))
        ttk.Button(toolbar, text="Paste", command=self.paste_after_selected).pack(side="left", padx=(6, 0))
        ttk.Button(toolbar, text="Delete", command=self.delete_selected).pack(side="left", padx=(6, 0))
        ttk.Button(toolbar, text="Up", command=lambda: self._move(-1)).pack(side="left", padx=(18, 0))
        ttk.Button(toolbar, text="Down", command=lambda: self._move(1)).pack(side="left", padx=(6, 0))

        self.tree = ttk.Treeview(self, columns=["name", "type"], show="headings", selectmode="browse")
        self.tree.heading("name", text="DO name")
        self.tree.heading("type", text="DO type")
        self.tree.column("name", width=220, anchor="w")
        self.tree.column("type", width=640, anchor="w")

        y = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=y.set)

        self.tree.pack(fill="both", expand=True, side="left")
        y.pack(fill="y", side="right")

        self.tree.bind("<Double-1>", self._on_double_click)

        # Copy/Paste (Ctrl+C / Ctrl+V) + context menu
        self.tree.bind("<Control-c>", lambda _e: self.copy_selected())
        self.tree.bind("<Control-C>", lambda _e: self.copy_selected())
        self.tree.bind("<Control-x>", lambda _e: self.cut_selected())
        self.tree.bind("<Control-X>", lambda _e: self.cut_selected())
        self.tree.bind("<Control-v>", lambda _e: self.paste_after_selected())
        self.tree.bind("<Control-V>", lambda _e: self.paste_after_selected())
        self.tree.bind("<Control-z>", lambda _e: self.undo())
        self.tree.bind("<Control-Z>", lambda _e: self.undo())
        self.tree.bind("<Delete>", lambda _e: self.delete_selected())
        self.tree.bind("<Button-3>", self._show_context_menu)

        self._menu = tk.Menu(self, tearoff=False)
        self._menu.add_command(label="Add", command=self._add)
        self._menu.add_command(label="Insert", command=self._insert)
        self._menu.add_separator()
        self._menu.add_command(label="Copy", command=self.copy_selected)
        self._menu.add_command(label="Cut", command=self.cut_selected)
        self._menu.add_command(label="Paste", command=self.paste_after_selected)
        self._menu.add_command(label="Delete", command=self.delete_selected)
        self._menu.add_separator()
        self._menu.add_command(label="Up", command=lambda: self._move(-1))
        self._menu.add_command(label="Down", command=lambda: self._move(1))

        self._menu.add_separator()
        self._menu.add_command(label="Rules...", command=self.edit_rules_for_selected)

    def edit_rules_for_selected(self) -> None:
        self._end_inline_name_edit(commit=True)
        idx = self._selected_index()
        if idx is None or idx < 0 or idx >= len(self.rows):
            return
        cb = getattr(self, "on_rules", None)
        if cb is None:
            return
        try:
            cb(idx)
        except Exception as e:
            try:
                messagebox.showerror("Error", f"Failed to open Rules dialog:\n{e}", parent=self)
            except Exception:
                pass
            return

    def set_rows(self, rows: list[DOItem]) -> None:
        self.rows = list(rows)
        self._undo_stack = []
        self.refresh()

    def get_rows(self) -> list[DOItem]:
        return list(self.rows)

    def _clone_private_item(self, p: PrivateItem) -> PrivateItem:
        return PrivateItem(attrib=dict(p.attrib), inner_xml=p.inner_xml)

    def _clone_do_item(self, x: DOItem) -> DOItem:
        privs = [self._clone_private_item(p) for p in (getattr(x, "privates", []) or [])]
        return DOItem(name=x.name, do_type=x.do_type, privates=privs)

    def _clone_rows(self, rows: list[DOItem]) -> list[DOItem]:
        return [self._clone_do_item(x) for x in rows]

    def _push_undo(self) -> None:
        self._undo_stack.append(self._clone_rows(self.rows))
        if len(self._undo_stack) > self._undo_max:
            self._undo_stack = self._undo_stack[-self._undo_max :]

    def undo(self) -> None:
        if not self._undo_stack:
            return
        prev = self._undo_stack.pop()
        self.rows = self._clone_rows(prev)
        self.refresh()

    def refresh(self) -> None:
        for item in self.tree.get_children(""):
            self.tree.delete(item)
        for idx, row in enumerate(self.rows):
            self.tree.insert("", "end", iid=str(idx), values=[row.name, row.do_type])

    def _begin_inline_name_edit(self, iid: str) -> None:
        if iid is None:
            return
        try:
            idx = int(iid)
        except Exception:
            return
        if idx < 0 or idx >= len(self.rows):
            return

        self._end_inline_name_edit(commit=True)

        bbox = self.tree.bbox(iid, column="#1")
        if not bbox:
            return
        x, y, w, h = bbox
        var = tk.StringVar(value=self.rows[idx].name)
        ent = ttk.Entry(self.tree, textvariable=var)
        ent.place(x=x, y=y, width=w, height=h)
        ent.focus_set()
        try:
            ent.selection_range(0, "end")
        except Exception:
            pass

        self._inline = ent
        self._inline_iid = iid

        def _commit(_e=None) -> None:
            self._end_inline_name_edit(commit=True)

        def _cancel(_e=None) -> None:
            self._end_inline_name_edit(commit=False)

        ent.bind("<Return>", _commit)
        ent.bind("<Escape>", _cancel)
        ent.bind("<FocusOut>", _commit)

    def _end_inline_name_edit(self, *, commit: bool) -> None:
        ent = self._inline
        iid = self._inline_iid
        if ent is None or iid is None:
            self._inline = None
            self._inline_iid = None
            return

        try:
            new_name = (ent.get() or "").strip()
        except Exception:
            new_name = ""

        try:
            ent.place_forget()
            ent.destroy()
        except Exception:
            pass

        self._inline = None
        self._inline_iid = None

        if not commit:
            return

        try:
            idx = int(iid)
        except Exception:
            return
        if idx < 0 or idx >= len(self.rows):
            return

        if not new_name:
            messagebox.showerror("Missing", "DO name is required", parent=self)
            return

        current = self.rows[idx]
        if new_name != current.name and any(x.name == new_name for x in self.rows):
            messagebox.showerror("Duplicate", f"DO name already exists: {new_name}", parent=self)
            return

        if new_name == current.name:
            return

        self._push_undo()
        self.rows[idx] = DOItem(
            name=new_name,
            do_type=current.do_type,
            privates=list(getattr(current, "privates", []) or []),
        )
        self.refresh()
        try:
            self.tree.selection_set(str(idx))
        except Exception:
            pass

    def _on_double_click(self, event: tk.Event) -> None:
        try:
            iid = self.tree.identify_row(event.y)
            col = self.tree.identify_column(event.x)
        except Exception:
            return
        if not iid:
            return
        try:
            self.tree.selection_set(iid)
        except Exception:
            pass

        if col == "#1":
            self._begin_inline_name_edit(iid)
        elif col == "#2":
            self._edit_type_for_selected()

    def _selected_index(self) -> int | None:
        sel = self.tree.selection()
        if not sel:
            return None
        try:
            return int(sel[0])
        except Exception:
            return None

    def _unique_copy_name(self, base_name: str) -> str:
        """Generate a unique copy name based on base_name + '_copy'.

        First paste uses '<name>_copy' if available, then '<name>_copy2', '<name>_copy3', ...
        """
        existing = {x.name for x in self.rows}
        candidate = f"{base_name}_copy"
        if candidate not in existing:
            return candidate
        i = 2
        while True:
            candidate = f"{base_name}_copy{i}"
            if candidate not in existing:
                return candidate
            i += 1

    def copy_selected(self) -> None:
        idx = self._selected_index()
        if idx is None or idx < 0 or idx >= len(self.rows):
            return
        item = self.rows[idx]
        self._clipboard = self._clone_do_item(item)
        # Also put something in OS clipboard for convenience
        try:
            self.clipboard_clear()
            self.clipboard_append(f"{item.name}\t{item.do_type}")
        except Exception:
            pass

    def cut_selected(self) -> None:
        self.copy_selected()
        self.delete_selected()

    def delete_selected(self) -> None:
        idx = self._selected_index()
        if idx is None or idx < 0 or idx >= len(self.rows):
            return
        self._push_undo()
        self.rows.pop(idx)
        self.refresh()
        if self.rows:
            sel = min(idx, len(self.rows) - 1)
            self.tree.selection_set(str(sel))

    def paste_after_selected(self) -> None:
        if self._clipboard is None:
            return

        self._push_undo()

        new_item = self._clone_do_item(self._clipboard)
        new_item.name = self._unique_copy_name(new_item.name)

        idx = self._selected_index()
        if idx is None or idx < 0 or idx >= len(self.rows):
            self.rows.append(new_item)
            self.refresh()
            self.tree.selection_set(str(len(self.rows) - 1))
            return

        insert_at = idx + 1
        self.rows.insert(insert_at, new_item)
        self.refresh()
        self.tree.selection_set(str(insert_at))

    def _show_context_menu(self, event: tk.Event) -> None:
        self._end_inline_name_edit(commit=True)

        # Select row under cursor (if any)
        try:
            row_id = self.tree.identify_row(event.y)
            if row_id:
                self.tree.selection_set(row_id)
        except Exception:
            pass

        idx = self._selected_index()
        can_copy = idx is not None
        can_delete = can_copy
        can_paste = self._clipboard is not None
        can_up = idx is not None and idx > 0
        can_down = idx is not None and idx < (len(self.rows) - 1)
        can_rules = idx is not None
        self._menu.entryconfigure("Copy", state=("normal" if can_copy else "disabled"))
        self._menu.entryconfigure("Cut", state=("normal" if can_copy else "disabled"))
        self._menu.entryconfigure("Paste", state=("normal" if can_paste else "disabled"))
        self._menu.entryconfigure("Delete", state=("normal" if can_delete else "disabled"))
        self._menu.entryconfigure("Up", state=("normal" if can_up else "disabled"))
        self._menu.entryconfigure("Down", state=("normal" if can_down else "disabled"))
        try:
            self._menu.entryconfigure("Rules...", state=("normal" if can_rules else "disabled"))
        except Exception:
            pass

        try:
            self._menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                self._menu.grab_release()
            except Exception:
                pass

    def _add(self) -> None:
        self._end_inline_name_edit(commit=True)
        dlg = DOEditDialog(self, title="Add", do_types=self.do_types, initial=None)
        res = dlg.show()
        if res is None:
            return
        if any(x.name == res.name for x in self.rows):
            messagebox.showerror("Duplicate", f"DO name already exists: {res.name}", parent=self)
            return
        self._push_undo()
        self.rows.append(res)
        self.refresh()

    def _insert(self) -> None:
        """Insert a new DO near the current selection.

        Behavior:
        - If a row is selected: insert AFTER it.
        - If nothing is selected: append at the end.
        """
        self._end_inline_name_edit(commit=True)
        dlg = DOEditDialog(self, title="Insert", do_types=self.do_types, initial=None)
        res = dlg.show()
        if res is None:
            return
        if any(x.name == res.name for x in self.rows):
            messagebox.showerror("Duplicate", f"DO name already exists: {res.name}", parent=self)
            return

        idx = self._selected_index()
        self._push_undo()
        if idx is None or idx < 0 or idx >= len(self.rows):
            self.rows.append(res)
            self.refresh()
            self.tree.selection_set(str(len(self.rows) - 1))
            return

        insert_at = idx + 1
        self.rows.insert(insert_at, res)
        self.refresh()
        self.tree.selection_set(str(insert_at))

    def _edit_type_for_selected(self) -> None:
        self._end_inline_name_edit(commit=True)
        idx = self._selected_index()
        if idx is None or idx < 0 or idx >= len(self.rows):
            return
        current = self.rows[idx]
        dlg = DOEditDialog(self, title="Edit type", do_types=self.do_types, initial=current, edit_name=False)
        res = dlg.show()
        if res is None:
            return
        if res.do_type == current.do_type:
            return
        self._push_undo()
        self.rows[idx] = DOItem(name=current.name, do_type=res.do_type, privates=list(getattr(current, "privates", []) or []))
        self.refresh()
        self.tree.selection_set(str(idx))

    def _move(self, delta: int) -> None:
        idx = self._selected_index()
        if idx is None:
            return
        j = idx + delta
        if j < 0 or j >= len(self.rows):
            return
        self._push_undo()
        self.rows[idx], self.rows[j] = self.rows[j], self.rows[idx]
        self.refresh()
        self.tree.selection_set(str(j))


class PrivateEditDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        title: str,
        initial: PrivateItem | None,
    ):
        super().__init__(parent)
        self.title(title)
        self.geometry("720x420")
        self.transient(parent)
        self.grab_set()

        self._result: PrivateItem | None = None
        self._initial_attrib = dict(initial.attrib) if initial else {}

        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)
        frm.rowconfigure(1, weight=1)

        ttk.Label(frm, text='Private type (attrib "type")').grid(row=0, column=0, sticky="w", pady=(0, 6))
        self.var_type = tk.StringVar(value=((initial.attrib.get("type") or "") if initial else ""))
        ent_type = ttk.Entry(frm, textvariable=self.var_type)
        ent_type.grid(row=0, column=1, sticky="we", padx=(10, 0), pady=(0, 6))

        ttk.Label(frm, text="Inner XML (optional)").grid(row=1, column=0, sticky="nw")
        txt = tk.Text(frm, wrap="none", height=12)
        txt.grid(row=1, column=1, sticky="nsew", padx=(10, 0))
        if initial and (initial.inner_xml or ""):
            txt.insert("1.0", initial.inner_xml)

        btns = ttk.Frame(frm)
        btns.grid(row=2, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(btns, text="Cancel", command=self._cancel).pack(side="right")
        ttk.Button(btns, text="OK", command=lambda: self._ok(txt)).pack(side="right", padx=(0, 8))

        self.bind("<Escape>", lambda _e: self._cancel())
        ent_type.focus_set()

    def _ok(self, txt: tk.Text) -> None:
        typ = self.var_type.get().strip()
        if not typ:
            messagebox.showerror("Missing", "Private type is required", parent=self)
            return
        inner = txt.get("1.0", "end").rstrip("\n")
        attrib = dict(self._initial_attrib)
        attrib["type"] = typ
        self._result = PrivateItem(attrib=attrib, inner_xml=inner)
        self.destroy()

    def _cancel(self) -> None:
        self._result = None
        self.destroy()

    def show(self) -> PrivateItem | None:
        self.wait_window(self)
        return self._result


class PrivateTable(ttk.Frame):
    def __init__(self, parent: tk.Misc):
        super().__init__(parent)
        self.rows: list[PrivateItem] = []

        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x", pady=(6, 4))
        ttk.Button(toolbar, text="Add Private", command=self._add).pack(side="left")
        ttk.Button(toolbar, text="Edit", command=self._edit).pack(side="left", padx=(6, 0))
        ttk.Button(toolbar, text="Delete", command=self._delete).pack(side="left", padx=(6, 0))

        self.tree = ttk.Treeview(self, columns=["type", "preview"], show="headings", selectmode="browse")
        self.tree.heading("type", text="type")
        self.tree.heading("preview", text="inner XML (preview)")
        self.tree.column("type", width=360, anchor="w")
        self.tree.column("preview", width=520, anchor="w")

        y = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=y.set)

        self.tree.pack(fill="both", expand=True, side="left")
        y.pack(fill="y", side="right")

        self.tree.bind("<Double-1>", lambda _e: self._edit())

    def set_rows(self, rows: list[PrivateItem]) -> None:
        self.rows = [PrivateItem(attrib=dict(x.attrib), inner_xml=x.inner_xml) for x in rows]
        self.refresh()

    def get_rows(self) -> list[PrivateItem]:
        return [PrivateItem(attrib=dict(x.attrib), inner_xml=x.inner_xml) for x in self.rows]

    def _selected_index(self) -> int | None:
        sel = self.tree.selection()
        if not sel:
            return None
        try:
            return int(sel[0])
        except Exception:
            return None

    def refresh(self) -> None:
        for item in self.tree.get_children(""):
            self.tree.delete(item)
        for idx, row in enumerate(self.rows):
            typ = (row.attrib.get("type") or "")
            preview = (row.inner_xml or "").replace("\r", "").replace("\n", " ").strip()
            if len(preview) > 120:
                preview = preview[:120] + "..."
            self.tree.insert("", "end", iid=str(idx), values=[typ, preview])

    def _add(self) -> None:
        dlg = PrivateEditDialog(self, title="Add Private", initial=None)
        res = dlg.show()
        if res is None:
            return
        self.rows.append(res)
        self.refresh()
        self.tree.selection_set(str(len(self.rows) - 1))

    def _edit(self) -> None:
        idx = self._selected_index()
        if idx is None or idx < 0 or idx >= len(self.rows):
            return
        current = self.rows[idx]
        dlg = PrivateEditDialog(self, title="Edit Private", initial=current)
        res = dlg.show()
        if res is None:
            return
        self.rows[idx] = res
        self.refresh()
        self.tree.selection_set(str(idx))

    def _delete(self) -> None:
        idx = self._selected_index()
        if idx is None or idx < 0 or idx >= len(self.rows):
            return
        self.rows.pop(idx)
        self.refresh()
        if self.rows:
            self.tree.selection_set(str(min(idx, len(self.rows) - 1)))


class DORulePanel(ttk.Frame):
    """Aggregated view/editor for ALL DO-level <Private> blocks ("rules") in the LN.

    Requirements:
    - Show a leftmost column with DO name
    - Sort rules by DO
    - Double-click any row to edit
    """

    def __init__(
        self,
        parent: tk.Misc,
        *,
        on_add_rule=None,
        on_edit_rule=None,
        on_delete_rule=None,
        on_paste_rule=None,
    ):
        super().__init__(parent)

        self._on_add_rule = on_add_rule
        self._on_edit_rule = on_edit_rule
        self._on_delete_rule = on_delete_rule
        self._on_paste_rule = on_paste_rule

        self._rows: list[tuple[str, int, PrivateItem]] = []  # (do_name, private_index, item)
        self._clipboard: PrivateItem | None = None

        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x", pady=(6, 4))
        ttk.Button(toolbar, text="Add", command=self._cmd_add).pack(side="left")
        ttk.Button(toolbar, text="Copy", command=self._cmd_copy).pack(side="left", padx=(6, 0))
        ttk.Button(toolbar, text="Cut", command=self._cmd_cut).pack(side="left", padx=(6, 0))
        ttk.Button(toolbar, text="Paste", command=self._cmd_paste).pack(side="left", padx=(6, 0))
        ttk.Button(toolbar, text="Delete", command=self._cmd_delete).pack(side="left", padx=(6, 0))

        self.tree = ttk.Treeview(self, columns=["do", "type", "preview"], show="headings", selectmode="browse")
        self.tree.heading("do", text="DO")
        self.tree.heading("type", text="type")
        self.tree.heading("preview", text="inner XML (preview)")
        self.tree.column("do", width=160, anchor="w")
        self.tree.column("type", width=360, anchor="w")
        self.tree.column("preview", width=700, anchor="w")

        y = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=y.set)

        self.tree.pack(fill="both", expand=True, side="left")
        y.pack(fill="y", side="right")

        self.tree.bind("<Double-1>", lambda _e: self._cmd_edit())

        self.tree.bind("<Control-c>", lambda _e: self._cmd_copy())
        self.tree.bind("<Control-C>", lambda _e: self._cmd_copy())
        self.tree.bind("<Control-x>", lambda _e: self._cmd_cut())
        self.tree.bind("<Control-X>", lambda _e: self._cmd_cut())
        self.tree.bind("<Control-v>", lambda _e: self._cmd_paste())
        self.tree.bind("<Control-V>", lambda _e: self._cmd_paste())
        self.tree.bind("<Delete>", lambda _e: self._cmd_delete())
        self.tree.bind("<Button-3>", self._show_context_menu)

        self._menu = tk.Menu(self, tearoff=False)
        self._menu.add_command(label="Add", command=self._cmd_add)
        self._menu.add_separator()
        self._menu.add_command(label="Copy", command=self._cmd_copy)
        self._menu.add_command(label="Cut", command=self._cmd_cut)
        self._menu.add_command(label="Paste", command=self._cmd_paste)
        self._menu.add_command(label="Delete", command=self._cmd_delete)

    def _clone_private_item(self, p: PrivateItem) -> PrivateItem:
        return PrivateItem(attrib=dict(p.attrib), inner_xml=p.inner_xml)

    def _selected_index(self) -> int | None:
        sel = self.tree.selection()
        if not sel:
            return None
        try:
            return int(sel[0])
        except Exception:
            return None

    def _selected_row(self) -> tuple[str, int, PrivateItem] | None:
        idx = self._selected_index()
        if idx is None or idx < 0 or idx >= len(self._rows):
            return None
        return self._rows[idx]

    def _show_context_menu(self, event: tk.Event) -> None:
        try:
            row_id = self.tree.identify_row(event.y)
            if row_id:
                self.tree.selection_set(row_id)
        except Exception:
            pass

        has_sel = self._selected_row() is not None
        can_paste = self._clipboard is not None and has_sel
        self._menu.entryconfigure("Copy", state=("normal" if has_sel else "disabled"))
        self._menu.entryconfigure("Cut", state=("normal" if has_sel else "disabled"))
        self._menu.entryconfigure("Paste", state=("normal" if can_paste else "disabled"))
        self._menu.entryconfigure("Delete", state=("normal" if has_sel else "disabled"))

        try:
            self._menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                self._menu.grab_release()
            except Exception:
                pass

    def _cmd_add(self) -> None:
        if self._on_add_rule is not None:
            self._on_add_rule()

    def _cmd_edit(self) -> None:
        row = self._selected_row()
        if row is None:
            return
        do_name, private_index, _cur = row
        if self._on_edit_rule is not None:
            self._on_edit_rule(do_name, private_index)

    def _cmd_copy(self) -> None:
        row = self._selected_row()
        if row is None:
            return
        _do_name, _private_index, cur = row
        self._clipboard = self._clone_private_item(cur)
        try:
            self.clipboard_clear()
            self.clipboard_append(cur.attrib.get("type") or "")
        except Exception:
            pass

    def _cmd_cut(self) -> None:
        row = self._selected_row()
        if row is None:
            return
        self._cmd_copy()
        self._cmd_delete()

    def _cmd_paste(self) -> None:
        if self._clipboard is None:
            return
        row = self._selected_row()
        if row is None:
            return
        do_name, private_index, _cur = row
        if self._on_paste_rule is not None:
            self._on_paste_rule(do_name, private_index, self._clone_private_item(self._clipboard))

    def _cmd_delete(self) -> None:
        row = self._selected_row()
        if row is None:
            return
        do_name, private_index, _cur = row
        if self._on_delete_rule is not None:
            self._on_delete_rule(do_name, private_index)

    def set_from_dos(self, dos: list[DOItem]) -> None:
        rows: list[tuple[str, int, PrivateItem]] = []
        for do in sorted(list(dos or []), key=lambda x: (x.name or "")):
            privs = list(getattr(do, "privates", []) or [])
            for i, p in enumerate(privs):
                rows.append((do.name, i, p))
        self._rows = rows
        self.refresh()

    def refresh(self) -> None:
        for item in self.tree.get_children(""):
            self.tree.delete(item)
        for idx, (do_name, _i, row) in enumerate(self._rows):
            typ = (row.attrib.get("type") or "")
            preview = (row.inner_xml or "").replace("\r", "").replace("\n", " ").strip()
            if len(preview) > 120:
                preview = preview[:120] + "..."
            self.tree.insert("", "end", iid=str(idx), values=[do_name, typ, preview])

    # Edit is handled by double-click (opens shared editor dialog in parent).


class NewTemplateDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        lnode_infos: list[LNodeTypeInfo],
    ):
        super().__init__(parent)
        self.title("New LN template (LNodeType)")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self._result: dict[str, str] | None = None
        self._lnode_infos = list(lnode_infos)
        self._id_internal_update = False
        self._id_user_modified = False
        self._last_suggested_id = ""

        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text="Create from").grid(row=0, column=0, sticky="w", pady=4)
        self.var_base = tk.StringVar(value="(Blank)")
        base_values = ["(Blank)"] + [x.id for x in self._lnode_infos]
        cb_base = ttk.Combobox(frm, textvariable=self.var_base, values=base_values, width=62)
        cb_base.grid(row=0, column=1, sticky="we", padx=(10, 0), pady=4)

        ttk.Label(frm, text="File name").grid(row=1, column=0, sticky="w", pady=4)
        self.var_id = tk.StringVar(value="")
        ent_id = ttk.Entry(frm, textvariable=self.var_id, width=64)
        ent_id.grid(row=1, column=1, sticky="we", padx=(10, 0), pady=4)

        def _mark_user_modified(*_args) -> None:
            if self._id_internal_update:
                return
            self._id_user_modified = True

        self.var_id.trace_add("write", _mark_user_modified)

        ttk.Label(frm, text="lnClass").grid(row=2, column=0, sticky="w", pady=4)
        self.var_lnclass = tk.StringVar(value="")
        ent_ln = ttk.Entry(frm, textvariable=self.var_lnclass, width=64)
        ent_ln.grid(row=2, column=1, sticky="we", padx=(10, 0), pady=4)

        ttk.Label(frm, text="desc (optional)").grid(row=3, column=0, sticky="w", pady=4)
        self.var_desc = tk.StringVar(value="")
        ent_desc = ttk.Entry(frm, textvariable=self.var_desc, width=64)
        ent_desc.grid(row=3, column=1, sticky="we", padx=(10, 0), pady=4)

        hint = ttk.Label(
            frm,
            text=(
                "Tip: If you pick an existing template, DO/Private blocks will be copied, then name/lnClass/desc updated."
            ),
        )
        hint.grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))

        btns = ttk.Frame(frm)
        btns.grid(row=5, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Cancel", command=self._cancel).pack(side="right")
        ttk.Button(btns, text="Create", command=self._ok).pack(side="right", padx=(0, 8))

        self.bind("<Escape>", lambda _e: self._cancel())

        def unique_copy_name(base: str) -> str:
            # User requested suffix '_cpoy' (typo kept as-is).
            existing = {x.id for x in self._lnode_infos}
            candidate = f"{base}_cpoy"
            if candidate not in existing:
                return candidate
            i = 2
            while True:
                candidate = f"{base}_cpoy{i}"
                if candidate not in existing:
                    return candidate
                i += 1

        def prefill(*_args) -> None:
            base_id = self.var_base.get().strip()
            if base_id == "(Blank)":
                return
            info = next((x for x in self._lnode_infos if x.id == base_id), None)
            if info is None:
                return

            # Auto-suggest File name when creating from an existing template.
            cur = self.var_id.get().strip()
            if (not self._id_user_modified) or (not cur) or (cur == self._last_suggested_id):
                suggested = unique_copy_name(base_id)
                self._id_internal_update = True
                try:
                    self.var_id.set(suggested)
                    self._last_suggested_id = suggested
                finally:
                    self._id_internal_update = False

            if not self.var_lnclass.get().strip() and info.ln_class:
                self.var_lnclass.set(info.ln_class)
            if not self.var_desc.get().strip() and info.desc:
                self.var_desc.set(info.desc)

        self.var_base.trace_add("write", prefill)
        prefill()

        ent_id.focus_set()

    def _ok(self) -> None:
        new_id = self.var_id.get().strip()
        ln_class = self.var_lnclass.get().strip()
        desc = self.var_desc.get().strip()
        base_id = self.var_base.get().strip()

        if not new_id:
            messagebox.showerror("Missing", "File name is required", parent=self)
            return
        if not ln_class:
            messagebox.showerror("Missing", "lnClass is required", parent=self)
            return

        self._result = {
            "base_id": base_id,
            "id": new_id,
            "lnClass": ln_class,
            "desc": desc,
        }
        self.destroy()

    def _cancel(self) -> None:
        self._result = None
        self.destroy()

    def show(self) -> dict[str, str] | None:
        self.wait_window(self)
        return self._result


class LNodeTypeEditor(ttk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        catalog: TypeCatalog,
        iec61850_dir: Path,
        create_instance_callback=None,
    ):
        super().__init__(parent)
        self.catalog = catalog
        self.iec61850_dir = Path(iec61850_dir)
        self._create_instance_callback = create_instance_callback
        self.model: LNodeTypeModel | None = None
        self.dirty = False
        self._saved_sig_full: tuple | None = None

        self._all_lnode_infos = list(catalog.lnode_types)
        self._all_lnode_ids = [x.id for x in self._all_lnode_infos]

        row1 = ttk.Frame(self, padding=(10, 10, 10, 0))
        row1.pack(fill="x")

        ttk.Button(row1, text="New", command=self.new_template).pack(side="left")
        ttk.Button(row1, text="Open", command=self.open_template).pack(side="left", padx=(8, 0))
        self.btn_save = ttk.Button(row1, text="Save", command=self.save_current)
        self.btn_save.pack(side="left", padx=(8, 0))
        ttk.Button(row1, text="Save As", command=self.save_as).pack(side="left", padx=(8, 0))

        self.btn_create_instance = ttk.Button(
            row1,
            text="Create instance with this template",
            command=self.create_instance_with_this_template,
            state="disabled",
        )
        self.btn_create_instance.pack(side="left", padx=(18, 0))

        row2 = ttk.Frame(self, padding=(10, 8, 10, 0))
        row2.pack(fill="x")

        # Search for template list (fuzzy by token contains)
        ttk.Label(row2, text="Search").pack(side="left")
        self.var_ln_filter = tk.StringVar(value="")
        ent_filter = ttk.Entry(row2, textvariable=self.var_ln_filter, width=28)
        ent_filter.pack(side="left", padx=(8, 0))

        self.var_selected = tk.StringVar()
        self.cb = ttk.Combobox(
            row2,
            textvariable=self.var_selected,
            values=self._all_lnode_ids,
            width=66,
        )
        self.cb.pack(side="left", padx=(10, 0))
        ttk.Button(row2, text="Load", command=self.load_selected).pack(side="left", padx=(8, 0))

        self.lbl_ln_match = ttk.Label(row2, text="")
        self.lbl_ln_match.pack(side="left", padx=(10, 0))

        self.lbl_meta = ttk.Label(row2, text="")
        self.lbl_meta.pack(side="left", padx=(12, 0))

        self.lbl_saved = ttk.Label(row2, text="")
        self.lbl_saved.pack(side="left", padx=(12, 0))

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=10, pady=10)

        self.table = DOTable(self.nb, do_types=catalog.do_types)
        self.nb.add(self.table, text="DO")
        self.table.on_rules = self._do_rules_for_index

        self.rule_panel = DORulePanel(
            self.nb,
            on_add_rule=self._rule_tab_add,
            on_edit_rule=self._rule_tab_edit,
            on_delete_rule=self._rule_tab_delete,
            on_paste_rule=self._rule_tab_paste_after,
        )
        self.nb.add(self.rule_panel, text="Rule")

        self.private_table = PrivateTable(self.nb)
        self.nb.add(self.private_table, text="Private")

        # Mark dirty when table changes by wrapping refresh
        orig_refresh = self.table.refresh

        def refresh_with_dirty() -> None:
            orig_refresh()
            self._update_dirty_from_view()
            self._refresh_all_rules_view()

        self.table.refresh = refresh_with_dirty  # type: ignore[method-assign]

        orig_priv_refresh = self.private_table.refresh

        def priv_refresh_with_dirty() -> None:
            orig_priv_refresh()
            self._update_dirty_from_view()

        self.private_table.refresh = priv_refresh_with_dirty  # type: ignore[method-assign]

        # Keep aggregated rule list in sync with DO changes/selection changes.
        self.table.tree.bind("<<TreeviewSelect>>", lambda _e: self._refresh_all_rules_view())

        self._update_save_button()
        self._update_create_instance_button()

        def apply_ln_filter(*_args) -> None:
            raw = self.var_ln_filter.get().strip().lower()
            if not raw:
                filtered_infos = self._all_lnode_infos
            else:
                tokens = [t for t in raw.split() if t]

                def ok(info: LNodeTypeInfo) -> bool:
                    hay = f"{info.id} {info.ln_class} {info.desc}".lower()
                    return all(t in hay for t in tokens)

                filtered_infos = [i for i in self._all_lnode_infos if ok(i)]

            filtered_ids = [i.id for i in filtered_infos]

            cur = self.var_selected.get().strip()
            if cur and cur not in filtered_ids:
                filtered_ids = [cur] + filtered_ids

            max_show = 1200
            shown = filtered_ids[:max_show]
            self.cb["values"] = shown
            suffix = "" if len(filtered_ids) <= max_show else f" (showing first {max_show})"
            self.lbl_ln_match.configure(text=f"{len(filtered_ids)} match{'' if len(filtered_ids)==1 else 'es'}{suffix}")

        self.var_ln_filter.trace_add("write", apply_ln_filter)
        apply_ln_filter()
        self._apply_ln_filter = apply_ln_filter

        # UX shortcuts
        self.cb.bind("<Return>", lambda _e: self.load_selected())
        self.bind_all("<Control-f>", lambda _e: ent_filter.focus_set())

    def _update_create_instance_button(self) -> None:
        try:
            enabled = (self.model is not None) and (self._create_instance_callback is not None)
            self.btn_create_instance.configure(state=("normal" if enabled else "disabled"))
        except Exception:
            pass

    def create_instance_with_this_template(self) -> None:
        if self.model is None:
            return
        if self._create_instance_callback is None:
            messagebox.showerror("Not available", "Instance editor is not available in this context.", parent=self)
            return
        try:
            self._create_instance_callback(self.model)
        except Exception as e:
            messagebox.showerror("Create failed", str(e), parent=self)

    def open_template(self) -> None:
        if self.dirty and not messagebox.askyesno("Unsaved", "Discard unsaved changes?", parent=self):
            return

        initialdir = self.iec61850_dir / "LNodeType"
        path = filedialog.askopenfilename(
            parent=self,
            title="Open LN template (LNodeType)",
            initialdir=os.fspath(initialdir),
            filetypes=[("XML", "*.xml"), ("All", "*")],
        )
        if not path:
            return

        selected_path = Path(path)

        def local_name(tag: str) -> str:
            if tag.startswith("{"):
                return tag.split("}", 1)[1]
            return tag

        try:
            import xml.etree.ElementTree as ET

            tree = ET.parse(selected_path)
            root = tree.getroot()
            ln_el = None
            for el in root.iter():
                if isinstance(el.tag, str) and local_name(el.tag) == "LNodeType":
                    ln_el = el
                    break
            if ln_el is None:
                raise ValueError("No <LNodeType> found")

            ln_id = (ln_el.attrib.get("id") or "").strip() or selected_path.stem
            ln_class = (ln_el.attrib.get("lnClass") or "").strip()
            desc = (ln_el.attrib.get("desc") or "").strip()
        except Exception as e:
            messagebox.showerror("Open failed", f"{selected_path}\n\n{e}", parent=self)
            return

        info = LNodeTypeInfo(id=ln_id, ln_class=ln_class, desc=desc, file_path=selected_path)

        # Ensure catalog includes this template so it can be selected.
        existing = next((x for x in self.catalog.lnode_types if x.id == ln_id), None)
        if existing is None:
            self.catalog.lnode_types.append(info)
        else:
            # Update file_path/desc if opening a different path for same id
            self.catalog.lnode_types = [info if x.id == ln_id else x for x in self.catalog.lnode_types]

        self.catalog.lnode_types.sort(key=lambda x: (x.ln_class, x.id))
        self._all_lnode_infos = list(self.catalog.lnode_types)
        self._all_lnode_ids = [x.id for x in self._all_lnode_infos]
        try:
            self._apply_ln_filter()
        except Exception:
            self.cb["values"] = self._all_lnode_ids

        self.var_selected.set(ln_id)
        self.load_selected()

    def _flash_saved(self, text: str, *, ms: int = 1800) -> None:
        self.lbl_saved.configure(text=text)

        def clear() -> None:
            # Only clear if unchanged (avoid racing with newer messages)
            if self.lbl_saved.cget("text") == text:
                self.lbl_saved.configure(text="")

        self.after(ms, clear)

    def _get_info(self, lnode_id: str) -> LNodeTypeInfo | None:
        for info in self.catalog.lnode_types:
            if info.id == lnode_id:
                return info
        return None

    def load_selected(self) -> None:
        lnode_id = self.var_selected.get().strip()
        if not lnode_id:
            return
        if self.dirty and not messagebox.askyesno("Unsaved", "Discard unsaved changes?", parent=self):
            return

        info = self._get_info(lnode_id)
        if info is None:
            messagebox.showerror("Not found", f"Unknown template name: {lnode_id}", parent=self)
            return

        try:
            self.model = load_lnode_type(info)
        except Exception as e:
            messagebox.showerror("Load failed", str(e), parent=self)
            return

        self.table.set_rows(self.model.dos)
        self.private_table.set_rows(getattr(self.model, "privates", []) or [])

        # Default-select first DO so the Rule tab has content.
        try:
            if self.table.rows:
                self.table.tree.selection_set("0")
        except Exception:
            pass

        self._refresh_all_rules_view()
        self._saved_sig_full = self._signature_full(
            dos=self.table.get_rows(),
            privates=self.private_table.get_rows(),
        )
        self.dirty = False
        self._update_save_button()
        self._update_create_instance_button()
        meta = f"lnClass={info.ln_class}  file={os.fspath(info.file_path)}"
        if info.desc:
            meta = meta + f"  desc={info.desc}"
        self.lbl_meta.configure(text=meta)

    def save_current(self) -> None:
        if self.model is None:
            # Silent no-op for Ctrl+S and Save
            return

        self.model.dos = self.table.get_rows()
        self.model.privates = self.private_table.get_rows()
        try:
            save_lnode_type(self.model, make_backup=True)
        except Exception as e:
            messagebox.showerror("Save failed", str(e), parent=self)
            return

        self._saved_sig_full = self._signature_full(
            dos=self.model.dos,
            privates=self.model.privates,
        )
        self.dirty = False
        self._update_save_button()
        self._refresh_all_rules_view()
        self._flash_saved("Saved")

    def save_as(self) -> None:
        if self.model is None:
            return

        current_path = self.model.info.file_path
        initialdir = current_path.parent if current_path else Path.cwd()
        initialfile = current_path.name if current_path else f"{self.model.info.id}.xml"

        filename = filedialog.asksaveasfilename(
            parent=self,
            title="Save As",
            initialdir=os.fspath(initialdir),
            initialfile=initialfile,
            defaultextension=".xml",
            filetypes=[("XML files", "*.xml"), ("All files", "*.*")],
        )
        if not filename:
            return

        target_path = Path(filename)
        if target_path.suffix.lower() != ".xml":
            target_path = target_path.with_suffix(".xml")

        try:
            same_target = target_path.resolve() == current_path.resolve()
        except Exception:
            same_target = os.fspath(target_path) == os.fspath(current_path)

        if same_target:
            self.save_current()
            return

        if target_path.exists():
            if not messagebox.askyesno(
                "Overwrite?",
                f"File already exists:\n{os.fspath(target_path)}\n\nOverwrite it?",
                parent=self,
            ):
                return

        # Optionally align LNodeType name (XML attribute 'id') with new filename stem.
        new_id = target_path.stem.strip()
        update_id = False
        if new_id and new_id != self.model.info.id:
            update_id = messagebox.askyesno(
                "Update template name?",
                f"New file name suggests name '{new_id}'.\n\nUpdate <LNodeType id> to match this name?",
                parent=self,
            )

        self.model.dos = self.table.get_rows()
        self.model.privates = self.private_table.get_rows()

        new_info_id = new_id if (update_id and new_id) else self.model.info.id
        new_ln_class = self.model.info.ln_class
        new_desc = self.model.info.desc

        if update_id and new_id:
            self.model.lnode_attrib["id"] = new_id

        self.model.info = LNodeTypeInfo(
            id=new_info_id,
            ln_class=new_ln_class,
            desc=new_desc,
            file_path=target_path,
        )

        try:
            save_lnode_type(self.model, make_backup=False, target_path=target_path)
        except Exception as e:
            messagebox.showerror("Save As failed", str(e), parent=self)
            return

        # If we created a new name, add it to the catalog list so it becomes selectable.
        if new_info_id and all(i.id != new_info_id for i in self.catalog.lnode_types):
            self.catalog.lnode_types.append(self.model.info)
            self.catalog.lnode_types.sort(key=lambda x: (x.ln_class, x.id))
            self._all_lnode_infos = list(self.catalog.lnode_types)
            self._all_lnode_ids = [x.id for x in self._all_lnode_infos]
            try:
                self._apply_ln_filter()
            except Exception:
                self.cb["values"] = self._all_lnode_ids

        self.var_selected.set(self.model.info.id)
        meta = f"lnClass={self.model.info.ln_class}  file={os.fspath(self.model.info.file_path)}"
        if self.model.info.desc:
            meta = meta + f"  desc={self.model.info.desc}"
        self.lbl_meta.configure(text=meta)

        self._saved_sig_full = self._signature_full(
            dos=self.model.dos,
            privates=self.model.privates,
        )
        self.dirty = False
        self._update_save_button()
        self._flash_saved("Saved As")

    def new_template(self) -> None:
        if self.dirty and not messagebox.askyesno("Unsaved", "Discard unsaved changes?", parent=self):
            return

        dlg = NewTemplateDialog(self, lnode_infos=self.catalog.lnode_types)
        res = dlg.show()
        if res is None:
            return

        base_id = res["base_id"].strip()
        new_id = res["id"].strip()
        ln_class = res["lnClass"].strip()
        desc = res.get("desc", "").strip()

        target_path = self.iec61850_dir / "LNodeType" / f"{new_id}.xml"
        if target_path.exists():
            if not messagebox.askyesno(
                "Overwrite?",
                f"Template already exists:\n{os.fspath(target_path)}\n\nOverwrite it?",
                parent=self,
            ):
                return

        if base_id and base_id != "(Blank)":
            base_info = self._get_info(base_id)
            if base_info is None:
                messagebox.showerror("Not found", f"Unknown base template: {base_id}", parent=self)
                return
            try:
                base_model = load_lnode_type(base_info)
            except Exception as e:
                messagebox.showerror("Load failed", str(e), parent=self)
                return

            # Deep-copy content (including DO-level rule blocks)
            dos = [
                DOItem(
                    name=x.name,
                    do_type=x.do_type,
                    privates=[PrivateItem(attrib=dict(p.attrib), inner_xml=p.inner_xml) for p in (getattr(x, "privates", []) or [])],
                )
                for x in base_model.dos
            ]
            privs = [PrivateItem(attrib=dict(x.attrib), inner_xml=x.inner_xml) for x in (base_model.privates or [])]
            attrib = dict(base_model.lnode_attrib or {})
        else:
            dos = []
            privs = []
            attrib = {}

        attrib["id"] = new_id
        attrib["lnClass"] = ln_class
        if desc:
            attrib["desc"] = desc
        else:
            attrib.pop("desc", None)

        info = LNodeTypeInfo(id=new_id, ln_class=ln_class, desc=desc, file_path=target_path)
        self.model = LNodeTypeModel(info=info, lnode_attrib=attrib, dos=dos, privates=privs)

        try:
            save_lnode_type(self.model, make_backup=False, target_path=target_path)
        except Exception as e:
            messagebox.showerror("Create failed", str(e), parent=self)
            return

        # Update catalog so it becomes selectable
        existing = next((x for x in self.catalog.lnode_types if x.id == new_id), None)
        if existing is None:
            self.catalog.lnode_types.append(info)
        else:
            self.catalog.lnode_types = [info if x.id == new_id else x for x in self.catalog.lnode_types]
        self.catalog.lnode_types.sort(key=lambda x: (x.ln_class, x.id))
        self._all_lnode_infos = list(self.catalog.lnode_types)
        self._all_lnode_ids = [x.id for x in self._all_lnode_infos]
        try:
            self._apply_ln_filter()
        except Exception:
            self.cb["values"] = self._all_lnode_ids

        # Load into UI
        self.var_selected.set(new_id)
        self.table.set_rows(self.model.dos)
        self.private_table.set_rows(self.model.privates)

        try:
            if self.table.rows:
                self.table.tree.selection_set("0")
        except Exception:
            pass
        self._refresh_all_rules_view()

        self._saved_sig_full = self._signature_full(dos=self.model.dos, privates=self.model.privates)
        self.dirty = False
        self._update_save_button()
        self._update_create_instance_button()
        meta = f"lnClass={info.ln_class}  file={os.fspath(info.file_path)}"
        if info.desc:
            meta = meta + f"  desc={info.desc}"
        self.lbl_meta.configure(text=meta)
        self._flash_saved("Created")

    def _signature_full(self, *, dos: list[DOItem], privates: list[PrivateItem]) -> tuple:
        sig_dos = tuple(
            (
                x.name,
                x.do_type,
                tuple(
                    (
                        tuple(sorted((k, str(v)) for k, v in (p.attrib or {}).items() if str(v) != "")),
                        (p.inner_xml or ""),
                    )
                    for p in (getattr(x, "privates", []) or [])
                ),
            )
            for x in dos
        )
        sig_priv = tuple(
            (
                tuple(sorted((k, str(v)) for k, v in (x.attrib or {}).items() if str(v) != "")),
                (x.inner_xml or ""),
            )
            for x in privates
        )
        return (sig_dos, sig_priv)

    def _refresh_all_rules_view(self) -> None:
        try:
            self.rule_panel.set_from_dos(self.table.get_rows())
        except Exception:
            try:
                self.rule_panel.set_from_dos(self.table.rows)
            except Exception:
                pass

    def _selected_do_index(self) -> int | None:
        try:
            sel = self.table.tree.selection()
            if not sel:
                return None
            return int(sel[0])
        except Exception:
            return None

    def _do_index_by_name(self, do_name: str) -> int | None:
        do_name = (do_name or "").strip()
        if not do_name:
            return None
        for i, x in enumerate(self.table.rows):
            if (x.name or "").strip() == do_name:
                return i
        return None

    def _do_type_id_for_do_name(self, do_name: str) -> str:
        idx = self._do_index_by_name(do_name)
        if idx is None:
            return ""
        return (self.table.rows[idx].do_type or "").strip()

    def _get_rule_text_for_do(self, do_name: str, private_type: str) -> str:
        private_type = (private_type or "").strip()
        idx = self._do_index_by_name(do_name)
        if idx is None or not private_type:
            return ""
        cur = self.table.rows[idx]
        for p in (getattr(cur, "privates", []) or []):
            if (p.attrib.get("type") or "").strip() == private_type:
                return (p.inner_xml or "")
        return ""

    def _apply_rule_text_for_do(self, do_name: str, private_type: str, text: str) -> None:
        """Create/update/delete a DO-level <Private> by type for the selected DO.

        If text is blank/whitespace -> delete.
        """
        do_name = (do_name or "").strip()
        private_type = (private_type or "").strip()
        if not do_name or not private_type:
            return

        idx = self._do_index_by_name(do_name)
        if idx is None:
            return

        cur = self.table.rows[idx]
        privs = list(getattr(cur, "privates", []) or [])
        existing_i = next(
            (i for i, p in enumerate(privs) if (p.attrib.get("type") or "").strip() == private_type),
            None,
        )

        txt = (text or "").rstrip("\n")
        if not txt.strip():
            # delete
            if existing_i is None:
                return
            privs.pop(existing_i)
            self.table.rows[idx] = DOItem(name=cur.name, do_type=cur.do_type, privates=privs)
            self._refresh_all_rules_view()
            self._update_dirty_from_view()
            return

        item = PrivateItem(attrib={"type": private_type}, inner_xml=txt)
        self._normalize_rule_item_inplace(item)
        if existing_i is None:
            privs.append(item)
        else:
            privs[existing_i] = item
        self.table.rows[idx] = DOItem(name=cur.name, do_type=cur.do_type, privates=privs)
        self._refresh_all_rules_view()
        self._update_dirty_from_view()

    def _apply_rule_result(
        self,
        *,
        old_do_name: str | None,
        old_private_index: int | None,
        new_do_name: str,
        new_item: PrivateItem,
        insert_after_index: int | None = None,
    ) -> None:
        """Apply a rule edit/add, optionally moving between DOs."""
        old_do_name = (old_do_name or "").strip()
        new_do_name = (new_do_name or "").strip()
        if not new_do_name:
            return

        # Remove old if editing existing.
        if old_do_name and old_private_index is not None:
            old_idx = self._do_index_by_name(old_do_name)
            if old_idx is not None:
                cur = self.table.rows[old_idx]
                privs = list(getattr(cur, "privates", []) or [])
                if 0 <= old_private_index < len(privs):
                    privs.pop(old_private_index)
                    self.table.rows[old_idx] = DOItem(name=cur.name, do_type=cur.do_type, privates=privs)

        # Insert into target.
        new_idx = self._do_index_by_name(new_do_name)
        if new_idx is None:
            return
        cur2 = self.table.rows[new_idx]
        privs2 = list(getattr(cur2, "privates", []) or [])
        if insert_after_index is None:
            privs2.append(new_item)
        else:
            at = max(0, min(len(privs2), insert_after_index + 1))
            privs2.insert(at, new_item)
        self.table.rows[new_idx] = DOItem(name=cur2.name, do_type=cur2.do_type, privates=privs2)

        self._refresh_all_rules_view()
        self._update_dirty_from_view()

    def _rule_tab_add(self) -> None:
        do_names = [x.name for x in (self.table.rows or []) if (x.name or "").strip()]
        parent_win = self.winfo_toplevel()
        dlg = RuleEditDialog(
            parent_win,
            title="Add rule",
            do_names=do_names,
            initial_do_name=None,
            initial_rule_type="SchneiderElectric-PowerLogic-RulesRatio",
            require_do_selection=True,
            get_rule_text=self._get_rule_text_for_do,
            apply_rule_text=self._apply_rule_text_for_do,
            get_relevancy_condition_candidates=lambda cur_do: self._setting_do_candidates(current_do_name=cur_do),
            get_relevancy_condition_ref=lambda cond_do: self._setting_do_value_ref(cond_do),
            get_enum_options_for_condition_do=lambda cond_do: self._enum_options_for_condition_do(cond_do),
            on_generate=lambda do_name, kind: self._build_ratio_rule_body(
                do_type_id=self._do_type_id_for_do_name(do_name),
                kind=kind,
            ),
        )
        dlg.show()

    def _rule_tab_edit(self, do_name: str, private_index: int) -> None:
        idx = self._do_index_by_name(do_name)
        if idx is None:
            return
        cur = self.table.rows[idx]
        privs = list(getattr(cur, "privates", []) or [])
        if private_index < 0 or private_index >= len(privs):
            return
        current_item = privs[private_index]
        current_type = (current_item.attrib.get("type") or "").strip()
        do_names = [x.name for x in (self.table.rows or []) if (x.name or "").strip()]

        parent_win = self.winfo_toplevel()
        dlg = RuleEditDialog(
            parent_win,
            title=f"Edit rule (DO {do_name})",
            do_names=do_names,
            initial_do_name=do_name,
            initial_rule_type=(current_type or "SchneiderElectric-PowerLogic-RulesRatio"),
            require_do_selection=True,
            get_rule_text=self._get_rule_text_for_do,
            apply_rule_text=self._apply_rule_text_for_do,
            get_relevancy_condition_candidates=lambda cur_do: self._setting_do_candidates(current_do_name=cur_do),
            get_relevancy_condition_ref=lambda cond_do: self._setting_do_value_ref(cond_do),
            get_enum_options_for_condition_do=lambda cond_do: self._enum_options_for_condition_do(cond_do),
            on_generate=lambda sel_do_name, kind: self._build_ratio_rule_body(
                do_type_id=self._do_type_id_for_do_name(sel_do_name),
                kind=kind,
            ),
        )
        dlg.show()

    def _rule_tab_delete(self, do_name: str, private_index: int) -> None:
        idx = self._do_index_by_name(do_name)
        if idx is None:
            return
        cur = self.table.rows[idx]
        privs = list(getattr(cur, "privates", []) or [])
        if private_index < 0 or private_index >= len(privs):
            return
        privs.pop(private_index)
        self.table.rows[idx] = DOItem(name=cur.name, do_type=cur.do_type, privates=privs)
        self._refresh_all_rules_view()
        self._update_dirty_from_view()

    def _rule_tab_paste_after(self, do_name: str, private_index: int, item: PrivateItem) -> None:
        self._normalize_rule_item_inplace(item)
        self._apply_rule_result(
            old_do_name=None,
            old_private_index=None,
            new_do_name=do_name,
            new_item=item,
            insert_after_index=private_index,
        )

    def _normalize_rule_item_inplace(self, item: PrivateItem) -> None:
        """Normalize rule formatting for known CDATA-based rules."""
        if item is None:
            return
        typ = (item.attrib.get("type") or "").strip()
        if typ in {
            "SchneiderElectric-PowerLogic-RulesRatio",
            "SchneiderElectric-PowerLogic-RulesRelevancy",
            "SchneiderElectric-PowerLogic-RulesDependency",
        }:
            txt = (item.inner_xml or "").rstrip("\n")
            if txt.strip():
                item.inner_xml = txt + "\n"
            else:
                item.inner_xml = ""

    def _do_type_sdo_names(self, do_type_id: str) -> list[str]:
        """Return ordered list of direct SDO names for a DOType."""
        do_type_id = (do_type_id or "").strip()
        if not do_type_id:
            return []

        cache = getattr(self, "_do_sdo_cache", None)
        if cache is None:
            cache = {}
            setattr(self, "_do_sdo_cache", cache)
        if do_type_id in cache:
            return list(cache[do_type_id])

        p = self._do_type_file_path(do_type_id)
        if p is None:
            cache[do_type_id] = []
            return []

        try:
            root = ET.parse(p).getroot()
        except Exception:
            cache[do_type_id] = []
            return []

        ns = ""
        if isinstance(root.tag, str) and root.tag.startswith("{"):
            ns = root.tag.split("}", 1)[0][1:]

        def q(tag: str) -> str:
            return f"{{{ns}}}{tag}" if ns else tag

        # Find the matching DOType element by id
        do_el = None
        for cand in root.findall(f".//{q('DOType')}"):
            if (cand.attrib.get("id") or "").strip() == do_type_id:
                do_el = cand
                break
        if do_el is None:
            cache[do_type_id] = []
            return []

        names: list[str] = []
        for sdo in do_el.findall(q("SDO")):
            nm = (sdo.attrib.get("name") or "").strip()
            if nm:
                names.append(nm)

        cache[do_type_id] = list(names)
        return list(names)

    def _do_type_cdc(self, do_type_id: str) -> str:
        """Return DOType@cdc for a DOType id (uppercased), or ""."""
        do_type_id = (do_type_id or "").strip()
        if not do_type_id:
            return ""

        cache = getattr(self, "_do_cdc_cache", None)
        if cache is None:
            cache = {}
            setattr(self, "_do_cdc_cache", cache)
        if do_type_id in cache:
            return str(cache[do_type_id] or "")

        p = self._do_type_file_path(do_type_id)
        if p is None:
            cache[do_type_id] = ""
            return ""

        try:
            root = ET.parse(p).getroot()
        except Exception:
            cache[do_type_id] = ""
            return ""

        ns = ""
        if isinstance(root.tag, str) and root.tag.startswith("{"):
            ns = root.tag.split("}", 1)[0][1:]

        def q(tag: str) -> str:
            return f"{{{ns}}}{tag}" if ns else tag

        do_el = None
        for cand in root.findall(f".//{q('DOType')}"):
            if (cand.attrib.get("id") or "").strip() == do_type_id:
                do_el = cand
                break
        if do_el is None:
            cache[do_type_id] = ""
            return ""

        cdc = (do_el.attrib.get("cdc") or "").strip().upper()
        cache[do_type_id] = cdc
        return cdc

    def _do_type_file_path(self, do_type_id: str) -> Path | None:
        """Best-effort lookup of which XML file contains a given DOType id."""
        do_type_id = (do_type_id or "").strip()
        if not do_type_id:
            return None

        cache = getattr(self, "_do_type_file_cache", None)
        if cache is None:
            cache = {}
            setattr(self, "_do_type_file_cache", cache)

        if do_type_id in cache:
            return cache[do_type_id]

        do_dir = self.iec61850_dir / "DOType"
        if not do_dir.exists():
            cache[do_type_id] = None
            return None

        # Build an index lazily (amortized) by scanning DOType XML files once.
        index = getattr(self, "_do_type_file_index", None)
        if index is None:
            index = {}
            for p in do_dir.rglob("*.xml"):
                try:
                    root = ET.parse(p).getroot()
                except Exception:
                    continue
                for el in root.iter():
                    if not isinstance(el.tag, str):
                        continue
                    if not el.tag.endswith("DOType"):
                        continue
                    id_ = (el.attrib.get("id") or "").strip()
                    if id_ and id_ not in index:
                        index[id_] = p
            setattr(self, "_do_type_file_index", index)

        found = index.get(do_type_id)
        cache[do_type_id] = found
        return found

    def _enum_type_file_path(self, enum_type_id: str) -> Path | None:
        """Best-effort lookup of which XML file contains a given EnumType id."""
        enum_type_id = (enum_type_id or "").strip()
        if not enum_type_id:
            return None

        cache = getattr(self, "_enum_type_file_cache", None)
        if cache is None:
            cache = {}
            setattr(self, "_enum_type_file_cache", cache)
        if enum_type_id in cache:
            return cache[enum_type_id]

        enum_dir = self.iec61850_dir / "EnumType"
        if not enum_dir.exists():
            cache[enum_type_id] = None
            return None

        index = getattr(self, "_enum_type_file_index", None)
        if index is None:
            index = {}
            for p in enum_dir.rglob("*.xml"):
                try:
                    root = ET.parse(p).getroot()
                except Exception:
                    continue
                for el in root.iter():
                    if not isinstance(el.tag, str):
                        continue
                    if not el.tag.endswith("EnumType"):
                        continue
                    id_ = (el.attrib.get("id") or "").strip()
                    if id_ and id_ not in index:
                        index[id_] = p
            setattr(self, "_enum_type_file_index", index)

        found = index.get(enum_type_id)
        cache[enum_type_id] = found
        return found

    def _da_type_file_path(self, da_type_id: str) -> Path | None:
        """Best-effort lookup of which XML file contains a given DAType id."""
        da_type_id = (da_type_id or "").strip()
        if not da_type_id:
            return None

        cache = getattr(self, "_da_type_file_cache", None)
        if cache is None:
            cache = {}
            setattr(self, "_da_type_file_cache", cache)
        if da_type_id in cache:
            return cache[da_type_id]

        da_dir = self.iec61850_dir / "DAType"
        if not da_dir.exists():
            cache[da_type_id] = None
            return None

        index = getattr(self, "_da_type_file_index", None)
        if index is None:
            index = {}
            for p in da_dir.rglob("*.xml"):
                try:
                    root = ET.parse(p).getroot()
                except Exception:
                    continue
                for el in root.iter():
                    if not isinstance(el.tag, str):
                        continue
                    if not el.tag.endswith("DAType"):
                        continue
                    id_ = (el.attrib.get("id") or "").strip()
                    if id_ and id_ not in index:
                        index[id_] = p
            setattr(self, "_da_type_file_index", index)

        found = index.get(da_type_id)
        cache[da_type_id] = found
        return found

    def _enum_values_for_enum_type(self, enum_type_id: str) -> list[str]:
        """Return ordered EnumVal texts for an EnumType id, or []."""
        enum_type_id = (enum_type_id or "").strip()
        if not enum_type_id:
            return []

        cache = getattr(self, "_enum_values_cache", None)
        if cache is None:
            cache = {}
            setattr(self, "_enum_values_cache", cache)
        if enum_type_id in cache:
            return list(cache[enum_type_id])

        p = self._enum_type_file_path(enum_type_id)
        if p is None:
            cache[enum_type_id] = []
            return []

        try:
            root = ET.parse(p).getroot()
        except Exception:
            cache[enum_type_id] = []
            return []

        ns = ""
        if isinstance(root.tag, str) and root.tag.startswith("{"):
            ns = root.tag.split("}", 1)[0][1:]

        def q(tag: str) -> str:
            return f"{{{ns}}}{tag}" if ns else tag

        enum_el = None
        for cand in root.findall(f".//{q('EnumType')}"):
            if (cand.attrib.get("id") or "").strip() == enum_type_id:
                enum_el = cand
                break
        if enum_el is None:
            cache[enum_type_id] = []
            return []

        items: list[tuple[int | None, str]] = []
        for ev in enum_el.findall(q("EnumVal")):
            txt = (ev.text or "").strip()
            if not txt:
                continue
            ord_s = (ev.attrib.get("ord") or "").strip()
            ord_i: int | None = None
            try:
                ord_i = int(ord_s) if ord_s else None
            except Exception:
                ord_i = None
            items.append((ord_i, txt))

        # If all ords are present, order by ord; else keep file order.
        if items and all(o is not None for o, _t in items):
            items.sort(key=lambda x: int(x[0] or 0))

        out = [t for _o, t in items]
        cache[enum_type_id] = list(out)
        return list(out)

    def _enum_options_for_condition_do(self, do_name: str) -> list[str]:
        """If the condition DO resolves to an Enum leaf (setVal / setMag.f/i), return options."""
        do_name = (do_name or "").strip()
        if not do_name or not re.match(r"^[A-Za-z0-9_]+$", do_name):
            return []

        idx = self._do_index_by_name(do_name)
        if idx is None:
            return []
        do_type_id = (self.table.rows[idx].do_type or "").strip()
        if not do_type_id:
            return []

        ref = "setVal"
        try:
            ref = (self._get_relevancy_condition_ref(do_name) or "setVal").strip() or "setVal"
        except Exception:
            ref = "setVal"

        base = ref
        sub = ""
        if "." in ref:
            base, sub = ref.split(".", 1)
            base = (base or "").strip()
            sub = (sub or "").strip()

        p = self._do_type_file_path(do_type_id)
        if p is None:
            return []

        try:
            root = ET.parse(p).getroot()
        except Exception:
            return []

        ns = ""
        if isinstance(root.tag, str) and root.tag.startswith("{"):
            ns = root.tag.split("}", 1)[0][1:]

        def q(tag: str) -> str:
            return f"{{{ns}}}{tag}" if ns else tag

        do_el = None
        for cand in root.findall(f".//{q('DOType')}"):
            if (cand.attrib.get("id") or "").strip() == do_type_id:
                do_el = cand
                break
        if do_el is None:
            return []

        da = next((d for d in do_el.findall(q("DA")) if (d.attrib.get("name") or "").strip() == base), None)
        if da is None:
            return []

        btype = (da.attrib.get("bType") or "").strip().lower()
        if not sub:
            if btype == "enum":
                enum_id = (da.attrib.get("type") or "").strip()
                return self._enum_values_for_enum_type(enum_id)
            return []

        # Struct leaf (e.g. setMag.f / setMag.i)
        if btype != "struct":
            return []
        da_type_id = (da.attrib.get("type") or "").strip()
        if not da_type_id:
            return []

        p_da = self._da_type_file_path(da_type_id)
        if p_da is None:
            return []

        try:
            da_root = ET.parse(p_da).getroot()
        except Exception:
            return []

        ns_da = ""
        if isinstance(da_root.tag, str) and da_root.tag.startswith("{"):
            ns_da = da_root.tag.split("}", 1)[0][1:]

        def qda(tag: str) -> str:
            return f"{{{ns_da}}}{tag}" if ns_da else tag

        da_type_el = None
        for cand in da_root.findall(f".//{qda('DAType')}"):
            if (cand.attrib.get("id") or "").strip() == da_type_id:
                da_type_el = cand
                break
        if da_type_el is None:
            return []

        bda = next(
            (b for b in da_type_el.findall(qda("BDA")) if (b.attrib.get("name") or "").strip() == sub),
            None,
        )
        if bda is None:
            return []
        b_btype = (bda.attrib.get("bType") or "").strip().lower()
        if b_btype != "enum":
            return []
        enum_id = (bda.attrib.get("type") or "").strip()
        return self._enum_values_for_enum_type(enum_id)

    def _setting_do_candidates(self, *, current_do_name: str) -> list[str]:
        """Return DO names that are 'setting' (has fc=SP/SE), excluding current DO.

        This intentionally mirrors Application setting auto-generation rules:
        - Excludes SetMod, Beh, and InRef* DOs.
        - Treats a DOType as a setting if it (or any nested SDO DOType) contains
          at least one DA with fc in {SP, SE}.
        """

        def is_excluded_do_name(nm: str) -> bool:
            dn = (nm or "").strip().lower()
            if not dn:
                return True
            if dn == "setmod":
                return True
            if dn == "beh":
                return True
            if dn.startswith("inref"):
                return True
            return False

        def do_type_has_spse(do_type_id: str) -> bool:
            do_type_id = (do_type_id or "").strip()
            if not do_type_id:
                return False

            cache = getattr(self, "_do_type_has_spse_cache", None)
            if cache is None:
                cache = {}
                setattr(self, "_do_type_has_spse_cache", cache)
            if do_type_id in cache:
                return bool(cache[do_type_id])

            visited: set[str] = set()

            def scan(type_id: str) -> bool:
                type_id = (type_id or "").strip()
                if not type_id or type_id in visited:
                    return False
                visited.add(type_id)

                p = self._do_type_file_path(type_id)
                if p is None:
                    return False
                try:
                    root = ET.parse(p).getroot()
                except Exception:
                    return False

                ns = ""
                if isinstance(root.tag, str) and root.tag.startswith("{"):
                    ns = root.tag.split("}", 1)[0][1:]

                def q(tag: str) -> str:
                    return f"{{{ns}}}{tag}" if ns else tag

                do_el = None
                for cand in root.findall(f".//{q('DOType')}"):
                    if (cand.attrib.get("id") or "").strip() == type_id:
                        do_el = cand
                        break
                if do_el is None:
                    return False

                for da in do_el.findall(q("DA")):
                    fc = (da.attrib.get("fc") or "").strip().upper()
                    if fc in {"SP", "SE"}:
                        return True

                for sdo in do_el.findall(q("SDO")):
                    sub_type = (sdo.attrib.get("type") or "").strip()
                    if scan(sub_type):
                        return True

                return False

            res = scan(do_type_id)
            cache[do_type_id] = bool(res)
            return bool(res)

        current_do_name = (current_do_name or "").strip()
        out: list[str] = []
        for do in (self.table.rows or []):
            nm = (do.name or "").strip()
            if not nm or nm == current_do_name:
                continue
            if is_excluded_do_name(nm):
                continue

            do_type_id = (do.do_type or "").strip()
            if not do_type_id:
                continue

            if do_type_has_spse(do_type_id):
                out.append(nm)

        out.sort(key=lambda s: s.lower())
        return out

    def _setting_do_value_ref(self, do_name: str) -> str:
        """Return the value reference under a setting DO: setVal | setMag.f | setMag.i.

        Preference order:
        - setVal (if DA exists)
        - setMag.f (if DA setMag exists and its DAType has BDA f)
        - setMag.i (if DA setMag exists and its DAType has BDA i)
        - setMag.f (fallback if setMag exists but DAType details unknown)
        - setVal (final fallback)
        """
        do_name = (do_name or "").strip()
        if not do_name:
            return "setVal"

        idx = self._do_index_by_name(do_name)
        if idx is None:
            return "setVal"
        do_type_id = (self.table.rows[idx].do_type or "").strip()
        if not do_type_id:
            return "setVal"

        cache = getattr(self, "_setting_value_ref_cache", None)
        if cache is None:
            cache = {}
            setattr(self, "_setting_value_ref_cache", cache)
        key = (do_type_id,)
        if key in cache:
            return str(cache[key] or "setVal")

        p = self._do_type_file_path(do_type_id)
        if p is None:
            cache[key] = "setVal"
            return "setVal"

        try:
            root = ET.parse(p).getroot()
        except Exception:
            cache[key] = "setVal"
            return "setVal"

        ns = ""
        if isinstance(root.tag, str) and root.tag.startswith("{"):
            ns = root.tag.split("}", 1)[0][1:]

        def q(tag: str) -> str:
            return f"{{{ns}}}{tag}" if ns else tag

        do_el = None
        for cand in root.findall(f".//{q('DOType')}"):
            if (cand.attrib.get("id") or "").strip() == do_type_id:
                do_el = cand
                break
        if do_el is None:
            cache[key] = "setVal"
            return "setVal"

        # Find DA names directly under DOType.
        das = list(do_el.findall(q("DA")))
        if any((da.attrib.get("name") or "").strip() == "setVal" for da in das):
            cache[key] = "setVal"
            return "setVal"

        setmag_da = next((da for da in das if (da.attrib.get("name") or "").strip() == "setMag"), None)
        if setmag_da is None:
            cache[key] = "setVal"
            return "setVal"

        da_type_id = (setmag_da.attrib.get("type") or "").strip()
        if not da_type_id:
            cache[key] = "setMag.f"
            return "setMag.f"

        da_type_el = None
        for cand in root.findall(f".//{q('DAType')}"):
            if (cand.attrib.get("id") or "").strip() == da_type_id:
                da_type_el = cand
                break
        if da_type_el is None:
            cache[key] = "setMag.f"
            return "setMag.f"

        bda_names = {(b.attrib.get("name") or "").strip() for b in da_type_el.findall(q("BDA"))}
        if "f" in bda_names:
            cache[key] = "setMag.f"
            return "setMag.f"
        if "i" in bda_names:
            cache[key] = "setMag.i"
            return "setMag.i"

        cache[key] = "setMag.f"
        return "setMag.f"

    def _build_ratio_rule_body(self, *, do_type_id: str, kind: str) -> str:
        """Build the rule body (plain text) for a ratio rule."""
        kind = (kind or "").strip().lower()

        if kind == "current":
            prim = "*^.InRef%ARtg##.InRef%ARtg##.ARtg.setMag.f"
            sec = "*^.InRef%ARtg##.InRef%ARtg##.ARtgSec.setMag.f"
        elif kind == "phase_voltage":
            prim = "*^.InRef%VRtg##.InRef%VRtg##.VRtgPh.setMag.f"
            sec = "*^.InRef%VRtg##.InRef%VRtg##.VRtgPhSec.setMag.f"
        elif kind == "line_voltage":
            prim = "*^.InRef%VRtg##.InRef%VRtg##.VRtg.setMag.f"
            sec = "*^.InRef%VRtg##.InRef%VRtg##.VRtgSec.setMag.f"
        elif kind == "power":
            prim = (
                "*^.InRef%VRtg##.InRef%VRtg##.VRtgToP.setMag.f * "
                "*^.InRef%ARtg##.InRef%ARtg##.ARtg.setMag.f"
            )
            sec = (
                "*^.InRef%VRtg##.InRef%VRtg##.VRtgToPSec.setMag.f * "
                "*^.InRef%ARtg##.InRef%ARtg##.ARtgSec.setMag.f"
            )
        else:
            prim = "*^.InRef%ARtg##.InRef%ARtg##.ARtg.setMag.f"
            sec = "*^.InRef%ARtg##.InRef%ARtg##.ARtgSec.setMag.f"

        cdc = (self._do_type_cdc(do_type_id) or "").strip().upper()
        targets: list[str]
        if cdc in {"WYE", "DEL", "SEQ"}:
            sdos = self._do_type_sdo_names(do_type_id)
            targets = [f".{nm}" for nm in sdos if nm]
        else:
            targets = [""]

        lines: list[str] = []

        if cdc in {"WYE", "DEL", "SEQ"}:
            for t in targets:
                prefix = f"{t}." if t else ""
                lines.append(f"{prefix}primRt := {prim};")
            lines.append("")
            for t in targets:
                prefix = f"{t}." if t else ""
                lines.append(f"{prefix}secRt := {sec};")
        else:
            for t in targets:
                prefix = f"{t}." if t else ""
                lines.append(f"{prefix}primRt := {prim};")
                lines.append(f"{prefix}secRt := {sec};")

        return "\n".join(lines).strip() + "\n"

    def _do_rules_for_index(self, idx: int) -> None:
        if idx is None or idx < 0 or idx >= len(self.table.rows):
            return

        do = self.table.rows[idx]
        do_type = (do.do_type or "").strip()
        if not do_type:
            messagebox.showerror("Missing", "This DO has no type. Set DO type before adding a ratio rule.", parent=self)
            return

        do_names = [x.name for x in (self.table.rows or []) if (x.name or "").strip()]

        parent_win = self.winfo_toplevel()
        dlg = RuleEditDialog(
            parent_win,
            title=f"Rules for DO {do.name}",
            do_names=do_names,
            initial_do_name=do.name,
            initial_rule_type="SchneiderElectric-PowerLogic-RulesRatio",
            require_do_selection=True,
            get_rule_text=self._get_rule_text_for_do,
            apply_rule_text=self._apply_rule_text_for_do,
            get_relevancy_condition_candidates=lambda cur_do: self._setting_do_candidates(current_do_name=cur_do),
            get_relevancy_condition_ref=lambda cond_do: self._setting_do_value_ref(cond_do),
            get_enum_options_for_condition_do=lambda cond_do: self._enum_options_for_condition_do(cond_do),
            on_generate=lambda do_name, kind: self._build_ratio_rule_body(
                do_type_id=self._do_type_id_for_do_name(do_name),
                kind=kind,
            ),
        )
        dlg.show()

    # (Rule tab no longer edits per-DO in-place; it edits via double-click per row.)

    def _update_dirty_from_view(self) -> None:
        if self.model is None:
            self.dirty = False
            self._update_save_button()
            return

        cur = self._signature_full(
            dos=self.table.get_rows(),
            privates=self.private_table.get_rows(),
        )
        self.dirty = (self._saved_sig_full is None) or (cur != self._saved_sig_full)
        self._update_save_button()

    def _update_save_button(self) -> None:
        # TTK themes may ignore background color; use text cue + style.
        try:
            style = ttk.Style(self)
            style.configure("Dirty.TButton", foreground="#C00000")
        except Exception:
            style = None

        if getattr(self, "btn_save", None) is None:
            return

        if self.dirty:
            self.btn_save.configure(text="Save *", style="Dirty.TButton")
        else:
            self.btn_save.configure(text="Save", style="TButton")


class RuleEditDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        title: str,
        do_names: list[str],
        initial_do_name: str | None,
        initial_rule_type: str,
        require_do_selection: bool,
        get_rule_text,
        apply_rule_text,
        get_relevancy_condition_candidates,
        get_relevancy_condition_ref,
        get_enum_options_for_condition_do=None,
        on_generate,
    ):
        super().__init__(parent)
        self.title(title)
        self.resizable(True, True)
        self.transient(parent)
        self.grab_set()

        self._result: bool | None = None
        self._on_generate = on_generate
        self._require_do_selection = require_do_selection
        self._get_rule_text = get_rule_text
        self._apply_rule_text = apply_rule_text
        self._get_relevancy_condition_candidates = get_relevancy_condition_candidates
        self._get_relevancy_condition_ref = get_relevancy_condition_ref
        self._get_enum_options_for_condition_do = get_enum_options_for_condition_do or (lambda _do: [])

        self._buffers: dict[tuple[str, str], str] = {}  # (do_name, private_type) -> text
        self._cur_key: tuple[str, str] | None = None

        ratio_type = "SchneiderElectric-PowerLogic-RulesRatio"
        relevancy_type = "SchneiderElectric-PowerLogic-RulesRelevancy"
        dependency_type = "SchneiderElectric-PowerLogic-RulesDependency"

        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)

        frm.columnconfigure(0, weight=1)
        frm.rowconfigure(3, weight=1)

        top = ttk.Frame(frm)
        top.grid(row=0, column=0, sticky="we")
        top.columnconfigure(7, weight=1)

        ttk.Label(top, text="DO").grid(row=0, column=0, sticky="w")
        self.var_do = tk.StringVar(value=((initial_do_name or "").strip()))
        cb_do = ttk.Combobox(top, textvariable=self.var_do, values=list(do_names or []), state="readonly", width=22)
        cb_do.grid(row=0, column=1, sticky="w", padx=(8, 14))

        ttk.Label(top, text="Rule type").grid(row=0, column=2, sticky="w")
        default_type = (initial_rule_type or "").strip() or ratio_type
        self._rule_type_label_to_value = {
            "Ratio Rule": ratio_type,
            "Relevancy Rule": relevancy_type,
            "Dependency Rule": dependency_type,
        }
        self._rule_type_value_to_label = {v: k for k, v in self._rule_type_label_to_value.items()}

        initial_label = self._rule_type_value_to_label.get(default_type, "Ratio Rule")
        self.var_rule_type = tk.StringVar(value=initial_label)

        rule_type_values = list(self._rule_type_label_to_value.keys())
        # If we encounter an unexpected Private type, keep it selectable to avoid data loss.
        if default_type and default_type not in self._rule_type_value_to_label:
            rule_type_values = [default_type] + rule_type_values
            self._rule_type_label_to_value[default_type] = default_type

        cb_rule_type = ttk.Combobox(
            top,
            textvariable=self.var_rule_type,
            values=rule_type_values,
            state="readonly",
            width=22,
        )
        cb_rule_type.grid(row=0, column=3, sticky="w", padx=(8, 14))

        self._lbl_kind = ttk.Label(top, text="Ratio kind")
        self._lbl_kind.grid(row=0, column=4, sticky="w")
        self.var_kind = tk.StringVar(value="current")
        self._kind_values = ["current", "phase_voltage", "line_voltage", "power"]
        self._cb_kind = ttk.Combobox(top, textvariable=self.var_kind, values=self._kind_values, state="readonly", width=18)
        self._cb_kind.grid(row=0, column=5, sticky="w", padx=(8, 14))

        self._btn_generate = ttk.Button(top, text="Generate", command=self._generate)
        self._btn_generate.grid(row=0, column=6, sticky="w")

        # Relevancy/Dependency generator controls (shown only for their rule types)
        top2 = ttk.Frame(frm)
        top2.grid(row=2, column=0, sticky="we")
        top2.columnconfigure(0, weight=1)

        # Row 0: condition count + reverse
        top2r0 = ttk.Frame(top2)
        top2r0.grid(row=0, column=0, sticky="we")
        top2r0.columnconfigure(7, weight=1)
        self._rel_top_row = top2r0

        ttk.Label(top2r0, text="Conditions").grid(row=0, column=0, sticky="w")
        self.var_rel_n = tk.IntVar(value=1)
        self._spn_rel_n = ttk.Spinbox(
            top2r0,
            from_=1,
            to=8,
            textvariable=self.var_rel_n,
            width=5,
        )
        self._spn_rel_n.grid(row=0, column=1, sticky="w", padx=(8, 14))

        self.var_rel_reverse = tk.BooleanVar(value=False)
        self._chk_rel_rev = ttk.Checkbutton(top2r0, text="Reverse", variable=self.var_rel_reverse)
        self._chk_rel_rev.grid(row=0, column=2, sticky="w")

        # Row 1+: N conditions with DO / operator / value, plus joiner for rows except last.
        self._rel_rows_frame = ttk.Frame(top2)
        self._rel_rows_frame.grid(row=1, column=0, sticky="we", pady=(6, 0))
        self._rel_rows_frame.columnconfigure(6, weight=1)

        ttk.Label(self._rel_rows_frame, text="Condition DO").grid(row=0, column=0, sticky="w")
        ttk.Label(self._rel_rows_frame, text="Op").grid(row=0, column=1, sticky="w", padx=(8, 14))
        ttk.Label(self._rel_rows_frame, text="Value").grid(row=0, column=2, sticky="w")

        self._rel_op_values = ["=", ">", ">=", "<", "<="]
        self._rel_join_values = ["AND", "OR", "OR (high)"]
        self._rel_rows: list[dict[str, object]] = []
        self._ensure_relevancy_rows(1)

        # Dependency generator controls (shown only when Dependency Rule selected)
        self._dep_frame = ttk.Frame(top2)
        # Put dependency generator in the same row as relevancy's top row so
        # the spacing below the Tip label stays consistent across rule types.
        self._dep_frame.grid(row=0, column=0, sticky="we", pady=(0, 0))
        self._dep_frame.columnconfigure(10, weight=1)

        # Row 0: condition count (Dependency)
        self._dep_top = ttk.Frame(self._dep_frame)
        self._dep_top.grid(row=0, column=0, sticky="we")
        self._dep_top.columnconfigure(8, weight=1)

        ttk.Label(self._dep_top, text="Conditions").grid(row=0, column=0, sticky="w")
        self.var_dep_n = tk.IntVar(value=1)
        self._spn_dep_n = ttk.Spinbox(
            self._dep_top,
            from_=0,
            to=8,
            textvariable=self.var_dep_n,
            width=5,
        )
        self._spn_dep_n.grid(row=0, column=1, sticky="w", padx=(8, 14))

        # Row 1+: N conditions: DO / OP / Fixed? / Fixed value / DO value / JOIN
        self._dep_rows_frame = ttk.Frame(self._dep_frame)
        self._dep_rows_frame.grid(row=1, column=0, sticky="we", pady=(6, 0))
        self._dep_rows_frame.columnconfigure(10, weight=1)

        ttk.Label(self._dep_rows_frame, text="Condition DO").grid(row=0, column=0, sticky="w")
        ttk.Label(self._dep_rows_frame, text="Op").grid(row=0, column=1, sticky="w", padx=(8, 14))
        ttk.Label(self._dep_rows_frame, text="Fixed").grid(row=0, column=2, sticky="w")
        ttk.Label(self._dep_rows_frame, text="Fix value").grid(row=0, column=3, sticky="w", padx=(8, 0))
        ttk.Label(self._dep_rows_frame, text="DO").grid(row=0, column=4, sticky="w", padx=(8, 0))

        self._dep_rows: list[dict[str, object]] = []
        self._ensure_dependency_rows(1)

        # Assignment control (single assignment like SE templates)
        self._dep_assign = ttk.Frame(self._dep_frame)
        self._dep_assign.grid(row=2, column=0, sticky="we", pady=(10, 0))
        self._dep_assign.columnconfigure(10, weight=1)
        ttk.Label(self._dep_assign, text="Set current DO to").grid(row=0, column=0, sticky="w")

        # Some vendor templates use assignment-only dependency rules (no IF),
        # which correspond to Conditions=0.
        self._dep_conditions_visible = True

        self.var_dep_assign_fixed = tk.BooleanVar(value=False)
        self._chk_dep_assign_fixed = ttk.Checkbutton(self._dep_assign, text="Fixed", variable=self.var_dep_assign_fixed)
        self._chk_dep_assign_fixed.grid(row=0, column=1, sticky="w", padx=(8, 0))

        self.var_dep_assign_fixed_val = tk.StringVar(value="")
        self._cb_dep_assign_fixed_val = ttk.Combobox(
            self._dep_assign,
            textvariable=self.var_dep_assign_fixed_val,
            values=[],
            state="normal",
            width=22,
        )
        self._cb_dep_assign_fixed_val.grid(row=0, column=2, sticky="w", padx=(8, 0))

        self.var_dep_assign_do = tk.StringVar(value="")
        self._cb_dep_assign_do = ttk.Combobox(
            self._dep_assign,
            textvariable=self.var_dep_assign_do,
            values=[],
            state="normal",
            width=22,
        )
        self._cb_dep_assign_do.grid(row=0, column=3, sticky="w", padx=(8, 0))

        def _dep_assign_toggle(*_a) -> None:
            fixed = bool(self.var_dep_assign_fixed.get())
            try:
                self._cb_dep_assign_fixed_val.configure(state="normal" if fixed else "disabled")
            except Exception:
                pass
            try:
                self._cb_dep_assign_do.configure(state="disabled" if fixed else "normal")
            except Exception:
                pass

        self.var_dep_assign_fixed.trace_add("write", _dep_assign_toggle)
        _dep_assign_toggle()

        hint = ttk.Label(
            frm,
            text=(
                "Tip: Click Generate to fill a default ratio rule from DOType. "
                "You can edit the text below before saving."
            ),
            justify="left",
        )
        hint.grid(row=1, column=0, sticky="w", pady=(8, 6))

        box = ttk.Frame(frm)
        box.grid(row=3, column=0, sticky="nsew")
        box.rowconfigure(0, weight=1)
        box.columnconfigure(0, weight=1)

        self.txt = tk.Text(box, wrap="none", height=18, width=92)
        self.txt.grid(row=0, column=0, sticky="nsew")
        ysb = ttk.Scrollbar(box, orient="vertical", command=self.txt.yview)
        ysb.grid(row=0, column=1, sticky="ns")
        xsb = ttk.Scrollbar(box, orient="horizontal", command=self.txt.xview)
        xsb.grid(row=1, column=0, sticky="ew")
        self.txt.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)

        # Text is loaded by _switch_context() based on DO + rule type.

        btns = ttk.Frame(frm)
        btns.grid(row=4, column=0, sticky="e", pady=(10, 0))
        # Button order (left->right): OK, Apply, Cancel, Clean
        # With side="right", pack in reverse so Clean ends up rightmost.
        ttk.Button(btns, text="Clean", command=self._clean).pack(side="right")
        ttk.Button(btns, text="Cancel", command=self._cancel).pack(side="right", padx=(0, 8))
        self._btn_apply = ttk.Button(btns, text="Apply", command=self._apply)
        self._btn_apply.pack(side="right", padx=(0, 8))
        ttk.Button(btns, text="OK", command=self._ok).pack(side="right", padx=(0, 8))

        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Return>", lambda _e: self._ok())
        try:
            self.txt.focus_set()
        except Exception:
            pass

        # Best-effort: bring the dialog to front.
        try:
            self.update_idletasks()
            self.lift()
            self.focus_force()
        except Exception:
            pass

        # React to changes
        self.var_do.trace_add("write", lambda *_a: self._switch_context())
        self.var_rule_type.trace_add("write", lambda *_a: self._switch_context())
        self.var_rel_n.trace_add("write", lambda *_a: self._on_rel_n_changed())
        self.var_dep_n.trace_add("write", lambda *_a: self._on_dep_n_changed())

        # Initial load
        self._switch_context(first=True)

    def _selected_private_type(self) -> str:
        label = (self.var_rule_type.get() or "").strip()
        return (self._rule_type_label_to_value.get(label) or label or "").strip()

    def _is_ratio_selected(self) -> bool:
        return self._selected_private_type() == "SchneiderElectric-PowerLogic-RulesRatio"

    def _is_relevancy_selected(self) -> bool:
        return self._selected_private_type() == "SchneiderElectric-PowerLogic-RulesRelevancy"

    def _is_dependency_selected(self) -> bool:
        return self._selected_private_type() == "SchneiderElectric-PowerLogic-RulesDependency"

    def _stash_current_buffer(self) -> None:
        if self._cur_key is None:
            return
        try:
            txt = self.txt.get("1.0", "end-1c").rstrip("\n")
        except Exception:
            return
        self._buffers[self._cur_key] = txt

    def _load_buffer(self, do_name: str, private_type: str) -> str:
        key = (do_name, private_type)
        if key in self._buffers:
            return self._buffers[key]
        try:
            txt = (self._get_rule_text(do_name, private_type) or "")
        except Exception:
            txt = ""
        txt = (txt or "").strip("\n")
        self._buffers[key] = txt
        return txt

    def _switch_context(self, *, first: bool = False) -> None:
        do_name = (self.var_do.get() or "").strip()
        private_type = self._selected_private_type()

        if not first:
            self._stash_current_buffer()

        # Toggle ratio widgets
        if self._is_ratio_selected():
            try:
                self._lbl_kind.grid()
                self._cb_kind.grid()
                self._btn_generate.grid()
            except Exception:
                pass
        else:
            try:
                self._lbl_kind.grid_remove()
                self._cb_kind.grid_remove()
            except Exception:
                pass

        # Toggle relevancy widgets and Generate button
        if self._is_relevancy_selected():
            # Populate candidates before any auto-fill sets var_rel_do.
            self._update_relevancy_condition_options(keep_selection=False)
            try:
                self._rel_top_row.grid()
                self._spn_rel_n.grid()
                self._chk_rel_rev.grid()
                self._rel_rows_frame.grid()
                self._dep_frame.grid_remove()
                self._btn_generate.grid()
            except Exception:
                pass
        else:
            try:
                self._rel_top_row.grid_remove()
                self._spn_rel_n.grid_remove()
                self._chk_rel_rev.grid_remove()
                self._rel_rows_frame.grid_remove()
            except Exception:
                pass

        # Toggle dependency widgets and Generate button
        if self._is_dependency_selected():
            self._update_dependency_options(keep_selection=False)
            try:
                self._dep_frame.grid()
                self._btn_generate.grid()
            except Exception:
                pass
        else:
            try:
                self._dep_frame.grid_remove()
            except Exception:
                pass

        if not do_name or not private_type:
            self._cur_key = None
            try:
                self.txt.delete("1.0", "end")
            except Exception:
                pass
            return

        self._cur_key = (do_name, private_type)
        new_txt = self._load_buffer(do_name, private_type)
        try:
            self.txt.delete("1.0", "end")
            if new_txt:
                self.txt.insert("1.0", new_txt + "\n")
        except Exception:
            pass

        # Auto-fill relevancy generator fields from existing rule text.
        if self._is_relevancy_selected():
            self._autofill_relevancy_controls_from_text(new_txt)

        # Auto-fill dependency generator fields from existing rule text.
        if self._is_dependency_selected():
            self._autofill_dependency_controls_from_text(new_txt)

    def _update_relevancy_condition_options(self, *, keep_selection: bool) -> None:
        if not self._is_relevancy_selected():
            return

        cur_do = (self.var_do.get() or "").strip()
        try:
            candidates = list(self._get_relevancy_condition_candidates(cur_do) or [])
        except Exception:
            candidates = []

        # Push candidate list into all condition DO comboboxes.
        # Note: a condition's left-hand side may be a non-setting reference (e.g. InRef%...)
        # which will not be present in candidates. In that case, we must not clear/modify it.
        for row in list(self._rel_rows or []):
            var_do = row.get("var_do")
            cb = row.get("cb_do")

            sel = ""
            try:
                sel = ((var_do.get() if var_do is not None else "") or "").strip()
            except Exception:
                sel = ""

            values = list(candidates)
            # Preserve any typed/non-candidate value by keeping it selectable.
            if sel and sel not in values:
                values = [sel] + values

            try:
                if cb is not None:
                    cb["values"] = values
            except Exception:
                pass

            # Never clear: typed values (e.g. InRef%...) are valid.

        # Refresh enum dropdown options for the Value fields.
        for row in list(getattr(self, "_rel_rows", []) or []):
            try:
                self._update_relevancy_value_enum_options_for_row(row)
            except Exception:
                pass

    def _update_relevancy_value_enum_options_for_row(self, row: dict[str, object]) -> None:
        cb_val = row.get("cb_val")
        var_val = row.get("var_val")
        var_do = row.get("var_do")
        if cb_val is None or var_val is None or var_do is None:
            return

        try:
            do_name = ((var_do.get() or "") if hasattr(var_do, "get") else "").strip()  # type: ignore[union-attr]
        except Exception:
            do_name = ""

        try:
            opts = list(self._get_enum_options_for_condition_do(do_name) or [])
        except Exception:
            opts = []

        try:
            cur = (var_val.get() or "") if hasattr(var_val, "get") else ""  # type: ignore[union-attr]
        except Exception:
            cur = ""

        values = list(opts)
        if cur and cur not in values:
            values = [cur] + values
        try:
            cb_val["values"] = values
        except Exception:
            pass

    def _update_dependency_options(self, *, keep_selection: bool) -> None:
        if not self._is_dependency_selected():
            return

        cur_do = (self.var_do.get() or "").strip()
        try:
            candidates = list(self._get_relevancy_condition_candidates(cur_do) or [])
        except Exception:
            candidates = []

        def apply_values(cb: ttk.Combobox | None, var: tk.StringVar | None) -> None:
            if cb is None or var is None:
                return
            try:
                sel = ((var.get() or "").strip())
            except Exception:
                sel = ""
            values = list(candidates)
            if sel and sel not in values:
                values = [sel] + values
            try:
                cb["values"] = values
            except Exception:
                pass

        for row in list(getattr(self, "_dep_rows", []) or []):
            apply_values(row.get("cb_cond_do"), row.get("var_cond_do"))
            apply_values(row.get("cb_rhs_do"), row.get("var_rhs_do"))

        # Refresh enum dropdown options for each row's Fix value field.
        for row in list(getattr(self, "_dep_rows", []) or []):
            try:
                self._update_dependency_fixed_enum_options_for_row(row)
            except Exception:
                pass

        apply_values(self._cb_dep_assign_do, self.var_dep_assign_do)

        # Refresh enum dropdown options for the assignment fixed value.
        try:
            self._update_dependency_assign_fixed_enum_options()
        except Exception:
            pass

    def _update_dependency_assign_fixed_enum_options(self) -> None:
        cb = getattr(self, "_cb_dep_assign_fixed_val", None)
        if cb is None:
            return
        try:
            do_name = (self.var_do.get() or "").strip()
        except Exception:
            do_name = ""
        try:
            opts = list(self._get_enum_options_for_condition_do(do_name) or [])
        except Exception:
            opts = []
        try:
            cur = (self.var_dep_assign_fixed_val.get() or "")
        except Exception:
            cur = ""
        values = list(opts)
        if cur and cur not in values:
            values = [cur] + values
        try:
            cb["values"] = values
        except Exception:
            pass

    def _update_dependency_fixed_enum_options_for_row(self, row: dict[str, object]) -> None:
        cb_fixed_val = row.get("cb_fixed_val")
        var_fixed_val = row.get("var_fixed_val")
        var_cond_do = row.get("var_cond_do")
        if cb_fixed_val is None or var_fixed_val is None or var_cond_do is None:
            return

        try:
            do_name = ((var_cond_do.get() or "") if hasattr(var_cond_do, "get") else "").strip()  # type: ignore[union-attr]
        except Exception:
            do_name = ""

        try:
            opts = list(self._get_enum_options_for_condition_do(do_name) or [])
        except Exception:
            opts = []

        try:
            cur = (var_fixed_val.get() or "") if hasattr(var_fixed_val, "get") else ""  # type: ignore[union-attr]
        except Exception:
            cur = ""

        values = list(opts)
        if cur and cur not in values:
            values = [cur] + values
        try:
            cb_fixed_val["values"] = values
        except Exception:
            pass

    def _set_dependency_condition_visibility(self, show: bool) -> None:
        show = bool(show)
        self._dep_conditions_visible = show
        try:
            if show:
                self._dep_rows_frame.grid()
            else:
                self._dep_rows_frame.grid_remove()
        except Exception:
            pass

    def _on_dep_n_changed(self) -> None:
        if not self._is_dependency_selected():
            return

        n = 1
        try:
            n = int(self.var_dep_n.get())
        except Exception:
            n = 1
        if n < 0:
            n = 0
        if n > 8:
            n = 8
        try:
            self.var_dep_n.set(n)
        except Exception:
            pass

        self._set_dependency_condition_visibility(n > 0)

        self._ensure_dependency_rows(n)
        self._update_dependency_options(keep_selection=True)

    def _ensure_dependency_rows(self, n: int) -> None:
        try:
            n = int(n)
        except Exception:
            n = 1
        if n < 0:
            n = 0
        if n > 8:
            n = 8

        prev: list[tuple[str, str, bool, str, str, str]] = []
        for row in list(getattr(self, "_dep_rows", []) or []):
            try:
                lhs = ((row.get("var_cond_do").get()) or "").strip()
            except Exception:
                lhs = ""
            try:
                op = ((row.get("var_op").get()) or "=").strip() or "="
            except Exception:
                op = "="
            try:
                fixed = bool(row.get("var_fixed").get())
            except Exception:
                fixed = False
            try:
                fixed_val = (row.get("var_fixed_val").get()) or ""
            except Exception:
                fixed_val = ""
            try:
                rhs_do = ((row.get("var_rhs_do").get()) or "").strip()
            except Exception:
                rhs_do = ""
            try:
                join_v = ((row.get("var_join").get()) or "AND").strip() or "AND"
            except Exception:
                join_v = "AND"
            prev.append((lhs, op, fixed, fixed_val, rhs_do, join_v))

        # Destroy old row widgets (keep header row=0)
        try:
            for ch in list(self._dep_rows_frame.winfo_children()):
                gi = {}
                try:
                    gi = ch.grid_info() or {}
                except Exception:
                    gi = {}
                r = int(gi.get("row") or 0)
                if r >= 1:
                    ch.destroy()
        except Exception:
            pass

        self._dep_rows = []

        if n == 0:
            return

        for i in range(n):
            row_idx = i + 1

            var_cond_do = tk.StringVar(value="")
            cb_cond_do = ttk.Combobox(
                self._dep_rows_frame,
                textvariable=var_cond_do,
                values=[],
                state="normal",
                width=22,
            )
            cb_cond_do.grid(row=row_idx, column=0, sticky="w")

            var_op = tk.StringVar(value="=")
            cb_op = ttk.Combobox(
                self._dep_rows_frame,
                textvariable=var_op,
                values=list(self._rel_op_values),
                state="readonly",
                width=5,
            )
            cb_op.grid(row=row_idx, column=1, sticky="w", padx=(8, 14))

            var_fixed = tk.BooleanVar(value=False)
            chk_fixed = ttk.Checkbutton(self._dep_rows_frame, variable=var_fixed)
            chk_fixed.grid(row=row_idx, column=2, sticky="w")

            var_fixed_val = tk.StringVar(value="")
            cb_fixed_val = ttk.Combobox(
                self._dep_rows_frame,
                textvariable=var_fixed_val,
                values=[],
                state="normal",
                width=22,
            )
            cb_fixed_val.grid(row=row_idx, column=3, sticky="w", padx=(8, 0))

            var_rhs_do = tk.StringVar(value="")
            cb_rhs_do = ttk.Combobox(
                self._dep_rows_frame,
                textvariable=var_rhs_do,
                values=[],
                state="normal",
                width=22,
            )
            cb_rhs_do.grid(row=row_idx, column=4, sticky="w", padx=(8, 0))

            var_join = tk.StringVar(value="AND")
            cb_join = ttk.Combobox(
                self._dep_rows_frame,
                textvariable=var_join,
                values=list(self._rel_join_values),
                state="readonly",
                width=9,
            )
            if i < n - 1:
                cb_join.grid(row=row_idx, column=5, sticky="w", padx=(8, 0))
            else:
                try:
                    cb_join.grid_remove()
                except Exception:
                    pass

            def _toggle_row(*_a, vfixed=var_fixed, efix=cb_fixed_val, crhs=cb_rhs_do) -> None:
                fixed = bool(vfixed.get())
                try:
                    efix.configure(state="normal" if fixed else "disabled")
                except Exception:
                    pass
                try:
                    crhs.configure(state="disabled" if fixed else "normal")
                except Exception:
                    pass

            var_fixed.trace_add("write", _toggle_row)
            _toggle_row()

            row_obj: dict[str, object] = {
                "var_cond_do": var_cond_do,
                "cb_cond_do": cb_cond_do,
                "var_op": var_op,
                "cb_op": cb_op,
                "var_fixed": var_fixed,
                "chk_fixed": chk_fixed,
                "var_fixed_val": var_fixed_val,
                "cb_fixed_val": cb_fixed_val,
                "var_rhs_do": var_rhs_do,
                "cb_rhs_do": cb_rhs_do,
                "var_join": var_join,
                "cb_join": cb_join,
            }

            # When condition DO changes, refresh enum options for Fix value.
            try:
                def _on_dep_cond_do_change(*_a, r=row_obj) -> None:
                    # Clear previous fixed value to avoid carrying an enum string
                    # from one DO to another.
                    try:
                        v = r.get("var_fixed_val")
                        if v is not None and hasattr(v, "set"):
                            v.set("")  # type: ignore[union-attr]
                    except Exception:
                        pass
                    try:
                        self._update_dependency_fixed_enum_options_for_row(r)
                    except Exception:
                        pass

                var_cond_do.trace_add("write", _on_dep_cond_do_change)
            except Exception:
                pass

            self._dep_rows.append(row_obj)

        # Restore previous values.
        for i in range(min(len(prev), len(self._dep_rows))):
            lhs, op, fixed, fixed_val, rhs_do, join_v = prev[i]
            try:
                self._dep_rows[i]["var_cond_do"].set(lhs)
            except Exception:
                pass
            try:
                self._dep_rows[i]["var_op"].set(op if op in self._rel_op_values else "=")
            except Exception:
                pass
            try:
                self._dep_rows[i]["var_fixed"].set(bool(fixed))
            except Exception:
                pass
            try:
                self._dep_rows[i]["var_fixed_val"].set(fixed_val)
            except Exception:
                pass
            try:
                self._dep_rows[i]["var_rhs_do"].set(rhs_do)
            except Exception:
                pass
            if i < len(self._dep_rows) - 1:
                raw_u = (join_v or "AND").strip().upper() or "AND"
                if raw_u == "AND":
                    j = "AND"
                elif raw_u == "OR":
                    j = "OR"
                elif raw_u.startswith("OR") and "HIGH" in raw_u:
                    j = "OR (high)"
                else:
                    j = "AND"
                try:
                    self._dep_rows[i]["var_join"].set(j)
                except Exception:
                    pass

    def _autofill_dependency_controls_from_text(self, text: str) -> None:
        conds, joins, assign_fixed, assign_fixed_val, assign_do = self._parse_dependency_rule_text(text or "")

        # Vendor templates may contain an assignment-only dependency rule (no IF).
        # In that case, set Conditions=0 and only keep the assignment row.
        raw = (text or "")
        has_if = bool(re.search(r"(?im)^\s*IF\b", raw))
        has_assign = bool(
            re.search(
                r"(?im)^\s*\.(?:setVal|setMag\.f|setMag\.i)\s*:=",
                raw,
            )
        )

        if has_if:
            n = max(1, min(8, len(conds) or 1))
        elif has_assign and raw.strip():
            n = 0
        else:
            n = max(1, min(8, len(conds) or 1))

        self._set_dependency_condition_visibility(n > 0)
        try:
            self.var_dep_n.set(n)
        except Exception:
            pass
        self._ensure_dependency_rows(n)
        self._update_dependency_options(keep_selection=True)

        for i in range(n):
            lhs = ""
            op = "="
            fixed = False
            fixed_val = ""
            rhs_do = ""
            if i < len(conds):
                lhs, op, fixed, fixed_val, rhs_do = conds[i]
            try:
                self._dep_rows[i]["var_cond_do"].set(lhs)
            except Exception:
                pass
            try:
                self._dep_rows[i]["var_op"].set(op if op in self._rel_op_values else "=")
            except Exception:
                pass
            try:
                self._dep_rows[i]["var_fixed"].set(bool(fixed))
            except Exception:
                pass
            try:
                self._dep_rows[i]["var_fixed_val"].set(fixed_val)
            except Exception:
                pass
            try:
                self._dep_rows[i]["var_rhs_do"].set(rhs_do)
            except Exception:
                pass
            if i < n - 1:
                raw = "AND"
                if i < len(joins):
                    raw = (joins[i] or "AND").strip() or "AND"
                raw_u = raw.upper()
                if raw_u == "AND":
                    j = "AND"
                elif raw_u == "OR":
                    j = "OR"
                elif raw_u.startswith("OR") and "HIGH" in raw_u:
                    j = "OR (high)"
                else:
                    j = "AND"
                try:
                    self._dep_rows[i]["var_join"].set(j)
                except Exception:
                    pass

        try:
            self.var_dep_assign_fixed.set(bool(assign_fixed))
        except Exception:
            pass
        try:
            self.var_dep_assign_fixed_val.set(assign_fixed_val)
        except Exception:
            pass
        try:
            self.var_dep_assign_do.set(assign_do)
        except Exception:
            pass

    def _parse_dependency_rule_text(
        self, text: str
    ) -> tuple[list[tuple[str, str, bool, str, str]], list[str], bool, str, str]:
        """Parse Dependency rule.

        Returns:
        - conditions: list of (lhs_do, op, rhs_is_fixed, rhs_fixed_val, rhs_do)
        - joins: list of AND/OR/OR (high) between conditions
        - assign_fixed: bool
        - assign_fixed_val: string (unquoted)
        - assign_do: string (DO name or raw ref like .InRef%...)
        """
        s = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not s:
            return ([], [], False, "", "")

        def extract_if_expr(rule_text: str) -> str:
            m_if = re.search(r"(?im)^\s*IF\b", rule_text)
            if not m_if:
                return ""
            start = m_if.end()
            rest = rule_text[start:]
            idx = rest.find("(")
            if idx >= 0:
                i = start + idx + 1
                depth = 1
                out: list[str] = []
                while i < len(rule_text):
                    ch = rule_text[i]
                    if ch == "(":
                        depth += 1
                    elif ch == ")":
                        depth -= 1
                        if depth == 0:
                            break
                    out.append(ch)
                    i += 1
                expr = "".join(out).strip()
                if expr:
                    return expr
            m_then = re.search(r"(?im)^\s*THEN\b", rule_text[m_if.start() :])
            if m_then:
                block = rule_text[m_if.end() : m_if.start() + m_then.start()]
            else:
                block = rule_text[m_if.end() :]
            block = block.strip()
            if block.startswith("(") and block.endswith(")"):
                block = block[1:-1].strip()
            return block

        def strip_outer_parens(x: str) -> str:
            x = (x or "").strip()
            if not x:
                return ""
            while x.startswith("(") and x.endswith(")"):
                depth = 0
                ok = True
                for i, ch in enumerate(x):
                    if ch == "(":
                        depth += 1
                    elif ch == ")":
                        depth -= 1
                        if depth == 0 and i != len(x) - 1:
                            ok = False
                            break
                if ok:
                    x = x[1:-1].strip()
                else:
                    break
            return x

        def split_top_level(x: str) -> tuple[list[str], list[str]]:
            x = (x or "").strip()
            if not x:
                return ([], [])
            parts: list[str] = []
            ops: list[str] = []
            depth = 0
            buf: list[str] = []
            i = 0
            upper = x.upper()
            while i < len(x):
                ch = x[i]
                if ch == "(":
                    depth += 1
                    buf.append(ch)
                    i += 1
                    continue
                if ch == ")":
                    depth = max(0, depth - 1)
                    buf.append(ch)
                    i += 1
                    continue
                if depth == 0 and upper.startswith(" AND ", i):
                    parts.append("".join(buf).strip())
                    buf = []
                    ops.append("AND")
                    i += 5
                    continue
                if depth == 0 and upper.startswith(" OR ", i):
                    parts.append("".join(buf).strip())
                    buf = []
                    ops.append("OR")
                    i += 4
                    continue
                buf.append(ch)
                i += 1
            if buf:
                parts.append("".join(buf).strip())
            return (parts, ops)

        def normalize_lhs(left: str) -> str:
            left = (left or "").strip()
            m_left_do = re.match(
                r"(?is)^\^\.\s*(?P<do>[A-Za-z0-9_]+)\s*\.\s*(?P<ref>setVal|setMag\.f|setMag\.i)\s*$",
                left,
            )
            if m_left_do:
                return (m_left_do.group("do") or "").strip()
            m_left_inref = re.match(r"(?is)^\*\^\.(?P<rest>.+)$", left)
            if m_left_inref:
                return "." + (m_left_inref.group("rest") or "").strip()
            return left

        def parse_rhs(right: str) -> tuple[bool, str, str]:
            right = (right or "").strip()
            if len(right) >= 2 and right[0] == '"' and right[-1] == '"':
                return (True, right[1:-1], "")
            if re.match(r"^[+-]?(?:\d+)(?:\.\d+)?$", right or ""):
                return (True, right, "")
            # Treat as DO/ref expression
            rhs_ui = normalize_lhs(right)
            return (False, "", rhs_ui)

        def parse_comparison(seg: str) -> tuple[str, str, bool, str, str] | None:
            seg = strip_outer_parens(seg)
            if not seg:
                return None
            m = re.match(r"(?is)^(?P<left>.+?)\s*(?P<op>>=|<=|=|>|<)\s*(?P<right>.+?)\s*$", seg)
            if not m:
                return None
            lhs_ui = normalize_lhs(m.group("left") or "")
            op = (m.group("op") or "=").strip() or "="
            rhs_is_fixed, rhs_fixed_val, rhs_do = parse_rhs(m.group("right") or "")
            return (lhs_ui, op, rhs_is_fixed, rhs_fixed_val, rhs_do)

        conditions: list[tuple[str, str, bool, str, str]] = []
        joins: list[str] = []

        expr = extract_if_expr(s)
        if expr:
            expr_core = strip_outer_parens(expr)
            top_terms, top_ops = split_top_level(expr_core)
            for ti, term_raw in enumerate(top_terms):
                outer_op = top_ops[ti] if ti < len(top_ops) else ""
                raw = (term_raw or "").strip()
                was_wrapped = raw.startswith("(") and raw.endswith(")")
                term = strip_outer_parens(raw)
                if not term:
                    continue
                if was_wrapped:
                    sub_terms, sub_ops = split_top_level(term)
                    if len(sub_terms) >= 2 and sub_ops and all(x == "OR" for x in sub_ops):
                        for si, st in enumerate(sub_terms):
                            comp = parse_comparison(st)
                            if comp is None:
                                continue
                            conditions.append(comp)
                            if si < len(sub_terms) - 1:
                                joins.append("OR (high)")
                        if outer_op in {"AND", "OR"}:
                            joins.append(outer_op)
                        continue
                comp = parse_comparison(term)
                if comp is not None:
                    conditions.append(comp)
                    if outer_op in {"AND", "OR"}:
                        joins.append(outer_op)

        # Parse assignment RHS
        assign_fixed = False
        assign_fixed_val = ""
        assign_do = ""
        m_as = re.search(r"(?im)^\s*\.(?P<ref>setVal|setMag\.f|setMag\.i)\s*:=\s*(?P<rhs>.+?)\s*;\s*$", s)
        if m_as:
            rhs = (m_as.group("rhs") or "").strip()
            if len(rhs) >= 2 and rhs[0] == '"' and rhs[-1] == '"':
                assign_fixed = True
                assign_fixed_val = rhs[1:-1]
            elif re.match(r"^[+-]?(?:\d+)(?:\.\d+)?$", rhs or ""):
                assign_fixed = True
                assign_fixed_val = rhs
            else:
                assign_fixed = False
                assign_do = normalize_lhs(rhs)

        if len(joins) > max(0, len(conditions) - 1):
            joins = joins[: max(0, len(conditions) - 1)]

        return (conditions, joins, assign_fixed, assign_fixed_val, assign_do)

    def _on_rel_n_changed(self) -> None:
        if not self._is_relevancy_selected():
            return

        n = 1
        try:
            n = int(self.var_rel_n.get() or 1)
        except Exception:
            n = 1
        if n < 1:
            n = 1
        if n > 8:
            n = 8
        try:
            self.var_rel_n.set(n)
        except Exception:
            pass

        self._ensure_relevancy_rows(n)
        self._update_relevancy_condition_options(keep_selection=True)

    def _ensure_relevancy_rows(self, n: int) -> None:
        n = int(n or 1)
        if n < 1:
            n = 1
        if n > 8:
            n = 8

        # Snapshot existing values so resizing does not wipe user input.
        prev: list[tuple[str, str, str, str]] = []
        for row in list(getattr(self, "_rel_rows", []) or []):
            try:
                do_v = ((row.get("var_do").get()) or "").strip()
            except Exception:
                do_v = ""
            try:
                op_v = ((row.get("var_op").get()) or "=").strip() or "="
            except Exception:
                op_v = "="
            try:
                val_v = (row.get("var_val").get()) or ""
            except Exception:
                val_v = ""
            try:
                join_v = ((row.get("var_join").get()) or "AND").strip() or "AND"
            except Exception:
                join_v = "AND"
            prev.append((do_v, op_v, val_v, join_v))

        # Destroy old row widgets (keep header row=0)
        try:
            for ch in list(self._rel_rows_frame.winfo_children()):
                gi = {}
                try:
                    gi = ch.grid_info() or {}
                except Exception:
                    gi = {}
                r = int(gi.get("row") or 0)
                if r >= 1:
                    ch.destroy()
        except Exception:
            pass

        self._rel_rows = []

        for i in range(n):
            row_idx = i + 1

            var_do = tk.StringVar(value="")
            cb_do = ttk.Combobox(
                self._rel_rows_frame,
                textvariable=var_do,
                values=[],
                state="normal",
                width=22,
            )
            cb_do.grid(row=row_idx, column=0, sticky="w")

            var_op = tk.StringVar(value="=")
            cb_op = ttk.Combobox(
                self._rel_rows_frame,
                textvariable=var_op,
                values=list(self._rel_op_values),
                state="readonly",
                width=5,
            )
            cb_op.grid(row=row_idx, column=1, sticky="w", padx=(8, 14))

            var_val = tk.StringVar(value="")
            cb_val = ttk.Combobox(
                self._rel_rows_frame,
                textvariable=var_val,
                values=[],
                state="normal",
                width=22,
            )
            cb_val.grid(row=row_idx, column=2, sticky="w")

            var_join = tk.StringVar(value="AND")
            cb_join = ttk.Combobox(
                self._rel_rows_frame,
                textvariable=var_join,
                values=list(self._rel_join_values),
                state="readonly",
                width=6,
            )
            if i < n - 1:
                cb_join.grid(row=row_idx, column=3, sticky="w", padx=(8, 0))
            else:
                try:
                    cb_join.grid_remove()
                except Exception:
                    pass

            row_obj: dict[str, object] = {
                "var_do": var_do,
                "cb_do": cb_do,
                "var_op": var_op,
                "cb_op": cb_op,
                "var_val": var_val,
                "cb_val": cb_val,
                "var_join": var_join,
                "cb_join": cb_join,
            }

            # When condition DO changes, refresh enum options for Value.
            try:
                var_do.trace_add(
                    "write",
                    lambda *_a, r=row_obj: self._update_relevancy_value_enum_options_for_row(r),
                )
            except Exception:
                pass

            self._rel_rows.append(row_obj)

        # Restore previous values into the top rows.
        for i in range(min(len(prev), len(self._rel_rows))):
            do_v, op_v, val_v, join_v = prev[i]
            try:
                self._rel_rows[i]["var_do"].set(do_v)
            except Exception:
                pass
            try:
                self._rel_rows[i]["var_op"].set(op_v if op_v in self._rel_op_values else "=")
            except Exception:
                pass
            try:
                self._rel_rows[i]["var_val"].set(val_v)
            except Exception:
                pass
            if i < len(self._rel_rows) - 1:
                raw = (join_v or "AND").strip()
                raw_u = raw.upper()
                if raw_u == "AND":
                    j = "AND"
                elif raw_u == "OR":
                    j = "OR"
                elif raw_u.startswith("OR") and "HIGH" in raw_u:
                    j = "OR (high)"
                else:
                    j = "AND"
                try:
                    self._rel_rows[i]["var_join"].set(j)
                except Exception:
                    pass

    def _autofill_relevancy_controls_from_text(self, text: str) -> None:
        """Best-effort parse of an existing relevancy rule to fill generator widgets."""
        conds, joins, reverse = self._parse_relevancy_rule_text(text or "")

        n = 1
        if conds:
            n = max(1, min(8, len(conds)))
        try:
            self.var_rel_n.set(n)
        except Exception:
            pass
        self._ensure_relevancy_rows(n)

        try:
            self.var_rel_reverse.set(bool(reverse))
        except Exception:
            pass

        # Ensure candidates are available before selecting DOs.
        self._update_relevancy_condition_options(keep_selection=True)

        for i in range(n):
            do_name = ""
            op = "="
            val = ""
            if i < len(conds):
                do_name, op, val = conds[i]
            try:
                self._rel_rows[i]["var_do"].set(do_name)
            except Exception:
                pass
            try:
                self._rel_rows[i]["var_op"].set(op if op in self._rel_op_values else "=")
            except Exception:
                pass
            try:
                self._rel_rows[i]["var_val"].set(val)
            except Exception:
                pass
            if i < n - 1:
                raw = "AND"
                if i < len(joins):
                    raw = (joins[i] or "AND").strip() or "AND"
                raw_u = raw.upper()
                if raw_u == "AND":
                    j = "AND"
                elif raw_u == "OR":
                    j = "OR"
                elif raw_u.startswith("OR") and "HIGH" in raw_u:
                    j = "OR (high)"
                else:
                    j = "AND"
                try:
                    self._rel_rows[i]["var_join"].set(j)
                except Exception:
                    pass

    def _parse_relevancy_rule_text(self, text: str) -> tuple[list[tuple[str, str, str]], list[str], bool]:
        """Parse relevancy rule template.

        Expected (but tolerant) structure:
        IF (^.<COND_DO>.<ref> = <value>)
        THEN
            .relevancy := true|false;
        ELSE
            .relevancy := true|false;
        END_IF

        Returns: (conditions, joins, reverse)
        - conditions: list of (cond_do, operator, cond_value)
        - joins: list of AND/OR between conditions (len = len(conditions)-1)
        """
        s = (text or "").replace("\r\n", "\n").replace("\r", "\n")
        s = s.strip()
        if not s:
            return ([], [], False)

        def extract_if_expr(rule_text: str) -> str:
            """Extract the IF expression, tolerant of multi-line formatting.

            Supports:
            - IF (<expr>) on one line
            - IF (\n  <expr>\n) spread over multiple lines
            - Fallback: IF <expr> (no parentheses)
            """
            m_if = re.search(r"(?im)^\s*IF\b", rule_text)
            if not m_if:
                return ""

            start = m_if.end()
            # Prefer scanning for a balanced parenthesized expression.
            rest = rule_text[start:]
            idx = rest.find("(")
            if idx >= 0:
                i = start + idx + 1
                depth = 1
                out_chars: list[str] = []
                while i < len(rule_text):
                    ch = rule_text[i]
                    if ch == "(":
                        depth += 1
                    elif ch == ")":
                        depth -= 1
                        if depth == 0:
                            break
                    out_chars.append(ch)
                    i += 1
                expr = "".join(out_chars).strip()
                if expr:
                    return expr

            # Fallback: take from IF line until THEN line.
            m_then = re.search(r"(?im)^\s*THEN\b", rule_text[m_if.start() :])
            if m_then:
                block = rule_text[m_if.end() : m_if.start() + m_then.start()]
            else:
                block = rule_text[m_if.end() :]
            block = block.strip()
            # Strip outer parentheses if present.
            if block.startswith("(") and block.endswith(")"):
                block = block[1:-1].strip()
            return block

        # 1) Parse the IF condition
        conditions: list[tuple[str, str, str]] = []
        joins: list[str] = []

        expr = extract_if_expr(s)
        if expr:
            def strip_outer_parens(x: str) -> str:
                x = (x or "").strip()
                if not x:
                    return ""
                while x.startswith("(") and x.endswith(")"):
                    depth = 0
                    ok = True
                    for i, ch in enumerate(x):
                        if ch == "(":
                            depth += 1
                        elif ch == ")":
                            depth -= 1
                            if depth == 0 and i != len(x) - 1:
                                ok = False
                                break
                    if ok:
                        x = x[1:-1].strip()
                    else:
                        break
                return x

            def split_top_level(x: str) -> tuple[list[str], list[str]]:
                x = (x or "").strip()
                if not x:
                    return ([], [])
                parts: list[str] = []
                ops: list[str] = []
                depth = 0
                buf: list[str] = []
                i = 0
                upper = x.upper()
                while i < len(x):
                    ch = x[i]
                    if ch == "(":
                        depth += 1
                        buf.append(ch)
                        i += 1
                        continue
                    if ch == ")":
                        depth = max(0, depth - 1)
                        buf.append(ch)
                        i += 1
                        continue
                    if depth == 0 and upper.startswith(" AND ", i):
                        parts.append("".join(buf).strip())
                        buf = []
                        ops.append("AND")
                        i += 5
                        continue
                    if depth == 0 and upper.startswith(" OR ", i):
                        parts.append("".join(buf).strip())
                        buf = []
                        ops.append("OR")
                        i += 4
                        continue
                    buf.append(ch)
                    i += 1
                if buf:
                    parts.append("".join(buf).strip())
                return (parts, ops)

            def parse_comparison(seg: str) -> tuple[str, str, str] | None:
                seg = strip_outer_parens(seg)
                if not seg:
                    return None
                m_seg = re.match(
                    r"(?is)^(?P<left>.+?)\s*(?P<op>>=|<=|=|>|<)\s*(?P<right>.+?)\s*$",
                    seg,
                )
                if not m_seg:
                    return None
                left = (m_seg.group("left") or "").strip()
                op = (m_seg.group("op") or "=").strip() or "="
                right = (m_seg.group("right") or "").strip()

                do_name = left
                m_left_do = re.match(
                    r"(?is)^\^\.\s*(?P<do>[A-Za-z0-9_]+)\s*\.\s*(?P<ref>setVal|setMag\.f|setMag\.i)\s*$",
                    left,
                )
                if m_left_do:
                    do_name = (m_left_do.group("do") or "").strip()
                else:
                    m_left_inref = re.match(r"(?is)^\*\^\.(?P<rest>.+)$", left)
                    if m_left_inref:
                        do_name = "." + (m_left_inref.group("rest") or "").strip()

                if len(right) >= 2 and right[0] == '"' and right[-1] == '"':
                    right_val = right[1:-1]
                else:
                    right_val = right

                return (do_name, op, right_val)

            expr_core = strip_outer_parens(expr)
            top_terms, top_ops = split_top_level(expr_core)

            for ti, term_raw in enumerate(top_terms):
                outer_op = top_ops[ti] if ti < len(top_ops) else ""
                raw = (term_raw or "").strip()
                was_wrapped = raw.startswith("(") and raw.endswith(")")
                term = strip_outer_parens(raw)
                if not term:
                    continue

                # Detect a parenthesized OR-only group: ((A) OR (B))
                if was_wrapped:
                    sub_terms, sub_ops = split_top_level(term)
                    if len(sub_terms) >= 2 and sub_ops and all(x == "OR" for x in sub_ops):
                        for si, st in enumerate(sub_terms):
                            comp = parse_comparison(st)
                            if comp is None:
                                continue
                            conditions.append(comp)
                            if si < len(sub_terms) - 1:
                                joins.append("OR (high)")
                        if outer_op in {"AND", "OR"}:
                            joins.append(outer_op)
                        continue

                comp = parse_comparison(term)
                if comp is not None:
                    conditions.append(comp)
                    if outer_op in {"AND", "OR"}:
                        joins.append(outer_op)

        # 2) Determine Reverse based on THEN/ELSE assignments
        vals = re.findall(r"(?is)\.relevancy\s*:=\s*(true|false)\s*;", s)
        reverse = False
        if len(vals) >= 2:
            then_val = (vals[0] or "").strip().lower()
            else_val = (vals[1] or "").strip().lower()
            if then_val == "false" and else_val == "true":
                reverse = True
            elif then_val == "true" and else_val == "false":
                reverse = False

        # Keep joins aligned (if parsing got fewer conditions than joins)
        if len(joins) > max(0, len(conditions) - 1):
            joins = joins[: max(0, len(conditions) - 1)]

        return (conditions, joins, reverse)

    def _generate(self) -> None:
        do_name = (self.var_do.get() or "").strip()

        if self._is_ratio_selected():
            if not do_name:
                messagebox.showerror("Missing", "Please select a DO.", parent=self)
                return
            kind = (self.var_kind.get() or "").strip().lower() or "current"
            try:
                body = self._on_generate(do_name, kind)
            except Exception as e:
                messagebox.showerror("Generate failed", str(e), parent=self)
                return

            if body and not body.endswith("\n"):
                body += "\n"
            self.txt.delete("1.0", "end")
            self.txt.insert("1.0", body)
            return

        if self._is_relevancy_selected():
            reverse = bool(self.var_rel_reverse.get())

            then_val = "false" if reverse else "true"
            else_val = "true" if reverse else "false"

            rows = list(self._rel_rows or [])
            multi = len(rows) > 1

            cond_exprs: list[str] = []
            join_ops: list[str] = []  # AND | OR | OR_HIGH

            for i, row in enumerate(rows):
                cond_do = ""
                cond_op = "="
                cond_val = ""
                try:
                    cond_do = ((row.get("var_do").get()) or "").strip()
                except Exception:
                    cond_do = ""
                try:
                    cond_op = ((row.get("var_op").get()) or "=").strip() or "="
                except Exception:
                    cond_op = "="
                if cond_op not in {"=", ">", ">=", "<", "<="}:
                    cond_op = "="
                try:
                    cond_val = (row.get("var_val").get()) or ""
                except Exception:
                    cond_val = ""

                # Left-hand side can be either:
                # - a setting DO name (e.g. VNdiffMod) -> ^.VNdiffMod.setVal / setMag.f/i
                # - a raw reference (e.g. .InRef%VRtg##.VTCon.setVal) -> *^.InRef%VRtg##.VTCon.setVal
                # - any other raw expression -> used verbatim
                expr_left = ""
                if not cond_do:
                    expr_left = "^..setVal"
                elif re.match(r"^[A-Za-z0-9_]+$", cond_do):
                    ref = "setVal"
                    try:
                        ref = (self._get_relevancy_condition_ref(cond_do) or "setVal").strip() or "setVal"
                    except Exception:
                        ref = "setVal"
                    expr_left = f"^.{cond_do}.{ref}"
                elif cond_do.startswith("."):
                    expr_left = f"*^{cond_do}"
                else:
                    expr_left = cond_do
                rhs_raw = (cond_val or "").strip()
                if len(rhs_raw) >= 2 and rhs_raw[0] == '"' and rhs_raw[-1] == '"':
                    expr_right = rhs_raw
                else:
                    # If user typed a pure number, keep it unquoted.
                    # Otherwise default to quoted string.
                    if re.match(r"^[+-]?(?:\d+)(?:\.\d+)?$", rhs_raw or ""):
                        expr_right = rhs_raw
                    else:
                        expr_right = f'"{cond_val}"'

                cond_expr = f"{expr_left} {cond_op} {expr_right}"
                if multi:
                    cond_expr = f"({cond_expr})"
                cond_exprs.append(cond_expr)

                if i < len(rows) - 1:
                    raw_join = "AND"
                    try:
                        raw_join = ((row.get("var_join").get()) or "AND").strip() or "AND"
                    except Exception:
                        raw_join = "AND"
                    raw_u = raw_join.upper()
                    if raw_u == "AND":
                        join_ops.append("AND")
                    elif raw_u == "OR":
                        join_ops.append("OR")
                    elif raw_u.startswith("OR") and "HIGH" in raw_u:
                        join_ops.append("OR_HIGH")
                    else:
                        join_ops.append("AND")

            if not cond_exprs:
                expr = "^..setVal = \"\""
            else:
                groups: list[str] = []
                group_ops: list[str] = []

                cur_group: list[str] = [cond_exprs[0]]
                cur_is_or_high = False

                for j, op in enumerate(join_ops):
                    nxt = cond_exprs[j + 1]
                    if op == "OR_HIGH":
                        cur_is_or_high = True
                        cur_group.append(nxt)
                        continue

                    if cur_is_or_high and len(cur_group) > 1:
                        groups.append("(" + " OR ".join(cur_group) + ")")
                    else:
                        groups.append(cur_group[0])
                    group_ops.append(op)

                    cur_group = [nxt]
                    cur_is_or_high = False

                if cur_is_or_high and len(cur_group) > 1:
                    groups.append("(" + " OR ".join(cur_group) + ")")
                else:
                    groups.append(cur_group[0])

                out_parts: list[str] = []
                for gi, g in enumerate(groups):
                    if not g:
                        continue
                    out_parts.append(g)
                    if gi < len(group_ops):
                        out_parts.append(group_ops[gi])
                expr = " ".join(out_parts).strip() or "^..setVal = \"\""

            body = (
                f"IF ({expr})\n"
                f"THEN\n"
                f"    .relevancy := {then_val};\n"
                f"ELSE\n"
                f"    .relevancy := {else_val};\n"
                f"END_IF\n"
            )

            self.txt.delete("1.0", "end")
            self.txt.insert("1.0", body)
            return

        if self._is_dependency_selected():
            if not do_name:
                messagebox.showerror("Missing", "Please select a DO.", parent=self)
                return

            def lhs_expr(ui_val: str) -> str:
                ui_val = (ui_val or "").strip()
                if not ui_val:
                    return "^..setVal"
                if re.match(r"^[A-Za-z0-9_]+$", ui_val):
                    ref = "setVal"
                    try:
                        ref = (self._get_relevancy_condition_ref(ui_val) or "setVal").strip() or "setVal"
                    except Exception:
                        ref = "setVal"
                    return f"^.{ui_val}.{ref}"
                if ui_val.startswith("."):
                    return f"*^{ui_val}"
                return ui_val

            def fixed_rhs(val: str) -> str:
                raw = (val or "").strip()
                if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
                    return raw
                if re.match(r"^[+-]?(?:\d+)(?:\.\d+)?$", raw or ""):
                    return raw
                return f'"{val}"'

            n_conds = 1
            try:
                n_conds = int(self.var_dep_n.get())
            except Exception:
                n_conds = 1
            if n_conds < 0:
                n_conds = 0
            if n_conds > 8:
                n_conds = 8

            expr = ""
            if n_conds > 0:
                cond_exprs: list[str] = []
                join_ops: list[str] = []  # AND | OR | OR_HIGH
                rows = list(getattr(self, "_dep_rows", []) or [])
                multi = len(rows) > 1

                for i, row in enumerate(rows):
                    try:
                        cdo = ((row.get("var_cond_do").get()) or "").strip()
                    except Exception:
                        cdo = ""
                    try:
                        cop = ((row.get("var_op").get()) or "=").strip() or "="
                    except Exception:
                        cop = "="
                    if cop not in {"=", ">", ">=", "<", "<="}:
                        cop = "="
                    try:
                        is_fixed = bool(row.get("var_fixed").get())
                    except Exception:
                        is_fixed = False
                    try:
                        fval = (row.get("var_fixed_val").get()) or ""
                    except Exception:
                        fval = ""
                    try:
                        rdo = ((row.get("var_rhs_do").get()) or "").strip()
                    except Exception:
                        rdo = ""

                    left = lhs_expr(cdo)
                    if is_fixed:
                        right = fixed_rhs(fval)
                    else:
                        right = lhs_expr(rdo)
                    ce = f"{left} {cop} {right}"
                    if multi:
                        ce = f"({ce})"
                    cond_exprs.append(ce)

                    if i < len(rows) - 1:
                        raw_join = "AND"
                        try:
                            raw_join = ((row.get("var_join").get()) or "AND").strip() or "AND"
                        except Exception:
                            raw_join = "AND"
                        raw_u = raw_join.upper()
                        if raw_u == "AND":
                            join_ops.append("AND")
                        elif raw_u == "OR":
                            join_ops.append("OR")
                        elif raw_u.startswith("OR") and "HIGH" in raw_u:
                            join_ops.append("OR_HIGH")
                        else:
                            join_ops.append("AND")

                # Group OR_HIGH segments so they get extra parentheses.
                if not cond_exprs:
                    expr = "^..setVal = \"\""
                else:
                    groups: list[str] = []
                    group_ops: list[str] = []
                    cur_group: list[str] = [cond_exprs[0]]
                    cur_is_or_high = False
                    for j, op in enumerate(join_ops):
                        nxt = cond_exprs[j + 1]
                        if op == "OR_HIGH":
                            cur_is_or_high = True
                            cur_group.append(nxt)
                            continue
                        if cur_is_or_high and len(cur_group) > 1:
                            groups.append("(" + " OR ".join(cur_group) + ")")
                        else:
                            groups.append(cur_group[0])
                        group_ops.append(op)
                        cur_group = [nxt]
                        cur_is_or_high = False
                    if cur_is_or_high and len(cur_group) > 1:
                        groups.append("(" + " OR ".join(cur_group) + ")")
                    else:
                        groups.append(cur_group[0])

                    out_parts: list[str] = []
                    for gi, g in enumerate(groups):
                        if not g:
                            continue
                        out_parts.append(g)
                        if gi < len(group_ops):
                            out_parts.append(group_ops[gi])
                    expr = " ".join(out_parts).strip() or "^..setVal = \"\""

            target_ref = "setVal"
            try:
                target_ref = (self._get_relevancy_condition_ref(do_name) or "setVal").strip() or "setVal"
            except Exception:
                target_ref = "setVal"

            if bool(self.var_dep_assign_fixed.get()):
                assign_rhs = fixed_rhs(self.var_dep_assign_fixed_val.get() or "")
            else:
                assign_rhs = lhs_expr(self.var_dep_assign_do.get() or "")

            if n_conds > 0:
                body = (
                    f"IF ({expr})\n"
                    f"THEN\n"
                    f"    .{target_ref} := {assign_rhs};\n"
                    f"END_IF\n"
                )
            else:
                body = f".{target_ref} := {assign_rhs};\n"

            self.txt.delete("1.0", "end")
            self.txt.insert("1.0", body)
            return

        # Dependency Rule has no generator yet.
        return

    def _clean(self) -> None:
        """Clear current DO + current rule type text (does not auto-apply)."""
        ok = False
        try:
            ok = bool(
                messagebox.askyesno(
                    "Confirm",
                    "Clear the current DO and rule type content?",
                    parent=self,
                )
            )
        except Exception:
            ok = True

        if not ok:
            return
        try:
            self.txt.delete("1.0", "end")
        except Exception:
            return
        if self._cur_key is not None:
            self._buffers[self._cur_key] = ""

    def _apply(self) -> None:
        do_name = (self.var_do.get() or "").strip()
        if self._require_do_selection and not do_name:
            messagebox.showerror("Missing", "DO is required", parent=self)
            return
        private_type = self._selected_private_type()
        if not private_type:
            messagebox.showerror("Missing", "Rule type is required", parent=self)
            return
        txt = ""
        try:
            txt = self.txt.get("1.0", "end-1c").rstrip("\n")
        except Exception:
            txt = ""

        # Keep buffer consistent
        if do_name and private_type:
            self._buffers[(do_name, private_type)] = txt

        try:
            self._apply_rule_text(do_name, private_type, txt)
        except Exception as e:
            messagebox.showerror("Apply failed", str(e), parent=self)
            raise

    def _ok(self) -> None:
        try:
            self._apply()
        except Exception:
            return
        self._result = True
        self.destroy()

    def _cancel(self) -> None:
        self._result = None
        self.destroy()

    def show(self) -> bool | None:
        self.wait_window(self)
        return self._result


class MainWindow(tk.Tk):
    def __init__(self, *, workspace_root: Path, open_builder_callback):
        super().__init__()
        self.title(f"{APP_TITLE}")
        self.geometry("1100x720")

        self.workspace_root = workspace_root
        self.open_builder_callback = open_builder_callback

        self.status = tk.StringVar(value="")

        self._create_menu()

        self.body = ttk.Frame(self)
        self.body.pack(fill="both", expand=True)

        self.notebook: ttk.Notebook | None = None
        self.tab_template: ttk.Frame | None = None
        self.tab_instance: ttk.Frame | None = None
        self.tab_application: ttk.Frame | None = None
        self.instance_editor: LNInstanceEditorFrame | None = None

        # Application editor state
        self._app_file_path: Path | None = None
        self._app_root: ET.Element | None = None
        self._app_funblock: ET.Element | None = None
        self._app_tv_input: ttk.Treeview | None = None
        self._app_tv_setting: ttk.Treeview | None = None
        self._app_tv_output: ttk.Treeview | None = None
        self._app_tv_conf: ttk.Treeview | None = None
        self._app_tv_control: ttk.Treeview | None = None

        self._app_input_rows: list[dict[str, str]] = []
        self._app_input_iid_to_row: dict[str, dict[str, str]] = {}
        self._app_input_types_cache: list[str] | None = None
        self._app_input_inline: tk.Widget | None = None
        self._app_input_inline_iid: str | None = None
        self._app_input_inline_col: str | None = None

        self._app_setting_types_cache: list[str] | None = None
        self._app_setting_inline: tk.Widget | None = None
        self._app_setting_inline_iid: str | None = None
        self._app_setting_inline_col: str | None = None

        self._app_output_types_cache: list[str] | None = None
        self._app_conf_types_cache: list[str] | None = None
        self._app_control_types_cache: list[str] | None = None
        self._app_output_inline: tk.Widget | None = None
        self._app_output_inline_iid: str | None = None
        self._app_output_inline_col: str | None = None
        self._app_conf_inline: tk.Widget | None = None
        self._app_conf_inline_iid: str | None = None
        self._app_conf_inline_col: str | None = None
        self._app_control_inline: tk.Widget | None = None
        self._app_control_inline_iid: str | None = None
        self._app_control_inline_col: str | None = None

        self._app_setting_rows: list[dict[str, str]] = []
        self._app_output_rows: list[dict[str, str]] = []
        self._app_conf_rows: list[dict[str, str]] = []
        self._app_control_rows: list[dict[str, str]] = []
        self._app_clipboard: dict[str, dict[str, str]] = {}
        self._app_ctx_table: str | None = None
        self._app_ctx_menu: tk.Menu | None = None

        self._set_status("Scanning IEC 61850 types...")
        self.update_idletasks()

        iec61850_dir = self.workspace_root / "ep7_datamodel" / "datamodel" / "iec61850"
        if not iec61850_dir.exists():
            messagebox.showerror(
                "Missing",
                f"IEC61850 folder not found:\n{os.fspath(iec61850_dir)}",
                parent=self,
            )
            self.destroy()
            return

        try:
            self.catalog = scan_type_catalog(iec61850_dir)
        except Exception as e:
            messagebox.showerror("Scan failed", str(e), parent=self)
            self.destroy()
            return

        self.notebook = ttk.Notebook(self.body)
        self.notebook.pack(fill="both", expand=True)

        self.tab_template = ttk.Frame(self.notebook)
        self.tab_instance = ttk.Frame(self.notebook)
        self.tab_application = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_template, text="LN template")
        self.notebook.add(self.tab_instance, text="LN instance")
        self.notebook.add(self.tab_application, text="Application")

        self.editor = LNodeTypeEditor(
            self.tab_template,
            catalog=self.catalog,
            iec61850_dir=iec61850_dir,
            create_instance_callback=self._create_instance_with_template,
        )
        self.editor.pack(fill="both", expand=True)

        lndm_dir = self.workspace_root / "ep7_datamodel" / "datamodel" / "lndm"
        self.instance_editor = LNInstanceEditorFrame(
            self.tab_instance,
            workspace_root=self.workspace_root,
            lndm_dir=lndm_dir,
            show_status_bar=False,
            status_callback=self._set_status,
        )
        self.instance_editor.pack(fill="both", expand=True)

        # Application tab UI
        if self.tab_application is not None and self.instance_editor is not None:
            toolbar = ttk.Frame(self.tab_application, padding=(10, 10, 10, 0))
            toolbar.pack(fill="x")
            ttk.Button(toolbar, text="New", command=self._new_application).pack(side="left")
            ttk.Button(toolbar, text="Open", command=self._open_application).pack(side="left", padx=(8, 0))
            ttk.Button(toolbar, text="Save", command=self._save_application).pack(side="left", padx=(8, 0))
            ttk.Button(toolbar, text="Save As", command=self._save_application_as).pack(side="left", padx=(8, 0))

            body = ttk.Frame(self.tab_application, padding=10)
            body.pack(fill="both", expand=True)
            body.columnconfigure(0, weight=1)
            body.rowconfigure(1, weight=1)

            fb = ttk.LabelFrame(body, text="funBlock", padding=10)
            fb.grid(row=0, column=0, sticky="we")
            # One-row fields
            for col in (1, 3, 7, 9):
                fb.columnconfigure(col, weight=1)

            ttk.Label(fb, text="name").grid(row=0, column=0, sticky="w")
            ttk.Entry(fb, textvariable=self.instance_editor.var_app_name, width=18).grid(row=0, column=1, sticky="we", padx=(6, 12))

            ttk.Label(fb, text="class").grid(row=0, column=2, sticky="w")
            ttk.Entry(fb, textvariable=self.instance_editor.var_app_class, width=18).grid(row=0, column=3, sticky="we", padx=(6, 12))

            ttk.Label(fb, text="seqNb").grid(row=0, column=4, sticky="w")
            ttk.Entry(fb, textvariable=self.instance_editor.var_app_seqNb, width=6).grid(row=0, column=5, sticky="w", padx=(6, 12))

            ttk.Label(fb, text="LnRef").grid(row=0, column=6, sticky="w")
            ttk.Entry(fb, textvariable=self.instance_editor.var_app_LnRef, width=22).grid(row=0, column=7, sticky="we", padx=(6, 12))

            ttk.Label(fb, text="desc").grid(row=0, column=8, sticky="w")
            ttk.Entry(fb, textvariable=self.instance_editor.var_app_desc, width=30).grid(row=0, column=9, sticky="we", padx=(6, 0))

            sub = ttk.Notebook(body)
            sub.grid(row=1, column=0, sticky="nsew", pady=(10, 0))

            tab_in = ttk.Frame(sub)
            tab_out = ttk.Frame(sub)
            tab_set = ttk.Frame(sub)
            tab_conf = ttk.Frame(sub)
            tab_ctl = ttk.Frame(sub)
            sub.add(tab_in, text="input")
            sub.add(tab_out, text="output")
            sub.add(tab_set, text="setting")
            sub.add(tab_conf, text="conf")
            sub.add(tab_ctl, text="control")

            def _make_tv(parent: tk.Misc, cols: list[str], heads: list[str]) -> ttk.Treeview:
                wrap = ttk.Frame(parent)
                wrap.pack(fill="both", expand=True)
                wrap.columnconfigure(0, weight=1)
                wrap.rowconfigure(1, weight=1)

                # Toolbar
                tb = ttk.Frame(wrap, padding=(0, 6, 0, 6))
                tb.grid(row=0, column=0, columnspan=2, sticky="we")

                def _btn(label: str, cmd, padx=(0, 0)) -> None:
                    ttk.Button(tb, text=label, command=cmd).pack(side="left", padx=padx)

                # The table key will be injected by closure at caller.
                # Placeholders here.

                tv = ttk.Treeview(wrap, columns=cols, show="headings", selectmode="browse")
                for c, h in zip(cols, heads, strict=False):
                    tv.heading(c, text=h)
                    if c in {"softlink", "confpin", "persist", "faultlog"}:
                        tv.column(c, anchor="center", width=80)
                    else:
                        tv.column(c, anchor="w", width=160)
                tv.grid(row=1, column=0, sticky="nsew")
                sb = ttk.Scrollbar(wrap, orient="vertical", command=tv.yview)
                tv.configure(yscrollcommand=sb.set)
                sb.grid(row=1, column=1, sticky="ns")
                wrap._toolbar = tb  # type: ignore[attr-defined]
                wrap._btn = _btn  # type: ignore[attr-defined]
                return tv

            self._app_tv_input = _make_tv(
                tab_in,
                ["name", "type", "src", "doRef", "softlink", "confpin"],
                ["name", "type", "src", "doRef", "softlink", "confpin"],
            )
            self._app_tv_setting = _make_tv(tab_set, ["name", "type", "src", "desc"], ["name", "type", "src", "desc"])
            self._app_tv_output = _make_tv(
                tab_out,
                ["name", "type", "doRef", "MaxContiguous", "Overlap", "persist", "faultlog", "desc"],
                ["name", "type", "doRef", "MaxContiguous", "Overlap", "persist", "faultlog", "desc"],
            )
            self._app_tv_conf = _make_tv(tab_conf, ["name", "type", "src", "desc"], ["name", "type", "src", "desc"])
            self._app_tv_control = _make_tv(tab_ctl, ["name", "type", "src", "desc"], ["name", "type", "src", "desc"])

            # Hook toolbars + context menus
            self._init_app_table_ui("input", self._app_tv_input)
            self._init_app_table_ui("setting", self._app_tv_setting)
            self._init_app_table_ui("output", self._app_tv_output)
            self._init_app_table_ui("conf", self._app_tv_conf)
            self._init_app_table_ui("control", self._app_tv_control)

            if self._app_tv_input is not None:
                # Single-click inline edit + checkbox toggle
                self._app_tv_input.bind("<Button-1>", self._on_app_input_click)
                self._app_tv_input.bind("<Escape>", lambda _e: self._end_app_input_inline_editor(commit=False))
                # Custom double click behavior (type typing mode)
                self._app_tv_input.bind("<Double-1>", self._on_app_input_double_click)

            if self._app_tv_setting is not None:
                self._app_tv_setting.bind("<Button-1>", self._on_app_setting_click)
                self._app_tv_setting.bind("<Double-1>", self._on_app_setting_double_click)
                self._app_tv_setting.bind("<Escape>", lambda _e: self._end_app_setting_inline_editor(commit=False))

            if self._app_tv_output is not None:
                self._app_tv_output.bind("<Button-1>", self._on_app_output_click)
                self._app_tv_output.bind("<Double-1>", self._on_app_output_double_click)
                self._app_tv_output.bind("<Escape>", lambda _e: self._end_app_output_inline_editor(commit=False))

            if self._app_tv_conf is not None:
                self._app_tv_conf.bind("<Button-1>", self._on_app_conf_click)
                self._app_tv_conf.bind("<Double-1>", self._on_app_conf_double_click)
                self._app_tv_conf.bind("<Escape>", lambda _e: self._end_app_conf_inline_editor(commit=False))

            if self._app_tv_control is not None:
                self._app_tv_control.bind("<Button-1>", self._on_app_control_click)
                self._app_tv_control.bind("<Double-1>", self._on_app_control_double_click)
                self._app_tv_control.bind("<Escape>", lambda _e: self._end_app_control_inline_editor(commit=False))

        # Shortcuts
        self.bind_all("<Control-n>", lambda _e: self._new_shortcut())
        self.bind_all("<Control-N>", lambda _e: self._new_shortcut())
        self.bind_all("<Control-o>", lambda _e: self._open_shortcut())
        self.bind_all("<Control-O>", lambda _e: self._open_shortcut())
        self.bind_all("<Control-s>", lambda _e: self._save_shortcut())
        self.bind_all("<Control-S>", lambda _e: self._save_shortcut())
        self.bind_all("<Control-Shift-s>", lambda _e: self._save_as_shortcut())
        self.bind_all("<Control-Shift-S>", lambda _e: self._save_as_shortcut())

        self._set_status(
            f"Loaded: DOType={len(self.catalog.do_types)}  DAType={len(self.catalog.da_types)}  EnumType={len(self.catalog.enum_types)}  LNodeType={len(self.catalog.lnode_types)}"
        )

    def _create_instance_with_template(self, model: LNodeTypeModel) -> None:
        if self.instance_editor is None:
            return
        try:
            self.notebook.select(self.tab_instance)
        except Exception:
            pass
        self.instance_editor.create_instance_with_template_model(model)

    def _active_tab(self) -> int:
        return self.notebook.index("current") if self.notebook is not None else 0

    def _new_shortcut(self) -> None:
        tab = self._active_tab()
        if tab == 0:
            self.editor.new_template()
            if self.editor.model is not None:
                self._set_status(f"Created: {os.fspath(self.editor.model.info.file_path)}")
        elif tab == 1:
            if self.instance_editor is None:
                return
            self.instance_editor.new_instance()
        else:
            self._new_application()

    def _open_shortcut(self) -> None:
        tab = self._active_tab()
        if tab == 0:
            self.editor.open_template()
            if self.editor.model is not None:
                self._set_status(f"Opened: {os.fspath(self.editor.model.info.file_path)}")
        elif tab == 1:
            if self.instance_editor is None:
                return
            self.instance_editor.open_dialog()
            if self.instance_editor.doc is not None:
                self._set_status(f"Opened: {os.fspath(self.instance_editor.doc.file_path)}")
        else:
            self._open_application()

    def _save_shortcut(self) -> None:
        tab = self._active_tab()
        if tab == 0:
            self.editor.save_current()
            if self.editor.model is not None:
                self._set_status(f"Saved: {os.fspath(self.editor.model.info.file_path)}")
        elif tab == 1:
            if self.instance_editor is None:
                return
            self.instance_editor.save()
            if self.instance_editor.doc is not None:
                self._set_status(f"Saved: {os.fspath(self.instance_editor.doc.file_path)}")
        else:
            self._save_application()

    def _save_as_shortcut(self) -> None:
        tab = self._active_tab()
        if tab == 0:
            self.editor.save_as()
            if self.editor.model is not None:
                self._set_status(f"Saved As: {os.fspath(self.editor.model.info.file_path)}")
        elif tab == 1:
            if self.instance_editor is None:
                return
            self.instance_editor.save_as()
            if self.instance_editor.doc is not None:
                self._set_status(f"Saved As: {os.fspath(self.instance_editor.doc.file_path)}")
        else:
            self._save_application_as()

    def _application_dir(self) -> Path:
        return self.workspace_root / "ep7_datamodel" / "datamodel" / "application"

    def _lndm_dir(self) -> Path:
        return self.workspace_root / "ep7_datamodel" / "datamodel" / "lndm"

    def _scan_xml_relpaths(self, base_dir: Path) -> list[str]:
        base_dir = Path(base_dir)
        if not base_dir.exists():
            return []
        rels: list[str] = []
        try:
            for p in base_dir.rglob("*.xml"):
                try:
                    rels.append(os.fspath(p.relative_to(base_dir)))
                except Exception:
                    rels.append(os.fspath(p))
                if len(rels) >= 8000:
                    break
        except Exception:
            rels = [os.fspath(p.name) for p in base_dir.glob("*.xml")]
        rels.sort(key=lambda s: s.lower())
        return rels

    def _scan_application_input_types(self) -> list[str]:
        app_dir = self._application_dir()
        if not app_dir.exists():
            return []
        types: set[str] = set()
        for rel in self._scan_xml_relpaths(app_dir):
            p = app_dir / rel
            try:
                root = ET.parse(p).getroot()
            except Exception:
                continue
            for el in root.iter():
                if not isinstance(el.tag, str):
                    continue
                if self._local_name(el.tag) != "input":
                    continue
                t = (el.attrib.get("type") or "").strip()
                if t:
                    types.add(t)
        return sorted(types, key=lambda s: s.lower())

    def _scan_application_setting_types(self) -> list[str]:
        app_dir = self._application_dir()
        if not app_dir.exists():
            return []
        types: set[str] = set()
        for rel in self._scan_xml_relpaths(app_dir):
            p = app_dir / rel
            try:
                root = ET.parse(p).getroot()
            except Exception:
                continue
            for el in root.iter():
                if not isinstance(el.tag, str):
                    continue
                if self._local_name(el.tag) != "setting":
                    continue
                t = (el.attrib.get("type") or "").strip()
                if t:
                    types.add(t)
        return sorted(types, key=lambda s: s.lower())

    def _scan_application_output_types(self) -> list[str]:
        app_dir = self._application_dir()
        if not app_dir.exists():
            return []
        types: set[str] = set()
        for rel in self._scan_xml_relpaths(app_dir):
            p = app_dir / rel
            try:
                root = ET.parse(p).getroot()
            except Exception:
                continue
            for el in root.iter():
                if not isinstance(el.tag, str):
                    continue
                if self._local_name(el.tag) != "output":
                    continue
                t = (el.attrib.get("type") or "").strip()
                if t:
                    types.add(t)
        return sorted(types, key=lambda s: s.lower())

    def _scan_application_conf_types(self) -> list[str]:
        app_dir = self._application_dir()
        if not app_dir.exists():
            return []
        types: set[str] = set()
        for rel in self._scan_xml_relpaths(app_dir):
            p = app_dir / rel
            try:
                root = ET.parse(p).getroot()
            except Exception:
                continue
            for el in root.iter():
                if not isinstance(el.tag, str):
                    continue
                if self._local_name(el.tag) != "conf":
                    continue
                t = (el.attrib.get("type") or "").strip()
                if t:
                    types.add(t)
        return sorted(types, key=lambda s: s.lower())

    def _scan_application_control_types(self) -> list[str]:
        app_dir = self._application_dir()
        if not app_dir.exists():
            return []
        types: set[str] = set()
        for rel in self._scan_xml_relpaths(app_dir):
            p = app_dir / rel
            try:
                root = ET.parse(p).getroot()
            except Exception:
                continue
            for el in root.iter():
                if not isinstance(el.tag, str):
                    continue
                if self._local_name(el.tag) != "control":
                    continue
                t = (el.attrib.get("type") or "").strip()
                if t:
                    types.add(t)
        return sorted(types, key=lambda s: s.lower())

    def _get_app_input_types(self) -> list[str]:
        if self._app_input_types_cache is None:
            self._app_input_types_cache = self._scan_application_input_types()
        return list(self._app_input_types_cache)

    def _get_app_setting_types(self) -> list[str]:
        if self._app_setting_types_cache is None:
            self._app_setting_types_cache = self._scan_application_setting_types()
        return list(self._app_setting_types_cache)

    def _get_app_output_types(self) -> list[str]:
        if self._app_output_types_cache is None:
            self._app_output_types_cache = self._scan_application_output_types()
        return list(self._app_output_types_cache)

    def _get_app_conf_types(self) -> list[str]:
        if self._app_conf_types_cache is None:
            self._app_conf_types_cache = self._scan_application_conf_types()
        return list(self._app_conf_types_cache)

    def _get_app_control_types(self) -> list[str]:
        if self._app_control_types_cache is None:
            self._app_control_types_cache = self._scan_application_control_types()
        return list(self._app_control_types_cache)

    def _sanitize_purpose(self, purpose: str) -> str:
        raw = (purpose or "").strip()
        if not raw:
            return ""
        # Remove any bracketed qualifiers, e.g. "Inh(general)" or "Inh[general]" -> "Inh"
        s = re.sub(r"\s*\(.*?\)\s*", "", raw)
        s = re.sub(r"\s*\[.*?\]\s*", "", s)
        return s.strip()

    def _read_ln_instance_lntype(self, ln_path: Path) -> str:
        doc = load_ln_instance_document(Path(ln_path))
        ln = doc.ln_elements[0]
        return ((ln.attrib.get("lnType") or "").strip())

    def _find_type_file(self, *, kind_dir: Path, type_id: str) -> Path | None:
        type_id = (type_id or "").strip()
        if not type_id:
            return None

        cache = getattr(self, "_type_file_cache", None)
        if cache is None:
            cache = {}
            setattr(self, "_type_file_cache", cache)
        key = (os.fspath(kind_dir), type_id)
        if key in cache:
            return cache[key]

        # Fast path: direct file name match at root
        direct = Path(kind_dir) / f"{type_id}.xml"
        if direct.is_file():
            cache[key] = direct
            return direct

        # Recursive search (folders like P7/ etc)
        found: Path | None = None
        try:
            for p in Path(kind_dir).rglob(f"{type_id}.xml"):
                if p.is_file():
                    found = p
                    break
        except Exception:
            found = None

        cache[key] = found
        return found

    def _do_type_spse_info(self, do_type_id: str) -> tuple[bool, str]:
        """Return (has_fc_spse, inferred_basic_type) for a DOType."""
        do_type_id = (do_type_id or "").strip()
        if not do_type_id:
            return (False, "")

        cache = getattr(self, "_do_spse_cache", None)
        if cache is None:
            cache = {}
            setattr(self, "_do_spse_cache", cache)
        if do_type_id in cache:
            return cache[do_type_id]

        do_dir = self.workspace_root / "ep7_datamodel" / "datamodel" / "iec61850" / "DOType"

        path = self._find_type_file(kind_dir=do_dir, type_id=do_type_id)
        if path is None:
            cache[do_type_id] = (False, "")
            return cache[do_type_id]

        try:
            tree = ET.parse(path)
            root = tree.getroot()
        except Exception:
            cache[do_type_id] = (False, "")
            return cache[do_type_id]

        ns = ""
        if isinstance(root.tag, str) and root.tag.startswith("{"):
            ns = root.tag.split("}", 1)[0][1:]

        def q(tag: str) -> str:
            return f"{{{ns}}}{tag}" if ns else tag

        visited: set[str] = set()

        def map_btype(btype: str) -> str:
            bt = (btype or "").strip()
            if not bt:
                return ""
            if bt.lower() == "enum":
                return "ENUMERATED"
            return bt.upper()

        def scan_do_type(type_id: str) -> tuple[bool, str]:
            type_id = (type_id or "").strip()
            if not type_id or type_id in visited:
                return (False, "")
            visited.add(type_id)

            p2 = self._find_type_file(kind_dir=do_dir, type_id=type_id)
            if p2 is None:
                return (False, "")
            try:
                r2 = ET.parse(p2).getroot()
            except Exception:
                return (False, "")

            ns2 = ""
            if isinstance(r2.tag, str) and r2.tag.startswith("{"):
                ns2 = r2.tag.split("}", 1)[0][1:]

            def q2(tag: str) -> str:
                return f"{{{ns2}}}{tag}" if ns2 else tag

            do_el = r2.find(f".//{q2('DOType')}")
            if do_el is None:
                return (False, "")

            # Prefer DA named setVal with fc=SP/SE to infer basic type.
            inferred = ""
            has = False
            for da in do_el.findall(q2("DA")):
                fc = (da.attrib.get("fc") or "").strip().upper()
                if fc in {"SP", "SE"}:
                    has = True
                    if (da.attrib.get("name") or "") == "setVal" and not inferred:
                        inferred = map_btype(da.attrib.get("bType") or "")
            if has and not inferred:
                for da in do_el.findall(q2("DA")):
                    fc = (da.attrib.get("fc") or "").strip().upper()
                    if fc in {"SP", "SE"}:
                        inferred = map_btype(da.attrib.get("bType") or "")
                        if inferred:
                            break

            # Recurse into SDOs (sub data objects)
            for sdo in do_el.findall(q2("SDO")):
                sub_type = (sdo.attrib.get("type") or "").strip()
                sub_has, sub_inferred = scan_do_type(sub_type)
                if sub_has:
                    has = True
                if has and not inferred and sub_inferred:
                    inferred = sub_inferred

            return (has, inferred)

        res = scan_do_type(do_type_id)
        cache[do_type_id] = res
        return res

    def _do_type_setting_entries(self, do_type_id: str) -> list[tuple[str, str]]:
        """Return list of (suffix_path, inferred_basic_type) for setting rows.

        Most DOs return [("", <type>)] meaning one setting per DO.
        Special case: setMag struct keeps one row per DO, but forces inferred type to FLOAT32/INT32
        based on the DAType BDA (f/i).
        """
        do_type_id = (do_type_id or "").strip()
        if not do_type_id:
            return []

        cache = getattr(self, "_do_setting_entries_cache", None)
        if cache is None:
            cache = {}
            setattr(self, "_do_setting_entries_cache", cache)
        if do_type_id in cache:
            return list(cache[do_type_id])

        do_dir = self.workspace_root / "ep7_datamodel" / "datamodel" / "iec61850" / "DOType"
        da_dir = self.workspace_root / "ep7_datamodel" / "datamodel" / "iec61850" / "DAType"

        def map_btype(btype: str) -> str:
            bt = (btype or "").strip()
            if not bt:
                return ""
            if bt.lower() == "enum":
                return "ENUMERATED"
            return bt.upper()

        visited: set[str] = set()

        def scan_do_type(type_id: str) -> tuple[bool, str, list[tuple[str, str]]]:
            type_id = (type_id or "").strip()
            if not type_id or type_id in visited:
                return (False, "", [])
            visited.add(type_id)

            p = self._find_type_file(kind_dir=do_dir, type_id=type_id)
            if p is None:
                return (False, "", [])
            try:
                root = ET.parse(p).getroot()
            except Exception:
                return (False, "", [])

            ns = ""
            if isinstance(root.tag, str) and root.tag.startswith("{"):
                ns = root.tag.split("}", 1)[0][1:]

            def q(tag: str) -> str:
                return f"{{{ns}}}{tag}" if ns else tag

            do_el = root.find(f".//{q('DOType')}")
            if do_el is None:
                return (False, "", [])

            has = False
            inferred = ""

            # Scan DA
            for da in do_el.findall(q("DA")):
                fc = (da.attrib.get("fc") or "").strip().upper()
                if fc not in {"SP", "SE"}:
                    continue
                has = True
                da_name = (da.attrib.get("name") or "").strip()
                btype = (da.attrib.get("bType") or "").strip()

                # Special case: setMag Struct -> infer type from DAType leaf (f/i)
                if da_name == "setMag" and btype.lower() == "struct":
                    da_type_id = (da.attrib.get("type") or "").strip()
                    if da_type_id:
                        p_da = self._find_type_file(kind_dir=da_dir, type_id=da_type_id)
                        if p_da is not None:
                            try:
                                da_root = ET.parse(p_da).getroot()
                            except Exception:
                                da_root = None
                            if da_root is not None:
                                ns_da = ""
                                if isinstance(da_root.tag, str) and da_root.tag.startswith("{"):
                                    ns_da = da_root.tag.split("}", 1)[0][1:]

                                def qda(tag: str) -> str:
                                    return f"{{{ns_da}}}{tag}" if ns_da else tag

                                da_type_el = da_root.find(f".//{qda('DAType')}")
                                if da_type_el is not None:
                                    has_f = False
                                    has_i = False
                                    for bda in da_type_el.findall(qda("BDA")):
                                        bda_name = (bda.attrib.get("name") or "").strip()
                                        if bda_name == "f":
                                            has_f = True
                                        elif bda_name == "i":
                                            has_i = True
                                    # Force mapping per requirement.
                                    forced = "FLOAT32" if has_f else ("INT32" if has_i else "")
                                    if forced:
                                        inferred = forced
                    # Do not treat setMag as creating extra setting rows.
                    continue

                # Default inference: prefer setVal
                if da_name == "setVal" and not inferred:
                    inferred = map_btype(btype)

            if has and not inferred:
                for da in do_el.findall(q("DA")):
                    fc = (da.attrib.get("fc") or "").strip().upper()
                    if fc in {"SP", "SE"}:
                        inferred = map_btype(da.attrib.get("bType") or "")
                        if inferred:
                            break

            # Recurse into SDOs
            for sdo in do_el.findall(q("SDO")):
                sub_type = (sdo.attrib.get("type") or "").strip()
                sub_has, sub_inferred, sub_entries = scan_do_type(sub_type)
                if sub_has:
                    has = True
                if has and not inferred and sub_inferred:
                    inferred = sub_inferred

            return (has, inferred, [("", inferred)] if has else [])

        has, inferred, entries = scan_do_type(do_type_id)
        out = list(entries) if has else []
        cache[do_type_id] = out
        return list(out)

    def _do_type_stmx_info(self, do_type_id: str) -> tuple[bool, str]:
        """Return (has_fc_stmx, inferred_basic_type) for a DOType.

        Used for auto-generating Application output rows.
        """
        do_type_id = (do_type_id or "").strip()
        if not do_type_id:
            return (False, "")

        cache = getattr(self, "_do_stmx_cache", None)
        if cache is None:
            cache = {}
            setattr(self, "_do_stmx_cache", cache)
        if do_type_id in cache:
            return cache[do_type_id]

        do_dir = self.workspace_root / "ep7_datamodel" / "datamodel" / "iec61850" / "DOType"
        da_dir = self.workspace_root / "ep7_datamodel" / "datamodel" / "iec61850" / "DAType"

        def map_btype(btype: str) -> str:
            bt = (btype or "").strip()
            if not bt:
                return ""
            if bt.lower() == "enum":
                return "ENUMERATED"
            return bt.upper()

        def infer_from_datype(da_type_id: str) -> str:
            da_type_id = (da_type_id or "").strip()
            if not da_type_id:
                return ""
            p_da = self._find_type_file(kind_dir=da_dir, type_id=da_type_id)
            if p_da is None:
                return ""
            try:
                da_root = ET.parse(p_da).getroot()
            except Exception:
                return ""
            ns_da = ""
            if isinstance(da_root.tag, str) and da_root.tag.startswith("{"):
                ns_da = da_root.tag.split("}", 1)[0][1:]

            def qda(tag: str) -> str:
                return f"{{{ns_da}}}{tag}" if ns_da else tag

            da_type_el = da_root.find(f".//{qda('DAType')}")
            if da_type_el is None:
                return ""

            # Common analogue patterns: prefer f (FLOAT32), else i (INT32/INT32U)
            has_f = False
            has_i = False
            i_btype = ""
            for bda in da_type_el.findall(qda("BDA")):
                nm = (bda.attrib.get("name") or "").strip()
                if nm == "f":
                    has_f = True
                elif nm == "i":
                    has_i = True
                    i_btype = (bda.attrib.get("bType") or "").strip()
            if has_f:
                return "FLOAT32"
            if has_i:
                # Respect unsigned if present (e.g. INT32U)
                bt = map_btype(i_btype)
                return bt if bt else "INT32"
            return ""

        visited: set[str] = set()

        def scan_do_type(type_id: str) -> tuple[bool, str]:
            type_id = (type_id or "").strip()
            if not type_id or type_id in visited:
                return (False, "")
            visited.add(type_id)

            p = self._find_type_file(kind_dir=do_dir, type_id=type_id)
            if p is None:
                return (False, "")
            try:
                root = ET.parse(p).getroot()
            except Exception:
                return (False, "")

            ns = ""
            if isinstance(root.tag, str) and root.tag.startswith("{"):
                ns = root.tag.split("}", 1)[0][1:]

            def q(tag: str) -> str:
                return f"{{{ns}}}{tag}" if ns else tag

            do_el = root.find(f".//{q('DOType')}")
            if do_el is None:
                return (False, "")

            has = False
            inferred = ""

            def try_infer_da(da: ET.Element) -> str:
                btype = (da.attrib.get("bType") or "").strip()
                if not btype:
                    return ""
                if btype.lower() != "struct":
                    return map_btype(btype)
                # Struct: try DAType leaf mapping
                return infer_from_datype((da.attrib.get("type") or "").strip()) or map_btype(btype)

            # Prefer common value DAs when present.
            preferred_names = {"stval", "mag", "instmag", "cval"}
            for da in do_el.findall(q("DA")):
                fc = (da.attrib.get("fc") or "").strip().upper()
                if fc not in {"ST", "MX"}:
                    continue
                has = True
                da_name = (da.attrib.get("name") or "").strip().lower()
                if da_name in preferred_names and not inferred:
                    inferred = try_infer_da(da)

            if has and not inferred:
                for da in do_el.findall(q("DA")):
                    fc = (da.attrib.get("fc") or "").strip().upper()
                    if fc in {"ST", "MX"}:
                        inferred = try_infer_da(da)
                        if inferred:
                            break

            # Recurse into SDOs
            for sdo in do_el.findall(q("SDO")):
                sub_type = (sdo.attrib.get("type") or "").strip()
                sub_has, sub_inferred = scan_do_type(sub_type)
                if sub_has:
                    has = True
                if has and not inferred and sub_inferred:
                    inferred = sub_inferred

            return (has, inferred)

        res = scan_do_type(do_type_id)
        cache[do_type_id] = res
        return res

    def _do_type_cdc(self, do_type_id: str) -> str:
        """Return DOType@cdc for a DOType id (uppercased), or "".

        Used to map Application output row `type` by CDC rules.
        """
        do_type_id = (do_type_id or "").strip()
        if not do_type_id:
            return ""

        cache = getattr(self, "_do_cdc_cache", None)
        if cache is None:
            cache = {}
            setattr(self, "_do_cdc_cache", cache)
        if do_type_id in cache:
            return cache[do_type_id]

        do_dir = self.workspace_root / "ep7_datamodel" / "datamodel" / "iec61850" / "DOType"
        p = self._find_type_file(kind_dir=do_dir, type_id=do_type_id)
        if p is None:
            cache[do_type_id] = ""
            return ""

        try:
            root = ET.parse(p).getroot()
        except Exception:
            cache[do_type_id] = ""
            return ""

        ns = ""
        if isinstance(root.tag, str) and root.tag.startswith("{"):
            ns = root.tag.split("}", 1)[0][1:]

        def q(tag: str) -> str:
            return f"{{{ns}}}{tag}" if ns else tag

        do_el = root.find(f".//{q('DOType')}")
        if do_el is None:
            cache[do_type_id] = ""
            return ""

        cdc = (do_el.attrib.get("cdc") or "").strip().upper()
        cache[do_type_id] = cdc
        return cdc

    def _output_type_from_cdc(self, cdc: str) -> str:
        cdc = (cdc or "").strip().upper()
        if not cdc:
            return ""
        mapping = {
            "ACD": "STD_ACD",
            "ACT": "STD_ACT",
            "SPS": "STD_BOOLEAN",
            "CMV": "STD_Vector",
            "MV": "STD_FLOAT32",
            "WYE": "TRI_STD_Vector",
            "DEL": "TRI_STD_Vector",
            "ENS": "STD_ENUMERATED",
            "DPS": "STD_ENUMERATED",
            "INS": "STD_INT32",
            "SEQ": "TRI_STD_Vector",
            "HWYE": "TRI_ARRAY_FLOAT32",
            "HDEL": "TRI_ARRAY_FLOAT32",
        }
        return mapping.get(cdc, "")

    def _control_type_from_cdc(self, cdc: str) -> str:
        """Map IEC 61850 CDC to Application <control type>.

        Application control types in this project are basic types:
        BOOLEAN / INT32 / FLOAT32 / ENUMERATED.

        Unknown CDC leaves type empty (""), matching the "unlisted -> blank" rule.
        """
        cdc = (cdc or "").strip().upper()
        if not cdc:
            return ""
        mapping = {
            "SPC": "BOOLEAN",
            "DPC": "BOOLEAN",
            "BSC": "BOOLEAN",
            "INC": "INT32",
            "ENC": "ENUMERATED",
            "APC": "FLOAT32",
        }
        return mapping.get(cdc, "")

    def _build_control_rows_from_lntype(self, ln_type_id: str) -> list[dict[str, str]]:
        """Auto-generate Application control rows from LNType.

        Generates one row per DO whose DOType CDC is a known control CDC
        (SPC/DPC/INC/ENC/APC/BSC).
        """
        ln_type_id = (ln_type_id or "").strip()
        if not ln_type_id:
            return []

        ln_dir = self.workspace_root / "ep7_datamodel" / "datamodel" / "iec61850" / "LNodeType"
        ln_path = self._find_type_file(kind_dir=ln_dir, type_id=ln_type_id)
        if ln_path is None:
            return []

        try:
            root = ET.parse(ln_path).getroot()
        except Exception:
            return []

        ns = ""
        if isinstance(root.tag, str) and root.tag.startswith("{"):
            ns = root.tag.split("}", 1)[0][1:]

        def q(tag: str) -> str:
            return f"{{{ns}}}{tag}" if ns else tag

        ln_el = root.find(f".//{q('LNodeType')}")
        if ln_el is None:
            return []

        rows: list[dict[str, str]] = []
        for do in ln_el.findall(q("DO")):
            do_name = (do.attrib.get("name") or "").strip()
            do_type = (do.attrib.get("type") or "").strip()
            if not do_name or not do_type:
                continue

            # Domain rules consistent with output/setting generation
            dn = do_name.lower()
            if dn == "setmod":
                continue
            if dn == "beh":
                continue
            if dn.startswith("inref"):
                continue

            cdc = self._do_type_cdc(do_type)
            ctl_type = self._control_type_from_cdc(cdc)
            if not ctl_type:
                continue

            rows.append(
                {
                    "name": do_name,
                    "type": ctl_type,
                    "src": f".{do_name}",
                    "desc": "",
                }
            )

        return rows

    def _build_output_rows_from_lntype(self, ln_type_id: str) -> list[dict[str, str]]:
        ln_type_id = (ln_type_id or "").strip()
        if not ln_type_id:
            return []

        ln_dir = self.workspace_root / "ep7_datamodel" / "datamodel" / "iec61850" / "LNodeType"
        ln_path = self._find_type_file(kind_dir=ln_dir, type_id=ln_type_id)
        if ln_path is None:
            return []

        try:
            root = ET.parse(ln_path).getroot()
        except Exception:
            return []

        ns = ""
        if isinstance(root.tag, str) and root.tag.startswith("{"):
            ns = root.tag.split("}", 1)[0][1:]

        def q(tag: str) -> str:
            return f"{{{ns}}}{tag}" if ns else tag

        ln_el = root.find(f".//{q('LNodeType')}")
        if ln_el is None:
            return []

        rows: list[dict[str, str]] = []
        for do in ln_el.findall(q("DO")):
            do_name = (do.attrib.get("name") or "").strip()
            do_type = (do.attrib.get("type") or "").strip()
            if not do_name or not do_type:
                continue

            dn = do_name.lower()
            if dn == "setmod":
                continue
            if dn == "beh":
                continue
            if dn.startswith("inref"):
                continue

            has, inferred_basic = self._do_type_stmx_info(do_type)
            if not has:
                continue

            # Output `type` is generated strictly from CDC mapping rules.
            cdc = self._do_type_cdc(do_type)
            mapped_type = self._output_type_from_cdc(cdc)
            if cdc == "MV" and inferred_basic in {"INT32", "INT32U"}:
                mapped_type = f"STD_{inferred_basic}"

            rows.append(
                {
                    "name": do_name,
                    "type": mapped_type,
                    "doRef": f".{do_name}",
                    "desc": "",
                    "outPurpose": "",
                    "srvRef": "",
                    "persist": "false",
                    "faultlog": "",
                    "MaxContiguous": "0",
                    "Overlap": "1",
                }
            )
        return rows

    def _build_setting_rows_from_lntype(self, ln_type_id: str) -> list[dict[str, str]]:
        ln_type_id = (ln_type_id or "").strip()
        if not ln_type_id:
            return []

        ln_dir = self.workspace_root / "ep7_datamodel" / "datamodel" / "iec61850" / "LNodeType"
        ln_path = self._find_type_file(kind_dir=ln_dir, type_id=ln_type_id)
        if ln_path is None:
            return []

        try:
            root = ET.parse(ln_path).getroot()
        except Exception:
            return []

        ns = ""
        if isinstance(root.tag, str) and root.tag.startswith("{"):
            ns = root.tag.split("}", 1)[0][1:]

        def q(tag: str) -> str:
            return f"{{{ns}}}{tag}" if ns else tag

        ln_el = root.find(f".//{q('LNodeType')}")
        if ln_el is None:
            return []

        rows: list[dict[str, str]] = []
        for do in ln_el.findall(q("DO")):
            do_name = (do.attrib.get("name") or "").strip()
            do_type = (do.attrib.get("type") or "").strip()
            if not do_name or not do_type:
                continue

            # Exceptions / domain rules
            # - SetMod never appears in Application files
            # - InRef* DO are treated as inputs, not settings
            dn = do_name.lower()
            if dn == "setmod":
                continue
            if dn == "beh":
                continue
            if dn.startswith("inref"):
                continue

            entries = self._do_type_setting_entries(do_type)
            if not entries:
                continue

            for suffix, inferred_type in entries:
                suffix = (suffix or "").strip().lstrip(".")
                if suffix:
                    rows.append(
                        {
                            "name": f"{do_name}.{suffix}",
                            "type": inferred_type,
                            "src": f".{do_name}.{suffix}",
                            "desc": "",
                        }
                    )
                else:
                    rows.append(
                        {
                            "name": do_name,
                            "type": inferred_type,
                            "src": f".{do_name}",
                            "desc": "",
                        }
                    )
        return rows

    def _extract_ln_instance_inrefs(self, ln_path: Path) -> list[dict[str, str]]:
        doc = load_ln_instance_document(Path(ln_path))
        ln = doc.ln_elements[0]

        def local(tag: str) -> str:
            return tag.split("}", 1)[1] if tag.startswith("{") else tag

        def find_dai(doi: ET.Element, name: str) -> ET.Element | None:
            for ch in list(doi):
                if not isinstance(ch.tag, str) or local(ch.tag) != "DAI":
                    continue
                if (ch.attrib.get("name") or "").strip() == name:
                    return ch
            return None

        def read_val(dai: ET.Element | None) -> str:
            if dai is None:
                return ""
            for ch in list(dai):
                if not isinstance(ch.tag, str) or local(ch.tag) != "Val":
                    continue
                return (ch.text or "").strip()
            return ""

        out: list[dict[str, str]] = []
        seq_fallback = 1
        for doi in list(ln):
            if not isinstance(doi.tag, str) or local(doi.tag) != "DOI":
                continue
            doi_name = (doi.attrib.get("name") or "").strip()
            if not doi_name.startswith("InRef"):
                continue

            m = re.search(r"(\d+)$", doi_name)
            seq = m.group(1) if m else str(seq_fallback)
            seq_fallback += 1

            purpose_raw = read_val(find_dai(doi, "purpose"))
            purpose_clean = self._sanitize_purpose(purpose_raw)
            out.append({"doi_name": doi_name, "seq": seq, "purpose_raw": purpose_raw, "purpose_clean": purpose_clean})
        return out

    def _set_app_input_rows(self, rows: list[dict[str, str]]) -> None:
        self._app_input_rows = [dict(r) for r in rows]
        self._refresh_app_input_tv()

    def _refresh_app_input_tv(self) -> None:
        tv = self._app_tv_input
        if tv is None:
            return
        self._app_input_iid_to_row = {}
        self._clear_tv(tv)
        for idx, row in enumerate(self._app_input_rows):
            iid = str(idx)
            self._app_input_iid_to_row[iid] = row
            soft = (row.get("softlink") or "").lower() == "true"
            conf = (row.get("confpin") or "").lower() == "true"
            tv.insert(
                "",
                "end",
                iid=iid,
                values=[
                    row.get("name") or "",
                    row.get("type") or "",
                    row.get("src") or "",
                    row.get("doRef") or "",
                    "☑" if soft else "☐",
                    "☑" if conf else "☐",
                ],
            )

    def _update_app_input_tv_row(self, iid: str) -> None:
        tv = self._app_tv_input
        if tv is None:
            return
        row = self._app_input_iid_to_row.get(iid)
        if row is None:
            return
        soft = (row.get("softlink") or "").lower() == "true"
        conf = (row.get("confpin") or "").lower() == "true"
        tv.item(
            iid,
            values=[
                row.get("name") or "",
                row.get("type") or "",
                row.get("src") or "",
                row.get("doRef") or "",
                "☑" if soft else "☐",
                "☑" if conf else "☐",
            ],
        )

    def _selected_app_input_iid(self) -> str | None:
        tv = self._app_tv_input
        if tv is None:
            return None
        sel = tv.selection()
        if not sel:
            return None
        return sel[0]

    def _edit_selected_app_input(self) -> None:
        iid = self._selected_app_input_iid()
        if iid is None:
            return
        row = self._app_input_iid_to_row.get(iid)
        if row is None:
            return
        dlg = _EditApplicationInputDialog(self, title="Edit input", input_types=self._get_app_input_types(), initial=row)
        res = dlg.show()
        if not res:
            return
        row.update(res)
        self._refresh_app_input_tv()

    def _on_app_input_click(self, event: tk.Event) -> None:
        tv = self._app_tv_input
        if tv is None:
            return
        region = tv.identify("region", event.x, event.y)
        if region != "cell":
            return
        col = tv.identify_column(event.x)
        row_iid = tv.identify_row(event.y)
        if not row_iid:
            return
        # Ensure selection follows click
        try:
            tv.selection_set(row_iid)
        except Exception:
            pass

        # If we're already editing this type cell, toggle dropdown open/close.
        if col == "#2":
            if (
                isinstance(self._app_input_inline, ttk.Combobox)
                and self._app_input_inline_iid == row_iid
                and self._app_input_inline_col == col
            ):
                self._combobox_toggle_posted(self._app_input_inline)
                return "break"

            # Not editing yet: single click should open the dropdown.
            try:
                tv.after_idle(lambda: self._begin_app_input_inline_edit(row_iid, col, mode="type_click"))
            except Exception:
                self._begin_app_input_inline_edit(row_iid, col, mode="type_click")
            return "break"

        # Checkbox columns
        if col in {"#5", "#6"}:
            row = self._app_input_iid_to_row.get(row_iid)
            if row is None:
                return
            key = "softlink" if col == "#5" else "confpin"
            cur = (row.get(key) or "").lower() == "true"
            row[key] = "" if cur else "true"
            self._update_app_input_tv_row(row_iid)
            self._end_app_input_inline_editor(commit=False)
            return "break"

        # Other columns: single click only selects; double click edits.
        return

    def _on_app_input_double_click(self, event: tk.Event) -> str:
        tv = self._app_tv_input
        if tv is None:
            return "break"
        region = tv.identify("region", event.x, event.y)
        if region != "cell":
            return "break"
        col = tv.identify_column(event.x)
        row_iid = tv.identify_row(event.y)
        if not row_iid:
            return "break"
        try:
            tv.selection_set(row_iid)
        except Exception:
            pass

        # Double click edits: name/src/doRef as text; type as typing mode.
        if col in {"#1", "#3", "#4"}:
            try:
                tv.after_idle(lambda: self._begin_app_input_inline_edit(row_iid, col, mode="text"))
            except Exception:
                self._begin_app_input_inline_edit(row_iid, col, mode="text")
        elif col == "#2":
            try:
                tv.after_idle(lambda: self._begin_app_input_inline_edit(row_iid, col, mode="type_input"))
            except Exception:
                self._begin_app_input_inline_edit(row_iid, col, mode="type_input")
        return "break"

    def _on_app_setting_click(self, event: tk.Event) -> str | None:
        tv = self._app_tv_setting
        if tv is None:
            return None
        region = tv.identify("region", event.x, event.y)
        if region != "cell":
            return None
        col = tv.identify_column(event.x)
        row_iid = tv.identify_row(event.y)
        if not row_iid:
            return None
        try:
            tv.selection_set(row_iid)
        except Exception:
            pass

        # type column: single click toggles dropdown
        if col == "#2":
            if (
                isinstance(self._app_setting_inline, ttk.Combobox)
                and self._app_setting_inline_iid == row_iid
                and self._app_setting_inline_col == col
            ):
                self._combobox_toggle_posted(self._app_setting_inline)
                return "break"
            try:
                tv.after_idle(lambda: self._begin_app_setting_inline_edit(row_iid, col, mode="type_click"))
            except Exception:
                self._begin_app_setting_inline_edit(row_iid, col, mode="type_click")
            return "break"

        # other columns: single click selects only; double click edits.
        return None

    def _on_app_setting_double_click(self, event: tk.Event) -> str:
        tv = self._app_tv_setting
        if tv is None:
            return "break"
        region = tv.identify("region", event.x, event.y)
        if region != "cell":
            return "break"
        col = tv.identify_column(event.x)
        row_iid = tv.identify_row(event.y)
        if not row_iid:
            return "break"
        try:
            tv.selection_set(row_iid)
        except Exception:
            pass

        # Double click edits: name/src/desc as text; type as typing mode.
        if col in {"#1", "#3", "#4"}:
            try:
                tv.after_idle(lambda: self._begin_app_setting_inline_edit(row_iid, col, mode="text"))
            except Exception:
                self._begin_app_setting_inline_edit(row_iid, col, mode="text")
        elif col == "#2":
            try:
                tv.after_idle(lambda: self._begin_app_setting_inline_edit(row_iid, col, mode="type_input"))
            except Exception:
                self._begin_app_setting_inline_edit(row_iid, col, mode="type_input")
        return "break"

    def _on_app_output_click(self, event: tk.Event) -> str | None:
        tv = self._app_tv_output
        if tv is None:
            return None
        region = tv.identify("region", event.x, event.y)
        if region != "cell":
            return None
        col = tv.identify_column(event.x)
        row_iid = tv.identify_row(event.y)
        if not row_iid:
            return None
        try:
            tv.selection_set(row_iid)
        except Exception:
            pass

        # persist/faultlog columns: single click toggles checkbox
        try:
            cols = list(tv["columns"])
            persist_col = f"#{cols.index('persist') + 1}"
            fault_col = f"#{cols.index('faultlog') + 1}"
            type_col = f"#{cols.index('type') + 1}"
        except Exception:
            persist_col = "#6"
            fault_col = "#7"
            type_col = "#2"

        if col in {persist_col, fault_col}:
            try:
                idx = int(row_iid)
            except Exception:
                return "break"
            if idx < 0 or idx >= len(self._app_output_rows):
                return "break"
            row = self._app_output_rows[idx]
            key = "persist" if col == persist_col else "faultlog"
            cur = (row.get(key) or "").lower() == "true"
            if key == "persist":
                row[key] = "false" if cur else "true"
            else:
                row[key] = "" if cur else "true"
            self._update_simple_app_tv_row("output", row_iid)
            self._end_app_output_inline_editor(commit=False)
            return "break"

        if col == type_col:
            if (
                isinstance(self._app_output_inline, ttk.Combobox)
                and self._app_output_inline_iid == row_iid
                and self._app_output_inline_col == col
            ):
                self._combobox_toggle_posted(self._app_output_inline)
                return "break"
            try:
                tv.after_idle(lambda: self._begin_app_output_inline_edit(row_iid, col, mode="type_click"))
            except Exception:
                self._begin_app_output_inline_edit(row_iid, col, mode="type_click")
            return "break"

        return None

    def _on_app_output_double_click(self, event: tk.Event) -> str:
        tv = self._app_tv_output
        if tv is None:
            return "break"
        region = tv.identify("region", event.x, event.y)
        if region != "cell":
            return "break"
        col = tv.identify_column(event.x)
        row_iid = tv.identify_row(event.y)
        if not row_iid:
            return "break"
        try:
            tv.selection_set(row_iid)
        except Exception:
            pass

        # Text columns editable; type as typing mode. (persist/faultlog are click-only)
        key: str | None = None
        try:
            cols = list(tv["columns"])
            idx_col = int(col.lstrip("#")) - 1
            if 0 <= idx_col < len(cols):
                key = cols[idx_col]
        except Exception:
            key = None

        if key in {"name", "doRef", "desc", "MaxContiguous", "Overlap"}:
            try:
                tv.after_idle(lambda: self._begin_app_output_inline_edit(row_iid, col, mode="text"))
            except Exception:
                self._begin_app_output_inline_edit(row_iid, col, mode="text")
        elif key == "type":
            try:
                tv.after_idle(lambda: self._begin_app_output_inline_edit(row_iid, col, mode="type_input"))
            except Exception:
                self._begin_app_output_inline_edit(row_iid, col, mode="type_input")
        return "break"

    def _on_app_conf_click(self, event: tk.Event) -> str | None:
        tv = self._app_tv_conf
        if tv is None:
            return None
        region = tv.identify("region", event.x, event.y)
        if region != "cell":
            return None
        col = tv.identify_column(event.x)
        row_iid = tv.identify_row(event.y)
        if not row_iid:
            return None
        try:
            tv.selection_set(row_iid)
        except Exception:
            pass

        if col == "#2":
            if (
                isinstance(self._app_conf_inline, ttk.Combobox)
                and self._app_conf_inline_iid == row_iid
                and self._app_conf_inline_col == col
            ):
                self._combobox_toggle_posted(self._app_conf_inline)
                return "break"
            try:
                tv.after_idle(lambda: self._begin_app_conf_inline_edit(row_iid, col, mode="type_click"))
            except Exception:
                self._begin_app_conf_inline_edit(row_iid, col, mode="type_click")
            return "break"

        return None

    def _on_app_conf_double_click(self, event: tk.Event) -> str:
        tv = self._app_tv_conf
        if tv is None:
            return "break"
        region = tv.identify("region", event.x, event.y)
        if region != "cell":
            return "break"
        col = tv.identify_column(event.x)
        row_iid = tv.identify_row(event.y)
        if not row_iid:
            return "break"
        try:
            tv.selection_set(row_iid)
        except Exception:
            pass

        if col in {"#1", "#3", "#4"}:
            try:
                tv.after_idle(lambda: self._begin_app_conf_inline_edit(row_iid, col, mode="text"))
            except Exception:
                self._begin_app_conf_inline_edit(row_iid, col, mode="text")
        elif col == "#2":
            try:
                tv.after_idle(lambda: self._begin_app_conf_inline_edit(row_iid, col, mode="type_input"))
            except Exception:
                self._begin_app_conf_inline_edit(row_iid, col, mode="type_input")
        return "break"

    def _on_app_control_click(self, event: tk.Event) -> str | None:
        tv = self._app_tv_control
        if tv is None:
            return None
        region = tv.identify("region", event.x, event.y)
        if region != "cell":
            return None
        col = tv.identify_column(event.x)
        row_iid = tv.identify_row(event.y)
        if not row_iid:
            return None
        try:
            tv.selection_set(row_iid)
        except Exception:
            pass

        if col == "#2":
            if (
                isinstance(self._app_control_inline, ttk.Combobox)
                and self._app_control_inline_iid == row_iid
                and self._app_control_inline_col == col
            ):
                self._combobox_toggle_posted(self._app_control_inline)
                return "break"
            try:
                tv.after_idle(lambda: self._begin_app_control_inline_edit(row_iid, col, mode="type_click"))
            except Exception:
                self._begin_app_control_inline_edit(row_iid, col, mode="type_click")
            return "break"

        return None

    def _on_app_control_double_click(self, event: tk.Event) -> str:
        tv = self._app_tv_control
        if tv is None:
            return "break"
        region = tv.identify("region", event.x, event.y)
        if region != "cell":
            return "break"
        col = tv.identify_column(event.x)
        row_iid = tv.identify_row(event.y)
        if not row_iid:
            return "break"
        try:
            tv.selection_set(row_iid)
        except Exception:
            pass

        if col in {"#1", "#3", "#4"}:
            try:
                tv.after_idle(lambda: self._begin_app_control_inline_edit(row_iid, col, mode="text"))
            except Exception:
                self._begin_app_control_inline_edit(row_iid, col, mode="text")
        elif col == "#2":
            try:
                tv.after_idle(lambda: self._begin_app_control_inline_edit(row_iid, col, mode="type_input"))
            except Exception:
                self._begin_app_control_inline_edit(row_iid, col, mode="type_input")
        return "break"

    def _combobox_is_posted(self, cb: ttk.Combobox) -> bool:
        try:
            popdown = cb.tk.call("ttk::combobox::PopdownWindow", str(cb))
            return bool(int(cb.tk.call("winfo", "ismapped", popdown)))
        except Exception:
            return False

    def _combobox_post(self, cb: ttk.Combobox) -> None:
        try:
            cb.tk.call("ttk::combobox::Post", str(cb))
        except Exception:
            try:
                cb.event_generate("<Alt-Down>")
            except Exception:
                pass

    def _combobox_unpost(self, cb: ttk.Combobox) -> None:
        try:
            cb.tk.call("ttk::combobox::Unpost", str(cb))
        except Exception:
            try:
                cb.event_generate("<Escape>")
            except Exception:
                pass

    def _combobox_toggle_posted(self, cb: ttk.Combobox) -> None:
        if self._combobox_is_posted(cb):
            self._combobox_unpost(cb)
        else:
            self._combobox_post(cb)

    def _init_app_table_ui(self, table: str, tv: ttk.Treeview | None) -> None:
        if tv is None:
            return
        # Context menu (right click)
        tv.bind("<Button-3>", lambda e, t=table: self._show_app_table_context_menu(e, t))

        # Toolbar buttons live in the parent wrapper (created by _make_tv)
        wrap = tv.master
        tb = getattr(wrap, "_toolbar", None)
        btn = getattr(wrap, "_btn", None)
        if tb is None or btn is None:
            return

        btn("Add", lambda t=table: self._app_table_add(t))
        btn("Insert", lambda t=table: self._app_table_insert(t), padx=(6, 0))
        btn("Copy", lambda t=table: self._app_table_copy(t), padx=(6, 0))
        btn("Cut", lambda t=table: self._app_table_cut(t), padx=(6, 0))
        btn("Paste", lambda t=table: self._app_table_paste(t), padx=(6, 0))
        btn("Delete", lambda t=table: self._app_table_delete(t), padx=(6, 0))
        btn("Up", lambda t=table: self._app_table_move(t, -1), padx=(18, 0))
        btn("Down", lambda t=table: self._app_table_move(t, 1), padx=(6, 0))

    def _show_app_table_context_menu(self, event: tk.Event, table: str) -> None:
        tv = self._app_table_tv(table)
        if tv is None:
            return
        iid = tv.identify_row(event.y)
        if iid:
            try:
                tv.selection_set(iid)
            except Exception:
                pass

        if self._app_ctx_menu is None:
            self._app_ctx_menu = tk.Menu(self, tearoff=False)

        m = self._app_ctx_menu
        try:
            m.delete(0, "end")
        except Exception:
            pass

        self._app_ctx_table = table

        m.add_command(label="Add", command=lambda: self._app_table_add(self._app_ctx_table or ""))
        m.add_command(label="Insert", command=lambda: self._app_table_insert(self._app_ctx_table or ""))

        if table == "input":
            m.add_command(label="Add shared setting", command=self._app_add_shared_setting_from_input)

        if table == "setting":
            m.add_command(label="Convert to conf", command=self._app_convert_setting_to_conf)
        elif table == "conf":
            m.add_command(label="Convert to setting", command=self._app_convert_conf_to_setting)

        m.add_separator()
        m.add_command(label="Copy", command=lambda: self._app_table_copy(self._app_ctx_table or ""))
        m.add_command(label="Cut", command=lambda: self._app_table_cut(self._app_ctx_table or ""))
        m.add_command(label="Paste", command=lambda: self._app_table_paste(self._app_ctx_table or ""))
        m.add_command(label="Delete", command=lambda: self._app_table_delete(self._app_ctx_table or ""))
        m.add_separator()
        m.add_command(label="Up", command=lambda: self._app_table_move(self._app_ctx_table or "", -1))
        m.add_command(label="Down", command=lambda: self._app_table_move(self._app_ctx_table or "", 1))

        # Enable/disable items
        idx = self._app_table_selected_index(table)
        has_sel = idx is not None
        can_paste = bool(self._app_clipboard.get(table))
        can_up = has_sel and idx is not None and idx > 0
        rows = self._app_table_rows(table)
        can_down = has_sel and idx is not None and idx < (len(rows) - 1)
        can_add_shared = False
        if table == "input" and has_sel and idx is not None and 0 <= idx < len(self._app_input_rows):
            can_add_shared = (self._app_input_rows[idx].get("confpin") or "").lower() == "true"

        for label in ("Copy", "Cut", "Delete"):
            try:
                m.entryconfigure(label, state=("normal" if has_sel else "disabled"))
            except Exception:
                pass
        try:
            m.entryconfigure("Paste", state=("normal" if can_paste else "disabled"))
        except Exception:
            pass
        if table == "setting":
            try:
                m.entryconfigure("Convert to conf", state=("normal" if has_sel else "disabled"))
            except Exception:
                pass
        if table == "conf":
            try:
                m.entryconfigure("Convert to setting", state=("normal" if has_sel else "disabled"))
            except Exception:
                pass
        if table == "input":
            try:
                m.entryconfigure("Add shared setting", state=("normal" if can_add_shared else "disabled"))
            except Exception:
                pass
        try:
            m.entryconfigure("Up", state=("normal" if can_up else "disabled"))
            m.entryconfigure("Down", state=("normal" if can_down else "disabled"))
        except Exception:
            pass
        try:
            self._app_ctx_menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                self._app_ctx_menu.grab_release()
            except Exception:
                pass

    def _app_add_shared_setting_from_input(self) -> None:
        idx = self._app_table_selected_index("input")
        if idx is None or idx < 0 or idx >= len(self._app_input_rows):
            return
        in_row = self._app_input_rows[idx]
        if (in_row.get("confpin") or "").lower() != "true":
            return

        base_name = (in_row.get("name") or "").strip()
        if not base_name:
            messagebox.showerror("Missing", "name is required", parent=self)
            return

        new_row = {
            "name": base_name,
            "type": (in_row.get("type") or "").strip(),
            "src": "",
            "desc": "",
        }

        setting_rows = list(self._app_setting_rows)
        existing_idx = None
        for i, r in enumerate(setting_rows):
            if (r.get("name") or "").strip() == base_name:
                existing_idx = i
                break

        insert_or_select_idx: int | None = None
        if existing_idx is None:
            setting_rows.append(dict(new_row))
            insert_or_select_idx = len(setting_rows) - 1
        else:
            dlg = _OverwriteDuplicateCancelDialog(
                self,
                title="Setting exists",
                message=(
                    f"A setting named '{base_name}' already exists.\n\n"
                    "Choose what to do:"
                ),
            )
            choice = dlg.show()
            if choice is None:
                return
            if choice == "duplicate":
                # Duplicate: append a numeric suffix until unique.
                n = 2
                while True:
                    cand = f"{base_name}_{n}"
                    if not any(((rr.get("name") or "").strip() == cand) for rr in setting_rows):
                        new_row["name"] = cand
                        break
                    n += 1
                setting_rows.append(dict(new_row))
                insert_or_select_idx = len(setting_rows) - 1
            elif choice == "overwrite":
                # Overwrite: replace the first matching row.
                setting_rows[existing_idx] = dict(new_row)
                insert_or_select_idx = existing_idx
            else:
                return

        self._app_table_set_rows("setting", setting_rows)

        tv_set = self._app_table_tv("setting")
        if tv_set is not None and insert_or_select_idx is not None:
            try:
                tv_set.selection_set(str(insert_or_select_idx))
            except Exception:
                pass

    def _app_convert_setting_to_conf(self) -> None:
        idx = self._app_table_selected_index("setting")
        if idx is None or idx < 0 or idx >= len(self._app_setting_rows):
            return
        row = dict(self._app_setting_rows.pop(idx))

        conf_rows = list(self._app_conf_rows)
        conf_rows.append(row)

        self._app_table_set_rows("setting", list(self._app_setting_rows))
        self._app_table_set_rows("conf", conf_rows)

        tv_conf = self._app_table_tv("conf")
        if tv_conf is not None:
            try:
                tv_conf.selection_set(str(len(conf_rows) - 1))
            except Exception:
                pass

    def _app_convert_conf_to_setting(self) -> None:
        idx = self._app_table_selected_index("conf")
        if idx is None or idx < 0 or idx >= len(self._app_conf_rows):
            return
        row = dict(self._app_conf_rows.pop(idx))
        name = (row.get("name") or "").strip()
        row["src"] = f".{name}" if name else ""

        setting_rows = list(self._app_setting_rows)
        setting_rows.append(row)

        self._app_table_set_rows("conf", list(self._app_conf_rows))
        self._app_table_set_rows("setting", setting_rows)

        tv_set = self._app_table_tv("setting")
        if tv_set is not None:
            try:
                tv_set.selection_set(str(len(setting_rows) - 1))
            except Exception:
                pass

    def _app_table_tv(self, table: str) -> ttk.Treeview | None:
        return {
            "input": self._app_tv_input,
            "setting": self._app_tv_setting,
            "output": self._app_tv_output,
            "conf": self._app_tv_conf,
            "control": self._app_tv_control,
        }.get(table)

    def _app_table_rows(self, table: str) -> list[dict[str, str]]:
        return {
            "input": self._app_input_rows,
            "setting": self._app_setting_rows,
            "output": self._app_output_rows,
            "conf": self._app_conf_rows,
            "control": self._app_control_rows,
        }.get(table, [])

    def _app_table_set_rows(self, table: str, rows: list[dict[str, str]]) -> None:
        if table == "input":
            self._set_app_input_rows(rows)
        elif table == "setting":
            self._app_setting_rows = [dict(r) for r in rows]
            self._refresh_simple_app_tv("setting")
        elif table == "output":
            self._app_output_rows = [dict(r) for r in rows]
            self._refresh_simple_app_tv("output")
        elif table == "conf":
            self._app_conf_rows = [dict(r) for r in rows]
            self._refresh_simple_app_tv("conf")
        elif table == "control":
            self._app_control_rows = [dict(r) for r in rows]
            self._refresh_simple_app_tv("control")

    def _refresh_simple_app_tv(self, table: str) -> None:
        tv = self._app_table_tv(table)
        if tv is None:
            return
        self._clear_tv(tv)
        rows = self._app_table_rows(table)
        cols = list(tv["columns"])
        for idx, row in enumerate(rows):
            values: list[str] = []
            for c in cols:
                if table == "output" and c in {"persist", "faultlog"}:
                    on = (row.get(c) or "").lower() == "true"
                    values.append("☑" if on else "☐")
                else:
                    values.append(row.get(c) or "")
            tv.insert("", "end", iid=str(idx), values=values)

    def _update_simple_app_tv_row(self, table: str, iid: str) -> None:
        tv = self._app_table_tv(table)
        if tv is None:
            return
        try:
            idx = int(iid)
        except Exception:
            return
        rows = self._app_table_rows(table)
        if idx < 0 or idx >= len(rows):
            return
        row = rows[idx]
        cols = list(tv["columns"])
        values: list[str] = []
        for c in cols:
            if table == "output" and c in {"persist", "faultlog"}:
                on = (row.get(c) or "").lower() == "true"
                values.append("☑" if on else "☐")
            else:
                values.append(row.get(c) or "")
        tv.item(iid, values=values)

    def _app_table_selected_index(self, table: str) -> int | None:
        tv = self._app_table_tv(table)
        if tv is None:
            return None
        sel = tv.selection()
        if not sel:
            return None
        try:
            return int(sel[0])
        except Exception:
            return None

    def _app_table_blank_row(self, table: str) -> dict[str, str]:
        if table == "input":
            return {
                "name": "",
                "type": "",
                "desc": "",
                "src": "",
                "doRef": "",
                "softlink": "",
                "confpin": "",
            }
        if table == "output":
            return {
                "name": "",
                "type": "",
                "desc": "",
                "outPurpose": "",
                "srvRef": "",
                "persist": "false",
                "doRef": "",
                "MaxContiguous": "0",
                "Overlap": "1",
                "faultlog": "",
            }
        # setting / conf / control
        return {
            "name": "",
            "type": "",
            "src": "",
            "desc": "",
        }

    def _app_table_add(self, table: str) -> None:
        if not table:
            return
        rows = self._app_table_rows(table)
        insert_at = len(rows)
        rows.insert(insert_at, self._app_table_blank_row(table))
        self._app_table_set_rows(table, rows)
        tv = self._app_table_tv(table)
        if tv is not None:
            try:
                tv.selection_set(str(insert_at))
            except Exception:
                pass

    def _app_table_insert(self, table: str) -> None:
        if not table:
            return
        rows = self._app_table_rows(table)
        idx = self._app_table_selected_index(table)
        insert_at = (idx + 1) if idx is not None else len(rows)
        insert_at = max(0, min(insert_at, len(rows)))
        rows.insert(insert_at, self._app_table_blank_row(table))
        self._app_table_set_rows(table, rows)
        tv = self._app_table_tv(table)
        if tv is not None:
            try:
                tv.selection_set(str(insert_at))
            except Exception:
                pass

    def _app_table_copy(self, table: str) -> None:
        if not table:
            return
        idx = self._app_table_selected_index(table)
        rows = self._app_table_rows(table)
        if idx is None or idx < 0 or idx >= len(rows):
            return
        self._app_clipboard[table] = dict(rows[idx])

    def _app_table_cut(self, table: str) -> None:
        self._app_table_copy(table)
        self._app_table_delete(table)

    def _app_table_paste(self, table: str) -> None:
        if not table:
            return
        clip = self._app_clipboard.get(table)
        if not clip:
            return
        rows = self._app_table_rows(table)
        idx = self._app_table_selected_index(table)
        insert_at = (idx + 1) if idx is not None else len(rows)
        insert_at = max(0, min(insert_at, len(rows)))
        rows.insert(insert_at, dict(clip))
        self._app_table_set_rows(table, rows)
        tv = self._app_table_tv(table)
        if tv is not None:
            try:
                tv.selection_set(str(insert_at))
            except Exception:
                pass

    def _app_table_delete(self, table: str) -> None:
        if not table:
            return
        idx = self._app_table_selected_index(table)
        rows = self._app_table_rows(table)
        if idx is None or idx < 0 or idx >= len(rows):
            return
        rows.pop(idx)
        self._app_table_set_rows(table, rows)
        tv = self._app_table_tv(table)
        if tv is not None and rows:
            sel = min(idx, len(rows) - 1)
            try:
                tv.selection_set(str(sel))
            except Exception:
                pass

    def _app_table_move(self, table: str, delta: int) -> None:
        if not table:
            return
        idx = self._app_table_selected_index(table)
        rows = self._app_table_rows(table)
        if idx is None:
            return
        j = idx + delta
        if j < 0 or j >= len(rows):
            return
        rows[idx], rows[j] = rows[j], rows[idx]
        self._app_table_set_rows(table, rows)
        tv = self._app_table_tv(table)
        if tv is not None:
            try:
                tv.selection_set(str(j))
            except Exception:
                pass

    def _begin_app_input_inline_edit(self, iid: str, col: str, mode: str = "text") -> None:
        tv = self._app_tv_input
        if tv is None:
            return
        row = self._app_input_iid_to_row.get(iid)
        if row is None:
            return

        # If other app inline editors are open, commit them.
        self._end_app_setting_inline_editor(commit=True)

        # Commit any existing editor
        self._end_app_input_inline_editor(commit=True)

        # Map Treeview column -> row key
        key_by_col = {"#1": "name", "#2": "type", "#3": "src", "#4": "doRef"}
        key = key_by_col.get(col)
        if key is None:
            return

        bbox = tv.bbox(iid, col)
        if not bbox:
            return
        x, y, w, h = bbox
        value = row.get(key) or ""

        if key == "type":
            all_values = list(self._get_app_input_types())
            cb = ttk.Combobox(tv, values=all_values, width=max(10, int(w / 7)))
            cb.set(value)

            # Click-mode: readonly for easier single-click selection.
            try:
                if mode == "type_click" and all_values:
                    cb.configure(state="readonly")
                elif mode == "type_input":
                    cb.configure(state="normal")
            except Exception:
                pass

            def is_subsequence(q: str, s: str) -> bool:
                qi = 0
                if not q:
                    return True
                for ch in s:
                    if qi < len(q) and ch == q[qi]:
                        qi += 1
                        if qi == len(q):
                            return True
                return qi == len(q)

            def rank_matches(q: str, items: list[str]) -> list[str]:
                ql = q.lower().strip()
                if not ql:
                    return items
                starts: list[str] = []
                contains: list[str] = []
                subseq: list[str] = []
                for it in items:
                    il = it.lower()
                    if il.startswith(ql):
                        starts.append(it)
                    elif ql in il:
                        contains.append(it)
                    elif is_subsequence(ql, il):
                        subseq.append(it)
                # Keep results stable-ish
                return starts + contains + subseq

            def on_key_release(_e=None) -> None:
                try:
                    q = cb.get() or ""
                    cb.configure(values=rank_matches(q, all_values))
                    # Keep dropdown open while typing to show filtered list.
                    if mode == "type_input":
                        self._combobox_post(cb)
                except Exception:
                    pass

            def commit_and_close(_e=None) -> None:
                self._end_app_input_inline_editor(commit=True)

            cb.bind("<<ComboboxSelected>>", commit_and_close)
            cb.bind("<Return>", commit_and_close)
            cb.bind("<Escape>", lambda _e: self._end_app_input_inline_editor(commit=False))
            cb.bind("<FocusOut>", lambda _e: self._end_app_input_inline_editor(commit=True))
            cb.bind("<KeyRelease>", on_key_release)

            if mode == "type_click":
                # Single click toggles dropdown open/close.
                cb.bind(
                    "<Button-1>",
                    lambda _e: (self._combobox_toggle_posted(cb), "break")[1],
                )
                # Double click switches into typing mode + fuzzy search.
                cb.bind(
                    "<Double-Button-1>",
                    lambda _e, _iid=iid, _col=col: (self._begin_app_input_inline_edit(_iid, _col, mode="type_input"), "break")[1],
                )
            cb.place(x=x, y=y, width=w, height=h)
            cb.focus_set()
            self._app_input_inline = cb

            # For single-click: open immediately. For double-click typing: open immediately too.
            if mode in {"type_click", "type_input"}:
                try:
                    tv.after_idle(lambda: self._combobox_post(cb))
                except Exception:
                    self._combobox_post(cb)

            if mode == "type_input":
                try:
                    cb.focus_set()
                    cb.icursor(tk.END)
                    cb.selection_range(0, tk.END)
                except Exception:
                    pass
        else:
            ent = ttk.Entry(tv)
            ent.insert(0, value)

            def commit_and_close(_e=None) -> None:
                self._end_app_input_inline_editor(commit=True)

            ent.bind("<Return>", commit_and_close)
            ent.bind("<Escape>", lambda _e: self._end_app_input_inline_editor(commit=False))
            ent.bind("<FocusOut>", lambda _e: self._end_app_input_inline_editor(commit=True))
            ent.place(x=x, y=y, width=w, height=h)
            ent.focus_set()
            ent.selection_range(0, tk.END)
            self._app_input_inline = ent

        self._app_input_inline_iid = iid
        self._app_input_inline_col = col

    def _begin_app_setting_inline_edit(self, iid: str, col: str, mode: str = "text") -> None:
        tv = self._app_tv_setting
        if tv is None:
            return
        try:
            idx = int(iid)
        except Exception:
            return
        if idx < 0 or idx >= len(self._app_setting_rows):
            return
        row = self._app_setting_rows[idx]

        # If other app inline editors are open, commit them.
        self._end_app_input_inline_editor(commit=True)
        self._end_app_output_inline_editor(commit=True)
        self._end_app_conf_inline_editor(commit=True)
        self._end_app_control_inline_editor(commit=True)

        # Commit any existing setting editor
        self._end_app_setting_inline_editor(commit=True)

        key_by_col = {"#1": "name", "#2": "type", "#3": "src", "#4": "desc"}
        key = key_by_col.get(col)
        if key is None:
            return

        bbox = tv.bbox(iid, col)
        if not bbox:
            return
        x, y, w, h = bbox
        value = row.get(key) or ""

        if key == "type":
            all_values = list(self._get_app_setting_types())
            cb = ttk.Combobox(tv, values=all_values, width=max(10, int(w / 7)))
            cb.set(value)

            try:
                if mode == "type_click" and all_values:
                    cb.configure(state="readonly")
                elif mode == "type_input":
                    cb.configure(state="normal")
            except Exception:
                pass

            def is_subsequence(q: str, s: str) -> bool:
                qi = 0
                if not q:
                    return True
                for ch in s:
                    if qi < len(q) and ch == q[qi]:
                        qi += 1
                        if qi == len(q):
                            return True
                return qi == len(q)

            def rank_matches(q: str, items: list[str]) -> list[str]:
                ql = q.lower().strip()
                if not ql:
                    return items
                starts: list[str] = []
                contains: list[str] = []
                subseq: list[str] = []
                for it in items:
                    il = it.lower()
                    if il.startswith(ql):
                        starts.append(it)
                    elif ql in il:
                        contains.append(it)
                    elif is_subsequence(ql, il):
                        subseq.append(it)
                return starts + contains + subseq

            def on_key_release(_e=None) -> None:
                try:
                    q = cb.get() or ""
                    cb.configure(values=rank_matches(q, all_values))
                    if mode == "type_input":
                        self._combobox_post(cb)
                except Exception:
                    pass

            def commit_and_close(_e=None) -> None:
                self._end_app_setting_inline_editor(commit=True)

            cb.bind("<<ComboboxSelected>>", commit_and_close)
            cb.bind("<Return>", commit_and_close)
            cb.bind("<Escape>", lambda _e: self._end_app_setting_inline_editor(commit=False))
            cb.bind("<FocusOut>", lambda _e: self._end_app_setting_inline_editor(commit=True))
            cb.bind("<KeyRelease>", on_key_release)

            if mode == "type_click":
                cb.bind("<Button-1>", lambda _e: (self._combobox_toggle_posted(cb), "break")[1])
                cb.bind(
                    "<Double-Button-1>",
                    lambda _e, _iid=iid, _col=col: (self._begin_app_setting_inline_edit(_iid, _col, mode="type_input"), "break")[1],
                )

            cb.place(x=x, y=y, width=w, height=h)
            cb.focus_set()
            self._app_setting_inline = cb

            if mode in {"type_click", "type_input"}:
                try:
                    tv.after_idle(lambda: self._combobox_post(cb))
                except Exception:
                    self._combobox_post(cb)

            if mode == "type_input":
                try:
                    cb.focus_set()
                    cb.icursor(tk.END)
                    cb.selection_range(0, tk.END)
                except Exception:
                    pass
        else:
            ent = ttk.Entry(tv)
            ent.insert(0, value)

            def commit_and_close(_e=None) -> None:
                self._end_app_setting_inline_editor(commit=True)

            ent.bind("<Return>", commit_and_close)
            ent.bind("<Escape>", lambda _e: self._end_app_setting_inline_editor(commit=False))
            ent.bind("<FocusOut>", lambda _e: self._end_app_setting_inline_editor(commit=True))
            ent.place(x=x, y=y, width=w, height=h)
            ent.focus_set()
            ent.selection_range(0, tk.END)
            self._app_setting_inline = ent

        self._app_setting_inline_iid = iid
        self._app_setting_inline_col = col

    def _begin_app_output_inline_edit(self, iid: str, col: str, mode: str = "text") -> None:
        tv = self._app_tv_output
        if tv is None:
            return
        try:
            idx = int(iid)
        except Exception:
            return
        if idx < 0 or idx >= len(self._app_output_rows):
            return
        row = self._app_output_rows[idx]

        self._end_app_input_inline_editor(commit=True)
        self._end_app_setting_inline_editor(commit=True)
        self._end_app_conf_inline_editor(commit=True)
        self._end_app_control_inline_editor(commit=True)
        self._end_app_output_inline_editor(commit=True)

        cols = list(tv["columns"])
        key: str | None = None
        try:
            idx_col = int(col.lstrip("#")) - 1
            if 0 <= idx_col < len(cols):
                key = cols[idx_col]
        except Exception:
            key = None

        if key not in {"name", "type", "doRef", "desc", "MaxContiguous", "Overlap"}:
            return

        bbox = tv.bbox(iid, col)
        if not bbox:
            return
        x, y, w, h = bbox
        value = row.get(key) or ""

        if key == "type":
            all_values = list(self._get_app_output_types())
            cb = ttk.Combobox(tv, values=all_values, width=max(10, int(w / 7)))
            cb.set(value)
            try:
                if mode == "type_click" and all_values:
                    cb.configure(state="readonly")
                elif mode == "type_input":
                    cb.configure(state="normal")
            except Exception:
                pass

            def is_subsequence(q: str, s: str) -> bool:
                qi = 0
                if not q:
                    return True
                for ch in s:
                    if qi < len(q) and ch == q[qi]:
                        qi += 1
                        if qi == len(q):
                            return True
                return qi == len(q)

            def rank_matches(q: str, items: list[str]) -> list[str]:
                ql = q.lower().strip()
                if not ql:
                    return items
                starts: list[str] = []
                contains: list[str] = []
                subseq: list[str] = []
                for it in items:
                    il = it.lower()
                    if il.startswith(ql):
                        starts.append(it)
                    elif ql in il:
                        contains.append(it)
                    elif is_subsequence(ql, il):
                        subseq.append(it)
                return starts + contains + subseq

            def on_key_release(_e=None) -> None:
                try:
                    q = cb.get() or ""
                    cb.configure(values=rank_matches(q, all_values))
                    if mode == "type_input":
                        self._combobox_post(cb)
                except Exception:
                    pass

            def commit_and_close(_e=None) -> None:
                self._end_app_output_inline_editor(commit=True)

            cb.bind("<<ComboboxSelected>>", commit_and_close)
            cb.bind("<Return>", commit_and_close)
            cb.bind("<Escape>", lambda _e: self._end_app_output_inline_editor(commit=False))
            cb.bind("<FocusOut>", lambda _e: self._end_app_output_inline_editor(commit=True))
            cb.bind("<KeyRelease>", on_key_release)

            if mode == "type_click":
                cb.bind("<Button-1>", lambda _e: (self._combobox_toggle_posted(cb), "break")[1])
                cb.bind(
                    "<Double-Button-1>",
                    lambda _e, _iid=iid, _col=col: (self._begin_app_output_inline_edit(_iid, _col, mode="type_input"), "break")[1],
                )

            cb.place(x=x, y=y, width=w, height=h)
            cb.focus_set()
            self._app_output_inline = cb

            try:
                tv.after_idle(lambda: self._combobox_post(cb))
            except Exception:
                self._combobox_post(cb)

            if mode == "type_input":
                try:
                    cb.icursor(tk.END)
                    cb.selection_range(0, tk.END)
                except Exception:
                    pass
        else:
            ent = ttk.Entry(tv)
            ent.insert(0, value)

            def commit_and_close(_e=None) -> None:
                self._end_app_output_inline_editor(commit=True)

            ent.bind("<Return>", commit_and_close)
            ent.bind("<Escape>", lambda _e: self._end_app_output_inline_editor(commit=False))
            ent.bind("<FocusOut>", lambda _e: self._end_app_output_inline_editor(commit=True))
            ent.place(x=x, y=y, width=w, height=h)
            ent.focus_set()
            ent.selection_range(0, tk.END)
            self._app_output_inline = ent

        self._app_output_inline_iid = iid
        self._app_output_inline_col = col

    def _begin_app_conf_inline_edit(self, iid: str, col: str, mode: str = "text") -> None:
        tv = self._app_tv_conf
        if tv is None:
            return
        try:
            idx = int(iid)
        except Exception:
            return
        if idx < 0 or idx >= len(self._app_conf_rows):
            return
        row = self._app_conf_rows[idx]

        self._end_app_input_inline_editor(commit=True)
        self._end_app_setting_inline_editor(commit=True)
        self._end_app_output_inline_editor(commit=True)
        self._end_app_control_inline_editor(commit=True)
        self._end_app_conf_inline_editor(commit=True)

        key_by_col = {"#1": "name", "#2": "type", "#3": "src", "#4": "desc"}
        key = key_by_col.get(col)
        if key is None:
            return

        bbox = tv.bbox(iid, col)
        if not bbox:
            return
        x, y, w, h = bbox
        value = row.get(key) or ""

        if key == "type":
            all_values = list(self._get_app_conf_types())
            cb = ttk.Combobox(tv, values=all_values, width=max(10, int(w / 7)))
            cb.set(value)
            try:
                if mode == "type_click" and all_values:
                    cb.configure(state="readonly")
                elif mode == "type_input":
                    cb.configure(state="normal")
            except Exception:
                pass

            def is_subsequence(q: str, s: str) -> bool:
                qi = 0
                if not q:
                    return True
                for ch in s:
                    if qi < len(q) and ch == q[qi]:
                        qi += 1
                        if qi == len(q):
                            return True
                return qi == len(q)

            def rank_matches(q: str, items: list[str]) -> list[str]:
                ql = q.lower().strip()
                if not ql:
                    return items
                starts: list[str] = []
                contains: list[str] = []
                subseq: list[str] = []
                for it in items:
                    il = it.lower()
                    if il.startswith(ql):
                        starts.append(it)
                    elif ql in il:
                        contains.append(it)
                    elif is_subsequence(ql, il):
                        subseq.append(it)
                return starts + contains + subseq

            def on_key_release(_e=None) -> None:
                try:
                    q = cb.get() or ""
                    cb.configure(values=rank_matches(q, all_values))
                    if mode == "type_input":
                        self._combobox_post(cb)
                except Exception:
                    pass

            def commit_and_close(_e=None) -> None:
                self._end_app_conf_inline_editor(commit=True)

            cb.bind("<<ComboboxSelected>>", commit_and_close)
            cb.bind("<Return>", commit_and_close)
            cb.bind("<Escape>", lambda _e: self._end_app_conf_inline_editor(commit=False))
            cb.bind("<FocusOut>", lambda _e: self._end_app_conf_inline_editor(commit=True))
            cb.bind("<KeyRelease>", on_key_release)

            if mode == "type_click":
                cb.bind("<Button-1>", lambda _e: (self._combobox_toggle_posted(cb), "break")[1])
                cb.bind(
                    "<Double-Button-1>",
                    lambda _e, _iid=iid, _col=col: (self._begin_app_conf_inline_edit(_iid, _col, mode="type_input"), "break")[1],
                )

            cb.place(x=x, y=y, width=w, height=h)
            cb.focus_set()
            self._app_conf_inline = cb
            try:
                tv.after_idle(lambda: self._combobox_post(cb))
            except Exception:
                self._combobox_post(cb)

            if mode == "type_input":
                try:
                    cb.icursor(tk.END)
                    cb.selection_range(0, tk.END)
                except Exception:
                    pass
        else:
            ent = ttk.Entry(tv)
            ent.insert(0, value)

            def commit_and_close(_e=None) -> None:
                self._end_app_conf_inline_editor(commit=True)

            ent.bind("<Return>", commit_and_close)
            ent.bind("<Escape>", lambda _e: self._end_app_conf_inline_editor(commit=False))
            ent.bind("<FocusOut>", lambda _e: self._end_app_conf_inline_editor(commit=True))
            ent.place(x=x, y=y, width=w, height=h)
            ent.focus_set()
            ent.selection_range(0, tk.END)
            self._app_conf_inline = ent

        self._app_conf_inline_iid = iid
        self._app_conf_inline_col = col

    def _begin_app_control_inline_edit(self, iid: str, col: str, mode: str = "text") -> None:
        tv = self._app_tv_control
        if tv is None:
            return
        try:
            idx = int(iid)
        except Exception:
            return
        if idx < 0 or idx >= len(self._app_control_rows):
            return
        row = self._app_control_rows[idx]

        self._end_app_input_inline_editor(commit=True)
        self._end_app_setting_inline_editor(commit=True)
        self._end_app_output_inline_editor(commit=True)
        self._end_app_conf_inline_editor(commit=True)
        self._end_app_control_inline_editor(commit=True)

        key_by_col = {"#1": "name", "#2": "type", "#3": "src", "#4": "desc"}
        key = key_by_col.get(col)
        if key is None:
            return

        bbox = tv.bbox(iid, col)
        if not bbox:
            return
        x, y, w, h = bbox
        value = row.get(key) or ""

        if key == "type":
            all_values = list(self._get_app_control_types())
            cb = ttk.Combobox(tv, values=all_values, width=max(10, int(w / 7)))
            cb.set(value)
            try:
                if mode == "type_click" and all_values:
                    cb.configure(state="readonly")
                elif mode == "type_input":
                    cb.configure(state="normal")
            except Exception:
                pass

            def is_subsequence(q: str, s: str) -> bool:
                qi = 0
                if not q:
                    return True
                for ch in s:
                    if qi < len(q) and ch == q[qi]:
                        qi += 1
                        if qi == len(q):
                            return True
                return qi == len(q)

            def rank_matches(q: str, items: list[str]) -> list[str]:
                ql = q.lower().strip()
                if not ql:
                    return items
                starts: list[str] = []
                contains: list[str] = []
                subseq: list[str] = []
                for it in items:
                    il = it.lower()
                    if il.startswith(ql):
                        starts.append(it)
                    elif ql in il:
                        contains.append(it)
                    elif is_subsequence(ql, il):
                        subseq.append(it)
                return starts + contains + subseq

            def on_key_release(_e=None) -> None:
                try:
                    q = cb.get() or ""
                    cb.configure(values=rank_matches(q, all_values))
                    if mode == "type_input":
                        self._combobox_post(cb)
                except Exception:
                    pass

            def commit_and_close(_e=None) -> None:
                self._end_app_control_inline_editor(commit=True)

            cb.bind("<<ComboboxSelected>>", commit_and_close)
            cb.bind("<Return>", commit_and_close)
            cb.bind("<Escape>", lambda _e: self._end_app_control_inline_editor(commit=False))
            cb.bind("<FocusOut>", lambda _e: self._end_app_control_inline_editor(commit=True))
            cb.bind("<KeyRelease>", on_key_release)

            if mode == "type_click":
                cb.bind("<Button-1>", lambda _e: (self._combobox_toggle_posted(cb), "break")[1])
                cb.bind(
                    "<Double-Button-1>",
                    lambda _e, _iid=iid, _col=col: (self._begin_app_control_inline_edit(_iid, _col, mode="type_input"), "break")[1],
                )

            cb.place(x=x, y=y, width=w, height=h)
            cb.focus_set()
            self._app_control_inline = cb
            try:
                tv.after_idle(lambda: self._combobox_post(cb))
            except Exception:
                self._combobox_post(cb)

            if mode == "type_input":
                try:
                    cb.icursor(tk.END)
                    cb.selection_range(0, tk.END)
                except Exception:
                    pass
        else:
            ent = ttk.Entry(tv)
            ent.insert(0, value)

            def commit_and_close(_e=None) -> None:
                self._end_app_control_inline_editor(commit=True)

            ent.bind("<Return>", commit_and_close)
            ent.bind("<Escape>", lambda _e: self._end_app_control_inline_editor(commit=False))
            ent.bind("<FocusOut>", lambda _e: self._end_app_control_inline_editor(commit=True))
            ent.place(x=x, y=y, width=w, height=h)
            ent.focus_set()
            ent.selection_range(0, tk.END)
            self._app_control_inline = ent

        self._app_control_inline_iid = iid
        self._app_control_inline_col = col

    def _end_app_input_inline_editor(self, *, commit: bool) -> None:
        w = self._app_input_inline
        if w is None:
            return

        iid = self._app_input_inline_iid
        col = self._app_input_inline_col

        # Clear state first to avoid recursion
        self._app_input_inline = None
        self._app_input_inline_iid = None
        self._app_input_inline_col = None

        try:
            w.place_forget()
        except Exception:
            pass

        if not commit or iid is None or col is None:
            try:
                w.destroy()
            except Exception:
                pass
            return

        row = self._app_input_iid_to_row.get(iid)
        if row is None:
            try:
                w.destroy()
            except Exception:
                pass
            return

        key_by_col = {"#1": "name", "#2": "type", "#3": "src", "#4": "doRef"}
        key = key_by_col.get(col)
        if key is None:
            try:
                w.destroy()
            except Exception:
                pass
            return

        new_value = ""
        try:
            new_value = (w.get() or "")  # type: ignore[attr-defined]
        except Exception:
            new_value = ""

        if key == "name" and not (new_value or "").strip():
            messagebox.showerror("Missing", "name is required", parent=self)
            try:
                w.destroy()
            except Exception:
                pass
            self._begin_app_input_inline_edit(iid, col)
            return

        if key == "type":
            typed = (new_value or "").strip()
            all_values = list(self._get_app_input_types())
            if typed and all_values and typed not in all_values:
                tl = typed.lower()
                starts = [s for s in all_values if s.lower().startswith(tl)]
                contains = [s for s in all_values if (tl in s.lower() and s not in starts)]
                pick = (starts + contains)
                if len(pick) == 1:
                    new_value = pick[0]

        row[key] = new_value.strip() if key in {"name", "type"} else new_value
        self._update_app_input_tv_row(iid)

        try:
            w.destroy()
        except Exception:
            pass

    def _end_app_setting_inline_editor(self, *, commit: bool) -> None:
        w = self._app_setting_inline
        if w is None:
            return

        iid = self._app_setting_inline_iid
        col = self._app_setting_inline_col

        self._app_setting_inline = None
        self._app_setting_inline_iid = None
        self._app_setting_inline_col = None

        try:
            w.place_forget()
        except Exception:
            pass

        if not commit or iid is None or col is None:
            try:
                w.destroy()
            except Exception:
                pass
            return

        try:
            idx = int(iid)
        except Exception:
            try:
                w.destroy()
            except Exception:
                pass
            return

        if idx < 0 or idx >= len(self._app_setting_rows):
            try:
                w.destroy()
            except Exception:
                pass
            return

        row = self._app_setting_rows[idx]
        key_by_col = {"#1": "name", "#2": "type", "#3": "src", "#4": "desc"}
        key = key_by_col.get(col)
        if key is None:
            try:
                w.destroy()
            except Exception:
                pass
            return

        new_value = ""
        try:
            new_value = (w.get() or "")  # type: ignore[attr-defined]
        except Exception:
            new_value = ""

        if key == "name" and not (new_value or "").strip():
            messagebox.showerror("Missing", "name is required", parent=self)
            try:
                w.destroy()
            except Exception:
                pass
            self._begin_app_setting_inline_edit(iid, col)
            return

        if key == "type":
            typed = (new_value or "").strip()
            all_values = list(self._get_app_setting_types())
            if typed and all_values and typed not in all_values:
                tl = typed.lower()
                starts = [s for s in all_values if s.lower().startswith(tl)]
                contains = [s for s in all_values if (tl in s.lower() and s not in starts)]
                pick = (starts + contains)
                if len(pick) == 1:
                    new_value = pick[0]

        row[key] = new_value.strip() if key in {"name", "type"} else new_value
        self._update_simple_app_tv_row("setting", iid)

        try:
            w.destroy()
        except Exception:
            pass

    def _end_app_output_inline_editor(self, *, commit: bool) -> None:
        w = self._app_output_inline
        if w is None:
            return

        iid = self._app_output_inline_iid
        col = self._app_output_inline_col

        self._app_output_inline = None
        self._app_output_inline_iid = None
        self._app_output_inline_col = None

        try:
            w.place_forget()
        except Exception:
            pass

        if not commit or iid is None or col is None:
            try:
                w.destroy()
            except Exception:
                pass
            return

        try:
            idx = int(iid)
        except Exception:
            try:
                w.destroy()
            except Exception:
                pass
            return

        if idx < 0 or idx >= len(self._app_output_rows):
            try:
                w.destroy()
            except Exception:
                pass
            return

        row = self._app_output_rows[idx]
        tv = self._app_tv_output
        if tv is None:
            try:
                w.destroy()
            except Exception:
                pass
            return

        cols = list(tv["columns"])
        key: str | None = None
        try:
            idx_col = int(col.lstrip("#")) - 1
            if 0 <= idx_col < len(cols):
                key = cols[idx_col]
        except Exception:
            key = None

        if key not in {"name", "type", "doRef", "desc", "MaxContiguous", "Overlap"}:
            try:
                w.destroy()
            except Exception:
                pass
            return

        new_value = ""
        try:
            new_value = (w.get() or "")  # type: ignore[attr-defined]
        except Exception:
            new_value = ""

        if key == "name" and not (new_value or "").strip():
            messagebox.showerror("Missing", "name is required", parent=self)
            try:
                w.destroy()
            except Exception:
                pass
            self._begin_app_output_inline_edit(iid, col)
            return

        if key == "type":
            typed = (new_value or "").strip()
            all_values = list(self._get_app_output_types())
            if typed and all_values and typed not in all_values:
                tl = typed.lower()
                starts = [s for s in all_values if s.lower().startswith(tl)]
                contains = [s for s in all_values if (tl in s.lower() and s not in starts)]
                pick = (starts + contains)
                if len(pick) == 1:
                    new_value = pick[0]

        row[key] = new_value.strip() if key in {"name", "type", "doRef", "MaxContiguous", "Overlap"} else new_value
        self._update_simple_app_tv_row("output", iid)

        try:
            w.destroy()
        except Exception:
            pass

    def _end_app_conf_inline_editor(self, *, commit: bool) -> None:
        w = self._app_conf_inline
        if w is None:
            return

        iid = self._app_conf_inline_iid
        col = self._app_conf_inline_col

        self._app_conf_inline = None
        self._app_conf_inline_iid = None
        self._app_conf_inline_col = None

        try:
            w.place_forget()
        except Exception:
            pass

        if not commit or iid is None or col is None:
            try:
                w.destroy()
            except Exception:
                pass
            return

        try:
            idx = int(iid)
        except Exception:
            try:
                w.destroy()
            except Exception:
                pass
            return

        if idx < 0 or idx >= len(self._app_conf_rows):
            try:
                w.destroy()
            except Exception:
                pass
            return

        row = self._app_conf_rows[idx]
        key_by_col = {"#1": "name", "#2": "type", "#3": "src", "#4": "desc"}
        key = key_by_col.get(col)
        if key is None:
            try:
                w.destroy()
            except Exception:
                pass
            return

        new_value = ""
        try:
            new_value = (w.get() or "")  # type: ignore[attr-defined]
        except Exception:
            new_value = ""

        if key == "name" and not (new_value or "").strip():
            messagebox.showerror("Missing", "name is required", parent=self)
            try:
                w.destroy()
            except Exception:
                pass
            self._begin_app_conf_inline_edit(iid, col)
            return

        if key == "type":
            typed = (new_value or "").strip()
            all_values = list(self._get_app_conf_types())
            if typed and all_values and typed not in all_values:
                tl = typed.lower()
                starts = [s for s in all_values if s.lower().startswith(tl)]
                contains = [s for s in all_values if (tl in s.lower() and s not in starts)]
                pick = (starts + contains)
                if len(pick) == 1:
                    new_value = pick[0]

        row[key] = new_value.strip() if key in {"name", "type"} else new_value
        self._update_simple_app_tv_row("conf", iid)

        try:
            w.destroy()
        except Exception:
            pass

    def _end_app_control_inline_editor(self, *, commit: bool) -> None:
        w = self._app_control_inline
        if w is None:
            return

        iid = self._app_control_inline_iid
        col = self._app_control_inline_col

        self._app_control_inline = None
        self._app_control_inline_iid = None
        self._app_control_inline_col = None

        try:
            w.place_forget()
        except Exception:
            pass

        if not commit or iid is None or col is None:
            try:
                w.destroy()
            except Exception:
                pass
            return

        try:
            idx = int(iid)
        except Exception:
            try:
                w.destroy()
            except Exception:
                pass
            return

        if idx < 0 or idx >= len(self._app_control_rows):
            try:
                w.destroy()
            except Exception:
                pass
            return

        row = self._app_control_rows[idx]
        key_by_col = {"#1": "name", "#2": "type", "#3": "src", "#4": "desc"}
        key = key_by_col.get(col)
        if key is None:
            try:
                w.destroy()
            except Exception:
                pass
            return

        new_value = ""
        try:
            new_value = (w.get() or "")  # type: ignore[attr-defined]
        except Exception:
            new_value = ""

        if key == "name" and not (new_value or "").strip():
            messagebox.showerror("Missing", "name is required", parent=self)
            try:
                w.destroy()
            except Exception:
                pass
            self._begin_app_control_inline_edit(iid, col)
            return

        if key == "type":
            typed = (new_value or "").strip()
            all_values = list(self._get_app_control_types())
            if typed and all_values and typed not in all_values:
                tl = typed.lower()
                starts = [s for s in all_values if s.lower().startswith(tl)]
                contains = [s for s in all_values if (tl in s.lower() and s not in starts)]
                pick = (starts + contains)
                if len(pick) == 1:
                    new_value = pick[0]

        row[key] = new_value.strip() if key in {"name", "type"} else new_value
        self._update_simple_app_tv_row("control", iid)

        try:
            w.destroy()
        except Exception:
            pass

        if not commit or iid is None or col is None:
            try:
                w.destroy()
            except Exception:
                pass
            return

        row = self._app_input_iid_to_row.get(iid)
        if row is None:
            try:
                w.destroy()
            except Exception:
                pass
            return

        key_by_col = {"#1": "name", "#2": "type", "#3": "src", "#4": "doRef"}
        key = key_by_col.get(col)
        if key is None:
            try:
                w.destroy()
            except Exception:
                pass
            return

        new_value = ""
        try:
            new_value = (w.get() or "")  # type: ignore[attr-defined]
        except Exception:
            new_value = ""

        if key == "name" and not (new_value or "").strip():
            messagebox.showerror("Missing", "name is required", parent=self)
            # Re-open editor on same cell
            try:
                w.destroy()
            except Exception:
                pass
            self._begin_app_input_inline_edit(iid, col)
            return

        if key == "type":
            # Fuzzy-pick best enum value if user typed partial text.
            typed = (new_value or "").strip()
            all_values = list(self._get_app_input_types())
            if typed and all_values and typed not in all_values:
                tl = typed.lower()
                starts = [s for s in all_values if s.lower().startswith(tl)]
                contains = [s for s in all_values if (tl in s.lower() and s not in starts)]
                pick = (starts + contains)
                if len(pick) == 1:
                    new_value = pick[0]
                elif pick:
                    # If multiple matches, keep what user typed (no surprise change).
                    pass

        row[key] = new_value.strip() if key in {"name", "type", "doRef"} else new_value
        self._update_app_input_tv_row(iid)

        try:
            w.destroy()
        except Exception:
            pass

    def _apply_app_input_rows_to_xml(self) -> None:
        if self._app_funblock is None:
            return
        fb = self._app_funblock
        ns = ""
        if isinstance(fb.tag, str) and fb.tag.startswith("{"):
            ns = fb.tag.split("}", 1)[0][1:]

        def q(local_name: str) -> str:
            return f"{{{ns}}}{local_name}" if ns else local_name

        # Remove existing inputs
        for ch in list(fb):
            if not isinstance(ch.tag, str):
                continue
            if self._local_name(ch.tag) == "input":
                fb.remove(ch)

        insert_index = 0
        for row in self._app_input_rows:
            el = ET.Element(q("input"))
            attrib: dict[str, str] = {}
            attrib["name"] = row.get("name") or ""
            attrib["type"] = row.get("type") or ""
            attrib["desc"] = row.get("desc") or ""
            attrib["src"] = row.get("src") or ""
            attrib["doRef"] = row.get("doRef") or ""
            if (row.get("softlink") or "").lower() == "true":
                attrib["softlink"] = "true"
            if (row.get("confpin") or "").lower() == "true":
                attrib["confpin"] = "true"
            attrib["buffer"] = "1"
            attrib["index"] = "-1"
            attrib["condition"] = "1"
            el.attrib = attrib
            fb.insert(insert_index, el)
            insert_index += 1

    def _apply_simple_app_rows_to_xml(self, *, tag_local: str, rows: list[dict[str, str]], attr_order: list[str]) -> None:
        if self._app_funblock is None:
            return
        fb = self._app_funblock
        ns = ""
        if isinstance(fb.tag, str) and fb.tag.startswith("{"):
            ns = fb.tag.split("}", 1)[0][1:]

        def q(local_name: str) -> str:
            return f"{{{ns}}}{local_name}" if ns else local_name

        # Remove existing elements
        for ch in list(fb):
            if not isinstance(ch.tag, str):
                continue
            if self._local_name(ch.tag) == tag_local:
                fb.remove(ch)

        # Append new elements (keep input handled separately)
        for row in rows:
            el = ET.Element(q(tag_local))
            attrib: dict[str, str] = {}
            for k in attr_order:
                v = row.get(k) or ""
                if k in {"name", "type", "src", "doRef", "persist", "faultlog", "outPurpose", "srvRef", "MaxContiguous", "Overlap"}:
                    v = v.strip()

                if tag_local == "output":
                    if k == "persist":
                        v = "true" if (v or "").lower() == "true" else "false"
                    elif k == "faultlog":
                        if (v or "").lower() != "true":
                            continue
                        v = "true"
                    elif k == "MaxContiguous":
                        v = v if v else "0"
                    elif k == "Overlap":
                        v = v if v else "1"
                attrib[k] = v
            el.attrib = attrib
            fb.append(el)

    def _new_application(self) -> None:
        dlg = _NewApplicationChoiceDialog(self)
        choice = dlg.show()
        if not choice:
            return
        if choice == "from_instance":
            self._new_application_from_ln_instance()
        elif choice == "copy":
            self._copy_existing_application_files()

    def _new_application_from_ln_instance(self) -> None:
        lndm_dir = self._lndm_dir()
        items = self._scan_xml_relpaths(lndm_dir)
        if not items:
            messagebox.showerror("Missing", f"No LN instance (*.xml) found under:\n\n{os.fspath(lndm_dir)}", parent=self)
            return

        dlg = _CreateFromLnInstanceDialog(self, lndm_dir=lndm_dir, items=items)
        res = dlg.show()
        if not res or self.instance_editor is None:
            return

        # Build input rows from LN instance InRef purpose
        try:
            inrefs = self._extract_ln_instance_inrefs(lndm_dir / res["ln_instance_rel"])
        except Exception as e:
            messagebox.showerror("Open failed", str(e), parent=self)
            return

        # Build setting rows from LN instance lnType (DOs containing fc=SP/SE)
        try:
            ln_type_id = self._read_ln_instance_lntype(lndm_dir / res["ln_instance_rel"])
        except Exception:
            ln_type_id = ""
        setting_rows = self._build_setting_rows_from_lntype(ln_type_id)
        output_rows = self._build_output_rows_from_lntype(ln_type_id)
        control_rows = self._build_control_rows_from_lntype(ln_type_id)

        input_rows: list[dict[str, str]] = []
        for it in inrefs:
            purpose = (it.get("purpose_clean") or "").strip()
            seq = (it.get("seq") or "1").strip()
            name = purpose if purpose else f"input{seq}"
            do_ref = f".InRef%{purpose}" if purpose else ""
            input_rows.append(
                {
                    "name": name,
                    "type": "",
                    "desc": "",
                    "src": "",
                    "doRef": do_ref,
                    "softlink": "",
                    "confpin": "",
                }
            )

        # Apply to UI vars
        self.instance_editor.var_app_name.set(res["name"])
        self.instance_editor.var_app_class.set(res["class"])
        self.instance_editor.var_app_seqNb.set(res["seqNb"])
        self.instance_editor.var_app_LnRef.set(res["LnRef"])
        self.instance_editor.var_app_desc.set(res["desc"])

        # Create a new in-memory ExecutionScheme skeleton
        exe_ns = "http://www.schneider-electric.com/PowerLogic/ExecutionScheme"
        xsi_ns = "http://www.w3.org/2001/XMLSchema-instance"
        root = ET.Element(f"{{{exe_ns}}}EasergyPExecutionScheme")
        root.attrib[f"{{{xsi_ns}}}schemaLocation"] = (
            "http://www.schneider-electric.com/PowerLogic/ExecutionScheme SE_PowerLogic_ExecutionScheme.xsd"
        )
        grp = ET.SubElement(root, f"{{{exe_ns}}}group")
        grp.attrib["name"] = "CAG_1"
        fb = ET.SubElement(grp, f"{{{exe_ns}}}funBlock")
        fb.attrib["name"] = res["name"]
        fb.attrib["class"] = res["class"]
        fb.attrib["seqNb"] = res["seqNb"]
        fb.attrib["LnRef"] = res["LnRef"]
        fb.attrib["desc"] = res["desc"]

        self._app_root = root
        self._app_funblock = fb
        self._app_file_path = None
        self._app_input_types_cache = None
        self._app_setting_types_cache = None
        self._app_output_types_cache = None
        self._app_conf_types_cache = None
        self._app_control_types_cache = None

        # Fill tables for a new skeleton
        self._set_app_input_rows(input_rows)
        self._app_table_set_rows("output", output_rows)
        self._app_table_set_rows("setting", setting_rows)
        self._app_table_set_rows("conf", [])
        self._app_table_set_rows("control", control_rows)

        try:
            if self.notebook is not None and self.tab_application is not None:
                self.notebook.select(self.tab_application)
        except Exception:
            pass

        self._set_status("New application created from LN instance (unsaved)")

    def _copy_existing_application_files(self) -> None:
        app_dir = self._application_dir()
        items = self._scan_xml_relpaths(app_dir)
        if not items:
            messagebox.showerror("Missing", f"No application (*.xml) found under:\n\n{os.fspath(app_dir)}", parent=self)
            return

        dlg = _CopyApplicationDialog(self, app_dir=app_dir, items=items)
        res = dlg.show()
        if not res:
            return

        src = app_dir / res["src_rel"]
        dst = app_dir / res["new_name"]

        if dst.exists():
            if not messagebox.askyesno("Overwrite?", f"File exists:\n\n{os.fspath(dst)}\n\nOverwrite?", parent=self):
                return
        try:
            dst.write_bytes(src.read_bytes())
        except Exception as e:
            messagebox.showerror("Copy failed", str(e), parent=self)
            return

        self._app_input_types_cache = None
        self._app_setting_types_cache = None
        self._app_output_types_cache = None
        self._app_conf_types_cache = None
        self._app_control_types_cache = None
        self._open_application_from_path(dst)

    def _local_name(self, tag: str) -> str:
        if tag.startswith("{"):
            return tag.split("}", 1)[1]
        return tag

    def _clear_tv(self, tv: ttk.Treeview | None) -> None:
        if tv is None:
            return
        for iid in tv.get_children(""):
            tv.delete(iid)

    def _fill_tv(self, tv: ttk.Treeview | None, rows: list[dict[str, str]], cols: list[str]) -> None:
        if tv is None:
            return
        self._clear_tv(tv)
        for i, r in enumerate(rows):
            tv.insert("", "end", iid=str(i), values=[(r.get(c) or "") for c in cols])

    def _open_application_from_path(self, path: Path) -> None:
        path = Path(path)
        try:
            tree = ET.parse(path)
            root = tree.getroot()
        except Exception as e:
            messagebox.showerror("Open failed", str(e), parent=self)
            return

        funblock = None
        for el in root.iter():
            if not isinstance(el.tag, str):
                continue
            if self._local_name(el.tag) == "funBlock":
                funblock = el
                break
        if funblock is None:
            messagebox.showerror("Invalid", "No <funBlock> found in file", parent=self)
            return

        self._app_file_path = path
        self._app_root = root
        self._app_funblock = funblock
        self._app_input_types_cache = None
        self._app_setting_types_cache = None
        self._app_output_types_cache = None
        self._app_conf_types_cache = None
        self._app_control_types_cache = None

        if self.instance_editor is not None:
            self.instance_editor.var_app_name.set((funblock.attrib.get("name") or "").strip())
            self.instance_editor.var_app_class.set((funblock.attrib.get("class") or "").strip())
            self.instance_editor.var_app_seqNb.set((funblock.attrib.get("seqNb") or "").strip() or "50")
            self.instance_editor.var_app_LnRef.set((funblock.attrib.get("LnRef") or "").strip())
            self.instance_editor.var_app_desc.set(funblock.attrib.get("desc") or "")

        def _rows(tag_name: str, wanted: list[str]) -> list[dict[str, str]]:
            out: list[dict[str, str]] = []
            for ch in list(funblock):
                if not isinstance(ch.tag, str):
                    continue
                if self._local_name(ch.tag) != tag_name:
                    continue
                row = {k: (ch.attrib.get(k) or "") for k in wanted}
                out.append(row)
            return out

        self._set_app_input_rows(_rows("input", ["name", "type", "desc", "src", "doRef", "softlink", "confpin"]))
        self._app_table_set_rows("setting", _rows("setting", ["name", "type", "src", "desc"]))
        self._app_table_set_rows(
            "output",
            _rows(
                "output",
                [
                    "name",
                    "type",
                    "doRef",
                    "desc",
                    "outPurpose",
                    "srvRef",
                    "persist",
                    "faultlog",
                    "MaxContiguous",
                    "Overlap",
                ],
            ),
        )
        self._app_table_set_rows("conf", _rows("conf", ["name", "type", "src", "desc"]))
        self._app_table_set_rows("control", _rows("control", ["name", "type", "src", "desc"]))

        self._set_status(f"Opened application: {os.fspath(path)}")

    def _open_application(self) -> None:
        app_dir = self._application_dir()
        initialdir = app_dir if app_dir.exists() else self.workspace_root
        target = filedialog.askopenfilename(
            parent=self,
            title="Open application file",
            initialdir=os.fspath(initialdir),
            filetypes=[("XML", "*.xml"), ("All", "*")],
        )
        if not target:
            return

        self._open_application_from_path(Path(target))

    def _apply_funblock_fields_to_xml(self) -> None:
        if self._app_funblock is None or self.instance_editor is None:
            return
        fb = self._app_funblock
        fb.attrib["name"] = (self.instance_editor.var_app_name.get() or "").strip()
        fb.attrib["class"] = (self.instance_editor.var_app_class.get() or "").strip()
        fb.attrib["seqNb"] = (self.instance_editor.var_app_seqNb.get() or "").strip() or "50"
        fb.attrib["LnRef"] = (self.instance_editor.var_app_LnRef.get() or "").strip()
        fb.attrib["desc"] = (self.instance_editor.var_app_desc.get() or "")

    def _save_application(self) -> None:
        if self._app_root is None:
            messagebox.showerror("Missing", "Open an application file first.", parent=self)
            return

        # Default Save uses funBlock name as file name under application/.
        app_dir = self._application_dir()
        fb_name = ""
        if self.instance_editor is not None:
            fb_name = (self.instance_editor.var_app_name.get() or "").strip()
        if not fb_name:
            messagebox.showerror("Missing", "funBlock name is required", parent=self)
            return

        base_dir: Path
        if self._app_file_path is not None:
            base_dir = Path(self._app_file_path).parent
        elif app_dir.exists():
            base_dir = app_dir
        else:
            base_dir = self.workspace_root

        target_path = base_dir / f"{fb_name}.xml"

        # If name points to a different existing file, confirm overwrite.
        try:
            if target_path.exists() and (self._app_file_path is None or target_path.resolve() != self._app_file_path.resolve()):
                if not messagebox.askyesno(
                    "Overwrite?",
                    f"File exists:\n\n{os.fspath(target_path)}\n\nOverwrite?",
                    parent=self,
                ):
                    return
        except Exception:
            pass

        self._apply_funblock_fields_to_xml()
        self._apply_app_input_rows_to_xml()
        self._apply_simple_app_rows_to_xml(
            tag_local="output",
            rows=self._app_output_rows,
            attr_order=["name", "type", "desc", "outPurpose", "srvRef", "persist", "doRef", "MaxContiguous", "Overlap", "faultlog"],
        )
        self._apply_simple_app_rows_to_xml(tag_local="setting", rows=self._app_setting_rows, attr_order=["name", "type", "desc", "src"])
        self._apply_simple_app_rows_to_xml(tag_local="conf", rows=self._app_conf_rows, attr_order=["name", "type", "desc", "src"])
        self._apply_simple_app_rows_to_xml(tag_local="control", rows=self._app_control_rows, attr_order=["name", "type", "desc", "src"])
        try:
            save_execution_scheme_root(self._app_root, target_path=target_path)
        except Exception as e:
            messagebox.showerror("Save failed", str(e), parent=self)
            return
        self._app_file_path = target_path
        self._set_status(f"Saved application: {os.fspath(target_path)}")

    def _save_application_as(self) -> None:
        if self._app_root is None:
            messagebox.showerror("Missing", "Open an application file first.", parent=self)
            return

        app_dir = self._application_dir()
        initialdir = app_dir if app_dir.exists() else self.workspace_root
        initialfile = (self._app_file_path.name if self._app_file_path is not None else "Application.xml")
        target = filedialog.asksaveasfilename(
            parent=self,
            title="Save application file as",
            defaultextension=".xml",
            initialdir=os.fspath(initialdir),
            initialfile=initialfile,
            filetypes=[("XML", "*.xml"), ("All", "*")],
        )
        if not target:
            return

        path = Path(target)
        self._apply_funblock_fields_to_xml()
        self._apply_app_input_rows_to_xml()
        self._apply_simple_app_rows_to_xml(
            tag_local="output",
            rows=self._app_output_rows,
            attr_order=["name", "type", "desc", "outPurpose", "srvRef", "persist", "doRef", "MaxContiguous", "Overlap", "faultlog"],
        )
        self._apply_simple_app_rows_to_xml(tag_local="setting", rows=self._app_setting_rows, attr_order=["name", "type", "desc", "src"])
        self._apply_simple_app_rows_to_xml(tag_local="conf", rows=self._app_conf_rows, attr_order=["name", "type", "desc", "src"])
        self._apply_simple_app_rows_to_xml(tag_local="control", rows=self._app_control_rows, attr_order=["name", "type", "desc", "src"])
        try:
            save_execution_scheme_root(self._app_root, target_path=path)
        except Exception as e:
            messagebox.showerror("Save As failed", str(e), parent=self)
            return

        self._app_file_path = path
        self._set_status(f"Saved application as: {os.fspath(path)}")

    def _create_menu(self) -> None:
        menubar = tk.Menu(self)

        m_file = tk.Menu(menubar, tearoff=False)
        m_file.add_command(label="New", accelerator="Ctrl+N", command=self._new_shortcut)
        m_file.add_command(label="Open...", accelerator="Ctrl+O", command=self._open_shortcut)
        m_file.add_separator()
        m_file.add_command(label="Save", accelerator="Ctrl+S", command=self._save_shortcut)
        m_file.add_command(label="Save As...", accelerator="Ctrl+Shift+S", command=self._save_as_shortcut)
        m_file.add_separator()
        m_file.add_command(label="Exit", command=self.destroy)

        menubar.add_cascade(label="File", menu=m_file)

        m_tools = tk.Menu(menubar, tearoff=False)
        m_tools.add_command(label="Open DBMBuilder (Build)", command=self.open_builder_callback)

        menubar.add_cascade(label="Tools", menu=m_tools)

        self.config(menu=menubar)

        bar = ttk.Label(self, textvariable=self.status, anchor="w")
        bar.pack(side="bottom", fill="x")

    def _set_status(self, text: str) -> None:
        self.status.set(text)
