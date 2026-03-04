from __future__ import annotations

import os
import hashlib
import re
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog
from tkinter import ttk
from typing import Callable
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

from ui.ln_instance_tab import LNInstanceEditorFrame
from ln_instance_scanner import save_execution_scheme_root
from ln_instance_scanner import load_ln_instance_document
from ui.enum_tab import EnumTab
from ui.do_template_tab import DoTemplateTab
from ui.ln_template_tab import LNodeTypeEditor
from ui.application_tab import ApplicationTab
from ui.afg_tab import AfgTab


APP_TITLE = "DBMEditor"


SCL_NS = "http://www.iec.ch/61850/2003/SCL"

# PowerLogic HMI customization namespace (HMI template files).
HMI_CUST_NS = "http://www.schneider-electric.com/PowerLogic/HmiCustomization"


def _local_name(tag: str) -> str:
    if isinstance(tag, str) and tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag


def _q(ns: str, local: str) -> str:
    return f"{{{ns}}}{local}" if ns else local


def _deepcopy_et_element(el: ET.Element) -> ET.Element:
    # ElementTree has no built-in deepcopy; round-trip via string is good enough here.
    return ET.fromstring(ET.tostring(el, encoding="unicode"))


def _clone_et_element_with_id_map(el: ET.Element) -> tuple[ET.Element, dict[int, int]]:
    """Clone an ElementTree element while producing an id(old)->id(new) mapping.

    This is used for AFG undo snapshots, where UI highlight baselines are keyed
    by element identity (id(el)).
    """

    id_map: dict[int, int] = {}

    def clone_node(src: ET.Element) -> ET.Element:
        dst = ET.Element(src.tag, attrib=dict(src.attrib))
        dst.text = src.text
        dst.tail = src.tail
        id_map[id(src)] = id(dst)
        for ch in list(src):
            dst.append(clone_node(ch))
        return dst

    return (clone_node(el), id_map)


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


class _DoTemplateSaveAsDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, *, initial_id: str = ""):
        super().__init__(parent)
        self.title("Save DO template as")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self._result: str | None = None

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="New DOType id (file name is <id>.xml):").grid(row=0, column=0, sticky="w")
        self.var_id = tk.StringVar(value=initial_id)
        ent = ttk.Entry(frm, textvariable=self.var_id, width=48)
        ent.grid(row=1, column=0, sticky="we", pady=(6, 0))
        frm.columnconfigure(0, weight=1)

        btns = ttk.Frame(frm)
        btns.grid(row=2, column=0, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Cancel", command=self._cancel).pack(side="right")
        ttk.Button(btns, text="OK", command=self._ok).pack(side="right", padx=(0, 8))

        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Return>", lambda _e: self._ok())

        try:
            ent.focus_set()
            ent.select_range(0, tk.END)
        except Exception:
            pass

    def _ok(self) -> None:
        new_id = (self.var_id.get() or "").strip()
        if not new_id:
            messagebox.showerror("Missing", "DOType id is required", parent=self)
            return
        self._result = new_id
        self.destroy()

    def _cancel(self) -> None:
        self._result = None
        self.destroy()

    def show(self) -> str | None:
        self.wait_window(self)
        return self._result


class _EnumTypeSaveAsDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, *, initial_id: str = ""):
        super().__init__(parent)
        self.title("Save EnumType as")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self._result: str | None = None

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="New EnumType id (file name is <id>.xml):").grid(row=0, column=0, sticky="w")
        self.var_id = tk.StringVar(value=initial_id)
        ent = ttk.Entry(frm, textvariable=self.var_id, width=48)
        ent.grid(row=1, column=0, sticky="we", pady=(6, 0))
        frm.columnconfigure(0, weight=1)

        btns = ttk.Frame(frm)
        btns.grid(row=2, column=0, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Cancel", command=self._cancel).pack(side="right")
        ttk.Button(btns, text="OK", command=self._ok).pack(side="right", padx=(0, 8))

        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Return>", lambda _e: self._ok())

        try:
            ent.focus_set()
            ent.select_range(0, tk.END)
        except Exception:
            pass

    def _ok(self) -> None:
        new_id = (self.var_id.get() or "").strip()
        if not new_id:
            messagebox.showerror("Missing", "EnumType id is required", parent=self)
            return
        self._result = new_id
        self.destroy()

    def _cancel(self) -> None:
        self._result = None
        self.destroy()

    def show(self) -> str | None:
        self.wait_window(self)
        return self._result


class _AfgNewDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        initial_name: str = "",
        initial_proxy: str = "",
        initial_chapter: str = "",
        initial_topic: str = "",
    ):
        super().__init__(parent)
        self.title("New AFG")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self._result: dict[str, str] | None = None

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text="name (file name is <name>.xml):").grid(row=0, column=0, sticky="w")
        self.var_name = tk.StringVar(value=initial_name)
        ent_name = ttk.Entry(frm, textvariable=self.var_name, width=48)
        ent_name.grid(row=0, column=1, sticky="we", padx=(8, 0))

        ttk.Label(frm, text="proxyName:").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.var_proxy = tk.StringVar(value=initial_proxy)
        ttk.Entry(frm, textvariable=self.var_proxy, width=48).grid(row=1, column=1, sticky="we", padx=(8, 0), pady=(8, 0))

        ttk.Label(frm, text="chapterName:").grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.var_chapter = tk.StringVar(value=initial_chapter)
        ttk.Entry(frm, textvariable=self.var_chapter, width=48).grid(row=2, column=1, sticky="we", padx=(8, 0), pady=(8, 0))

        ttk.Label(frm, text="topicName:").grid(row=3, column=0, sticky="w", pady=(8, 0))
        self.var_topic = tk.StringVar(value=initial_topic)
        ttk.Entry(frm, textvariable=self.var_topic, width=48).grid(row=3, column=1, sticky="we", padx=(8, 0), pady=(8, 0))

        btns = ttk.Frame(frm)
        btns.grid(row=4, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Cancel", command=self._cancel).pack(side="right")
        ttk.Button(btns, text="OK", command=self._ok).pack(side="right", padx=(0, 8))

        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Return>", lambda _e: self._ok())

        try:
            ent_name.focus_set()
            ent_name.select_range(0, tk.END)
        except Exception:
            pass

    def _ok(self) -> None:
        name = (self.var_name.get() or "").strip()
        if not name:
            messagebox.showerror("Missing", "AFG name is required", parent=self)
            return
        self._result = {
            "name": name,
            "proxyName": (self.var_proxy.get() or ""),
            "chapterName": (self.var_chapter.get() or ""),
            "topicName": (self.var_topic.get() or ""),
        }
        self.destroy()

    def _cancel(self) -> None:
        self._result = None
        self.destroy()

    def show(self) -> dict[str, str] | None:
        self.wait_window(self)
        return self._result


class _AfgSaveAsDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, *, initial_name: str = ""):
        super().__init__(parent)
        self.title("Save AFG as")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self._result: str | None = None

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="New AFG name (file name is <name>.xml):").grid(row=0, column=0, sticky="w")
        self.var_name = tk.StringVar(value=initial_name)
        ent = ttk.Entry(frm, textvariable=self.var_name, width=48)
        ent.grid(row=1, column=0, sticky="we", pady=(6, 0))
        frm.columnconfigure(0, weight=1)

        btns = ttk.Frame(frm)
        btns.grid(row=2, column=0, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Cancel", command=self._cancel).pack(side="right")
        ttk.Button(btns, text="OK", command=self._ok).pack(side="right", padx=(0, 8))

        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Return>", lambda _e: self._ok())

        try:
            ent.focus_set()
            ent.select_range(0, tk.END)
        except Exception:
            pass

    def _ok(self) -> None:
        new_name = (self.var_name.get() or "").strip()
        if not new_name:
            messagebox.showerror("Missing", "AFG name is required", parent=self)
            return
        self._result = new_name
        self.destroy()

    def _cancel(self) -> None:
        self._result = None
        self.destroy()

    def show(self) -> str | None:
        self.wait_window(self)
        return self._result


class _AfgFbEditDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, *, name: str, pos_x: str, pos_y: str):
        super().__init__(parent)
        self.title("Edit AFB")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self._result: dict[str, str] | None = None

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text="name:").grid(row=0, column=0, sticky="w")
        self.var_name = tk.StringVar(value=name)
        ent_name = ttk.Entry(frm, textvariable=self.var_name, width=40, state="readonly")
        ent_name.grid(row=0, column=1, sticky="we", padx=(8, 0))

        ttk.Label(frm, text="posX:").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.var_x = tk.StringVar(value=pos_x)
        ent_x = ttk.Entry(frm, textvariable=self.var_x, width=18)
        ent_x.grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(8, 0))

        ttk.Label(frm, text="posY:").grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.var_y = tk.StringVar(value=pos_y)
        ttk.Entry(frm, textvariable=self.var_y, width=18).grid(row=2, column=1, sticky="w", padx=(8, 0), pady=(8, 0))

        btns = ttk.Frame(frm)
        btns.grid(row=3, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Cancel", command=self._cancel).pack(side="right")
        ttk.Button(btns, text="OK", command=self._ok).pack(side="right", padx=(0, 8))

        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Return>", lambda _e: self._ok())

        try:
            # name is readonly; focus the first editable field
            ent_x.focus_set()
            ent_x.select_range(0, tk.END)
        except Exception:
            pass

    def _ok(self) -> None:
        self._result = {
            "name": (self.var_name.get() or "").strip(),
            "posX": (self.var_x.get() or "").strip(),
            "posY": (self.var_y.get() or "").strip(),
        }
        self.destroy()

    def _cancel(self) -> None:
        self._result = None
        self.destroy()

    def show(self) -> dict[str, str] | None:
        self.wait_window(self)
        return self._result


class _AfgInEditDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        name: str,
        pos_x: str,
        pos_y: str,
        src: str,
        do_ref: str,
        do_ref_values: list[str] | None,
        confpin: bool,
        softlink: bool,
    ):
        super().__init__(parent)
        self.title("Edit AFG Input")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self._result: dict[str, object] | None = None

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text="name:").grid(row=0, column=0, sticky="w")
        self.var_name = tk.StringVar(value=(name or ""))
        ent_name = ttk.Entry(frm, textvariable=self.var_name, width=40)
        ent_name.grid(row=0, column=1, sticky="we", padx=(8, 0))

        ttk.Label(frm, text="posX:").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.var_x = tk.StringVar(value=(pos_x or ""))
        ttk.Entry(frm, textvariable=self.var_x, width=18).grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(8, 0))

        ttk.Label(frm, text="posY:").grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.var_y = tk.StringVar(value=(pos_y or ""))
        ttk.Entry(frm, textvariable=self.var_y, width=18).grid(row=2, column=1, sticky="w", padx=(8, 0), pady=(8, 0))

        ttk.Label(frm, text="src:").grid(row=3, column=0, sticky="w", pady=(8, 0))
        self.var_src = tk.StringVar(value=(src or ""))
        ttk.Entry(frm, textvariable=self.var_src, width=60).grid(row=3, column=1, sticky="we", padx=(8, 0), pady=(8, 0))

        ttk.Label(frm, text="doRef:").grid(row=4, column=0, sticky="w", pady=(8, 0))
        self.var_do = tk.StringVar(value=(do_ref or ""))
        vals = list(do_ref_values or [])
        cur0 = (do_ref or "").strip()
        if cur0 and cur0 not in vals:
            vals = [cur0] + vals
        cb_do = ttk.Combobox(frm, textvariable=self.var_do, values=tuple(vals), width=58)
        cb_do.grid(row=4, column=1, sticky="we", padx=(8, 0), pady=(8, 0))

        self.var_confpin = tk.BooleanVar(value=bool(confpin))
        self.var_softlink = tk.BooleanVar(value=bool(softlink))
        cfrm = ttk.Frame(frm)
        cfrm.grid(row=5, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Checkbutton(cfrm, text="confpin", variable=self.var_confpin).pack(side="left")
        ttk.Checkbutton(cfrm, text="softlink", variable=self.var_softlink).pack(side="left", padx=(12, 0))

        btns = ttk.Frame(frm)
        btns.grid(row=6, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Cancel", command=self._cancel).pack(side="right")
        ttk.Button(btns, text="OK", command=self._ok).pack(side="right", padx=(0, 8))

        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Return>", lambda _e: self._ok())

        try:
            ent_name.focus_set()
            ent_name.select_range(0, tk.END)
        except Exception:
            pass

    def _ok(self) -> None:
        res = {
            "name": (self.var_name.get() or "").strip(),
            "posX": (self.var_x.get() or "").strip(),
            "posY": (self.var_y.get() or "").strip(),
            "src": (self.var_src.get() or "").strip(),
            "doRef": (self.var_do.get() or "").strip(),
            "confpin": bool(self.var_confpin.get()),
            "softlink": bool(self.var_softlink.get()),
        }
        if not res["name"]:
            messagebox.showerror("Missing", "name is required", parent=self)
            return
        self._result = res
        self.destroy()

    def _cancel(self) -> None:
        self._result = None
        self.destroy()

    def show(self) -> dict[str, object] | None:
        self.wait_window(self)
        return self._result


class _AfgOutEditDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        name: str,
        pos_x: str,
        pos_y: str,
        do_ref: str,
        do_ref_values_status: list[str] | None,
        do_ref_values_inref: list[str] | None,
        confpin: bool,
    ):
        super().__init__(parent)
        self.title("Edit AFG Output")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self._result: dict[str, object] | None = None

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text="name:").grid(row=0, column=0, sticky="w")
        self.var_name = tk.StringVar(value=(name or ""))
        ent_name = ttk.Entry(frm, textvariable=self.var_name, width=40)
        ent_name.grid(row=0, column=1, sticky="we", padx=(8, 0))

        ttk.Label(frm, text="posX:").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.var_x = tk.StringVar(value=(pos_x or ""))
        ttk.Entry(frm, textvariable=self.var_x, width=18).grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(8, 0))

        ttk.Label(frm, text="posY:").grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.var_y = tk.StringVar(value=(pos_y or ""))
        ttk.Entry(frm, textvariable=self.var_y, width=18).grid(row=2, column=1, sticky="w", padx=(8, 0), pady=(8, 0))

        ttk.Label(frm, text="doRef:").grid(row=3, column=0, sticky="w", pady=(8, 0))
        self.var_do = tk.StringVar(value=(do_ref or ""))
        cb_do = ttk.Combobox(frm, textvariable=self.var_do, width=58)
        cb_do.grid(row=3, column=1, sticky="we", padx=(8, 0), pady=(8, 0))

        self.var_confpin = tk.BooleanVar(value=bool(confpin))

        status_vals = list(do_ref_values_status or [])
        inref_vals = list(do_ref_values_inref or [])

        def apply_do_values() -> None:
            cur = (self.var_do.get() or "").strip()
            base = inref_vals if bool(self.var_confpin.get()) else status_vals
            vals = list(base)
            if cur and cur not in vals:
                vals = [cur] + vals
            try:
                cb_do.configure(values=tuple(vals))
            except Exception:
                try:
                    cb_do["values"] = tuple(vals)
                except Exception:
                    pass

        def on_confpin_toggle() -> None:
            apply_do_values()

        ttk.Checkbutton(frm, text="confpin", variable=self.var_confpin, command=on_confpin_toggle).grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(8, 0)
        )
        apply_do_values()

        btns = ttk.Frame(frm)
        btns.grid(row=5, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Cancel", command=self._cancel).pack(side="right")
        ttk.Button(btns, text="OK", command=self._ok).pack(side="right", padx=(0, 8))

        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Return>", lambda _e: self._ok())

        try:
            ent_name.focus_set()
            ent_name.select_range(0, tk.END)
        except Exception:
            pass

    def _ok(self) -> None:
        res = {
            "name": (self.var_name.get() or "").strip(),
            "posX": (self.var_x.get() or "").strip(),
            "posY": (self.var_y.get() or "").strip(),
            "doRef": (self.var_do.get() or "").strip(),
            "confpin": bool(self.var_confpin.get()),
        }
        if not res["name"]:
            messagebox.showerror("Missing", "name is required", parent=self)
            return
        self._result = res
        self.destroy()

    def _cancel(self) -> None:
        self._result = None
        self.destroy()

    def show(self) -> dict[str, object] | None:
        self.wait_window(self)
        return self._result


class _AfgArrowEditDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        title: str,
        start_owners: list[str],
        start_pins_by_owner: dict[str, list[tuple[str, str]]],
        start_owner_for_pin: dict[str, str],
        end_owners: list[str],
        end_pins_by_owner: dict[str, list[tuple[str, str]]],
        end_owner_for_pin: dict[str, str],
        start_pin_id: str,
        end_pin_id: str,
    ):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self._result: dict[str, str] | None = None
        self._start_pins_by_owner = start_pins_by_owner
        self._start_owner_for_pin = start_owner_for_pin
        self._end_pins_by_owner = end_pins_by_owner
        self._end_owner_for_pin = end_owner_for_pin

        # Display label -> pinID mapping for each side is rebuilt whenever owner changes.
        self._start_label_to_pid: dict[str, str] = {}
        self._end_label_to_pid: dict[str, str] = {}

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)
        frm.columnconfigure(3, weight=1)

        # Start side
        ttk.Label(frm, text="start:").grid(row=0, column=0, sticky="w")
        self.var_start_owner = tk.StringVar(value="")
        self.var_start_pin = tk.StringVar(value="")
        cb_start_owner = ttk.Combobox(frm, textvariable=self.var_start_owner, values=tuple(start_owners), width=34)
        cb_start_owner.grid(row=0, column=1, sticky="we", padx=(8, 12))
        cb_start_pin = ttk.Combobox(frm, textvariable=self.var_start_pin, values=(), width=48)
        cb_start_pin.grid(row=0, column=2, columnspan=2, sticky="we")

        # End side
        ttk.Label(frm, text="end:").grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.var_end_owner = tk.StringVar(value="")
        self.var_end_pin = tk.StringVar(value="")
        cb_end_owner = ttk.Combobox(frm, textvariable=self.var_end_owner, values=tuple(end_owners), width=34)
        cb_end_owner.grid(row=1, column=1, sticky="we", padx=(8, 12), pady=(10, 0))
        cb_end_pin = ttk.Combobox(frm, textvariable=self.var_end_pin, values=(), width=48)
        cb_end_pin.grid(row=1, column=2, columnspan=2, sticky="we", pady=(10, 0))

        btns = ttk.Frame(frm)
        btns.grid(row=2, column=0, columnspan=4, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Cancel", command=self._cancel).pack(side="right")
        ttk.Button(btns, text="OK", command=self._ok).pack(side="right", padx=(0, 8))

        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Return>", lambda _e: self._ok())

        def _apply_owner(
            *,
            owner_var: tk.StringVar,
            pin_var: tk.StringVar,
            pin_cb: ttk.Combobox,
            label_to_pid: dict[str, str],
            initial_pin_id: str,
            pins_by_owner: dict[str, list[tuple[str, str]]],
        ) -> None:
            owner = (owner_var.get() or "").strip()
            items = list(pins_by_owner.get(owner, []))

            label_to_pid.clear()
            labels: list[str] = []
            for pid, label in items:
                p = (pid or "").strip()
                if not p:
                    continue
                l = (label or "").strip() or p
                # Ensure uniqueness in UI; fall back to including pid.
                if l in label_to_pid and label_to_pid[l] != p:
                    l = f"{l} ({p})"
                label_to_pid[l] = p
                labels.append(l)

            try:
                pin_cb.configure(values=tuple(labels))
            except Exception:
                try:
                    pin_cb["values"] = tuple(labels)
                except Exception:
                    pass

            # Select initial pin if it belongs to this owner; else select the first.
            target_pid = (initial_pin_id or "").strip()
            if target_pid:
                for l, p in label_to_pid.items():
                    if p == target_pid:
                        pin_var.set(l)
                        return
            if labels:
                pin_var.set(labels[0])
            else:
                pin_var.set("")

        def _on_start_owner_change(*_a) -> None:
            _apply_owner(
                owner_var=self.var_start_owner,
                pin_var=self.var_start_pin,
                pin_cb=cb_start_pin,
                label_to_pid=self._start_label_to_pid,
                initial_pin_id="",
                pins_by_owner=self._start_pins_by_owner,
            )

        def _on_end_owner_change(*_a) -> None:
            _apply_owner(
                owner_var=self.var_end_owner,
                pin_var=self.var_end_pin,
                pin_cb=cb_end_pin,
                label_to_pid=self._end_label_to_pid,
                initial_pin_id="",
                pins_by_owner=self._end_pins_by_owner,
            )

        try:
            self.var_start_owner.trace_add("write", _on_start_owner_change)
            self.var_end_owner.trace_add("write", _on_end_owner_change)
        except Exception:
            pass

        # Initialize selection based on existing pinIDs.
        sp0 = (start_pin_id or "").strip()
        ep0 = (end_pin_id or "").strip()
        start_owner0 = self._start_owner_for_pin.get(sp0, "")
        end_owner0 = self._end_owner_for_pin.get(ep0, "")

        if start_owner0 and start_owner0 in start_owners:
            self.var_start_owner.set(start_owner0)
        elif start_owners:
            self.var_start_owner.set(start_owners[0])
        _apply_owner(
            owner_var=self.var_start_owner,
            pin_var=self.var_start_pin,
            pin_cb=cb_start_pin,
            label_to_pid=self._start_label_to_pid,
            initial_pin_id=sp0,
            pins_by_owner=self._start_pins_by_owner,
        )

        if end_owner0 and end_owner0 in end_owners:
            self.var_end_owner.set(end_owner0)
        elif end_owners:
            self.var_end_owner.set(end_owners[0])
        _apply_owner(
            owner_var=self.var_end_owner,
            pin_var=self.var_end_pin,
            pin_cb=cb_end_pin,
            label_to_pid=self._end_label_to_pid,
            initial_pin_id=ep0,
            pins_by_owner=self._end_pins_by_owner,
        )

        try:
            cb_start_owner.focus_set()
        except Exception:
            pass

    def _ok(self) -> None:
        sp_label = (self.var_start_pin.get() or "").strip()
        ep_label = (self.var_end_pin.get() or "").strip()
        sp = (self._start_label_to_pid.get(sp_label) or "").strip()
        ep = (self._end_label_to_pid.get(ep_label) or "").strip()
        if not sp or not ep:
            messagebox.showerror("Missing", "start and end pin are required", parent=self)
            return
        if sp == ep:
            messagebox.showerror("Invalid", "start and end pin must be different", parent=self)
            return
        self._result = {"startPinID": sp, "endPinID": ep}
        self.destroy()

    def _cancel(self) -> None:
        self._result = None
        self.destroy()

    def show(self) -> dict[str, str] | None:
        self.wait_window(self)
        return self._result


class _HmiMenuEditDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        title: str,
        name: str,
        desc: str,
        data_type: str,
        view_type: str,
    ):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self._result: dict[str, str] | None = None
        self.var_name = tk.StringVar(value=name or "")
        self.var_desc = tk.StringVar(value=desc or "")
        self.var_data = tk.StringVar(value=data_type or "")
        self.var_view = tk.StringVar(value=view_type or "")

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text="name").grid(row=0, column=0, sticky="w")
        ent_name = ttk.Entry(frm, textvariable=self.var_name, width=54)
        ent_name.grid(row=0, column=1, sticky="we", padx=(8, 0))

        ttk.Label(frm, text="desc").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(frm, textvariable=self.var_desc, width=54).grid(row=1, column=1, sticky="we", padx=(8, 0), pady=(8, 0))

        ttk.Label(frm, text="hmiMenuDataType").grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(frm, textvariable=self.var_data, width=54).grid(row=2, column=1, sticky="we", padx=(8, 0), pady=(8, 0))

        ttk.Label(frm, text="hmiMenuViewType").grid(row=3, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(frm, textvariable=self.var_view, width=54).grid(row=3, column=1, sticky="we", padx=(8, 0), pady=(8, 0))

        btns = ttk.Frame(frm)
        btns.grid(row=4, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Cancel", command=self._cancel).pack(side="right")
        ttk.Button(btns, text="OK", command=self._ok).pack(side="right", padx=(0, 8))

        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Return>", lambda _e: self._ok())
        try:
            ent_name.focus_set()
            ent_name.select_range(0, tk.END)
        except Exception:
            pass

    def _ok(self) -> None:
        name = (self.var_name.get() or "").strip()
        if not name:
            messagebox.showerror("Missing", "name is required", parent=self)
            return
        self._result = {
            "name": name,
            "desc": self.var_desc.get() or "",
            "hmiMenuDataType": (self.var_data.get() or "").strip(),
            "hmiMenuViewType": (self.var_view.get() or "").strip(),
        }
        self.destroy()

    def _cancel(self) -> None:
        self._result = None
        self.destroy()

    def show(self) -> dict[str, str] | None:
        self.wait_window(self)
        return self._result


class _HmiMenuItemEditDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        title: str,
        name: str,
        ref: str,
        do_ref: str,
        da_ref: str,
    ):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self._result: dict[str, str] | None = None
        self.var_name = tk.StringVar(value=name or "")
        self.var_ref = tk.StringVar(value=ref or "")
        self.var_do = tk.StringVar(value=do_ref or "")
        self.var_da = tk.StringVar(value=da_ref or "")

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text="name").grid(row=0, column=0, sticky="w")
        ent_name = ttk.Entry(frm, textvariable=self.var_name, width=60)
        ent_name.grid(row=0, column=1, sticky="we", padx=(8, 0))

        ttk.Label(frm, text="ref (optional)").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(frm, textvariable=self.var_ref, width=60).grid(row=1, column=1, sticky="we", padx=(8, 0), pady=(8, 0))

        ttk.Label(frm, text="doRef").grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(frm, textvariable=self.var_do, width=60).grid(row=2, column=1, sticky="we", padx=(8, 0), pady=(8, 0))

        ttk.Label(frm, text="daRef").grid(row=3, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(frm, textvariable=self.var_da, width=60).grid(row=3, column=1, sticky="we", padx=(8, 0), pady=(8, 0))

        ttk.Label(frm, text="If ref is set, name/doRef/daRef are ignored.").grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(8, 0)
        )

        btns = ttk.Frame(frm)
        btns.grid(row=5, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Cancel", command=self._cancel).pack(side="right")
        ttk.Button(btns, text="OK", command=self._ok).pack(side="right", padx=(0, 8))

        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Return>", lambda _e: self._ok())
        try:
            ent_name.focus_set()
            ent_name.select_range(0, tk.END)
        except Exception:
            pass

    def _ok(self) -> None:
        ref = (self.var_ref.get() or "").strip()
        name = (self.var_name.get() or "").strip()
        if not ref and not name:
            messagebox.showerror("Missing", "Either ref or name is required", parent=self)
            return
        self._result = {
            "ref": ref,
            "name": name,
            "doRef": (self.var_do.get() or "").strip(),
            "daRef": (self.var_da.get() or "").strip(),
        }
        self.destroy()

    def _cancel(self) -> None:
        self._result = None
        self.destroy()

    def show(self) -> dict[str, str] | None:
        self.wait_window(self)
        return self._result


class _HmiDataItemEditDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        title: str,
        name: str,
        do_ref: str,
        da_ref: str,
    ):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self._result: dict[str, str] | None = None
        self.var_name = tk.StringVar(value=name or "")
        self.var_do = tk.StringVar(value=do_ref or "")
        self.var_da = tk.StringVar(value=da_ref or "")

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text="name").grid(row=0, column=0, sticky="w")
        ent_name = ttk.Entry(frm, textvariable=self.var_name, width=54)
        ent_name.grid(row=0, column=1, sticky="we", padx=(8, 0))

        ttk.Label(frm, text="doRef").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(frm, textvariable=self.var_do, width=54).grid(row=1, column=1, sticky="we", padx=(8, 0), pady=(8, 0))

        ttk.Label(frm, text="daRef").grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(frm, textvariable=self.var_da, width=54).grid(row=2, column=1, sticky="we", padx=(8, 0), pady=(8, 0))

        btns = ttk.Frame(frm)
        btns.grid(row=3, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Cancel", command=self._cancel).pack(side="right")
        ttk.Button(btns, text="OK", command=self._ok).pack(side="right", padx=(0, 8))

        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Return>", lambda _e: self._ok())
        try:
            ent_name.focus_set()
            ent_name.select_range(0, tk.END)
        except Exception:
            pass

    def _ok(self) -> None:
        name = (self.var_name.get() or "").strip()
        if not name:
            messagebox.showerror("Missing", "name is required", parent=self)
            return
        self._result = {
            "name": name,
            "doRef": (self.var_do.get() or "").strip(),
            "daRef": (self.var_da.get() or "").strip(),
        }
        self.destroy()

    def _cancel(self) -> None:
        self._result = None
        self.destroy()

    def show(self) -> dict[str, str] | None:
        self.wait_window(self)
        return self._result


class _AfgArrowGraphDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        title: str,
        root: ET.Element,
        on_select_arrow: Callable[[ET.Element | None], None] | None = None,
        on_arrows_changed: Callable[[ET.Element | None], None] | None = None,
        on_close: Callable[[], None] | None = None,
        initial_selected: ET.Element | None = None,
    ):
        super().__init__(parent)
        self.title(title)
        self.resizable(True, True)
        # Keep this window modeless and allow the main window to come
        # in front when it gets focus (Windows transient dialogs often
        # stay above their parent, which is not desired here).
        try:
            self.transient(None)
        except Exception:
            pass

        # A reasonable default size; scrollbars handle larger diagrams.
        try:
            self.geometry("1200x720")
        except Exception:
            pass

        outer = ttk.Frame(self, padding=8)
        outer.pack(fill="both", expand=True)
        outer.rowconfigure(1, weight=1)
        outer.columnconfigure(0, weight=1)

        # NOTE: don't use attribute name `_root` here; Tkinter widgets have an internal `_root()` method.
        # If we shadow it with an Element, Tk's exception reporter will crash.
        self._xml_root = root

        self._pin_map: dict[str, str] = {}
        self._pin_role: dict[str, str] = {}  # pinID -> "start" | "end"
        self._pin_id_by_canvas_item: dict[int, str] = {}
        self._anchor_by_pin_id: dict[str, tuple[float, float]] = {}
        self._arrow_by_line_id: dict[int, tuple[str, str]] = {}
        self._arrow_el_by_line_id: dict[int, ET.Element] = {}
        self._arrow_line_style: dict[int, dict[str, object]] = {}
        self._selected_line_id: int | None = None
        self._info_var = tk.StringVar(value="")
        self._on_select_arrow = on_select_arrow
        self._on_arrows_changed = on_arrows_changed
        self._on_close_cb = on_close
        self._closed = False
        self._syncing = False

        self._connect_var = tk.BooleanVar(value=False)
        self._connect_start_pid: str | None = None
        self._rubber_line_id: int | None = None
        self._drag_moved: bool = False
        self._press_xy: tuple[float, float] | None = None
        self._ctx_menu: tk.Menu | None = None

        toolbar = ttk.Frame(outer)
        toolbar.grid(row=0, column=0, columnspan=2, sticky="we", pady=(0, 6))
        try:
            btn_conn = ttk.Checkbutton(
                toolbar,
                text="Connection",
                variable=self._connect_var,
                style="Toolbutton",
                command=self._on_connection_toggle,
            )
        except Exception:
            btn_conn = ttk.Checkbutton(toolbar, text="Connection", variable=self._connect_var, command=self._on_connection_toggle)
        btn_conn.pack(side="left")

        self.canvas = tk.Canvas(outer, highlightthickness=0)
        vsb = ttk.Scrollbar(outer, orient="vertical", command=self.canvas.yview)
        hsb = ttk.Scrollbar(outer, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.canvas.grid(row=1, column=0, sticky="nsew")
        vsb.grid(row=1, column=1, sticky="ns")
        hsb.grid(row=2, column=0, sticky="we")

        info = ttk.Label(outer, textvariable=self._info_var, anchor="w")
        info.grid(row=3, column=0, columnspan=2, sticky="we", pady=(6, 0))

        btns = ttk.Frame(outer)
        btns.grid(row=4, column=0, columnspan=2, sticky="e", pady=(8, 0))
        ttk.Button(btns, text="Close", command=self._close).pack(side="right")

        self.bind("<Escape>", lambda _e: self._close())
        self.bind("<Delete>", lambda _e: self._delete_selected_arrow())
        self.bind("<BackSpace>", lambda _e: self._delete_selected_arrow())

        # Mousewheel scrolling + click selection
        try:
            self.canvas.bind("<Enter>", lambda _e: self.canvas.focus_set())
            self.canvas.bind("<MouseWheel>", self._on_mousewheel)
            self.canvas.bind("<Shift-MouseWheel>", self._on_shift_mousewheel)
            self.canvas.bind("<Control-MouseWheel>", self._on_ctrl_mousewheel)
            self.canvas.bind("<ButtonPress-1>", self._on_canvas_press)
            self.canvas.bind("<B1-Motion>", self._on_canvas_drag)
            self.canvas.bind("<ButtonRelease-1>", self._on_canvas_release)
            self.canvas.bind("<Button-3>", self._on_canvas_right_click)
        except Exception:
            pass

        # Draw once.
        try:
            self._draw_graph(root)
        except Exception as e:
            messagebox.showerror("Graph", str(e), parent=self)
            try:
                self.destroy()
            except Exception:
                pass
            return

        # Apply initial selection if requested.
        if initial_selected is not None:
            try:
                self.highlight_arrow_element(initial_selected)
            except Exception:
                pass

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
            self.update_idletasks()
            sw = int(self.winfo_width())
            sh = int(self.winfo_height())
        except Exception:
            sw = sh = 0

        if pw > 0 and ph > 0 and sw > 0 and sh > 0:
            x = px + max(0, (pw - sw) // 2)
            y = py + max(0, (ph - sh) // 2)
            self.geometry(f"+{x}+{y}")

    def _draw_graph(self, root: ET.Element) -> None:
        c = self.canvas
        c.delete("all")

        # Any in-progress drag visuals are removed by delete('all').
        self._rubber_line_id = None

        grid_color = "gray70"

        def get_child(el: ET.Element, local: str) -> ET.Element | None:
            for ch in list(el):
                if isinstance(ch.tag, str) and _local_name(ch.tag) == local:
                    return ch
            return None

        def iter_children(el: ET.Element | None, local: str) -> list[ET.Element]:
            if el is None:
                return []
            return [x for x in list(el) if isinstance(x.tag, str) and _local_name(x.tag) == local]

        def build_pin_map() -> dict[str, str]:
            out: dict[str, str] = {}

            def add(pid: str | None, label: str) -> None:
                p = (pid or "").strip()
                if not p:
                    return
                out[p] = label

            for it in iter_children(get_child(root, "afgInItems"), "afgInItem"):
                n = (it.attrib.get("name") or "").strip()
                add(it.attrib.get("pinID"), f"AFG_IN:{n}" if n else "AFG_IN")

            for it in iter_children(get_child(root, "afgOutItems"), "afgOutItem"):
                n = (it.attrib.get("name") or "").strip()
                add(it.attrib.get("pinID"), f"AFG_OUT:{n}" if n else "AFG_OUT")

            for fb in iter_children(get_child(root, "fbItems"), "fbItem"):
                fb_name = (fb.attrib.get("name") or "").strip()
                inputs_el = get_child(fb, "Inputs")
                outputs_el = get_child(fb, "Outputs")

                for it in iter_children(inputs_el, "Input"):
                    n = (it.attrib.get("name") or "").strip()
                    add(it.attrib.get("pinID"), f"{fb_name}:In:{n}" if fb_name else f"In:{n}")

                for it in iter_children(outputs_el, "Output"):
                    n = (it.attrib.get("name") or "").strip()
                    add(it.attrib.get("pinID"), f"{fb_name}:Out:{n}" if fb_name else f"Out:{n}")

            return out

        # Collect pins in document order.
        afg_in: list[tuple[str, str]] = []
        for it in iter_children(get_child(root, "afgInItems"), "afgInItem"):
            pid = (it.attrib.get("pinID") or "").strip()
            if not pid:
                continue
            name = (it.attrib.get("name") or "").strip() or pid
            afg_in.append((pid, name))

        afg_out: list[tuple[str, str]] = []
        for it in iter_children(get_child(root, "afgOutItems"), "afgOutItem"):
            pid = (it.attrib.get("pinID") or "").strip()
            if not pid:
                continue
            name = (it.attrib.get("name") or "").strip() or pid
            afg_out.append((pid, name))

        afbs: list[dict[str, object]] = []
        for fb in iter_children(get_child(root, "fbItems"), "fbItem"):
            fb_name = (fb.attrib.get("name") or "").strip() or "(no name)"
            inputs_el = get_child(fb, "Inputs")
            outputs_el = get_child(fb, "Outputs")

            in_pins: list[tuple[str, str]] = []
            for it in iter_children(inputs_el, "Input"):
                pid = (it.attrib.get("pinID") or "").strip()
                if not pid:
                    continue
                name = (it.attrib.get("name") or "").strip() or pid
                in_pins.append((pid, name))

            out_pins: list[tuple[str, str]] = []
            for it in iter_children(outputs_el, "Output"):
                pid = (it.attrib.get("pinID") or "").strip()
                if not pid:
                    continue
                name = (it.attrib.get("name") or "").strip() or pid
                out_pins.append((pid, name))

            afbs.append({"name": fb_name, "inputs": in_pins, "outputs": out_pins})

        arrows: list[tuple[ET.Element, str, str, str]] = []
        arrows_el = get_child(root, "arrows")
        for ar in iter_children(arrows_el, "arrowItem"):
            sp = (ar.attrib.get("startPinID") or "").strip()
            ep = (ar.attrib.get("endPinID") or "").strip()
            col = (ar.attrib.get("lineColor") or "").strip()
            arrows.append((ar, sp, ep, col))

        # Layout constants (pixels)
        margin_x = 40
        margin_y = 30
        title_h = 22
        row_h = 44
        gap_x = 36
        list_w = 102
        block_w = 156
        header_h = 26
        pad = 8

        x_afg_in = margin_x
        x_afb0 = x_afg_in + list_w + gap_x
        x_afg_out = x_afb0 + (block_w + gap_x) * max(0, len(afbs))

        # pinID -> (x, y) anchor mapping for line endpoints
        anchors: dict[str, tuple[float, float]] = {}

        self._pin_map = build_pin_map()
        self._pin_role = {}
        self._pin_id_by_canvas_item = {}
        self._arrow_by_line_id = {}
        self._arrow_el_by_line_id = {}
        self._arrow_line_style = {}
        self._selected_line_id = None
        if bool(self._connect_var.get()):
            if self._connect_start_pid:
                sp = self._connect_start_pid
                self._info_var.set(f"Connection mode: start selected {sp} {self._pin_map.get(sp, '')}  -> select end")
            else:
                self._info_var.set("Connection mode: click start-pin then end-pin")
        else:
            self._info_var.set("")

        def draw_list(*, title: str, x: int, y: int, w: int, items: list[tuple[str, str]], side: str, role: str) -> int:
            # side: "left" (connections originate to right) or "right" (connections terminate from left)
            c.create_text(x, y, text=title, anchor="nw")
            box_y0 = y + title_h
            box_h = max(1, len(items)) * row_h + pad * 2
            c.create_rectangle(x, box_y0, x + w, box_y0 + box_h)

            for i, (pid, name) in enumerate(items):
                ty = box_y0 + pad + i * row_h
                # Row box (grid)
                rid = c.create_rectangle(x, ty, x + w, ty + row_h, outline=grid_color, tags=("pin",))
                tid = c.create_text(x + pad, ty + row_h / 2, text=name, anchor="w", tags=("pin",))
                if side == "left":
                    ax = x + w - 2
                else:
                    ax = x + 2
                anchors[pid] = (ax, ty + row_h / 2)
                self._pin_role[pid] = role
                self._pin_id_by_canvas_item[int(rid)] = pid
                self._pin_id_by_canvas_item[int(tid)] = pid
                # Small anchor dot
                try:
                    did = c.create_oval(
                        ax - 2,
                        (ty + row_h / 2) - 2,
                        ax + 2,
                        (ty + row_h / 2) + 2,
                        fill="black",
                        outline="",
                        tags=("pin",),
                    )
                    self._pin_id_by_canvas_item[int(did)] = pid
                except Exception:
                    pass

            return box_y0 + box_h

        def draw_afb(*, fb: dict[str, object], x: int, y: int) -> int:
            fb_name = str(fb.get("name") or "")
            inputs: list[tuple[str, str]] = list(fb.get("inputs") or [])
            outputs: list[tuple[str, str]] = list(fb.get("outputs") or [])

            pins_h = max(1, max(len(inputs), len(outputs))) * row_h + pad * 2
            h = header_h + pins_h
            c.create_rectangle(x, y, x + block_w, y + h)
            c.create_text(x + pad, y + pad, text=fb_name, anchor="nw")

            # Column headers
            c.create_text(x + pad, y + header_h - 8, text="Inputs", anchor="sw", fill="gray30")
            c.create_text(x + block_w - pad, y + header_h - 8, text="Outputs", anchor="se", fill="gray30")

            # Vertical divider (between input/output columns)
            mid_x = x + block_w / 2
            c.create_line(mid_x, y + header_h, mid_x, y + h)

            # Horizontal grid lines across the pins area
            rows = max(1, max(len(inputs), len(outputs)))
            for i in range(rows + 1):
                yy = y + header_h + pad + i * row_h
                c.create_line(x, yy, x + block_w, yy, fill=grid_color)

            # Inputs on left
            for i, (pid, name) in enumerate(inputs):
                ty = y + header_h + pad + i * row_h
                # Row box for left half
                rid = c.create_rectangle(x, ty, mid_x, ty + row_h, outline=grid_color, tags=("pin",))
                tid = c.create_text(x + pad, ty + row_h / 2, text=name, anchor="w", tags=("pin",))
                ax, ay = (x + 2, ty + row_h / 2)
                anchors[pid] = (ax, ay)
                self._pin_role[pid] = "end"
                self._pin_id_by_canvas_item[int(rid)] = pid
                self._pin_id_by_canvas_item[int(tid)] = pid
                try:
                    did = c.create_oval(ax - 2, ay - 2, ax + 2, ay + 2, fill="black", outline="", tags=("pin",))
                    self._pin_id_by_canvas_item[int(did)] = pid
                except Exception:
                    pass

            # Outputs on right
            for i, (pid, name) in enumerate(outputs):
                ty = y + header_h + pad + i * row_h
                # Row box for right half
                rid = c.create_rectangle(mid_x, ty, x + block_w, ty + row_h, outline=grid_color, tags=("pin",))
                tid = c.create_text(x + block_w - pad, ty + row_h / 2, text=name, anchor="e", tags=("pin",))
                ax, ay = (x + block_w - 2, ty + row_h / 2)
                anchors[pid] = (ax, ay)
                self._pin_role[pid] = "start"
                self._pin_id_by_canvas_item[int(rid)] = pid
                self._pin_id_by_canvas_item[int(tid)] = pid
                try:
                    did = c.create_oval(ax - 2, ay - 2, ax + 2, ay + 2, fill="black", outline="", tags=("pin",))
                    self._pin_id_by_canvas_item[int(did)] = pid
                except Exception:
                    pass

            return y + h

        # Draw columns
        y0 = margin_y
        y_max = y0
        y_max = max(
            y_max,
            draw_list(title="AFG Inputs", x=x_afg_in, y=y0, w=list_w, items=afg_in, side="left", role="start"),
        )
        y_max = max(
            y_max,
            draw_list(title="AFG Outputs", x=int(x_afg_out), y=y0, w=list_w, items=afg_out, side="right", role="end"),
        )

        # Persist anchors for interactive dragging.
        self._anchor_by_pin_id = dict(anchors)

        for i, fb in enumerate(afbs):
            xb = int(x_afb0 + i * (block_w + gap_x))
            y_max = max(y_max, draw_afb(fb=fb, x=xb, y=y0))

        # Draw arrows (straight lines) and keep them behind nodes.
        missing: list[str] = []
        for ar_el, sp, ep, col in arrows:
            if not sp or not ep:
                continue
            p1 = anchors.get(sp)
            p2 = anchors.get(ep)
            if p1 is None or p2 is None:
                if p1 is None:
                    missing.append(sp)
                if p2 is None:
                    missing.append(ep)
                continue
            x1, y1 = p1
            x2, y2 = p2
            color = col if col else "#000000"
            try:
                line_id = c.create_line(
                    x1,
                    y1,
                    x2,
                    y2,
                    fill=color,
                    width=1,
                    arrow="last",
                    tags=("arrow", "arrowline"),
                )
            except Exception:
                line_id = c.create_line(
                    x1,
                    y1,
                    x2,
                    y2,
                    fill=color,
                    width=1,
                    arrow="last",
                    tags=("arrow", "arrowline"),
                )

            self._arrow_by_line_id[int(line_id)] = (sp, ep)
            self._arrow_el_by_line_id[int(line_id)] = ar_el
            self._arrow_line_style[int(line_id)] = {"fill": color, "width": 1}

        try:
            c.tag_lower("arrow")
        except Exception:
            pass

        # Expand scroll region
        total_w = int(x_afg_out + list_w + margin_x)
        total_h = int(y_max + margin_y)
        c.configure(scrollregion=(0, 0, max(1, total_w), max(1, total_h)))

        if missing:
            uniq = []
            seen = set()
            for p in missing:
                if p not in seen:
                    uniq.append(p)
                    seen.add(p)
            msg = "Some arrows reference missing pinID(s):\n" + "\n".join(uniq[:30])
            if len(uniq) > 30:
                msg += f"\n... ({len(uniq) - 30} more)"
            messagebox.showwarning("Missing pins", msg, parent=self)

    def _on_mousewheel(self, e: tk.Event) -> None:
        # Windows: event.delta is multiple of 120.
        try:
            delta = int(getattr(e, "delta", 0) or 0)
        except Exception:
            delta = 0
        if delta == 0:
            return
        units = -1 * (delta // 120) if abs(delta) >= 120 else (-1 if delta > 0 else 1)
        try:
            self.canvas.yview_scroll(units, "units")
        except Exception:
            pass

    def _on_shift_mousewheel(self, e: tk.Event) -> None:
        try:
            delta = int(getattr(e, "delta", 0) or 0)
        except Exception:
            delta = 0
        if delta == 0:
            return
        units = -1 * (delta // 120) if abs(delta) >= 120 else (-1 if delta > 0 else 1)
        try:
            self.canvas.xview_scroll(units, "units")
        except Exception:
            pass

    def _on_ctrl_mousewheel(self, e: tk.Event) -> None:
        # Alternative horizontal scroll shortcut.
        self._on_shift_mousewheel(e)

    def _get_child(self, el: ET.Element, local: str) -> ET.Element | None:
        for ch in list(el):
            if isinstance(ch.tag, str) and _local_name(ch.tag) == local:
                return ch
        return None

    def _get_or_create_child(self, el: ET.Element, local: str) -> ET.Element:
        ch = self._get_child(el, local)
        if ch is not None:
            return ch
        new_el = ET.Element(local)
        el.append(new_el)
        return new_el

    def _on_connection_toggle(self) -> None:
        if not bool(self._connect_var.get()):
            self._connect_start_pid = None
            self._clear_rubber_line()
            self._info_var.set("")
            return
        self._connect_start_pid = None
        self._clear_rubber_line()
        self._info_var.set("Connection mode: click start-pin then end-pin")

    def _clear_rubber_line(self) -> None:
        if self._rubber_line_id is None:
            return
        try:
            self.canvas.delete(self._rubber_line_id)
        except Exception:
            pass
        self._rubber_line_id = None

    def _pick_pin_at(self, x: float, y: float) -> str | None:
        c = self.canvas
        try:
            hits = list(c.find_overlapping(x - 4, y - 4, x + 4, y + 4))
        except Exception:
            hits = []
        for hid in hits:
            pid = self._pin_id_by_canvas_item.get(int(hid))
            if pid:
                return pid
        try:
            closest = c.find_closest(x, y)
        except Exception:
            closest = ()
        if closest:
            pid = self._pin_id_by_canvas_item.get(int(closest[0]))
            if pid:
                return pid
        return None

    def _pick_line_at(self, x: float, y: float) -> int | None:
        c = self.canvas
        try:
            hits = list(c.find_overlapping(x - 4, y - 4, x + 4, y + 4))
        except Exception:
            hits = []

        for hid in hits:
            try:
                tags = set(c.gettags(hid))
            except Exception:
                tags = set()
            if "arrowline" in tags:
                return int(hid)

        try:
            closest = c.find_closest(x, y)
        except Exception:
            closest = ()
        if closest:
            hid = int(closest[0])
            try:
                tags = set(c.gettags(hid))
            except Exception:
                tags = set()
            if "arrowline" in tags:
                return hid
        return None

    def _add_arrow(self, start_pin_id: str, end_pin_id: str) -> tuple[ET.Element, bool]:
        arrows_el = self._get_or_create_child(self._xml_root, "arrows")
        for ar in list(arrows_el):
            if not (isinstance(ar.tag, str) and _local_name(ar.tag) == "arrowItem"):
                continue
            sp = (ar.attrib.get("startPinID") or "").strip()
            ep = (ar.attrib.get("endPinID") or "").strip()
            if sp == start_pin_id and ep == end_pin_id:
                return ar, False

        new_el = ET.Element(
            "arrowItem",
            attrib={
                "startPinID": start_pin_id,
                "endPinID": end_pin_id,
                "zValue": "-1000.000000",
                "lineColor": "#000000",
            },
        )
        arrows_el.append(new_el)
        return new_el, True

    def _remove_arrow_element(self, arrow_el: ET.Element) -> bool:
        arrows_el = self._get_child(self._xml_root, "arrows")
        if arrows_el is None:
            return False
        try:
            arrows_el.remove(arrow_el)
            return True
        except Exception:
            return False

    def _complete_connection(self, start_pid: str, end_pid: str) -> None:
        sp = (start_pid or "").strip()
        ep = (end_pid or "").strip()
        if not sp or not ep:
            self._info_var.set("Connection mode: click start-pin then end-pin")
            return
        if sp == ep:
            self._info_var.set("Start and end cannot be the same")
            return

        new_el, created = self._add_arrow(sp, ep)
        if not created:
            self._info_var.set("Connection already exists")
        elif self._on_arrows_changed is not None:
            try:
                self._on_arrows_changed(new_el)
            except Exception:
                pass

        try:
            self._draw_graph(self._xml_root)
            self.highlight_arrow_element(new_el)
        except Exception:
            pass

    def _on_canvas_click(self, e: tk.Event) -> None:
        # Backward compatibility: treat as press+release.
        try:
            self._on_canvas_press(e)
            self._on_canvas_release(e)
        except Exception:
            pass

    def _on_canvas_press(self, e: tk.Event) -> None:
        c = self.canvas
        try:
            x = float(c.canvasx(e.x))
            y = float(c.canvasy(e.y))
        except Exception:
            return
        self._press_xy = (x, y)
        self._drag_moved = False

        if not bool(self._connect_var.get()):
            return

        pid = self._pick_pin_at(x, y)
        if not pid:
            return

        role = self._pin_role.get(pid)
        if self._connect_start_pid is not None:
            if role == "end":
                self._clear_rubber_line()
                sp = self._connect_start_pid
                self._connect_start_pid = None
                self._complete_connection(sp, pid)
                return
            if role == "start":
                self._connect_start_pid = pid
            else:
                return
        else:
            if role != "start":
                self._info_var.set("Start must be AFG input or AFB output")
                return
            self._connect_start_pid = pid

        # Start rubber-band line from the start pin anchor.
        self._clear_rubber_line()
        ax, ay = self._anchor_by_pin_id.get(pid, (x, y))
        try:
            self._rubber_line_id = int(
                c.create_line(ax, ay, x, y, fill="red", width=2, dash=(4, 3), tags=("rubber",))
            )
        except Exception:
            self._rubber_line_id = None
        self._info_var.set(f"Connection mode: start selected {pid} {self._pin_map.get(pid, '')}  -> select end")

    def _on_canvas_drag(self, e: tk.Event) -> None:
        if not bool(self._connect_var.get()):
            return
        if self._rubber_line_id is None:
            return
        c = self.canvas
        try:
            x = float(c.canvasx(e.x))
            y = float(c.canvasy(e.y))
        except Exception:
            return
        self._drag_moved = True
        sp = self._connect_start_pid
        if not sp:
            return
        ax, ay = self._anchor_by_pin_id.get(sp, (x, y))
        try:
            c.coords(self._rubber_line_id, ax, ay, x, y)
        except Exception:
            pass

    def _on_canvas_release(self, e: tk.Event) -> None:
        c = self.canvas
        try:
            x = float(c.canvasx(e.x))
            y = float(c.canvasy(e.y))
        except Exception:
            return

        if bool(self._connect_var.get()) and self._connect_start_pid and self._rubber_line_id is not None:
            sp = self._connect_start_pid
            self._clear_rubber_line()

            pid = self._pick_pin_at(x, y)
            role = self._pin_role.get(pid or "") if pid else None
            if pid and role == "end":
                self._connect_start_pid = None
                self._complete_connection(sp, pid)
                return

            # If user just clicked start (no drag), keep the start selected for a second click.
            if not self._drag_moved:
                self._connect_start_pid = sp
                self._info_var.set(
                    f"Connection mode: start selected {sp} {self._pin_map.get(sp, '')}  -> select end"
                )
                return

            self._connect_start_pid = None
            self._info_var.set("Connection mode: click start-pin then end-pin")
            return

        # Normal mode: click selects an arrow line.
        line_id = self._pick_line_at(x, y)
        if line_id is None:
            self._select_line(None)
            return
        self._select_line(line_id)

    def _on_canvas_right_click(self, e: tk.Event) -> None:
        c = self.canvas
        try:
            x = float(c.canvasx(e.x))
            y = float(c.canvasy(e.y))
        except Exception:
            return

        line_id = self._pick_line_at(x, y)
        if line_id is not None:
            try:
                self._select_line(line_id)
            except Exception:
                pass

        if self._ctx_menu is None:
            m = tk.Menu(self, tearoff=0)
            m.add_command(label="Delete", command=self._delete_selected_arrow)
            self._ctx_menu = m

        if self._selected_line_id is None:
            try:
                self._ctx_menu.entryconfigure(0, state="disabled")
            except Exception:
                pass
        else:
            try:
                self._ctx_menu.entryconfigure(0, state="normal")
            except Exception:
                pass

        try:
            self._ctx_menu.tk_popup(int(e.x_root), int(e.y_root))
        finally:
            try:
                self._ctx_menu.grab_release()
            except Exception:
                pass

    def _delete_selected_arrow(self) -> None:
        if self._selected_line_id is None:
            return
        arrow_el = self._arrow_el_by_line_id.get(self._selected_line_id)
        if arrow_el is None:
            return
        ok = self._remove_arrow_element(arrow_el)
        if not ok:
            return

        if self._on_arrows_changed is not None:
            try:
                self._on_arrows_changed(None)
            except Exception:
                pass

        self._selected_line_id = None
        try:
            self._draw_graph(self._xml_root)
        except Exception:
            pass

    def _select_line(self, line_id: int | None) -> None:
        c = self.canvas

        # Clear previous selection
        if self._selected_line_id is not None:
            prev = self._selected_line_id
            style = self._arrow_line_style.get(prev, {})
            try:
                c.itemconfigure(prev, width=int(style.get("width", 1)), fill=str(style.get("fill", "#000000")))
            except Exception:
                pass

        self._selected_line_id = None
        self._info_var.set("")

        if line_id is None:
            return

        if line_id not in self._arrow_by_line_id:
            return

        self._selected_line_id = line_id
        try:
            c.itemconfigure(line_id, width=3, fill="red")
        except Exception:
            pass

        sp, ep = self._arrow_by_line_id.get(line_id, ("", ""))
        s_label = self._pin_map.get(sp, "")
        e_label = self._pin_map.get(ep, "")
        self._info_var.set(f"Start: {sp}  {s_label}    End: {ep}  {e_label}")

        if not self._syncing and self._on_select_arrow is not None:
            try:
                self._on_select_arrow(self._arrow_el_by_line_id.get(line_id))
            except Exception:
                pass

    def highlight_arrow_element(self, el: ET.Element | None) -> None:
        if el is None:
            self._syncing = True
            try:
                self._select_line(None)
            finally:
                self._syncing = False
            return

        line_id: int | None = None
        for lid, ael in self._arrow_el_by_line_id.items():
            if ael is el:
                line_id = lid
                break

        self._syncing = True
        try:
            self._select_line(line_id)
        finally:
            self._syncing = False

    def _close(self) -> None:
        if not self._closed:
            self._closed = True
            if self._on_close_cb is not None:
                try:
                    self._on_close_cb()
                except Exception:
                    pass
        try:
            super().destroy()
        except Exception:
            pass

    def destroy(self) -> None:
        # Ensure we notify close callback regardless of how window is closed.
        try:
            self._close()
        except Exception:
            try:
                super().destroy()
            except Exception:
                pass


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


class _EditApplicationSimpleDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        title: str,
        type_values: list[str],
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
        self.var_src = tk.StringVar(value=(initial.get("src") or ""))
        self.var_desc = tk.StringVar(value=(initial.get("desc") or ""))

        ttk.Label(frm, text="name").grid(row=0, column=0, sticky="w", pady=4)
        ent_name = ttk.Entry(frm, textvariable=self.var_name, width=56)
        ent_name.grid(row=0, column=1, sticky="we", pady=4)

        ttk.Label(frm, text="type").grid(row=1, column=0, sticky="w", pady=4)
        cb_type = ttk.Combobox(frm, textvariable=self.var_type, values=list(type_values), width=54)
        cb_type.grid(row=1, column=1, sticky="we", pady=4)

        ttk.Label(frm, text="src").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=self.var_src, width=56).grid(row=2, column=1, sticky="we", pady=4)

        ttk.Label(frm, text="desc").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=self.var_desc, width=56).grid(row=3, column=1, sticky="we", pady=4)

        btns = ttk.Frame(frm)
        btns.grid(row=4, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Cancel", command=self._cancel).pack(side="right")
        ttk.Button(btns, text="OK", command=self._ok).pack(side="right", padx=(0, 8))

        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Return>", lambda _e: self._ok())
        cb_type.bind("<Return>", lambda _e: self._ok())

        ent_name.focus_set()
        try:
            ent_name.selection_range(0, tk.END)
        except Exception:
            pass

    def _ok(self) -> None:
        name = (self.var_name.get() or "").strip()
        if not name:
            messagebox.showerror("Missing", "name is required", parent=self)
            return

        self._result = {
            "name": name,
            "type": (self.var_type.get() or "").strip(),
            "src": (self.var_src.get() or ""),
            "desc": (self.var_desc.get() or ""),
        }
        self.destroy()

    def _cancel(self) -> None:
        self._result = None
        self.destroy()

    def show(self) -> dict[str, str] | None:
        self.wait_window(self)
        return self._result


class _EditApplicationOutputDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        title: str,
        output_types: list[str],
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
        self.var_outPurpose = tk.StringVar(value=(initial.get("outPurpose") or ""))
        self.var_srvRef = tk.StringVar(value=(initial.get("srvRef") or ""))
        self.var_doRef = tk.StringVar(value=(initial.get("doRef") or ""))
        self.var_max = tk.StringVar(value=(initial.get("MaxContiguous") or ""))
        self.var_overlap = tk.StringVar(value=(initial.get("Overlap") or ""))
        self.var_persist = tk.BooleanVar(value=((initial.get("persist") or "").lower() == "true"))
        self.var_fault = tk.BooleanVar(value=((initial.get("faultlog") or "").lower() == "true"))

        ttk.Label(frm, text="name").grid(row=0, column=0, sticky="w", pady=4)
        ent_name = ttk.Entry(frm, textvariable=self.var_name, width=56)
        ent_name.grid(row=0, column=1, sticky="we", pady=4)

        ttk.Label(frm, text="type").grid(row=1, column=0, sticky="w", pady=4)
        cb_type = ttk.Combobox(frm, textvariable=self.var_type, values=list(output_types), width=54)
        cb_type.grid(row=1, column=1, sticky="we", pady=4)

        ttk.Label(frm, text="desc").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=self.var_desc, width=56).grid(row=2, column=1, sticky="we", pady=4)

        ttk.Label(frm, text="outPurpose").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=self.var_outPurpose, width=56).grid(row=3, column=1, sticky="we", pady=4)

        ttk.Label(frm, text="srvRef").grid(row=4, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=self.var_srvRef, width=56).grid(row=4, column=1, sticky="we", pady=4)

        ttk.Label(frm, text="doRef").grid(row=5, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=self.var_doRef, width=56).grid(row=5, column=1, sticky="we", pady=4)

        ttk.Label(frm, text="MaxContiguous").grid(row=6, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=self.var_max, width=56).grid(row=6, column=1, sticky="we", pady=4)

        ttk.Label(frm, text="Overlap").grid(row=7, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=self.var_overlap, width=56).grid(row=7, column=1, sticky="we", pady=4)

        flags = ttk.Frame(frm)
        flags.grid(row=8, column=0, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Checkbutton(flags, text="persist", variable=self.var_persist).pack(side="left")
        ttk.Checkbutton(flags, text="faultlog", variable=self.var_fault).pack(side="left", padx=(16, 0))

        btns = ttk.Frame(frm)
        btns.grid(row=9, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Cancel", command=self._cancel).pack(side="right")
        ttk.Button(btns, text="OK", command=self._ok).pack(side="right", padx=(0, 8))

        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Return>", lambda _e: self._ok())
        cb_type.bind("<Return>", lambda _e: self._ok())

        ent_name.focus_set()
        try:
            ent_name.selection_range(0, tk.END)
        except Exception:
            pass

    def _ok(self) -> None:
        name = (self.var_name.get() or "").strip()
        if not name:
            messagebox.showerror("Missing", "name is required", parent=self)
            return

        maxc = (self.var_max.get() or "").strip()
        overlap = (self.var_overlap.get() or "").strip()
        if not maxc:
            maxc = "0"
        if not overlap:
            overlap = "1"

        self._result = {
            "name": name,
            "type": (self.var_type.get() or "").strip(),
            "desc": (self.var_desc.get() or ""),
            "outPurpose": (self.var_outPurpose.get() or ""),
            "srvRef": (self.var_srvRef.get() or ""),
            "persist": "true" if bool(self.var_persist.get()) else "false",
            "doRef": (self.var_doRef.get() or "").strip(),
            "MaxContiguous": maxc,
            "Overlap": overlap,
            "faultlog": "true" if bool(self.var_fault.get()) else "",
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
        get_do_type_preview: Callable[[str], str] | None = None,
    ):
        super().__init__(parent)
        self.title(title)
        self.resizable(True, True)
        self.transient(parent)
        self.grab_set()

        self._result: DOItem | None = None
        self._get_do_type_preview = get_do_type_preview
        self._preview_after_id: str | None = None

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

        # Preview area: shows selected DOType template content.
        preview_box = ttk.Frame(frm)
        preview_box.grid(row=2, column=0, columnspan=2, sticky="nsew", pady=(10, 0))
        ttk.Label(preview_box, text="DO type template preview").pack(anchor="w")

        preview_inner = ttk.Frame(preview_box)
        preview_inner.pack(fill="both", expand=True, pady=(6, 0))
        preview_inner.columnconfigure(0, weight=1)
        preview_inner.rowconfigure(0, weight=1)

        self.txt_preview = tk.Text(preview_inner, height=14, wrap="none")
        y = ttk.Scrollbar(preview_inner, orient="vertical", command=self.txt_preview.yview)
        self.txt_preview.configure(yscrollcommand=y.set)
        self.txt_preview.grid(row=0, column=0, sticky="nsew")
        y.grid(row=0, column=1, sticky="ns")
        try:
            self.txt_preview.configure(state="disabled")
        except Exception:
            pass

        frm.rowconfigure(2, weight=1)

        btns = ttk.Frame(frm)
        btns.grid(row=3, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Cancel", command=self._cancel).pack(side="right")
        ttk.Button(btns, text="OK", command=self._ok).pack(side="right", padx=(0, 8))

        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Control-f>", lambda _e: ent_filter.focus_set())

        def schedule_preview_update(*_args) -> None:
            if getattr(self, "txt_preview", None) is None:
                return
            if self._preview_after_id is not None:
                try:
                    self.after_cancel(self._preview_after_id)
                except Exception:
                    pass
                self._preview_after_id = None
            try:
                self._preview_after_id = self.after(80, self._update_preview)
            except Exception:
                self._preview_after_id = None

        self.var_type.trace_add("write", schedule_preview_update)
        try:
            self.cb.bind("<<ComboboxSelected>>", lambda _e: self._update_preview())
        except Exception:
            pass

        self._update_preview()

        ent_filter.focus_set()

        # Make the dialog open larger (2x current requested size).
        try:
            self.update_idletasks()
            w = int(self.winfo_reqwidth() or 0)
            h = int(self.winfo_reqheight() or 0)
            if w > 0 and h > 0:
                self.geometry(f"{w * 2}x{h * 2}")
                try:
                    self.minsize(w, h)
                except Exception:
                    pass
        except Exception:
            pass

    def _update_preview(self) -> None:
        get_preview = getattr(self, "_get_do_type_preview", None)
        txt = getattr(self, "txt_preview", None)
        if txt is None:
            return

        do_type = (self.var_type.get() or "").strip()

        if not callable(get_preview) or not do_type:
            preview = ""
        else:
            try:
                preview = str(get_preview(do_type) or "")
            except Exception as e:
                preview = f"(Failed to load preview: {e})"

        if callable(get_preview) and do_type and not preview.strip():
            preview = "(DOType not found)"

        try:
            txt.configure(state="normal")
        except Exception:
            pass
        try:
            txt.delete("1.0", "end")
            if preview:
                txt.insert("1.0", preview)
        finally:
            try:
                txt.configure(state="disabled")
            except Exception:
                pass

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


class DAEditDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        title: str,
        initial: dict[str, str] | None,
        btype_options: list[str] | None = None,
        enum_type_ids: list[str] | None = None,
        get_enum_values: Callable[[str], list[str]] | None = None,
        get_enum_preview: Callable[[str], str] | None = None,
    ):
        super().__init__(parent)
        self.title(title)
        self.resizable(True, True)
        self.transient(parent)
        self.grab_set()
        self._result: dict[str, str] | None = None

        self._all_btypes = [x for x in (btype_options or []) if (x or '').strip()]
        self._all_enum_ids = [x for x in (enum_type_ids or []) if (x or '').strip()]
        self._get_enum_values = get_enum_values
        self._get_enum_preview = get_enum_preview

        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        init = initial or {}
        self.var_name = tk.StringVar(value=(init.get("name") or ""))
        self.var_fc = tk.StringVar(value=(init.get("fc") or ""))
        self.var_bType = tk.StringVar(value=(init.get("bType") or ""))
        self.var_type = tk.StringVar(value=(init.get("type") or ""))
        self.var_valKind = tk.StringVar(value=(init.get("valKind") or ""))
        self.var_valImport = tk.StringVar(value=(init.get("valImport") or ""))
        self.var_dchg = tk.StringVar(value=(init.get("dchg") or ""))
        self.var_val = tk.StringVar(value=(init.get("val") or ""))
        self.var_desc = tk.StringVar(value=(init.get("desc") or ""))

        self.var_enum_filter = tk.StringVar(value="")

        ttk.Label(frm, text="name").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=self.var_name, width=44).grid(row=0, column=1, sticky="we", pady=4)

        ttk.Label(frm, text="fc").grid(row=1, column=0, sticky="w", pady=4)
        cb_fc = ttk.Combobox(
            frm,
            textvariable=self.var_fc,
            state="readonly",
            values=("ST", "MX", "SP", "SV", "CF", "DC", "EX", "CO", "SG", "SE"),
            width=12,
        )
        cb_fc.grid(row=1, column=1, sticky="w", pady=4)

        ttk.Label(frm, text="bType").grid(row=2, column=0, sticky="w", pady=4)
        btypes = self._all_btypes or []
        cur_bt = (self.var_bType.get() or "").strip()
        if cur_bt and cur_bt not in btypes:
            btypes = [cur_bt] + btypes
        cb_bt = ttk.Combobox(frm, textvariable=self.var_bType, state="readonly", values=tuple(btypes), width=16)
        cb_bt.grid(row=2, column=1, sticky="w", pady=4)

        ttk.Label(frm, text="type").grid(row=3, column=0, sticky="w", pady=4)

        type_box = ttk.Frame(frm)
        type_box.grid(row=3, column=1, sticky="we", pady=4)
        type_box.columnconfigure(0, weight=1)

        # Enum mode widgets (filter + combobox)
        type_enum_box = ttk.Frame(type_box)
        type_enum_box.grid(row=0, column=0, sticky="we")
        type_enum_box.columnconfigure(0, weight=1)

        ent_filter = ttk.Entry(type_enum_box, textvariable=self.var_enum_filter)
        ent_filter.grid(row=0, column=0, sticky="we")
        ttk.Label(type_enum_box, text="Search").grid(row=0, column=1, sticky="e", padx=(8, 0))

        self.lbl_enum_match = ttk.Label(type_enum_box, text="")
        self.lbl_enum_match.grid(row=1, column=1, sticky="e", padx=(8, 0), pady=(4, 0))

        self.cb_enum_type = ttk.Combobox(
            type_enum_box,
            textvariable=self.var_type,
            state="readonly",
            values=tuple(self._all_enum_ids),
        )
        self.cb_enum_type.grid(row=1, column=0, sticky="we", pady=(4, 0))

        # Non-enum mode widget (plain entry)
        self.ent_type_plain = ttk.Entry(type_box, textvariable=self.var_type, width=44)
        self.ent_type_plain.grid(row=1, column=0, sticky="we")

        ttk.Label(frm, text="val").grid(row=4, column=0, sticky="w", pady=4)
        val_box = ttk.Frame(frm)
        val_box.grid(row=4, column=1, sticky="we", pady=4)
        val_box.columnconfigure(0, weight=1)

        self.cb_enum_val = ttk.Combobox(val_box, textvariable=self.var_val, state="readonly", values=("",))
        self.cb_enum_val.grid(row=0, column=0, sticky="we")
        self.ent_val_plain = ttk.Entry(val_box, textvariable=self.var_val, width=44)
        self.ent_val_plain.grid(row=1, column=0, sticky="we")

        ttk.Label(frm, text="valKind").grid(row=5, column=0, sticky="w", pady=4)
        cb_vk = ttk.Combobox(frm, textvariable=self.var_valKind, state="readonly", values=("", "Set", "Conf", "RO"), width=16)
        cb_vk.grid(row=5, column=1, sticky="w", pady=4)

        ttk.Label(frm, text="valImport").grid(row=6, column=0, sticky="w", pady=4)
        cb_vi = ttk.Combobox(frm, textvariable=self.var_valImport, state="readonly", values=("", "true", "false"), width=16)
        cb_vi.grid(row=6, column=1, sticky="w", pady=4)

        ttk.Label(frm, text="dchg").grid(row=7, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=self.var_dchg, width=16).grid(row=7, column=1, sticky="w", pady=4)

        ttk.Label(frm, text="desc").grid(row=8, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=self.var_desc, width=44).grid(row=8, column=1, sticky="we", pady=4)

        # Enum preview area (only meaningful when bType=Enum and type is selected)
        preview_box = ttk.Frame(frm)
        preview_box.grid(row=9, column=0, columnspan=2, sticky="nsew", pady=(10, 0))
        ttk.Label(preview_box, text="Enum preview").pack(anchor="w")

        preview_inner = ttk.Frame(preview_box)
        preview_inner.pack(fill="both", expand=True, pady=(6, 0))
        preview_inner.columnconfigure(0, weight=1)
        preview_inner.rowconfigure(0, weight=1)

        self.txt_enum_preview = tk.Text(preview_inner, height=10, wrap="none")
        y = ttk.Scrollbar(preview_inner, orient="vertical", command=self.txt_enum_preview.yview)
        self.txt_enum_preview.configure(yscrollcommand=y.set)
        self.txt_enum_preview.grid(row=0, column=0, sticky="nsew")
        y.grid(row=0, column=1, sticky="ns")
        try:
            self.txt_enum_preview.configure(state="disabled")
        except Exception:
            pass

        frm.rowconfigure(9, weight=1)

        btns = ttk.Frame(frm)
        btns.grid(row=10, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Cancel", command=self._cancel).pack(side="right")
        ttk.Button(btns, text="OK", command=self._ok).pack(side="right", padx=(0, 8))

        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Return>", lambda _e: self._ok())

        def is_enum() -> bool:
            return (self.var_bType.get() or "").strip().upper() == "ENUM"

        def apply_enum_filter(*_args) -> None:
            if not is_enum():
                try:
                    self.cb_enum_type["values"] = tuple(self._all_enum_ids)
                except Exception:
                    pass
                try:
                    self.lbl_enum_match.configure(text="")
                except Exception:
                    pass
                return

            raw = (self.var_enum_filter.get() or "").strip().lower()
            if not raw:
                filtered = list(self._all_enum_ids)
            else:
                tokens = [t for t in raw.split() if t]

                def ok(v: str) -> bool:
                    lv = v.lower()
                    return all(t in lv for t in tokens)

                filtered = [v for v in self._all_enum_ids if ok(v)]

            cur = (self.var_type.get() or "").strip()
            if cur and cur not in filtered:
                filtered = [cur] + filtered

            max_show = 1500
            shown = filtered[:max_show]
            try:
                self.cb_enum_type["values"] = tuple(shown)
            except Exception:
                pass
            suffix = "" if len(filtered) <= max_show else f" (showing first {max_show})"
            try:
                self.lbl_enum_match.configure(text=f"{len(filtered)} match{'' if len(filtered)==1 else 'es'}{suffix}")
            except Exception:
                pass

        def update_val_widget() -> None:
            bt_enum = is_enum()
            enum_id = (self.var_type.get() or "").strip() if bt_enum else ""

            # Type widgets
            if bt_enum:
                try:
                    type_enum_box.grid()
                except Exception:
                    pass
                try:
                    self.ent_type_plain.grid_remove()
                except Exception:
                    pass
            else:
                try:
                    type_enum_box.grid_remove()
                except Exception:
                    pass
                try:
                    self.ent_type_plain.grid()
                except Exception:
                    pass

            # Val widgets
            if bt_enum and enum_id:
                vals: list[str] = []
                if callable(self._get_enum_values):
                    try:
                        vals = list(self._get_enum_values(enum_id) or [])
                    except Exception:
                        vals = []
                values = [""] + [v for v in vals if (v or "").strip()]
                cur = self.var_val.get() or ""
                if cur and cur not in values:
                    values = [cur] + values
                try:
                    self.cb_enum_val["values"] = tuple(values)
                except Exception:
                    pass
                try:
                    self.cb_enum_val.grid()
                except Exception:
                    pass
                try:
                    self.ent_val_plain.grid_remove()
                except Exception:
                    pass
            else:
                try:
                    self.cb_enum_val.grid_remove()
                except Exception:
                    pass
                try:
                    self.ent_val_plain.grid()
                except Exception:
                    pass

        def update_enum_preview(*_args) -> None:
            txt = getattr(self, "txt_enum_preview", None)
            if txt is None:
                return
            bt_enum = is_enum()
            enum_id = (self.var_type.get() or "").strip() if bt_enum else ""
            if bt_enum and enum_id and callable(self._get_enum_preview):
                try:
                    preview = str(self._get_enum_preview(enum_id) or "")
                except Exception as e:
                    preview = f"(Failed to load preview: {e})"
            else:
                preview = ""

            try:
                txt.configure(state="normal")
            except Exception:
                pass
            try:
                txt.delete("1.0", "end")
                if preview:
                    txt.insert("1.0", preview)
            finally:
                try:
                    txt.configure(state="disabled")
                except Exception:
                    pass

        self.var_enum_filter.trace_add("write", apply_enum_filter)
        self.var_bType.trace_add("write", lambda *_a: (apply_enum_filter(), update_val_widget(), update_enum_preview()))
        self.var_type.trace_add("write", lambda *_a: (update_val_widget(), update_enum_preview()))

        apply_enum_filter()
        update_val_widget()
        update_enum_preview()

        # Start with name focused.
        try:
            self.after(10, lambda: self.focus_force())
        except Exception:
            pass

    def _ok(self) -> None:
        name = (self.var_name.get() or "").strip()
        if not name:
            messagebox.showerror("Missing", "DA name is required", parent=self)
            return
        fc = (self.var_fc.get() or "").strip().upper()
        if not fc:
            messagebox.showerror("Missing", "FC is required (must not be empty)", parent=self)
            return
        bt = (self.var_bType.get() or "").strip()
        if not bt:
            messagebox.showerror("Missing", "bType is required (must not be empty)", parent=self)
            return

        vk0 = (self.var_valKind.get() or "").strip()
        u = vk0.upper()
        if u == "SET":
            vk = "Set"
        elif u == "CONF":
            vk = "Conf"
        elif u == "RO":
            vk = "RO"
        else:
            vk = vk0

        vi0 = (self.var_valImport.get() or "").strip()
        vi = vi0.lower() if vi0.lower() in {"true", "false"} else vi0

        self._result = {
            "name": name,
            "fc": fc,
            "bType": bt,
            "type": (self.var_type.get() or "").strip(),
            "valKind": vk,
            "valImport": vi,
            "dchg": (self.var_dchg.get() or "").strip(),
            "val": (self.var_val.get() or ""),
            "desc": (self.var_desc.get() or ""),
        }
        self.destroy()

    def _cancel(self) -> None:
        self._result = None
        self.destroy()

    def show(self) -> dict[str, str] | None:
        self.wait_window(self)
        return self._result


class DATable(ttk.Frame):
    def __init__(self, parent: tk.Misc):
        super().__init__(parent)
        self.rows: list[dict[str, str]] = []
        self._clipboard: dict[str, str] | None = None
        self._undo_stack: list[list[dict[str, str]]] = []
        self._undo_max = 50

        # Changed-row highlighting (vs last saved snapshot)
        self._saved_sig_by_name: dict[str, tuple[str, ...]] = {}

        # UI-only row states:
        # - added: keep green until saved
        # - deleted: keep red and visible until saved, then removed from model
        self._UI_ADDED = "__ui_added"
        self._UI_DELETED = "__ui_deleted"

        self._inline: ttk.Entry | None = None
        self._inline_iid: str | None = None
        self._inline_col: str | None = None
        self._inline_started_at: float | None = None

        # Optional providers for EnumType integration.
        # - get_enum_type_ids(): list of EnumType@id strings
        # - get_enum_values(enum_id): ordered list of EnumVal texts
        self.get_enum_type_ids: Callable[[], list[str]] | None = None
        self.get_enum_values: Callable[[str], list[str]] | None = None
        # - get_enum_preview(enum_id): preview text for EnumType
        self.get_enum_preview: Callable[[str], str] | None = None

        # Optional provider for bType dropdown values.
        # If not provided, the dropdown falls back to values seen in current rows.
        self.get_btype_options: Callable[[], list[str]] | None = None

        # Optional callback invoked after any user-visible mutation.
        # Signature: callback() -> None
        self.on_change: Callable[[], None] | None = None

        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x", pady=(6, 4))
        ttk.Button(toolbar, text="Add", command=self._add).pack(side="left")
        ttk.Button(toolbar, text="Insert", command=self._insert).pack(side="left", padx=(6, 0))
        ttk.Button(toolbar, text="Edit", command=self.edit_selected).pack(side="left", padx=(6, 0))
        ttk.Button(toolbar, text="Copy", command=self.copy_selected).pack(side="left", padx=(6, 0))
        ttk.Button(toolbar, text="Cut", command=self.cut_selected).pack(side="left", padx=(6, 0))
        ttk.Button(toolbar, text="Paste", command=self.paste_after_selected).pack(side="left", padx=(6, 0))
        ttk.Button(toolbar, text="Delete", command=self.delete_selected).pack(side="left", padx=(6, 0))
        ttk.Button(toolbar, text="Up", command=lambda: self._move(-1)).pack(side="left", padx=(18, 0))
        ttk.Button(toolbar, text="Down", command=lambda: self._move(1)).pack(side="left", padx=(6, 0))

        content = ttk.Frame(self)
        content.pack(fill="both", expand=True)

        cols = ["name", "fc", "bType", "type", "valKind", "valImport", "dchg", "val", "desc"]
        self.tree = ttk.Treeview(content, columns=cols, show="headings", selectmode="browse")
        for c in cols:
            self.tree.heading(c, text=c)
            if c in {"name", "fc", "bType"}:
                if c == "fc":
                    # FC is typically 2 chars (e.g., ST/MX/SP). Keep it narrow.
                    self.tree.column(c, width=52, anchor="w", stretch=False)
                else:
                    self.tree.column(c, width=120, anchor="w")
            elif c in {"valKind", "valImport", "dchg"}:
                self.tree.column(c, width=90, anchor="w")
            elif c == "type":
                self.tree.column(c, width=220, anchor="w")
            elif c == "val":
                self.tree.column(c, width=220, anchor="w")
            else:  # desc
                self.tree.column(c, width=320, anchor="w")

        # Scrollbars: include horizontal so rightmost columns are reachable.
        content.columnconfigure(0, weight=1)
        content.rowconfigure(0, weight=1)

        y = ttk.Scrollbar(content, orient="vertical", command=self.tree.yview)
        x = ttk.Scrollbar(content, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=y.set, xscrollcommand=x.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        y.grid(row=0, column=1, sticky="ns")
        x.grid(row=1, column=0, columnspan=2, sticky="ew")

        # Single click: open dropdown editors for specific columns.
        # Double click: edit text columns.
        self.tree.bind("<Button-1>", self._on_left_click)
        self.tree.bind("<Double-1>", self._on_double_click)

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
        self._menu.add_command(label="Edit", command=self.edit_selected)
        self._menu.add_separator()
        self._menu.add_command(label="Copy", command=self.copy_selected)
        self._menu.add_command(label="Cut", command=self.cut_selected)
        self._menu.add_command(label="Paste", command=self.paste_after_selected)
        self._menu.add_command(label="Delete", command=self.delete_selected)
        self._menu.add_separator()
        self._menu.add_command(label="Up", command=lambda: self._move(-1))
        self._menu.add_command(label="Down", command=lambda: self._move(1))

        try:
            self.tree.tag_configure("added", background="honeydew2")
            self.tree.tag_configure("removed", background="misty rose")
            self.tree.tag_configure("changed", background="lemon chiffon")
        except Exception:
            pass

    def _row_is_added(self, r: dict[str, str]) -> bool:
        try:
            return bool(r.get(self._UI_ADDED))
        except Exception:
            return False

    def _row_is_deleted(self, r: dict[str, str]) -> bool:
        try:
            return bool(r.get(self._UI_DELETED))
        except Exception:
            return False

    def _strip_ui_flags(self, r: dict[str, str]) -> dict[str, str]:
        d = dict(r)
        d.pop(self._UI_ADDED, None)
        d.pop(self._UI_DELETED, None)
        return d

    def _row_tags(self, r: dict[str, str]) -> tuple[str, ...]:
        if self._row_is_deleted(r):
            return ("removed",)
        if self._row_is_added(r):
            return ("added",)
        return ("changed",) if self._row_is_changed(r) else ()

    def _row_sig(self, r: dict[str, str]) -> tuple[str, ...]:
        keys = ["name", "fc", "bType", "type", "valKind", "valImport", "dchg", "val", "desc"]
        return tuple((r.get(k) or "") for k in keys)

    def _snapshot_sig_by_name(self) -> dict[str, tuple[str, ...]]:
        out: dict[str, tuple[str, ...]] = {}
        for r in (self.rows or []):
            if self._row_is_deleted(r):
                continue
            k = (r.get("name") or "").strip()
            if not k:
                continue
            out[k] = self._row_sig(r)
        return out

    def _row_is_changed(self, r: dict[str, str]) -> bool:
        k = (r.get("name") or "").strip()
        if not k:
            return False
        cur = self._row_sig(r)
        saved = self._saved_sig_by_name.get(k)
        return (saved is None) or (saved != cur)

    def _reapply_row_tags(self) -> None:
        try:
            for iid in self.tree.get_children(""):
                try:
                    idx = int(iid)
                except Exception:
                    continue
                if idx < 0 or idx >= len(self.rows):
                    continue
                tags = self._row_tags(self.rows[idx])
                try:
                    self.tree.item(iid, tags=tags)
                except Exception:
                    pass
        except Exception:
            pass

    def mark_saved(self) -> None:
        # Apply pending deletions + clear "added" state.
        try:
            self.rows = [r for r in (self.rows or []) if not self._row_is_deleted(r)]
            for r in (self.rows or []):
                r.pop(self._UI_ADDED, None)
                r.pop(self._UI_DELETED, None)
        except Exception:
            pass

        self._saved_sig_by_name = self._snapshot_sig_by_name()
        self.refresh()


    def commit_any_edit(self) -> None:
        """Commit any active inline edit."""
        try:
            self._end_inline_edit(commit=True)
        except Exception:
            pass

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

    def _on_inline_combobox_focus_out(self, event: tk.Event) -> None:
        """Commit inline edit on focus-out, but avoid committing while the combobox dropdown is open.

        On Windows, clicking the combobox dropdown list may trigger <FocusOut> on the combobox
        before <<ComboboxSelected>> fires. If we commit/destroy on that focus change, the dropdown
        appears empty / unselectable.
        """
        try:
            widget = event.widget
        except Exception:
            widget = None

        if not isinstance(widget, ttk.Combobox):
            self._end_inline_edit(commit=True)
            return

        cb = widget

        # If focus moved into the combobox popdown (listbox), do NOT commit/destroy.
        # That focus change is part of selecting an item.
        try:
            popdown = cb.tk.call("ttk::combobox::PopdownWindow", str(cb))
            focus_w = str(cb.tk.call("focus") or "")
            if popdown and focus_w and focus_w.startswith(str(popdown)):
                return
        except Exception:
            pass

        if self._combobox_is_posted(cb):
            # Re-check shortly: if user dismissed the dropdown without selecting,
            # this will commit once the popdown closes.
            try:
                self.after(
                    75,
                    lambda: (
                        self._end_inline_edit(commit=True)
                        if self._inline is cb and not self._combobox_is_posted(cb)
                        else None
                    ),
                )
            except Exception:
                pass
            return

        self._end_inline_edit(commit=True)

    def _row_by_iid(self, iid: str) -> dict[str, str] | None:
        try:
            idx = int(iid)
        except Exception:
            return None
        if idx < 0 or idx >= len(self.rows):
            return None
        return self.rows[idx]

    def _enum_type_ids(self) -> list[str]:
        try:
            if self.get_enum_type_ids is None:
                return []
            return list(self.get_enum_type_ids() or [])
        except Exception:
            return []

    def _enum_values(self, enum_type_id: str) -> list[str]:
        try:
            if self.get_enum_values is None:
                return []
            return list(self.get_enum_values(enum_type_id) or [])
        except Exception:
            return []

    def _enum_preview_text(self, enum_type_id: str) -> str:
        try:
            if self.get_enum_preview is None:
                return ""
            return str(self.get_enum_preview(enum_type_id) or "")
        except Exception:
            return ""

    def _btype_options(self) -> list[str]:
        # Prefer a global provider (datamodel-derived list).
        try:
            if self.get_btype_options is not None:
                opts = [str(x) for x in (self.get_btype_options() or [])]
                opts = [o.strip() for o in opts if (o or "").strip()]
                if opts:
                    return opts
        except Exception:
            pass

        # Fallback: values seen in current rows.
        seen: list[str] = []
        for r in (self.rows or []):
            bt = (r.get("bType") or "").strip()
            if bt and bt not in seen:
                seen.append(bt)
        if "Enum" not in seen and "ENUM" not in {s.upper() for s in seen}:
            seen.append("Enum")
        return seen

    def _is_dropdown_cell(self, iid: str, col_name: str) -> bool:
        if col_name in {"fc", "valKind", "valImport", "bType"}:
            return True

        row = self._row_by_iid(iid)
        if row is None:
            return False
        bt = (row.get("bType") or "").strip()
        if bt.upper() == "ENUM" and col_name == "type":
            return True
        if bt.upper() == "ENUM" and col_name == "val":
            enum_id = (row.get("type") or "").strip()
            # Even if EnumType catalog is incomplete, treat it as a dropdown cell
            # as long as an enum id is provided; options may be empty if id is invalid.
            return bool(enum_id)
        return False

    def set_rows(self, rows: list[dict[str, str]]) -> None:
        # Loading rows resets UI-only added/deleted state.
        self.rows = [self._strip_ui_flags(dict(r)) for r in (rows or [])]
        self._undo_stack = []
        self._saved_sig_by_name = self._snapshot_sig_by_name()
        self.refresh()
        self._fire_change()

    def _fire_change(self) -> None:
        cb = getattr(self, "on_change", None)
        if cb is None:
            return
        try:
            cb()
        except Exception:
            pass

    def get_rows(self) -> list[dict[str, str]]:
        # Exclude pending deletions from saved/exported model.
        out: list[dict[str, str]] = []
        for r in (self.rows or []):
            if self._row_is_deleted(r):
                continue
            out.append(self._strip_ui_flags(r))
        return out

    def refresh(self) -> None:
        for item in self.tree.get_children(""):
            self.tree.delete(item)
        cols = ["name", "fc", "bType", "type", "valKind", "valImport", "dchg", "val", "desc"]
        for idx, row in enumerate(self.rows):
            self.tree.insert("", "end", iid=str(idx), values=[row.get(c, "") for c in cols], tags=self._row_tags(row))

    def _selected_index(self) -> int | None:
        try:
            sel = self.tree.selection()
            if not sel:
                return None
            return int(sel[0])
        except Exception:
            return None

    def _clone_row(self, r: dict[str, str]) -> dict[str, str]:
        return dict(r)

    def _clone_rows(self, rows: list[dict[str, str]]) -> list[dict[str, str]]:
        return [self._clone_row(x) for x in (rows or [])]

    def _push_undo(self) -> None:
        self._undo_stack.append(self._clone_rows(self.rows))
        if len(self._undo_stack) > self._undo_max:
            self._undo_stack = self._undo_stack[-self._undo_max :]

    def undo(self) -> None:
        self._end_inline_edit(commit=True)
        if not self._undo_stack:
            return
        prev = self._undo_stack.pop()
        self.rows = self._clone_rows(prev)
        self.refresh()
        self._fire_change()

    def _unique_copy_name(self, base_name: str) -> str:
        existing = {(x.get("name") or "").strip() for x in self.rows if not self._row_is_deleted(x)}
        candidate = f"{base_name}_copy"
        if candidate not in existing:
            return candidate
        i = 2
        while True:
            candidate = f"{base_name}_copy{i}"
            if candidate not in existing:
                return candidate
            i += 1

    def _unique_new_name(self, base: str = "newDA") -> str:
        existing = {(x.get("name") or "").strip() for x in self.rows if not self._row_is_deleted(x)}
        if base not in existing:
            return base
        i = 2
        while True:
            cand = f"{base}{i}"
            if cand not in existing:
                return cand
            i += 1

    def copy_selected(self) -> None:
        self._end_inline_edit(commit=True)
        idx = self._selected_index()
        if idx is None or idx < 0 or idx >= len(self.rows):
            return
        self._clipboard = self._clone_row(self.rows[idx])
        try:
            self.clipboard_clear()
            self.clipboard_append((self.rows[idx].get("name") or "") + "\t" + (self.rows[idx].get("bType") or ""))
        except Exception:
            pass

    def cut_selected(self) -> None:
        self.copy_selected()
        self.delete_selected()

    def delete_selected(self) -> None:
        self._end_inline_edit(commit=True)
        idx = self._selected_index()
        if idx is None or idx < 0 or idx >= len(self.rows):
            return
        if self._row_is_deleted(self.rows[idx]):
            return
        # Added-then-deleted before save: cancel the addition (no red removed state).
        try:
            if bool(self.rows[idx].get(self._UI_ADDED)):
                self._push_undo()
                self.rows.pop(idx)
                self.refresh()
                if self.rows:
                    try:
                        self.tree.selection_set(str(min(idx, len(self.rows) - 1)))
                    except Exception:
                        pass
                self._fire_change()
                return
        except Exception:
            pass
        self._push_undo()
        self.rows[idx][self._UI_DELETED] = "1"
        try:
            self.rows[idx].pop(self._UI_ADDED, None)
        except Exception:
            pass
        self.refresh()
        if self.rows:
            try:
                self.tree.selection_set(str(min(idx, len(self.rows) - 1)))
            except Exception:
                pass
        self._fire_change()

    def paste_after_selected(self) -> None:
        self._end_inline_edit(commit=True)
        if self._clipboard is None:
            return
        self._push_undo()

        new_row = self._clone_row(self._clipboard)
        new_row["name"] = self._unique_copy_name((new_row.get("name") or "").strip() or "DA")
        new_row[self._UI_ADDED] = "1"
        new_row.pop(self._UI_DELETED, None)

        idx = self._selected_index()
        if idx is None or idx < 0 or idx >= len(self.rows):
            self.rows.append(new_row)
            self.refresh()
            try:
                self.tree.selection_set(str(len(self.rows) - 1))
            except Exception:
                pass
            self._fire_change()
            return

        insert_at = idx + 1
        self.rows.insert(insert_at, new_row)
        self.refresh()
        try:
            self.tree.selection_set(str(insert_at))
        except Exception:
            pass
        self._fire_change()

    def _move(self, delta: int) -> None:
        self._end_inline_edit(commit=True)
        idx = self._selected_index()
        if idx is None:
            return
        j = idx + delta
        if j < 0 or j >= len(self.rows):
            return
        self._push_undo()
        self.rows[idx], self.rows[j] = self.rows[j], self.rows[idx]
        self.refresh()
        try:
            self.tree.selection_set(str(j))
        except Exception:
            pass
        self._fire_change()

    def _show_context_menu(self, event: tk.Event) -> None:
        self._end_inline_edit(commit=True)

        try:
            row_id = self.tree.identify_row(event.y)
            if row_id:
                self.tree.selection_set(row_id)
        except Exception:
            pass

        idx = self._selected_index()
        can_copy = idx is not None
        can_edit = can_copy
        can_delete = can_copy
        can_paste = self._clipboard is not None
        can_up = idx is not None and idx > 0
        can_down = idx is not None and idx < (len(self.rows) - 1)

        self._menu.entryconfigure("Edit", state=("normal" if can_edit else "disabled"))
        self._menu.entryconfigure("Copy", state=("normal" if can_copy else "disabled"))
        self._menu.entryconfigure("Cut", state=("normal" if can_copy else "disabled"))
        self._menu.entryconfigure("Paste", state=("normal" if can_paste else "disabled"))
        self._menu.entryconfigure("Delete", state=("normal" if can_delete else "disabled"))
        self._menu.entryconfigure("Up", state=("normal" if can_up else "disabled"))
        self._menu.entryconfigure("Down", state=("normal" if can_down else "disabled"))

        try:
            self._menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                self._menu.grab_release()
            except Exception:
                pass

    def _add(self) -> None:
        self._end_inline_edit(commit=True)
        self._push_undo()
        new_row = {
            "name": self._unique_new_name("newDA"),
            "fc": "",
            "bType": "",
            "type": "",
            "valKind": "",
            "valImport": "",
            "dchg": "",
            "val": "",
            "desc": "",
        }
        new_row[self._UI_ADDED] = "1"
        self.rows.append(new_row)
        self.refresh()
        iid = str(len(self.rows) - 1)
        try:
            self.tree.selection_set(iid)
        except Exception:
            pass
        self._begin_inline_edit(iid, "name")
        self._fire_change()

    def _insert(self) -> None:
        self._end_inline_edit(commit=True)
        self._push_undo()
        new_row = {
            "name": self._unique_new_name("newDA"),
            "fc": "",
            "bType": "",
            "type": "",
            "valKind": "",
            "valImport": "",
            "dchg": "",
            "val": "",
            "desc": "",
        }
        new_row[self._UI_ADDED] = "1"
        idx = self._selected_index()
        if idx is None or idx < 0 or idx >= len(self.rows):
            self.rows.append(new_row)
            self.refresh()
            iid = str(len(self.rows) - 1)
            try:
                self.tree.selection_set(iid)
            except Exception:
                pass
            self._begin_inline_edit(iid, "name")
            self._fire_change()
            return

        insert_at = idx + 1
        self.rows.insert(insert_at, new_row)
        self.refresh()
        iid = str(insert_at)
        try:
            self.tree.selection_set(iid)
        except Exception:
            pass
        self._begin_inline_edit(iid, "name")
        self._fire_change()

    def edit_selected(self) -> None:
        self._end_inline_edit(commit=True)
        idx = self._selected_index()
        if idx is None or idx < 0 or idx >= len(self.rows):
            return

        initial = dict(self.rows[idx])
        dlg = DAEditDialog(
            self,
            title="Edit DA",
            initial=initial,
            btype_options=self._btype_options(),
            enum_type_ids=self._enum_type_ids(),
            get_enum_values=lambda enum_id: self._enum_values(enum_id),
            get_enum_preview=lambda enum_id: self._enum_preview_text(enum_id),
        )
        res = dlg.show()
        if res is None:
            return

        new_name = (res.get("name") or "").strip()
        if not new_name:
            return
        if any(i != idx and (x.get("name") or "").strip() == new_name for i, x in enumerate(self.rows)):
            messagebox.showerror("Duplicate", f"DA name already exists: {new_name}", parent=self)
            return

        # Normalize key fields.
        fc = (res.get("fc") or "").strip().upper()
        bt = (res.get("bType") or "").strip()
        typ = (res.get("type") or "").strip()
        val = res.get("val") or ""

        new_row = dict(self.rows[idx])
        new_row.update(
            {
                "name": new_name,
                "fc": fc,
                "bType": bt,
                "type": typ,
                "valKind": (res.get("valKind") or "").strip(),
                "valImport": (res.get("valImport") or "").strip(),
                "dchg": (res.get("dchg") or "").strip(),
                "val": val,
                "desc": (res.get("desc") or ""),
            }
        )

        # Enum consistency: if bType is Enum but type is empty, clear val.
        if (bt or "").strip().upper() == "ENUM":
            if not typ:
                new_row["val"] = ""
            else:
                # If val is not in EnumVal options, clear it (prevents stale values).
                enum_vals = [""] + list(self._enum_values(typ) or [])
                if (new_row.get("val") or "") and (new_row.get("val") not in enum_vals):
                    new_row["val"] = ""

        if new_row == self.rows[idx]:
            return

        self._push_undo()
        self.rows[idx] = new_row
        self.refresh()
        try:
            self.tree.selection_set(str(idx))
        except Exception:
            pass
        self._fire_change()

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
        try:
            col_index = int(col.lstrip("#")) - 1
        except Exception:
            return
        cols = list(self.tree["columns"])
        if col_index < 0 or col_index >= len(cols):
            return
        col_name = cols[col_index]
        # Dropdown cells are edited via single click.
        if self._is_dropdown_cell(iid, col_name):
            return
        self._begin_inline_edit(iid, col_name)

    def _on_left_click(self, event: tk.Event) -> str | None:
        # Single-click opens dropdown editors for select columns (fc/valKind/valImport).
        try:
            region = self.tree.identify("region", event.x, event.y)
            if region != "cell":
                return None

            col = self.tree.identify_column(event.x)
            iid = self.tree.identify_row(event.y)
            if not iid:
                return None

            try:
                self.tree.selection_set(iid)
            except Exception:
                pass

            try:
                col_index = int(col.lstrip("#")) - 1
            except Exception:
                return None
            cols = list(self.tree["columns"])
            if col_index < 0 or col_index >= len(cols):
                return None
            col_name = cols[col_index]

            if not self._is_dropdown_cell(iid, col_name):
                return None

            # If already editing this cell with a combobox, toggle dropdown.
            if (
                isinstance(self._inline, ttk.Combobox)
                and self._inline_iid == iid
                and self._inline_col == col_name
            ):
                self._combobox_toggle_posted(self._inline)
                return "break"

            # Create editor immediately; delays here can cause FocusOut timing glitches.
            self._begin_inline_edit(iid, col_name)
            return "break"
        except Exception:
            return None

    def _begin_inline_edit(self, iid: str, col_name: str) -> None:
        if iid is None or col_name is None:
            return
        try:
            idx = int(iid)
        except Exception:
            return
        if idx < 0 or idx >= len(self.rows):
            return

        self._end_inline_edit(commit=True)

        bbox = self.tree.bbox(iid, column=col_name)
        if not bbox:
            return
        x, y, w, h = bbox
        row = self.rows[idx]
        current = row.get(col_name) or ""

        # bType dropdown: auto-fill from existing values.
        if col_name == "bType":
            opts = list(self._btype_options())
            cur0 = (current or "").strip()
            if cur0 and cur0 not in opts:
                opts = [cur0] + opts

            # bType is required; do not offer an empty selection.
            values = tuple(opts)
            cb = ttk.Combobox(self.tree, state="readonly", values=values)
            cb.place(x=x, y=y, width=w, height=h)
            cb.set(cur0 if cur0 in values else (cur0 or ""))
            cb.focus_set()

            self._inline = cb  # type: ignore[assignment]
            self._inline_iid = iid
            self._inline_col = col_name
            self._inline_started_at = time.monotonic()

            cb.bind("<<ComboboxSelected>>", lambda _e: self._end_inline_edit(commit=True))
            cb.bind("<Return>", lambda _e: self._end_inline_edit(commit=True))
            cb.bind("<Escape>", lambda _e: self._end_inline_edit(commit=False))
            cb.bind("<FocusOut>", self._on_inline_combobox_focus_out)
            cb.bind("<Button-1>", lambda _e: (self._combobox_toggle_posted(cb), "break")[1])
            try:
                self.tree.after_idle(lambda: self._combobox_post(cb))
            except Exception:
                self._combobox_post(cb)
            return

        # Enum integration: when bType=Enum, use dropdown for type and val.
        bt = (row.get("bType") or "").strip().upper()
        if bt == "ENUM" and col_name == "type":
            enum_ids = self._enum_type_ids()
            values = tuple([""] + list(enum_ids))
            cur0 = (current or "").strip()
            cb = ttk.Combobox(self.tree, state="readonly", values=values)
            cb.place(x=x, y=y, width=w, height=h)
            cb.set(cur0 if cur0 in values else (cur0 or ""))
            cb.focus_set()

            self._inline = cb  # type: ignore[assignment]
            self._inline_iid = iid
            self._inline_col = col_name
            self._inline_started_at = time.monotonic()

            cb.bind("<<ComboboxSelected>>", lambda _e: self._end_inline_edit(commit=True))
            cb.bind("<Return>", lambda _e: self._end_inline_edit(commit=True))
            cb.bind("<Escape>", lambda _e: self._end_inline_edit(commit=False))
            cb.bind("<FocusOut>", self._on_inline_combobox_focus_out)
            cb.bind("<Button-1>", lambda _e: (self._combobox_toggle_posted(cb), "break")[1])
            try:
                self.tree.after_idle(lambda: self._combobox_post(cb))
            except Exception:
                self._combobox_post(cb)
            return

        if bt == "ENUM" and col_name == "val":
            enum_id = (row.get("type") or "").strip()
            if enum_id:
                enum_vals = self._enum_values(enum_id)
                values = tuple([""] + list(enum_vals))
                cur0 = (current or "")
                cb = ttk.Combobox(self.tree, state="readonly", values=values)
                cb.place(x=x, y=y, width=w, height=h)
                cb.set(cur0 if cur0 in values else (cur0 or ""))
                cb.focus_set()

                self._inline = cb  # type: ignore[assignment]
                self._inline_iid = iid
                self._inline_col = col_name
                self._inline_started_at = time.monotonic()

                cb.bind("<<ComboboxSelected>>", lambda _e: self._end_inline_edit(commit=True))
                cb.bind("<Return>", lambda _e: self._end_inline_edit(commit=True))
                cb.bind("<Escape>", lambda _e: self._end_inline_edit(commit=False))
                cb.bind("<FocusOut>", self._on_inline_combobox_focus_out)
                cb.bind("<Button-1>", lambda _e: (self._combobox_toggle_posted(cb), "break")[1])
                try:
                    self.tree.after_idle(lambda: self._combobox_post(cb))
                except Exception:
                    self._combobox_post(cb)
                return

        # Use combobox for specific columns.
        if col_name == "fc":
            # FC must not be empty.
            values = ("ST", "MX", "SP", "SV", "CF", "DC", "EX", "CO", "SG", "SE")
            cur = (current or "").strip().upper()
            cb = ttk.Combobox(self.tree, state="readonly", values=values)
            cb.place(x=x, y=y, width=w, height=h)
            cb.set(cur if cur in values else (current or ""))
            cb.focus_set()

            self._inline = cb  # type: ignore[assignment]
            self._inline_iid = iid
            self._inline_col = col_name
            self._inline_started_at = time.monotonic()

            cb.bind("<<ComboboxSelected>>", lambda _e: self._end_inline_edit(commit=True))
            cb.bind("<Return>", lambda _e: self._end_inline_edit(commit=True))
            cb.bind("<Escape>", lambda _e: self._end_inline_edit(commit=False))
            cb.bind("<FocusOut>", self._on_inline_combobox_focus_out)

            # Allow single-click to toggle dropdown while editing.
            cb.bind("<Button-1>", lambda _e: (self._combobox_toggle_posted(cb), "break")[1])
            try:
                self.tree.after_idle(lambda: self._combobox_post(cb))
            except Exception:
                self._combobox_post(cb)
            return

        if col_name == "valKind":
            values = ("", "Set", "Conf", "RO")
            v0 = (current or "").strip()
            u = v0.upper()
            if u == "SET":
                cur = "Set"
            elif u == "CONF":
                cur = "Conf"
            elif u == "RO":
                cur = "RO"
            else:
                cur = v0
            cb = ttk.Combobox(self.tree, state="readonly", values=values)
            cb.place(x=x, y=y, width=w, height=h)
            cb.set(cur if cur in values else (current or ""))
            cb.focus_set()

            self._inline = cb  # type: ignore[assignment]
            self._inline_iid = iid
            self._inline_col = col_name
            self._inline_started_at = time.monotonic()

            cb.bind("<<ComboboxSelected>>", lambda _e: self._end_inline_edit(commit=True))
            cb.bind("<Return>", lambda _e: self._end_inline_edit(commit=True))
            cb.bind("<Escape>", lambda _e: self._end_inline_edit(commit=False))
            cb.bind("<FocusOut>", self._on_inline_combobox_focus_out)

            cb.bind("<Button-1>", lambda _e: (self._combobox_toggle_posted(cb), "break")[1])
            try:
                self.tree.after_idle(lambda: self._combobox_post(cb))
            except Exception:
                self._combobox_post(cb)
            return

        if col_name == "valImport":
            values = ("", "true", "false")
            cur0 = (current or "").strip().lower()
            cb = ttk.Combobox(self.tree, state="readonly", values=values)
            cb.place(x=x, y=y, width=w, height=h)
            cb.set(cur0 if cur0 in values else "")
            cb.focus_set()

            self._inline = cb  # type: ignore[assignment]
            self._inline_iid = iid
            self._inline_col = col_name
            self._inline_started_at = time.monotonic()

            cb.bind("<<ComboboxSelected>>", lambda _e: self._end_inline_edit(commit=True))
            cb.bind("<Return>", lambda _e: self._end_inline_edit(commit=True))
            cb.bind("<Escape>", lambda _e: self._end_inline_edit(commit=False))
            cb.bind("<FocusOut>", self._on_inline_combobox_focus_out)

            cb.bind("<Button-1>", lambda _e: (self._combobox_toggle_posted(cb), "break")[1])
            try:
                self.tree.after_idle(lambda: self._combobox_post(cb))
            except Exception:
                self._combobox_post(cb)
            return

        ent = ttk.Entry(self.tree)
        ent.place(x=x, y=y, width=w, height=h)
        # Use insert() to ensure ent.get() round-trips correctly on commit.
        ent.insert(0, current)
        ent.focus_set()
        try:
            ent.selection_range(0, "end")
        except Exception:
            pass

        self._inline = ent
        self._inline_iid = iid
        self._inline_col = col_name

        ent.bind("<Return>", lambda _e: self._end_inline_edit(commit=True))
        ent.bind("<Escape>", lambda _e: self._end_inline_edit(commit=False))
        ent.bind("<FocusOut>", lambda _e: self._end_inline_edit(commit=True))

    def _end_inline_edit(self, *, commit: bool) -> None:
        ent = self._inline
        iid = self._inline_iid
        col_name = self._inline_col

        if ent is None or iid is None or col_name is None:
            self._inline = None
            self._inline_iid = None
            self._inline_col = None
            return

        try:
            new_val = ent.get()
        except Exception:
            new_val = ""

        try:
            ent.place_forget()
            ent.destroy()
        except Exception:
            pass

        self._inline = None
        self._inline_iid = None
        self._inline_col = None
        self._inline_started_at = None

        if not commit:
            return

        try:
            idx = int(iid)
        except Exception:
            return
        if idx < 0 or idx >= len(self.rows):
            return

        if self._row_is_deleted(self.rows[idx]):
            return

        if col_name == "name":
            new_name = (new_val or "").strip()
            if not new_name:
                messagebox.showerror("Missing", "DA name is required", parent=self)
                return
            if any(
                i != idx and (not self._row_is_deleted(x)) and (x.get("name") or "").strip() == new_name
                for i, x in enumerate(self.rows)
            ):
                messagebox.showerror("Duplicate", f"DA name already exists: {new_name}", parent=self)
                return
            if (self.rows[idx].get("name") or "").strip() == new_name:
                return
            self._push_undo()
            self.rows[idx]["name"] = new_name
        elif col_name == "fc":
            fc_new = (new_val or "").strip().upper()
            if not fc_new:
                messagebox.showerror("Missing", "FC is required (must not be empty)", parent=self)
                return
            if (self.rows[idx].get("fc") or "") == fc_new:
                return
            self._push_undo()
            self.rows[idx]["fc"] = fc_new
        elif col_name == "bType":
            old_bt = (self.rows[idx].get("bType") or "").strip()
            new_bt = (new_val or "").strip()
            if not new_bt and old_bt:
                messagebox.showerror("Missing", "bType is required (must not be empty)", parent=self)
                return
            if old_bt == new_bt:
                return

            old_is_enum = old_bt.strip().upper() == "ENUM"
            new_is_enum = new_bt.strip().upper() == "ENUM"

            self._push_undo()
            self.rows[idx]["bType"] = new_bt

            # If leaving Enum, clear dependent fields.
            if old_is_enum and not new_is_enum:
                self.rows[idx]["type"] = ""
                self.rows[idx]["val"] = ""
            # If entering Enum, clear incompatible stale values.
            if new_is_enum:
                enum_id = (self.rows[idx].get("type") or "").strip()
                if not enum_id or (enum_id not in set(self._enum_type_ids())):
                    self.rows[idx]["type"] = ""
                    self.rows[idx]["val"] = ""
        else:
            if (self.rows[idx].get(col_name) or "") == (new_val or ""):
                return
            # Special case: Enum type change should clear Enum value.
            if col_name == "type" and (self.rows[idx].get("bType") or "").strip().upper() == "ENUM":
                self._push_undo()
                self.rows[idx][col_name] = new_val or ""
                self.rows[idx]["val"] = ""
            else:
                self._push_undo()
                self.rows[idx][col_name] = new_val or ""

        self.refresh()
        try:
            self.tree.selection_set(str(idx))
        except Exception:
            pass
        self._fire_change()



# LN template editor moved to ui/ln_template_tab.py

class MainWindow(tk.Tk):
    def __init__(self, *, workspace_root: Path, open_builder_callback):
        super().__init__()
        self.title(f"{APP_TITLE}")
        self.geometry("1100x720")

        # UX: start maximized by default (Windows).
        try:
            import os

            if os.name == "nt":
                self.after(0, lambda: self.state("zoomed"))
        except Exception:
            pass

        self.workspace_root = workspace_root
        self.open_builder_callback = open_builder_callback

        self.status = tk.StringVar(value="")

        self._create_menu()

        self.body = ttk.Frame(self)
        self.body.pack(fill="both", expand=True)

        self.notebook: ttk.Notebook | None = None
        self.tab_enum_type: ttk.Frame | None = None
        self.tab_do_template: ttk.Frame | None = None
        self.tab_template: ttk.Frame | None = None
        self.tab_instance: ttk.Frame | None = None
        self.tab_application: ttk.Frame | None = None
        self.tab_afg: ttk.Frame | None = None
        self.tab_hmi: ttk.Frame | None = None
        self.instance_editor: LNInstanceEditorFrame | None = None

        self.enum_tab: EnumTab | None = None
        self.do_template_tab: DoTemplateTab | None = None

        # Application editor state
        self._app_file_path: Path | None = None
        self._app_root: ET.Element | None = None
        self._app_funblock: ET.Element | None = None
        self._app_tv_input: ttk.Treeview | None = None
        self._app_tv_setting: ttk.Treeview | None = None
        self._app_tv_output: ttk.Treeview | None = None
        self._app_tv_conf: ttk.Treeview | None = None
        self._app_tv_control: ttk.Treeview | None = None

        self.btn_app_refresh: ttk.Button | None = None

        # Application refresh diff state (added/removed rows)
        self._app_sync_added_names: dict[str, set[str]] = {
            "input": set(),
            "setting": set(),
            "output": set(),
            "conf": set(),
            "control": set(),
        }
        self._app_sync_removed_snapshots: dict[str, list[dict[str, str]]] = {
            "input": [],
            "setting": [],
            "output": [],
            "conf": [],
            "control": [],
        }

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

        # Application undo (Ctrl+Z): snapshot of ALL app tables (+ refresh diff state)
        # to support multi-table ops and refresh rollback.
        self._app_undo_stack: list[dict[str, object]] = []
        self._app_undo_max = 50
        self._app_undoing: bool = False

        self._app_saved_sig: str | None = None
        self._app_loading: bool = False
        self.btn_app_save: ttk.Button | None = None

        # Application "Search" UI state
        self._all_app_files: list[str] = []
        self.var_app_filter: tk.StringVar | None = None
        self.var_app_selected: tk.StringVar | None = None
        self.cb_app: ttk.Combobox | None = None
        self.lbl_app_match: ttk.Label | None = None

        # AFG (Application Group) viewer state (files under datamodel/applicationgroup)
        self._afg_file_path: Path | None = None
        self._afg_root: ET.Element | None = None

        self._afg_saved_sig: bytes | None = None
        self._afg_saved_el_sig_by_id: dict[int, str] | None = None
        self.btn_afg_save: ttk.Button | None = None
        self.btn_afg_refresh: ttk.Button | None = None

        # AFG undo (Ctrl+Z): snapshot of AFG XML + UI diff state.
        self._afg_undo_stack: list[dict[str, object]] = []
        self._afg_undo_max = 50
        self._afg_undoing: bool = False

        self._afg_ui_added_ids: set[int] = set()
        self._afg_ui_deleted_fb_rows: list[tuple[str, str, str, str, str]] = []
        self._afg_ui_deleted_in_rows: list[tuple[str, str, str, str, str, str, str]] = []
        self._afg_ui_deleted_out_rows: list[tuple[str, str, str, str, str]] = []
        self._afg_ui_deleted_arrow_rows: list[tuple[str, str, str, str]] = []

        # AFG -> LN instance (lndm) suggestion state (for doRef dropdowns)
        self._afg_ln_cached_name: str = ""
        self._afg_ln_instance_path: Path | None = None
        self._afg_ln_inref_doref_values: list[str] = []
        self._afg_ln_status_doref_values: list[str] = []
        self._all_afg_files: list[str] = []
        self.var_afg_filter: tk.StringVar | None = None
        self.var_afg_selected: tk.StringVar | None = None
        self.cb_afg: ttk.Combobox | None = None
        self.lbl_afg_match: ttk.Label | None = None

        self._afg_meta_name: tk.StringVar | None = None
        self._afg_meta_proxy: tk.StringVar | None = None
        self._afg_meta_chapter: tk.StringVar | None = None
        self._afg_meta_topic: tk.StringVar | None = None
        self._afg_meta_loading: bool = False
        self._afg_meta_undo_cap: tuple[bytes, dict[str, object]] | None = None

        self._afg_tv_fb: ttk.Treeview | None = None
        self._afg_tv_fb_inputs: ttk.Treeview | None = None
        self._afg_tv_fb_outputs: ttk.Treeview | None = None
        self._afg_tv_in: ttk.Treeview | None = None
        self._afg_tv_out: ttk.Treeview | None = None
        self._afg_tv_arrows: ttk.Treeview | None = None
        self._afg_fb_iid_to_item: dict[str, ET.Element] = {}
        self._afg_fb_clipboard: ET.Element | None = None
        self._afg_fb_ctx_menu: tk.Menu | None = None

        self._afg_fb_inline: tk.Widget | None = None
        self._afg_fb_inline_iid: str | None = None
        self._afg_fb_inline_col: str | None = None

        self._afg_in_iid_to_item: dict[str, ET.Element] = {}
        self._afg_out_iid_to_item: dict[str, ET.Element] = {}
        self._afg_in_clipboard: ET.Element | None = None
        self._afg_out_clipboard: ET.Element | None = None
        self._afg_in_ctx_menu: tk.Menu | None = None
        self._afg_out_ctx_menu: tk.Menu | None = None
        self._afg_arrow_iid_to_item: dict[str, ET.Element] = {}
        self._afg_arrow_ctx_menu: tk.Menu | None = None
        self._afg_arrow_graph_dlg: _AfgArrowGraphDialog | None = None
        self._afg_arrow_graph_syncing: bool = False
        self._afg_arrow_graph_undo_cap: tuple[bytes, dict[str, object]] | None = None
        self._afg_in_inline: tk.Widget | None = None
        self._afg_in_inline_iid: str | None = None
        self._afg_in_inline_col: str | None = None
        self._afg_out_inline: tk.Widget | None = None
        self._afg_out_inline_iid: str | None = None
        self._afg_out_inline_col: str | None = None

        # HMI template editor state (files under datamodel/hmi_template/application)
        self._hmi_file_path: Path | None = None
        self._hmi_root: ET.Element | None = None
        self._hmi_saved_sig: bytes | None = None
        self.btn_hmi_save: ttk.Button | None = None

        # HMI "Search" UI state
        self._all_hmi_files: list[str] = []
        self.var_hmi_filter: tk.StringVar | None = None
        self.var_hmi_selected: tk.StringVar | None = None
        self.cb_hmi: ttk.Combobox | None = None
        self.lbl_hmi_match: ttk.Label | None = None

        # HMI treeviews + mappings
        self._hmi_tv_menus: ttk.Treeview | None = None
        self._hmi_tv_items: ttk.Treeview | None = None
        self._hmi_tv_data: ttk.Treeview | None = None
        self._hmi_menu_iid_to_el: dict[str, tuple[ET.Element, ET.Element]] = {}
        self._hmi_item_iid_to_el: dict[str, tuple[ET.Element, ET.Element]] = {}
        self._hmi_data_iid_to_el: dict[str, tuple[ET.Element, ET.Element]] = {}

        self._set_status("Scanning IEC 61850 types...")
        self.update_idletasks()

        iec61850_dir = self.workspace_root / "ep7_datamodel" / "datamodel" / "iec61850"
        self.iec61850_dir = iec61850_dir
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

        self.tab_enum_type = ttk.Frame(self.notebook)
        self.tab_do_template = ttk.Frame(self.notebook)
        self.tab_template = ttk.Frame(self.notebook)
        self.tab_instance = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_enum_type, text="Enum")
        self.notebook.add(self.tab_do_template, text="DO template")
        self.notebook.add(self.tab_template, text="LN template")
        self.notebook.add(self.tab_instance, text="LN instance")

        try:
            self.notebook.bind("<<NotebookTabChanged>>", lambda _e: self._on_main_notebook_tab_changed())
        except Exception:
            pass

        # Global Ctrl+Z fallback: active on Application + AFG tabs.
        # (Other tabs often bind Ctrl+Z on their Treeviews without returning "break",
        # so we must gate this strictly to avoid cross-tab interference.)
        try:
            self.bind("<Control-z>", self._on_global_ctrl_z)
            self.bind("<Control-Z>", self._on_global_ctrl_z)
        except Exception:
            pass

        # EnumType tab UI (files under iec61850/EnumType)
        if self.tab_enum_type is not None:
            self.enum_tab = EnumTab(
                self.tab_enum_type,
                workspace_root=self.workspace_root,
                catalog=self.catalog,
                set_status=self._set_status,
            )
            self.enum_tab.pack(fill="both", expand=True)

        # DO template tab UI (files under iec61850/DOType)
        if self.tab_do_template is not None:
            self.do_template_tab = DoTemplateTab(
                self.tab_do_template,
                workspace_root=self.workspace_root,
                catalog=self.catalog,
                get_btype_options=self._all_btype_options,
                set_status=self._set_status,
            )
            self.do_template_tab.pack(fill="both", expand=True)

        self.editor = LNodeTypeEditor(
            self.tab_template,
            catalog=self.catalog,
            iec61850_dir=iec61850_dir,
            create_instance_callback=self._create_instance_with_template,
            template_structure_changed_callback=self._on_ln_template_structure_changed,
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

        # Application + AFG tabs moved to ui/application_tab.py and ui/afg_tab.py
        self.tab_application = ApplicationTab(self.notebook, owner=self)
        self.notebook.add(self.tab_application, text="Application")
        self.tab_afg = AfgTab(self.notebook, owner=self)
        self.notebook.add(self.tab_afg, text="AFG")

        self.tab_hmi = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_hmi, text="HMI")

        # HMI tab UI (files under datamodel/hmi_template/application)
        if self.tab_hmi is not None:
            toolbar = ttk.Frame(self.tab_hmi, padding=(10, 10, 10, 0))
            toolbar.pack(fill="x")
            ttk.Button(toolbar, text="New", command=self._new_hmi).pack(side="left")
            ttk.Button(toolbar, text="Open", command=self._open_hmi).pack(side="left", padx=(8, 0))
            self.btn_hmi_save = ttk.Button(toolbar, text="Save", command=self._save_hmi)
            self.btn_hmi_save.pack(side="left", padx=(8, 0))
            ttk.Button(toolbar, text="Save As", command=self._save_hmi_as).pack(side="left", padx=(8, 0))
            ttk.Button(toolbar, text="Generate", command=self._hmi_generate_from_application).pack(
                side="left", padx=(18, 0)
            )

            row2 = ttk.Frame(self.tab_hmi, padding=(10, 8, 10, 0))
            row2.pack(fill="x")
            ttk.Label(row2, text="Search").pack(side="left")
            self.var_hmi_filter = tk.StringVar(value="")
            ent_filter = ttk.Entry(row2, textvariable=self.var_hmi_filter, width=28)
            ent_filter.pack(side="left", padx=(8, 0))

            self.var_hmi_selected = tk.StringVar(value="")
            self.cb_hmi = ttk.Combobox(row2, textvariable=self.var_hmi_selected, values=[], width=66)
            self.cb_hmi.pack(side="left", padx=(10, 0))
            ttk.Button(row2, text="Load", command=self._open_hmi_from_search).pack(side="left", padx=(8, 0))

            self.lbl_hmi_match = ttk.Label(row2, text="")
            self.lbl_hmi_match.pack(side="left", padx=(10, 0))

            try:
                if self.cb_hmi is not None:
                    self.cb_hmi.bind("<Return>", lambda _e: self._open_hmi_from_search())
            except Exception:
                pass

            body = ttk.Frame(self.tab_hmi, padding=10)
            body.pack(fill="both", expand=True)
            body.columnconfigure(0, weight=1)
            body.columnconfigure(1, weight=2)
            body.columnconfigure(2, weight=2)
            body.rowconfigure(1, weight=1)

            btns = ttk.Frame(body)
            btns.grid(row=0, column=0, columnspan=3, sticky="we", pady=(0, 8))
            ttk.Button(btns, text="Add Menu", command=self._hmi_menu_add).pack(side="left")
            ttk.Button(btns, text="Edit Menu", command=self._hmi_menu_edit).pack(side="left", padx=(6, 0))
            ttk.Button(btns, text="Delete Menu", command=self._hmi_menu_delete).pack(side="left", padx=(6, 0))
            ttk.Separator(btns, orient="vertical").pack(side="left", fill="y", padx=12)
            ttk.Button(btns, text="Add Item", command=self._hmi_item_add).pack(side="left")
            ttk.Button(btns, text="Edit Item", command=self._hmi_item_edit).pack(side="left", padx=(6, 0))
            ttk.Button(btns, text="Delete Item", command=self._hmi_item_delete).pack(side="left", padx=(6, 0))
            ttk.Separator(btns, orient="vertical").pack(side="left", fill="y", padx=12)
            ttk.Button(btns, text="Add Data", command=self._hmi_data_add).pack(side="left")
            ttk.Button(btns, text="Edit Data", command=self._hmi_data_edit).pack(side="left", padx=(6, 0))
            ttk.Button(btns, text="Delete Data", command=self._hmi_data_delete).pack(side="left", padx=(6, 0))

            menus_box = ttk.LabelFrame(body, text="Menus (Menu_*)", padding=6)
            menus_box.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
            menus_box.columnconfigure(0, weight=1)
            menus_box.rowconfigure(0, weight=1)
            self._hmi_tv_menus = ttk.Treeview(
                menus_box,
                columns=("name", "desc", "dataType", "viewType"),
                show="headings",
                selectmode="browse",
            )
            for c, h, w, a in (
                ("name", "name", 300, "w"),
                ("desc", "desc", 180, "w"),
                ("dataType", "hmiMenuDataType", 120, "w"),
                ("viewType", "hmiMenuViewType", 120, "w"),
            ):
                self._hmi_tv_menus.heading(c, text=h)
                self._hmi_tv_menus.column(c, width=w, anchor=a)
            self._hmi_tv_menus.grid(row=0, column=0, sticky="nsew")
            sb_m = ttk.Scrollbar(menus_box, orient="vertical", command=self._hmi_tv_menus.yview)
            self._hmi_tv_menus.configure(yscrollcommand=sb_m.set)
            sb_m.grid(row=0, column=1, sticky="ns")

            items_box = ttk.LabelFrame(body, text="Menu Items", padding=6)
            items_box.grid(row=1, column=1, sticky="nsew", padx=(0, 8))
            items_box.columnconfigure(0, weight=1)
            items_box.rowconfigure(0, weight=1)
            self._hmi_tv_items = ttk.Treeview(
                items_box,
                columns=("kind", "name", "ref", "doRef", "daRef"),
                show="headings",
                selectmode="browse",
            )
            for c, h, w, a in (
                ("kind", "kind", 60, "center"),
                ("name", "name", 180, "w"),
                ("ref", "ref", 220, "w"),
                ("doRef", "doRef", 220, "w"),
                ("daRef", "daRef", 120, "w"),
            ):
                self._hmi_tv_items.heading(c, text=h)
                self._hmi_tv_items.column(c, width=w, anchor=a)
            self._hmi_tv_items.grid(row=0, column=0, sticky="nsew")
            sb_i = ttk.Scrollbar(items_box, orient="vertical", command=self._hmi_tv_items.yview)
            self._hmi_tv_items.configure(yscrollcommand=sb_i.set)
            sb_i.grid(row=0, column=1, sticky="ns")

            data_box = ttk.LabelFrame(body, text="HMIDataItem (for selected item)", padding=6)
            data_box.grid(row=1, column=2, sticky="nsew")
            data_box.columnconfigure(0, weight=1)
            data_box.rowconfigure(0, weight=1)
            self._hmi_tv_data = ttk.Treeview(
                data_box,
                columns=("name", "doRef", "daRef"),
                show="headings",
                selectmode="browse",
            )
            for c, h, w, a in (
                ("name", "name", 160, "w"),
                ("doRef", "doRef", 260, "w"),
                ("daRef", "daRef", 140, "w"),
            ):
                self._hmi_tv_data.heading(c, text=h)
                self._hmi_tv_data.column(c, width=w, anchor=a)
            self._hmi_tv_data.grid(row=0, column=0, sticky="nsew")
            sb_d = ttk.Scrollbar(data_box, orient="vertical", command=self._hmi_tv_data.yview)
            self._hmi_tv_data.configure(yscrollcommand=sb_d.set)
            sb_d.grid(row=0, column=1, sticky="ns")

            try:
                if self._hmi_tv_menus is not None:
                    self._hmi_tv_menus.bind("<<TreeviewSelect>>", lambda _e: self._hmi_on_menu_selected())
                    self._hmi_tv_menus.bind("<Double-1>", lambda _e: self._hmi_menu_edit())
                if self._hmi_tv_items is not None:
                    self._hmi_tv_items.bind("<<TreeviewSelect>>", lambda _e: self._hmi_on_item_selected())
                    self._hmi_tv_items.bind("<Double-1>", lambda _e: self._hmi_item_edit())
                if self._hmi_tv_data is not None:
                    self._hmi_tv_data.bind("<Double-1>", lambda _e: self._hmi_data_edit())
            except Exception:
                pass

            self._refresh_hmi_search_list(select_rel=None)
            try:
                self._mark_hmi_saved()
            except Exception:
                pass

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

    def _all_btype_options(self) -> list[str]:
        """Return a stable list of bType values found in the datamodel.

        This is used to populate the DO template editor bType dropdown even when
        the current file/new template doesn't contain any bType values yet.
        """
        cache = getattr(self, "_btype_options_cache", None)
        if cache is not None:
            return list(cache)

        def add_value(mapping: dict[str, str], raw: str) -> None:
            v = (raw or "").strip()
            if not v:
                return
            k = v.upper()
            # Prefer common casing for Enum.
            if k == "ENUM":
                v = "Enum"
            if k not in mapping:
                mapping[k] = v

        found: dict[str, str] = {}
        pattern = re.compile(r"\bbType=\"([^\"]+)\"", flags=re.IGNORECASE)
        for folder_name in ("DOType", "DAType"):
            base = self.iec61850_dir / folder_name
            if not base.exists():
                continue
            for p in base.rglob("*.xml"):
                try:
                    txt = p.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue
                for m in pattern.finditer(txt):
                    add_value(found, m.group(1))

        # If scanning fails for any reason, keep a small safe fallback.
        if not found:
            for v in ("Enum", "BOOLEAN", "INT32", "FLOAT32", "FLOAT64", "Quality", "Timestamp", "VisString255"):
                add_value(found, v)

        preferred = [
            "BOOLEAN",
            "INT8",
            "INT16",
            "INT32",
            "INT64",
            "INT8U",
            "INT16U",
            "INT32U",
            "INT64U",
            "FLOAT32",
            "FLOAT64",
            "ENUM",
            "QUALITY",
            "TIMESTAMP",
        ]

        preferred_out: list[str] = []
        used: set[str] = set()
        for k in preferred:
            if k in found:
                preferred_out.append(found[k])
                used.add(k)

        rest = [v for k, v in found.items() if k not in used]
        rest.sort(key=lambda s: (s or "").lower())
        out = preferred_out + rest

        setattr(self, "_btype_options_cache", list(out))
        return list(out)

    def _create_instance_with_template(self, model: LNodeTypeModel) -> None:
        if self.instance_editor is None:
            return
        try:
            self.notebook.select(self.tab_instance)
        except Exception:
            pass
        self.instance_editor.create_instance_with_template_model(model)

    def _on_ln_template_structure_changed(self, model: LNodeTypeModel) -> None:
        if self.instance_editor is None or self.instance_editor.doc is None:
            return
        try:
            tpl_id = (model.info.id or "").strip()
        except Exception:
            tpl_id = ""
        if not tpl_id:
            return

        # Only refresh the instance if the currently-selected LN matches this template.
        ln_type = ""
        try:
            ln_type = (self.instance_editor.var_lnType.get() or "").strip()
        except Exception:
            ln_type = ""

        if not ln_type:
            try:
                idx = getattr(self.instance_editor, "_current_ln_index", 0)
                ln = self.instance_editor.doc.ln_elements[idx]
                ln_type = (ln.attrib.get("lnType") or "").strip()
            except Exception:
                ln_type = ""

        if (ln_type or "") != tpl_id:
            return

        try:
            self.instance_editor.refresh_from_template_model(model)
        except Exception:
            pass

    def _active_tab(self) -> int:
        return self.notebook.index("current") if self.notebook is not None else 0

    def _ensure_dirty_button_style(self) -> None:
        try:
            style = ttk.Style(self)
            style.configure("Dirty.TButton", foreground="#C00000")
        except Exception:
            pass

    def _set_save_button_dirty(self, btn: ttk.Button | None, *, dirty: bool) -> None:
        if btn is None:
            return
        self._ensure_dirty_button_style()
        try:
            if dirty:
                btn.configure(text="Save *", style="Dirty.TButton")
            else:
                btn.configure(text="Save", style="TButton")
        except Exception:
            pass

    def _on_main_notebook_tab_changed(self) -> None:
        # When switching tabs, make sure the visible tab's Save reflects current dirty state.
        try:
            self._update_dirty_ui_enum()
        except Exception:
            pass
        try:
            self._update_dirty_ui_do_tmpl()
        except Exception:
            pass
        try:
            self._update_dirty_ui_application()
        except Exception:
            pass
        try:
            self._update_app_refresh_button_state()
        except Exception:
            pass
        try:
            self._update_dirty_ui_afg()
        except Exception:
            pass
        try:
            self._update_dirty_ui_hmi()
        except Exception:
            pass

    def _on_global_ctrl_z(self, _event: tk.Event) -> str | None:
        # Only apply as a fallback on Application/AFG tabs.
        try:
            if self.notebook is None:
                return None
            active = self.notebook.select()
            is_app = self.tab_application is not None and active == str(self.tab_application)
            is_afg = self.tab_afg is not None and active == str(self.tab_afg)
            if not (is_app or is_afg):
                return None
        except Exception:
            return None

        w = None
        try:
            w = self.focus_get()
        except Exception:
            w = None

        # If focus isn't within the Application tab, don't interfere.
        try:
            if w is not None:
                if self.tab_application is not None and str(w).startswith(str(self.tab_application)):
                    pass
                elif self.tab_afg is not None and str(w).startswith(str(self.tab_afg)):
                    pass
                else:
                    return None
        except Exception:
            pass

        # Don't steal Ctrl+Z from text-like widgets.
        try:
            if w is not None and (w.winfo_class() in {"Entry", "TEntry", "Text", "TCombobox", "Combobox", "Spinbox", "TSpinbox"}):
                return None
        except Exception:
            pass

        try:
            if self.notebook is not None and self.tab_application is not None and self.notebook.select() == str(self.tab_application):
                self._app_undo()
            elif self.notebook is not None and self.tab_afg is not None and self.notebook.select() == str(self.tab_afg):
                self._afg_undo()
            else:
                return None
        except Exception:
            return None
        return "break"

    def _update_dirty_ui_enum(self) -> None:
        if self.enum_tab is None:
            return
        try:
            self.enum_tab.update_dirty_ui()
        except Exception:
            pass

    def _on_enum_view_changed(self) -> None:
        self._update_dirty_ui_enum()

    def _mark_enum_saved(self) -> None:
        if self.enum_tab is None:
            return
        try:
            self.enum_tab.mark_saved()
        except Exception:
            pass

    def _mark_enum_unsaved(self) -> None:
        if self.enum_tab is None:
            return
        try:
            self.enum_tab.mark_unsaved()
        except Exception:
            pass

    def _update_dirty_ui_do_tmpl(self) -> None:
        if self.do_template_tab is None:
            return
        try:
            self.do_template_tab.update_dirty_ui()
        except Exception:
            pass

    def _on_do_tmpl_view_changed(self) -> None:
        self._update_dirty_ui_do_tmpl()

    def _mark_do_tmpl_saved(self) -> None:
        if self.do_template_tab is None:
            return
        try:
            self.do_template_tab.mark_saved()
        except Exception:
            pass

    def _mark_do_tmpl_unsaved(self) -> None:
        if self.do_template_tab is None:
            return
        try:
            self.do_template_tab.mark_unsaved()
        except Exception:
            pass

    def _wire_application_funblock_traces(self) -> None:
        if getattr(self, "_app_traces_wired", False):
            return
        if self.instance_editor is None:
            return
        vars_to_trace = [
            self.instance_editor.var_app_name,
            self.instance_editor.var_app_class,
            self.instance_editor.var_app_seqNb,
            self.instance_editor.var_app_LnRef,
            self.instance_editor.var_app_desc,
        ]
        for v in vars_to_trace:
            try:
                v.trace_add("write", lambda *_args: self._on_app_view_changed())
            except Exception:
                pass
        setattr(self, "_app_traces_wired", True)

    def _application_signature_from_view(self) -> str:
        # Signature based on what will be written on Save.
        if self.instance_editor is None:
            fb = ("", "", "", "", "")
        else:
            fb = (
                (self.instance_editor.var_app_name.get() or "").strip(),
                (self.instance_editor.var_app_class.get() or "").strip(),
                (self.instance_editor.var_app_seqNb.get() or "").strip(),
                (self.instance_editor.var_app_LnRef.get() or "").strip(),
                (self.instance_editor.var_app_desc.get() or ""),
            )

        in_keys = ["name", "type", "desc", "src", "doRef", "softlink", "confpin"]
        simple_keys = ["name", "type", "src", "desc"]
        out_keys = [
            "name",
            "type",
            "desc",
            "outPurpose",
            "srvRef",
            "persist",
            "doRef",
            "MaxContiguous",
            "Overlap",
            "faultlog",
        ]

        def not_deleted(rr: dict[str, str]) -> bool:
            try:
                return not bool(rr.get("__ui_deleted"))
            except Exception:
                return True

        norm_in = [tuple((r.get(k) or "") for k in in_keys) for r in (self._app_input_rows or []) if not_deleted(r)]
        norm_set = [tuple((r.get(k) or "") for k in simple_keys) for r in (self._app_setting_rows or []) if not_deleted(r)]
        norm_out = [tuple((r.get(k) or "") for k in out_keys) for r in (self._app_output_rows or []) if not_deleted(r)]
        norm_conf = [tuple((r.get(k) or "") for k in simple_keys) for r in (self._app_conf_rows or []) if not_deleted(r)]
        norm_ctl = [tuple((r.get(k) or "") for k in simple_keys) for r in (self._app_control_rows or []) if not_deleted(r)]
        return repr((fb, norm_in, norm_set, norm_out, norm_conf, norm_ctl))

    def _update_dirty_ui_application(self) -> None:
        if getattr(self, "_app_loading", False):
            return
        cur = self._application_signature_from_view()
        dirty = (self._app_saved_sig is None) or (cur != self._app_saved_sig)
        self._set_save_button_dirty(self.btn_app_save, dirty=dirty)

    def _app_table_saved_keys(self, table: str) -> list[str]:
        # Keys that matter for Save (and therefore for "changed" highlight)
        if table == "input":
            return ["name", "type", "desc", "src", "doRef", "softlink", "confpin"]
        if table == "output":
            return [
                "name",
                "type",
                "desc",
                "outPurpose",
                "srvRef",
                "persist",
                "doRef",
                "MaxContiguous",
                "Overlap",
                "faultlog",
            ]
        # setting / conf / control
        return ["name", "type", "src", "desc"]

    def _app_snapshot_rows_by_name(self, table: str, rows: list[dict[str, str]]) -> dict[str, tuple[str, ...]]:
        keys = self._app_table_saved_keys(table)
        out: dict[str, tuple[str, ...]] = {}
        for r in (rows or []):
            try:
                if bool(r.get("__ui_deleted")):
                    continue
            except Exception:
                pass
            nm = (r.get("name") or "").strip()
            if not nm:
                continue
            if nm in out:
                # Ignore duplicates in baseline; duplicates are unusual and are treated as separate rows.
                continue
            out[nm] = tuple((r.get(k) or "") for k in keys)
        return out

    def _update_application_changed_row_state(self) -> None:
        """Recompute per-table changed row names vs last saved snapshot."""
        saved = getattr(self, "_app_saved_snapshot_by_table", None)
        if not isinstance(saved, dict):
            saved = {}
            setattr(self, "_app_saved_snapshot_by_table", saved)

        changed = getattr(self, "_app_changed_names", None)
        if not isinstance(changed, dict):
            changed = {}
            setattr(self, "_app_changed_names", changed)

        # If no saved signature, treat as no baseline => no "changed" highlighting.
        if self._app_saved_sig is None:
            for t in ("input", "setting", "output", "conf", "control"):
                changed[t] = set()
            return

        cur_map = {
            "input": self._app_snapshot_rows_by_name("input", getattr(self, "_app_input_rows", []) or []),
            "setting": self._app_snapshot_rows_by_name("setting", getattr(self, "_app_setting_rows", []) or []),
            "output": self._app_snapshot_rows_by_name("output", getattr(self, "_app_output_rows", []) or []),
            "conf": self._app_snapshot_rows_by_name("conf", getattr(self, "_app_conf_rows", []) or []),
            "control": self._app_snapshot_rows_by_name("control", getattr(self, "_app_control_rows", []) or []),
        }

        for t, cur in cur_map.items():
            base = saved.get(t) or {}
            ch: set[str] = set()
            # Added or modified rows
            for nm, sig in cur.items():
                if nm not in base:
                    ch.add(nm)
                elif base.get(nm) != sig:
                    ch.add(nm)
            # Removed rows are already shown via refresh snapshots; do not mark anything else here.
            changed[t] = ch

    def _reapply_application_row_tags(self) -> None:
        """Apply added/changed tags to currently-rendered rows without rebuilding tables."""
        # input table uses a custom iid-to-row map
        try:
            for iid in list(getattr(self, "_app_input_iid_to_row", {}).keys()):
                self._update_app_input_tv_row(iid)
        except Exception:
            pass

        # simple tables use numeric iids
        for t in ("setting", "output", "conf", "control"):
            tv = self._app_table_tv(t)
            if tv is None:
                continue
            try:
                for iid in tv.get_children(""):
                    self._update_simple_app_tv_row(t, iid)
            except Exception:
                pass

    def _on_app_view_changed(self) -> None:
        if getattr(self, "_app_loading", False):
            return
        try:
            self._update_application_changed_row_state()
        except Exception:
            pass
        try:
            self._reapply_application_row_tags()
        except Exception:
            pass
        self._update_dirty_ui_application()

    def _mark_application_saved(self) -> None:
        # Save boundary: apply pending deletions and clear added/deleted UI-only flags.
        try:
            def purge(rows0: list[dict[str, str]]) -> list[dict[str, str]]:
                out0: list[dict[str, str]] = []
                for r in (rows0 or []):
                    try:
                        if bool(r.get("__ui_deleted")):
                            continue
                    except Exception:
                        pass
                    rr = dict(r)
                    rr.pop("__ui_added", None)
                    rr.pop("__ui_deleted", None)
                    out0.append(rr)
                return out0

            self._app_input_rows = purge(getattr(self, "_app_input_rows", []) or [])
            self._app_setting_rows = purge(getattr(self, "_app_setting_rows", []) or [])
            self._app_output_rows = purge(getattr(self, "_app_output_rows", []) or [])
            self._app_conf_rows = purge(getattr(self, "_app_conf_rows", []) or [])
            self._app_control_rows = purge(getattr(self, "_app_control_rows", []) or [])

            # Also clear Application Refresh diff highlights (added/removed) on Save.
            self._clear_app_refresh_diff_state()

            # Refresh tables so removed rows disappear.
            try:
                self._refresh_app_input_tv()
            except Exception:
                pass
            for t in ("setting", "output", "conf", "control"):
                try:
                    self._refresh_simple_app_tv(t)
                except Exception:
                    pass
        except Exception:
            pass

        try:
            self._app_saved_sig = self._application_signature_from_view()
        except Exception:
            self._app_saved_sig = ""

        # Update saved baseline used for "changed" row highlighting.
        try:
            snap = getattr(self, "_app_saved_snapshot_by_table", None)
            if not isinstance(snap, dict):
                snap = {}
                setattr(self, "_app_saved_snapshot_by_table", snap)
            snap["input"] = self._app_snapshot_rows_by_name("input", getattr(self, "_app_input_rows", []) or [])
            snap["setting"] = self._app_snapshot_rows_by_name("setting", getattr(self, "_app_setting_rows", []) or [])
            snap["output"] = self._app_snapshot_rows_by_name("output", getattr(self, "_app_output_rows", []) or [])
            snap["conf"] = self._app_snapshot_rows_by_name("conf", getattr(self, "_app_conf_rows", []) or [])
            snap["control"] = self._app_snapshot_rows_by_name("control", getattr(self, "_app_control_rows", []) or [])
        except Exception:
            pass

        try:
            self._update_application_changed_row_state()
        except Exception:
            pass
        self._update_dirty_ui_application()

    def _mark_application_unsaved(self) -> None:
        self._app_saved_sig = None
        try:
            # No baseline => no changed highlighting.
            changed = getattr(self, "_app_changed_names", None)
            if isinstance(changed, dict):
                for t in ("input", "setting", "output", "conf", "control"):
                    changed[t] = set()
        except Exception:
            pass
        self._update_dirty_ui_application()

    def _afg_signature_from_model(self) -> bytes:
        if self._afg_root is None:
            return b""
        try:
            return ET.tostring(self._afg_root, encoding="utf-8", short_empty_elements=True)
        except Exception:
            try:
                return (ET.tostring(self._afg_root, encoding="unicode") or "").encode("utf-8", errors="ignore")
            except Exception:
                return b""

    def _update_dirty_ui_afg(self) -> None:
        cur = self._afg_signature_from_model()
        dirty = (self._afg_saved_sig is None) or (cur != self._afg_saved_sig)
        self._set_save_button_dirty(self.btn_afg_save, dirty=dirty)

        # Refresh only makes sense with a loaded AFG.
        try:
            if self.btn_afg_refresh is not None:
                self.btn_afg_refresh.configure(state=("normal" if self._afg_root is not None else "disabled"))
        except Exception:
            pass

    def _afg_el_sig(self, el: ET.Element) -> str:
        # pinID is not user-editable and may change due to normalization/refresh;
        # do not treat pinID-only changes as a "changed" row.
        #
        # - AFG Inputs/Outputs: pinID lives on the item itself.
        # - AFB rows (fbItem): pinID lives on descendant IO pin items.
        try:
            local = _local_name(el.tag) if isinstance(el.tag, str) else ""
        except Exception:
            local = ""

        el2 = el
        if local in {"afgInItem", "afgOutItem"}:
            try:
                el2 = _deepcopy_et_element(el)
                try:
                    el2.attrib.pop("pinID", None)
                except Exception:
                    pass
            except Exception:
                el2 = el

        elif local == "fbItem":
            def strip_pinid_rec(n: ET.Element) -> None:
                try:
                    n.attrib.pop("pinID", None)
                except Exception:
                    pass
                for ch in list(n):
                    if isinstance(ch, ET.Element):
                        strip_pinid_rec(ch)

            try:
                el2 = _deepcopy_et_element(el)
                strip_pinid_rec(el2)
            except Exception:
                el2 = el

        elif local == "arrowItem":
            # Arrow display depends on the connected pin labels, not just pinID.
            # Highlight arrows when their logical endpoints' labels change.
            try:
                pin_map = self._afg_pin_id_display_map()
                sp = (el.attrib.get("startPinID") or "").strip()
                ep = (el.attrib.get("endPinID") or "").strip()
                # Use labels to avoid pinID-only diffs (pinIDs may be normalized).
                key = (pin_map.get(sp, ""), pin_map.get(ep, ""))
                return hashlib.sha1(repr(key).encode("utf-8", errors="ignore")).hexdigest()
            except Exception:
                pass

        try:
            b = ET.tostring(el2, encoding="utf-8", short_empty_elements=True)
        except Exception:
            try:
                b = (ET.tostring(el2, encoding="unicode") or "").encode("utf-8", errors="ignore")
            except Exception:
                b = b""
        return hashlib.sha1(b).hexdigest()

    def _afg_snapshot_el_sigs(self) -> dict[int, str]:
        root = self._afg_root
        if root is None:
            return {}

        out: dict[int, str] = {}

        fb_items_el = self._afg_get_child(root, "fbItems")
        if fb_items_el is not None:
            for fb in list(fb_items_el):
                if isinstance(fb.tag, str) and _local_name(fb.tag) == "fbItem":
                    out[id(fb)] = self._afg_el_sig(fb)

        in_items_el = self._afg_get_child(root, "afgInItems")
        if in_items_el is not None:
            for it in list(in_items_el):
                if isinstance(it.tag, str) and _local_name(it.tag) == "afgInItem":
                    out[id(it)] = self._afg_el_sig(it)

        out_items_el = self._afg_get_child(root, "afgOutItems")
        if out_items_el is not None:
            for it in list(out_items_el):
                if isinstance(it.tag, str) and _local_name(it.tag) == "afgOutItem":
                    out[id(it)] = self._afg_el_sig(it)

        arrows_el = self._afg_get_child(root, "arrows")
        if arrows_el is not None:
            for ar in list(arrows_el):
                if isinstance(ar.tag, str) and _local_name(ar.tag) == "arrowItem":
                    out[id(ar)] = self._afg_el_sig(ar)

        return out

    def _afg_row_tags_for_el(self, el: ET.Element) -> tuple[str, ...]:
        if id(el) in (self._afg_ui_added_ids or set()):
            return ("added",)
        saved = self._afg_saved_el_sig_by_id
        if saved is None:
            return ()
        cur = self._afg_el_sig(el)
        prev = saved.get(id(el))
        if prev is None:
            return ("added",)
        if cur != prev:
            return ("changed",)
        return ()

    def _on_afg_view_changed(self) -> None:
        self._update_dirty_ui_afg()

    def _mark_afg_saved(self) -> None:
        self._afg_saved_sig = self._afg_signature_from_model()
        try:
            self._afg_saved_el_sig_by_id = self._afg_snapshot_el_sigs()
        except Exception:
            self._afg_saved_el_sig_by_id = None

        # Save boundary: clear transient UI states.
        try:
            self._afg_ui_added_ids.clear()
        except Exception:
            pass
        self._afg_ui_deleted_fb_rows = []
        self._afg_ui_deleted_in_rows = []
        self._afg_ui_deleted_out_rows = []
        self._afg_ui_deleted_arrow_rows = []

        try:
            self._refresh_afg_views(select_first_fb=False)
        except Exception:
            pass
        self._update_dirty_ui_afg()

    def _mark_afg_unsaved(self) -> None:
        self._afg_saved_sig = None
        self._update_dirty_ui_afg()

    def _afg_snapshot_state_for_undo(self) -> dict[str, object] | None:
        root = self._afg_root
        if root is None:
            return None

        cloned_root, id_map = _clone_et_element_with_id_map(root)

        saved_el = None
        try:
            if self._afg_saved_el_sig_by_id is not None:
                saved_el = {id_map[k]: v for k, v in self._afg_saved_el_sig_by_id.items() if k in id_map}
        except Exception:
            saved_el = None

        added_ids: set[int] = set()
        try:
            for old_id in (self._afg_ui_added_ids or set()):
                if old_id in id_map:
                    added_ids.add(id_map[old_id])
        except Exception:
            added_ids = set()

        snap: dict[str, object] = {
            "root": cloned_root,
            "file_path": self._afg_file_path,
            "saved_sig": self._afg_saved_sig,
            "saved_el_sig_by_id": saved_el,
            "ui_added_ids": set(added_ids),
            "ui_deleted_fb_rows": list(self._afg_ui_deleted_fb_rows or []),
            "ui_deleted_in_rows": list(self._afg_ui_deleted_in_rows or []),
            "ui_deleted_out_rows": list(self._afg_ui_deleted_out_rows or []),
            "ui_deleted_arrow_rows": list(self._afg_ui_deleted_arrow_rows or []),
        }
        return snap

    def _afg_begin_undo_capture(self) -> tuple[bytes, dict[str, object]] | None:
        if self._afg_root is None:
            return None
        if getattr(self, "_afg_undoing", False):
            return None
        before_sig = self._afg_signature_from_model()
        snap = self._afg_snapshot_state_for_undo()
        if snap is None:
            return None
        return (before_sig, snap)

    def _afg_end_undo_capture(self, cap: tuple[bytes, dict[str, object]] | None) -> None:
        if cap is None:
            return
        before_sig, snap = cap
        after_sig = self._afg_signature_from_model()
        if after_sig == before_sig:
            return
        self._afg_undo_stack.append(snap)
        if len(self._afg_undo_stack) > self._afg_undo_max:
            self._afg_undo_stack = self._afg_undo_stack[-self._afg_undo_max :]

    def _afg_restore_undo_snapshot(self, snap: dict[str, object]) -> None:
        root = snap.get("root")
        if not isinstance(root, ET.Element):
            return
        self._afg_root = root
        self._afg_file_path = snap.get("file_path") if isinstance(snap.get("file_path"), Path) else snap.get("file_path")
        self._afg_saved_sig = snap.get("saved_sig") if isinstance(snap.get("saved_sig"), (bytes, type(None))) else self._afg_saved_sig
        saved_el = snap.get("saved_el_sig_by_id")
        self._afg_saved_el_sig_by_id = saved_el if isinstance(saved_el, (dict, type(None))) else self._afg_saved_el_sig_by_id

        added_ids = snap.get("ui_added_ids")
        self._afg_ui_added_ids = set(added_ids) if isinstance(added_ids, set) else set()

        self._afg_ui_deleted_fb_rows = list(snap.get("ui_deleted_fb_rows") or [])
        self._afg_ui_deleted_in_rows = list(snap.get("ui_deleted_in_rows") or [])
        self._afg_ui_deleted_out_rows = list(snap.get("ui_deleted_out_rows") or [])
        self._afg_ui_deleted_arrow_rows = list(snap.get("ui_deleted_arrow_rows") or [])

        # Reset LN suggestion cache; will be reloaded lazily based on restored name.
        try:
            self._afg_reset_ln_suggestions()
        except Exception:
            pass

        try:
            self._refresh_afg_views(select_first_fb=False)
        except Exception:
            pass
        try:
            self._on_afg_view_changed()
        except Exception:
            pass

    def _afg_undo(self) -> None:
        if not getattr(self, "_afg_undo_stack", None) and self._afg_arrow_graph_undo_cap is None:
            return

        # Close inline editors without committing.
        try:
            self._end_afg_in_inline_editor(commit=False)
            self._end_afg_out_inline_editor(commit=False)
            self._end_afg_fb_inline_editor(commit=False)
        except Exception:
            pass

        # Commit pending meta edits (name/proxy/chapter/topic) into undo stack.
        try:
            self._afg_end_undo_capture(self._afg_meta_undo_cap)
        except Exception:
            pass
        self._afg_meta_undo_cap = None

        # Arrow graph dialog holds direct reference to root; close it.
        try:
            if self._afg_arrow_graph_dlg is not None and bool(self._afg_arrow_graph_dlg.winfo_exists()):
                self._afg_arrow_graph_dlg.destroy()
        except Exception:
            pass
        self._afg_arrow_graph_dlg = None

        # If the graph dialog performed in-place edits, commit its pending undo capture now.
        try:
            self._afg_end_undo_capture(self._afg_arrow_graph_undo_cap)
        except Exception:
            pass
        self._afg_arrow_graph_undo_cap = None

        if not self._afg_undo_stack:
            return

        snap = self._afg_undo_stack.pop()
        self._afg_undoing = True
        try:
            self._afg_restore_undo_snapshot(snap)
        finally:
            self._afg_undoing = False

    def _hmi_signature_from_model(self) -> bytes:
        if self._hmi_root is None:
            return b""
        try:
            return ET.tostring(self._hmi_root, encoding="utf-8", short_empty_elements=True)
        except Exception:
            try:
                return (ET.tostring(self._hmi_root, encoding="unicode") or "").encode("utf-8", errors="ignore")
            except Exception:
                return b""

    def _update_dirty_ui_hmi(self) -> None:
        cur = self._hmi_signature_from_model()
        dirty = (self._hmi_saved_sig is None) or (cur != self._hmi_saved_sig)
        self._set_save_button_dirty(self.btn_hmi_save, dirty=dirty)

    def _mark_hmi_saved(self) -> None:
        self._hmi_saved_sig = self._hmi_signature_from_model()
        self._update_dirty_ui_hmi()

    def _mark_hmi_unsaved(self) -> None:
        self._hmi_saved_sig = None
        self._update_dirty_ui_hmi()

    def _new_shortcut(self) -> None:
        tab = self._active_tab()
        if tab == 0:
            self._new_enum_type_dialog()
        elif tab == 1:
            self._new_do_template_dialog()
        elif tab == 2:
            self.editor.new_template()
            if self.editor.model is not None:
                self._set_status(f"Created: {os.fspath(self.editor.model.info.file_path)}")
        elif tab == 3:
            if self.instance_editor is None:
                return
            self.instance_editor.new_instance()
        elif tab == 4:
            self._new_application()
        elif tab == 5:
            self._new_afg()
        else:
            self._new_hmi()

    def _open_shortcut(self) -> None:
        tab = self._active_tab()
        if tab == 0:
            self._open_enum_type()
        elif tab == 1:
            self._open_do_template()
        elif tab == 2:
            self.editor.open_template()
            if self.editor.model is not None:
                self._set_status(f"Opened: {os.fspath(self.editor.model.info.file_path)}")
        elif tab == 3:
            if self.instance_editor is None:
                return
            self.instance_editor.open_dialog()
            if self.instance_editor.doc is not None:
                self._set_status(f"Opened: {os.fspath(self.instance_editor.doc.file_path)}")
            try:
                self._update_app_refresh_button_state()
            except Exception:
                pass
        elif tab == 4:
            self._open_application()
        elif tab == 5:
            self._open_afg()
        else:
            self._open_hmi()

    def _save_shortcut(self) -> None:
        tab = self._active_tab()
        if tab == 0:
            self._save_enum_type()
        elif tab == 1:
            self._save_do_template()
        elif tab == 2:
            self.editor.save_current()
            if self.editor.model is not None:
                self._set_status(f"Saved: {os.fspath(self.editor.model.info.file_path)}")
        elif tab == 3:
            if self.instance_editor is None:
                return
            self.instance_editor.save()
            if self.instance_editor.doc is not None:
                self._set_status(f"Saved: {os.fspath(self.instance_editor.doc.file_path)}")
        elif tab == 4:
            self._save_application()
        elif tab == 5:
            self._save_afg()
        else:
            self._save_hmi()

    def _save_as_shortcut(self) -> None:
        tab = self._active_tab()
        if tab == 0:
            self._save_enum_type_as()
        elif tab == 1:
            self._save_do_template_as()
        elif tab == 2:
            self.editor.save_as()
            if self.editor.model is not None:
                self._set_status(f"Saved As: {os.fspath(self.editor.model.info.file_path)}")
        elif tab == 3:
            if self.instance_editor is None:
                return
            self.instance_editor.save_as()
            if self.instance_editor.doc is not None:
                self._set_status(f"Saved As: {os.fspath(self.instance_editor.doc.file_path)}")
        elif tab == 4:
            self._save_application_as()
        elif tab == 5:
            self._save_afg_as()
        else:
            self._save_hmi_as()

    def _application_dir(self) -> Path:
        return self.workspace_root / "ep7_datamodel" / "datamodel" / "application"

    def _hmi_template_dir(self) -> Path:
        return self.workspace_root / "ep7_datamodel" / "datamodel" / "hmi_template" / "application"

    def _do_type_dir(self) -> Path:
        return self.workspace_root / "ep7_datamodel" / "datamodel" / "iec61850" / "DOType"

    def _enum_type_dir(self) -> Path:
        return self.workspace_root / "ep7_datamodel" / "datamodel" / "iec61850" / "EnumType"

    def _applicationgroup_dir(self) -> Path:
        return self.workspace_root / "ep7_datamodel" / "datamodel" / "applicationgroup"

    def _lndm_dir(self) -> Path:
        return self.workspace_root / "ep7_datamodel" / "datamodel" / "lndm"

    def _afg_reset_ln_suggestions(self) -> None:
        self._afg_ln_cached_name = ""
        self._afg_ln_instance_path = None
        self._afg_ln_inref_doref_values = []
        self._afg_ln_status_doref_values = []

    def _afg_ensure_ln_suggestions_loaded(self) -> None:
        root = self._afg_root
        if root is None:
            self._afg_reset_ln_suggestions()
            return

        name = (root.attrib.get("name") or "").strip()
        if name == self._afg_ln_cached_name:
            return
        self._afg_ln_cached_name = name

        if not name:
            self._afg_ln_instance_path = None
            self._afg_ln_inref_doref_values = []
            self._afg_ln_status_doref_values = []
            return

        path = self._afg_guess_ln_instance_path(name)
        if path is None:
            self._afg_ln_instance_path = None
            self._afg_ln_inref_doref_values = []
            self._afg_ln_status_doref_values = []
            return

        try:
            parsed = self._afg_parse_ln_instance_for_doref(path)
        except Exception:
            parsed = None

        if not parsed:
            self._afg_ln_instance_path = None
            self._afg_ln_inref_doref_values = []
            self._afg_ln_status_doref_values = []
            return

        self._afg_ln_instance_path = path
        self._afg_ln_inref_doref_values = list(parsed.get("inref", []))
        self._afg_ln_status_doref_values = list(parsed.get("status", []))

    def _afg_guess_ln_instance_path(self, afg_name: str) -> Path | None:
        name = (afg_name or "").strip()
        if not name:
            return None
        lndm_dir = self._lndm_dir()
        if not lndm_dir.exists():
            return None

        preferred = lndm_dir / f"{name}GAPC.xml"
        candidates: list[Path] = []
        if preferred.exists():
            candidates.append(preferred)

        def add_glob(base: Path) -> None:
            try:
                for p in base.glob(f"{name}*.xml"):
                    if p.is_file():
                        candidates.append(p)
            except Exception:
                return

        add_glob(lndm_dir)
        for sub in ("P7", "P3Plus"):
            try:
                pdir = lndm_dir / sub
                if pdir.exists():
                    add_glob(pdir)
            except Exception:
                pass

        # De-dup, keep order
        seen: set[str] = set()
        uniq: list[Path] = []
        for p in candidates:
            sp = os.fspath(p)
            if sp in seen:
                continue
            seen.add(sp)
            uniq.append(p)
        candidates = uniq[:50]

        best: tuple[int, Path] | None = None
        for p in candidates:
            try:
                prefix, ln_class = self._afg_peek_ln_prefix_and_class(p)
            except Exception:
                continue
            score = 0
            if (prefix or "").strip() == name:
                score += 100
            if (ln_class or "").strip() == "GAPC":
                score += 10
            if best is None or score > best[0]:
                best = (score, p)

        if best is not None and best[0] > 0:
            return best[1]
        return preferred if preferred.exists() else (candidates[0] if candidates else None)

    def _afg_peek_ln_prefix_and_class(self, path: Path) -> tuple[str, str]:
        tree = ET.parse(path)
        root = tree.getroot()
        ln_el: ET.Element | None = None
        for el in root.iter():
            if isinstance(el.tag, str) and _local_name(el.tag) == "LN":
                ln_el = el
                break
        if ln_el is None:
            return ("", "")
        return ((ln_el.attrib.get("prefix") or ""), (ln_el.attrib.get("lnClass") or ""))

    def _afg_parse_ln_instance_for_doref(self, path: Path) -> dict[str, list[str]] | None:
        tree = ET.parse(path)
        root = tree.getroot()
        ln_el: ET.Element | None = None
        for el in root.iter():
            if isinstance(el.tag, str) and _local_name(el.tag) == "LN":
                ln_el = el
                break
        if ln_el is None:
            return None

        doi_names: list[str] = []
        inref_purposes: list[str] = []

        for doi in ln_el.iter():
            if not (isinstance(doi.tag, str) and _local_name(doi.tag) == "DOI"):
                continue
            dn = (doi.attrib.get("name") or "").strip()
            if not dn:
                continue
            doi_names.append(dn)

            if dn.startswith("InRef"):
                purpose = ""
                for dai in doi.iter():
                    if not (isinstance(dai.tag, str) and _local_name(dai.tag) == "DAI"):
                        continue
                    if (dai.attrib.get("name") or "") != "purpose":
                        continue
                    for v in dai.iter():
                        if isinstance(v.tag, str) and _local_name(v.tag) == "Val":
                            purpose = (v.text or "").strip()
                            break
                    break
                if purpose:
                    inref_purposes.append(purpose)

        # Build dropdown values
        inref_vals: list[str] = []
        seen_p: set[str] = set()
        for p in inref_purposes:
            if p in seen_p:
                continue
            seen_p.add(p)
            inref_vals.append(f".InRef%{p}")

        status_vals: list[str] = []
        seen_d: set[str] = set()
        for dn in doi_names:
            if dn.startswith("InRef"):
                continue
            if dn in {"NamPlt", "Beh"}:
                continue
            if dn in seen_d:
                continue
            seen_d.add(dn)
            status_vals.append(f".{dn}")

        return {"inref": inref_vals, "status": status_vals}

    def _afg_doref_values_inref(self, *, current: str = "") -> list[str]:
        self._afg_ensure_ln_suggestions_loaded()
        base = [""] + list(self._afg_ln_inref_doref_values or [])
        cur = (current or "").strip()
        if cur and cur not in base:
            base.insert(1, cur)
        return base

    def _afg_doref_values_status(self, *, current: str = "") -> list[str]:
        self._afg_ensure_ln_suggestions_loaded()
        base = [""] + list(self._afg_ln_status_doref_values or [])
        cur = (current or "").strip()
        if cur and cur not in base:
            base.insert(1, cur)
        return base

    def _enum_langref_private_type(self) -> str:
        return "SchneiderElectric-PowerLogic-LangRef"

    def _refresh_enum_search_list(self, *, select_rel: str | None) -> None:
        if self.cb_enum is None or self.var_enum_selected is None or self.lbl_enum_match is None:
            return
        enum_dir = self._enum_type_dir()
        self._all_enum_files = self._scan_xml_relpaths(enum_dir)

        def apply_filter(*_args) -> None:
            raw = ""
            if self.var_enum_filter is not None:
                raw = self.var_enum_filter.get().strip().lower()
            if not raw:
                filtered = list(self._all_enum_files)
            else:
                tokens = [t for t in raw.split() if t]

                def ok(v: str) -> bool:
                    lv = (v or "").lower()
                    return all(t in lv for t in tokens)

                filtered = [v for v in self._all_enum_files if ok(v)]

            cur = (self.var_enum_selected.get() or "").strip()
            if cur and cur not in filtered:
                filtered = [cur] + filtered

            max_show = 1200
            shown = filtered[:max_show]
            self.cb_enum["values"] = shown
            suffix = "" if len(filtered) <= max_show else f" (showing first {max_show})"
            self.lbl_enum_match.configure(text=f"{len(filtered)} match{'' if len(filtered)==1 else 'es'}{suffix}")

        if getattr(self, "_enum_apply_filter", None) is None:
            if self.var_enum_filter is not None:
                self.var_enum_filter.trace_add("write", apply_filter)
            setattr(self, "_enum_apply_filter", apply_filter)
        else:
            apply_filter = getattr(self, "_enum_apply_filter")

        if select_rel:
            try:
                self.var_enum_selected.set(select_rel)
            except Exception:
                pass
        apply_filter()

    def _refresh_afg_search_list(self, *, select_rel: str | None) -> None:
        if self.cb_afg is None or self.var_afg_selected is None or self.lbl_afg_match is None:
            return
        base_dir = self._applicationgroup_dir()
        self._all_afg_files = self._scan_xml_relpaths(base_dir)

        def apply_filter(*_args) -> None:
            raw = ""
            if self.var_afg_filter is not None:
                raw = self.var_afg_filter.get().strip().lower()
            if not raw:
                filtered = list(self._all_afg_files)
            else:
                tokens = [t for t in raw.split() if t]

                def ok(v: str) -> bool:
                    lv = (v or "").lower()
                    return all(t in lv for t in tokens)

                filtered = [v for v in self._all_afg_files if ok(v)]

            cur = (self.var_afg_selected.get() or "").strip()
            if cur and cur not in filtered:
                filtered = [cur] + filtered

            max_show = 1200
            shown = filtered[:max_show]
            self.cb_afg["values"] = shown
            suffix = "" if len(filtered) <= max_show else f" (showing first {max_show})"
            self.lbl_afg_match.configure(text=f"{len(filtered)} match{'' if len(filtered)==1 else 'es'}{suffix}")

        if getattr(self, "_afg_apply_filter", None) is None:
            if self.var_afg_filter is not None:
                self.var_afg_filter.trace_add("write", apply_filter)
            setattr(self, "_afg_apply_filter", apply_filter)
        else:
            apply_filter = getattr(self, "_afg_apply_filter")

        if select_rel:
            try:
                self.var_afg_selected.set(select_rel)
            except Exception:
                pass
        apply_filter()

    def _new_enum_type_dialog(self) -> None:
        if self.enum_tab is None:
            return
        try:
            self.enum_tab.new_enum_type_dialog()
        except Exception:
            pass

    def _open_enum_type(self) -> None:
        if self.enum_tab is None:
            return
        try:
            self.enum_tab.open_enum_type()
        except Exception:
            pass

    def _open_enum_type_from_search(self) -> None:
        if self.enum_tab is None:
            return
        try:
            self.enum_tab.open_enum_type_from_search()
        except Exception:
            pass

    # -----------------
    # AFG editor/viewer
    # -----------------

    def _new_afg(self) -> None:
        dlg = _AfgNewDialog(self, initial_name="", initial_proxy="", initial_chapter="", initial_topic="")
        res = dlg.show()
        if not res:
            return

        root = ET.Element("AfgDiagramXml")
        root.attrib["name"] = (res.get("name") or "").strip()
        root.attrib["proxyName"] = res.get("proxyName") or ""
        root.attrib["chapterName"] = res.get("chapterName") or ""
        root.attrib["topicName"] = res.get("topicName") or ""
        root.attrib["maxPinID"] = "0"

        ET.SubElement(root, "fbItems")
        ET.SubElement(root, "afgInItems")
        ET.SubElement(root, "afgOutItems")
        ET.SubElement(root, "arrows")

        self._afg_root = root
        self._afg_file_path = None
        self._afg_fb_clipboard = None
        self._afg_in_clipboard = None
        self._afg_out_clipboard = None

        self._afg_reset_ln_suggestions()

        try:
            self._afg_undo_stack = []
        except Exception:
            pass

        self._refresh_afg_views(select_first_fb=False)
        self._mark_afg_unsaved()
        self._set_status("New AFG created (unsaved)")

    def _sync_afg_meta_vars_to_root(self) -> None:
        root = self._afg_root
        if root is None:
            return

        # While we're populating the meta StringVars from XML (open/refresh),
        # their trace callbacks must not write back partial/empty state.
        if getattr(self, "_afg_meta_loading", False):
            return

        # Coalesce many keystrokes into one undo step: create a pending capture
        # on first modification and commit it on focus-out / Ctrl+Z.
        if not getattr(self, "_afg_undoing", False) and self._afg_meta_undo_cap is None:
            self._afg_meta_undo_cap = self._afg_begin_undo_capture()

        try:
            if self._afg_meta_name is not None:
                root.attrib["name"] = (self._afg_meta_name.get() or "").strip()
            if self._afg_meta_proxy is not None:
                root.attrib["proxyName"] = self._afg_meta_proxy.get() or ""
            if self._afg_meta_chapter is not None:
                root.attrib["chapterName"] = self._afg_meta_chapter.get() or ""
            if self._afg_meta_topic is not None:
                root.attrib["topicName"] = self._afg_meta_topic.get() or ""
        except Exception:
            pass

        # If nothing actually changed, drop the pending capture.
        try:
            if self._afg_meta_undo_cap is not None:
                before_sig, _snap = self._afg_meta_undo_cap
                if self._afg_signature_from_model() == before_sig:
                    self._afg_meta_undo_cap = None
        except Exception:
            pass

        try:
            self._on_afg_view_changed()
        except Exception:
            pass

    def _afg_end_meta_undo_capture(self, _event: tk.Event | None = None) -> None:
        try:
            self._afg_end_undo_capture(self._afg_meta_undo_cap)
        except Exception:
            pass
        self._afg_meta_undo_cap = None

    def _save_afg(self) -> None:
        if self._afg_root is None:
            messagebox.showerror("Missing", "No AFG loaded", parent=self)
            return

        # Flush in-progress edits so save reflects what user sees.
        try:
            self._end_afg_in_inline_editor(commit=True)
            self._end_afg_out_inline_editor(commit=True)
            self._end_afg_fb_inline_editor(commit=True)
        except Exception:
            pass
        try:
            self._afg_end_meta_undo_capture()
        except Exception:
            pass

        if not self._afg_validate_out_list_unique_or_show():
            return

        self._sync_afg_meta_vars_to_root()

        name = (self._afg_root.attrib.get("name") or "").strip()
        if not name:
            messagebox.showerror("Missing", "AFG name is required (used as file name)", parent=self)
            return

        stem = re.sub(r'[<>:"/\\|?*]', "_", name).strip() or "AFG"
        target_path = self._applicationgroup_dir() / f"{stem}.xml"

        try:
            self._normalize_afg_pin_ids_and_arrows()
            self._write_afg_xml(target_path)
        except Exception as e:
            messagebox.showerror("Save failed", str(e), parent=self)
            return

        self._afg_file_path = target_path
        try:
            base_dir = self._applicationgroup_dir()
            rel = os.fspath(target_path.relative_to(base_dir))
        except Exception:
            rel = os.fspath(target_path.name)
        self._refresh_afg_search_list(select_rel=rel)
        self._set_status(f"Saved AFG: {os.fspath(target_path)}")

        self._mark_afg_saved()

    def _save_afg_as(self) -> None:
        if self._afg_root is None:
            messagebox.showerror("Missing", "No AFG loaded", parent=self)
            return

        self._sync_afg_meta_vars_to_root()

        cur_name = (self._afg_root.attrib.get("name") or "").strip()
        initial = f"{cur_name}_copy" if cur_name else ""
        dlg = _AfgSaveAsDialog(self, initial_name=initial)
        new_name = dlg.show()
        if not new_name:
            return

        stem = re.sub(r'[<>:"/\\|?*]', "_", new_name).strip() or "AFG"
        target_path = self._applicationgroup_dir() / f"{stem}.xml"

        try:
            cur_path = self._afg_file_path.resolve() if self._afg_file_path is not None else None
        except Exception:
            cur_path = self._afg_file_path
        try:
            tgt_resolved = target_path.resolve()
        except Exception:
            tgt_resolved = target_path

        if target_path.exists() and (cur_path is None or tgt_resolved != cur_path):
            ok = messagebox.askyesno(
                "Overwrite?",
                f"File already exists:\n\n{os.fspath(target_path)}\n\nOverwrite?",
                parent=self,
            )
            if not ok:
                return

        self._afg_root.attrib["name"] = new_name
        self._afg_file_path = target_path
        self._save_afg()

    def _refresh_afg_from_latest_ln_instance(self) -> None:
        root = self._afg_root
        if root is None:
            messagebox.showerror("Missing", "No AFG loaded", parent=self)
            return

        cap = self._afg_begin_undo_capture()
        try:
            fb_updated = 0
            in_added = 0
            out_added = 0
            try:
                fb_updated = self._afg_refresh_sync_fb_io_from_application()
            except Exception:
                fb_updated = 0

            try:
                in_added, out_added = self._afg_refresh_sync_io_from_ln_instance()
            except Exception:
                in_added, out_added = (0, 0)

            try:
                self._normalize_afg_pin_ids_and_arrows()
            except Exception:
                pass

            try:
                self._refresh_afg_views(select_first_fb=False)
            except Exception:
                pass

            try:
                self._on_afg_view_changed()
            except Exception:
                pass

            self._set_status(
                f"AFG refreshed: {fb_updated} AFB(s) updated, +{in_added} input(s), +{out_added} output(s)"
            )
        finally:
            self._afg_end_undo_capture(cap)

    def _afg_refresh_sync_fb_io_from_application(self) -> int:
        root = self._afg_root
        if root is None:
            return 0

        fb_items_el = self._afg_get_child(root, "fbItems")
        if fb_items_el is None:
            return 0

        app_dir = self._application_dir()
        rels = self._scan_xml_relpaths(app_dir)
        if not rels:
            return 0

        # Index Application funBlocks by their declared name.
        app_by_fb_name: dict[str, tuple[list[str], list[str]]] = {}
        for rel in rels:
            path = app_dir / rel
            try:
                fb_name, input_names, output_names = self._read_application_funblock_io(path)
            except Exception:
                continue
            if not fb_name:
                continue
            if fb_name in app_by_fb_name:
                continue
            app_by_fb_name[fb_name] = (list(input_names), list(output_names))

        updated = 0
        for fb in [x for x in list(fb_items_el) if isinstance(x.tag, str) and _local_name(x.tag) == "fbItem"]:
            fb_name = (fb.attrib.get("name") or "").strip()
            if not fb_name:
                continue
            desired = app_by_fb_name.get(fb_name)
            if desired is None:
                continue
            desired_inputs, desired_outputs = desired
            if self._afg_sync_fb_item_pins_from_names(fb, desired_inputs, desired_outputs):
                updated += 1
        return updated

    def _afg_sync_fb_item_pins_from_names(self, fb: ET.Element, desired_inputs: list[str], desired_outputs: list[str]) -> bool:
        changed = False

        def _get_or_create_box(local: str) -> ET.Element:
            nonlocal changed
            for ch in list(fb):
                if isinstance(ch.tag, str) and _local_name(ch.tag) == local:
                    return ch
            changed = True
            return ET.SubElement(fb, local)

        def _sync_pins(box: ET.Element, *, pin_local: str, desired_names: list[str]) -> bool:
            nonlocal changed
            existing = [x for x in list(box) if isinstance(x.tag, str) and _local_name(x.tag) == pin_local]
            existing_names = [(x.attrib.get("name") or "").strip() for x in existing]
            desired_names2 = [(n or "").strip() for n in desired_names if (n or "").strip()]

            # Fast path: already matches exactly (names and order).
            if existing_names == desired_names2 and len(existing) == len(desired_names2):
                return False

            by_name: dict[str, ET.Element] = {}
            for el in existing:
                nm = (el.attrib.get("name") or "").strip()
                if nm and nm not in by_name:
                    by_name[nm] = el

            new_order: list[ET.Element] = []
            for nm in desired_names2:
                el = by_name.get(nm)
                if el is None:
                    el = ET.Element(
                        pin_local,
                        attrib={
                            "name": nm,
                            "lineColor": "#000000",
                            "itemColor": "#000000",
                            "pinLineColor": "#000000",
                        },
                    )
                    changed = True
                new_order.append(el)

            # Remove all current pins (keep any non-pin children untouched), then append desired order.
            for el in existing:
                try:
                    box.remove(el)
                except Exception:
                    pass
            for el in new_order:
                box.append(el)
            changed = True
            return True

        inputs_el = _get_or_create_box("Inputs")
        outputs_el = _get_or_create_box("Outputs")
        _sync_pins(inputs_el, pin_local="Input", desired_names=desired_inputs)
        _sync_pins(outputs_el, pin_local="Output", desired_names=desired_outputs)
        return changed

    def _afg_refresh_sync_io_from_ln_instance(self) -> tuple[int, int]:
        root = self._afg_root
        if root is None:
            return (0, 0)

        # Force reload even if name unchanged; LN instance may have changed on disk.
        try:
            self._afg_ln_cached_name = ""
        except Exception:
            pass

        self._afg_ensure_ln_suggestions_loaded()
        inref_vals = [v.strip() for v in (self._afg_ln_inref_doref_values or []) if (v or "").strip()]
        status_vals = [v.strip() for v in (self._afg_ln_status_doref_values or []) if (v or "").strip()]

        in_parent = self._afg_get_or_create_child(root, "afgInItems")
        out_parent = self._afg_get_or_create_child(root, "afgOutItems")

        existing_in_doref: set[str] = set()
        used_in_names: set[str] = set()
        for it in list(in_parent):
            if not (isinstance(it.tag, str) and _local_name(it.tag) == "afgInItem"):
                continue
            existing_in_doref.add((it.attrib.get("doRef") or "").strip())
            used_in_names.add((it.attrib.get("name") or "").strip())

        existing_out_doref: set[str] = set()
        out_confpin_inref_doref: set[str] = set()
        for it in list(out_parent):
            if not (isinstance(it.tag, str) and _local_name(it.tag) == "afgOutItem"):
                continue
            existing_out_doref.add((it.attrib.get("doRef") or "").strip())
            try:
                if (it.attrib.get("confpin") or "").strip().lower() == "true":
                    dr = (it.attrib.get("doRef") or "").strip()
                    if dr:
                        out_confpin_inref_doref.add(dr)
            except Exception:
                pass

        def _unique_in_name(base: str) -> str:
            b = (base or "").strip() or "NewIn"
            if b not in used_in_names:
                used_in_names.add(b)
                return b
            i = 2
            while True:
                cand = f"{b}{i}"
                if cand not in used_in_names:
                    used_in_names.add(cand)
                    return cand
                i += 1

        in_added = 0
        for dr in inref_vals:
            if not dr or dr in existing_in_doref:
                continue
            # Some InRef purposes are used as confpin AFG Outputs; those do not need
            # to be duplicated as AFG Inputs.
            if dr in out_confpin_inref_doref:
                continue
            el = self._afg_make_new_in_item()
            el.attrib["doRef"] = dr
            base = dr.lstrip(".")
            base = base.replace("%", "_")
            el.attrib["name"] = _unique_in_name(base)
            in_parent.append(el)
            existing_in_doref.add(dr)
            in_added += 1
            try:
                self._afg_ui_added_ids.add(id(el))
            except Exception:
                pass

        out_added = 0
        for dr in status_vals:
            if not dr or dr in existing_out_doref:
                continue
            el = self._afg_make_new_out_item()
            el.attrib["doRef"] = dr
            base = (dr.lstrip(".") or "NewOut")
            el.attrib["name"] = self._afg_out_unique_name(base)
            out_parent.append(el)
            existing_out_doref.add(dr)
            out_added += 1
            try:
                self._afg_ui_added_ids.add(id(el))
            except Exception:
                pass

        return (in_added, out_added)

    def _write_afg_xml(self, path: Path) -> None:
        if self._afg_root is None:
            raise ValueError("No AFG loaded")

        root = self._afg_root
        try:
            ET.indent(root, space="    ")
        except Exception:
            pass
        body = ET.tostring(root, encoding="unicode", short_empty_elements=True)
        text = "<?xml version=\"1.0\" encoding=\"utf-8\" ?>\n" + body.rstrip() + "\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\r\n") as f:
            f.write(text)

    def _refresh_afg_views(self, *, select_first_fb: bool) -> None:
        # Meta
        try:
            self._afg_meta_loading = True
            root = self._afg_root
            if root is None:
                if self._afg_meta_name is not None:
                    self._afg_meta_name.set("")
                if self._afg_meta_proxy is not None:
                    self._afg_meta_proxy.set("")
                if self._afg_meta_chapter is not None:
                    self._afg_meta_chapter.set("")
                if self._afg_meta_topic is not None:
                    self._afg_meta_topic.set("")
            else:
                if self._afg_meta_name is not None:
                    self._afg_meta_name.set(root.attrib.get("name") or "")
                if self._afg_meta_proxy is not None:
                    self._afg_meta_proxy.set(root.attrib.get("proxyName") or "")
                if self._afg_meta_chapter is not None:
                    self._afg_meta_chapter.set(root.attrib.get("chapterName") or "")
                if self._afg_meta_topic is not None:
                    self._afg_meta_topic.set(root.attrib.get("topicName") or "")
        except Exception:
            pass
        finally:
            try:
                self._afg_meta_loading = False
            except Exception:
                pass

        self._refresh_afg_fb_table(select_first=select_first_fb)
        self._refresh_afg_io_tables()
        # _refresh_afg_io_tables() already refreshes the arrow table to keep pin
        # names consistent; avoid doing the same work twice.

        try:
            self._update_dirty_ui_afg()
        except Exception:
            pass

    def _afg_get_child(self, root: ET.Element, local: str) -> ET.Element | None:
        for ch in list(root):
            if isinstance(ch.tag, str) and _local_name(ch.tag) == local:
                return ch
        return None

    def _afg_get_or_create_child(self, root: ET.Element, local: str) -> ET.Element:
        el = self._afg_get_child(root, local)
        if el is not None:
            return el
        return ET.SubElement(root, local)

    def _parse_pos(self, raw: str) -> tuple[str, str]:
        s = (raw or "").strip()
        if not s:
            return ("", "")
        parts = [p.strip() for p in s.split(",")]
        if len(parts) >= 2:
            return (parts[0], parts[1])
        return (s, "")

    def _refresh_afg_fb_table(self, *, select_first: bool) -> None:
        if self._afg_tv_fb is None:
            return

        self._clear_tv(self._afg_tv_fb)
        self._afg_fb_iid_to_item = {}

        root = self._afg_root
        if root is None:
            return
        fb_items_el = self._afg_get_child(root, "fbItems")
        if fb_items_el is None:
            return

        def _count_io(fb: ET.Element, local: str, child_local: str) -> int:
            box = None
            for x in list(fb):
                if isinstance(x.tag, str) and _local_name(x.tag) == local:
                    box = x
                    break
            if box is None:
                return 0
            return sum(1 for x in list(box) if isinstance(x.tag, str) and _local_name(x.tag) == child_local)

        for i, fb in enumerate([x for x in list(fb_items_el) if isinstance(x.tag, str) and _local_name(x.tag) == "fbItem"]):
            name = (fb.attrib.get("name") or "").strip()
            pos_x, pos_y = self._parse_pos(fb.attrib.get("pos") or "")
            in_n = _count_io(fb, "Inputs", "Input")
            out_n = _count_io(fb, "Outputs", "Output")
            iid = str(i)
            self._afg_fb_iid_to_item[iid] = fb
            self._afg_tv_fb.insert(
                "",
                "end",
                iid=iid,
                values=(name, pos_x, pos_y, str(in_n), str(out_n)),
                tags=self._afg_row_tags_for_el(fb),
            )

        for j, row in enumerate(self._afg_ui_deleted_fb_rows or []):
            iid = f"del_fb_{j:04d}"
            try:
                self._afg_tv_fb.insert("", "end", iid=iid, values=row, tags=("removed",))
            except Exception:
                pass

        if select_first:
            try:
                kids = self._afg_tv_fb.get_children("")
                if kids:
                    self._afg_tv_fb.selection_set(kids[0])
                    self._afg_tv_fb.focus(kids[0])
            except Exception:
                pass

    def _refresh_afg_io_tables(self) -> None:
        root = self._afg_root
        if root is None:
            self._clear_tv(self._afg_tv_in)
            self._clear_tv(self._afg_tv_out)
            self._clear_tv(self._afg_tv_arrows)
            self._afg_in_iid_to_item = {}
            self._afg_out_iid_to_item = {}
            self._afg_arrow_iid_to_item = {}
            try:
                self._update_dirty_ui_afg()
            except Exception:
                pass
            return

        self._clear_tv(self._afg_tv_in)
        self._clear_tv(self._afg_tv_out)
        self._clear_tv(self._afg_tv_arrows)
        self._afg_in_iid_to_item = {}
        self._afg_out_iid_to_item = {}
        self._afg_arrow_iid_to_item = {}

        def _is_true(v: str | None) -> bool:
            return (v or "").strip().lower() == "true"

        def _box(v: str | None) -> str:
            return "☑" if _is_true(v) else "☐"

        in_items_el = self._afg_get_child(root, "afgInItems")
        if in_items_el is not None and self._afg_tv_in is not None:
            i = 0
            for it in list(in_items_el):
                if not (isinstance(it.tag, str) and _local_name(it.tag) == "afgInItem"):
                    continue
                iid = str(i)
                self._afg_in_iid_to_item[iid] = it
                px, py = self._parse_pos(it.attrib.get("pos") or "")
                vals = (
                    (it.attrib.get("name") or ""),
                    px,
                    py,
                    (it.attrib.get("src") or ""),
                    (it.attrib.get("doRef") or ""),
                    _box(it.attrib.get("confpin")),
                    _box(it.attrib.get("softlink")),
                )
                self._afg_tv_in.insert("", "end", iid=iid, values=vals, tags=self._afg_row_tags_for_el(it))
                i += 1

            for j, row in enumerate(self._afg_ui_deleted_in_rows or []):
                iid = f"del_in_{j:04d}"
                try:
                    self._afg_tv_in.insert("", "end", iid=iid, values=row, tags=("removed",))
                except Exception:
                    pass

        out_items_el = self._afg_get_child(root, "afgOutItems")
        if out_items_el is not None and self._afg_tv_out is not None:
            i = 0
            for it in list(out_items_el):
                if not (isinstance(it.tag, str) and _local_name(it.tag) == "afgOutItem"):
                    continue
                iid = str(i)
                self._afg_out_iid_to_item[iid] = it
                px, py = self._parse_pos(it.attrib.get("pos") or "")
                vals = (
                    (it.attrib.get("name") or ""),
                    px,
                    py,
                    (it.attrib.get("doRef") or ""),
                    _box(it.attrib.get("confpin")),
                )
                self._afg_tv_out.insert("", "end", iid=iid, values=vals, tags=self._afg_row_tags_for_el(it))
                i += 1

            for j, row in enumerate(self._afg_ui_deleted_out_rows or []):
                iid = f"del_out_{j:04d}"
                try:
                    self._afg_tv_out.insert("", "end", iid=iid, values=row, tags=("removed",))
                except Exception:
                    pass

        # Keep arrows view in sync with current pin names.
        try:
            self._refresh_afg_arrow_table()
        except Exception:
            pass

        # IO edits should immediately update Save button state.
        try:
            self._update_dirty_ui_afg()
        except Exception:
            pass

    def _afg_pin_id_display_map(self) -> dict[str, str]:
        root = self._afg_root
        if root is None:
            return {}

        def _add(pid: str | None, label: str) -> None:
            p = (pid or "").strip()
            if not p:
                return
            out[p] = label

        out: dict[str, str] = {}

        fb_items_el = self._afg_get_child(root, "fbItems")
        if fb_items_el is not None:
            for fb in list(fb_items_el):
                if not (isinstance(fb.tag, str) and _local_name(fb.tag) == "fbItem"):
                    continue
                fb_name = (fb.attrib.get("name") or "").strip()
                inputs_el = None
                outputs_el = None
                for ch in list(fb):
                    if not isinstance(ch.tag, str):
                        continue
                    ln = _local_name(ch.tag)
                    if ln == "Inputs":
                        inputs_el = ch
                    elif ln == "Outputs":
                        outputs_el = ch

                if inputs_el is not None:
                    for it in list(inputs_el):
                        if not (isinstance(it.tag, str) and _local_name(it.tag) == "Input"):
                            continue
                        n = (it.attrib.get("name") or "").strip()
                        _add(it.attrib.get("pinID"), f"{fb_name}:In:{n}" if fb_name else f"In:{n}")

                if outputs_el is not None:
                    for it in list(outputs_el):
                        if not (isinstance(it.tag, str) and _local_name(it.tag) == "Output"):
                            continue
                        n = (it.attrib.get("name") or "").strip()
                        _add(it.attrib.get("pinID"), f"{fb_name}:Out:{n}" if fb_name else f"Out:{n}")

        in_items_el = self._afg_get_child(root, "afgInItems")
        if in_items_el is not None:
            for it in list(in_items_el):
                if not (isinstance(it.tag, str) and _local_name(it.tag) == "afgInItem"):
                    continue
                n = (it.attrib.get("name") or "").strip()
                _add(it.attrib.get("pinID"), f"AFG_IN:{n}" if n else "AFG_IN")

        out_items_el = self._afg_get_child(root, "afgOutItems")
        if out_items_el is not None:
            for it in list(out_items_el):
                if not (isinstance(it.tag, str) and _local_name(it.tag) == "afgOutItem"):
                    continue
                n = (it.attrib.get("name") or "").strip()
                _add(it.attrib.get("pinID"), f"AFG_OUT:{n}" if n else "AFG_OUT")

        return out

    def _refresh_afg_arrow_table(self) -> None:
        tv = self._afg_tv_arrows
        if tv is None:
            return

        self._clear_tv(tv)
        self._afg_arrow_iid_to_item = {}

        root = self._afg_root
        if root is None:
            return
        arrows_el = self._afg_get_child(root, "arrows")
        if arrows_el is None:
            return

        pin_map = self._afg_pin_id_display_map()

        def _k(pid: str) -> tuple[int, str]:
            s = (pid or "").strip()
            try:
                return (int(s), s)
            except Exception:
                return (10**9, s)

        i = 0
        for ar in [x for x in list(arrows_el) if isinstance(x.tag, str) and _local_name(x.tag) == "arrowItem"]:
            sp = (ar.attrib.get("startPinID") or "").strip()
            ep = (ar.attrib.get("endPinID") or "").strip()
            vals = (sp, pin_map.get(sp, ""), ep, pin_map.get(ep, ""))
            iid = str(i)
            self._afg_arrow_iid_to_item[iid] = ar
            tv.insert("", "end", iid=iid, values=vals, tags=self._afg_row_tags_for_el(ar))
            i += 1

        for j, row in enumerate(self._afg_ui_deleted_arrow_rows or []):
            iid = f"del_ar_{j:04d}"
            try:
                tv.insert("", "end", iid=iid, values=row, tags=("removed",))
            except Exception:
                pass

        # Keep a stable-ish ordering by current document order (no sorting here).

    def _afg_arrow_owner_pin_index(self) -> tuple[list[str], dict[str, list[tuple[str, str]]], dict[str, str]]:
        """Build owners list and pin lists for arrow dialog.

        owners: display strings for first combobox.
        pins_by_owner: owner -> [(pinID, label)] for second combobox.
        owner_for_pin: pinID -> owner
        """
        root = self._afg_root
        if root is None:
            return ([], {}, {})

        owners: list[str] = []
        pins_by_owner: dict[str, list[tuple[str, str]]] = {}
        owner_for_pin: dict[str, str] = {}

        def add_owner(owner: str) -> None:
            if owner not in pins_by_owner:
                pins_by_owner[owner] = []
                owners.append(owner)

        def add_pin(owner: str, pid: str | None, label: str) -> None:
            p = (pid or "").strip()
            if not p:
                return
            add_owner(owner)
            pins_by_owner[owner].append((p, label))
            owner_for_pin[p] = owner

        # AFG side pins
        in_items_el = self._afg_get_child(root, "afgInItems")
        if in_items_el is not None:
            for it in list(in_items_el):
                if not (isinstance(it.tag, str) and _local_name(it.tag) == "afgInItem"):
                    continue
                n = (it.attrib.get("name") or "").strip() or "(no name)"
                add_pin("AFG", it.attrib.get("pinID"), f"Input: {n}")

        out_items_el = self._afg_get_child(root, "afgOutItems")
        if out_items_el is not None:
            for it in list(out_items_el):
                if not (isinstance(it.tag, str) and _local_name(it.tag) == "afgOutItem"):
                    continue
                n = (it.attrib.get("name") or "").strip() or "(no name)"
                add_pin("AFG", it.attrib.get("pinID"), f"Output: {n}")

        # AFB pins
        fb_items_el = self._afg_get_child(root, "fbItems")
        if fb_items_el is not None:
            for fb in list(fb_items_el):
                if not (isinstance(fb.tag, str) and _local_name(fb.tag) == "fbItem"):
                    continue
                fb_name = (fb.attrib.get("name") or "").strip() or "(no name)"
                owner = f"AFB: {fb_name}"

                inputs_el = None
                outputs_el = None
                for ch in list(fb):
                    if not isinstance(ch.tag, str):
                        continue
                    ln = _local_name(ch.tag)
                    if ln == "Inputs":
                        inputs_el = ch
                    elif ln == "Outputs":
                        outputs_el = ch

                if inputs_el is not None:
                    for it in list(inputs_el):
                        if not (isinstance(it.tag, str) and _local_name(it.tag) == "Input"):
                            continue
                        n = (it.attrib.get("name") or "").strip() or "(no name)"
                        add_pin(owner, it.attrib.get("pinID"), f"Input: {n}")
                if outputs_el is not None:
                    for it in list(outputs_el):
                        if not (isinstance(it.tag, str) and _local_name(it.tag) == "Output"):
                            continue
                        n = (it.attrib.get("name") or "").strip() or "(no name)"
                        add_pin(owner, it.attrib.get("pinID"), f"Output: {n}")

        return (owners, pins_by_owner, owner_for_pin)

    def _afg_arrow_owner_pin_index_for_side(
        self, *, side: str
    ) -> tuple[list[str], dict[str, list[tuple[str, str]]], dict[str, str]]:
        """Index pins for arrow dialog with side constraints.

        side == "start": allow AFG inputs + AFB outputs
        side == "end": allow AFB inputs + AFG outputs
        """
        root = self._afg_root
        if root is None:
            return ([], {}, {})

        owners: list[str] = []
        pins_by_owner: dict[str, list[tuple[str, str]]] = {}
        owner_for_pin: dict[str, str] = {}

        def add_owner(owner: str) -> None:
            if owner not in pins_by_owner:
                pins_by_owner[owner] = []
                owners.append(owner)

        def add_pin(owner: str, pid: str | None, label: str) -> None:
            p = (pid or "").strip()
            if not p:
                return
            add_owner(owner)
            pins_by_owner[owner].append((p, label))
            owner_for_pin[p] = owner

        allow_afg_in = side == "start"
        allow_afg_out = side == "end"
        allow_afb_out = side == "start"
        allow_afb_in = side == "end"

        if allow_afg_in:
            in_items_el = self._afg_get_child(root, "afgInItems")
            if in_items_el is not None:
                for it in list(in_items_el):
                    if not (isinstance(it.tag, str) and _local_name(it.tag) == "afgInItem"):
                        continue
                    n = (it.attrib.get("name") or "").strip() or "(no name)"
                    add_pin("AFG", it.attrib.get("pinID"), f"Input: {n}")

        if allow_afg_out:
            out_items_el = self._afg_get_child(root, "afgOutItems")
            if out_items_el is not None:
                for it in list(out_items_el):
                    if not (isinstance(it.tag, str) and _local_name(it.tag) == "afgOutItem"):
                        continue
                    n = (it.attrib.get("name") or "").strip() or "(no name)"
                    add_pin("AFG", it.attrib.get("pinID"), f"Output: {n}")

        fb_items_el = self._afg_get_child(root, "fbItems")
        if fb_items_el is not None:
            for fb in list(fb_items_el):
                if not (isinstance(fb.tag, str) and _local_name(fb.tag) == "fbItem"):
                    continue
                fb_name = (fb.attrib.get("name") or "").strip() or "(no name)"
                owner = f"AFB: {fb_name}"

                inputs_el = None
                outputs_el = None
                for ch in list(fb):
                    if not isinstance(ch.tag, str):
                        continue
                    ln = _local_name(ch.tag)
                    if ln == "Inputs":
                        inputs_el = ch
                    elif ln == "Outputs":
                        outputs_el = ch

                if allow_afb_in and inputs_el is not None:
                    for it in list(inputs_el):
                        if not (isinstance(it.tag, str) and _local_name(it.tag) == "Input"):
                            continue
                        n = (it.attrib.get("name") or "").strip() or "(no name)"
                        add_pin(owner, it.attrib.get("pinID"), f"Input: {n}")

                if allow_afb_out and outputs_el is not None:
                    for it in list(outputs_el):
                        if not (isinstance(it.tag, str) and _local_name(it.tag) == "Output"):
                            continue
                        n = (it.attrib.get("name") or "").strip() or "(no name)"
                        add_pin(owner, it.attrib.get("pinID"), f"Output: {n}")

        # Filter out owners that ended up empty (shouldn't happen, but safe)
        owners2 = [o for o in owners if pins_by_owner.get(o)]
        pins_by_owner2 = {o: pins_by_owner[o] for o in owners2}
        owner_for_pin2: dict[str, str] = {}
        for o in owners2:
            for pid, _lbl in pins_by_owner2.get(o, []):
                owner_for_pin2[pid] = o

        return (owners2, pins_by_owner2, owner_for_pin2)


    def _afg_selected_fb_iid(self) -> str | None:
        if self._afg_tv_fb is None:
            return None
        try:
            cur = self._afg_tv_fb.selection()
            return cur[0] if cur else None
        except Exception:
            return None

    def _afg_selected_fb(self) -> ET.Element | None:
        iid = self._afg_selected_fb_iid()
        if not iid:
            return None
        return self._afg_fb_iid_to_item.get(iid)

    def _afg_selected_in_iid(self) -> str | None:
        tv = self._afg_tv_in
        if tv is None:
            return None
        sel = tv.selection()
        if not sel:
            return None
        return sel[0]

    def _afg_selected_in(self) -> ET.Element | None:
        iid = self._afg_selected_in_iid()
        if iid is None:
            return None
        return self._afg_in_iid_to_item.get(iid)

    def _afg_selected_out_iid(self) -> str | None:
        tv = self._afg_tv_out
        if tv is None:
            return None
        sel = tv.selection()
        if not sel:
            return None
        return sel[0]

    def _afg_selected_out(self) -> ET.Element | None:
        iid = self._afg_selected_out_iid()
        if iid is None:
            return None
        return self._afg_out_iid_to_item.get(iid)

    def _afg_selected_arrow_iid(self) -> str | None:
        tv = self._afg_tv_arrows
        if tv is None:
            return None
        sel = tv.selection()
        if not sel:
            return None
        return sel[0]

    def _afg_selected_arrow(self) -> ET.Element | None:
        iid = self._afg_selected_arrow_iid()
        if iid is None:
            return None
        return self._afg_arrow_iid_to_item.get(iid)

    def _select_afg_arrow_element(self, el: ET.Element) -> None:
        tv = self._afg_tv_arrows
        if tv is None:
            return
        for iid, it in self._afg_arrow_iid_to_item.items():
            if it is el:
                try:
                    tv.selection_set(iid)
                    tv.focus(iid)
                    tv.see(iid)
                except Exception:
                    pass
                return

    def _select_afg_in_element(self, el: ET.Element) -> None:
        tv = self._afg_tv_in
        if tv is None:
            return
        for iid, it in self._afg_in_iid_to_item.items():
            if it is el:
                try:
                    tv.selection_set(iid)
                    tv.focus(iid)
                    tv.see(iid)
                except Exception:
                    pass
                return

    def _select_afg_out_element(self, el: ET.Element) -> None:
        tv = self._afg_tv_out
        if tv is None:
            return
        for iid, it in self._afg_out_iid_to_item.items():
            if it is el:
                try:
                    tv.selection_set(iid)
                    tv.focus(iid)
                    tv.see(iid)
                except Exception:
                    pass
                return

    def _afg_suggest_new_io_pos(self, *, parent_local: str, default_x: float) -> tuple[float, float]:
        root = self._afg_root
        if root is None:
            return (default_x, 100.0)
        parent = self._afg_get_or_create_child(root, parent_local)
        max_y = None
        for it in list(parent):
            if not isinstance(it.tag, str):
                continue
            px, py = self._parse_pos(it.attrib.get("pos") or "")
            try:
                fy = float(py) if py.strip() else 0.0
            except Exception:
                fy = 0.0
            if max_y is None or fy > max_y:
                max_y = fy
        y = 100.0 if max_y is None else (max_y + 100.0)
        return (default_x, y)

    def _afg_make_new_in_item(self) -> ET.Element:
        x, y = self._afg_suggest_new_io_pos(parent_local="afgInItems", default_x=100.0)
        el = ET.Element(
            "afgInItem",
            attrib={
                "pos": f"{x:.6f},{y:.6f}",
                "name": "NewIn",
                "src": "",
                "softlink": "false",
                "confpin": "false",
                "doRef": "",
                "daRef": "",
                "lineColor": "#000000",
                "itemColor": "#ffffff",
                "pinLineColor": "#000000",
            },
        )
        return el

    def _afg_make_new_out_item(self) -> ET.Element:
        x, y = self._afg_suggest_new_io_pos(parent_local="afgOutItems", default_x=2600.0)
        el = ET.Element(
            "afgOutItem",
            attrib={
                "pos": f"{x:.6f},{y:.6f}",
                "name": "NewOut",
                "confpin": "false",
                "doRef": "",
                "daRef": "",
                "lineColor": "#000000",
                "itemColor": "#ffffff",
                "pinLineColor": "#000000",
            },
        )
        return el

    def _afg_out_items(self) -> list[ET.Element]:
        root = self._afg_root
        if root is None:
            return []
        parent = self._afg_get_child(root, "afgOutItems")
        if parent is None:
            return []
        return [x for x in list(parent) if isinstance(x.tag, str) and _local_name(x.tag) == "afgOutItem"]

    def _afg_out_unique_name(self, base: str) -> str:
        base2 = (base or "").strip() or "NewOut"
        used = {(it.attrib.get("name") or "").strip() for it in self._afg_out_items()}
        if base2 not in used:
            return base2
        i = 2
        while True:
            cand = f"{base2}{i}"
            if cand not in used:
                return cand
            i += 1

    def _afg_out_validate_unique(self, *, name: str, do_ref: str, exclude: ET.Element | None) -> str | None:
        nm = (name or "").strip()
        dr = (do_ref or "").strip()
        if not nm:
            return "name is required"

        for it in self._afg_out_items():
            if exclude is not None and it is exclude:
                continue
            other_nm = (it.attrib.get("name") or "").strip()
            if other_nm and other_nm == nm:
                return f"Duplicate output name: {nm}"

        if dr:
            for it in self._afg_out_items():
                if exclude is not None and it is exclude:
                    continue
                other_dr = (it.attrib.get("doRef") or "").strip()
                if other_dr and other_dr == dr:
                    return f"Duplicate doRef: {dr}"
        return None

    def _afg_validate_out_list_unique_or_show(self) -> bool:
        for it in self._afg_out_items():
            msg = self._afg_out_validate_unique(
                name=(it.attrib.get("name") or ""),
                do_ref=(it.attrib.get("doRef") or ""),
                exclude=it,
            )
            if msg:
                messagebox.showerror(
                    "Invalid AFG Outputs",
                    msg + "\n\nAFG Outputs: name and doRef must be unique.",
                    parent=self,
                )
                return False
        return True

    def _afg_in_add(self) -> None:
        self._afg_in_add_impl(insert_mode="append")

    def _afg_in_insert(self) -> None:
        self._afg_in_add_impl(insert_mode="before")

    def _afg_in_add_impl(self, *, insert_mode: str) -> None:
        root = self._afg_root
        if root is None:
            messagebox.showerror("Missing", "No AFG loaded", parent=self)
            return

        cap = self._afg_begin_undo_capture()
        parent = self._afg_get_or_create_child(root, "afgInItems")
        new_el = self._afg_make_new_in_item()

        selected = self._afg_selected_in()
        if insert_mode == "before" and selected is not None:
            try:
                idx = list(parent).index(selected)
                parent.insert(idx, new_el)
            except Exception:
                parent.append(new_el)
        else:
            parent.append(new_el)

        try:
            self._afg_ui_added_ids.add(id(new_el))
        except Exception:
            pass

        self._normalize_afg_pin_ids_and_arrows()
        self._refresh_afg_io_tables()
        self._select_afg_in_element(new_el)
        try:
            self.after_idle(lambda: self._begin_afg_in_inline_edit_for_selected(col="#1"))
        except Exception:
            pass
        self._afg_end_undo_capture(cap)

    def _afg_in_edit(self) -> None:
        el = self._afg_selected_in()
        if el is None:
            return

        name = (el.attrib.get("name") or "")
        pos_x, pos_y = self._parse_pos(el.attrib.get("pos") or "")
        src = (el.attrib.get("src") or "")
        do_ref = (el.attrib.get("doRef") or "")
        confpin = (el.attrib.get("confpin") or "").strip().lower() == "true"
        softlink = (el.attrib.get("softlink") or "").strip().lower() == "true"

        self._afg_ensure_ln_suggestions_loaded()
        do_ref_values = self._afg_doref_values_inref(current=do_ref)

        dlg = _AfgInEditDialog(
            self,
            name=name,
            pos_x=pos_x,
            pos_y=pos_y,
            src=src,
            do_ref=do_ref,
            do_ref_values=do_ref_values,
            confpin=confpin,
            softlink=softlink,
        )
        res = dlg.show()
        if not res:
            return

        cap = self._afg_begin_undo_capture()

        el.attrib["name"] = (res.get("name") or "").strip()
        x = (res.get("posX") or "").strip()
        y = (res.get("posY") or "").strip()
        if x or y or "pos" in el.attrib:
            el.attrib["pos"] = f"{x},{y}" if y != "" else x
        el.attrib["src"] = (res.get("src") or "").strip()
        el.attrib["doRef"] = (res.get("doRef") or "").strip()
        el.attrib["confpin"] = "true" if bool(res.get("confpin")) else "false"
        el.attrib["softlink"] = "true" if bool(res.get("softlink")) else "false"

        self._normalize_afg_pin_ids_and_arrows()
        self._refresh_afg_io_tables()
        self._select_afg_in_element(el)
        self._afg_end_undo_capture(cap)

    def _afg_in_copy(self) -> None:
        el = self._afg_selected_in()
        if el is None:
            return
        self._afg_in_clipboard = _deepcopy_et_element(el)

    def _afg_in_cut(self) -> None:
        el = self._afg_selected_in()
        if el is None:
            return
        self._afg_in_clipboard = _deepcopy_et_element(el)
        self._afg_in_delete()

    def _afg_in_paste(self) -> None:
        if self._afg_in_clipboard is None:
            return
        root = self._afg_root
        if root is None:
            return

        cap = self._afg_begin_undo_capture()
        parent = self._afg_get_or_create_child(root, "afgInItems")
        new_el = _deepcopy_et_element(self._afg_in_clipboard)
        try:
            if "pinID" in new_el.attrib:
                del new_el.attrib["pinID"]
        except Exception:
            pass

        selected = self._afg_selected_in()
        if selected is not None:
            try:
                idx = list(parent).index(selected)
                parent.insert(idx + 1, new_el)
            except Exception:
                parent.append(new_el)
        else:
            parent.append(new_el)

        try:
            self._afg_ui_added_ids.add(id(new_el))
        except Exception:
            pass

        self._normalize_afg_pin_ids_and_arrows()
        self._refresh_afg_io_tables()
        self._select_afg_in_element(new_el)
        self._afg_end_undo_capture(cap)

    def _afg_in_delete(self) -> None:
        root = self._afg_root
        el = self._afg_selected_in()
        if root is None or el is None:
            return

        cap = self._afg_begin_undo_capture()

        is_added = False
        try:
            is_added = id(el) in (self._afg_ui_added_ids or set())
        except Exception:
            is_added = False

        # Only keep a red tombstone until Save when deleting something that
        # existed in the saved baseline. If it was added then deleted before
        # saving, just remove it immediately.
        if not is_added:
            try:
                pos_x, pos_y = self._parse_pos(el.attrib.get("pos") or "")
                confpin = "☑" if (el.attrib.get("confpin") or "").strip().lower() == "true" else "☐"
                softlink = "☑" if (el.attrib.get("softlink") or "").strip().lower() == "true" else "☐"
                self._afg_ui_deleted_in_rows.append(
                    (
                        (el.attrib.get("name") or ""),
                        pos_x,
                        pos_y,
                        (el.attrib.get("src") or ""),
                        (el.attrib.get("doRef") or ""),
                        confpin,
                        softlink,
                    )
                )
            except Exception:
                pass
        try:
            self._afg_ui_added_ids.discard(id(el))
        except Exception:
            pass
        parent = self._afg_get_child(root, "afgInItems")
        if parent is None:
            return
        try:
            parent.remove(el)
        except Exception:
            return
        self._normalize_afg_pin_ids_and_arrows()
        self._refresh_afg_io_tables()
        self._afg_end_undo_capture(cap)

    def _afg_in_up(self) -> None:
        self._afg_in_move(delta=-1)

    def _afg_in_down(self) -> None:
        self._afg_in_move(delta=1)

    def _afg_in_move(self, *, delta: int) -> None:
        root = self._afg_root
        el = self._afg_selected_in()
        if root is None or el is None:
            return

        cap = self._afg_begin_undo_capture()
        parent = self._afg_get_child(root, "afgInItems")
        if parent is None:
            return
        items = [x for x in list(parent) if isinstance(x.tag, str) and _local_name(x.tag) == "afgInItem"]
        try:
            idx = items.index(el)
        except Exception:
            return
        new_idx = idx + delta
        if new_idx < 0 or new_idx >= len(items):
            return
        parent.remove(el)
        items2 = [x for x in list(parent) if isinstance(x.tag, str) and _local_name(x.tag) == "afgInItem"]
        if new_idx >= len(items2):
            parent.append(el)
        else:
            parent.insert(new_idx, el)
        self._normalize_afg_pin_ids_and_arrows()
        self._refresh_afg_io_tables()
        self._select_afg_in_element(el)
        self._afg_end_undo_capture(cap)

    def _afg_out_add(self) -> None:
        self._afg_out_add_impl(insert_mode="append")

    def _afg_out_insert(self) -> None:
        self._afg_out_add_impl(insert_mode="before")

    def _afg_out_add_impl(self, *, insert_mode: str) -> None:
        root = self._afg_root
        if root is None:
            messagebox.showerror("Missing", "No AFG loaded", parent=self)
            return

        cap = self._afg_begin_undo_capture()
        parent = self._afg_get_or_create_child(root, "afgOutItems")
        new_el = self._afg_make_new_out_item()
        try:
            new_el.attrib["name"] = self._afg_out_unique_name(new_el.attrib.get("name") or "NewOut")
        except Exception:
            pass

        selected = self._afg_selected_out()
        if insert_mode == "before" and selected is not None:
            try:
                idx = list(parent).index(selected)
                parent.insert(idx, new_el)
            except Exception:
                parent.append(new_el)
        else:
            parent.append(new_el)

        try:
            self._afg_ui_added_ids.add(id(new_el))
        except Exception:
            pass

        self._normalize_afg_pin_ids_and_arrows()
        self._refresh_afg_io_tables()
        self._select_afg_out_element(new_el)
        try:
            self.after_idle(lambda: self._begin_afg_out_inline_edit_for_selected(col="#1"))
        except Exception:
            pass
        self._afg_end_undo_capture(cap)

    def _afg_out_edit(self) -> None:
        el = self._afg_selected_out()
        if el is None:
            return

        name = (el.attrib.get("name") or "")
        pos_x, pos_y = self._parse_pos(el.attrib.get("pos") or "")
        do_ref = (el.attrib.get("doRef") or "")
        confpin = (el.attrib.get("confpin") or "").strip().lower() == "true"

        while True:
            self._afg_ensure_ln_suggestions_loaded()
            do_ref_values_status = self._afg_doref_values_status(current=do_ref)
            do_ref_values_inref = self._afg_doref_values_inref(current=do_ref)

            dlg = _AfgOutEditDialog(
                self,
                name=name,
                pos_x=pos_x,
                pos_y=pos_y,
                do_ref=do_ref,
                do_ref_values_status=do_ref_values_status,
                do_ref_values_inref=do_ref_values_inref,
                confpin=confpin,
            )
            res = dlg.show()
            if not res:
                return

            name2 = (res.get("name") or "").strip()
            do2 = (res.get("doRef") or "").strip()
            msg = self._afg_out_validate_unique(name=name2, do_ref=do2, exclude=el)
            if msg:
                messagebox.showerror(
                    "Invalid",
                    msg + "\n\nAFG Outputs: name and doRef must be unique.",
                    parent=self,
                )
                name = name2
                do_ref = do2
                pos_x = (res.get("posX") or "").strip()
                pos_y = (res.get("posY") or "").strip()
                confpin = bool(res.get("confpin"))
                continue

            cap = self._afg_begin_undo_capture()

            el.attrib["name"] = name2
            x = (res.get("posX") or "").strip()
            y = (res.get("posY") or "").strip()
            if x or y or "pos" in el.attrib:
                el.attrib["pos"] = f"{x},{y}" if y != "" else x
            el.attrib["doRef"] = do2
            el.attrib["confpin"] = "true" if bool(res.get("confpin")) else "false"
            break

        self._normalize_afg_pin_ids_and_arrows()
        self._refresh_afg_io_tables()
        self._select_afg_out_element(el)

        self._afg_end_undo_capture(cap)

    def _afg_out_copy(self) -> None:
        el = self._afg_selected_out()
        if el is None:
            return
        self._afg_out_clipboard = _deepcopy_et_element(el)

    def _afg_out_cut(self) -> None:
        el = self._afg_selected_out()
        if el is None:
            return
        self._afg_out_clipboard = _deepcopy_et_element(el)
        self._afg_out_delete()

    def _afg_out_paste(self) -> None:
        if self._afg_out_clipboard is None:
            return
        root = self._afg_root
        if root is None:
            return

        cap = self._afg_begin_undo_capture()
        parent = self._afg_get_or_create_child(root, "afgOutItems")
        new_el = _deepcopy_et_element(self._afg_out_clipboard)
        try:
            if "pinID" in new_el.attrib:
                del new_el.attrib["pinID"]
        except Exception:
            pass

        msg = self._afg_out_validate_unique(
            name=(new_el.attrib.get("name") or ""),
            do_ref=(new_el.attrib.get("doRef") or ""),
            exclude=None,
        )
        if msg:
            messagebox.showerror(
                "Invalid",
                msg + "\n\nPaste blocked: AFG Outputs name and doRef must be unique.",
                parent=self,
            )
            return

        selected = self._afg_selected_out()
        if selected is not None:
            try:
                idx = list(parent).index(selected)
                parent.insert(idx + 1, new_el)
            except Exception:
                parent.append(new_el)
        else:
            parent.append(new_el)

        try:
            self._afg_ui_added_ids.add(id(new_el))
        except Exception:
            pass

        self._normalize_afg_pin_ids_and_arrows()
        self._refresh_afg_io_tables()
        self._select_afg_out_element(new_el)
        self._afg_end_undo_capture(cap)

    def _afg_out_delete(self) -> None:
        root = self._afg_root
        el = self._afg_selected_out()
        if root is None or el is None:
            return

        cap = self._afg_begin_undo_capture()

        is_added = False
        try:
            is_added = id(el) in (self._afg_ui_added_ids or set())
        except Exception:
            is_added = False

        if not is_added:
            try:
                pos_x, pos_y = self._parse_pos(el.attrib.get("pos") or "")
                confpin = "☑" if (el.attrib.get("confpin") or "").strip().lower() == "true" else "☐"
                self._afg_ui_deleted_out_rows.append(
                    (
                        (el.attrib.get("name") or ""),
                        pos_x,
                        pos_y,
                        (el.attrib.get("doRef") or ""),
                        confpin,
                    )
                )
            except Exception:
                pass
        try:
            self._afg_ui_added_ids.discard(id(el))
        except Exception:
            pass
        parent = self._afg_get_child(root, "afgOutItems")
        if parent is None:
            return
        try:
            parent.remove(el)
        except Exception:
            return
        self._normalize_afg_pin_ids_and_arrows()
        self._refresh_afg_io_tables()
        self._afg_end_undo_capture(cap)

    def _afg_out_up(self) -> None:
        self._afg_out_move(delta=-1)

    def _afg_out_down(self) -> None:
        self._afg_out_move(delta=1)

    def _afg_out_move(self, *, delta: int) -> None:
        root = self._afg_root
        el = self._afg_selected_out()
        if root is None or el is None:
            return

        cap = self._afg_begin_undo_capture()
        parent = self._afg_get_child(root, "afgOutItems")
        if parent is None:
            return
        items = [x for x in list(parent) if isinstance(x.tag, str) and _local_name(x.tag) == "afgOutItem"]
        try:
            idx = items.index(el)
        except Exception:
            return
        new_idx = idx + delta
        if new_idx < 0 or new_idx >= len(items):
            return
        parent.remove(el)
        items2 = [x for x in list(parent) if isinstance(x.tag, str) and _local_name(x.tag) == "afgOutItem"]
        if new_idx >= len(items2):
            parent.append(el)
        else:
            parent.insert(new_idx, el)
        self._normalize_afg_pin_ids_and_arrows()
        self._refresh_afg_io_tables()
        self._select_afg_out_element(el)
        self._afg_end_undo_capture(cap)

    def _afg_arrow_add(self) -> None:
        self._afg_arrow_add_impl(insert_mode="append")

    def _afg_arrow_insert(self) -> None:
        self._afg_arrow_add_impl(insert_mode="before")

    def _afg_arrow_add_impl(self, *, insert_mode: str) -> None:
        root = self._afg_root
        if root is None:
            messagebox.showerror("Missing", "No AFG loaded", parent=self)
            return

        cap = self._afg_begin_undo_capture()

        arrows_el = self._afg_get_or_create_child(root, "arrows")
        s_owners, s_pins_by_owner, s_owner_for_pin = self._afg_arrow_owner_pin_index_for_side(side="start")
        e_owners, e_pins_by_owner, e_owner_for_pin = self._afg_arrow_owner_pin_index_for_side(side="end")
        total_start = sum(len(v) for v in s_pins_by_owner.values())
        total_end = sum(len(v) for v in e_pins_by_owner.values())
        if total_start < 1 or total_end < 1:
            messagebox.showerror(
                "Missing",
                "Need at least 1 start pin (AFG input or AFB output) and 1 end pin (AFB input or AFG output).",
                parent=self,
            )
            return

        # Pick a reasonable default: first pin as start, second pin (possibly same owner) as end.
        s_flat: list[str] = []
        for o in s_owners:
            for pid, _lbl in s_pins_by_owner.get(o, []):
                s_flat.append(pid)
        e_flat: list[str] = []
        for o in e_owners:
            for pid, _lbl in e_pins_by_owner.get(o, []):
                e_flat.append(pid)

        start_default = s_flat[0] if s_flat else ""
        end_default = e_flat[0] if e_flat else ""
        if start_default and end_default and start_default == end_default:
            for cand in e_flat:
                if cand != start_default:
                    end_default = cand
                    break
        dlg = _AfgArrowEditDialog(
            self,
            title="Add AFG Arrow",
            start_owners=s_owners,
            start_pins_by_owner=s_pins_by_owner,
            start_owner_for_pin=s_owner_for_pin,
            end_owners=e_owners,
            end_pins_by_owner=e_pins_by_owner,
            end_owner_for_pin=e_owner_for_pin,
            start_pin_id=start_default,
            end_pin_id=end_default,
        )
        res = dlg.show()
        if not res:
            return

        new_el = ET.Element(
            "arrowItem",
            attrib={
                "startPinID": (res.get("startPinID") or "").strip(),
                "endPinID": (res.get("endPinID") or "").strip(),
                "zValue": "-1000.000000",
                "lineColor": "#000000",
            },
        )

        selected = self._afg_selected_arrow()
        if insert_mode == "before" and selected is not None:
            try:
                idx = list(arrows_el).index(selected)
                arrows_el.insert(idx, new_el)
            except Exception:
                arrows_el.append(new_el)
        else:
            arrows_el.append(new_el)

        try:
            self._afg_ui_added_ids.add(id(new_el))
        except Exception:
            pass

        self._refresh_afg_arrow_table()
        self._select_afg_arrow_element(new_el)
        try:
            self._on_afg_view_changed()
        except Exception:
            pass

        self._afg_end_undo_capture(cap)

    def _afg_arrow_edit(self) -> None:
        root = self._afg_root
        ar = self._afg_selected_arrow()
        if root is None or ar is None:
            return

        s_owners, s_pins_by_owner, s_owner_for_pin = self._afg_arrow_owner_pin_index_for_side(side="start")
        e_owners, e_pins_by_owner, e_owner_for_pin = self._afg_arrow_owner_pin_index_for_side(side="end")
        total_start = sum(len(v) for v in s_pins_by_owner.values())
        total_end = sum(len(v) for v in e_pins_by_owner.values())
        if total_start < 1 or total_end < 1:
            return

        dlg = _AfgArrowEditDialog(
            self,
            title="Edit AFG Arrow",
            start_owners=s_owners,
            start_pins_by_owner=s_pins_by_owner,
            start_owner_for_pin=s_owner_for_pin,
            end_owners=e_owners,
            end_pins_by_owner=e_pins_by_owner,
            end_owner_for_pin=e_owner_for_pin,
            start_pin_id=(ar.attrib.get("startPinID") or "").strip(),
            end_pin_id=(ar.attrib.get("endPinID") or "").strip(),
        )
        res = dlg.show()
        if not res:
            return

        cap = self._afg_begin_undo_capture()

        ar.attrib["startPinID"] = (res.get("startPinID") or "").strip()
        ar.attrib["endPinID"] = (res.get("endPinID") or "").strip()

        self._refresh_afg_arrow_table()
        self._select_afg_arrow_element(ar)
        try:
            self._on_afg_view_changed()
        except Exception:
            pass

        self._afg_end_undo_capture(cap)

    def _afg_arrow_delete(self) -> None:
        root = self._afg_root
        ar = self._afg_selected_arrow()
        if root is None or ar is None:
            return

        cap = self._afg_begin_undo_capture()

        is_added = False
        try:
            is_added = id(ar) in (self._afg_ui_added_ids or set())
        except Exception:
            is_added = False

        if not is_added:
            try:
                pin_map = self._afg_pin_id_display_map()
                sp = (ar.attrib.get("startPinID") or "").strip()
                ep = (ar.attrib.get("endPinID") or "").strip()
                self._afg_ui_deleted_arrow_rows.append((sp, pin_map.get(sp, ""), ep, pin_map.get(ep, "")))
            except Exception:
                pass
        try:
            self._afg_ui_added_ids.discard(id(ar))
        except Exception:
            pass
        arrows_el = self._afg_get_child(root, "arrows")
        if arrows_el is None:
            return
        try:
            arrows_el.remove(ar)
        except Exception:
            return
        self._refresh_afg_arrow_table()
        try:
            self._on_afg_view_changed()
        except Exception:
            pass

        self._afg_end_undo_capture(cap)

    def _afg_arrow_up(self) -> None:
        self._afg_arrow_move(delta=-1)

    def _afg_arrow_down(self) -> None:
        self._afg_arrow_move(delta=1)

    def _afg_arrow_move(self, *, delta: int) -> None:
        root = self._afg_root
        ar = self._afg_selected_arrow()
        if root is None or ar is None:
            return

        cap = self._afg_begin_undo_capture()
        arrows_el = self._afg_get_child(root, "arrows")
        if arrows_el is None:
            return
        items = [x for x in list(arrows_el) if isinstance(x.tag, str) and _local_name(x.tag) == "arrowItem"]
        try:
            idx = items.index(ar)
        except Exception:
            return
        new_idx = idx + delta
        if new_idx < 0 or new_idx >= len(items):
            return
        try:
            arrows_el.remove(ar)
        except Exception:
            return

        items2 = [x for x in list(arrows_el) if isinstance(x.tag, str) and _local_name(x.tag) == "arrowItem"]
        if new_idx >= len(items2):
            arrows_el.append(ar)
        else:
            arrows_el.insert(new_idx, ar)

        self._refresh_afg_arrow_table()
        self._select_afg_arrow_element(ar)
        try:
            self._on_afg_view_changed()
        except Exception:
            pass

        self._afg_end_undo_capture(cap)

    def _afg_arrow_graph(self) -> None:
        root = self._afg_root
        if root is None:
            messagebox.showerror("Missing", "No AFG loaded", parent=self)
            return

        # Viewer dialog (modeless) - uses current in-memory document.
        dlg = self._afg_arrow_graph_dlg
        try:
            if dlg is not None:
                try:
                    exists = bool(dlg.winfo_exists())
                except Exception:
                    exists = False
                if exists:
                    try:
                        dlg.lift()
                        dlg.focus_force()
                    except Exception:
                        pass
                    try:
                        dlg.highlight_arrow_element(self._afg_selected_arrow())
                    except Exception:
                        pass
                    return
                self._afg_arrow_graph_dlg = None

            def on_close() -> None:
                self._afg_arrow_graph_dlg = None
                try:
                    self._afg_end_undo_capture(self._afg_arrow_graph_undo_cap)
                except Exception:
                    pass
                self._afg_arrow_graph_undo_cap = None

            def on_select_from_graph(el: ET.Element | None) -> None:
                tv = self._afg_tv_arrows
                if tv is None:
                    return
                self._afg_arrow_graph_syncing = True
                try:
                    if el is None:
                        try:
                            tv.selection_remove(tv.selection())
                        except Exception:
                            pass
                        return
                    self._select_afg_arrow_element(el)
                finally:
                    self._afg_arrow_graph_syncing = False

            def on_arrows_changed_from_graph(el: ET.Element | None) -> None:
                tv = self._afg_tv_arrows
                if tv is None:
                    return
                self._afg_arrow_graph_syncing = True
                try:
                    self._refresh_afg_arrow_table()
                    if el is None:
                        try:
                            tv.selection_remove(tv.selection())
                        except Exception:
                            pass
                    else:
                        self._select_afg_arrow_element(el)
                    try:
                        self._on_afg_view_changed()
                    except Exception:
                        pass
                finally:
                    self._afg_arrow_graph_syncing = False

            # Capture a single undo snapshot for all edits performed in the graph dialog.
            self._afg_arrow_graph_undo_cap = self._afg_begin_undo_capture()

            self._afg_arrow_graph_dlg = _AfgArrowGraphDialog(
                self,
                title="AFG Arrow Graph",
                root=root,
                on_select_arrow=on_select_from_graph,
                on_arrows_changed=on_arrows_changed_from_graph,
                on_close=on_close,
                initial_selected=self._afg_selected_arrow(),
            )
        except Exception as e:
            messagebox.showerror("Graph", str(e), parent=self)
            self._afg_arrow_graph_dlg = None
            self._afg_arrow_graph_undo_cap = None

    def _on_afg_arrow_select_changed(self) -> None:
        if self._afg_arrow_graph_syncing:
            return
        dlg = self._afg_arrow_graph_dlg
        if dlg is None:
            return
        try:
            if not bool(dlg.winfo_exists()):
                self._afg_arrow_graph_dlg = None
                return
        except Exception:
            self._afg_arrow_graph_dlg = None
            return
        try:
            dlg.highlight_arrow_element(self._afg_selected_arrow())
        except Exception:
            pass

    def _on_afg_arrow_right_click(self, e: tk.Event) -> None:
        tv = self._afg_tv_arrows
        if tv is None:
            return
        iid = None
        try:
            iid = tv.identify_row(e.y)
        except Exception:
            iid = None
        if iid:
            try:
                tv.selection_set(iid)
                tv.focus(iid)
            except Exception:
                pass

        if self._afg_arrow_ctx_menu is None:
            m = tk.Menu(self, tearoff=0)
            m.add_command(label="Add", command=self._afg_arrow_add)
            m.add_command(label="Insert", command=self._afg_arrow_insert)
            m.add_command(label="Edit", command=self._afg_arrow_edit)
            m.add_separator()
            m.add_command(label="Delete", command=self._afg_arrow_delete)
            m.add_separator()
            m.add_command(label="Up", command=self._afg_arrow_up)
            m.add_command(label="Down", command=self._afg_arrow_down)
            self._afg_arrow_ctx_menu = m

        try:
            self._afg_arrow_ctx_menu.tk_popup(e.x_root, e.y_root)
        finally:
            try:
                self._afg_arrow_ctx_menu.grab_release()
            except Exception:
                pass

    def _begin_afg_in_inline_edit_for_selected(self, *, col: str) -> None:
        iid = self._afg_selected_in_iid()
        if iid is None:
            return
        self._begin_afg_in_inline_edit(iid, col)

    def _begin_afg_out_inline_edit_for_selected(self, *, col: str) -> None:
        iid = self._afg_selected_out_iid()
        if iid is None:
            return
        self._begin_afg_out_inline_edit(iid, col)

    def _on_afg_in_inline_combobox_focus_out(self, event: tk.Event) -> None:
        """Commit AFG-in inline edit on focus-out, but avoid committing while dropdown is open."""
        try:
            widget = event.widget
        except Exception:
            widget = None

        if not isinstance(widget, ttk.Combobox):
            self._end_afg_in_inline_editor(commit=True)
            return

        cb = widget
        try:
            popdown = cb.tk.call("ttk::combobox::PopdownWindow", str(cb))
            focus_w = str(cb.tk.call("focus") or "")
            if popdown and focus_w and focus_w.startswith(str(popdown)):
                return
        except Exception:
            pass

        if self._combobox_is_posted(cb):
            try:
                self.after(
                    75,
                    lambda: (
                        self._end_afg_in_inline_editor(commit=True)
                        if self._afg_in_inline is cb and not self._combobox_is_posted(cb)
                        else None
                    ),
                )
            except Exception:
                pass
            return

        self._end_afg_in_inline_editor(commit=True)

    def _on_afg_out_inline_combobox_focus_out(self, event: tk.Event) -> None:
        """Commit AFG-out inline edit on focus-out, but avoid committing while dropdown is open."""
        try:
            widget = event.widget
        except Exception:
            widget = None

        if not isinstance(widget, ttk.Combobox):
            self._end_afg_out_inline_editor(commit=True)
            return

        cb = widget
        try:
            popdown = cb.tk.call("ttk::combobox::PopdownWindow", str(cb))
            focus_w = str(cb.tk.call("focus") or "")
            if popdown and focus_w and focus_w.startswith(str(popdown)):
                return
        except Exception:
            pass

        if self._combobox_is_posted(cb):
            try:
                self.after(
                    75,
                    lambda: (
                        self._end_afg_out_inline_editor(commit=True)
                        if self._afg_out_inline is cb and not self._combobox_is_posted(cb)
                        else None
                    ),
                )
            except Exception:
                pass
            return

        self._end_afg_out_inline_editor(commit=True)

    def _begin_afg_in_inline_edit(self, iid: str, col: str) -> None:
        tv = self._afg_tv_in
        if tv is None:
            return
        el = self._afg_in_iid_to_item.get(iid)
        if el is None:
            return

        # Commit any existing inline editors
        self._end_afg_out_inline_editor(commit=True)
        self._end_afg_in_inline_editor(commit=True)
        self._end_afg_fb_inline_editor(commit=True)

        key_by_col = {"#1": "name", "#2": "posX", "#3": "posY", "#4": "src", "#5": "doRef"}
        key = key_by_col.get(col)
        if key is None:
            return

        bbox = tv.bbox(iid, col)
        if not bbox:
            return
        x, y, w, h = bbox
        if key == "posX":
            value, _y0 = self._parse_pos(el.attrib.get("pos") or "")
        elif key == "posY":
            _x0, value = self._parse_pos(el.attrib.get("pos") or "")
        else:
            value = el.attrib.get(key) or ""
        if key == "doRef":
            self._afg_ensure_ln_suggestions_loaded()
            values = tuple(self._afg_doref_values_inref(current=value))
            cb = ttk.Combobox(tv, values=values)
            cb.place(x=x, y=y, width=w, height=h)
            cb.set(value)
            cb.focus_set()

            cb.bind("<<ComboboxSelected>>", lambda _e: self._end_afg_in_inline_editor(commit=True))
            cb.bind("<Return>", lambda _e: self._end_afg_in_inline_editor(commit=True))
            cb.bind("<Escape>", lambda _e: self._end_afg_in_inline_editor(commit=False))
            cb.bind("<FocusOut>", self._on_afg_in_inline_combobox_focus_out)
            cb.bind("<Button-1>", lambda _e: (self._combobox_toggle_posted(cb), "break")[1])
            try:
                tv.after_idle(lambda: self._combobox_post(cb))
            except Exception:
                self._combobox_post(cb)

            self._afg_in_inline = cb
            self._afg_in_inline_iid = iid
            self._afg_in_inline_col = col
            return

        ent = ttk.Entry(tv)
        ent.insert(0, value)

        def commit_and_close(_e=None) -> None:
            self._end_afg_in_inline_editor(commit=True)

        ent.bind("<Return>", commit_and_close)
        ent.bind("<Escape>", lambda _e: self._end_afg_in_inline_editor(commit=False))
        ent.bind("<FocusOut>", lambda _e: self._end_afg_in_inline_editor(commit=True))
        ent.place(x=x, y=y, width=w, height=h)
        ent.focus_set()
        try:
            ent.select_range(0, tk.END)
        except Exception:
            pass

        self._afg_in_inline = ent
        self._afg_in_inline_iid = iid
        self._afg_in_inline_col = col

    def _begin_afg_out_inline_edit(self, iid: str, col: str) -> None:
        tv = self._afg_tv_out
        if tv is None:
            return
        el = self._afg_out_iid_to_item.get(iid)
        if el is None:
            return

        self._end_afg_in_inline_editor(commit=True)
        self._end_afg_out_inline_editor(commit=True)
        self._end_afg_fb_inline_editor(commit=True)

        key_by_col = {"#1": "name", "#2": "posX", "#3": "posY", "#4": "doRef"}
        key = key_by_col.get(col)
        if key is None:
            return

        bbox = tv.bbox(iid, col)
        if not bbox:
            return
        x, y, w, h = bbox
        if key == "posX":
            value, _y0 = self._parse_pos(el.attrib.get("pos") or "")
        elif key == "posY":
            _x0, value = self._parse_pos(el.attrib.get("pos") or "")
        else:
            value = el.attrib.get(key) or ""
        if key == "doRef":
            self._afg_ensure_ln_suggestions_loaded()
            confpin = (el.attrib.get("confpin") or "").strip().lower() == "true"
            values = (
                self._afg_doref_values_inref(current=value)
                if confpin
                else self._afg_doref_values_status(current=value)
            )
            cb = ttk.Combobox(tv, values=tuple(values))
            cb.place(x=x, y=y, width=w, height=h)
            cb.set(value)
            cb.focus_set()

            cb.bind("<<ComboboxSelected>>", lambda _e: self._end_afg_out_inline_editor(commit=True))
            cb.bind("<Return>", lambda _e: self._end_afg_out_inline_editor(commit=True))
            cb.bind("<Escape>", lambda _e: self._end_afg_out_inline_editor(commit=False))
            cb.bind("<FocusOut>", self._on_afg_out_inline_combobox_focus_out)
            cb.bind("<Button-1>", lambda _e: (self._combobox_toggle_posted(cb), "break")[1])
            try:
                tv.after_idle(lambda: self._combobox_post(cb))
            except Exception:
                self._combobox_post(cb)

            self._afg_out_inline = cb
            self._afg_out_inline_iid = iid
            self._afg_out_inline_col = col
            return

        ent = ttk.Entry(tv)
        ent.insert(0, value)

        def commit_and_close(_e=None) -> None:
            self._end_afg_out_inline_editor(commit=True)

        ent.bind("<Return>", commit_and_close)
        ent.bind("<Escape>", lambda _e: self._end_afg_out_inline_editor(commit=False))
        ent.bind("<FocusOut>", lambda _e: self._end_afg_out_inline_editor(commit=True))
        ent.place(x=x, y=y, width=w, height=h)
        ent.focus_set()
        try:
            ent.select_range(0, tk.END)
        except Exception:
            pass

        self._afg_out_inline = ent
        self._afg_out_inline_iid = iid
        self._afg_out_inline_col = col

    def _end_afg_in_inline_editor(self, *, commit: bool) -> None:
        ent = self._afg_in_inline
        if ent is None:
            return
        iid = self._afg_in_inline_iid
        col = self._afg_in_inline_col
        self._afg_in_inline = None
        self._afg_in_inline_iid = None
        self._afg_in_inline_col = None

        tv = self._afg_tv_in
        el = self._afg_in_iid_to_item.get(iid or "") if iid is not None else None
        key_by_col = {"#1": "name", "#2": "posX", "#3": "posY", "#4": "src", "#5": "doRef"}
        key = key_by_col.get(col or "")
        try:
            value = ent.get()
        except Exception:
            value = ""
        try:
            ent.destroy()
        except Exception:
            pass

        cap = None
        if commit and el is not None and key is not None:
            cap = self._afg_begin_undo_capture()
            if key in {"posX", "posY"}:
                x0, y0 = self._parse_pos(el.attrib.get("pos") or "")
                x1, y1 = x0, y0
                if key == "posX":
                    x1 = (value or "").strip()
                else:
                    y1 = (value or "").strip()
                if x1 or y1 or "pos" in el.attrib:
                    el.attrib["pos"] = f"{x1},{y1}" if y1 != "" else x1
            else:
                proposed = (value or "").strip()
                if key == "name":
                    msg = self._afg_out_validate_unique(
                        name=proposed,
                        do_ref=(el.attrib.get("doRef") or ""),
                        exclude=el,
                    )
                    if msg:
                        messagebox.showerror(
                            "Invalid",
                            msg + "\n\nAFG Outputs: name and doRef must be unique.",
                            parent=self,
                        )
                        self._refresh_afg_io_tables()
                        self._select_afg_out_element(el)
                        return

                if key == "doRef":
                    msg = self._afg_out_validate_unique(
                        name=(el.attrib.get("name") or ""),
                        do_ref=proposed,
                        exclude=el,
                    )
                    if msg:
                        messagebox.showerror(
                            "Invalid",
                            msg + "\n\nAFG Outputs: name and doRef must be unique.",
                            parent=self,
                        )
                        self._refresh_afg_io_tables()
                        self._select_afg_out_element(el)
                        return

                el.attrib[key] = proposed
                self._normalize_afg_pin_ids_and_arrows()
        self._refresh_afg_io_tables()
        if el is not None:
            self._select_afg_in_element(el)
        try:
            if tv is not None:
                tv.focus_set()
        except Exception:
            pass
        self._afg_end_undo_capture(cap)

    def _end_afg_out_inline_editor(self, *, commit: bool) -> None:
        ent = self._afg_out_inline
        if ent is None:
            return
        iid = self._afg_out_inline_iid
        col = self._afg_out_inline_col
        self._afg_out_inline = None
        self._afg_out_inline_iid = None
        self._afg_out_inline_col = None

        tv = self._afg_tv_out
        el = self._afg_out_iid_to_item.get(iid or "") if iid is not None else None
        key_by_col = {"#1": "name", "#2": "posX", "#3": "posY", "#4": "doRef"}
        key = key_by_col.get(col or "")
        try:
            value = ent.get()
        except Exception:
            value = ""
        try:
            ent.destroy()
        except Exception:
            pass

        cap = None
        if commit and el is not None and key is not None:
            cap = self._afg_begin_undo_capture()
            if key in {"posX", "posY"}:
                x0, y0 = self._parse_pos(el.attrib.get("pos") or "")
                x1, y1 = x0, y0
                if key == "posX":
                    x1 = (value or "").strip()
                else:
                    y1 = (value or "").strip()
                if x1 or y1 or "pos" in el.attrib:
                    el.attrib["pos"] = f"{x1},{y1}" if y1 != "" else x1
            else:
                el.attrib[key] = (value or "").strip()
                self._normalize_afg_pin_ids_and_arrows()
        self._refresh_afg_io_tables()
        if el is not None:
            self._select_afg_out_element(el)
        try:
            if tv is not None:
                tv.focus_set()
        except Exception:
            pass
        self._afg_end_undo_capture(cap)

    def _on_afg_in_click(self, event: tk.Event) -> str | None:
        tv = self._afg_tv_in
        if tv is None:
            return None
        region = tv.identify("region", event.x, event.y)
        if region != "cell":
            return None
        col = tv.identify_column(event.x)
        iid = tv.identify_row(event.y)
        if not iid:
            return None
        try:
            tv.selection_set(iid)
        except Exception:
            pass

        # Checkbox columns: confpin (#6), softlink (#7)
        if col in {"#6", "#7"}:
            # Close any active inline editor (discard edits) before toggling.
            if self._afg_in_inline is not None:
                try:
                    self._afg_in_inline.destroy()
                except Exception:
                    pass
                self._afg_in_inline = None
                self._afg_in_inline_iid = None
                self._afg_in_inline_col = None

            el = self._afg_in_iid_to_item.get(iid)
            if el is None:
                return None
            cap = self._afg_begin_undo_capture()
            key = "confpin" if col == "#6" else "softlink"
            cur = (el.attrib.get(key) or "").strip().lower() == "true"
            el.attrib[key] = "false" if cur else "true"
            self._normalize_afg_pin_ids_and_arrows()
            self._refresh_afg_io_tables()
            self._select_afg_in_element(el)
            self._afg_end_undo_capture(cap)
            return "break"
        return None

    def _on_afg_out_click(self, event: tk.Event) -> str | None:
        tv = self._afg_tv_out
        if tv is None:
            return None
        region = tv.identify("region", event.x, event.y)
        if region != "cell":
            return None
        col = tv.identify_column(event.x)
        iid = tv.identify_row(event.y)
        if not iid:
            return None
        try:
            tv.selection_set(iid)
        except Exception:
            pass
        # Checkbox column: confpin (#5)
        if col == "#5":
            if self._afg_out_inline is not None:
                try:
                    self._afg_out_inline.destroy()
                except Exception:
                    pass
                self._afg_out_inline = None
                self._afg_out_inline_iid = None
                self._afg_out_inline_col = None

            el = self._afg_out_iid_to_item.get(iid)
            if el is None:
                return None
            cap = self._afg_begin_undo_capture()
            cur = (el.attrib.get("confpin") or "").strip().lower() == "true"
            el.attrib["confpin"] = "false" if cur else "true"
            self._normalize_afg_pin_ids_and_arrows()
            self._refresh_afg_io_tables()
            self._select_afg_out_element(el)
            self._afg_end_undo_capture(cap)
            return "break"
        return None

    def _on_afg_in_double_click(self, event: tk.Event) -> str:
        tv = self._afg_tv_in
        if tv is None:
            return "break"
        region = tv.identify("region", event.x, event.y)
        if region != "cell":
            return "break"
        col = tv.identify_column(event.x)
        iid = tv.identify_row(event.y)
        if not iid:
            return "break"
        try:
            tv.selection_set(iid)
        except Exception:
            pass
        if col in {"#1", "#2", "#3", "#4", "#5"}:
            try:
                tv.after_idle(lambda: self._begin_afg_in_inline_edit(iid, col))
            except Exception:
                self._begin_afg_in_inline_edit(iid, col)
        return "break"

    def _on_afg_out_double_click(self, event: tk.Event) -> str:
        tv = self._afg_tv_out
        if tv is None:
            return "break"
        region = tv.identify("region", event.x, event.y)
        if region != "cell":
            return "break"
        col = tv.identify_column(event.x)
        iid = tv.identify_row(event.y)
        if not iid:
            return "break"
        try:
            tv.selection_set(iid)
        except Exception:
            pass
        if col in {"#1", "#2", "#3", "#4"}:
            try:
                tv.after_idle(lambda: self._begin_afg_out_inline_edit(iid, col))
            except Exception:
                self._begin_afg_out_inline_edit(iid, col)
        return "break"

    def _on_afg_in_right_click(self, e: tk.Event) -> None:
        tv = self._afg_tv_in
        if tv is None:
            return
        iid = None
        try:
            iid = tv.identify_row(e.y)
        except Exception:
            iid = None
        if iid:
            try:
                tv.selection_set(iid)
                tv.focus(iid)
            except Exception:
                pass

        if self._afg_in_ctx_menu is None:
            m = tk.Menu(self, tearoff=0)
            m.add_command(label="Add", command=self._afg_in_add)
            m.add_command(label="Insert", command=self._afg_in_insert)
            m.add_command(label="Edit", command=self._afg_in_edit)
            m.add_separator()
            m.add_command(label="Copy", command=self._afg_in_copy)
            m.add_command(label="Cut", command=self._afg_in_cut)
            m.add_command(label="Paste", command=self._afg_in_paste)
            m.add_separator()
            m.add_command(label="Delete", command=self._afg_in_delete)
            m.add_separator()
            m.add_command(label="Up", command=self._afg_in_up)
            m.add_command(label="Down", command=self._afg_in_down)
            self._afg_in_ctx_menu = m

        try:
            self._afg_in_ctx_menu.tk_popup(e.x_root, e.y_root)
        finally:
            try:
                self._afg_in_ctx_menu.grab_release()
            except Exception:
                pass

    def _on_afg_out_right_click(self, e: tk.Event) -> None:
        tv = self._afg_tv_out
        if tv is None:
            return
        iid = None
        try:
            iid = tv.identify_row(e.y)
        except Exception:
            iid = None
        if iid:
            try:
                tv.selection_set(iid)
                tv.focus(iid)
            except Exception:
                pass

        if self._afg_out_ctx_menu is None:
            m = tk.Menu(self, tearoff=0)
            m.add_command(label="Add", command=self._afg_out_add)
            m.add_command(label="Insert", command=self._afg_out_insert)
            m.add_command(label="Edit", command=self._afg_out_edit)
            m.add_separator()
            m.add_command(label="Copy", command=self._afg_out_copy)
            m.add_command(label="Cut", command=self._afg_out_cut)
            m.add_command(label="Paste", command=self._afg_out_paste)
            m.add_separator()
            m.add_command(label="Delete", command=self._afg_out_delete)
            m.add_separator()
            m.add_command(label="Up", command=self._afg_out_up)
            m.add_command(label="Down", command=self._afg_out_down)
            self._afg_out_ctx_menu = m

        try:
            self._afg_out_ctx_menu.tk_popup(e.x_root, e.y_root)
        finally:
            try:
                self._afg_out_ctx_menu.grab_release()
            except Exception:
                pass

    def _afg_max_pin_id(self) -> int:
        root = self._afg_root
        if root is None:
            return 0
        m = 0
        try:
            m = int((root.attrib.get("maxPinID") or "0").strip() or "0")
        except Exception:
            m = 0
        for el in root.iter():
            if not isinstance(el.tag, str):
                continue
            v = (el.attrib.get("pinID") or "").strip()
            if not v:
                continue
            try:
                iv = int(v)
            except Exception:
                continue
            if iv > m:
                m = iv
        return m

    def _afg_suggest_new_fb_pos(self) -> tuple[float, float]:
        root = self._afg_root
        if root is None:
            return (0.0, 0.0)
        fb_items_el = self._afg_get_child(root, "fbItems")
        if fb_items_el is None:
            return (300.0, 100.0)
        max_x = None
        y = 100.0
        for fb in list(fb_items_el):
            if not (isinstance(fb.tag, str) and _local_name(fb.tag) == "fbItem"):
                continue
            px, py = self._parse_pos(fb.attrib.get("pos") or "")
            try:
                fx = float(px) if px.strip() else 0.0
            except Exception:
                fx = 0.0
            try:
                fy = float(py) if py.strip() else 0.0
            except Exception:
                fy = 0.0
            if max_x is None or fx > max_x:
                max_x = fx
                y = fy
        if max_x is None:
            return (300.0, 100.0)
        return (max_x + 400.0, y)

    def _read_application_funblock_io(self, path: Path) -> tuple[str, list[str], list[str]]:
        path = Path(path)
        tree = ET.parse(path)
        root = tree.getroot()

        funblock = None
        for el in root.iter():
            if not isinstance(el.tag, str):
                continue
            if self._local_name(el.tag) == "funBlock":
                funblock = el
                break
        if funblock is None:
            raise ValueError("No <funBlock> found")

        fb_name = (funblock.attrib.get("name") or "").strip() or path.stem
        inputs: list[str] = []
        outputs: list[str] = []
        for ch in list(funblock):
            if not isinstance(ch.tag, str):
                continue
            local = self._local_name(ch.tag)
            if local == "input":
                n = (ch.attrib.get("name") or "").strip()
                if n:
                    inputs.append(n)
            elif local == "output":
                n = (ch.attrib.get("name") or "").strip()
                if n:
                    outputs.append(n)
        return (fb_name, inputs, outputs)

    def _afg_fb_add(self) -> None:
        self._afg_fb_add_from_application(insert_mode="append")

    def _afg_fb_insert(self) -> None:
        self._afg_fb_add_from_application(insert_mode="before")

    def _afg_fb_add_from_application(self, *, insert_mode: str) -> None:
        root = self._afg_root
        if root is None:
            messagebox.showerror("Missing", "No AFG loaded", parent=self)
            return

        cap = self._afg_begin_undo_capture()

        app_dir = self._application_dir()
        items = self._scan_xml_relpaths(app_dir)
        if not items:
            messagebox.showerror("Missing", f"No application (*.xml) found under:\n\n{os.fspath(app_dir)}", parent=self)
            return

        dlg = _PickFromListDialog(
            self,
            title="Pick application",
            label="Application file",
            items=items,
            initial="",
        )
        rel = dlg.show()
        if not rel:
            return
        rel = rel.strip()
        app_path = app_dir / rel
        if not app_path.exists():
            messagebox.showerror("Missing", f"File not found:\n\n{os.fspath(app_path)}", parent=self)
            return

        try:
            fb_name, input_names, output_names = self._read_application_funblock_io(app_path)
        except Exception as e:
            messagebox.showerror("Open failed", str(e), parent=self)
            return

        fb_items_el = self._afg_get_or_create_child(root, "fbItems")

        # Create a new fbItem with IO pins based on Application funBlock.
        x, y = self._afg_suggest_new_fb_pos()
        new_fb = ET.Element(
            "fbItem",
            attrib={
                "pos": f"{x:.6f},{y:.6f}",
                "name": fb_name,
                "lineColor": "#000000",
                "itemColor": "#ffffff",
            },
        )

        inputs_el = ET.SubElement(new_fb, "Inputs")
        outputs_el = ET.SubElement(new_fb, "Outputs")

        next_pin = self._afg_max_pin_id() + 1
        for n in input_names:
            ET.SubElement(
                inputs_el,
                "Input",
                attrib={
                    "name": n,
                    "lineColor": "#000000",
                    "itemColor": "#000000",
                    "pinLineColor": "#000000",
                    "pinID": str(next_pin),
                },
            )
            next_pin += 1
        for n in output_names:
            ET.SubElement(
                outputs_el,
                "Output",
                attrib={
                    "name": n,
                    "lineColor": "#000000",
                    "itemColor": "#000000",
                    "pinLineColor": "#000000",
                    "pinID": str(next_pin),
                },
            )
            next_pin += 1
        try:
            root.attrib["maxPinID"] = str(max(self._afg_max_pin_id(), next_pin - 1))
        except Exception:
            pass

        selected = self._afg_selected_fb()
        if insert_mode == "before" and selected is not None:
            idx = list(fb_items_el).index(selected)
            fb_items_el.insert(idx, new_fb)
        else:
            fb_items_el.append(new_fb)

        try:
            self._afg_ui_added_ids.add(id(new_fb))
        except Exception:
            pass

        self._normalize_afg_pin_ids_and_arrows()
        self._refresh_afg_views(select_first_fb=False)
        self._select_fb_element(new_fb)
        self._afg_end_undo_capture(cap)

    def _select_fb_element(self, fb: ET.Element) -> None:
        if self._afg_tv_fb is None:
            return
        for iid, el in self._afg_fb_iid_to_item.items():
            if el is fb:
                try:
                    self._afg_tv_fb.selection_set(iid)
                    self._afg_tv_fb.focus(iid)
                    self._afg_tv_fb.see(iid)
                except Exception:
                    pass
                return

    def _afg_fb_edit(self) -> None:
        fb = self._afg_selected_fb()
        if fb is None:
            return
        name = (fb.attrib.get("name") or "")
        pos_x, pos_y = self._parse_pos(fb.attrib.get("pos") or "")
        dlg = _AfgFbEditDialog(self, name=name, pos_x=pos_x, pos_y=pos_y)
        res = dlg.show()
        if not res:
            return

        cap = self._afg_begin_undo_capture()
        x = (res.get("posX") or "").strip()
        y = (res.get("posY") or "").strip()
        if x or y or "pos" in fb.attrib:
            fb.attrib["pos"] = f"{x},{y}" if y != "" else x
        self._refresh_afg_views(select_first_fb=False)
        self._select_fb_element(fb)
        self._afg_end_undo_capture(cap)

    def _on_afg_fb_double_click(self, event: tk.Event) -> str:
        tv = self._afg_tv_fb
        if tv is None:
            return "break"
        region = tv.identify("region", event.x, event.y)
        if region != "cell":
            return "break"
        col = tv.identify_column(event.x)
        iid = tv.identify_row(event.y)
        if not iid:
            return "break"
        try:
            tv.selection_set(iid)
        except Exception:
            pass

        # Only posX/posY are editable by double-click. (name is not editable)
        if col in {"#2", "#3"}:
            try:
                tv.after_idle(lambda: self._begin_afg_fb_inline_edit(iid, col))
            except Exception:
                self._begin_afg_fb_inline_edit(iid, col)
        return "break"

    def _begin_afg_fb_inline_edit(self, iid: str, col: str) -> None:
        tv = self._afg_tv_fb
        if tv is None:
            return
        el = self._afg_fb_iid_to_item.get(iid)
        if el is None:
            return

        # Commit any existing inline editors
        self._end_afg_in_inline_editor(commit=True)
        self._end_afg_out_inline_editor(commit=True)
        self._end_afg_fb_inline_editor(commit=True)

        if col not in {"#2", "#3"}:
            return

        bbox = tv.bbox(iid, col)
        if not bbox:
            return
        x, y, w, h = bbox

        pos_x, pos_y = self._parse_pos(el.attrib.get("pos") or "")
        value = pos_x if col == "#2" else pos_y

        ent = ttk.Entry(tv)
        ent.insert(0, value)

        def commit_and_close(_e=None) -> None:
            self._end_afg_fb_inline_editor(commit=True)

        ent.bind("<Return>", commit_and_close)
        ent.bind("<Escape>", lambda _e: self._end_afg_fb_inline_editor(commit=False))
        ent.bind("<FocusOut>", lambda _e: self._end_afg_fb_inline_editor(commit=True))
        ent.place(x=x, y=y, width=w, height=h)
        ent.focus_set()
        try:
            ent.select_range(0, tk.END)
        except Exception:
            pass

        self._afg_fb_inline = ent
        self._afg_fb_inline_iid = iid
        self._afg_fb_inline_col = col

    def _end_afg_fb_inline_editor(self, *, commit: bool) -> None:
        ent = self._afg_fb_inline
        if ent is None:
            return
        iid = self._afg_fb_inline_iid
        col = self._afg_fb_inline_col
        self._afg_fb_inline = None
        self._afg_fb_inline_iid = None
        self._afg_fb_inline_col = None

        tv = self._afg_tv_fb
        el = self._afg_fb_iid_to_item.get(iid or "") if iid is not None else None
        try:
            value = ent.get()
        except Exception:
            value = ""
        try:
            ent.destroy()
        except Exception:
            pass

        cap = None
        if commit and el is not None and col in {"#2", "#3"}:
            cap = self._afg_begin_undo_capture()
            x0, y0 = self._parse_pos(el.attrib.get("pos") or "")
            x1, y1 = x0, y0
            if col == "#2":
                x1 = (value or "").strip()
            else:
                y1 = (value or "").strip()
            if x1 or y1 or "pos" in el.attrib:
                el.attrib["pos"] = f"{x1},{y1}" if y1 != "" else x1

        self._refresh_afg_views(select_first_fb=False)
        if el is not None:
            self._select_fb_element(el)
        try:
            if tv is not None:
                tv.focus_set()
        except Exception:
            pass
        self._afg_end_undo_capture(cap)

    def _afg_fb_copy(self) -> None:
        fb = self._afg_selected_fb()
        if fb is None:
            return
        self._afg_fb_clipboard = _deepcopy_et_element(fb)

    def _afg_fb_cut(self) -> None:
        fb = self._afg_selected_fb()
        if fb is None:
            return
        self._afg_fb_clipboard = _deepcopy_et_element(fb)
        self._afg_fb_delete()

    def _afg_fb_paste(self) -> None:
        if self._afg_fb_clipboard is None:
            return
        root = self._afg_root
        if root is None:
            return

        cap = self._afg_begin_undo_capture()
        fb_items_el = self._afg_get_or_create_child(root, "fbItems")

        new_fb = _deepcopy_et_element(self._afg_fb_clipboard)

        selected = self._afg_selected_fb()
        if selected is not None:
            idx = list(fb_items_el).index(selected)
            fb_items_el.insert(idx + 1, new_fb)
        else:
            fb_items_el.append(new_fb)

        try:
            self._afg_ui_added_ids.add(id(new_fb))
        except Exception:
            pass

        self._normalize_afg_pin_ids_and_arrows()
        self._refresh_afg_views(select_first_fb=False)
        self._select_fb_element(new_fb)
        self._afg_end_undo_capture(cap)

    def _afg_fb_delete(self) -> None:
        root = self._afg_root
        fb = self._afg_selected_fb()
        if root is None or fb is None:
            return

        cap = self._afg_begin_undo_capture()

        is_added = False
        try:
            is_added = id(fb) in (self._afg_ui_added_ids or set())
        except Exception:
            is_added = False

        if not is_added:
            try:
                # Capture a tombstone row for UI (keep visible until Save).
                def _count_io(_fb: ET.Element, local: str, child_local: str) -> int:
                    box = None
                    for x in list(_fb):
                        if isinstance(x.tag, str) and _local_name(x.tag) == local:
                            box = x
                            break
                    if box is None:
                        return 0
                    return sum(1 for x in list(box) if isinstance(x.tag, str) and _local_name(x.tag) == child_local)

                name = (fb.attrib.get("name") or "").strip()
                pos_x, pos_y = self._parse_pos(fb.attrib.get("pos") or "")
                in_n = _count_io(fb, "Inputs", "Input")
                out_n = _count_io(fb, "Outputs", "Output")
                self._afg_ui_deleted_fb_rows.append((name, pos_x, pos_y, str(in_n), str(out_n)))
            except Exception:
                pass
        try:
            self._afg_ui_added_ids.discard(id(fb))
        except Exception:
            pass
        fb_items_el = self._afg_get_child(root, "fbItems")
        if fb_items_el is None:
            return
        try:
            fb_items_el.remove(fb)
        except Exception:
            return

        self._normalize_afg_pin_ids_and_arrows()
        self._refresh_afg_views(select_first_fb=True)
        self._afg_end_undo_capture(cap)

    def _afg_fb_up(self) -> None:
        self._afg_fb_move(delta=-1)

    def _afg_fb_down(self) -> None:
        self._afg_fb_move(delta=1)

    def _afg_fb_move(self, *, delta: int) -> None:
        root = self._afg_root
        fb = self._afg_selected_fb()
        if root is None or fb is None:
            return

        cap = self._afg_begin_undo_capture()
        fb_items_el = self._afg_get_child(root, "fbItems")
        if fb_items_el is None:
            return
        items = [x for x in list(fb_items_el) if isinstance(x.tag, str) and _local_name(x.tag) == "fbItem"]
        try:
            idx = items.index(fb)
        except Exception:
            return
        new_idx = idx + delta
        if new_idx < 0 or new_idx >= len(items):
            return

        fb_items_el.remove(fb)
        # Recompute list after removal.
        items2 = [x for x in list(fb_items_el) if isinstance(x.tag, str) and _local_name(x.tag) == "fbItem"]
        if new_idx >= len(items2):
            fb_items_el.append(fb)
        else:
            fb_items_el.insert(new_idx, fb)

        self._normalize_afg_pin_ids_and_arrows()
        self._refresh_afg_views(select_first_fb=False)
        self._select_fb_element(fb)
        self._afg_end_undo_capture(cap)

    def _on_afg_fb_right_click(self, e: tk.Event) -> None:
        if self._afg_tv_fb is None:
            return
        iid = None
        try:
            iid = self._afg_tv_fb.identify_row(e.y)
        except Exception:
            iid = None
        if iid:
            try:
                self._afg_tv_fb.selection_set(iid)
                self._afg_tv_fb.focus(iid)
            except Exception:
                pass

        if self._afg_fb_ctx_menu is None:
            m = tk.Menu(self, tearoff=0)
            m.add_command(label="Add", command=self._afg_fb_add)
            m.add_command(label="Insert", command=self._afg_fb_insert)
            m.add_command(label="Edit", command=self._afg_fb_edit)
            m.add_separator()
            m.add_command(label="Copy", command=self._afg_fb_copy)
            m.add_command(label="Cut", command=self._afg_fb_cut)
            m.add_command(label="Paste", command=self._afg_fb_paste)
            m.add_separator()
            m.add_command(label="Delete", command=self._afg_fb_delete)
            m.add_separator()
            m.add_command(label="Up", command=self._afg_fb_up)
            m.add_command(label="Down", command=self._afg_fb_down)
            self._afg_fb_ctx_menu = m

        try:
            self._afg_fb_ctx_menu.tk_popup(e.x_root, e.y_root)
        finally:
            try:
                self._afg_fb_ctx_menu.grab_release()
            except Exception:
                pass

    def _normalize_afg_pin_ids_and_arrows(self) -> None:
        root = self._afg_root
        if root is None:
            return

        def parse_int(s: str | None) -> int | None:
            try:
                if s is None:
                    return None
                s2 = str(s).strip()
                if not s2:
                    return None
                return int(s2)
            except Exception:
                return None

        pin_elems: list[ET.Element] = []

        fb_items_el = self._afg_get_child(root, "fbItems")
        if fb_items_el is not None:
            for fb in [x for x in list(fb_items_el) if isinstance(x.tag, str) and _local_name(x.tag) == "fbItem"]:
                inputs_el = None
                outputs_el = None
                for ch in list(fb):
                    if not isinstance(ch.tag, str):
                        continue
                    ln = _local_name(ch.tag)
                    if ln == "Inputs":
                        inputs_el = ch
                    elif ln == "Outputs":
                        outputs_el = ch

                if inputs_el is not None:
                    for it in list(inputs_el):
                        if isinstance(it.tag, str) and _local_name(it.tag) == "Input":
                            pin_elems.append(it)
                if outputs_el is not None:
                    for it in list(outputs_el):
                        if isinstance(it.tag, str) and _local_name(it.tag) == "Output":
                            pin_elems.append(it)

        in_items_el = self._afg_get_child(root, "afgInItems")
        if in_items_el is not None:
            for it in list(in_items_el):
                if isinstance(it.tag, str) and _local_name(it.tag) == "afgInItem":
                    pin_elems.append(it)

        out_items_el = self._afg_get_child(root, "afgOutItems")
        if out_items_el is not None:
            for it in list(out_items_el):
                if isinstance(it.tag, str) and _local_name(it.tag) == "afgOutItem":
                    pin_elems.append(it)

        mapping: dict[int, int] = {}
        new_id = 1
        for el in pin_elems:
            old_id = parse_int(el.attrib.get("pinID"))
            el.attrib["pinID"] = str(new_id)
            if old_id is not None and old_id not in mapping:
                mapping[old_id] = new_id
            new_id += 1

        root.attrib["maxPinID"] = str(max(0, new_id - 1))

        arrows_el = self._afg_get_child(root, "arrows")
        if arrows_el is None:
            return

        to_remove: list[ET.Element] = []
        for ar in list(arrows_el):
            if not (isinstance(ar.tag, str) and _local_name(ar.tag) == "arrowItem"):
                continue
            sp_old = parse_int(ar.attrib.get("startPinID"))
            ep_old = parse_int(ar.attrib.get("endPinID"))
            if sp_old is None or ep_old is None:
                to_remove.append(ar)
                continue
            if sp_old not in mapping or ep_old not in mapping:
                to_remove.append(ar)
                continue
            ar.attrib["startPinID"] = str(mapping[sp_old])
            ar.attrib["endPinID"] = str(mapping[ep_old])

        for ar in to_remove:
            try:
                arrows_el.remove(ar)
            except Exception:
                pass

    def _open_afg(self) -> None:
        base_dir = self._applicationgroup_dir()
        initialdir = base_dir if base_dir.exists() else self.workspace_root
        target = filedialog.askopenfilename(
            parent=self,
            title="Open AFG file",
            initialdir=os.fspath(initialdir),
            filetypes=[("XML", "*.xml"), ("All", "*")],
        )
        if not target:
            return
        self._open_afg_from_path(Path(target))

    def _open_afg_from_search(self) -> None:
        if self.var_afg_selected is None:
            return
        rel = (self.var_afg_selected.get() or "").strip()
        if not rel:
            return
        base_dir = self._applicationgroup_dir()
        target = base_dir / rel
        if not target.exists():
            messagebox.showerror("Missing", f"File not found:\n\n{os.fspath(target)}", parent=self)
            return
        self._open_afg_from_path(target)

    def _open_afg_from_path(self, path: Path) -> None:
        path = Path(path)
        if self._afg_tv_fb is None:
            return

        try:
            tree = ET.parse(path)
            root = tree.getroot()
        except Exception as e:
            messagebox.showerror("Open failed", str(e), parent=self)
            return

        # Basic validation
        if not (isinstance(root.tag, str) and _local_name(root.tag) == "AfgDiagramXml"):
            messagebox.showerror("Invalid", "Root element is not <AfgDiagramXml>", parent=self)
            return

        self._afg_file_path = path
        self._afg_root = root
        # Do not eagerly scan/parse LN instance files here.
        # Some workspaces have very large lndm folders and glob+parse can block
        # the Tk main thread long enough that users think the app is hung.
        # doRef dropdown suggestions are loaded lazily on first edit.
        self._afg_reset_ln_suggestions()
        try:
            self._afg_undo_stack = []
        except Exception:
            pass
        try:
            name = (root.attrib.get("name") or "").strip()
            if name:
                # Fast-path: only check the common preferred naming.
                preferred = self._lndm_dir() / f"{name}GAPC.xml"
                if preferred.exists():
                    self._afg_ln_instance_path = preferred
        except Exception:
            pass
        self._refresh_afg_views(select_first_fb=True)

        self._mark_afg_saved()

        # Update search combobox selection if file is under base dir.
        try:
            base_dir = self._applicationgroup_dir()
            rel = os.fspath(path.relative_to(base_dir))
        except Exception:
            rel = os.fspath(path.name)
        self._refresh_afg_search_list(select_rel=rel)
        if self._afg_ln_instance_path is not None:
            self._set_status(
                f"Opened AFG: {os.fspath(path)}  (LN instance: {os.fspath(self._afg_ln_instance_path.name)})"
            )
        else:
            self._set_status(f"Opened AFG: {os.fspath(path)}")

    # HMI editor/viewer

    def _refresh_hmi_search_list(self, *, select_rel: str | None) -> None:
        if self.cb_hmi is None or self.var_hmi_selected is None or self.lbl_hmi_match is None:
            return
        base_dir = self._hmi_template_dir()
        self._all_hmi_files = self._scan_xml_relpaths(base_dir)

        def apply_filter(*_args) -> None:
            raw = ""
            if self.var_hmi_filter is not None:
                raw = self.var_hmi_filter.get().strip().lower()
            if not raw:
                filtered = list(self._all_hmi_files)
            else:
                tokens = [t for t in raw.split() if t]

                def ok(v: str) -> bool:
                    lv = (v or "").lower()
                    return all(t in lv for t in tokens)

                filtered = [v for v in self._all_hmi_files if ok(v)]

            cur = (self.var_hmi_selected.get() or "").strip()
            if cur and cur not in filtered:
                filtered = [cur] + filtered

            max_show = 1200
            shown = filtered[:max_show]
            self.cb_hmi["values"] = shown
            suffix = "" if len(filtered) <= max_show else f" (showing first {max_show})"
            self.lbl_hmi_match.configure(text=f"{len(filtered)} match{'' if len(filtered)==1 else 'es'}{suffix}")

        if getattr(self, "_hmi_apply_filter", None) is None:
            if self.var_hmi_filter is not None:
                self.var_hmi_filter.trace_add("write", apply_filter)
            setattr(self, "_hmi_apply_filter", apply_filter)
        else:
            apply_filter = getattr(self, "_hmi_apply_filter")

        if select_rel:
            try:
                self.var_hmi_selected.set(select_rel)
            except Exception:
                pass
        apply_filter()

    def _new_hmi(self) -> None:
        base_dir = self._hmi_template_dir()
        base_dir.mkdir(parents=True, exist_ok=True)
        stem = simpledialog.askstring("New HMI", "HMI file name (without .xml)", parent=self)
        if not stem:
            return
        safe = re.sub(r'[<>:"/\\|?*]', "_", (stem or "").strip()) or "HMI"
        target_path = base_dir / f"{safe}.xml"
        if target_path.exists():
            ok = messagebox.askyesno(
                "Overwrite?",
                f"File already exists:\n\n{os.fspath(target_path)}\n\nOverwrite?",
                parent=self,
            )
            if not ok:
                return

        root = ET.Element(_q(HMI_CUST_NS, "PowerLogicHmiCustomization"))
        root.attrib["version"] = "1.0"
        self._hmi_root = root
        self._hmi_file_path = target_path
        self._refresh_hmi_views(select_first_menu=False)
        self._mark_hmi_unsaved()
        try:
            rel = os.fspath(target_path.relative_to(base_dir))
        except Exception:
            rel = os.fspath(target_path.name)
        self._refresh_hmi_search_list(select_rel=rel)
        self._set_status(f"Created HMI: {os.fspath(target_path)}")

    def _open_hmi(self) -> None:
        base_dir = self._hmi_template_dir()
        initialdir = base_dir if base_dir.exists() else self.workspace_root
        target = filedialog.askopenfilename(
            parent=self,
            title="Open HMI file",
            initialdir=os.fspath(initialdir),
            filetypes=[("XML", "*.xml"), ("All", "*")],
        )
        if not target:
            return
        self._open_hmi_from_path(Path(target))

    def _open_hmi_from_search(self) -> None:
        if self.var_hmi_selected is None:
            return
        rel = (self.var_hmi_selected.get() or "").strip()
        if not rel:
            return
        base_dir = self._hmi_template_dir()
        target = base_dir / rel
        if not target.exists():
            messagebox.showerror("Missing", f"File not found:\n\n{os.fspath(target)}", parent=self)
            return
        self._open_hmi_from_path(target)

    def _open_hmi_from_path(self, path: Path) -> None:
        path = Path(path)
        if self._hmi_tv_menus is None:
            return
        try:
            tree = ET.parse(path)
            root = tree.getroot()
        except Exception as e:
            messagebox.showerror("Open failed", str(e), parent=self)
            return

        if not (isinstance(root.tag, str) and _local_name(root.tag) == "PowerLogicHmiCustomization"):
            messagebox.showerror("Invalid", "Root element is not <PowerLogicHmiCustomization>", parent=self)
            return

        self._hmi_file_path = path
        self._hmi_root = root
        self._refresh_hmi_views(select_first_menu=True)
        self._mark_hmi_saved()

        try:
            base_dir = self._hmi_template_dir()
            rel = os.fspath(path.relative_to(base_dir))
        except Exception:
            rel = os.fspath(path.name)
        self._refresh_hmi_search_list(select_rel=rel)
        self._set_status(f"Opened HMI: {os.fspath(path)}")

    def _write_hmi_xml(self, path: Path) -> None:
        if self._hmi_root is None:
            raise ValueError("No HMI loaded")
        try:
            ET.register_namespace("", HMI_CUST_NS)
        except Exception:
            pass
        root = self._hmi_root
        try:
            ET.indent(root, space="    ")
        except Exception:
            pass
        body = ET.tostring(root, encoding="unicode", short_empty_elements=True)
        text = "<?xml version=\"1.0\" encoding=\"utf-8\" ?>\n" + body.rstrip() + "\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\r\n") as f:
            f.write(text)

    def _save_hmi(self) -> None:
        if self._hmi_root is None or self._hmi_file_path is None:
            messagebox.showerror("Missing", "No HMI loaded", parent=self)
            return
        try:
            self._write_hmi_xml(self._hmi_file_path)
        except Exception as e:
            messagebox.showerror("Save failed", str(e), parent=self)
            return
        self._set_status(f"Saved HMI: {os.fspath(self._hmi_file_path)}")
        self._mark_hmi_saved()

    def _save_hmi_as(self) -> None:
        if self._hmi_root is None:
            messagebox.showerror("Missing", "No HMI loaded", parent=self)
            return
        base_dir = self._hmi_template_dir()
        base_dir.mkdir(parents=True, exist_ok=True)
        initialfile = ""
        try:
            if self._hmi_file_path is not None:
                initialfile = self._hmi_file_path.name
        except Exception:
            initialfile = ""
        target = filedialog.asksaveasfilename(
            parent=self,
            title="Save HMI As",
            initialdir=os.fspath(base_dir),
            initialfile=initialfile,
            defaultextension=".xml",
            filetypes=[("XML", "*.xml"), ("All", "*")],
        )
        if not target:
            return
        target_path = Path(target)
        self._hmi_file_path = target_path
        self._save_hmi()
        try:
            rel = os.fspath(target_path.relative_to(base_dir))
        except Exception:
            rel = os.fspath(target_path.name)
        self._refresh_hmi_search_list(select_rel=rel)

    def _hmi_all_menus(self) -> list[ET.Element]:
        root = self._hmi_root
        if root is None:
            return []
        menus: list[ET.Element] = []
        for el in root.iter():
            if not (isinstance(el.tag, str) and _local_name(el.tag) == "HMIMenu"):
                continue
            name = (el.attrib.get("name") or "").strip()
            if not name.startswith("Menu_"):
                continue
            menus.append(el)
        menus.sort(key=lambda m: (m.attrib.get("name") or "").lower())
        return menus

    def _hmi_selected_menu(self) -> tuple[ET.Element, ET.Element] | None:
        if self._hmi_tv_menus is None:
            return None
        sel = self._hmi_tv_menus.selection()
        if not sel:
            return None
        return self._hmi_menu_iid_to_el.get(sel[0])

    def _hmi_selected_item(self) -> tuple[ET.Element, ET.Element] | None:
        if self._hmi_tv_items is None:
            return None
        sel = self._hmi_tv_items.selection()
        if not sel:
            return None
        return self._hmi_item_iid_to_el.get(sel[0])

    def _hmi_selected_data(self) -> tuple[ET.Element, ET.Element] | None:
        if self._hmi_tv_data is None:
            return None
        sel = self._hmi_tv_data.selection()
        if not sel:
            return None
        return self._hmi_data_iid_to_el.get(sel[0])

    def _refresh_hmi_views(self, *, select_first_menu: bool) -> None:
        self._refresh_hmi_menu_table(select_first=select_first_menu)
        self._refresh_hmi_item_table(select_first=False)
        self._refresh_hmi_data_table(select_first=False)
        try:
            self._update_dirty_ui_hmi()
        except Exception:
            pass

    def _refresh_hmi_menu_table(self, *, select_first: bool) -> None:
        tv = self._hmi_tv_menus
        if tv is None:
            return
        self._hmi_menu_iid_to_el.clear()
        try:
            for iid in tv.get_children(""):
                tv.delete(iid)
        except Exception:
            pass

        root = self._hmi_root
        if root is None:
            return

        for idx, menu in enumerate(self._hmi_all_menus()):
            name = menu.attrib.get("name") or ""
            desc = menu.attrib.get("desc") or ""
            dt = menu.attrib.get("hmiMenuDataType") or ""
            vt = menu.attrib.get("hmiMenuViewType") or ""
            iid = f"m{idx}"
            try:
                tv.insert("", "end", iid=iid, values=(name, desc, dt, vt))
            except Exception:
                continue
            self._hmi_menu_iid_to_el[iid] = (root, menu)

        if select_first:
            try:
                kids = tv.get_children("")
                if kids:
                    tv.selection_set(kids[0])
            except Exception:
                pass

    def _refresh_hmi_item_table(self, *, select_first: bool) -> None:
        tv = self._hmi_tv_items
        if tv is None:
            return
        self._hmi_item_iid_to_el.clear()
        try:
            for iid in tv.get_children(""):
                tv.delete(iid)
        except Exception:
            pass

        sel = self._hmi_selected_menu()
        if sel is None:
            return
        parent, menu = sel

        idx = 0
        for ch in list(menu):
            if not (isinstance(ch.tag, str) and _local_name(ch.tag) == "HMIMenuItem"):
                continue
            ref = (ch.attrib.get("ref") or "").strip()
            if ref:
                kind = "ref"
                name = ""
                do_ref = ""
                da_ref = ""
            else:
                kind = "item"
                name = ch.attrib.get("name") or ""
                do_ref = ch.attrib.get("doRef") or ""
                da_ref = ch.attrib.get("daRef") or ""
            iid = f"i{idx}"
            idx += 1
            try:
                tv.insert("", "end", iid=iid, values=(kind, name, ref, do_ref, da_ref))
            except Exception:
                continue
            self._hmi_item_iid_to_el[iid] = (menu, ch)

        if select_first:
            try:
                kids = tv.get_children("")
                if kids:
                    tv.selection_set(kids[0])
            except Exception:
                pass

    def _refresh_hmi_data_table(self, *, select_first: bool) -> None:
        tv = self._hmi_tv_data
        if tv is None:
            return
        self._hmi_data_iid_to_el.clear()
        try:
            for iid in tv.get_children(""):
                tv.delete(iid)
        except Exception:
            pass

        sel = self._hmi_selected_item()
        if sel is None:
            return
        parent, item = sel

        idx = 0
        for ch in list(item):
            if not (isinstance(ch.tag, str) and _local_name(ch.tag) == "HMIDataItem"):
                continue
            name = ch.attrib.get("name") or ""
            do_ref = ch.attrib.get("doRef") or ""
            da_ref = ch.attrib.get("daRef") or ""
            iid = f"d{idx}"
            idx += 1
            try:
                tv.insert("", "end", iid=iid, values=(name, do_ref, da_ref))
            except Exception:
                continue
            self._hmi_data_iid_to_el[iid] = (item, ch)

        if select_first:
            try:
                kids = tv.get_children("")
                if kids:
                    tv.selection_set(kids[0])
            except Exception:
                pass

    def _hmi_on_menu_selected(self) -> None:
        self._refresh_hmi_item_table(select_first=False)
        self._refresh_hmi_data_table(select_first=False)

    def _hmi_on_item_selected(self) -> None:
        self._refresh_hmi_data_table(select_first=False)

    def _hmi_menu_add(self) -> None:
        if self._hmi_root is None:
            messagebox.showerror("Missing", "No HMI loaded", parent=self)
            return
        dlg = _HmiMenuEditDialog(self, title="Add HMIMenu", name="Menu_", desc="", data_type="", view_type="")
        res = dlg.show()
        if not res:
            return
        menu = ET.SubElement(self._hmi_root, _q(HMI_CUST_NS, "HMIMenu"))
        for k, v in res.items():
            if v:
                menu.attrib[k] = v
        self._refresh_hmi_views(select_first_menu=True)
        self._mark_hmi_unsaved()

    def _hmi_menu_edit(self) -> None:
        sel = self._hmi_selected_menu()
        if sel is None:
            return
        parent, menu = sel
        dlg = _HmiMenuEditDialog(
            self,
            title="Edit HMIMenu",
            name=menu.attrib.get("name") or "",
            desc=menu.attrib.get("desc") or "",
            data_type=menu.attrib.get("hmiMenuDataType") or "",
            view_type=menu.attrib.get("hmiMenuViewType") or "",
        )
        res = dlg.show()
        if not res:
            return
        for k in ("name", "desc", "hmiMenuDataType", "hmiMenuViewType"):
            v = (res.get(k) or "").strip() if k != "desc" else (res.get(k) or "")
            if v:
                menu.attrib[k] = v
            else:
                menu.attrib.pop(k, None)
        self._refresh_hmi_views(select_first_menu=False)
        self._mark_hmi_unsaved()

    def _hmi_menu_delete(self) -> None:
        sel = self._hmi_selected_menu()
        if sel is None:
            return
        parent, menu = sel
        name = (menu.attrib.get("name") or "").strip()
        ok = messagebox.askyesno("Delete?", f"Delete menu {name or '(unnamed)'}?", parent=self)
        if not ok:
            return
        try:
            parent.remove(menu)
        except Exception:
            return
        self._refresh_hmi_views(select_first_menu=True)
        self._mark_hmi_unsaved()

    def _hmi_item_add(self) -> None:
        sel_menu = self._hmi_selected_menu()
        if sel_menu is None:
            messagebox.showerror("Missing", "Select a menu", parent=self)
            return
        _parent, menu = sel_menu
        dlg = _HmiMenuItemEditDialog(self, title="Add HMIMenuItem", name="", ref="", do_ref="", da_ref="")
        res = dlg.show()
        if not res:
            return
        item = ET.SubElement(menu, _q(HMI_CUST_NS, "HMIMenuItem"))
        if (res.get("ref") or "").strip():
            item.attrib["ref"] = (res.get("ref") or "").strip()
        else:
            item.attrib["name"] = (res.get("name") or "").strip()
            if (res.get("doRef") or "").strip():
                item.attrib["doRef"] = (res.get("doRef") or "").strip()
            if (res.get("daRef") or "").strip():
                item.attrib["daRef"] = (res.get("daRef") or "").strip()
        self._refresh_hmi_item_table(select_first=False)
        self._mark_hmi_unsaved()

    def _hmi_item_edit(self) -> None:
        sel = self._hmi_selected_item()
        if sel is None:
            return
        parent, item = sel
        dlg = _HmiMenuItemEditDialog(
            self,
            title="Edit HMIMenuItem",
            name=item.attrib.get("name") or "",
            ref=item.attrib.get("ref") or "",
            do_ref=item.attrib.get("doRef") or "",
            da_ref=item.attrib.get("daRef") or "",
        )
        res = dlg.show()
        if not res:
            return
        ref = (res.get("ref") or "").strip()
        for k in ("ref", "name", "doRef", "daRef"):
            item.attrib.pop(k, None)
        if ref:
            item.attrib["ref"] = ref
        else:
            item.attrib["name"] = (res.get("name") or "").strip()
            do_ref = (res.get("doRef") or "").strip()
            da_ref = (res.get("daRef") or "").strip()
            if do_ref:
                item.attrib["doRef"] = do_ref
            if da_ref:
                item.attrib["daRef"] = da_ref
        self._refresh_hmi_item_table(select_first=False)
        self._refresh_hmi_data_table(select_first=False)
        self._mark_hmi_unsaved()

    def _hmi_item_delete(self) -> None:
        sel = self._hmi_selected_item()
        if sel is None:
            return
        parent, item = sel
        label = (item.attrib.get("name") or item.attrib.get("ref") or "").strip()
        ok = messagebox.askyesno("Delete?", f"Delete item {label or '(unnamed)'}?", parent=self)
        if not ok:
            return
        try:
            parent.remove(item)
        except Exception:
            return
        self._refresh_hmi_item_table(select_first=False)
        self._refresh_hmi_data_table(select_first=False)
        self._mark_hmi_unsaved()

    def _hmi_data_add(self) -> None:
        sel_item = self._hmi_selected_item()
        if sel_item is None:
            messagebox.showerror("Missing", "Select a menu item", parent=self)
            return
        _parent, item = sel_item
        dlg = _HmiDataItemEditDialog(self, title="Add HMIDataItem", name="", do_ref="", da_ref="")
        res = dlg.show()
        if not res:
            return
        di = ET.SubElement(item, _q(HMI_CUST_NS, "HMIDataItem"))
        di.attrib["name"] = (res.get("name") or "").strip()
        if (res.get("doRef") or "").strip():
            di.attrib["doRef"] = (res.get("doRef") or "").strip()
        if (res.get("daRef") or "").strip():
            di.attrib["daRef"] = (res.get("daRef") or "").strip()
        self._refresh_hmi_data_table(select_first=False)
        self._mark_hmi_unsaved()

    def _hmi_data_edit(self) -> None:
        sel = self._hmi_selected_data()
        if sel is None:
            return
        parent, di = sel
        dlg = _HmiDataItemEditDialog(
            self,
            title="Edit HMIDataItem",
            name=di.attrib.get("name") or "",
            do_ref=di.attrib.get("doRef") or "",
            da_ref=di.attrib.get("daRef") or "",
        )
        res = dlg.show()
        if not res:
            return
        di.attrib["name"] = (res.get("name") or "").strip()
        do_ref = (res.get("doRef") or "").strip()
        da_ref = (res.get("daRef") or "").strip()
        if do_ref:
            di.attrib["doRef"] = do_ref
        else:
            di.attrib.pop("doRef", None)
        if da_ref:
            di.attrib["daRef"] = da_ref
        else:
            di.attrib.pop("daRef", None)
        self._refresh_hmi_data_table(select_first=False)
        self._mark_hmi_unsaved()

    def _hmi_data_delete(self) -> None:
        sel = self._hmi_selected_data()
        if sel is None:
            return
        parent, di = sel
        name = (di.attrib.get("name") or "").strip()
        ok = messagebox.askyesno("Delete?", f"Delete data item {name or '(unnamed)'}?", parent=self)
        if not ok:
            return
        try:
            parent.remove(di)
        except Exception:
            return
        self._refresh_hmi_data_table(select_first=False)
        self._mark_hmi_unsaved()

    def _hmi_generate_from_application(self) -> None:
        if self._hmi_root is None:
            messagebox.showerror("Missing", "No HMI loaded", parent=self)
            return
        if self._hmi_file_path is None:
            messagebox.showerror("Missing", "Save or open an HMI file first", parent=self)
            return

        app_path = self._application_dir() / f"{self._hmi_file_path.stem}.xml"
        if not app_path.exists():
            messagebox.showerror(
                "Missing",
                f"Matching application file not found:\n\n{os.fspath(app_path)}",
                parent=self,
            )
            return

        try:
            app_root = ET.parse(app_path).getroot()
        except Exception as e:
            messagebox.showerror("Read failed", f"Failed to parse application XML:\n\n{e}", parent=self)
            return

        fun_block: ET.Element | None = None
        for el in app_root.iter():
            if isinstance(el.tag, str) and _local_name(el.tag) == "funBlock":
                fun_block = el
                break
        if fun_block is None:
            messagebox.showerror("Invalid", "No <funBlock> found in application file", parent=self)
            return
        ln_ref = (fun_block.attrib.get("LnRef") or "").strip()
        if not ln_ref:
            messagebox.showerror("Invalid", "funBlock has no LnRef", parent=self)
            return

        outputs: list[tuple[str, str]] = []
        settings: list[str] = []
        for el in list(fun_block):
            if not isinstance(el.tag, str):
                continue
            local = _local_name(el.tag)
            if local == "output":
                name = (el.attrib.get("name") or "").strip()
                do_ref = (el.attrib.get("doRef") or "").strip()
                if name:
                    outputs.append((name, do_ref))
            elif local == "setting":
                name = (el.attrib.get("name") or "").strip()
                if name:
                    settings.append(name)

        # Merge into existing menus.
        menus = self._hmi_all_menus()
        out_menus = [m for m in menus if (m.attrib.get("name") or "").endswith("_Outputs")]
        set_menus = [m for m in menus if (m.attrib.get("name") or "").endswith("_Settings")]
        if not out_menus and not set_menus:
            messagebox.showerror(
                "Missing",
                "No Menu_*_Outputs or Menu_*_Settings menus found in this HMI file.\n\n"
                "Load a template (e.g. AZnDis.xml) or create these menus first.",
                parent=self,
            )
            return

        def merge_outputs(menu: ET.Element) -> int:
            existing_by_name: dict[str, ET.Element] = {}
            for it in list(menu):
                if not (isinstance(it.tag, str) and _local_name(it.tag) == "HMIMenuItem"):
                    continue
                if (it.attrib.get("ref") or "").strip():
                    continue
                nm = (it.attrib.get("name") or "").strip()
                if nm:
                    existing_by_name[nm] = it
            added = 0
            for nm, do_ref_raw in outputs:
                full_do = f"{ln_ref}{do_ref_raw}" if do_ref_raw.startswith(".") else (do_ref_raw or f"{ln_ref}.{nm}")
                if nm in existing_by_name:
                    it = existing_by_name[nm]
                    if not (it.attrib.get("doRef") or "").strip():
                        it.attrib["doRef"] = full_do
                    continue
                it = ET.SubElement(menu, _q(HMI_CUST_NS, "HMIMenuItem"))
                it.attrib["name"] = nm
                it.attrib["doRef"] = full_do
                added += 1
            return added

        def merge_settings(menu: ET.Element) -> int:
            existing_by_name: dict[str, ET.Element] = {}
            for it in list(menu):
                if not (isinstance(it.tag, str) and _local_name(it.tag) == "HMIMenuItem"):
                    continue
                if (it.attrib.get("ref") or "").strip():
                    continue
                nm = (it.attrib.get("name") or "").strip()
                if nm:
                    existing_by_name[nm] = it
            added = 0
            for nm in settings:
                if nm in existing_by_name:
                    it = existing_by_name[nm]
                    if not (it.attrib.get("doRef") or "").strip():
                        it.attrib["doRef"] = f"{ln_ref}.{nm}"
                    continue
                it = ET.SubElement(menu, _q(HMI_CUST_NS, "HMIMenuItem"))
                it.attrib["name"] = nm
                it.attrib["doRef"] = f"{ln_ref}.{nm}"
                added += 1
            return added

        added_out = 0
        for m in out_menus:
            added_out += merge_outputs(m)
        added_set = 0
        for m in set_menus:
            added_set += merge_settings(m)

        self._refresh_hmi_views(select_first_menu=False)
        self._mark_hmi_unsaved()
        self._set_status(
            f"Generated from application {os.fspath(app_path.name)} (LnRef={ln_ref}): +{added_out} outputs, +{added_set} settings"
        )

    def _open_enum_type_from_path(self, path: Path) -> None:
        if self.enum_tab is None:
            return
        try:
            self.enum_tab.open_enum_type_from_path(Path(path))
        except Exception:
            pass

    def _save_enum_type(self) -> None:
        if self.enum_tab is None:
            return
        try:
            self.enum_tab.save_enum_type()
        except Exception:
            pass

    def _save_enum_type_as(self) -> None:
        if self.enum_tab is None:
            return
        try:
            self.enum_tab.save_enum_type_as()
        except Exception:
            pass

    def _apply_enum_ui_to_xml(self) -> None:
        if self._enum_table is None or self._enum_id is None:
            return
        if self._enum_root is None or self._enum_enumtype is None:
            self._new_enum_type()
        if self._enum_root is None or self._enum_enumtype is None:
            return

        root = self._enum_root
        enum_el = self._enum_enumtype

        ns = ""
        if isinstance(root.tag, str) and root.tag.startswith("{"):
            ns = root.tag.split("}", 1)[0][1:]
        ns = ns or SCL_NS

        enum_el.attrib["id"] = (self._enum_id.get() or "").strip()

        # Rebuild children: preserved-before, LangRef privates, EnumVal, preserved-after.
        for ch in list(enum_el):
            enum_el.remove(ch)

        for el in (self._enum_preserved_before or []):
            enum_el.append(_deepcopy_et_element(el))

        rows = self._enum_table.get_rows()
        for r in rows:
            p = ET.Element(_q(ns, "Private"))
            p.attrib["type"] = self._enum_langref_private_type()
            txt = (r.get("langRef") or "").strip()
            # Keep one Private per EnumVal to preserve positional mapping.
            p.text = txt
            enum_el.append(p)

        for r in rows:
            ev = ET.Element(_q(ns, "EnumVal"))
            o = (r.get("ord") or "").strip()
            if o:
                ev.attrib["ord"] = o
            d = r.get("desc") or ""
            if (d or "").strip() or "desc" in ev.attrib:
                if (d or "") != "":
                    ev.attrib["desc"] = d
            v = r.get("val") or ""
            ev.text = v
            enum_el.append(ev)

        for el in (self._enum_preserved_after or []):
            enum_el.append(_deepcopy_et_element(el))

    def _write_enum_type_xml(self, path: Path) -> None:
        if self._enum_root is None or self._enum_enumtype is None:
            raise ValueError("No EnumType loaded")

        root = self._enum_root

        ns = ""
        if isinstance(root.tag, str) and root.tag.startswith("{"):
            ns = root.tag.split("}", 1)[0][1:]

        schema_ns = "http://www.w3.org/2001/XMLSchema"
        xsi_ns = "http://www.w3.org/2001/XMLSchema-instance"
        root.attrib[_q(xsi_ns, "schemaLocation")] = f"{SCL_NS} SCL.xsd"

        ET.register_namespace("", ns or SCL_NS)
        ET.register_namespace("xsi", xsi_ns)
        ET.register_namespace("xsd", schema_ns)
        try:
            ET.indent(root, space="    ")
        except Exception:
            pass

        body = ET.tostring(root, encoding="unicode", short_empty_elements=True)
        open_end = body.find(">")
        if open_end != -1:
            required_open = (
                f'<SCL xmlns:xsd="{schema_ns}" '
                f'xmlns="{SCL_NS}" '
                f'xmlns:xsi="{xsi_ns}" '
                f'xsi:schemaLocation="{SCL_NS} SCL.xsd">'
            )
            body = required_open + body[open_end + 1 :]
        body = re.sub(r"</[^>]*:?SCL\s*>", "</SCL>", body)

        text = "<?xml version=\"1.0\" encoding=\"utf-8\" ?>\n" + body.rstrip() + "\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\r\n") as f:
            f.write(text)

    def _on_enum_details_tab_changed(self) -> None:
        # Refresh the language ref page when selected.
        try:
            if self._enum_details_nb is None:
                return
            idx = self._enum_details_nb.index("current")
        except Exception:
            return
        if idx == 1:
            try:
                self._refresh_enum_language_reference()
            except Exception:
                pass

    def _refresh_enum_language_reference(self) -> None:
        if self._enum_lang_tree is None or self._enum_table is None:
            return

        # Ensure tags exist for state coloring.
        try:
            self._enum_lang_tree.tag_configure("added", background="honeydew2")
            self._enum_lang_tree.tag_configure("removed", background="misty rose")
            self._enum_lang_tree.tag_configure("changed", background="lemon chiffon")
        except Exception:
            pass

        # Use table rows (not get_rows) so we can show soft-deleted items in red.
        rows = list(self._enum_table.rows or [])
        view_rows: list[dict[str, str]] = []
        for i, r in enumerate(rows):
            ord0 = (r.get("ord") or "").strip()
            val0 = (r.get("val") or "").strip()
            name = f"{ord0}: {val0}" if ord0 or val0 else f"#{i}"
            view_rows.append(
                {
                    # idx points into EnumValTable.rows (NOT get_rows)
                    "idx": str(i),
                    "name": name,
                    "id": (r.get("langRef") or "").strip(),
                    "desc": (r.get("desc") or ""),
                }
            )

        self._enum_lang_rows_all = view_rows
        self._apply_enum_lang_filter()

    def _clear_enum_lang_filter(self) -> None:
        if self.var_enum_lang_filter is None:
            return
        try:
            self.var_enum_lang_filter.set("")
        except Exception:
            pass

    def _apply_enum_lang_filter(self) -> None:
        if self._enum_lang_tree is None or self.lbl_enum_lang_match is None:
            return
        raw = ""
        if self.var_enum_lang_filter is not None:
            raw = (self.var_enum_lang_filter.get() or "").strip().lower()
        if not raw:
            filtered = list(self._enum_lang_rows_all)
        else:
            tokens = [t for t in raw.split() if t]

            def ok(row: dict[str, str]) -> bool:
                hay = " ".join([row.get("name", ""), row.get("id", ""), row.get("desc", "")]).lower()
                return all(t in hay for t in tokens)

            filtered = [r for r in self._enum_lang_rows_all if ok(r)]

        self._enum_lang_rows_filtered = filtered

        for item in self._enum_lang_tree.get_children(""):
            self._enum_lang_tree.delete(item)
        for i, row in enumerate(filtered):
            # Apply consistent row-state coloring driven by the EnumValTable.
            tags: tuple[str, ...] = ()
            try:
                if self._enum_table is not None:
                    src_idx = int(row.get("idx") or "-1")
                    if 0 <= src_idx < len(self._enum_table.rows):
                        src = self._enum_table.rows[src_idx]
                        if bool(src.get("__ui_deleted")):
                            tags = ("removed",)
                        elif bool(src.get("__ui_added")):
                            tags = ("added",)
                        else:
                            try:
                                changed = bool(self._enum_table._row_is_changed_at_index(src_idx))
                            except Exception:
                                changed = False
                            tags = ("changed",) if changed else ()
            except Exception:
                tags = ()

            self._enum_lang_tree.insert(
                "",
                "end",
                iid=str(i),
                values=[row.get("name", ""), row.get("id", ""), row.get("desc", "")],
                tags=tags,
            )

        self.lbl_enum_lang_match.configure(text=f"{len(filtered)} match{'' if len(filtered)==1 else 'es'}")

    def _on_enum_lang_left_click(self, _event: tk.Event) -> None:
        try:
            self._end_enum_lang_inline(commit=True)
        except Exception:
            pass

    def _on_enum_lang_double_click(self, event: tk.Event) -> None:
        if self._enum_lang_tree is None:
            return
        try:
            iid = self._enum_lang_tree.identify_row(event.y)
            col = self._enum_lang_tree.identify_column(event.x)
        except Exception:
            return
        if not iid or col != "#2":
            return
        self._start_enum_lang_inline(iid)

    def _start_enum_lang_inline(self, iid: str) -> None:
        if self._enum_lang_tree is None:
            return
        try:
            bbox = self._enum_lang_tree.bbox(iid, "id")
        except Exception:
            bbox = None
        if not bbox:
            return
        x, y, w, h = bbox

        try:
            idx = int(iid)
        except Exception:
            return
        if idx < 0 or idx >= len(self._enum_lang_rows_filtered):
            return
        cur = self._enum_lang_rows_filtered[idx].get("id", "")

        ent = ttk.Entry(self._enum_lang_tree)
        ent.insert(0, cur)
        ent.place(x=x, y=y, width=w, height=h)
        self._enum_lang_inline = ent
        self._enum_lang_inline_iid = iid

        ent.focus_set()
        ent.select_range(0, tk.END)
        ent.bind("<Return>", lambda _e: self._end_enum_lang_inline(commit=True))
        ent.bind("<Escape>", lambda _e: self._end_enum_lang_inline(commit=False))
        ent.bind("<FocusOut>", lambda _e: self._end_enum_lang_inline(commit=True))
        ent.bind("<Control-z>", lambda _e: (self._enum_lang_undo(), "break")[1])
        ent.bind("<Control-Z>", lambda _e: (self._enum_lang_undo(), "break")[1])

    def _end_enum_lang_inline(self, *, commit: bool) -> None:
        if self._enum_lang_inline is None:
            return
        ent = self._enum_lang_inline
        iid = self._enum_lang_inline_iid

        try:
            value = (ent.get() or "").strip()
        except Exception:
            value = ""

        try:
            ent.destroy()
        except Exception:
            pass
        self._enum_lang_inline = None
        self._enum_lang_inline_iid = None

        if not commit or iid is None:
            return
        try:
            view_idx = int(iid)
        except Exception:
            return
        if view_idx < 0 or view_idx >= len(self._enum_lang_rows_filtered):
            return
        src_row = self._enum_lang_rows_filtered[view_idx]
        src_idx = int(src_row.get("idx") or "-1")
        if self._enum_table is None:
            return
        if src_idx < 0 or src_idx >= len(self._enum_table.rows):
            return
        # Do not allow editing soft-deleted rows.
        try:
            if bool(self._enum_table.rows[src_idx].get("__ui_deleted")):
                return
        except Exception:
            pass

        try:
            self._enum_table._end_inline(commit=True)
        except Exception:
            pass
        try:
            self._enum_table._push_undo()
        except Exception:
            pass
        try:
            self._enum_table.rows[src_idx]["langRef"] = value
        except Exception:
            return
        try:
            self._enum_table.refresh()
        except Exception:
            pass
        try:
            self._refresh_enum_language_reference()
        except Exception:
            pass

        try:
            self._on_enum_view_changed()
        except Exception:
            pass

    def _enum_lang_undo(self) -> None:
        if self._enum_table is None:
            return
        try:
            self._end_enum_lang_inline(commit=False)
        except Exception:
            pass
        try:
            self._enum_table.undo()
        except Exception:
            return
        try:
            self._refresh_enum_language_reference()
        except Exception:
            pass

    def _do_type_list_path(self) -> Path:
        return self._do_type_dir() / "DoTypeList.xml"

    def _ensure_do_type_in_list(self, do_type_id: str) -> None:
        do_type_id = (do_type_id or "").strip()
        if not do_type_id:
            return

        path = self._do_type_list_path()
        if not path.exists():
            raise FileNotFoundError(f"DoTypeList.xml not found: {os.fspath(path)}")

        # Keep formatting intact by doing a minimal textual insertion.
        text = path.read_text(encoding="utf-8", errors="ignore")
        newline = "\r\n" if "\r\n" in text else "\n"

        # 1) Robust de-dup: try XML parse first (handles whitespace/order/quotes).
        try:
            root = ET.fromstring(text)
            for el in root.iter():
                if not isinstance(el.tag, str) or _local_name(el.tag) != "Type":
                    continue
                if (el.attrib.get("ref") or "").strip() == do_type_id:
                    return
        except Exception:
            pass

        # 2) Fallback de-dup: tolerant regex (handles spaces + both quote types).
        try:
            pat = rf"<Type\b[^>]*\bref\s*=\s*(['\"])\s*{re.escape(do_type_id)}\s*\1"
            if re.search(pat, text, flags=re.IGNORECASE):
                return
        except Exception:
            pass

        # Compute next id as max(existing ids) + 1.
        max_id = 0
        try:
            root = ET.fromstring(text)
            for el in root.iter():
                if not isinstance(el.tag, str) or _local_name(el.tag) != "Type":
                    continue
                raw = (el.attrib.get("id") or "").strip()
                if raw.isdigit():
                    max_id = max(max_id, int(raw))
        except Exception:
            for m in re.finditer(r"<Type\b[^>]*\bid\s*=\s*(['\"])(\d+)\1", text, flags=re.IGNORECASE):
                try:
                    max_id = max(max_id, int(m.group(2)))
                except Exception:
                    pass
        next_id = max_id + 1

        # Preserve indentation style (copy from first <Type ...> line if present).
        indent = "    "
        try:
            m_indent = re.search(r"^[ \t]*<Type\b", text, flags=re.IGNORECASE | re.MULTILINE)
            if m_indent is not None:
                indent = re.match(r"^[ \t]*", m_indent.group(0)).group(0)  # type: ignore[union-attr]
        except Exception:
            indent = "    "

        # Find closing </LIST> (case-insensitive) and insert right before it.
        close_iter = list(re.finditer(r"</\s*LIST\s*>", text, flags=re.IGNORECASE))
        if not close_iter:
            raise ValueError(f"Invalid DoTypeList.xml (missing </LIST>): {os.fspath(path)}")
        idx = close_iter[-1].start()

        insert = f"{indent}<Type id=\"{next_id}\" ref=\"{do_type_id}\" />{newline}"
        prefix = text[:idx]
        if prefix and not prefix.endswith(("\n", "\r")):
            insert = newline + insert

        new_text = text[:idx] + insert + text[idx:]
        path.write_text(new_text, encoding="utf-8")

    def _scan_do_cdc_types(self) -> list[str]:
        do_dir = self._do_type_dir()
        if not do_dir.exists():
            return []
        types: set[str] = set()
        for rel in self._scan_xml_relpaths(do_dir):
            p = do_dir / rel
            try:
                root = ET.parse(p).getroot()
            except Exception:
                continue
            for el in root.iter():
                if not isinstance(el.tag, str):
                    continue
                if _local_name(el.tag) != "DOType":
                    continue
                cdc = (el.attrib.get("cdc") or "").strip()
                if cdc:
                    types.add(cdc.strip().upper())
                break
        return sorted(types, key=lambda s: s.lower())

    def _get_do_cdc_types(self) -> list[str]:
        cache = getattr(self, "_do_cdc_types_cache", None)
        if cache is None:
            cache = self._scan_do_cdc_types()
            setattr(self, "_do_cdc_types_cache", list(cache))
        return list(cache)

    def _do_type_cdc_for_id(self, do_type_id: str) -> str:
        do_type_id = (do_type_id or "").strip()
        if not do_type_id:
            return ""
        do_dir = self._do_type_dir()
        path = self._find_type_file(kind_dir=do_dir, type_id=do_type_id)
        if path is None:
            return ""
        try:
            root = ET.parse(path).getroot()
        except Exception:
            return ""
        for el in root.iter():
            if isinstance(el.tag, str) and _local_name(el.tag) == "DOType":
                return (el.attrib.get("cdc") or "").strip().upper()
        return ""

    def _new_do_template_dialog(self) -> None:
        if self.do_template_tab is None:
            return
        try:
            self.do_template_tab.new_do_template_dialog()
        except Exception:
            pass

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

    def _refresh_do_template_search_list(self, *, select_rel: str | None) -> None:
        if self.cb_do_tmpl is None or self.var_do_tmpl_selected is None or self.lbl_do_tmpl_match is None:
            return
        do_dir = self._do_type_dir()
        self._all_do_tmpl_files = self._scan_xml_relpaths(do_dir)

        def apply_filter(*_args) -> None:
            raw = ""
            if self.var_do_tmpl_filter is not None:
                raw = self.var_do_tmpl_filter.get().strip().lower()
            if not raw:
                filtered = list(self._all_do_tmpl_files)
            else:
                tokens = [t for t in raw.split() if t]

                def ok(v: str) -> bool:
                    lv = (v or "").lower()
                    return all(t in lv for t in tokens)

                filtered = [v for v in self._all_do_tmpl_files if ok(v)]

            cur = (self.var_do_tmpl_selected.get() or "").strip()
            if cur and cur not in filtered:
                filtered = [cur] + filtered

            max_show = 1200
            shown = filtered[:max_show]
            self.cb_do_tmpl["values"] = shown
            suffix = "" if len(filtered) <= max_show else f" (showing first {max_show})"
            self.lbl_do_tmpl_match.configure(text=f"{len(filtered)} match{'' if len(filtered)==1 else 'es'}{suffix}")

        if getattr(self, "_do_tmpl_apply_filter", None) is None:
            if self.var_do_tmpl_filter is not None:
                self.var_do_tmpl_filter.trace_add("write", apply_filter)
            setattr(self, "_do_tmpl_apply_filter", apply_filter)
        else:
            apply_filter = getattr(self, "_do_tmpl_apply_filter")

        if select_rel:
            try:
                self.var_do_tmpl_selected.set(select_rel)
            except Exception:
                pass

        try:
            apply_filter()
        except Exception:
            pass

    # --- DO template: Language reference (similar to LN instance) ---

    def _on_do_tmpl_details_tab_changed(self) -> None:
        try:
            if self._do_tmpl_details_nb is None:
                return
            current = self._do_tmpl_details_nb.select()
            tab_text = self._do_tmpl_details_nb.tab(current, "text")
            if tab_text == "Language reference":
                self._refresh_do_tmpl_language_reference()
        except Exception:
            pass

    def _clear_do_tmpl_lang_filter(self) -> None:
        try:
            if self.var_do_tmpl_lang_filter is not None:
                self.var_do_tmpl_lang_filter.set("")
        except Exception:
            pass
        self._apply_do_tmpl_lang_filter()

    def _do_tmpl_langref_private_type(self) -> str:
        return "SchneiderElectric-PowerLogic-LangRef"

    def _do_tmpl_get_da_langref_id(self, da_el: ET.Element) -> str:
        ptype = self._do_tmpl_langref_private_type()
        try:
            for ch in list(da_el):
                if not isinstance(ch.tag, str) or _local_name(ch.tag) != "Private":
                    continue
                if (ch.attrib.get("type") or "") != ptype:
                    continue
                return (ch.text or "").strip()
        except Exception:
            pass
        return ""

    def _do_tmpl_set_da_langref_id(self, da_el: ET.Element, value: str) -> None:
        value = (value or "").strip()
        ptype = self._do_tmpl_langref_private_type()

        ns = ""
        try:
            if isinstance(da_el.tag, str) and da_el.tag.startswith("{"):
                ns = da_el.tag.split("}", 1)[0][1:]
        except Exception:
            ns = ""

        p_el: ET.Element | None = None
        try:
            for ch in list(da_el):
                if not isinstance(ch.tag, str) or _local_name(ch.tag) != "Private":
                    continue
                if (ch.attrib.get("type") or "") != ptype:
                    continue
                p_el = ch
                break
        except Exception:
            p_el = None

        if value == "":
            if p_el is not None:
                da_el.remove(p_el)
            return

        if p_el is None:
            p_el = ET.SubElement(da_el, _q(ns, "Private"))
            p_el.set("type", ptype)
        p_el.text = value

    def _refresh_do_tmpl_language_reference(self) -> None:
        if self._do_tmpl_lang_tree is None:
            return

        # Ensure tags exist for state coloring.
        try:
            self._do_tmpl_lang_tree.tag_configure("added", background="honeydew2")
            self._do_tmpl_lang_tree.tag_configure("removed", background="misty rose")
            self._do_tmpl_lang_tree.tag_configure("changed", background="lemon chiffon")
        except Exception:
            pass

        # Ensure any pending DA-table edit is committed before we read element state.
        try:
            if self._do_tmpl_table is not None:
                self._do_tmpl_table.commit_any_edit()
        except Exception:
            pass

        # Use table rows (not get_rows) so we can show soft-deleted DAs in red.
        table_rows = list(self._do_tmpl_table.rows or []) if self._do_tmpl_table is not None else []

        rows: list[dict[str, str]] = []
        for idx, row in enumerate(table_rows):
            name = (row.get("name") or "").strip()
            lang_id = (row.get("langRef") or "").strip()
            btype = (row.get("bType") or "").strip()
            enum_type = (row.get("type") or "").strip()
            fc = (row.get("fc") or "").strip()
            desc = (row.get("desc") or "").strip()

            type_txt = btype
            if btype.upper() == "ENUM" and enum_type:
                type_txt = f"{btype} ({enum_type})"

            parts: list[str] = []
            if fc:
                parts.append(f"[{fc}]")
            if type_txt:
                parts.append(type_txt)
            if desc:
                parts.append(desc)
            desc_txt = " ".join(parts)

            rows.append(
                {
                    "iid": str(idx),
                    "name": name,
                    "id": lang_id,
                    "desc": desc_txt,
                }
            )

        self._do_tmpl_lang_rows_all = rows
        self._apply_do_tmpl_lang_filter()

    def _apply_do_tmpl_lang_filter(self) -> None:
        if self._do_tmpl_lang_tree is None:
            return

        flt = ""
        try:
            if self.var_do_tmpl_lang_filter is not None:
                flt = (self.var_do_tmpl_lang_filter.get() or "").strip().lower()
        except Exception:
            flt = ""

        self._do_tmpl_lang_tree.delete(*self._do_tmpl_lang_tree.get_children())

        filtered: list[dict[str, str]] = []
        for row in self._do_tmpl_lang_rows_all or []:
            if flt:
                hay = f"{row.get('name','')} {row.get('id','')} {row.get('desc','')}".lower()
                if flt not in hay:
                    continue
            filtered.append(row)
            tags: tuple[str, ...] = ()
            try:
                if self._do_tmpl_table is not None:
                    src_idx = int(row.get("iid") or "-1")
                    if 0 <= src_idx < len(self._do_tmpl_table.rows):
                        src = self._do_tmpl_table.rows[src_idx]
                        if bool(src.get("__ui_deleted")):
                            tags = ("removed",)
                        elif bool(src.get("__ui_added")):
                            tags = ("added",)
                        else:
                            nm = (src.get("name") or "").strip()
                            cur_lr = (src.get("langRef") or "").strip()
                            saved_lr = (getattr(self, "_do_tmpl_lang_saved_by_name", {}) or {}).get(nm)
                            tags = ("changed",) if (saved_lr is None or saved_lr != cur_lr) else ()
            except Exception:
                tags = ()

            self._do_tmpl_lang_tree.insert(
                "",
                "end",
                iid=row.get("iid") or "",
                values=(row.get("name", ""), row.get("id", ""), row.get("desc", "")),
                tags=tags,
            )

        self._do_tmpl_lang_rows_filtered = filtered

        try:
            if self.lbl_do_tmpl_lang_match is not None:
                self.lbl_do_tmpl_lang_match.configure(
                    text=f"{len(filtered)}/{len(self._do_tmpl_lang_rows_all or [])}"
                )
        except Exception:
            pass

    def _on_do_tmpl_lang_left_click(self, _evt=None) -> None:
        try:
            self._end_do_tmpl_lang_inline(commit=True)
        except Exception:
            pass

    def _on_do_tmpl_lang_double_click(self, evt) -> None:
        if self._do_tmpl_lang_tree is None:
            return
        try:
            iid = self._do_tmpl_lang_tree.identify_row(evt.y)
            col = self._do_tmpl_lang_tree.identify_column(evt.x)
        except Exception:
            return

        if not iid or col != "#2":
            return

        self._start_do_tmpl_lang_inline(iid)

    def _start_do_tmpl_lang_inline(self, iid: str) -> None:
        if self._do_tmpl_lang_tree is None:
            return
        self._end_do_tmpl_lang_inline(commit=True)

        try:
            bbox = self._do_tmpl_lang_tree.bbox(iid, "#2")
        except Exception:
            bbox = None
        if not bbox:
            return

        x, y, w, h = bbox
        cur = ""
        try:
            cur = (self._do_tmpl_lang_tree.set(iid, "id") or "").strip()
        except Exception:
            cur = ""

        ent = ttk.Entry(self._do_tmpl_lang_tree)
        ent.insert(0, cur)
        ent.select_range(0, tk.END)
        ent.focus_set()
        ent.place(x=x, y=y, width=w, height=h)

        self._do_tmpl_lang_inline = ent
        self._do_tmpl_lang_inline_iid = iid

        ent.bind("<Return>", lambda _e: self._end_do_tmpl_lang_inline(commit=True))
        ent.bind("<Escape>", lambda _e: self._end_do_tmpl_lang_inline(commit=False))
        ent.bind("<FocusOut>", lambda _e: self._end_do_tmpl_lang_inline(commit=True))
        ent.bind("<Control-z>", lambda _e: (self._do_tmpl_lang_undo(), "break")[1])
        ent.bind("<Control-Z>", lambda _e: (self._do_tmpl_lang_undo(), "break")[1])

    def _end_do_tmpl_lang_inline(self, commit: bool) -> None:
        ent = self._do_tmpl_lang_inline
        iid = self._do_tmpl_lang_inline_iid
        if ent is None or iid is None:
            return

        new_val = (ent.get() or "").strip()

        try:
            ent.place_forget()
        except Exception:
            pass
        try:
            ent.destroy()
        except Exception:
            pass

        self._do_tmpl_lang_inline = None
        self._do_tmpl_lang_inline_iid = None

        if not commit:
            return

        if new_val and not re.fullmatch(r"\d+(?:\.\d+)?", new_val):
            messagebox.showerror(
                "Invalid Language reference",
                "Language reference must be empty or like 12 or 12.34",
                parent=self,
            )
            return

        try:
            idx = int(iid)
        except Exception:
            return

        if self._do_tmpl_table is None or idx < 0 or idx >= len(self._do_tmpl_table.rows):
            return

        # Do not allow editing soft-deleted rows.
        try:
            if bool(self._do_tmpl_table.rows[idx].get("__ui_deleted")):
                return
        except Exception:
            pass

        try:
            self._do_tmpl_table.commit_any_edit()
        except Exception:
            pass
        try:
            self._do_tmpl_table._push_undo()
        except Exception:
            pass
        self._do_tmpl_table.rows[idx]["langRef"] = new_val

        try:
            for r in (self._do_tmpl_lang_rows_all or []):
                if (r.get("iid") or "") == iid:
                    r["id"] = new_val
                    break
        except Exception:
            pass

        try:
            if self._do_tmpl_lang_tree is not None:
                self._do_tmpl_lang_tree.set(iid, "id", new_val)
        except Exception:
            pass

        try:
            self._refresh_do_tmpl_language_reference()
        except Exception:
            pass

        try:
            self._on_do_tmpl_view_changed()
        except Exception:
            pass

    def _do_tmpl_lang_undo(self) -> None:
        if self._do_tmpl_table is None:
            return
        try:
            self._end_do_tmpl_lang_inline(commit=False)
        except Exception:
            pass
        try:
            self._do_tmpl_table.undo()
        except Exception:
            return
        try:
            self._refresh_do_tmpl_language_reference()
        except Exception:
            pass

    def _scan_do_cdc_qt_presence(self) -> dict[str, tuple[int, int, int]]:
        do_dir = self._do_type_dir()
        if not do_dir.exists():
            return {}

        # cdc -> (total, q_count, t_count)
        stats: dict[str, list[int]] = {}
        for rel in self._scan_xml_relpaths(do_dir):
            p = do_dir / rel
            try:
                root = ET.parse(p).getroot()
            except Exception:
                continue

            do_el: ET.Element | None = None
            for el in root.iter():
                if isinstance(el.tag, str) and _local_name(el.tag) == "DOType":
                    do_el = el
                    break
            if do_el is None:
                continue

            cdc = (do_el.attrib.get("cdc") or "").strip().upper()
            if not cdc:
                continue

            has_q = False
            has_t = False
            try:
                for child in list(do_el):
                    if not isinstance(child.tag, str) or _local_name(child.tag) != "DA":
                        continue
                    n = (child.attrib.get("name") or "").strip()
                    if n == "q":
                        has_q = True
                    elif n == "t":
                        has_t = True
                    if has_q and has_t:
                        break
            except Exception:
                pass

            cur = stats.setdefault(cdc, [0, 0, 0])
            cur[0] += 1
            cur[1] += 1 if has_q else 0
            cur[2] += 1 if has_t else 0

        return {k: (v[0], v[1], v[2]) for k, v in stats.items()}

    def _get_do_cdc_qt_presence(self) -> dict[str, tuple[int, int, int]]:
        cache = getattr(self, "_do_cdc_qt_presence_cache", None)
        if cache is None:
            cache = self._scan_do_cdc_qt_presence()
            setattr(self, "_do_cdc_qt_presence_cache", dict(cache))
        return dict(cache)

    def _default_new_do_template_da_names_for_cdc(self, cdc: str) -> list[str]:
        # Always include 'd'.
        cdc = (cdc or "").strip().upper()
        names: list[str] = ["d"]
        if not cdc:
            return names

        stats = self._get_do_cdc_qt_presence().get(cdc)
        if not stats:
            return names

        total, q_cnt, t_cnt = stats
        if total <= 0:
            return names

        q_all = q_cnt == total
        t_all = t_cnt == total
        q_any = q_cnt > 0

        # Conservative defaults:
        # - if q+t are universal for that CDC, include both
        # - if only t is universal and q never appears, include only t
        if q_all and t_all:
            return ["q", "t", "d"]
        if t_all and not q_any:
            return ["t", "d"]
        return names

    def _new_do_template(self, *, default_cdc: str = "") -> None:
        self._do_tmpl_file_path = None
        self._do_tmpl_root = None
        self._do_tmpl_dotype = None
        self._do_tmpl_child_specs = []
        self._do_tmpl_da_elements = []
        if self._do_tmpl_table is None or self._do_tmpl_id is None or self._do_tmpl_cdc is None or self._do_tmpl_desc is None:
            return

        # Create a minimal in-memory SCL root and DOType skeleton.
        ET.register_namespace("", SCL_NS)
        ET.register_namespace("xsi", "http://www.w3.org/2001/XMLSchema-instance")
        ET.register_namespace("xsd", "http://www.w3.org/2001/XMLSchema")

        root = ET.Element(_q(SCL_NS, "SCL"))
        root.attrib[_q("http://www.w3.org/2001/XMLSchema-instance", "schemaLocation")] = f"{SCL_NS} SCL.xsd"
        do_el = ET.SubElement(root, _q(SCL_NS, "DOType"))
        do_el.attrib["id"] = ""
        do_el.attrib["cdc"] = (default_cdc or "").strip().upper()
        do_el.attrib["desc"] = ""

        # Default DA rows for a new DOType: always 'd', and optionally q/t depending on CDC.
        chosen_names = self._default_new_do_template_da_names_for_cdc(do_el.attrib.get("cdc") or "")
        da_elements: list[ET.Element] = []
        table_rows: list[dict[str, str]] = []

        def add_q() -> None:
            da = ET.Element(_q(SCL_NS, "DA"))
            da.attrib.update(
                {
                    "name": "q",
                    "fc": "ST",
                    "bType": "Quality",
                    "qchg": "true",
                    "desc": "The quality of the value",
                }
            )
            da_elements.append(da)
            table_rows.append(
                {
                    "name": "q",
                    "fc": "ST",
                    "bType": "Quality",
                    "type": "",
                    "valKind": "",
                    "valImport": "",
                    "dchg": "",
                    "val": "",
                    "langRef": "",
                    "desc": "The quality of the value",
                }
            )

        def add_t() -> None:
            da = ET.Element(_q(SCL_NS, "DA"))
            da.attrib.update(
                {
                    "name": "t",
                    "fc": "ST",
                    "bType": "Timestamp",
                    "desc": "Timestamp of the last change in state",
                }
            )
            da_elements.append(da)
            table_rows.append(
                {
                    "name": "t",
                    "fc": "ST",
                    "bType": "Timestamp",
                    "type": "",
                    "valKind": "",
                    "valImport": "",
                    "dchg": "",
                    "val": "",
                    "langRef": "",
                    "desc": "Timestamp of the last change in state",
                }
            )

        def add_d() -> None:
            da = ET.Element(_q(SCL_NS, "DA"))
            da.attrib.update(
                {
                    "name": "d",
                    "fc": "DC",
                    "bType": "VisString255",
                    "valKind": "RO",
                    "valImport": "false",
                    "desc": "English label",
                }
            )
            da_elements.append(da)
            table_rows.append(
                {
                    "name": "d",
                    "fc": "DC",
                    "bType": "VisString255",
                    "type": "",
                    "valKind": "RO",
                    "valImport": "false",
                    "dchg": "",
                    "val": "",
                    "langRef": "",
                    "desc": "English label",
                }
            )

        for n in chosen_names:
            if n == "q":
                add_q()
            elif n == "t":
                add_t()
            elif n == "d":
                add_d()

        self._do_tmpl_root = root
        self._do_tmpl_dotype = do_el
        self._do_tmpl_child_specs = []
        self._do_tmpl_da_elements = list(da_elements)

        self._do_tmpl_loading = True
        try:
            self._do_tmpl_id.set("")
            self._do_tmpl_cdc.set(do_el.attrib.get("cdc") or "")
            self._do_tmpl_desc.set("")
            self._do_tmpl_table.set_rows(list(table_rows))

            try:
                self._refresh_do_tmpl_language_reference()
            except Exception:
                pass

            if self._do_tmpl_private_enabled is not None:
                try:
                    self._do_tmpl_private_enabled.set(False)
                except Exception:
                    pass

            if self.var_do_tmpl_selected is not None:
                try:
                    self.var_do_tmpl_selected.set("")
                except Exception:
                    pass
        finally:
            self._do_tmpl_loading = False

        self._mark_do_tmpl_unsaved()
        self._set_status("New DO template created (unsaved)")

    def _on_do_tmpl_private_toggle(self) -> None:
        if self._do_tmpl_private_enabled is None or self._do_tmpl_table is None:
            return

        enabled = bool(self._do_tmpl_private_enabled.get())
        if not enabled:
            # Remove the managed dataNs DA row when Private is unchecked.
            try:
                rows0 = self._do_tmpl_table.get_rows()
            except Exception:
                rows0 = []
            rows = [r for r in rows0 if (r.get("name") or "").strip() != "dataNs"]
            if len(rows) != len(rows0):
                self._do_tmpl_table.set_rows(rows)
                # Drop DA placeholders in child specs to avoid stale indices after structural edits.
                try:
                    self._do_tmpl_child_specs = [x for x in (self._do_tmpl_child_specs or []) if x[0] == "ELEM"]
                except Exception:
                    pass
                try:
                    self._apply_do_template_ui_to_xml()
                except Exception:
                    pass
                try:
                    self._on_do_tmpl_view_changed()
                except Exception:
                    pass
            return

        if self._do_tmpl_root is None or self._do_tmpl_dotype is None:
            self._new_do_template()
        if self._do_tmpl_root is None or self._do_tmpl_dotype is None:
            return

        # Normalize current DA XML list before appending.
        try:
            self._apply_do_template_ui_to_xml()
        except Exception:
            pass

        rows = self._do_tmpl_table.get_rows()
        if any((r.get("name") or "").strip() == "dataNs" for r in rows):
            return

        rows.append(
            {
                "name": "dataNs",
                "fc": "EX",
                "bType": "VisString255",
                "type": "",
                "valKind": "",
                "valImport": "",
                "dchg": "",
                "val": "SE_PowerLogic_dataNs_V001:2016",
                "langRef": "",
                "desc": "Private name space",
            }
        )
        self._do_tmpl_table.set_rows(rows)

        do_el = self._do_tmpl_dotype
        ns = ""
        if isinstance(do_el.tag, str) and do_el.tag.startswith("{"):
            ns = do_el.tag.split("}", 1)[0][1:]
        ns = ns or SCL_NS

        da_el = ET.Element(_q(ns, "DA"))
        da_el.attrib.update(
            {
                "name": "dataNs",
                "fc": "EX",
                "bType": "VisString255",
                "desc": "Private name space",
            }
        )
        v = ET.SubElement(da_el, _q(ns, "Val"))
        v.text = "SE_PowerLogic_dataNs_V001:2016"
        self._do_tmpl_da_elements.append(da_el)
        try:
            self._on_do_tmpl_view_changed()
        except Exception:
            pass

    def _open_do_template_from_path(self, path: Path) -> None:
        if self.do_template_tab is None:
            return
        try:
            self.do_template_tab.open_do_template_from_path(Path(path))
        except Exception:
            pass

    def _open_do_template(self) -> None:
        if self.do_template_tab is None:
            return
        try:
            self.do_template_tab.open_do_template()
        except Exception:
            pass

    def _open_do_template_from_search(self) -> None:
        if self.do_template_tab is None:
            return
        try:
            self.do_template_tab.open_do_template_from_search()
        except Exception:
            pass

    def _save_do_template(self) -> None:
        if self.do_template_tab is None:
            return
        try:
            self.do_template_tab.save_do_template()
        except Exception:
            pass

    def _save_do_template_as(self) -> None:
        if self.do_template_tab is None:
            return
        try:
            self.do_template_tab.save_do_template_as()
        except Exception:
            pass

    def _apply_do_template_ui_to_xml(self) -> None:
        if self._do_tmpl_table is None or self._do_tmpl_id is None or self._do_tmpl_cdc is None or self._do_tmpl_desc is None:
            return
        if self._do_tmpl_root is None or self._do_tmpl_dotype is None:
            # If user never opened/created, start from skeleton.
            self._new_do_template()
        if self._do_tmpl_root is None or self._do_tmpl_dotype is None:
            return

        do_el = self._do_tmpl_dotype
        ns = ""
        if isinstance(do_el.tag, str) and do_el.tag.startswith("{"):
            ns = do_el.tag.split("}", 1)[0][1:]

        do_el.attrib["id"] = (self._do_tmpl_id.get() or "").strip()
        do_el.attrib["cdc"] = (self._do_tmpl_cdc.get() or "").strip()
        do_el.attrib["desc"] = self._do_tmpl_desc.get() or ""

        # Rebuild DA elements list and apply attribute edits.
        rows = self._do_tmpl_table.get_rows()
        new_da_elems: list[ET.Element] = []
        for i, row in enumerate(rows):
            base = None
            if i < len(self._do_tmpl_da_elements):
                base = _deepcopy_et_element(self._do_tmpl_da_elements[i])
            if base is None:
                base = ET.Element(_q(ns, "DA"))
            # Apply attributes
            for k in ["name", "fc", "bType", "type", "valKind", "valImport", "dchg", "desc"]:
                val = (row.get(k) or "").strip() if k != "desc" else (row.get(k) or "")
                if val:
                    base.attrib[k] = val
                else:
                    if k in base.attrib:
                        del base.attrib[k]

            # Apply <Val> (optional)
            raw_val = row.get("val") or ""
            if (raw_val or "").strip():
                val_el = None
                for sub in list(base):
                    if isinstance(sub.tag, str) and _local_name(sub.tag) == "Val":
                        val_el = sub
                        break
                if val_el is None:
                    val_el = ET.SubElement(base, _q(ns, "Val"))
                val_el.text = raw_val
            else:
                # Remove any existing Val if user clears it.
                for sub in list(base):
                    if isinstance(sub.tag, str) and _local_name(sub.tag) == "Val":
                        try:
                            base.remove(sub)
                        except Exception:
                            pass

            # Apply Language reference (<Private type="...LangRef">) from hidden row field.
            try:
                self._do_tmpl_set_da_langref_id(base, (row.get("langRef") or "").strip())
            except Exception:
                pass
            new_da_elems.append(base)

        self._do_tmpl_da_elements = new_da_elems

    def _write_do_template_xml(self, path: Path) -> None:
        if self._do_tmpl_root is None or self._do_tmpl_dotype is None:
            raise ValueError("No DO template loaded")

        root = self._do_tmpl_root
        do_el = self._do_tmpl_dotype

        ns = ""
        if isinstance(root.tag, str) and root.tag.startswith("{"):
            ns = root.tag.split("}", 1)[0][1:]

        # Rebuild DOType children preserving non-DA nodes and ordering.
        for ch in list(do_el):
            do_el.remove(ch)

        rows = self._do_tmpl_table.get_rows() if self._do_tmpl_table is not None else []
        # Ensure DA elements list length matches rows.
        if len(self._do_tmpl_da_elements) != len(rows):
            # Re-apply will normalize.
            self._apply_do_template_ui_to_xml()

        for kind, payload in (self._do_tmpl_child_specs or []):
            if kind == "ELEM":
                try:
                    do_el.append(_deepcopy_et_element(payload))  # type: ignore[arg-type]
                except Exception:
                    continue
            elif kind == "DA":
                try:
                    idx = int(payload)  # type: ignore[arg-type]
                except Exception:
                    continue
                if idx < 0 or idx >= len(self._do_tmpl_da_elements):
                    continue
                do_el.append(_deepcopy_et_element(self._do_tmpl_da_elements[idx]))

        # If specs were empty (new template), append non-DA from current do_el and then all DAs.
        if not self._do_tmpl_child_specs:
            for da in self._do_tmpl_da_elements:
                do_el.append(_deepcopy_et_element(da))

        # If user added more DAs than originally existed, append extras at the end.
        existing_da_count = sum(1 for k, _p in (self._do_tmpl_child_specs or []) if k == "DA")
        for extra in self._do_tmpl_da_elements[existing_da_count:]:
            do_el.append(_deepcopy_et_element(extra))

        # Serialize with required header/footer formatting.
        schema_ns = "http://www.w3.org/2001/XMLSchema"
        xsi_ns = "http://www.w3.org/2001/XMLSchema-instance"

        # Ensure schemaLocation exists (content is fixed per requirement).
        root.attrib[_q(xsi_ns, "schemaLocation")] = f"{SCL_NS} SCL.xsd"

        ET.register_namespace("", ns or SCL_NS)
        ET.register_namespace("xsi", xsi_ns)
        ET.register_namespace("xsd", schema_ns)
        try:
            ET.indent(root, space="    ")
        except Exception:
            pass

        body = ET.tostring(root, encoding="unicode", short_empty_elements=True)
        # Replace the root opening tag to exactly match required attribute order.
        open_end = body.find(">")
        if open_end != -1:
            required_open = (
                f'<SCL xmlns:xsd="{schema_ns}" '
                f'xmlns="{SCL_NS}" '
                f'xmlns:xsi="{xsi_ns}" '
                f'xsi:schemaLocation="{SCL_NS} SCL.xsd">'
            )
            body = required_open + body[open_end + 1 :]
        # Ensure closing tag is </SCL>
        body = re.sub(r"</[^>]*:?SCL\s*>", "</SCL>", body)

        text = "<?xml version=\"1.0\" encoding=\"utf-8\" ?>\n" + body.rstrip() + "\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        # Use CRLF for saved XML files on Windows.
        with open(path, "w", encoding="utf-8", newline="\r\n") as f:
            f.write(text)

    def _refresh_application_search_list(self, *, select_rel: str | None) -> None:
        """Refresh the Application Search combobox items."""
        if self.cb_app is None or self.var_app_selected is None or self.lbl_app_match is None:
            return
        app_dir = self._application_dir()
        self._all_app_files = self._scan_xml_relpaths(app_dir)

        def apply_filter(*_args) -> None:
            raw = ""
            if self.var_app_filter is not None:
                raw = self.var_app_filter.get().strip().lower()
            if not raw:
                filtered = list(self._all_app_files)
            else:
                tokens = [t for t in raw.split() if t]

                def ok(v: str) -> bool:
                    lv = (v or "").lower()
                    return all(t in lv for t in tokens)

                filtered = [v for v in self._all_app_files if ok(v)]

            cur = (self.var_app_selected.get() or "").strip()
            if cur and cur not in filtered:
                filtered = [cur] + filtered

            max_show = 1200
            shown = filtered[:max_show]
            self.cb_app["values"] = shown
            suffix = "" if len(filtered) <= max_show else f" (showing first {max_show})"
            self.lbl_app_match.configure(text=f"{len(filtered)} match{'' if len(filtered)==1 else 'es'}{suffix}")

        # Wire filter only once per UI lifetime.
        if getattr(self, "_app_apply_filter", None) is None:
            if self.var_app_filter is not None:
                self.var_app_filter.trace_add("write", apply_filter)
            setattr(self, "_app_apply_filter", apply_filter)
        else:
            apply_filter = getattr(self, "_app_apply_filter")

        if select_rel:
            try:
                self.var_app_selected.set(select_rel)
            except Exception:
                pass

        try:
            apply_filter()
        except Exception:
            pass

    def _open_application_from_search(self) -> None:
        if self.var_app_selected is None:
            return
        rel = (self.var_app_selected.get() or "").strip()
        if not rel:
            return
        app_dir = self._application_dir()
        target = app_dir / rel
        if not target.exists():
            messagebox.showerror("Missing", f"File not found:\n\n{os.fspath(target)}", parent=self)
            return
        self._open_application_from_path(target)

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

    def _do_type_has_angle(self, do_type_id: str) -> bool:
        """Return True if the DOType structure includes an angle ("ang") field.

        Used to decide whether CDC mappings that normally produce Vector types
        should instead use FLOAT32 variants when the underlying structure lacks
        any angle component.
        """

        do_type_id = (do_type_id or "").strip()
        if not do_type_id:
            return False

        cache = getattr(self, "_do_angle_cache", None)
        if cache is None:
            cache = {}
            setattr(self, "_do_angle_cache", cache)
        if do_type_id in cache:
            return bool(cache[do_type_id])

        do_dir = self.workspace_root / "ep7_datamodel" / "datamodel" / "iec61850" / "DOType"
        da_dir = self.workspace_root / "ep7_datamodel" / "datamodel" / "iec61850" / "DAType"

        visited_do: set[str] = set()
        visited_da: set[str] = set()

        def parse_xml_root(path: Path) -> ET.Element | None:
            try:
                return ET.parse(path).getroot()
            except Exception:
                return None

        def nsq(root: ET.Element):
            ns = ""
            if isinstance(root.tag, str) and root.tag.startswith("{"):
                ns = root.tag.split("}", 1)[0][1:]

            def q(tag: str) -> str:
                return f"{{{ns}}}{tag}" if ns else tag

            return q

        def scan_da_type(da_type_id: str) -> bool:
            da_type_id = (da_type_id or "").strip()
            if not da_type_id or da_type_id in visited_da:
                return False
            visited_da.add(da_type_id)

            p = self._find_type_file(kind_dir=da_dir, type_id=da_type_id)
            if p is None:
                return False
            root = parse_xml_root(p)
            if root is None:
                return False

            q = nsq(root)
            da_el = root.find(f".//{q('DAType')}")
            if da_el is None:
                return False

            for bda in da_el.findall(q("BDA")):
                name = (bda.attrib.get("name") or "").strip().lower()
                if name == "ang":
                    return True
                btype = (bda.attrib.get("bType") or "").strip().lower()
                if btype == "struct":
                    sub_type = (bda.attrib.get("type") or "").strip()
                    if sub_type and scan_da_type(sub_type):
                        return True

            return False

        def scan_do_type(type_id: str) -> bool:
            type_id = (type_id or "").strip()
            if not type_id or type_id in visited_do:
                return False
            visited_do.add(type_id)

            p = self._find_type_file(kind_dir=do_dir, type_id=type_id)
            if p is None:
                return False
            root = parse_xml_root(p)
            if root is None:
                return False

            q = nsq(root)
            do_el = root.find(f".//{q('DOType')}")
            if do_el is None:
                return False

            # Scan DAs for direct/struct angle.
            for da in do_el.findall(q("DA")):
                name = (da.attrib.get("name") or "").strip().lower()
                if name == "ang":
                    return True
                btype = (da.attrib.get("bType") or "").strip().lower()
                if btype == "struct":
                    sub_type = (da.attrib.get("type") or "").strip()
                    if sub_type and scan_da_type(sub_type):
                        return True

            # Recurse into SDOs.
            for sdo in do_el.findall(q("SDO")):
                sub_type = (sdo.attrib.get("type") or "").strip()
                if sub_type and scan_do_type(sub_type):
                    return True

            return False

        res = scan_do_type(do_type_id)
        cache[do_type_id] = bool(res)
        return bool(res)

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

            # For CMV/WYE/DEL/SEQ, select Vector vs Float32 based on whether the
            # underlying DO structure contains an angle ("ang").
            if cdc in {"CMV", "WYE", "DEL", "SEQ"} and mapped_type:
                if not self._do_type_has_angle(do_type):
                    mapped_type = "STD_FLOAT32" if cdc == "CMV" else "TRI_STD_FLOAT32"

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
        try:
            self._on_app_view_changed()
        except Exception:
            pass

    def _refresh_app_input_tv(self) -> None:
        tv = self._app_tv_input
        if tv is None:
            return
        self._app_input_iid_to_row = {}
        self._clear_tv(tv)
        added_names = set(self._app_sync_added_names.get("input", set()))
        changed_names = set((getattr(self, "_app_changed_names", {}) or {}).get("input", set()))
        for idx, row in enumerate(self._app_input_rows):
            iid = str(idx)
            self._app_input_iid_to_row[iid] = row
            soft = (row.get("softlink") or "").lower() == "true"
            conf = (row.get("confpin") or "").lower() == "true"
            nm = (row.get("name") or "").strip()
            tags: tuple[str, ...]
            if bool(row.get("__ui_deleted")):
                tags = ("removed",)
            elif bool(row.get("__ui_added")) or (nm in added_names):
                tags = ("added",)
            elif nm in changed_names:
                tags = ("changed",)
            else:
                tags = ()
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
                tags=tags,
            )

        # Append removed snapshots (non-editable)
        removed = list(self._app_sync_removed_snapshots.get("input", []))
        for i, snap in enumerate(removed):
            soft = (snap.get("softlink") or "").lower() == "true"
            conf = (snap.get("confpin") or "").lower() == "true"
            tv.insert(
                "",
                "end",
                iid=f"__removed_input_{i}",
                values=[
                    snap.get("name") or "",
                    snap.get("type") or "",
                    snap.get("src") or "",
                    snap.get("doRef") or "",
                    "☑" if soft else "☐",
                    "☑" if conf else "☐",
                ],
                tags=("removed",),
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
        nm = (row.get("name") or "").strip()
        added_names = set(self._app_sync_added_names.get("input", set()))
        changed_names = set((getattr(self, "_app_changed_names", {}) or {}).get("input", set()))
        if bool(row.get("__ui_deleted")):
            tags: tuple[str, ...] = ("removed",)
        elif bool(row.get("__ui_added")) or (nm in added_names):
            tags = ("added",)
        elif nm in changed_names:
            tags = ("changed",)
        else:
            tags = ()
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
            tags=tags,
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
        if bool(row.get("__ui_deleted")):
            return
        dlg = _EditApplicationInputDialog(self, title="Edit input", input_types=self._get_app_input_types(), initial=row)
        res = dlg.show()
        if not res:
            return
        row.update(res)
        self._refresh_app_input_tv()
        try:
            self._on_app_view_changed()
        except Exception:
            pass

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
            tv.focus_set()
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
            if bool(row.get("__ui_deleted")):
                return "break"
            key = "softlink" if col == "#5" else "confpin"
            cur = (row.get(key) or "").lower() == "true"
            row[key] = "" if cur else "true"
            self._update_app_input_tv_row(row_iid)
            self._end_app_input_inline_editor(commit=False)
            try:
                self._on_app_view_changed()
            except Exception:
                pass
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
            tv.focus_set()
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
            tv.focus_set()
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
            tv.focus_set()
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
            tv.focus_set()
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
            if bool(row.get("__ui_deleted")):
                return "break"
            key = "persist" if col == persist_col else "faultlog"
            cur = (row.get(key) or "").lower() == "true"
            if key == "persist":
                row[key] = "false" if cur else "true"
            else:
                row[key] = "" if cur else "true"
            self._update_simple_app_tv_row("output", row_iid)
            self._end_app_output_inline_editor(commit=False)
            try:
                self._on_app_view_changed()
            except Exception:
                pass
            return "break"

        if col == type_col:
            try:
                idx0 = int(row_iid)
                if 0 <= idx0 < len(self._app_output_rows) and bool(self._app_output_rows[idx0].get("__ui_deleted")):
                    return "break"
            except Exception:
                pass
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
            tv.focus_set()
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
            tv.focus_set()
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
            tv.focus_set()
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
            tv.focus_set()
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
            tv.focus_set()
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

        tv.bind("<Control-z>", lambda _e: (self._app_undo(), "break")[1])
        tv.bind("<Control-Z>", lambda _e: (self._app_undo(), "break")[1])

        def _focus_then(fn):
            def _wrapped(t=table, _tv=tv):
                try:
                    _tv.focus_set()
                except Exception:
                    pass
                return fn(t)

            return _wrapped

        # Toolbar buttons live in the parent wrapper (created by _make_tv)
        wrap = tv.master
        tb = getattr(wrap, "_toolbar", None)
        btn = getattr(wrap, "_btn", None)
        if tb is None or btn is None:
            return

        btn("Add", _focus_then(self._app_table_add))
        btn("Insert", _focus_then(self._app_table_insert), padx=(6, 0))
        btn("Edit", _focus_then(self._app_table_edit), padx=(6, 0))
        btn("Copy", _focus_then(self._app_table_copy), padx=(6, 0))
        btn("Cut", _focus_then(self._app_table_cut), padx=(6, 0))
        btn("Paste", _focus_then(self._app_table_paste), padx=(6, 0))
        btn("Delete", _focus_then(self._app_table_delete), padx=(6, 0))
        btn("Up", lambda t=table, _tv=tv: (_tv.focus_set(), self._app_table_move(t, -1)), padx=(18, 0))
        btn("Down", lambda t=table, _tv=tv: (_tv.focus_set(), self._app_table_move(t, 1)), padx=(6, 0))

    def _show_app_table_context_menu(self, event: tk.Event, table: str) -> None:
        tv = self._app_table_tv(table)
        if tv is None:
            return
        iid = tv.identify_row(event.y)
        if iid:
            try:
                tv.selection_set(iid)
                tv.focus_set()
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
        m.add_command(label="Edit", command=lambda: self._app_table_edit(self._app_ctx_table or ""))

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
        is_deleted = False
        try:
            if has_sel and idx is not None:
                rows0 = self._app_table_rows(table)
                if 0 <= idx < len(rows0):
                    is_deleted = bool(rows0[idx].get("__ui_deleted"))
        except Exception:
            is_deleted = False
        can_paste = bool(self._app_clipboard.get(table))
        can_up = has_sel and idx is not None and idx > 0 and (not is_deleted)
        rows = self._app_table_rows(table)
        can_down = has_sel and idx is not None and idx < (len(rows) - 1) and (not is_deleted)
        can_add_shared = False
        if table == "input" and has_sel and idx is not None and 0 <= idx < len(self._app_input_rows):
            can_add_shared = (self._app_input_rows[idx].get("confpin") or "").lower() == "true"
            if is_deleted:
                can_add_shared = False

        try:
            m.entryconfigure("Copy", state=("normal" if has_sel else "disabled"))
        except Exception:
            pass
        for label in ("Edit", "Cut", "Delete"):
            try:
                m.entryconfigure(label, state=("normal" if (has_sel and (not is_deleted)) else "disabled"))
            except Exception:
                pass
        try:
            m.entryconfigure("Paste", state=("normal" if can_paste else "disabled"))
        except Exception:
            pass
        if table == "setting":
            try:
                m.entryconfigure("Convert to conf", state=("normal" if (has_sel and (not is_deleted)) else "disabled"))
            except Exception:
                pass
        if table == "conf":
            try:
                m.entryconfigure("Convert to setting", state=("normal" if (has_sel and (not is_deleted)) else "disabled"))
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
        if bool(in_row.get("__ui_deleted")):
            return
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
            if bool(r.get("__ui_deleted")):
                continue
            if (r.get("name") or "").strip() == base_name:
                existing_idx = i
                break

        insert_or_select_idx: int | None = None
        if existing_idx is None:
            rr = dict(new_row)
            rr["__ui_added"] = "1"
            rr.pop("__ui_deleted", None)
            setting_rows.append(rr)
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
                    if not any(
                        (
                            (not bool(rr.get("__ui_deleted")))
                            and ((rr.get("name") or "").strip() == cand)
                        )
                        for rr in setting_rows
                    ):
                        new_row["name"] = cand
                        break
                    n += 1
                rr = dict(new_row)
                rr["__ui_added"] = "1"
                rr.pop("__ui_deleted", None)
                setting_rows.append(rr)
                insert_or_select_idx = len(setting_rows) - 1
            elif choice == "overwrite":
                # Overwrite: replace the first matching row.
                setting_rows[existing_idx] = dict(new_row)
                insert_or_select_idx = existing_idx
            else:
                return

        self._app_push_undo()
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
        if bool(self._app_setting_rows[idx].get("__ui_deleted")):
            return
        self._app_push_undo()
        setting_rows = list(self._app_setting_rows)
        setting_rows[idx]["__ui_deleted"] = "1"
        try:
            setting_rows[idx].pop("__ui_added", None)
        except Exception:
            pass

        conf_rows = list(self._app_conf_rows)
        row = dict(setting_rows[idx])
        row.pop("__ui_deleted", None)
        row["__ui_added"] = "1"
        conf_rows.append(row)

        self._app_table_set_rows("setting", setting_rows)
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
        if bool(self._app_conf_rows[idx].get("__ui_deleted")):
            return
        self._app_push_undo()
        conf_rows = list(self._app_conf_rows)
        conf_rows[idx]["__ui_deleted"] = "1"
        try:
            conf_rows[idx].pop("__ui_added", None)
        except Exception:
            pass

        row = dict(conf_rows[idx])
        row.pop("__ui_deleted", None)
        row["__ui_added"] = "1"
        name = (row.get("name") or "").strip()
        row["src"] = f".{name}" if name else ""

        setting_rows = list(self._app_setting_rows)
        setting_rows.append(row)

        self._app_table_set_rows("conf", conf_rows)
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

        try:
            self._on_app_view_changed()
        except Exception:
            pass

    def _app_snapshot_all_rows(self) -> dict[str, object]:
        return {
            "input": [dict(r) for r in (self._app_input_rows or [])],
            "setting": [dict(r) for r in (self._app_setting_rows or [])],
            "output": [dict(r) for r in (self._app_output_rows or [])],
            "conf": [dict(r) for r in (self._app_conf_rows or [])],
            "control": [dict(r) for r in (self._app_control_rows or [])],
            "__sync_added_names": {
                "input": set(self._app_sync_added_names.get("input", set())),
                "setting": set(self._app_sync_added_names.get("setting", set())),
                "output": set(self._app_sync_added_names.get("output", set())),
                "conf": set(self._app_sync_added_names.get("conf", set())),
                "control": set(self._app_sync_added_names.get("control", set())),
            },
            "__sync_removed_snapshots": {
                "input": [dict(r) for r in (self._app_sync_removed_snapshots.get("input", []) or [])],
                "setting": [dict(r) for r in (self._app_sync_removed_snapshots.get("setting", []) or [])],
                "output": [dict(r) for r in (self._app_sync_removed_snapshots.get("output", []) or [])],
                "conf": [dict(r) for r in (self._app_sync_removed_snapshots.get("conf", []) or [])],
                "control": [dict(r) for r in (self._app_sync_removed_snapshots.get("control", []) or [])],
            },
        }

    def _app_restore_snapshot(self, snap: dict[str, object]) -> None:
        # Restore refresh diff state (added/removed row highlights).
        try:
            added = snap.get("__sync_added_names")  # type: ignore[assignment]
            removed = snap.get("__sync_removed_snapshots")  # type: ignore[assignment]
            if isinstance(added, dict) and isinstance(removed, dict):
                self._app_sync_added_names = {
                    "input": set(added.get("input", set())),
                    "setting": set(added.get("setting", set())),
                    "output": set(added.get("output", set())),
                    "conf": set(added.get("conf", set())),
                    "control": set(added.get("control", set())),
                }
                self._app_sync_removed_snapshots = {
                    "input": [dict(r) for r in (removed.get("input", []) or [])],
                    "setting": [dict(r) for r in (removed.get("setting", []) or [])],
                    "output": [dict(r) for r in (removed.get("output", []) or [])],
                    "conf": [dict(r) for r in (removed.get("conf", []) or [])],
                    "control": [dict(r) for r in (removed.get("control", []) or [])],
                }
            else:
                self._clear_app_refresh_diff_state()
        except Exception:
            try:
                self._clear_app_refresh_diff_state()
            except Exception:
                pass

        self._app_input_rows = [dict(r) for r in (snap.get("input") or [])]
        self._refresh_app_input_tv()

        self._app_setting_rows = [dict(r) for r in (snap.get("setting") or [])]
        self._refresh_simple_app_tv("setting")

        self._app_output_rows = [dict(r) for r in (snap.get("output") or [])]
        self._refresh_simple_app_tv("output")

        self._app_conf_rows = [dict(r) for r in (snap.get("conf") or [])]
        self._refresh_simple_app_tv("conf")

        self._app_control_rows = [dict(r) for r in (snap.get("control") or [])]
        self._refresh_simple_app_tv("control")

        try:
            self._on_app_view_changed()
        except Exception:
            pass

    def _app_push_undo(self) -> None:
        if getattr(self, "_app_loading", False):
            return
        if getattr(self, "_app_undoing", False):
            return
        self._app_undo_stack.append(self._app_snapshot_all_rows())
        if len(self._app_undo_stack) > self._app_undo_max:
            self._app_undo_stack = self._app_undo_stack[-self._app_undo_max :]

    def _app_undo(self) -> None:
        if not getattr(self, "_app_undo_stack", None):
            return

        # Close inline editors without committing.
        try:
            self._end_app_input_inline_editor(commit=False)
            self._end_app_setting_inline_editor(commit=False)
            self._end_app_output_inline_editor(commit=False)
            self._end_app_conf_inline_editor(commit=False)
            self._end_app_control_inline_editor(commit=False)
        except Exception:
            pass

        snap = self._app_undo_stack.pop()
        self._app_undoing = True
        try:
            self._app_restore_snapshot(snap)
        finally:
            self._app_undoing = False

    def _refresh_simple_app_tv(self, table: str) -> None:
        tv = self._app_table_tv(table)
        if tv is None:
            return
        self._clear_tv(tv)
        rows = self._app_table_rows(table)
        cols = list(tv["columns"])
        added_names = set(self._app_sync_added_names.get(table, set()))
        changed_names = set((getattr(self, "_app_changed_names", {}) or {}).get(table, set()))
        for idx, row in enumerate(rows):
            values: list[str] = []
            for c in cols:
                if table == "output" and c in {"persist", "faultlog"}:
                    on = (row.get(c) or "").lower() == "true"
                    values.append("☑" if on else "☐")
                else:
                    values.append(row.get(c) or "")
            nm = (row.get("name") or "").strip()
            tags: tuple[str, ...]
            if bool(row.get("__ui_deleted")):
                tags = ("removed",)
            elif bool(row.get("__ui_added")) or (nm in added_names):
                tags = ("added",)
            elif nm in changed_names:
                tags = ("changed",)
            else:
                tags = ()
            tv.insert("", "end", iid=str(idx), values=values, tags=tags)

        # Append removed snapshots (non-editable)
        removed = list(self._app_sync_removed_snapshots.get(table, []))
        for i, snap in enumerate(removed):
            values: list[str] = []
            for c in cols:
                if table == "output" and c in {"persist", "faultlog"}:
                    on = (snap.get(c) or "").lower() == "true"
                    values.append("☑" if on else "☐")
                else:
                    values.append(snap.get(c) or "")
            tv.insert("", "end", iid=f"__removed_{table}_{i}", values=values, tags=("removed",))

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
        nm = (row.get("name") or "").strip()
        added_names = set(self._app_sync_added_names.get(table, set()))
        changed_names = set((getattr(self, "_app_changed_names", {}) or {}).get(table, set()))
        if bool(row.get("__ui_deleted")):
            tags: tuple[str, ...] = ("removed",)
        elif bool(row.get("__ui_added")) or (nm in added_names):
            tags = ("added",)
        elif nm in changed_names:
            tags = ("changed",)
        else:
            tags = ()
        tv.item(iid, values=values, tags=tags)

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
        self._app_push_undo()
        r = self._app_table_blank_row(table)
        r["__ui_added"] = "1"
        r.pop("__ui_deleted", None)
        rows.insert(insert_at, r)
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
        self._app_push_undo()
        r = self._app_table_blank_row(table)
        r["__ui_added"] = "1"
        r.pop("__ui_deleted", None)
        rows.insert(insert_at, r)
        self._app_table_set_rows(table, rows)
        tv = self._app_table_tv(table)
        if tv is not None:
            try:
                tv.selection_set(str(insert_at))
            except Exception:
                pass

    def _app_table_edit(self, table: str) -> None:
        if not table:
            return

        # Commit any open inline editors first.
        try:
            self._end_app_input_inline_editor(commit=True)
            self._end_app_setting_inline_editor(commit=True)
            self._end_app_output_inline_editor(commit=True)
            self._end_app_conf_inline_editor(commit=True)
            self._end_app_control_inline_editor(commit=True)
        except Exception:
            pass

        if table == "input":
            self._edit_selected_app_input()
            return

        idx = self._app_table_selected_index(table)
        rows = self._app_table_rows(table)
        if idx is None or idx < 0 or idx >= len(rows):
            return

        try:
            if bool(rows[idx].get("__ui_deleted")):
                return
        except Exception:
            pass

        current = dict(rows[idx])

        if table == "output":
            dlg = _EditApplicationOutputDialog(
                self,
                title="Edit output",
                output_types=self._get_app_output_types(),
                initial=current,
            )
            res = dlg.show()
            if not res:
                return
            current.update(res)
        elif table == "setting":
            dlg = _EditApplicationSimpleDialog(
                self,
                title="Edit setting",
                type_values=self._get_app_setting_types(),
                initial=current,
            )
            res = dlg.show()
            if not res:
                return
            current.update(res)
        elif table == "conf":
            dlg = _EditApplicationSimpleDialog(
                self,
                title="Edit conf",
                type_values=self._get_app_conf_types(),
                initial=current,
            )
            res = dlg.show()
            if not res:
                return
            current.update(res)
        elif table == "control":
            dlg = _EditApplicationSimpleDialog(
                self,
                title="Edit control",
                type_values=self._get_app_control_types(),
                initial=current,
            )
            res = dlg.show()
            if not res:
                return
            current.update(res)
        else:
            return

        new_rows = [dict(r) for r in rows]
        self._app_push_undo()
        new_rows[idx] = current
        self._app_table_set_rows(table, new_rows)

        tv = self._app_table_tv(table)
        if tv is not None:
            try:
                tv.selection_set(str(idx))
            except Exception:
                pass

    def _app_table_copy(self, table: str) -> None:
        if not table:
            return
        idx = self._app_table_selected_index(table)
        rows = self._app_table_rows(table)
        if idx is None or idx < 0 or idx >= len(rows):
            return
        try:
            if bool(rows[idx].get("__ui_deleted")):
                return
        except Exception:
            pass
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
        self._app_push_undo()
        new_row = dict(clip)
        new_row["__ui_added"] = "1"
        new_row.pop("__ui_deleted", None)
        rows.insert(insert_at, new_row)
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
        try:
            if bool(rows[idx].get("__ui_deleted")):
                return
        except Exception:
            pass
        self._app_push_undo()
        # Added-then-deleted before save: cancel the addition (no red removed state).
        try:
            if bool(rows[idx].get("__ui_added")):
                rows.pop(idx)
                self._app_table_set_rows(table, rows)
                tv = self._app_table_tv(table)
                if tv is not None and rows:
                    sel = min(idx, len(rows) - 1)
                    try:
                        tv.selection_set(str(sel))
                    except Exception:
                        pass
                return
        except Exception:
            pass
        rows[idx]["__ui_deleted"] = "1"
        try:
            rows[idx].pop("__ui_added", None)
        except Exception:
            pass
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
        try:
            if 0 <= idx < len(rows) and bool(rows[idx].get("__ui_deleted")):
                return
        except Exception:
            pass
        j = idx + delta
        if j < 0 or j >= len(rows):
            return
        self._app_push_undo()
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
            cb.bind("<Control-z>", lambda _e: (self._app_undo(), "break")[1])
            cb.bind("<Control-Z>", lambda _e: (self._app_undo(), "break")[1])
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
            ent.bind("<Control-z>", lambda _e: (self._app_undo(), "break")[1])
            ent.bind("<Control-Z>", lambda _e: (self._app_undo(), "break")[1])
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
            cb.bind("<Control-z>", lambda _e: (self._app_undo(), "break")[1])
            cb.bind("<Control-Z>", lambda _e: (self._app_undo(), "break")[1])
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
            ent.bind("<Control-z>", lambda _e: (self._app_undo(), "break")[1])
            ent.bind("<Control-Z>", lambda _e: (self._app_undo(), "break")[1])
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
            cb.bind("<Control-z>", lambda _e: (self._app_undo(), "break")[1])
            cb.bind("<Control-Z>", lambda _e: (self._app_undo(), "break")[1])
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
            ent.bind("<Control-z>", lambda _e: (self._app_undo(), "break")[1])
            ent.bind("<Control-Z>", lambda _e: (self._app_undo(), "break")[1])
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
            cb.bind("<Control-z>", lambda _e: (self._app_undo(), "break")[1])
            cb.bind("<Control-Z>", lambda _e: (self._app_undo(), "break")[1])
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
            ent.bind("<Control-z>", lambda _e: (self._app_undo(), "break")[1])
            ent.bind("<Control-Z>", lambda _e: (self._app_undo(), "break")[1])
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
            cb.bind("<Control-z>", lambda _e: (self._app_undo(), "break")[1])
            cb.bind("<Control-Z>", lambda _e: (self._app_undo(), "break")[1])
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
            ent.bind("<Control-z>", lambda _e: (self._app_undo(), "break")[1])
            ent.bind("<Control-Z>", lambda _e: (self._app_undo(), "break")[1])
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

        final_value = new_value.strip() if key in {"name", "type"} else new_value
        if (row.get(key) or "") == final_value:
            try:
                w.destroy()
            except Exception:
                pass
            return
        self._app_push_undo()
        row[key] = final_value
        self._update_app_input_tv_row(iid)

        try:
            w.destroy()
        except Exception:
            pass

        try:
            self._on_app_view_changed()
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

        final_value = new_value.strip() if key in {"name", "type"} else new_value
        if (row.get(key) or "") == final_value:
            try:
                w.destroy()
            except Exception:
                pass
            return
        self._app_push_undo()
        row[key] = final_value
        self._update_simple_app_tv_row("setting", iid)

        try:
            w.destroy()
        except Exception:
            pass

        try:
            self._on_app_view_changed()
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

        final_value = new_value.strip() if key in {"name", "type", "doRef", "MaxContiguous", "Overlap"} else new_value
        if (row.get(key) or "") == final_value:
            try:
                w.destroy()
            except Exception:
                pass
            return
        self._app_push_undo()
        row[key] = final_value
        self._update_simple_app_tv_row("output", iid)

        try:
            w.destroy()
        except Exception:
            pass

        try:
            self._on_app_view_changed()
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

        final_value = new_value.strip() if key in {"name", "type"} else new_value
        if (row.get(key) or "") == final_value:
            try:
                w.destroy()
            except Exception:
                pass
            return
        self._app_push_undo()
        row[key] = final_value
        self._update_simple_app_tv_row("conf", iid)

        try:
            w.destroy()
        except Exception:
            pass

        try:
            self._on_app_view_changed()
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

        final_value = new_value.strip() if key in {"name", "type"} else new_value
        if (row.get(key) or "") == final_value:
            try:
                w.destroy()
            except Exception:
                pass
            return
        self._app_push_undo()
        row[key] = final_value
        self._update_simple_app_tv_row("control", iid)

        try:
            w.destroy()
        except Exception:
            pass

        try:
            self._on_app_view_changed()
        except Exception:
            pass

        return

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

        self._app_loading = True
        try:
            self._clear_app_refresh_diff_state()
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
        finally:
            self._app_loading = False

        try:
            self._wire_application_funblock_traces()
        except Exception:
            pass

        self._mark_application_unsaved()

        try:
            self._update_app_refresh_button_state()
        except Exception:
            pass

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
        try:
            rel = os.fspath(dst.relative_to(app_dir))
        except Exception:
            rel = os.fspath(dst.name)
        self._refresh_application_search_list(select_rel=rel)

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

        def _rows(tag_name: str, wanted: list[str]) -> list[dict[str, str]]:
            out: list[dict[str, str]] = []
            for ch in list(funblock):
                if not isinstance(ch.tag, str):
                    continue
                if self._local_name(ch.tag) != tag_name:
                    continue
                out.append({k: (ch.attrib.get(k) or "") for k in wanted})
            return out

        self._app_loading = True
        try:
            self._clear_app_refresh_diff_state()
            self._app_file_path = path
            self._app_root = root
            self._app_funblock = funblock

            self._app_undo_stack = []

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
        finally:
            self._app_loading = False

        # Sync search selection if file is under application/.
        try:
            app_dir = self._application_dir()
            rel = os.fspath(Path(path).resolve().relative_to(app_dir.resolve()))
            if self.var_app_selected is not None:
                self.var_app_selected.set(rel)
            self._refresh_application_search_list(select_rel=rel)
        except Exception:
            pass

        self._set_status(f"Opened application: {os.fspath(path)}")

        try:
            self._wire_application_funblock_traces()
        except Exception:
            pass
        self._mark_application_saved()

        try:
            self._update_app_refresh_button_state()
        except Exception:
            pass

    def _update_app_refresh_button_state(self) -> None:
        btn = self.btn_app_refresh
        if btn is None:
            return

        has_app = self._app_root is not None and self._app_funblock is not None
        try:
            btn.configure(state=("normal" if has_app else "disabled"))
        except Exception:
            pass

    def _clear_app_refresh_diff_state(self) -> None:
        for k in list(self._app_sync_added_names):
            self._app_sync_added_names[k] = set()
        for k in list(self._app_sync_removed_snapshots):
            self._app_sync_removed_snapshots[k] = []

    def _current_ln_instance_element(self) -> ET.Element | None:
        if self.instance_editor is None or self.instance_editor.doc is None:
            return None
        doc = self.instance_editor.doc
        if not getattr(doc, "ln_elements", None):
            return None
        try:
            idx = int(getattr(self.instance_editor, "_current_ln_index", 0))
        except Exception:
            idx = 0
        if idx < 0 or idx >= len(doc.ln_elements):
            idx = 0
        try:
            return doc.ln_elements[idx]
        except Exception:
            return doc.ln_elements[0] if doc.ln_elements else None

    def _lnref_from_application(self) -> str:
        # Prefer current UI field (may be edited), fallback to loaded XML.
        try:
            if self.instance_editor is not None:
                v = (self.instance_editor.var_app_LnRef.get() or "").strip()
                if v:
                    return v
        except Exception:
            pass
        try:
            if self._app_funblock is not None:
                return (self._app_funblock.attrib.get("LnRef") or "").strip()
        except Exception:
            pass
        return ""

    def _normalize_lnref(self, lnref: str) -> str:
        """Normalize Application LnRef to match LN instance naming.

        In this project LnRef sometimes carries a trailing '#', e.g. 'ZNPDIS#'.
        We strip suffixes after '#', and trim trailing separators.
        """
        s = (lnref or "").strip()
        if not s:
            return ""
        # Common case: 'ZNPDIS#' or 'ZNPDIS#something'
        if "#" in s:
            s = s.split("#", 1)[0]
        s = s.strip()
        # Trim any remaining trailing separators
        s = s.rstrip("#.;:,_- ")
        return s.strip()

    def _guess_ln_instance_path_from_lnref(self, lnref: str) -> Path | None:
        """Find best matching LN instance file in lndm/ using Application LnRef.

        LnRef is typically prefix+lnClass (e.g. ZNPDIS). We score candidates by:
        - exact match on prefix+lnClass
        - filename stem contains LnRef
        """
        lnref_raw = (lnref or "").strip()
        lnref = self._normalize_lnref(lnref_raw)
        if not lnref:
            return None

        # Small cache to avoid rescanning on repeated refresh.
        cache = getattr(self, "_lnref_to_lndm_path_cache", None)
        if cache is None:
            cache = {}
            setattr(self, "_lnref_to_lndm_path_cache", cache)
        cache_key = lnref
        if cache_key in cache:
            p = cache[cache_key]
            return Path(p) if p else None

        lndm_dir = self._lndm_dir()
        if not lndm_dir.exists():
            cache[cache_key] = None
            return None

        # Heuristic parse: last 4 chars are lnClass for most IEC LNs.
        ln_class_guess = lnref[-4:] if len(lnref) >= 5 else ""
        prefix_guess = lnref[: -4] if ln_class_guess else ""

        candidates: list[Path] = []
        preferred = lndm_dir / f"{lnref}.xml"
        if preferred.exists():
            candidates.append(preferred)

        # Consider nearby matching stems first.
        try:
            for p in lndm_dir.glob(f"*{lnref}*.xml"):
                if p.is_file():
                    candidates.append(p)
        except Exception:
            pass

        # Fall back to full list (bounded)
        if len(candidates) < 50:
            try:
                rels = self._scan_xml_relpaths(lndm_dir)
            except Exception:
                rels = []
            for rel in rels[:2000]:
                p = lndm_dir / rel
                if p.is_file():
                    candidates.append(p)

        # De-dup keep order
        seen: set[str] = set()
        uniq: list[Path] = []
        for p in candidates:
            sp = os.fspath(p)
            if sp in seen:
                continue
            seen.add(sp)
            uniq.append(p)
        candidates = uniq[:300]

        best: tuple[int, Path] | None = None
        for p in candidates:
            score = 0
            try:
                if p.stem.lower() == lnref.lower():
                    score += 50
                elif lnref.lower() in p.stem.lower():
                    score += 10
            except Exception:
                pass

            try:
                prefix, ln_class = self._afg_peek_ln_prefix_and_class(p)
                if (prefix + ln_class).strip().lower() == lnref.lower():
                    score += 200
                if prefix_guess and (prefix or "").strip().lower() == prefix_guess.lower():
                    score += 20
                if ln_class_guess and (ln_class or "").strip().lower() == ln_class_guess.lower():
                    score += 20
            except Exception:
                pass

            if best is None or score > best[0]:
                best = (score, p)

        out = best[1] if best is not None and best[0] > 0 else (preferred if preferred.exists() else None)
        cache[cache_key] = os.fspath(out) if out else None
        return out

    def _pick_ln_element_for_lnref(self, doc, lnref: str) -> ET.Element | None:
        try:
            els = list(getattr(doc, "ln_elements", []) or [])
        except Exception:
            els = []
        if not els:
            return None
        lnref = self._normalize_lnref(lnref).strip().lower()
        if not lnref:
            return els[0]

        def concat(el: ET.Element) -> str:
            try:
                return ((el.attrib.get("prefix") or "") + (el.attrib.get("lnClass") or "")).strip().lower()
            except Exception:
                return ""

        for el in els:
            if concat(el) == lnref:
                return el
        return els[0]

    def _extract_inrefs_from_ln_element(self, ln_el: ET.Element) -> list[dict[str, str]]:
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
        for doi in list(ln_el):
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

    def _app_sync_reconcile(
        self,
        *,
        table: str,
        current_rows: list[dict[str, str]],
        desired_rows: list[dict[str, str]],
        update_fields_from_desired: set[str],
    ) -> tuple[list[dict[str, str]], set[str], list[dict[str, str]]]:
        def key_of(row: dict[str, str]) -> str:
            return (row.get("name") or "").strip()

        # Important UX rule (Application Refresh): do NOT reorder existing rows.
        # Keep current order for keys that still exist; only append newly added keys at the end.
        desired_order: list[str] = []
        desired_by_key: dict[str, dict[str, str]] = {}
        for r in (desired_rows or []):
            k = key_of(r)
            if not k:
                continue
            if k not in desired_by_key:
                desired_order.append(k)
            desired_by_key[k] = dict(r)

        desired_set = set(desired_by_key.keys())

        seen_current: set[str] = set()
        new_rows: list[dict[str, str]] = []
        removed: list[dict[str, str]] = []

        # Keep current rows in current order.
        for r in (current_rows or []):
            k = key_of(r)
            if not k:
                continue
            if k in seen_current:
                # Duplicate keys are treated as removed snapshots.
                removed.append(dict(r))
                continue
            seen_current.add(k)

            if k in desired_set:
                want = desired_by_key.get(k, {})
                merged = dict(r)
                for f in update_fields_from_desired:
                    if f in want:
                        merged[f] = want.get(f) or ""
                new_rows.append(merged)
            else:
                # No longer desired.
                removed.append(dict(r))

        # Append newly-added desired rows (in desired order) to the end.
        added: set[str] = set()
        for k in desired_order:
            if k in seen_current:
                continue
            want = desired_by_key.get(k)
            if not want:
                continue
            new_rows.append(dict(want))
            added.add(k)

        return (new_rows, added, removed)

    def _app_sync_add_only(
        self,
        *,
        table: str,
        current_rows: list[dict[str, str]],
        desired_rows: list[dict[str, str]],
        update_fields_from_desired: set[str],
    ) -> tuple[list[dict[str, str]], set[str], list[dict[str, str]]]:
        """Application Refresh helper (add-only).

        Keeps all existing rows (no auto-delete), updates selected fields for
        rows that still exist in the desired set, and appends newly desired keys.

        Returns (new_rows, added_keys, removed_snapshots). removed_snapshots is
        always empty for add-only refresh.
        """

        def key_of(row: dict[str, str]) -> str:
            return (row.get("name") or "").strip()

        desired_order: list[str] = []
        desired_by_key: dict[str, dict[str, str]] = {}
        for r in (desired_rows or []):
            k = key_of(r)
            if not k:
                continue
            if k not in desired_by_key:
                desired_order.append(k)
            desired_by_key[k] = dict(r)

        # Keep all current rows, in current order.
        seen_current: set[str] = set()
        new_rows: list[dict[str, str]] = []
        for r in (current_rows or []):
            k = key_of(r)
            if k:
                seen_current.add(k)
            if k and k in desired_by_key:
                want = desired_by_key.get(k, {})
                merged = dict(r)
                for f in update_fields_from_desired:
                    if f in want:
                        merged[f] = want.get(f) or ""
                new_rows.append(merged)
            else:
                new_rows.append(dict(r))

        # Append newly-added desired keys at the end.
        added: set[str] = set()
        for k in desired_order:
            if k in seen_current:
                continue
            want = desired_by_key.get(k)
            if not want:
                continue
            new_rows.append(dict(want))
            added.add(k)

        return (new_rows, added, [])

    def _clear_application_refresh_highlights(self) -> None:
        """Clear Application Refresh diff highlights and removed snapshot rows from the UI."""
        try:
            self._clear_app_refresh_diff_state()
        except Exception:
            return
        try:
            self._refresh_app_input_tv()
        except Exception:
            pass
        for t in ("setting", "output", "conf", "control"):
            try:
                self._refresh_simple_app_tv(t)
            except Exception:
                pass

    def _refresh_application_from_latest_ln_instance(self) -> None:
        # Enabled state should prevent this, but keep it safe.
        if self._app_root is None or self._app_funblock is None:
            messagebox.showerror("Missing", "Open an application file first.", parent=self)
            return

        # Commit any open inline editor so current rows are up-to-date.
        try:
            self._end_app_input_inline_editor(commit=True)
            self._end_app_setting_inline_editor(commit=True)
            self._end_app_output_inline_editor(commit=True)
            self._end_app_conf_inline_editor(commit=True)
            self._end_app_control_inline_editor(commit=True)
        except Exception:
            pass

        sig_before: str | None
        try:
            sig_before = self._application_signature_from_view()
        except Exception:
            sig_before = None

        # Undo snapshot (Ctrl+Z): capture the pre-refresh view state.
        # We only push it onto the undo stack if the refresh results in real changes.
        undo_snap: dict[str, object] | None
        try:
            undo_snap = self._app_snapshot_all_rows()
        except Exception:
            undo_snap = None

        # Preserve settings that are derived from input pins marked confpin.
        # Example: AZnDis has inputs like ChrAng/StartMod that should be kept as settings.
        preserve_setting_names: set[str] = set()
        try:
            for r in (self._app_input_rows or []):
                if (r.get("confpin") or "").lower() == "true":
                    nm = (r.get("name") or "").strip()
                    if nm:
                        preserve_setting_names.add(nm)
        except Exception:
            preserve_setting_names = set()

        # Use currently-open LN instance if available; otherwise resolve via Application LnRef.
        ln_el: ET.Element | None = None
        ln_source_path: Path | None = None
        if self.instance_editor is not None and self.instance_editor.doc is not None:
            ln_el = self._current_ln_instance_element()
        else:
            lnref = self._lnref_from_application()
            if not lnref:
                messagebox.showerror("Missing", "LnRef is required to locate the LN instance.", parent=self)
                return
            lnref_norm = self._normalize_lnref(lnref)
            ln_source_path = self._guess_ln_instance_path_from_lnref(lnref)
            if ln_source_path is None or not ln_source_path.exists():
                messagebox.showerror(
                    "Missing",
                    f"No matching LN instance file found under:\n\n{os.fspath(self._lndm_dir())}\n\nLnRef: {lnref}",
                    parent=self,
                )
                return
            try:
                doc = load_ln_instance_document(Path(ln_source_path))
            except Exception as e:
                messagebox.showerror("Open failed", str(e), parent=self)
                return
            ln_el = self._pick_ln_element_for_lnref(doc, lnref_norm)

        if ln_el is None:
            messagebox.showerror("Missing", "No LN found for refresh.", parent=self)
            return

        ln_type_id = (ln_el.attrib.get("lnType") or "").strip()

        # Desired rows from latest LN instance
        inrefs = self._extract_inrefs_from_ln_element(ln_el)
        desired_inputs: list[dict[str, str]] = []
        for it in inrefs:
            purpose = (it.get("purpose_clean") or "").strip()
            seq = (it.get("seq") or "1").strip()
            name = purpose if purpose else f"input{seq}"
            do_ref = f".InRef%{purpose}" if purpose else ""
            desired_inputs.append(
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

        desired_setting = self._build_setting_rows_from_lntype(ln_type_id)
        desired_output = self._build_output_rows_from_lntype(ln_type_id)
        desired_control = self._build_control_rows_from_lntype(ln_type_id)

        # Keep user-managed conf table unchanged by refresh.

        # Extend desired settings with any existing settings whose names correspond to confpin inputs.
        desired_setting_names = {(r.get("name") or "").strip() for r in (desired_setting or []) if (r.get("name") or "").strip()}
        extra_settings: list[dict[str, str]] = []
        if preserve_setting_names:
            for r in (self._app_setting_rows or []):
                nm = (r.get("name") or "").strip()
                if not nm:
                    continue
                if nm not in preserve_setting_names:
                    continue
                if nm in desired_setting_names:
                    continue
                extra_settings.append(dict(r))
        if extra_settings:
            desired_setting = list(desired_setting) + extra_settings

        self._app_loading = True
        try:
            # Clear previous diff state
            self._clear_app_refresh_diff_state()

            # input
            new_in, added_in, _removed_in = self._app_sync_add_only(
                table="input",
                current_rows=list(self._app_input_rows),
                desired_rows=desired_inputs,
                update_fields_from_desired={"doRef"},
            )
            self._app_sync_added_names["input"] = set(added_in)
            self._app_sync_removed_snapshots["input"] = []
            self._set_app_input_rows(new_in)

            # setting
            new_set, added_set, _removed_set = self._app_sync_add_only(
                table="setting",
                current_rows=list(self._app_setting_rows),
                desired_rows=desired_setting,
                update_fields_from_desired={"type", "src"},
            )
            self._app_sync_added_names["setting"] = set(added_set)
            self._app_sync_removed_snapshots["setting"] = []
            self._app_table_set_rows("setting", new_set)

            # output
            new_out, added_out, _removed_out = self._app_sync_add_only(
                table="output",
                current_rows=list(self._app_output_rows),
                desired_rows=desired_output,
                update_fields_from_desired={"type", "doRef"},
            )
            self._app_sync_added_names["output"] = set(added_out)
            self._app_sync_removed_snapshots["output"] = []
            self._app_table_set_rows("output", new_out)

            # control
            new_ctl, added_ctl, _removed_ctl = self._app_sync_add_only(
                table="control",
                current_rows=list(self._app_control_rows),
                desired_rows=desired_control,
                update_fields_from_desired={"type", "src"},
            )
            self._app_sync_added_names["control"] = set(added_ctl)
            self._app_sync_removed_snapshots["control"] = []
            self._app_table_set_rows("control", new_ctl)

            # Keep in-memory XML aligned with the refreshed view (but do not write to disk).
            try:
                self._apply_funblock_fields_to_xml()
                self._apply_app_input_rows_to_xml()
                self._apply_simple_app_rows_to_xml(
                    tag_local="output",
                    rows=self._app_output_rows,
                    attr_order=[
                        "name",
                        "type",
                        "desc",
                        "outPurpose",
                        "srvRef",
                        "persist",
                        "doRef",
                        "MaxContiguous",
                        "Overlap",
                        "faultlog",
                    ],
                )
                self._apply_simple_app_rows_to_xml(
                    tag_local="setting",
                    rows=self._app_setting_rows,
                    attr_order=["name", "type", "desc", "src"],
                )
                self._apply_simple_app_rows_to_xml(
                    tag_local="conf",
                    rows=self._app_conf_rows,
                    attr_order=["name", "type", "desc", "src"],
                )
                self._apply_simple_app_rows_to_xml(
                    tag_local="control",
                    rows=self._app_control_rows,
                    attr_order=["name", "type", "desc", "src"],
                )
            except Exception:
                pass
        finally:
            self._app_loading = False

        # Refresh should not mark the Application dirty if it results in no actual changes.
        changed = True
        if sig_before is not None:
            try:
                changed = (self._application_signature_from_view() != sig_before)
            except Exception:
                changed = True

        try:
            self._on_app_view_changed()
        except Exception:
            pass

        if changed:
            if undo_snap is not None and not getattr(self, "_app_undoing", False):
                self._app_undo_stack.append(undo_snap)
                if len(self._app_undo_stack) > self._app_undo_max:
                    self._app_undo_stack = self._app_undo_stack[-self._app_undo_max :]
            if ln_source_path is not None:
                self._set_status(f"Application refreshed from LN instance: {os.fspath(ln_source_path)} (unsaved)")
            else:
                self._set_status("Application refreshed from latest LN instance (unsaved)")
        else:
            self._set_status("Application refresh: no changes")

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
        try:
            app_dir = self._application_dir()
            rel = os.fspath(target_path.relative_to(app_dir))
        except Exception:
            rel = os.fspath(target_path.name)
        self._refresh_application_search_list(select_rel=rel)
        self._set_status(f"Saved application: {os.fspath(target_path)}")

        self._mark_application_saved()
        try:
            self._clear_application_refresh_highlights()
        except Exception:
            pass

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
        try:
            app_dir = self._application_dir()
            rel = os.fspath(path.relative_to(app_dir))
        except Exception:
            rel = os.fspath(path.name)
        self._refresh_application_search_list(select_rel=rel)

        self._set_status(f"Saved application as: {os.fspath(path)}")

        self._mark_application_saved()
        try:
            self._clear_application_refresh_highlights()
        except Exception:
            pass

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
