from __future__ import annotations

import os
import hashlib
import json
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
from ui.hmi_tab import HmiTab


APP_TITLE = "DBMEditor"


SCL_NS = "http://www.iec.ch/61850/2003/SCL"

# PowerLogic HMI customization namespace (HMI template files).
HMI_CUST_NS = "http://www.schneider-electric.com/PowerLogic/HmiCustomization"

# XML Schema Instance namespace (for xsi:schemaLocation on HMI/SCL roots).
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"


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


class _NewHmiChoiceDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc):
        super().__init__(parent)
        self.title("New HMI")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self._result: str | None = None

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="Create a new HMI file:").pack(anchor="w")

        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(12, 0))

        ttk.Button(btns, text="Create from application", command=lambda: self._set("from_application")).pack(
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
        source_relpaths: list[str] | None = None,
        source_base_dir: Path | None = None,
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
        self._source_relpaths = list(source_relpaths or [])
        self._source_base_dir = Path(source_base_dir) if source_base_dir is not None else None
        self._source_blank = "(Blank)"
        self._source_values = [self._source_blank] + self._source_relpaths

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text="Create from").grid(row=0, column=0, sticky="w")
        self.var_source_filter = tk.StringVar(value="")
        self.var_source = tk.StringVar(value=self._source_blank)

        source_filter_row = ttk.Frame(frm)
        source_filter_row.grid(row=0, column=1, sticky="we")
        source_filter_row.columnconfigure(1, weight=1)
        ttk.Label(source_filter_row, text="Filter").grid(row=0, column=0, sticky="w")
        ent_filter = ttk.Entry(source_filter_row, textvariable=self.var_source_filter)
        ent_filter.grid(row=0, column=1, sticky="we", padx=(8, 0))

        self.cb_source = ttk.Combobox(frm, textvariable=self.var_source, values=self._source_values, width=48)
        self.cb_source.grid(row=1, column=1, sticky="we", padx=(8, 0), pady=(6, 0))
        ttk.Label(frm, text="").grid(row=1, column=0)

        ttk.Label(frm, text="File name").grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.var_name = tk.StringVar(value=initial_name)
        ent_name = ttk.Entry(frm, textvariable=self.var_name, width=48)
        ent_name.grid(row=2, column=1, sticky="we", padx=(8, 0), pady=(8, 0))

        ttk.Label(frm, text="proxyName:").grid(row=3, column=0, sticky="w", pady=(8, 0))
        self.var_proxy = tk.StringVar(value=initial_proxy)
        ttk.Entry(frm, textvariable=self.var_proxy, width=48).grid(row=3, column=1, sticky="we", padx=(8, 0), pady=(8, 0))

        ttk.Label(frm, text="chapterName:").grid(row=4, column=0, sticky="w", pady=(8, 0))
        self.var_chapter = tk.StringVar(value=initial_chapter)
        ttk.Entry(frm, textvariable=self.var_chapter, width=48).grid(row=4, column=1, sticky="we", padx=(8, 0), pady=(8, 0))

        ttk.Label(frm, text="topicName:").grid(row=5, column=0, sticky="w", pady=(8, 0))
        self.var_topic = tk.StringVar(value=initial_topic)
        ttk.Entry(frm, textvariable=self.var_topic, width=48).grid(row=5, column=1, sticky="we", padx=(8, 0), pady=(8, 0))

        btns = ttk.Frame(frm)
        btns.grid(row=6, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Cancel", command=self._cancel).pack(side="right")
        ttk.Button(btns, text="OK", command=self._ok).pack(side="right", padx=(0, 8))

        def apply_source_filter(*_args) -> None:
            raw = (self.var_source_filter.get() or "").strip().lower()
            if not raw:
                filtered = list(self._source_values)
            else:
                tokens = [t for t in raw.split() if t]

                def ok(v: str) -> bool:
                    lv = (v or "").lower()
                    return all(t in lv for t in tokens)

                filtered = [x for x in self._source_values if ok(x)]

            cur = (self.var_source.get() or "").strip()
            self.cb_source["values"] = filtered[:2000]
            if raw:
                if filtered:
                    self.var_source.set(filtered[0])
                return
            if filtered and cur not in filtered:
                self.var_source.set(filtered[0])

        def on_source_change(*_args) -> None:
            src_rel = (self.var_source.get() or "").strip()
            if not src_rel or src_rel == self._source_blank:
                return
            if self._source_base_dir is None:
                return
            try:
                src_path = self._source_base_dir / src_rel
                tree = ET.parse(src_path)
                root = tree.getroot()
                if not (isinstance(root.tag, str) and _local_name(root.tag) == "AfgDiagramXml"):
                    return
                if not (self.var_proxy.get() or "").strip():
                    self.var_proxy.set(root.attrib.get("proxyName") or "")
                if not (self.var_chapter.get() or "").strip():
                    self.var_chapter.set(root.attrib.get("chapterName") or "")
                if not (self.var_topic.get() or "").strip():
                    self.var_topic.set(root.attrib.get("topicName") or "")
            except Exception:
                pass

        self.var_source_filter.trace_add("write", apply_source_filter)
        self.var_source.trace_add("write", on_source_change)
        apply_source_filter()

        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Return>", lambda _e: self._ok())
        self.bind("<Control-f>", lambda _e: ent_filter.focus_set())

        try:
            ent_filter.focus_set()
            ent_name.select_range(0, tk.END)
        except Exception:
            pass

    def _ok(self) -> None:
        name = (self.var_name.get() or "").strip()
        if not name:
            messagebox.showerror("Missing", "AFG name is required", parent=self)
            return
        self._result = {
            "source_rel": (self.var_source.get() or "").strip(),
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
        lang_ref: str,
        data_type: str,
        view_type: str,
        sub_tree_type: str,
        data_type_values: list[str] | None = None,
        view_type_values: list[str] | None = None,
    ):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self._result: dict[str, str] | None = None
        self.var_name = tk.StringVar(value=name or "")
        self.var_desc = tk.StringVar(value=desc or "")
        self.var_lang = tk.StringVar(value=lang_ref or "")
        self.var_data = tk.StringVar(value=data_type or "")
        self.var_view = tk.StringVar(value=view_type or "")
        self.var_subtree = tk.StringVar(value=sub_tree_type or "")

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text="name").grid(row=0, column=0, sticky="w")
        ent_name = ttk.Entry(frm, textvariable=self.var_name, width=54)
        ent_name.grid(row=0, column=1, sticky="we", padx=(8, 0))

        ttk.Label(frm, text="desc").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(frm, textvariable=self.var_desc, width=54).grid(row=1, column=1, sticky="we", padx=(8, 0), pady=(8, 0))

        ttk.Label(frm, text="langRef").grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(frm, textvariable=self.var_lang, width=54).grid(row=2, column=1, sticky="we", padx=(8, 0), pady=(8, 0))

        ttk.Label(frm, text="hmiMenuDataType").grid(row=3, column=0, sticky="w", pady=(8, 0))
        if data_type_values:
            cb_data = ttk.Combobox(frm, textvariable=self.var_data, width=52, state="readonly", values=data_type_values)
            cb_data.grid(row=3, column=1, sticky="we", padx=(8, 0), pady=(8, 0))
        else:
            ttk.Entry(frm, textvariable=self.var_data, width=54).grid(
                row=3, column=1, sticky="we", padx=(8, 0), pady=(8, 0)
            )

        ttk.Label(frm, text="hmiMenuViewType").grid(row=4, column=0, sticky="w", pady=(8, 0))
        if view_type_values:
            cb_view = ttk.Combobox(frm, textvariable=self.var_view, width=52, state="readonly", values=view_type_values)
            cb_view.grid(row=4, column=1, sticky="we", padx=(8, 0), pady=(8, 0))
        else:
            ttk.Entry(frm, textvariable=self.var_view, width=54).grid(
                row=4, column=1, sticky="we", padx=(8, 0), pady=(8, 0)
            )

        ttk.Label(frm, text="hmiSubTreeType").grid(row=5, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(frm, textvariable=self.var_subtree, width=54).grid(row=5, column=1, sticky="we", padx=(8, 0), pady=(8, 0))

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
        name = (self.var_name.get() or "").strip()
        if not name:
            messagebox.showerror("Missing", "name is required", parent=self)
            return
        self._result = {
            "name": name,
            "desc": self.var_desc.get() or "",
            "langRef": (self.var_lang.get() or "").strip(),
            "hmiMenuDataType": (self.var_data.get() or "").strip(),
            "hmiMenuViewType": (self.var_view.get() or "").strip(),
            "hmiSubTreeType": (self.var_subtree.get() or "").strip(),
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
        ent_name = ttk.Entry(frm, textvariable=self.var_name, width=60, state="disabled")
        ent_name.grid(row=0, column=1, sticky="we", padx=(8, 0))

        ttk.Label(frm, text="ref (optional)").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ent_ref = ttk.Entry(frm, textvariable=self.var_ref, width=60)
        ent_ref.grid(row=1, column=1, sticky="we", padx=(8, 0), pady=(8, 0))

        ttk.Label(frm, text="doRef").grid(row=2, column=0, sticky="w", pady=(8, 0))
        ent_do = ttk.Entry(frm, textvariable=self.var_do, width=60)
        ent_do.grid(row=2, column=1, sticky="we", padx=(8, 0), pady=(8, 0))

        ttk.Label(frm, text="daRef").grid(row=3, column=0, sticky="w", pady=(8, 0))
        ent_da = ttk.Entry(frm, textvariable=self.var_da, width=60)
        ent_da.grid(row=3, column=1, sticky="we", padx=(8, 0), pady=(8, 0))

        ttk.Label(frm, text="If ref is set, doRef/daRef are ignored.").grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(8, 0)
        )

        def _do_name_from_doref(do_ref: str) -> str:
            txt = (do_ref or "").strip()
            if not txt:
                return ""
            if "." in txt:
                txt = (txt.rsplit(".", 1)[-1] or "").strip()
            if txt.lower().startswith("inref%"):
                txt = txt[len("InRef%") :].strip()
            return txt

        def _sync_name(*_args) -> None:
            ref = (self.var_ref.get() or "").strip()
            if ref:
                try:
                    self.var_name.set("")
                except Exception:
                    pass
                return
            do_ref = (self.var_do.get() or "").strip()
            try:
                self.var_name.set(_do_name_from_doref(do_ref))
            except Exception:
                pass

        try:
            self.var_ref.trace_add("write", _sync_name)
            self.var_do.trace_add("write", _sync_name)
        except Exception:
            pass
        _sync_name()

        btns = ttk.Frame(frm)
        btns.grid(row=5, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Cancel", command=self._cancel).pack(side="right")
        ttk.Button(btns, text="OK", command=self._ok).pack(side="right", padx=(0, 8))

        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Return>", lambda _e: self._ok())
        try:
            ent_da.focus_set()
            ent_da.select_range(0, tk.END)
        except Exception:
            pass

    def _ok(self) -> None:
        ref = (self.var_ref.get() or "").strip()
        do_ref = (self.var_do.get() or "").strip()
        da_ref = (self.var_da.get() or "").strip()
        if not ref:
            if not do_ref:
                messagebox.showerror("Missing", "doRef is required (or set ref)", parent=self)
                return
        if ref:
            name = ""
        else:
            name = do_ref.rsplit(".", 1)[-1].strip() if do_ref else ""
        self._result = {
            "ref": ref,
            "name": name,
            "doRef": do_ref,
            "daRef": da_ref,
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
        ent_name = ttk.Entry(frm, textvariable=self.var_name, width=54, state="disabled")
        ent_name.grid(row=0, column=1, sticky="we", padx=(8, 0))

        ttk.Label(frm, text="doRef").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(frm, textvariable=self.var_do, width=54).grid(row=1, column=1, sticky="we", padx=(8, 0), pady=(8, 0))

        ttk.Label(frm, text="daRef").grid(row=2, column=0, sticky="w", pady=(8, 0))
        ent_da = ttk.Entry(frm, textvariable=self.var_da, width=54)
        ent_da.grid(row=2, column=1, sticky="we", padx=(8, 0), pady=(8, 0))

        def _sync_name(*_args) -> None:
            da_ref = (self.var_da.get() or "").strip()
            if da_ref.startswith("."):
                da_ref = da_ref[1:]
            if "." in da_ref:
                da_ref = (da_ref.rsplit(".", 1)[-1] or "").strip()
            try:
                self.var_name.set(da_ref)
            except Exception:
                pass

        try:
            self.var_da.trace_add("write", _sync_name)
        except Exception:
            pass
        _sync_name()

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
        da_ref = (self.var_da.get() or "").strip()
        if not da_ref:
            messagebox.showerror("Missing", "daRef is required", parent=self)
            return
        name0 = da_ref
        if name0.startswith("."):
            name0 = name0[1:]
        if "." in name0:
            name0 = (name0.rsplit(".", 1)[-1] or "").strip()
        name = name0
        self._result = {
            "name": name,
            "doRef": (self.var_do.get() or "").strip(),
            "daRef": da_ref,
        }
        self.destroy()

    def _cancel(self) -> None:
        self._result = None
        self.destroy()

    def show(self) -> dict[str, str] | None:
        self.wait_window(self)
        return self._result


class _HmiAttrEditDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        title: str,
        name: str,
        value: str,
        value_values: list[str] | None = None,
    ):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self._result: dict[str, str] | None = None
        self.var_name = tk.StringVar(value=name or "")
        self.var_value = tk.StringVar(value=value or "")

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text="name").grid(row=0, column=0, sticky="w")
        ent_name = ttk.Entry(frm, textvariable=self.var_name, width=54)
        ent_name.grid(row=0, column=1, sticky="we", padx=(8, 0))

        ttk.Label(frm, text="value").grid(row=1, column=0, sticky="w", pady=(8, 0))
        if value_values is not None:
            cb_val = ttk.Combobox(frm, textvariable=self.var_value, width=54, state="readonly", values=value_values)
            cb_val.grid(row=1, column=1, sticky="we", padx=(8, 0), pady=(8, 0))
        else:
            ent_val = ttk.Entry(frm, textvariable=self.var_value, width=54)
            ent_val.grid(row=1, column=1, sticky="we", padx=(8, 0), pady=(8, 0))

        btns = ttk.Frame(frm)
        btns.grid(row=2, column=0, columnspan=2, sticky="e", pady=(12, 0))
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
            "value": self.var_value.get() or "",
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
            self.cb["values"] = filtered[:2000]
            if raw:
                if filtered:
                    self.var_value.set(filtered[0])
                return
            if filtered and cur not in filtered:
                self.var_value.set(filtered[0])

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
            self.cb_ln["values"] = filtered[:2000]
            if raw:
                if filtered:
                    self.var_ln.set(filtered[0])
                return
            if filtered and cur not in filtered:
                self.var_ln.set(filtered[0])

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
    def __init__(
        self,
        parent: tk.Misc,
        *,
        app_dir: Path,
        items: list[str],
        title: str = "Copy application",
        source_label: str = "Source file",
        blank_option: str = "",
    ):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self._app_dir = Path(app_dir)
        self._blank_option = (blank_option or "").strip()
        self._source_items = list(items)
        self._items_all = ([self._blank_option] if self._blank_option else []) + list(items)
        self._result: dict[str, str] | None = None
        self._last_auto_new_name = ""

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text=source_label).grid(row=0, column=0, sticky="w", pady=4)
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

        ttk.Label(frm, text="File name").grid(row=2, column=0, sticky="w", pady=4)
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
            self.cb_src["values"] = filtered[:2000]
            if raw:
                if filtered:
                    self.var_src.set(filtered[0])
                return
            if filtered and cur not in filtered:
                self.var_src.set(filtered[0])

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
        if src != self._blank_option and src not in self._source_items:
            messagebox.showerror("Invalid", "Source file not found", parent=self)
            return
        if not new_name:
            messagebox.showerror("Missing", "File name is required", parent=self)
            return
        if any(sep in new_name for sep in ("/", "\\")):
            messagebox.showerror("Invalid", "File name must not contain path separators", parent=self)
            return
        if not new_name.lower().endswith(".xml"):
            new_name = new_name + ".xml"

        # Very small Windows-invalid check (keep simple)
        invalid = set('<>:"/\\|?*')
        if any(ch in invalid for ch in new_name):
            messagebox.showerror("Invalid", "File name contains invalid characters", parent=self)
            return

        self._result = {"src_rel": src, "new_name": new_name}
        self.destroy()

    def _cancel(self) -> None:
        self._result = None
        self.destroy()

    def show(self) -> dict[str, str] | None:
        self.wait_window(self)
        return self._result


class _CopyHmiDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        hmi_relpaths: list[str],
        suggested_filename: str = "",
    ):
        super().__init__(parent)
        self.title("Copy existing files")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self._blank_option = "Blank"
        self._relpaths = list(hmi_relpaths)
        self._source_values = [self._blank_option] + self._relpaths
        self._result: dict[str, str] | None = None

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text="Create from").grid(row=0, column=0, sticky="w", pady=4)
        self.var_filter = tk.StringVar(value="")
        filter_row = ttk.Frame(frm)
        filter_row.grid(row=0, column=1, sticky="we", pady=4)
        filter_row.columnconfigure(1, weight=1)
        ttk.Label(filter_row, text="Filter").grid(row=0, column=0, sticky="w")
        ent_filter = ttk.Entry(filter_row, textvariable=self.var_filter)
        ent_filter.grid(row=0, column=1, sticky="we", padx=(8, 0))

        self.var_src = tk.StringVar(value=self._blank_option)
        cb = ttk.Combobox(frm, textvariable=self.var_src, values=self._source_values, width=72)
        cb.grid(row=1, column=1, sticky="we", pady=(0, 4))
        ttk.Label(frm, text="").grid(row=1, column=0)

        ttk.Label(frm, text="File name").grid(row=2, column=0, sticky="w", pady=4)
        self.var_filename = tk.StringVar(value=(suggested_filename or ""))
        ttk.Entry(frm, textvariable=self.var_filename, width=36).grid(row=2, column=1, sticky="w", pady=4)

        btns = ttk.Frame(frm)
        btns.grid(row=3, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Cancel", command=self._cancel).pack(side="right")
        ttk.Button(btns, text="Copy", command=self._ok).pack(side="right", padx=(0, 8))

        def apply_filter(*_args) -> None:
            raw = (self.var_filter.get() or "").strip().lower()
            if not raw:
                filtered = list(self._source_values)
            else:
                tokens = [t for t in raw.split() if t]

                def ok(v: str) -> bool:
                    lv = (v or "").lower()
                    return all(t in lv for t in tokens)

                filtered = [x for x in self._source_values if ok(x)]

            cur = (self.var_src.get() or "").strip()
            cb["values"] = filtered[:2000]
            if raw:
                if filtered:
                    self.var_src.set(filtered[0])
                return
            if filtered and cur not in filtered:
                self.var_src.set(filtered[0])

        self.var_filter.trace_add("write", apply_filter)
        apply_filter()

        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Control-f>", lambda _e: ent_filter.focus_set())
        cb.bind("<Return>", lambda _e: self._ok())

    def _ok(self) -> None:
        src = (self.var_src.get() or "").strip()
        if not src:
            messagebox.showerror("Missing", "Source file is required", parent=self)
            return
        if src != self._blank_option and src not in self._relpaths:
            messagebox.showerror("Invalid", "Source file not found", parent=self)
            return

        filename = (self.var_filename.get() or "").strip()
        if not filename:
            messagebox.showerror("Missing", "File name is required", parent=self)
            return
        if "." not in filename:
            filename = filename + ".xml"
        if any(ch in filename for ch in set('<>:"/\\|?*')):
            messagebox.showerror("Invalid", "File name contains invalid characters", parent=self)
            return

        self._result = {"src_rel": src, "new_name": filename}
        self.destroy()

    def _cancel(self) -> None:
        self._result = None
        self.destroy()

    def show(self) -> dict[str, str] | None:
        self.wait_window(self)
        return self._result


class _CreateHmiFromApplicationDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, *, app_relpaths: list[str]):
        super().__init__(parent)
        self.title("Create HMI from application")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self._app_relpaths = list(app_relpaths)
        self._result: dict[str, str] | None = None

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text="Application file").grid(row=0, column=0, sticky="w", pady=4)
        self.var_filter = tk.StringVar(value="")
        filter_row = ttk.Frame(frm)
        filter_row.grid(row=0, column=1, sticky="we", pady=4)
        filter_row.columnconfigure(1, weight=1)
        ttk.Label(filter_row, text="Filter").grid(row=0, column=0, sticky="w")
        ent_filter = ttk.Entry(filter_row, textvariable=self.var_filter)
        ent_filter.grid(row=0, column=1, sticky="we", padx=(8, 0))

        self.var_app = tk.StringVar(value=(self._app_relpaths[0] if self._app_relpaths else ""))
        self.cb_app = ttk.Combobox(frm, textvariable=self.var_app, values=self._app_relpaths, width=84)
        self.cb_app.grid(row=1, column=1, sticky="we", pady=(0, 8))
        ttk.Label(frm, text="").grid(row=1, column=0)

        btns = ttk.Frame(frm)
        btns.grid(row=2, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Cancel", command=self._cancel).pack(side="right")
        ttk.Button(btns, text="Create", command=self._ok).pack(side="right", padx=(0, 8))

        def apply_filter(*_args) -> None:
            raw = (self.var_filter.get() or "").strip().lower()
            if not raw:
                filtered = list(self._app_relpaths)
            else:
                tokens = [t for t in raw.split() if t]

                def ok(v: str) -> bool:
                    lv = (v or "").lower()
                    return all(t in lv for t in tokens)

                filtered = [x for x in self._app_relpaths if ok(x)]

            cur = (self.var_app.get() or "").strip()
            self.cb_app["values"] = filtered[:2000]

            # When user types in Filter, auto-pick the first matching result.
            if raw:
                if filtered:
                    self.var_app.set(filtered[0])
                return

            # No filter: keep current if valid, otherwise default to first item.
            if filtered and cur not in filtered:
                self.var_app.set(filtered[0])

        self.var_filter.trace_add("write", apply_filter)
        apply_filter()

        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Control-f>", lambda _e: ent_filter.focus_set())
        self.cb_app.bind("<Return>", lambda _e: self._ok())
        ent_filter.focus_set()

    def _ok(self) -> None:
        rel = (self.var_app.get() or "").strip()
        if not rel:
            messagebox.showerror("Missing", "Application file is required", parent=self)
            return
        if rel not in self._app_relpaths:
            messagebox.showerror("Invalid", "Application file not found", parent=self)
            return
        self._result = {"app_rel": rel}
        self.destroy()

    def _cancel(self) -> None:
        self._result = None
        self.destroy()

    def show(self) -> dict[str, str] | None:
        self.wait_window(self)
        return self._result


class _SelectParentMenuDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        menu_name: str,
        parent_names: list[str],
        current_parent: str,
    ):
        super().__init__(parent)
        self.title("Select parent menu")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self._parent_names = list(parent_names)
        self._result: dict[str, str] | None = None

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text="Menu").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Label(frm, text=menu_name).grid(row=0, column=1, sticky="w", pady=4)

        self.var_filter = tk.StringVar(value="")
        ttk.Label(frm, text="Filter").grid(row=1, column=0, sticky="w", pady=4)
        ent_filter = ttk.Entry(frm, textvariable=self.var_filter, width=56)
        ent_filter.grid(row=1, column=1, sticky="we", pady=4)

        vals0 = ["(Top level)"] + self._parent_names
        cur0 = (current_parent or "").strip()
        self.var_parent = tk.StringVar(value=(cur0 if cur0 else "(Top level)"))
        ttk.Label(frm, text="Parent").grid(row=2, column=0, sticky="w", pady=4)
        self.cb_parent = ttk.Combobox(frm, textvariable=self.var_parent, values=vals0, width=54)
        self.cb_parent.grid(row=2, column=1, sticky="we", pady=4)

        btns = ttk.Frame(frm)
        btns.grid(row=3, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Cancel", command=self._cancel).pack(side="right")
        ttk.Button(btns, text="Apply", command=self._ok).pack(side="right", padx=(0, 8))

        def apply_filter(*_args) -> None:
            raw = (self.var_filter.get() or "").strip().lower()
            base = ["(Top level)"]
            if not raw:
                filtered = list(self._parent_names)
            else:
                tokens = [t for t in raw.split() if t]

                def ok(v: str) -> bool:
                    vv = (v or "").lower()
                    return all(t in vv for t in tokens)

                filtered = [v for v in self._parent_names if ok(v)]

            cur = (self.var_parent.get() or "").strip()
            values = base + filtered
            if cur and cur not in values:
                values = [cur] + values
            self.cb_parent["values"] = values[:2000]

        self.var_filter.trace_add("write", apply_filter)
        apply_filter()

        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Control-f>", lambda _e: ent_filter.focus_set())
        self.cb_parent.bind("<Return>", lambda _e: self._ok())
        ent_filter.focus_set()

    def _ok(self) -> None:
        val = (self.var_parent.get() or "").strip()
        if not val:
            messagebox.showerror("Missing", "Parent menu is required", parent=self)
            return
        if val != "(Top level)" and val not in self._parent_names:
            messagebox.showerror("Invalid", "Selected parent menu not found", parent=self)
            return
        self._result = {"parent": ("" if val == "(Top level)" else val)}
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

            # Avoid huge UI lag if matches are extremely large
            max_show = 1500
            shown = filtered[:max_show]
            self.cb["values"] = shown
            suffix = "" if len(filtered) <= max_show else f" (showing first {max_show})"
            self.lbl_match.configure(text=f"{len(filtered)} match{'' if len(filtered)==1 else 'es'}{suffix}")
            if raw:
                if shown:
                    self.var_type.set(shown[0])
                return
            if shown and cur not in shown:
                self.var_type.set(shown[0])

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
            if raw:
                if shown:
                    self.var_type.set(shown[0])
                return
            if shown and cur not in shown:
                self.var_type.set(shown[0])

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

        # UI state persisted across runs (column widths etc.)
        self._ui_state: dict[str, object] = self._load_ui_state()
        self._ui_state_save_after_id: str | None = None
        self._hmi_pref_col_widths: dict[str, int] = {}
        try:
            raw = self._ui_state.get("hmi_column_widths", {}) or {}
            if isinstance(raw, dict):
                for k, v in raw.items():
                    kk = str(k)
                    if not kk:
                        continue
                    try:
                        iv = int(v)
                    except Exception:
                        continue
                    if iv > 0:
                        self._hmi_pref_col_widths[kk] = iv
        except Exception:
            self._hmi_pref_col_widths = {}

        try:
            self.protocol("WM_DELETE_WINDOW", self._on_exit)
        except Exception:
            pass

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
        self.btn_app_create_hmi: ttk.Button | None = None

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
        # Per-node baseline snapshot captured at last save/open (used to auto-clear 'changed' highlight
        # when a single node is reverted back to saved values, regardless of other edits).
        self._hmi_saved_el_sig_by_key: dict[tuple, list[tuple[tuple, int]]] | None = None
        # Track moved elements so we can treat ordering as a meaningful diff (don't clear highlight
        # immediately after move; clear only when moved back to saved position).
        self._hmi_ui_moved_el_ids: set[int] = set()
        self.btn_hmi_save: ttk.Button | None = None
        self.btn_hmi_refresh: ttk.Button | None = None

        # HMI undo (Ctrl+Z): snapshot of HMI XML (+ selection/open UI state).
        self._hmi_undo_stack: list[dict[str, object]] = []
        self._hmi_undo_max = 50
        self._hmi_undoing: bool = False

        # HMI "Search" UI state
        self._all_hmi_files: list[str] = []
        # Dropdown options (collected across all HMI template XML files).
        self._hmi_menu_data_type_values: list[str] = []
        self._hmi_menu_view_type_values: list[str] = []
        self.var_hmi_filter: tk.StringVar | None = None
        self.var_hmi_selected: tk.StringVar | None = None
        self.cb_hmi: ttk.Combobox | None = None
        self.lbl_hmi_match: ttk.Label | None = None

        # HMI treeviews + mappings
        self._hmi_tv_menus: ttk.Treeview | None = None
        self._hmi_tv_menus_ied: ttk.Treeview | None = None
        self._hmi_tv_menus_iet: ttk.Treeview | None = None
        self._hmi_tv_menus_manual: ttk.Treeview | None = None
        self._hmi_tv_items: ttk.Treeview | None = None
        self._hmi_tv_data: ttk.Treeview | None = None
        self._hmi_menu_iid_to_el: dict[str, tuple[ET.Element, ET.Element]] = {}
        self._hmi_item_iid_to_el: dict[str, tuple[ET.Element, ET.Element]] = {}
        self._hmi_data_iid_to_el: dict[str, tuple[ET.Element, ET.Element]] = {}
        self._hmi_tree_iid_to_kind: dict[str, str] = {}
        # HMI UI scope: IED (classic Menu_*), IET (IET_Protection*), Manual (Manual_Protection*)
        self._hmi_scope: str = "ied"
        self._hmi_tree_iid_to_node: dict[str, tuple[str, ET.Element | None, ET.Element | None]] = {}
        # For ref_menu nodes: map submenu iid -> (parent_menu_el, ref_link_item_el)
        self._hmi_tree_iid_to_ref_link: dict[str, tuple[ET.Element, ET.Element]] = {}
        # Keep per-scope menu-tree mappings so tab switching can preserve expand/collapse state.
        self._hmi_menu_iid_to_el_by_scope: dict[str, dict[str, tuple[ET.Element, ET.Element]]] = {
            "ied": {},
            "iet": {},
            "manual": {},
        }
        self._hmi_tree_iid_to_kind_by_scope: dict[str, dict[str, str]] = {
            "ied": {},
            "iet": {},
            "manual": {},
        }
        self._hmi_tree_iid_to_node_by_scope: dict[
            str, dict[str, tuple[str, ET.Element | None, ET.Element | None]]
        ] = {
            "ied": {},
            "iet": {},
            "manual": {},
        }
        self._hmi_tree_iid_to_ref_link_by_scope: dict[str, dict[str, tuple[ET.Element, ET.Element]]] = {
            "ied": {},
            "iet": {},
            "manual": {},
        }
        self._hmi_edit_entry: ttk.Entry | None = None
        self._hmi_edit_iid: str | None = None
        self._hmi_edit_col: str | None = None
        self._hmi_edit_cb: ttk.Combobox | None = None
        self._hmi_edit_cb_iid: str | None = None
        self._hmi_edit_cb_col: str | None = None

        # HMI tree actions (toolbar + context menu)
        self._hmi_btn_add: ttk.Button | None = None
        self._hmi_btn_insert: ttk.Button | None = None
        self._hmi_btn_edit: ttk.Button | None = None
        self._hmi_btn_copy: ttk.Button | None = None
        self._hmi_btn_cut: ttk.Button | None = None
        self._hmi_btn_paste: ttk.Button | None = None
        self._hmi_btn_delete: ttk.Button | None = None
        # Single toggle button (like LN instance): text switches between 'Fold all' and 'Unfold all'.
        self._hmi_btn_fold_all: ttk.Button | None = None
        self._hmi_tree_ctx_menu: tk.Menu | None = None
        # Clipboard payload: (kind, element_copy). kind in {'menu','ref_menu','item'}.
        self._hmi_clipboard: tuple[str, ET.Element] | None = None

        # HMI -> LN instance suggestion state (for doRef dropdowns)
        self._hmi_ln_cached_path: Path | None = None
        self._hmi_ln_cached_mtime: float | None = None
        self._hmi_ln_do_names: list[str] = []
        self._hmi_ln_do_names_source: str = ""  # '', 'instance', 'template'
        self._hmi_ln_cached_lntype_id: str | None = None
        self._hmi_ln_do_types_by_name: dict[str, str] = {}
        self._hmi_ln_da_names_by_dotype: dict[str, list[str]] = {}
        self._hmi_ln_ref: str = ""  # e.g. 'ZNPDIS#'

        # Cache for resolving matching Application LnRef (HMI stem -> application/<stem>.xml)
        self._hmi_app_cached_path: Path | None = None
        self._hmi_app_cached_mtime: float | None = None
        self._hmi_app_cached_lnref: str = ""

        # HMI column resize state
        self._hmi_resize_after_id: str | None = None

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

        # Application + AFG + HMI tabs moved to ui/application_tab.py, ui/afg_tab.py and ui/hmi_tab.py
        self.tab_application = ApplicationTab(self.notebook, owner=self)
        self.notebook.add(self.tab_application, text="Application")
        self.tab_afg = AfgTab(self.notebook, owner=self)
        self.notebook.add(self.tab_afg, text="AFG")

        self.tab_hmi = HmiTab(self.notebook, owner=self)
        self.notebook.add(self.tab_hmi, text="HMI")

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
        # Only apply as a fallback on Application/AFG/HMI tabs.
        try:
            if self.notebook is None:
                return None
            active = self.notebook.select()
            is_app = self.tab_application is not None and active == str(self.tab_application)
            is_afg = self.tab_afg is not None and active == str(self.tab_afg)
            is_hmi = self.tab_hmi is not None and active == str(self.tab_hmi)
            if not (is_app or is_afg or is_hmi):
                return None
        except Exception:
            return None

        w = None
        try:
            w = self.focus_get()
        except Exception:
            w = None

        # If focus isn't within the active tab, don't interfere.
        try:
            if w is not None:
                if self.tab_application is not None and str(w).startswith(str(self.tab_application)):
                    pass
                elif self.tab_afg is not None and str(w).startswith(str(self.tab_afg)):
                    pass
                elif self.tab_hmi is not None and str(w).startswith(str(self.tab_hmi)):
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
            elif self.notebook is not None and self.tab_hmi is not None and self.notebook.select() == str(self.tab_hmi):
                self._hmi_undo()
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
        root = self._hmi_root_for_persist()
        if root is None:
            return b""
        try:
            return ET.tostring(root, encoding="utf-8", short_empty_elements=True)
        except Exception:
            try:
                return (ET.tostring(root, encoding="unicode") or "").encode("utf-8", errors="ignore")
            except Exception:
                return b""

    _HMI_UI_TAG_ATTR = "__ui_tag"
    _HMI_UI_PREFIX = "__ui_"

    def _hmi_is_ui_attr(self, name: str) -> bool:
        return (name or "").startswith(self._HMI_UI_PREFIX)

    def _hmi_el_attr_sig(self, el: ET.Element) -> tuple[tuple[str, str], ...]:
        """Shallow signature for a single node (attributes only, excluding UI-only attrs)."""
        try:
            items = [(k, v) for k, v in (el.attrib or {}).items() if isinstance(k, str) and not self._hmi_is_ui_attr(k)]
        except Exception:
            items = []
        try:
            items.sort(key=lambda kv: kv[0])
        except Exception:
            pass
        out: list[tuple[str, str]] = []
        for k, v in items:
            try:
                out.append((k, "" if v is None else str(v)))
            except Exception:
                out.append((k, ""))
        return tuple(out)

    def _hmi_iter_elements_with_keys(self, root: ET.Element) -> list[tuple[tuple, ET.Element, int]]:
        """Yield (key, element, position) for persisted HMI nodes.

        Keys are based on the element's own attributes + surrounding context (menu name / parent item),
        so when a user edits a value and later edits it back, the key will match again and allow
        clearing of the per-node 'changed' highlight.
        """
        out: list[tuple[tuple, ET.Element, int]] = []

        def local(t: object) -> str:
            try:
                return _local_name(t)  # type: ignore[arg-type]
            except Exception:
                return ""

        for menu in list(root):
            if not (isinstance(menu.tag, str) and local(menu.tag) == "HMIMenu"):
                continue
            menu_name = (menu.attrib.get("name") or "").strip()
            out.append((("menu", menu_name), menu, -1))

            items = [
                ch
                for ch in list(menu)
                if isinstance(ch.tag, str) and local(ch.tag) == "HMIMenuItem"
            ]
            for item_pos, item in enumerate(items):
                ref = (item.attrib.get("ref") or "").strip()
                if ref:
                    out.append((("ref_link", menu_name, ref), item, item_pos))
                    continue

                item_name = (item.attrib.get("name") or "").strip()
                item_do = (item.attrib.get("doRef") or "").strip()
                item_da = (item.attrib.get("daRef") or "").strip()
                out.append((("item", menu_name, item_name, item_do, item_da), item, item_pos))

                data_items = [
                    di
                    for di in list(item)
                    if isinstance(di.tag, str) and local(di.tag) == "HMIDataItem"
                ]
                for di_pos, di in enumerate(data_items):
                    di_name = (di.attrib.get("name") or "").strip()
                    di_do = (di.attrib.get("doRef") or "").strip()
                    di_da = (di.attrib.get("daRef") or "").strip()
                    out.append((("data", menu_name, item_name, item_do, item_da, di_name, di_do, di_da), di, di_pos))

        return out

    def _hmi_capture_saved_el_baseline(self) -> None:
        """Capture a per-node baseline map for per-item revert detection."""
        try:
            root = self._hmi_root_for_persist()
        except Exception:
            root = None
        if root is None:
            self._hmi_saved_el_sig_by_key = None
            return

        baseline: dict[tuple, list[tuple[tuple, int]]] = {}
        for key, el, pos in self._hmi_iter_elements_with_keys(root):
            sig = self._hmi_el_attr_sig(el)
            baseline.setdefault(key, []).append((sig, int(pos)))
        self._hmi_saved_el_sig_by_key = baseline

    def _hmi_clear_changed_tags_if_reverted(self) -> bool:
        """Clear 'changed' tags for nodes that match last saved per-node baseline.

        Unlike the old global clean check, this works per-node: if *one* item is edited and then
        manually changed back to match the last saved state, its highlight disappears even if
        other items are still dirty.

        Returns True if any tag was cleared.
        """
        if self._hmi_root is None:
            return False
        baseline = getattr(self, "_hmi_saved_el_sig_by_key", None)
        if not isinstance(baseline, dict) or not baseline:
            return False

        cleared = False
        try:
            cur_rows = self._hmi_iter_elements_with_keys(self._hmi_root)
        except Exception:
            return False

        moved_ids = getattr(self, "_hmi_ui_moved_el_ids", None)
        if not isinstance(moved_ids, set):
            moved_ids = set()
            self._hmi_ui_moved_el_ids = moved_ids

        for key, el, pos in cur_rows:
            if not (isinstance(el.tag, str) and (el.attrib.get(self._HMI_UI_TAG_ATTR) or "").strip() == "changed"):
                continue

            entries = baseline.get(key) or []
            if not entries:
                continue
            cur_sig = self._hmi_el_attr_sig(el)

            el_id = id(el)
            if el_id in moved_ids:
                # For move diffs, ordering is meaningful: only clear when it returned to saved position.
                if not any((sig == cur_sig and int(saved_pos) == int(pos)) for sig, saved_pos in entries):
                    continue

            # For value diffs, ignore position: match by per-node attributes.
            if not any((sig == cur_sig) for sig, _saved_pos in entries):
                continue

            try:
                el.attrib.pop(self._HMI_UI_TAG_ATTR, None)
                cleared = True
            except Exception:
                pass
            try:
                moved_ids.discard(el_id)
            except Exception:
                pass

        return cleared

    def _hmi_ui_tag_get(self, el: ET.Element | None) -> str:
        if el is None:
            return ""
        try:
            return (el.attrib.get(self._HMI_UI_TAG_ATTR) or "").strip()
        except Exception:
            return ""

    def _hmi_ui_tag_set(self, el: ET.Element | None, tag: str) -> None:
        if el is None:
            return
        tag0 = (tag or "").strip()
        if not tag0:
            return
        cur = self._hmi_ui_tag_get(el)
        # Precedence: removed > added > changed.
        if cur == "removed":
            return
        if cur == "added" and tag0 in {"changed"}:
            return
        if tag0 == cur:
            return
        try:
            el.attrib[self._HMI_UI_TAG_ATTR] = tag0
        except Exception:
            pass

    def _hmi_ui_tag_clear(self, el: ET.Element | None) -> None:
        if el is None:
            return
        try:
            el.attrib.pop(self._HMI_UI_TAG_ATTR, None)
        except Exception:
            pass

    def _hmi_ui_is_removed(self, el: ET.Element | None) -> bool:
        return self._hmi_ui_tag_get(el) == "removed"

    def _hmi_ui_is_added(self, el: ET.Element | None) -> bool:
        return self._hmi_ui_tag_get(el) == "added"

    def _hmi_ui_is_changed(self, el: ET.Element | None) -> bool:
        return self._hmi_ui_tag_get(el) == "changed"

    def _hmi_mark_added_recursive(self, el: ET.Element | None) -> None:
        if el is None:
            return
        try:
            for ch in el.iter():
                if isinstance(ch.tag, str):
                    self._hmi_ui_tag_set(ch, "added")
        except Exception:
            pass

    def _hmi_root_for_persist(self) -> ET.Element | None:
        """Return a normalized copy of the HMI model for dirty-checking and saving.

        - Applies pending deletes (ui tag == 'removed')
        - Removes internal UI tag attributes from the output
        """
        if self._hmi_root is None:
            return None

        try:
            root = _deepcopy_et_element(self._hmi_root)
        except Exception:
            return self._hmi_root

        # Collect deleted menu names first.
        deleted_menu_names: set[str] = set()
        menus: list[ET.Element] = []
        for el in list(root):
            if not (isinstance(el.tag, str) and _local_name(el.tag) == "HMIMenu"):
                continue
            menus.append(el)
            if (el.attrib.get(self._HMI_UI_TAG_ATTR) or "").strip() == "removed":
                nm = (el.attrib.get("name") or "").strip()
                if nm:
                    deleted_menu_names.add(nm)

        # Remove deleted HMIMenu elements.
        for m in list(menus):
            if (m.attrib.get(self._HMI_UI_TAG_ATTR) or "").strip() == "removed":
                try:
                    root.remove(m)
                except Exception:
                    pass

        # Remove deleted links/items/data and links to deleted menus.
        for menu in list(root):
            if not (isinstance(menu.tag, str) and _local_name(menu.tag) == "HMIMenu"):
                continue
            for item in list(menu):
                if not (isinstance(item.tag, str) and _local_name(item.tag) == "HMIMenuItem"):
                    continue

                # Pending delete on the link/item itself.
                if (item.attrib.get(self._HMI_UI_TAG_ATTR) or "").strip() == "removed":
                    try:
                        menu.remove(item)
                    except Exception:
                        pass
                    continue

                ref = (item.attrib.get("ref") or "").strip()
                if ref and ref in deleted_menu_names:
                    try:
                        menu.remove(item)
                    except Exception:
                        pass
                    continue

                # DO item: prune deleted HMIDataItem children.
                if not ref:
                    for di in list(item):
                        if not (isinstance(di.tag, str) and _local_name(di.tag) == "HMIDataItem"):
                            continue
                        if (di.attrib.get(self._HMI_UI_TAG_ATTR) or "").strip() == "removed":
                            try:
                                item.remove(di)
                            except Exception:
                                pass

        # Strip UI tags everywhere.
        try:
            for el in root.iter():
                if isinstance(el.tag, str):
                    el.attrib.pop(self._HMI_UI_TAG_ATTR, None)
        except Exception:
            pass

        # Normalize attribute order to match persisted output.
        try:
            self._hmi_normalize_attr_order_in_place(root)
        except Exception:
            pass

        return root

    def _hmi_apply_persist_in_place(self) -> None:
        """Mutate the current HMI model into its persisted form.

        Removes pending-deleted nodes (ui tag == 'removed') and clears UI tag attributes.
        """
        root = self._hmi_root
        if root is None:
            return

        deleted_menu_names: set[str] = set()
        menus: list[ET.Element] = []
        for el in list(root):
            if not (isinstance(el.tag, str) and _local_name(el.tag) == "HMIMenu"):
                continue
            menus.append(el)
            if (el.attrib.get(self._HMI_UI_TAG_ATTR) or "").strip() == "removed":
                nm = (el.attrib.get("name") or "").strip()
                if nm:
                    deleted_menu_names.add(nm)

        for m in list(menus):
            if (m.attrib.get(self._HMI_UI_TAG_ATTR) or "").strip() == "removed":
                try:
                    root.remove(m)
                except Exception:
                    pass

        for menu in list(root):
            if not (isinstance(menu.tag, str) and _local_name(menu.tag) == "HMIMenu"):
                continue
            for item in list(menu):
                if not (isinstance(item.tag, str) and _local_name(item.tag) == "HMIMenuItem"):
                    continue

                if (item.attrib.get(self._HMI_UI_TAG_ATTR) or "").strip() == "removed":
                    try:
                        menu.remove(item)
                    except Exception:
                        pass
                    continue

                ref = (item.attrib.get("ref") or "").strip()
                if ref and ref in deleted_menu_names:
                    try:
                        menu.remove(item)
                    except Exception:
                        pass
                    continue

                if not ref:
                    for di in list(item):
                        if not (isinstance(di.tag, str) and _local_name(di.tag) == "HMIDataItem"):
                            continue
                        if (di.attrib.get(self._HMI_UI_TAG_ATTR) or "").strip() == "removed":
                            try:
                                item.remove(di)
                            except Exception:
                                pass

        try:
            for el in root.iter():
                if isinstance(el.tag, str):
                    el.attrib.pop(self._HMI_UI_TAG_ATTR, None)
        except Exception:
            pass

        # Enforce derived names at persist-time.
        try:
            self._hmi_sync_names_from_refs_in_place(root)
        except Exception:
            pass

        # Normalize attribute order for saved XML.
        try:
            self._hmi_normalize_attr_order_in_place(root)
        except Exception:
            pass

    def _hmi_normalize_attr_order_in_place(self, root: ET.Element) -> None:
        """Normalize attribute ordering for persisted HMI XML.

        Requirements:
        - HMIDataItem: groupid must appear between name and doRef.
        - HMIMenu: attributes must be saved in this order:
            name, desc, instantiate, langRef, hmiMenuDataType, hmiMenuViewType, hmiSubTreeType

        Note: XML attribute order is not semantically significant, but downstream
        tooling may expect a stable order.
        """

        def reorder(el: ET.Element, preferred: list[str]) -> None:
            try:
                items = list(el.attrib.items())
            except Exception:
                items = []
            if not items:
                return

            new_attrs: dict[str, str] = {}
            for k in preferred:
                if k in el.attrib:
                    new_attrs[k] = el.attrib.get(k) or ""
            for k, v in items:
                if k not in new_attrs:
                    new_attrs[k] = v
            try:
                el.attrib.clear()
                el.attrib.update(new_attrs)
            except Exception:
                pass

        for el in root.iter():
            if not (isinstance(el.tag, str) and el.attrib):
                continue
            local = _local_name(el.tag)
            if local == "HMIDataItem":
                reorder(el, ["name", "groupid", "doRef", "daRef"])
            elif local == "HMIMenu":
                menu_name = (el.attrib.get("name") or "").strip()
                if menu_name.startswith("Manual_Protection"):
                    # Manual auto menus must not carry these attributes.
                    for k in ("desc", "langRef", "hmiMenuDataType", "hmiMenuViewType"):
                        el.attrib.pop(k, None)
                    reorder(el, ["name", "instantiate", "hmiSubTreeType"])
                else:
                    # Persist-time defaults required by downstream tooling.
                    # Always emit desc/langRef even when not filled in UI.
                    if "desc" not in el.attrib:
                        el.attrib["desc"] = ""
                    if "langRef" not in el.attrib:
                        el.attrib["langRef"] = "0.0"
                    reorder(
                        el,
                        [
                            "name",
                            "desc",
                            "instantiate",
                            "langRef",
                            "hmiMenuDataType",
                            "hmiMenuViewType",
                            "hmiSubTreeType",
                        ],
                    )

    def _hmi_sync_names_from_refs_in_place(self, root: ET.Element) -> None:
        """Ensure DO/DA node @name matches doRef/daRef (derived display name).

        DO level: HMIMenuItem without @ref => if @doRef set, @name = last segment of @doRef.
        DA level: HMIDataItem => if @daRef set, @name = @daRef without leading '.', then last segment.
        """

        def _da_name_from_daref(da_ref: str) -> str:
            txt = (da_ref or "").strip()
            if not txt:
                return ""
            if txt.startswith("."):
                txt = txt[1:]
            if "." in txt:
                txt = (txt.rsplit(".", 1)[-1] or "").strip()
            return txt

        for menu in list(root):
            if not (isinstance(menu.tag, str) and _local_name(menu.tag) == "HMIMenu"):
                continue
            for item in list(menu):
                if not (isinstance(item.tag, str) and _local_name(item.tag) == "HMIMenuItem"):
                    continue
                if (item.attrib.get("ref") or "").strip():
                    continue
                do_ref = (item.attrib.get("doRef") or "").strip()
                if do_ref:
                    item.attrib["name"] = self._hmi_do_name_from_doref(do_ref)
                else:
                    item.attrib.pop("name", None)
                for di in list(item):
                    if not (isinstance(di.tag, str) and _local_name(di.tag) == "HMIDataItem"):
                        continue
                    da_ref = (di.attrib.get("daRef") or "").strip()
                    if da_ref:
                        di.attrib["name"] = _da_name_from_daref(da_ref)
                    else:
                        di.attrib.pop("name", None)

    def _update_dirty_ui_hmi(self) -> None:
        cur = self._hmi_signature_from_model()
        dirty = (self._hmi_saved_sig is None) or (cur != self._hmi_saved_sig)
        self._set_save_button_dirty(self.btn_hmi_save, dirty=dirty)

        # Refresh requires an opened/saved HMI file (used to locate matching application XML).
        try:
            if self.btn_hmi_refresh is not None:
                enabled = self._hmi_root is not None and self._hmi_file_path is not None
                self.btn_hmi_refresh.configure(state=("normal" if enabled else "disabled"))
        except Exception:
            pass

    def _hmi_clear_changed_tags_if_clean(self) -> bool:
        """If the persisted HMI equals the saved signature, clear all 'changed' tags.

        This ensures that when a user edits a value and then changes it back to the
        original, the background highlight automatically disappears.

        Returns True if any tag was cleared.
        """
        if self._hmi_root is None:
            return False
        if self._hmi_saved_sig is None:
            return False
        try:
            cur = self._hmi_signature_from_model()
        except Exception:
            return False
        if cur != self._hmi_saved_sig:
            return False

        cleared = False
        try:
            for el in self._hmi_root.iter():
                if not isinstance(el.tag, str):
                    continue
                if (el.attrib.get(self._HMI_UI_TAG_ATTR) or "").strip() == "changed":
                    try:
                        el.attrib.pop(self._HMI_UI_TAG_ATTR, None)
                        cleared = True
                    except Exception:
                        pass
        except Exception:
            return False
        return cleared

    def _mark_hmi_saved(self) -> None:
        self._hmi_saved_sig = self._hmi_signature_from_model()
        try:
            self._hmi_capture_saved_el_baseline()
        except Exception:
            self._hmi_saved_el_sig_by_key = None
        try:
            self._hmi_ui_moved_el_ids.clear()
        except Exception:
            pass
        self._update_dirty_ui_hmi()

    def _mark_hmi_unsaved(self) -> None:
        self._update_dirty_ui_hmi()
        # If a node returned to the last saved state, clear only that node's 'changed' highlight.
        try:
            if self._hmi_clear_changed_tags_if_reverted():
                self._refresh_hmi_views(select_first_menu=False, open_selection_path=False)
        except Exception:
            pass

    def _hmi_tree_text_path_for_iid(self, iid: str) -> list[str]:
        tv = self._hmi_tv_menus
        if tv is None:
            return []
        parts: list[str] = []
        cur = iid
        while cur:
            try:
                parts.append((tv.item(cur, "text") or "").strip())
            except Exception:
                parts.append("")
            try:
                cur = tv.parent(cur)
            except Exception:
                break
        parts.reverse()
        return [p for p in parts if p]

    def _hmi_iter_tree_iids(self) -> list[str]:
        tv = self._hmi_tv_menus
        if tv is None:
            return []
        out: list[str] = []

        def walk(parent: str) -> None:
            try:
                kids = tv.get_children(parent)
            except Exception:
                kids = ()
            for k in kids:
                out.append(k)
                walk(k)

        walk("")
        return out

    def _hmi_capture_tree_state(self) -> tuple[list[str] | None, list[list[str]]]:
        tv = self._hmi_tv_menus
        if tv is None:
            return None, []

        sel_path: list[str] | None = None
        try:
            sel = tv.selection()
            if sel:
                sel_path = self._hmi_tree_text_path_for_iid(sel[0])
        except Exception:
            sel_path = None

        open_paths: list[list[str]] = []
        for iid in self._hmi_iter_tree_iids():
            try:
                if bool(tv.item(iid, "open")):
                    p = self._hmi_tree_text_path_for_iid(iid)
                    if p:
                        open_paths.append(p)
            except Exception:
                pass
        return sel_path, open_paths

    def _hmi_find_iid_by_text_path(self, path: list[str]) -> str | None:
        tv = self._hmi_tv_menus
        if tv is None:
            return None
        if not path:
            return None
        cur_parent = ""
        cur_iid: str | None = None
        for seg in path:
            found: str | None = None
            try:
                kids = tv.get_children(cur_parent)
            except Exception:
                kids = ()
            for k in kids:
                try:
                    if (tv.item(k, "text") or "").strip() == seg:
                        found = k
                        break
                except Exception:
                    continue
            if found is None:
                return None
            cur_iid = found
            cur_parent = found
        return cur_iid

    def _hmi_push_undo(self) -> None:
        if self._hmi_root is None:
            return
        if getattr(self, "_hmi_undoing", False):
            return

        try:
            xml_text = ET.tostring(self._hmi_root, encoding="unicode", short_empty_elements=True)
        except Exception:
            return
        sel_path, open_paths = self._hmi_capture_tree_state()
        try:
            self._hmi_undo_stack.append((xml_text, sel_path, open_paths))
            if len(self._hmi_undo_stack) > int(getattr(self, "_hmi_undo_max", 50) or 50):
                self._hmi_undo_stack = self._hmi_undo_stack[-int(getattr(self, "_hmi_undo_max", 50) or 50) :]
        except Exception:
            pass

    def _hmi_undo(self) -> None:
        if self._hmi_root is None:
            return
        if not (getattr(self, "_hmi_undo_stack", None) or []):
            return

        try:
            xml_text, sel_path, open_paths = self._hmi_undo_stack.pop()
        except Exception:
            return

        try:
            self._hmi_undoing = True
            try:
                self._hmi_end_cell_edit(commit=True)
                self._hmi_end_combo_edit(commit=True)
            except Exception:
                pass
            try:
                self._hmi_root = ET.fromstring(xml_text)
            except Exception:
                return
            # Element identities changed; clear move tracking.
            try:
                self._hmi_ui_moved_el_ids.clear()
            except Exception:
                pass

            self._refresh_hmi_views(select_first_menu=True, open_selection_path=False)
            tv = self._hmi_tv_menus
            if tv is not None:
                for p in open_paths or []:
                    iid = self._hmi_find_iid_by_text_path(p)
                    if iid:
                        try:
                            tv.item(iid, open=True)
                        except Exception:
                            pass
                if sel_path:
                    iid = self._hmi_find_iid_by_text_path(sel_path)
                    if iid:
                        try:
                            tv.selection_set(iid)
                            self._hmi_open_iid_path(iid, open_self=True)
                        except Exception:
                            pass
        finally:
            try:
                self._hmi_undoing = False
            except Exception:
                pass

        try:
            self._update_dirty_ui_hmi()
        except Exception:
            pass

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

            max_show = 1200
            shown = filtered[:max_show]
            self.cb_enum["values"] = shown
            suffix = "" if len(filtered) <= max_show else f" (showing first {max_show})"
            self.lbl_enum_match.configure(text=f"{len(filtered)} match{'' if len(filtered)==1 else 'es'}{suffix}")
            if raw:
                if shown:
                    self.var_enum_selected.set(shown[0])
                return
            if shown and cur not in shown:
                self.var_enum_selected.set(shown[0])

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

            max_show = 1200
            shown = filtered[:max_show]
            self.cb_afg["values"] = shown
            suffix = "" if len(filtered) <= max_show else f" (showing first {max_show})"
            self.lbl_afg_match.configure(text=f"{len(filtered)} match{'' if len(filtered)==1 else 'es'}{suffix}")
            if raw:
                if shown:
                    self.var_afg_selected.set(shown[0])
                return
            if shown and cur not in shown:
                self.var_afg_selected.set(shown[0])

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
        base_dir = self._applicationgroup_dir()
        source_relpaths = self._scan_xml_relpaths(base_dir)
        dlg = _AfgNewDialog(
            self,
            source_relpaths=source_relpaths,
            source_base_dir=base_dir,
            initial_name="",
            initial_proxy="",
            initial_chapter="",
            initial_topic="",
        )
        res = dlg.show()
        if not res:
            return

        source_rel = (res.get("source_rel") or "").strip()
        source_blank = "(Blank)"
        root: ET.Element
        if source_rel and source_rel != source_blank:
            src_path = base_dir / source_rel
            try:
                tree = ET.parse(src_path)
                src_root = tree.getroot()
            except Exception as e:
                messagebox.showerror("Create failed", f"Failed to load source AFG:\n\n{os.fspath(src_path)}\n\n{e}", parent=self)
                return

            if not (isinstance(src_root.tag, str) and _local_name(src_root.tag) == "AfgDiagramXml"):
                messagebox.showerror("Invalid", "Source root element is not <AfgDiagramXml>", parent=self)
                return

            root = _deepcopy_et_element(src_root)
        else:
            root = ET.Element("AfgDiagramXml")
            ET.SubElement(root, "fbItems")
            ET.SubElement(root, "afgInItems")
            ET.SubElement(root, "afgOutItems")
            ET.SubElement(root, "arrows")

        root.attrib["name"] = (res.get("name") or "").strip()
        root.attrib["proxyName"] = res.get("proxyName") or ""
        root.attrib["chapterName"] = res.get("chapterName") or ""
        root.attrib["topicName"] = res.get("topicName") or ""
        if (root.attrib.get("maxPinID") or "").strip() == "":
            root.attrib["maxPinID"] = "0"

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
        if source_rel and source_rel != source_blank:
            self._set_status(f"New AFG created from {source_rel} (unsaved)")
        else:
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

        # Build dropdown values for menu columns (scanned across all HMI templates).
        try:
            self._hmi_collect_menu_type_values(base_dir=base_dir, rel_paths=self._all_hmi_files)
        except Exception:
            self._hmi_menu_data_type_values = []
            self._hmi_menu_view_type_values = []

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

            max_show = 1200
            shown = filtered[:max_show]
            self.cb_hmi["values"] = shown
            suffix = "" if len(filtered) <= max_show else f" (showing first {max_show})"
            self.lbl_hmi_match.configure(text=f"{len(filtered)} match{'' if len(filtered)==1 else 'es'}{suffix}")
            if raw:
                if shown:
                    self.var_hmi_selected.set(shown[0])
                return
            if shown and cur not in shown:
                self.var_hmi_selected.set(shown[0])

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

    def _hmi_collect_menu_type_values(self, *, base_dir: Path, rel_paths: list[str]) -> None:
        data_set: set[str] = set()
        view_set: set[str] = set()

        for rel in rel_paths or []:
            try:
                p = Path(base_dir) / rel
            except Exception:
                continue
            if not p.exists():
                continue
            try:
                root = ET.parse(p).getroot()
            except Exception:
                continue
            if not (isinstance(root.tag, str) and _local_name(root.tag) == "PowerLogicHmiCustomization"):
                continue
            for menu in list(root):
                if not (isinstance(menu.tag, str) and _local_name(menu.tag) == "HMIMenu"):
                    continue
                dt = (menu.attrib.get("hmiMenuDataType") or "").strip()
                vt = (menu.attrib.get("hmiMenuViewType") or "").strip()
                if dt:
                    data_set.add(dt)
                if vt:
                    view_set.add(vt)

        self._hmi_menu_data_type_values = sorted(data_set)
        self._hmi_menu_view_type_values = sorted(view_set)

    def _new_hmi(self) -> None:
        base_dir = self._hmi_template_dir()
        base_dir.mkdir(parents=True, exist_ok=True)

        choice = _NewHmiChoiceDialog(self).show()
        if not choice:
            return

        def make_blank_root() -> ET.Element:
            root0 = ET.Element(_q(HMI_CUST_NS, "PowerLogicHmiCustomization"))
            root0.attrib[_q(XSI_NS, "schemaLocation")] = f"{HMI_CUST_NS} SE_PowerLogic_HmiCustomization.xsd"
            root0.attrib["desc"] = "yyy"
            return root0

        if choice == "from_application":
            app_dir = self._application_dir()
            app_items = self._scan_xml_relpaths(app_dir)
            if not app_items:
                messagebox.showerror("Missing", f"No application (*.xml) found under:\n\n{os.fspath(app_dir)}", parent=self)
                return

            dlg = _CreateHmiFromApplicationDialog(self, app_relpaths=app_items)
            res = dlg.show()
            if not res:
                return

            app_path = app_dir / (res.get("app_rel") or "")
            try:
                tree = ET.parse(app_path)
                app_root = tree.getroot()
            except Exception as e:
                messagebox.showerror("Open failed", str(e), parent=self)
                return

            funblock = None
            for el in app_root.iter():
                if not isinstance(el.tag, str):
                    continue
                if self._local_name(el.tag) == "funBlock":
                    funblock = el
                    break
            if funblock is None:
                messagebox.showerror("Invalid", "No <funBlock> found in file", parent=self)
                return

            old_app_root = self._app_root
            old_app_funblock = self._app_funblock
            old_app_path = self._app_file_path
            try:
                self._app_root = app_root
                self._app_funblock = funblock
                self._app_file_path = app_path
                self._create_hmi_for_this_afb(sync_from_app_ui=False)
            finally:
                self._app_root = old_app_root
                self._app_funblock = old_app_funblock
                self._app_file_path = old_app_path
            return

        if choice == "copy":
            items = self._scan_xml_relpaths(base_dir)
            dlg = _CopyHmiDialog(
                self,
                hmi_relpaths=items,
                suggested_filename="HMI.xml",
            )
            res = dlg.show()
            if not res:
                return

            src_rel = (res.get("src_rel") or "").strip()
            target_path = base_dir / (res.get("new_name") or "")
            if not (target_path.name or "").strip():
                return
            if target_path.exists():
                ok = messagebox.askyesno(
                    "Overwrite?",
                    f"File already exists:\n\n{os.fspath(target_path)}\n\nOverwrite?",
                    parent=self,
                )
                if not ok:
                    return
            if src_rel == "Blank":
                root = make_blank_root()
            else:
                src = base_dir / src_rel
                if not src.exists():
                    messagebox.showerror("Missing", f"Source file not found:\n\n{os.fspath(src)}", parent=self)
                    return
                try:
                    root = ET.parse(src).getroot()
                except Exception as e:
                    messagebox.showerror("Read failed", str(e), parent=self)
                    return
                if not (isinstance(root.tag, str) and _local_name(root.tag) == "PowerLogicHmiCustomization"):
                    messagebox.showerror("Invalid", "Source root element is not <PowerLogicHmiCustomization>", parent=self)
                    return
                root = _deepcopy_et_element(root)
        else:
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
            root = make_blank_root()

        self._hmi_root = root
        self._hmi_file_path = target_path
        self._hmi_saved_sig = None
        try:
            self._hmi_undo_stack = []
        except Exception:
            pass
        self._refresh_hmi_views(select_first_menu=False)
        self._mark_hmi_unsaved()
        try:
            rel = os.fspath(target_path.relative_to(base_dir))
        except Exception:
            rel = os.fspath(target_path.name)
        self._refresh_hmi_search_list(select_rel=rel)
        if choice == "copy":
            self._set_status(f"Created HMI from selected source: {os.fspath(target_path)}")
        else:
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
        try:
            self._hmi_undo_stack = []
        except Exception:
            pass

        # UX requirement: on open, IED/IET/Manual trees should start fully expanded.
        # IMPORTANT: _hmi_set_scope() normally refreshes the tree. Refreshing again after
        # unfolding can lose open state (depending on prior tree state). So we:
        # 1) switch scope without refreshing
        # 2) refresh+unfold that scope
        # 3) restore original scope without refreshing
        scope0 = (getattr(self, "_hmi_scope", "ied") or "ied").strip().lower()
        if scope0 not in {"ied", "iet", "manual"}:
            scope0 = "ied"

        for s in ("ied", "iet", "manual"):
            try:
                self._hmi_set_scope(s, refresh=False)
            except Exception:
                continue
            try:
                self._refresh_hmi_views(select_first_menu=True, open_selection_path=False)
            except Exception:
                pass
            try:
                self._hmi_unfold_all()
            except Exception:
                pass

        try:
            self._hmi_set_scope(scope0, refresh=False)
        except Exception:
            pass
        try:
            self._hmi_update_hmi_action_state()
        except Exception:
            pass
        try:
            self._hmi_update_fold_all_button()
        except Exception:
            pass

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
        try:
            ET.register_namespace("xsi", XSI_NS)
        except Exception:
            pass
        root = self._hmi_root
        # Always normalize attribute ordering right before serialization.
        # (Save normally calls _hmi_apply_persist_in_place which already does this,
        # but other code paths may call _write_hmi_xml directly.)
        try:
            self._hmi_normalize_attr_order_in_place(root)
        except Exception:
            pass
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
            msg = self._hmi_validate_before_save()
        except Exception:
            msg = None
        if msg:
            messagebox.showerror("Missing", msg, parent=self)
            return
        try:
            self._hmi_end_cell_edit(commit=True)
            self._hmi_end_combo_edit(commit=True)
        except Exception:
            pass
        try:
            # Apply staged deletions and strip UI-only diff markers.
            self._hmi_apply_persist_in_place()
            self._write_hmi_xml(self._hmi_file_path)
        except Exception as e:
            messagebox.showerror("Save failed", str(e), parent=self)
            return
        self._set_status(f"Saved HMI: {os.fspath(self._hmi_file_path)}")
        self._mark_hmi_saved()
        self._refresh_hmi_views(select_first_menu=True, open_selection_path=False)

    def _hmi_validate_before_save(self) -> str | None:
        """Return an error message if the current HMI cannot be saved yet."""

        root = self._hmi_root
        if root is None:
            return None

        missing: list[str] = []
        for el in root.iter():
            if not (isinstance(el.tag, str) and _local_name(el.tag) == "HMIMenu"):
                continue
            name = (el.attrib.get("name") or "").strip()
            dt = (el.attrib.get("hmiMenuDataType") or "").strip()
            vt = (el.attrib.get("hmiMenuViewType") or "").strip()
            st = (el.attrib.get("hmiSubTreeType") or "").strip()

            # Create-HMI requirement: tabs menu must have hmiSubTreeType chosen.
            if (
                name.startswith("Menu_Protection_")
                and dt == "HMI_MENU_DATA_TYPE_TAB"
                and vt == "HMI_MENU_VIEW_TYPE_TABS"
                and not st
            ):
                missing.append(f"{name or '(unnamed)'}: hmiSubTreeType")

            # Create-HMI requirement: outputs menu view type must be chosen.
            if name.startswith("Menu_Protection_") and name.endswith("_Outputs"):
                if not vt:
                    missing.append(f"{name or '(unnamed)'}: hmiMenuViewType")

        if not missing:
            return None
        return "Cannot save yet. Fill required HMI menu fields:\n\n" + "\n".join(missing)

    def _save_hmi_as(self) -> None:
        if self._hmi_root is None:
            messagebox.showerror("Missing", "No HMI loaded", parent=self)
            return
        try:
            self._hmi_end_cell_edit(commit=True)
            self._hmi_end_combo_edit(commit=True)
        except Exception:
            pass
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
        scope = (getattr(self, "_hmi_scope", "ied") or "ied").strip().lower()
        menus: list[ET.Element] = []
        for el in root.iter():
            if not (isinstance(el.tag, str) and _local_name(el.tag) == "HMIMenu"):
                continue
            name = (el.attrib.get("name") or "").strip()
            is_iet = name.startswith("IET_Protection")
            is_manual = name.startswith("Manual_Protection")
            if scope == "iet":
                if not is_iet:
                    continue
            elif scope == "manual":
                if not is_manual:
                    continue
            else:
                # IED view shows everything that is not IET/Manual.
                if is_iet or is_manual:
                    continue
            menus.append(el)
        menus.sort(key=lambda m: (m.attrib.get("name") or "").lower())
        return menus

    def _hmi_set_scope(self, scope: str, *, refresh: bool = True) -> None:
        """Switch HMI UI scope between IED, IET and Manual.

        This updates which menu treeview is considered active and refreshes the
        visible menu table.
        """

        prev_scope = (getattr(self, "_hmi_scope", "ied") or "ied").strip().lower()
        if prev_scope in {"ied", "iet", "manual"}:
            self._hmi_save_scope_tree_state(prev_scope)

        s = (scope or "").strip().lower()
        if s not in {"ied", "iet", "manual"}:
            s = "ied"
        self._hmi_scope = s

        # Update the active tree reference used by the rest of the HMI logic.
        if s == "ied":
            tv = self._hmi_tv_menus_ied
        elif s == "iet":
            tv = self._hmi_tv_menus_iet
        else:
            tv = self._hmi_tv_menus_manual
        if tv is not None:
            self._hmi_tv_menus = tv

        # Restore this scope's mapping snapshot so refresh can read current open state
        # from the correct tree ids.
        self._hmi_restore_scope_tree_state(s)

        # Buttons live inside each sub-tab (IED/IET/Manual). Point generic button
        # refs used by the existing handlers to the currently active set.
        suffix = s
        for key in (
            "_hmi_btn_add",
            "_hmi_btn_insert",
            "_hmi_btn_edit",
            "_hmi_btn_copy",
            "_hmi_btn_cut",
            "_hmi_btn_paste",
            "_hmi_btn_delete",
            "_hmi_btn_up",
            "_hmi_btn_down",
            "_hmi_btn_fold_all",
        ):
            try:
                btn = getattr(self, f"{key}_{suffix}", None)
            except Exception:
                btn = None
            setattr(self, key, btn)

        if refresh:
            try:
                self._hmi_restore_column_widths()
            except Exception:
                pass

            # Refresh just the menu table for the newly active scope.
            try:
                self._refresh_hmi_views(select_first_menu=True, open_selection_path=False)
            except Exception:
                pass

            try:
                self._hmi_schedule_column_resize()
            except Exception:
                pass

            try:
                self._hmi_update_hmi_action_state()
            except Exception:
                pass

            # Keep the cache aligned with whichever scope was just refreshed.
            self._hmi_save_scope_tree_state(s)

    def _hmi_save_scope_tree_state(self, scope: str) -> None:
        s = (scope or "").strip().lower()
        if s not in {"ied", "iet", "manual"}:
            return
        self._hmi_menu_iid_to_el_by_scope[s] = dict(self._hmi_menu_iid_to_el or {})
        self._hmi_tree_iid_to_kind_by_scope[s] = dict(self._hmi_tree_iid_to_kind or {})
        self._hmi_tree_iid_to_node_by_scope[s] = dict(self._hmi_tree_iid_to_node or {})
        self._hmi_tree_iid_to_ref_link_by_scope[s] = dict(self._hmi_tree_iid_to_ref_link or {})

    def _hmi_restore_scope_tree_state(self, scope: str) -> None:
        s = (scope or "").strip().lower()
        if s not in {"ied", "iet", "manual"}:
            return
        self._hmi_menu_iid_to_el = dict(self._hmi_menu_iid_to_el_by_scope.get(s, {}) or {})
        self._hmi_tree_iid_to_kind = dict(self._hmi_tree_iid_to_kind_by_scope.get(s, {}) or {})
        self._hmi_tree_iid_to_node = dict(self._hmi_tree_iid_to_node_by_scope.get(s, {}) or {})
        self._hmi_tree_iid_to_ref_link = dict(self._hmi_tree_iid_to_ref_link_by_scope.get(s, {}) or {})

    def _hmi_selected_menu(self) -> tuple[ET.Element, ET.Element] | None:
        if self._hmi_tv_menus is None:
            return None
        sel = self._hmi_tv_menus.selection()
        if not sel:
            return None
        iid = sel[0]
        node = self._hmi_tree_iid_to_node.get(iid)
        if node is None:
            return None
        kind, _parent, el = node
        if kind != "menu" or el is None:
            return None
        return self._hmi_menu_iid_to_el.get(iid)

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

    def _refresh_hmi_views(self, *, select_first_menu: bool, open_selection_path: bool = True) -> None:
        self._refresh_hmi_menu_table(select_first=select_first_menu, open_selection_path=open_selection_path)
        try:
            self._update_dirty_ui_hmi()
        except Exception:
            pass
        try:
            self._hmi_schedule_column_resize()
        except Exception:
            pass

    def _hmi_schedule_column_resize(self) -> None:
        tv = self._hmi_tv_menus
        if tv is None:
            return
        try:
            if self._hmi_resize_after_id is not None:
                self.after_cancel(self._hmi_resize_after_id)
        except Exception:
            pass
        try:
            self._hmi_resize_after_id = self.after(80, self._hmi_resize_columns)
        except Exception:
            self._hmi_resize_after_id = None

    def _hmi_resize_columns(self) -> None:
        tv = self._hmi_tv_menus
        if tv is None:
            return
        self._hmi_resize_after_id = None

        # Fit all columns into the visible width (no horizontal scrolling).
        try:
            total = int(tv.winfo_width())
        except Exception:
            total = 0
        if total <= 0:
            return

        # Conservative padding for borders.
        avail = max(260, total - 2)

        cols = ["#0"] + list(tv["columns"])

        # Minimums: keep small so we can always show all columns.
        mins: dict[str, int] = {
            "#0": 60,
            "desc": 40,
            "value": 40,
            "instantiate": 30,
            "langRef": 40,
            "hmiMenuDataType": 60,
            "hmiMenuViewType": 60,
            "hmiSubTreeType": 60,
            "doRef": 60,
            "daRef": 40,
            "hideunit": 30,
        }

        # Preferred widths come from user-resized columns (persisted). If missing, seed from current.
        pref = dict(getattr(self, "_hmi_pref_col_widths", None) or {})
        if not pref:
            try:
                pref["#0"] = int(tv.column("#0").get("width") or 0)
                for c in tv["columns"]:
                    pref[c] = int(tv.column(c).get("width") or 0)
            except Exception:
                pref = {}
            self._hmi_pref_col_widths = {k: v for k, v in pref.items() if v > 0}
            pref = dict(self._hmi_pref_col_widths)

        # If mins don't fit, scale mins down (best-effort) so all columns remain visible.
        min_sum = sum(int(mins.get(c, 20)) for c in cols)
        if min_sum > avail:
            scale = avail / float(min_sum) if min_sum else 1.0
            for c in cols:
                lo = 30 if c == "#0" else 20
                mins[c] = max(lo, int(mins.get(c, 20) * scale))

            # If we still don't fit (due to lower bounds), fall back to ultra-compact mins.
            if sum(int(mins.get(c, 20)) for c in cols) > avail:
                for c in cols:
                    mins[c] = 30 if c == "#0" else 20

        # Start with proportional widths based on pref.
        safe_pref = {c: max(int(pref.get(c, mins.get(c, 20)) or 0), 1) for c in cols}
        pref_sum = sum(safe_pref.values())
        if pref_sum <= 0:
            return

        widths: dict[str, int] = {
            c: int(avail * safe_pref[c] / float(pref_sum)) for c in cols
        }
        for c in cols:
            widths[c] = max(int(mins.get(c, 20)), int(widths.get(c, 0)))

        # If we overshot due to mins rounding, shrink in a stable order.
        diff = int(avail - sum(widths.values()))
        if diff < 0:
            need = -diff
            shrink_order = [
                "#0",
                "desc",
                "value",
                "doRef",
                "hmiMenuDataType",
                "hmiMenuViewType",
                "hmiSubTreeType",
                "instantiate",
                "langRef",
                "daRef",
                "hideunit",
            ]
            for c in shrink_order:
                if c not in widths:
                    continue
                reducible = int(widths[c] - mins.get(c, 20))
                if reducible <= 0:
                    continue
                take = reducible if reducible < need else need
                widths[c] -= take
                need -= take
                if need <= 0:
                    break
        elif diff > 0:
            # Give extra space to the tree column for readability.
            widths["#0"] = int(widths.get("#0", 0) + diff)

        try:
            tv.column("#0", width=widths["#0"], minwidth=mins["#0"], stretch=True)
            for c in tv["columns"]:
                if c in widths:
                    tv.column(c, width=widths[c], minwidth=mins.get(c, 20), stretch=True)
        except Exception:
            pass

    def _hmi_current_ln_instance_path(self) -> Path | None:
        # Prefer the currently-open LN instance in the LN instance tab.
        try:
            if self.instance_editor is not None and getattr(self.instance_editor, "doc", None) is not None:
                p = getattr(self.instance_editor.doc, "file_path", None)
                if p:
                    pp = Path(p)
                    return pp if pp.exists() else None
        except Exception:
            pass
        return None

    def _hmi_current_ln_type_id(self) -> str | None:
        # Prefer the lnType shown in the LN instance tab (template id).
        try:
            if self.instance_editor is not None and hasattr(self.instance_editor, "var_lnType"):
                v = (self.instance_editor.var_lnType.get() or "").strip()
                if v:
                    return v
        except Exception:
            pass

        # Fallback: try reading from the loaded document (if any).
        try:
            if self.instance_editor is not None and getattr(self.instance_editor, "doc", None) is not None:
                doc = self.instance_editor.doc
                ln_elements = getattr(doc, "ln_elements", None)
                if ln_elements:
                    ln0 = ln_elements[0]
                    if isinstance(ln0, ET.Element):
                        v = (ln0.attrib.get("lnType") or "").strip()
                        if v:
                            return v
        except Exception:
            pass

        # Fallback: use the currently-loaded LN template editor (no need to open LN instance).
        try:
            ed = getattr(self, "editor", None)
            if ed is not None:
                model = getattr(ed, "model", None)
                if model is not None:
                    info = getattr(model, "info", None)
                    v = (getattr(info, "id", "") or "").strip()
                    if v:
                        return v
                if hasattr(ed, "var_selected"):
                    v2 = (ed.var_selected.get() or "").strip()
                    if v2:
                        return v2
        except Exception:
            pass

        return None

    def _hmi_parse_ln_instance_do_names(self, path: Path) -> list[str]:
        # Parse LN instance and list all DOI names.
        tree = ET.parse(path)
        root = tree.getroot()
        ln_el: ET.Element | None = None
        for el in root.iter():
            if isinstance(el.tag, str) and _local_name(el.tag) == "LN":
                ln_el = el
                break
        if ln_el is None:
            return []

        names: list[str] = []
        for doi in ln_el.iter():
            if not (isinstance(doi.tag, str) and _local_name(doi.tag) == "DOI"):
                continue
            dn = (doi.attrib.get("name") or "").strip()
            if dn:
                names.append(dn)

        # Stable unique sort.
        seen: set[str] = set()
        uniq: list[str] = []
        for n in names:
            if n in seen:
                continue
            seen.add(n)
            uniq.append(n)
        uniq.sort(key=lambda s: (s or "").lower())
        return uniq

    def _hmi_peek_ln_instance_ln_attrs(self, path: Path) -> tuple[str, str, str]:
        """Return (lnType, prefix, lnClass) from the first <LN> element in an LNDM instance."""

        try:
            tree = ET.parse(path)
            root = tree.getroot()
        except Exception:
            return ("", "", "")

        ln_el: ET.Element | None = None
        for el in root.iter():
            if isinstance(el.tag, str) and _local_name(el.tag) == "LN":
                ln_el = el
                break
        if ln_el is None:
            return ("", "", "")

        try:
            ln_type = (ln_el.attrib.get("lnType") or "").strip()
        except Exception:
            ln_type = ""
        try:
            prefix = (ln_el.attrib.get("prefix") or "").strip()
        except Exception:
            prefix = ""
        try:
            ln_class = (ln_el.attrib.get("lnClass") or "").strip()
        except Exception:
            ln_class = ""

        return (ln_type, prefix, ln_class)

    def _hmi_matching_application_path(self) -> Path | None:
        try:
            if self._hmi_file_path is None:
                return None
            return self._application_dir() / f"{self._hmi_file_path.stem}.xml"
        except Exception:
            return None

    def _hmi_read_matching_application_lnref(self) -> str:
        """Read LnRef from the same-stem Application XML for the current HMI file.

        This is used for resolving the LN instance for doRef suggestions without requiring
        the LN instance tab to be opened.
        """

        app_path = self._hmi_matching_application_path()
        if app_path is None or (not app_path.exists()):
            self._hmi_app_cached_path = None
            self._hmi_app_cached_mtime = None
            self._hmi_app_cached_lnref = ""
            return ""

        try:
            mtime = float(app_path.stat().st_mtime)
        except Exception:
            mtime = None

        if (
            self._hmi_app_cached_path is not None
            and app_path == self._hmi_app_cached_path
            and mtime is not None
            and self._hmi_app_cached_mtime == mtime
        ):
            return (self._hmi_app_cached_lnref or "").strip()

        ln_ref = ""
        try:
            app_root = ET.parse(app_path).getroot()
            fun_block: ET.Element | None = None
            for el in app_root.iter():
                if isinstance(el.tag, str) and _local_name(el.tag) == "funBlock":
                    fun_block = el
                    break
            if fun_block is not None:
                ln_ref = (fun_block.attrib.get("LnRef") or "").strip()
        except Exception:
            ln_ref = ""

        self._hmi_app_cached_path = app_path
        self._hmi_app_cached_mtime = mtime
        self._hmi_app_cached_lnref = ln_ref
        return (ln_ref or "").strip()

    def _hmi_normalize_lnref_for_doref(self, lnref: str) -> str:
        """Normalize LnRef into the canonical doRef prefix form, e.g. 'ZNPDIS#'."""

        s = (lnref or "").strip()
        if not s:
            return ""
        if "#" in s:
            s = s.split("#", 1)[0]
        s = s.strip().rstrip("#.;:,_- ")
        if not s:
            return ""
        return f"{s}#"

    def _hmi_ensure_ln_do_suggestions_loaded(self) -> None:
        # Prefer resolving doRef suggestions from the LN instance referenced by the
        # same-stem Application XML (HMI stem -> application/<stem>.xml -> funBlock@LnRef).
        ln_ref_raw = self._hmi_read_matching_application_lnref()
        ln_ref = self._hmi_normalize_lnref_for_doref(ln_ref_raw)
        if ln_ref:
            self._hmi_ln_ref = ln_ref
        elif not (ln_ref_raw or "").strip():
            # No matching application (or no LnRef): avoid reusing an old prefix.
            self._hmi_ln_ref = ""

        try:
            inst_path = self._guess_ln_instance_path_from_lnref(ln_ref_raw) if ln_ref_raw else None
        except Exception:
            inst_path = None

        if inst_path is not None and inst_path.exists():
            try:
                inst_mtime = float(inst_path.stat().st_mtime)
            except Exception:
                inst_mtime = None

            if inst_path != self._hmi_ln_cached_path or inst_mtime != self._hmi_ln_cached_mtime:
                try:
                    parsed = self._hmi_parse_ln_instance_do_names(inst_path)
                except Exception:
                    parsed = []
                if parsed:
                    self._hmi_ln_do_names = parsed
                    self._hmi_ln_do_names_source = "instance"
                else:
                    # If we previously showed instance-derived DOIs, clear them so we can
                    # fall back to template-based suggestions.
                    if (getattr(self, "_hmi_ln_do_names_source", "") or "") == "instance":
                        self._hmi_ln_do_names = []
                    self._hmi_ln_do_names_source = ""
                self._hmi_ln_cached_path = inst_path
                self._hmi_ln_cached_mtime = inst_mtime
        else:
            self._hmi_ln_cached_path = None
            self._hmi_ln_cached_mtime = None
            if (getattr(self, "_hmi_ln_do_names_source", "") or "") == "instance":
                self._hmi_ln_do_names = []
                self._hmi_ln_do_names_source = ""

        ln_type_id = self._hmi_current_ln_type_id()
        if (not ln_type_id) and inst_path is not None and inst_path.exists():
            # Key fix: if user didn't open LN instance/template tabs, derive lnType from
            # the resolved LNDM instance so we can load LNodeType -> DOType -> DA list.
            try:
                ln_type_id, inst_prefix, inst_ln_class = self._hmi_peek_ln_instance_ln_attrs(inst_path)
            except Exception:
                ln_type_id, inst_prefix, inst_ln_class = ("", "", "")

            # If we also lack LnRef, synthesize it from instance prefix+lnClass.
            try:
                if not (getattr(self, "_hmi_ln_ref", "") or "").strip():
                    if (inst_prefix or inst_ln_class):
                        self._hmi_ln_ref = f"{(inst_prefix or '')}{(inst_ln_class or '')}#"
            except Exception:
                pass
        if not ln_type_id:
            # We can still provide doRef dropdown values from the LN instance (above),
            # but DA suggestions require an LN template mapping.
            self._hmi_ln_class = ""
            try:
                self._hmi_ln_do_types_by_name = {}
            except Exception:
                pass
            try:
                self._hmi_ln_cached_lntype_id = None
            except Exception:
                pass
            return

        # If the LN template editor has a loaded model for this lnType, always source from it.
        # This keeps dropdowns up-to-date even when the user edits DO list without saving.
        try:
            ed = getattr(self, "editor", None)
            model = getattr(ed, "model", None) if ed is not None else None
            if model is not None:
                info0 = getattr(model, "info", None)
                if (getattr(info0, "id", "") or "").strip() == ln_type_id:
                    self._hmi_ln_cached_lntype_id = ln_type_id
                    template_do_names = [d.name for d in (model.dos or []) if (d.name or "").strip()]
                    self._hmi_ln_do_types_by_name = {
                        d.name: (d.do_type or "").strip() for d in (model.dos or []) if (d.name or "").strip()
                    }
                    self._hmi_ln_class = (getattr(info0, "ln_class", "") or "").strip()
                    if not self._hmi_ln_do_names:
                        self._hmi_ln_do_names = template_do_names
                        self._hmi_ln_do_names_source = "template"
        except Exception:
            pass

        cached_id = getattr(self, "_hmi_ln_cached_lntype_id", None)
        if isinstance(cached_id, str) and cached_id == ln_type_id:
            return

        self._hmi_ln_cached_lntype_id = ln_type_id
        # Only clear DO names if they were not loaded from the LN instance.
        if not self._hmi_ln_do_names:
            self._hmi_ln_do_names = []
        self._hmi_ln_do_types_by_name = {}
        self._hmi_ln_class = ""

        # (Editor-model sourcing handled earlier.)

        try:
            info = None
            if getattr(self, "catalog", None) is not None:
                for it in (self.catalog.lnode_types or []):
                    if it.id == ln_type_id:
                        info = it
                        break
            if info is None:
                # Catalog scan is non-recursive; try locating the template file directly.
                try:
                    ln_dir = Path(self.iec61850_dir) / "LNodeType"
                    cand = None
                    p0 = ln_dir / f"{ln_type_id}.xml"
                    if p0.is_file():
                        cand = p0
                    else:
                        for p in ln_dir.rglob(f"{ln_type_id}.xml"):
                            if p.is_file():
                                cand = p
                                break
                    if cand is not None:
                        tree = ET.parse(cand)
                        rr = tree.getroot()
                        ln = rr.find(f".//{_q(SCL_NS, 'LNodeType')}")
                        ln_class = (ln.attrib.get("lnClass") or "").strip() if ln is not None else ""
                        desc = (ln.attrib.get("desc") or "").strip() if ln is not None else ""
                        info = LNodeTypeInfo(id=ln_type_id, ln_class=ln_class, desc=desc, file_path=cand)
                except Exception:
                    info = None

            if info is None:
                raise ValueError("LNodeType not found")
            model = load_lnode_type(info)
            template_do_names = [d.name for d in (model.dos or []) if (d.name or "").strip()]
            self._hmi_ln_do_types_by_name = {d.name: (d.do_type or "").strip() for d in (model.dos or []) if d.name}
            try:
                self._hmi_ln_class = (getattr(getattr(model, "info", None), "ln_class", "") or "").strip()
            except Exception:
                self._hmi_ln_class = (getattr(info, "ln_class", "") or "").strip()
            if not self._hmi_ln_do_names:
                self._hmi_ln_do_names = template_do_names
                self._hmi_ln_do_names_source = "template"
        except Exception:
            if not self._hmi_ln_do_names:
                self._hmi_ln_do_names = []
            self._hmi_ln_do_types_by_name = {}
            self._hmi_ln_class = ""

            # Fallback 1: template DOI order exposed by the LN instance tab.
            try:
                if self.instance_editor is not None:
                    tpl = list(getattr(self.instance_editor, "_tpl_doi_names", []) or [])
                    if tpl:
                        if not self._hmi_ln_do_names:
                            self._hmi_ln_do_names = [str(x).strip() for x in tpl if str(x).strip()]
                            self._hmi_ln_do_names_source = "template"
            except Exception:
                pass

            # Fallback 2: DOI names present in the currently opened instance file.
            try:
                if not self._hmi_ln_do_names:
                    path = self._hmi_current_ln_instance_path()
                    if path is not None:
                        self._hmi_ln_do_names = self._hmi_parse_ln_instance_do_names(path)
            except Exception:
                pass

    def _hmi_doref_dropdown_values(self, *, current: str = "") -> list[str]:
        self._hmi_ensure_ln_do_suggestions_loaded()
        try:
            ln_ref = (getattr(self, "_hmi_ln_ref", "") or "").strip()
        except Exception:
            ln_ref = ""
        if ln_ref:
            prefix = f"{ln_ref}."
        else:
            try:
                ln_class = (getattr(self, "_hmi_ln_class", "") or "").strip()
            except Exception:
                ln_class = ""
            prefix = f"{ln_class}#." if ln_class else ""
        base = [""] + [prefix + n for n in (self._hmi_ln_do_names or [])]
        # Custom group functionality placeholder.
        if "bay.LLN0.SettingControl" not in base:
            base.append("bay.LLN0.SettingControl")
        cur = (current or "").strip()
        if cur and cur not in base:
            base.insert(1, cur)
        # Keep stable; do not sort so blank/current stay near top.
        return base

    def _hmi_daref_dropdown_values(self, *, do_ref: str, current: str = "") -> list[str]:
        self._hmi_ensure_ln_do_suggestions_loaded()

        def _dot(v: str) -> str:
            vv = (v or "").strip()
            if not vv:
                return ""
            return vv if vv.startswith(".") else ("." + vv)

        do_ref = (do_ref or "").strip()
        cur = _dot(current)
        if not do_ref:
            base = [""]
            if cur:
                base.append(cur)
            return base

        do_name = self._hmi_do_name_from_doref(do_ref)
        do_type = (getattr(self, "_hmi_ln_do_types_by_name", {}) or {}).get(do_name) or ""
        do_type = (do_type or "").strip()
        if not do_type:
            base = [""]
            if cur:
                base.append(cur)
            return base

        cache = getattr(self, "_hmi_ln_da_names_by_dotype", None)
        if cache is None:
            cache = {}
            self._hmi_ln_da_names_by_dotype = cache
        if do_type not in cache:
            cache[do_type] = self._hmi_parse_dotype_da_names(do_type)

        da_names = list(cache.get(do_type) or [])
        da_names = [_dot(n) for n in da_names]
        base = [""] + da_names
        if cur and cur not in base:
            base.insert(1, cur)
        return base

    def _hmi_inref_dropdown_values(self, *, current: str = "") -> list[str]:
        """Return dropdown values for IET_HARDLINK_DEFINITION.

        Values are sourced from the current LN's InRef purposes and formatted as:
        `.InRef%<purpose>`.
        """

        cur = (current or "").strip()

        # Prefer currently-open LN instance tab for live values.
        try:
            if self.instance_editor is not None and getattr(self.instance_editor, "doc", None) is not None:
                ln_el = self._current_ln_instance_element()
                if ln_el is not None:
                    inrefs = self._extract_inrefs_from_ln_element(ln_el)
                    vals: list[str] = []
                    seen: set[str] = set()
                    for it in (inrefs or []):
                        purpose = (it.get("purpose_clean") or "").strip()
                        if not purpose:
                            continue
                        v = f".InRef%{purpose}"
                        if v in seen:
                            continue
                        seen.add(v)
                        vals.append(v)
                    base = [""] + vals
                    if cur and cur not in base:
                        base.insert(1, cur)
                    return base
        except Exception:
            pass

        # Fallback: resolve LN instance via matching application LnRef.
        try:
            ln_ref_raw = self._hmi_read_matching_application_lnref()
        except Exception:
            ln_ref_raw = ""

        try:
            inst_path = self._guess_ln_instance_path_from_lnref(ln_ref_raw) if ln_ref_raw else None
        except Exception:
            inst_path = None

        if inst_path is None or not inst_path.exists():
            base = [""]
            if cur and cur not in base:
                base.append(cur)
            return base

        try:
            inst_mtime = float(inst_path.stat().st_mtime)
        except Exception:
            inst_mtime = None

        cache_path = getattr(self, "_hmi_inref_cached_path", None)
        cache_mtime = getattr(self, "_hmi_inref_cached_mtime", None)
        cache_vals = getattr(self, "_hmi_inref_cached_values", None)
        if (
            isinstance(cache_vals, list)
            and cache_path == inst_path
            and ((cache_mtime is None and inst_mtime is None) or (cache_mtime == inst_mtime))
        ):
            base = [""] + list(cache_vals)
            if cur and cur not in base:
                base.insert(1, cur)
            return base

        vals2: list[str] = []
        try:
            doc = load_ln_instance_document(Path(inst_path))
            lnref_norm = self._normalize_lnref(ln_ref_raw)
            ln_el = self._pick_ln_element_for_lnref(doc, lnref_norm)
            if ln_el is not None:
                inrefs = self._extract_inrefs_from_ln_element(ln_el)
                seen2: set[str] = set()
                for it in (inrefs or []):
                    purpose = (it.get("purpose_clean") or "").strip()
                    if not purpose:
                        continue
                    v = f".InRef%{purpose}"
                    if v in seen2:
                        continue
                    seen2.add(v)
                    vals2.append(v)
        except Exception:
            vals2 = []

        try:
            self._hmi_inref_cached_path = inst_path
            self._hmi_inref_cached_mtime = inst_mtime
            self._hmi_inref_cached_values = list(vals2)
        except Exception:
            pass

        base = [""] + vals2
        if cur and cur not in base:
            base.insert(1, cur)
        return base

    def _hmi_parse_dotype_da_names(self, do_type_id: str) -> list[str]:
        do_type_id = (do_type_id or "").strip()
        if not do_type_id:
            return []

        root = None
        try:
            root = getattr(self, "iec61850_dir", None)
        except Exception:
            root = None
        if root is None:
            return []

        do_dir = Path(root) / "DOType"
        if not do_dir.exists():
            return []

        # Fast path: file name often matches the DOType id.
        candidates: list[Path] = []
        try:
            direct = do_dir / f"{do_type_id}.xml"
            if direct.is_file():
                candidates.append(direct)
        except Exception:
            pass

        try:
            if not candidates:
                for p in do_dir.rglob(f"{do_type_id}.xml"):
                    if p.is_file():
                        candidates.append(p)
                        break
        except Exception:
            pass

        # Fallback: scan files for a matching DOType id.
        if not candidates:
            try:
                candidates = list(do_dir.rglob("*.xml"))
            except Exception:
                candidates = []

        def local(tag: str) -> str:
            return _local_name(tag) if isinstance(tag, str) else ""

        def find_da_type_el(da_type_id: str) -> ET.Element | None:
            da_type_id = (da_type_id or "").strip()
            if not da_type_id:
                return None

            cache = getattr(self, "_hmi_da_type_el_cache", None)
            if cache is None:
                cache = {}
                setattr(self, "_hmi_da_type_el_cache", cache)
            if da_type_id in cache:
                return cache.get(da_type_id)

            da_dir = Path(root) / "DAType"
            if not da_dir.exists():
                cache[da_type_id] = None
                return None

            # Fast path: matching file name.
            candidates0: list[Path] = []
            try:
                direct0 = da_dir / f"{da_type_id}.xml"
                if direct0.is_file():
                    candidates0.append(direct0)
            except Exception:
                pass
            try:
                if not candidates0:
                    for p0 in da_dir.rglob(f"{da_type_id}.xml"):
                        if p0.is_file():
                            candidates0.append(p0)
                            break
            except Exception:
                pass

            if not candidates0:
                try:
                    candidates0 = list(da_dir.rglob("*.xml"))
                except Exception:
                    candidates0 = []

            found0: ET.Element | None = None
            for p0 in candidates0:
                try:
                    tr0 = ET.parse(p0)
                    rr0 = tr0.getroot()
                    for el0 in rr0.findall(f".//{_q(SCL_NS, 'DAType')}"):
                        if (el0.attrib.get("id") or "").strip() == da_type_id:
                            found0 = el0
                            break
                    if found0 is not None:
                        break
                except Exception:
                    continue

            cache[da_type_id] = found0
            return found0

        def expand_struct(type_id: str, prefix: str, stack: set[str]) -> list[str]:
            type_id = (type_id or "").strip()
            if not type_id:
                return []
            if type_id in stack:
                return []
            stack.add(type_id)
            out0: list[str] = []
            dt = find_da_type_el(type_id)
            if dt is not None:
                for ch0 in list(dt):
                    if local(ch0.tag) != "BDA":
                        continue
                    nm0 = (ch0.attrib.get("name") or "").strip()
                    if not nm0:
                        continue
                    path0 = f"{prefix}{nm0}" if prefix else nm0
                    out0.append(path0)
                    btype0 = (ch0.attrib.get("bType") or "").strip()
                    t0 = (ch0.attrib.get("type") or "").strip()
                    if (btype0 or "").lower() == "struct" and t0:
                        out0.extend(expand_struct(t0, path0 + ".", stack))
            stack.remove(type_id)
            return out0

        def find_do_type_el(do_type_id0: str) -> ET.Element | None:
            do_type_id0 = (do_type_id0 or "").strip()
            if not do_type_id0:
                return None

            cache0 = getattr(self, "_hmi_do_type_el_cache", None)
            if cache0 is None:
                cache0 = {}
                setattr(self, "_hmi_do_type_el_cache", cache0)
            if do_type_id0 in cache0:
                return cache0.get(do_type_id0)

            if not do_dir.exists():
                cache0[do_type_id0] = None
                return None

            candidates0: list[Path] = []
            try:
                direct0 = do_dir / f"{do_type_id0}.xml"
                if direct0.is_file():
                    candidates0.append(direct0)
            except Exception:
                pass
            try:
                if not candidates0:
                    for p0 in do_dir.rglob(f"{do_type_id0}.xml"):
                        if p0.is_file():
                            candidates0.append(p0)
                            break
            except Exception:
                pass
            if not candidates0:
                try:
                    candidates0 = list(do_dir.rglob("*.xml"))
                except Exception:
                    candidates0 = []

            found0: ET.Element | None = None
            for p0 in candidates0:
                try:
                    tr0 = ET.parse(p0)
                    rr0 = tr0.getroot()
                    for el0 in rr0.findall(f".//{_q(SCL_NS, 'DOType')}"):
                        if (el0.attrib.get("id") or "").strip() == do_type_id0:
                            found0 = el0
                            break
                    if found0 is not None:
                        break
                except Exception:
                    continue

            cache0[do_type_id0] = found0
            return found0

        def expand_do_el(do_el: ET.Element, prefix: str, do_stack: set[str]) -> list[str]:
            out1: list[str] = []
            for ch1 in list(do_el):
                loc1 = local(ch1.tag)
                if loc1 == "DA":
                    nm1 = (ch1.attrib.get("name") or "").strip()
                    if not nm1:
                        continue
                    path1 = f"{prefix}{nm1}" if prefix else nm1
                    out1.append(path1)
                    btype1 = (ch1.attrib.get("bType") or "").strip()
                    t1 = (ch1.attrib.get("type") or "").strip()
                    if (btype1 or "").lower() == "struct" and t1:
                        out1.extend(expand_struct(t1, path1 + ".", set()))
                elif loc1 == "SDO":
                    nm1 = (ch1.attrib.get("name") or "").strip()
                    t1 = (ch1.attrib.get("type") or "").strip()
                    if not (nm1 and t1):
                        continue

                    # Add the SDO itself as an option (sub DO), e.g. 'phsAB'.
                    # This allows selecting '.phsAB' in daRef dropdown.
                    sdo_path = f"{prefix}{nm1}" if prefix else nm1
                    if sdo_path:
                        out1.append(sdo_path)

                    if t1 in do_stack:
                        continue
                    do_stack.add(t1)
                    sub_el = find_do_type_el(t1)
                    if sub_el is not None:
                        out1.extend(expand_do_el(sub_el, f"{prefix}{nm1}.", do_stack))
                    do_stack.remove(t1)
            return out1

        da_names: list[str] = []
        for path in candidates:
            try:
                tree = ET.parse(path)
                r = tree.getroot()
                found = None
                for el in r.findall(f".//{_q(SCL_NS, 'DOType')}"):
                    if (el.attrib.get("id") or "").strip() == do_type_id:
                        found = el
                        break
                if found is None:
                    continue

                try:
                    da_names.extend(expand_do_el(found, "", {do_type_id}))
                except Exception:
                    pass
                break
            except Exception:
                continue

        # Stable unique sort.
        seen: set[str] = set()
        uniq: list[str] = []
        for n in da_names:
            if n in seen:
                continue
            seen.add(n)
            uniq.append(n)
        uniq.sort(key=lambda s: (s or "").lower())
        return uniq

    def _hmi_dotype_cdc(self, do_type_id: str) -> str:
        do_type_id = (do_type_id or "").strip()
        if not do_type_id:
            return ""

        cache = getattr(self, "_hmi_dotype_cdc_cache", None)
        if cache is None:
            cache = {}
            setattr(self, "_hmi_dotype_cdc_cache", cache)
        if do_type_id in cache:
            try:
                return str(cache.get(do_type_id) or "")
            except Exception:
                return ""

        root = None
        try:
            root = getattr(self, "iec61850_dir", None)
        except Exception:
            root = None
        if root is None:
            cache[do_type_id] = ""
            return ""

        do_dir = Path(root) / "DOType"
        if not do_dir.exists():
            cache[do_type_id] = ""
            return ""

        candidates: list[Path] = []
        try:
            direct = do_dir / f"{do_type_id}.xml"
            if direct.is_file():
                candidates.append(direct)
        except Exception:
            pass
        try:
            if not candidates:
                for p in do_dir.rglob(f"{do_type_id}.xml"):
                    if p.is_file():
                        candidates.append(p)
                        break
        except Exception:
            pass
        if not candidates:
            try:
                candidates = list(do_dir.rglob("*.xml"))
            except Exception:
                candidates = []

        cdc = ""
        for p in candidates:
            try:
                rr = ET.parse(p).getroot()
            except Exception:
                continue
            try:
                for el in rr.findall(f".//{_q(SCL_NS, 'DOType')}"):
                    if (el.attrib.get("id") or "").strip() != do_type_id:
                        continue
                    cdc = (el.attrib.get("cdc") or "").strip()
                    break
            except Exception:
                pass
            if cdc:
                break

        cache[do_type_id] = cdc
        return cdc

    def _hmi_cdc_st_da_paths(self, do_type_id: str, *, cdc: str) -> list[str]:
        """Return DA/SDO paths (no leading dot) for a CDC where DA fc=ST.

        Excludes q/t (quality/timestamp). Used for auto-populating HMIDataItem rows
        during HMI Refresh.
        """

        do_type_id = (do_type_id or "").strip()
        cdc = (cdc or "").strip().upper()
        if not do_type_id or not cdc:
            return []

        cache = getattr(self, "_hmi_cdc_st_da_cache", None)
        if cache is None:
            cache = {}
            setattr(self, "_hmi_cdc_st_da_cache", cache)
        cache_key = (do_type_id, cdc)
        if cache_key in cache:
            try:
                return list(cache.get(cache_key) or [])
            except Exception:
                return []

        root0 = None
        try:
            root0 = getattr(self, "iec61850_dir", None)
        except Exception:
            root0 = None
        if root0 is None:
            cache[do_type_id] = []
            return []

        do_dir = Path(root0) / "DOType"
        da_dir = Path(root0) / "DAType"
        if not do_dir.exists():
            cache[do_type_id] = []
            return []

        def local(tag: str) -> str:
            return _local_name(tag) if isinstance(tag, str) else ""

        def _is_st_fc(v: str | None) -> bool:
            return (v or "").strip().upper() == "ST"

        def _is_excluded_path(path0: str) -> bool:
            last = (path0.split(".")[-1] if path0 else "").strip().lower()
            return last in {"q", "t"}

        def find_da_type_el(da_type_id: str) -> ET.Element | None:
            da_type_id = (da_type_id or "").strip()
            if not da_type_id:
                return None

            cache_dt = getattr(self, "_hmi_da_type_el_cache", None)
            if cache_dt is None:
                cache_dt = {}
                setattr(self, "_hmi_da_type_el_cache", cache_dt)
            if da_type_id in cache_dt:
                return cache_dt.get(da_type_id)

            if not da_dir.exists():
                cache_dt[da_type_id] = None
                return None

            candidates0: list[Path] = []
            try:
                direct0 = da_dir / f"{da_type_id}.xml"
                if direct0.is_file():
                    candidates0.append(direct0)
            except Exception:
                pass
            try:
                if not candidates0:
                    for p0 in da_dir.rglob(f"{da_type_id}.xml"):
                        if p0.is_file():
                            candidates0.append(p0)
                            break
            except Exception:
                pass
            if not candidates0:
                try:
                    candidates0 = list(da_dir.rglob("*.xml"))
                except Exception:
                    candidates0 = []

            found0: ET.Element | None = None
            for p0 in candidates0:
                try:
                    tr0 = ET.parse(p0)
                    rr0 = tr0.getroot()
                    for el0 in rr0.findall(f".//{_q(SCL_NS, 'DAType')}"):
                        if (el0.attrib.get("id") or "").strip() == da_type_id:
                            found0 = el0
                            break
                    if found0 is not None:
                        break
                except Exception:
                    continue

            cache_dt[da_type_id] = found0
            return found0

        def find_do_type_el(do_type_id0: str) -> ET.Element | None:
            do_type_id0 = (do_type_id0 or "").strip()
            if not do_type_id0:
                return None

            cache_do = getattr(self, "_hmi_do_type_el_cache", None)
            if cache_do is None:
                cache_do = {}
                setattr(self, "_hmi_do_type_el_cache", cache_do)
            if do_type_id0 in cache_do:
                return cache_do.get(do_type_id0)

            candidates0: list[Path] = []
            try:
                direct0 = do_dir / f"{do_type_id0}.xml"
                if direct0.is_file():
                    candidates0.append(direct0)
            except Exception:
                pass
            try:
                if not candidates0:
                    for p0 in do_dir.rglob(f"{do_type_id0}.xml"):
                        if p0.is_file():
                            candidates0.append(p0)
                            break
            except Exception:
                pass
            if not candidates0:
                try:
                    candidates0 = list(do_dir.rglob("*.xml"))
                except Exception:
                    candidates0 = []

            found0: ET.Element | None = None
            for p0 in candidates0:
                try:
                    tr0 = ET.parse(p0)
                    rr0 = tr0.getroot()
                    for el0 in rr0.findall(f".//{_q(SCL_NS, 'DOType')}"):
                        if (el0.attrib.get("id") or "").strip() == do_type_id0:
                            found0 = el0
                            break
                    if found0 is not None:
                        break
                except Exception:
                    continue

            cache_do[do_type_id0] = found0
            return found0

        def expand_struct(type_id: str, prefix: str, stack: set[str]) -> list[str]:
            type_id = (type_id or "").strip()
            if not type_id:
                return []
            if type_id in stack:
                return []
            stack.add(type_id)
            out0: list[str] = []
            dt = find_da_type_el(type_id)
            if dt is not None:
                for ch0 in list(dt):
                    if local(ch0.tag) != "BDA":
                        continue
                    nm0 = (ch0.attrib.get("name") or "").strip()
                    if not nm0:
                        continue
                    path0 = f"{prefix}{nm0}" if prefix else nm0
                    if not _is_excluded_path(path0):
                        out0.append(path0)
                    btype0 = (ch0.attrib.get("bType") or "").strip()
                    t0 = (ch0.attrib.get("type") or "").strip()
                    if (btype0 or "").lower() == "struct" and t0:
                        out0.extend(expand_struct(t0, path0 + ".", stack))
            stack.remove(type_id)
            return out0

        def expand_do_el(do_el: ET.Element, prefix: str, do_stack: set[str]) -> list[str]:
            out1: list[str] = []
            for ch1 in list(do_el):
                loc1 = local(ch1.tag)
                if loc1 == "DA":
                    nm1 = (ch1.attrib.get("name") or "").strip()
                    if not nm1:
                        continue
                    if not _is_st_fc(ch1.attrib.get("fc")):
                        continue
                    path1 = f"{prefix}{nm1}" if prefix else nm1
                    if _is_excluded_path(path1):
                        continue
                    out1.append(path1)
                    btype1 = (ch1.attrib.get("bType") or "").strip()
                    t1 = (ch1.attrib.get("type") or "").strip()
                    if (btype1 or "").lower() == "struct" and t1:
                        out1.extend(expand_struct(t1, path1 + ".", set()))
                elif loc1 == "SDO":
                    nm1 = (ch1.attrib.get("name") or "").strip()
                    t1 = (ch1.attrib.get("type") or "").strip()
                    if not (nm1 and t1):
                        continue
                    if t1 in do_stack:
                        continue
                    do_stack.add(t1)
                    sub_el = find_do_type_el(t1)
                    if sub_el is not None:
                        out1.extend(expand_do_el(sub_el, f"{prefix}{nm1}.", do_stack))
                    do_stack.remove(t1)
            return out1

        do_el0 = find_do_type_el(do_type_id)
        if do_el0 is None:
            cache[cache_key] = []
            return []

        if (do_el0.attrib.get("cdc") or "").strip().upper() != cdc:
            cache[cache_key] = []
            return []

        try:
            paths = expand_do_el(do_el0, "", {do_type_id})
        except Exception:
            paths = []

        # Stable unique sort.
        seen: set[str] = set()
        uniq: list[str] = []
        for n in paths:
            if n in seen:
                continue
            seen.add(n)
            uniq.append(n)
        cache[cache_key] = uniq
        return uniq

    def _hmi_acd_st_da_paths(self, do_type_id: str) -> list[str]:
        """Return DA/SDO paths (no leading dot) for ACD CDC with fc=ST.

        Excludes q/t (quality/timestamp). Used for auto-populating HMIDataItem rows
        when an ACD-type DO is added via HMI Refresh.
        """

        return self._hmi_cdc_st_da_paths(do_type_id, cdc="ACD")

    def _hmi_act_st_da_paths(self, do_type_id: str) -> list[str]:
        """Return DA/SDO paths (no leading dot) for ACT CDC with fc=ST.

        Excludes q/t (quality/timestamp). Used for auto-populating HMIDataItem rows
        when an ACT-type DO is added via HMI Refresh.
        """

        return self._hmi_cdc_st_da_paths(do_type_id, cdc="ACT")

    def _hmi_find_do_type_el(self, do_type_id: str) -> ET.Element | None:
        do_type_id = (do_type_id or "").strip()
        if not do_type_id:
            return None

        cache_do = getattr(self, "_hmi_do_type_el_cache", None)
        if cache_do is None:
            cache_do = {}
            setattr(self, "_hmi_do_type_el_cache", cache_do)
        if do_type_id in cache_do:
            return cache_do.get(do_type_id)

        root0 = None
        try:
            root0 = getattr(self, "iec61850_dir", None)
        except Exception:
            root0 = None
        if root0 is None:
            cache_do[do_type_id] = None
            return None

        do_dir = Path(root0) / "DOType"
        if not do_dir.exists():
            cache_do[do_type_id] = None
            return None

        candidates0: list[Path] = []
        try:
            direct0 = do_dir / f"{do_type_id}.xml"
            if direct0.is_file():
                candidates0.append(direct0)
        except Exception:
            pass
        try:
            if not candidates0:
                for p0 in do_dir.rglob(f"{do_type_id}.xml"):
                    if p0.is_file():
                        candidates0.append(p0)
                        break
        except Exception:
            pass
        if not candidates0:
            try:
                candidates0 = list(do_dir.rglob("*.xml"))
            except Exception:
                candidates0 = []

        found0: ET.Element | None = None
        for p0 in candidates0:
            try:
                tr0 = ET.parse(p0)
                rr0 = tr0.getroot()
                for el0 in rr0.findall(f".//{_q(SCL_NS, 'DOType')}"):
                    if (el0.attrib.get("id") or "").strip() == do_type_id:
                        found0 = el0
                        break
                if found0 is not None:
                    break
            except Exception:
                continue

        cache_do[do_type_id] = found0
        return found0

    def _hmi_measure_da_paths_for_do_type(self, do_type_id: str) -> list[str]:
        """Return desired DA/SDO paths (no leading dot) for supported CDCs.

        Rules (per user request):
        - DEL/WYE/SEQ: add all CMV SDOs (including e.g. neut) + each's cVal/units
        - CMV: cVal + units
        - MV: mag + units
        """

        do_type_id0 = (do_type_id or "").strip()
        if not do_type_id0:
            return []

        cdc0 = (self._hmi_dotype_cdc(do_type_id0) or "").strip().upper()

        if cdc0 == "CMV":
            return ["cVal", "units"]
        if cdc0 == "MV":
            return ["mag", "units"]

        def _fallback_bases(cdc1: str) -> list[str]:
            if cdc1 == "DEL":
                return ["phsAB", "phsBC", "phsCA"]
            if cdc1 == "WYE":
                return ["phsA", "phsB", "phsC"]
            if cdc1 == "SEQ":
                return ["c1", "c2", "c3"]
            return []

        if cdc0 in {"DEL", "WYE", "SEQ"}:
            out: list[str] = []
            do_el0 = self._hmi_find_do_type_el(do_type_id0)
            if do_el0 is not None:
                try:
                    for ch in list(do_el0):
                        if not (isinstance(ch.tag, str) and _local_name(ch.tag) == "SDO"):
                            continue
                        nm = (ch.attrib.get("name") or "").strip()
                        t = (ch.attrib.get("type") or "").strip()
                        if not (nm and t):
                            continue
                        if (self._hmi_dotype_cdc(t) or "").strip().upper() != "CMV":
                            continue
                        out.append(nm)
                        out.append(f"{nm}.cVal")
                        out.append(f"{nm}.units")
                except Exception:
                    out = []

            if not out:
                for b in _fallback_bases(cdc0):
                    out.append(b)
                    out.append(f"{b}.cVal")
                    out.append(f"{b}.units")

            # Stable unique keep order.
            seen: set[str] = set()
            uniq: list[str] = []
            for p in out:
                p0 = (p or "").strip()
                if not p0 or p0 in seen:
                    continue
                seen.add(p0)
                uniq.append(p0)
            return uniq

        return []

    def _hmi_measure_da_entries_for_do_type(self, do_type_id: str) -> list[tuple[str, int]]:
        """Return desired DA/SDO paths and their groupid for supported CDCs.

        groupid rules (per user request):
        - DEL/WYE/SEQ: each CMV SDO gets its own groupid (1..N). The SDO row itself
          and its cVal/units rows share that same groupid.
        - CMV/MV: all generated rows share groupid=1.
        """

        do_type_id0 = (do_type_id or "").strip()
        if not do_type_id0:
            return []
        cdc0 = (self._hmi_dotype_cdc(do_type_id0) or "").strip().upper()

        if cdc0 == "CMV":
            return [("cVal", 1), ("units", 1)]
        if cdc0 == "MV":
            return [("mag", 1), ("units", 1)]

        def _fallback_bases(cdc1: str) -> list[str]:
            if cdc1 == "DEL":
                return ["phsAB", "phsBC", "phsCA"]
            if cdc1 == "WYE":
                return ["phsA", "phsB", "phsC"]
            if cdc1 == "SEQ":
                return ["c1", "c2", "c3"]
            return []

        if cdc0 in {"DEL", "WYE", "SEQ"}:
            bases: list[str] = []
            do_el0 = self._hmi_find_do_type_el(do_type_id0)
            if do_el0 is not None:
                try:
                    for ch in list(do_el0):
                        if not (isinstance(ch.tag, str) and _local_name(ch.tag) == "SDO"):
                            continue
                        nm = (ch.attrib.get("name") or "").strip()
                        t = (ch.attrib.get("type") or "").strip()
                        if not (nm and t):
                            continue
                        if (self._hmi_dotype_cdc(t) or "").strip().upper() != "CMV":
                            continue
                        bases.append(nm)
                except Exception:
                    bases = []

            if not bases:
                bases = _fallback_bases(cdc0)

            out: list[tuple[str, int]] = []
            gid = 1
            for b in bases:
                b0 = (b or "").strip()
                if not b0:
                    continue
                out.append((b0, gid))
                out.append((f"{b0}.cVal", gid))
                out.append((f"{b0}.units", gid))
                gid += 1

            # Stable unique keep order: first occurrence wins groupid.
            seen: set[str] = set()
            uniq: list[tuple[str, int]] = []
            for p, g in out:
                p0 = (p or "").strip()
                if not p0 or p0 in seen:
                    continue
                seen.add(p0)
                uniq.append((p0, int(g)))
            return uniq

        return []

    def _hmi_sync_dataitems_for_do(
        self,
        parent_it: ET.Element,
        *,
        full_do: str,
        do_type_id: str,
        prune_extra: bool = False,
    ) -> bool:
        """Sync required HMIDataItem children for the DO's CDC.

        Adds missing items and (optionally) stage-removes extras. Returns True if
        any add/remove happened.
        """

        do_type_id = (do_type_id or "").strip()
        if not do_type_id:
            return False
        entries = self._hmi_measure_da_entries_for_do_type(do_type_id)
        if not entries:
            return False

        def _norm_da_ref(v: str) -> str:
            vv = (v or "").strip()
            if vv.startswith("."):
                vv = vv[1:]
            return vv

        expected_gid_by_path: dict[str, int] = {}
        for p, gid in entries:
            p0 = _norm_da_ref(p)
            if not p0:
                continue
            expected_gid_by_path[p0] = int(gid)

        expected: set[str] = set(expected_gid_by_path.keys())
        expected.discard("")

        existing_by_path: dict[str, ET.Element] = {}
        try:
            for ch in list(parent_it):
                if not (isinstance(ch.tag, str) and _local_name(ch.tag) == "HMIDataItem"):
                    continue
                da0 = _norm_da_ref(ch.attrib.get("daRef") or "")
                if da0:
                    existing_by_path[da0] = ch
        except Exception:
            existing_by_path = {}

        changed_any = False

        if prune_extra and expected:
            for da0, ch in list(existing_by_path.items()):
                if da0 in expected:
                    continue
                if self._hmi_ui_is_added(ch):
                    try:
                        parent_it.remove(ch)
                    except Exception:
                        pass
                else:
                    self._hmi_ui_tag_set(ch, "removed")
                changed_any = True
                existing_by_path.pop(da0, None)

        for p, gid in entries:
            p0 = (p or "").strip()
            if not p0:
                continue
            existing = existing_by_path.get(p0)
            if existing is None:
                di = ET.SubElement(parent_it, _q(HMI_CUST_NS, "HMIDataItem"))
                di.attrib["name"] = (p0.rsplit(".", 1)[-1] or "").strip()
                di.attrib["groupid"] = str(int(gid))
                di.attrib["doRef"] = (full_do or "").strip()
                di.attrib["daRef"] = f".{p0}"
                self._hmi_ui_tag_set(di, "added")
                changed_any = True
                existing_by_path[p0] = di
            else:
                # Keep groupid in sync even when item already exists.
                want = str(int(expected_gid_by_path.get(p0, int(gid))))
                cur = (existing.attrib.get("groupid") or "").strip()
                if cur != want:
                    try:
                        existing.attrib["groupid"] = want
                        self._hmi_ui_tag_set(existing, "changed")
                        changed_any = True
                    except Exception:
                        pass

        return changed_any

    def _refresh_hmi_menu_table(self, *, select_first: bool, open_selection_path: bool = True) -> None:
        tv = self._hmi_tv_menus
        if tv is None:
            return

        # Preserve selection element identity so we can re-select and open only its path.
        prev_sel_el: ET.Element | None = None
        # Preserve open/closed state per element so refresh does not change the current display.
        open_by_el: dict[ET.Element, bool] = {}
        try:
            sel0 = tv.selection()
            if sel0:
                node0 = self._hmi_tree_iid_to_node.get(sel0[0])
                if node0 is not None:
                    _k0, _p0, e0 = node0
                    if isinstance(e0, ET.Element):
                        prev_sel_el = e0
        except Exception:
            prev_sel_el = None

        try:
            for iid, node in (self._hmi_tree_iid_to_node or {}).items():
                try:
                    _k, _p, e = node
                except Exception:
                    continue
                if not isinstance(e, ET.Element):
                    continue
                try:
                    open_by_el[e] = bool(tv.item(iid, "open"))
                except Exception:
                    pass
        except Exception:
            pass

        self._hmi_menu_iid_to_el.clear()
        self._hmi_tree_iid_to_kind.clear()
        self._hmi_tree_iid_to_node.clear()
        self._hmi_tree_iid_to_ref_link.clear()
        try:
            self._hmi_end_cell_edit(commit=True)
            self._hmi_end_combo_edit(commit=True)
        except Exception:
            pass
        try:
            for iid in tv.get_children(""):
                tv.delete(iid)
        except Exception:
            pass

        root = self._hmi_root
        if root is None:
            return

        menus = self._hmi_all_menus()
        menu_by_name: dict[str, ET.Element] = {}
        menu_names: list[str] = []
        for menu in menus:
            name = (menu.attrib.get("name") or "").strip()
            if not name:
                continue
            menu_by_name[name] = menu
            menu_names.append(name)

        referenced_names: set[str] = set()
        for menu in menu_by_name.values():
            for ch in list(menu):
                if not (isinstance(ch.tag, str) and _local_name(ch.tag) == "HMIMenuItem"):
                    continue
                ref = (ch.attrib.get("ref") or "").strip()
                if ref:
                    referenced_names.add(ref)

        root_names = [n for n in menu_names if n not in referenced_names]
        if not root_names:
            root_names = menu_names

        idx = 0
        first_top: str | None = None

        def _tags_for_state(*, removed: bool, added: bool, changed: bool) -> tuple[str, ...]:
            if removed:
                return ("removed",)
            if added:
                return ("added",)
            if changed:
                return ("changed",)
            return ()

        def _tags_for_el(el: ET.Element, *, inherited_removed: bool = False) -> tuple[str, ...]:
            return _tags_for_state(
                removed=inherited_removed or self._hmi_ui_is_removed(el),
                added=(not inherited_removed) and self._hmi_ui_is_added(el),
                changed=(not inherited_removed) and self._hmi_ui_is_changed(el),
            )

        def _tags_for_ref_node(menu_el: ET.Element, ref_link_el: ET.Element, *, inherited_removed: bool = False) -> tuple[str, ...]:
            removed = inherited_removed or self._hmi_ui_is_removed(ref_link_el) or self._hmi_ui_is_removed(menu_el)
            added = (not removed) and (self._hmi_ui_is_added(ref_link_el) or self._hmi_ui_is_added(menu_el))
            changed = (not removed) and (self._hmi_ui_is_changed(ref_link_el) or self._hmi_ui_is_changed(menu_el))
            return _tags_for_state(removed=removed, added=added, changed=changed)

        def _instantiate_cell(menu_el: ET.Element) -> str:
            v = (menu_el.attrib.get("instantiate") or "").strip().lower()
            return "☑" if v == "false" else "☐"

        def _menu_values(menu_el: ET.Element) -> dict[str, str]:
            return {
                "desc": menu_el.attrib.get("desc") or "",
                "value": "",
                "instantiate": _instantiate_cell(menu_el),
                "langRef": menu_el.attrib.get("langRef") or "",
                "hmiMenuDataType": menu_el.attrib.get("hmiMenuDataType") or "",
                "hmiMenuViewType": menu_el.attrib.get("hmiMenuViewType") or "",
                "hmiSubTreeType": menu_el.attrib.get("hmiSubTreeType") or "",
                "doRef": "",
                "daRef": "",
                "hideunit": "",
            }

        def _hideunit_cell(item_el: ET.Element) -> str:
            try:
                opt = (item_el.attrib.get("attrOption") or "").strip()
            except Exception:
                opt = ""
            if not opt:
                return "☐"
            parts = [p for p in re.split(r"[\s,;|]+", opt) if p]
            return "☑" if any(p.strip().lower() == "hideunits" for p in parts) else "☐"

        def _item_values(item_el: ET.Element) -> dict[str, str]:
            return {
                "desc": "",
                "value": "",
                "instantiate": "",
                "langRef": "",
                "hmiMenuDataType": "",
                "hmiMenuViewType": "",
                "hmiSubTreeType": "",
                "doRef": item_el.attrib.get("doRef") or "",
                "daRef": item_el.attrib.get("daRef") or "",
                "hideunit": _hideunit_cell(item_el),
            }

        def _data_values(data_el: ET.Element) -> dict[str, str]:
            return {
                "desc": "",
                "value": "",
                "instantiate": "",
                "langRef": "",
                "hmiMenuDataType": "",
                "hmiMenuViewType": "",
                "hmiSubTreeType": "",
                "doRef": "",
                "daRef": data_el.attrib.get("daRef") or "",
                "hideunit": "",
            }

        def _attr_values(attr_el: ET.Element) -> dict[str, str]:
            # HMIAttr is displayed as a row under its parent menu.
            v = (attr_el.attrib.get("value") or attr_el.attrib.get("val") or "").strip()
            return {
                "desc": "",
                "value": v,
                "instantiate": "",
                "langRef": "",
                "hmiMenuDataType": "",
                "hmiMenuViewType": "",
                "hmiSubTreeType": "",
                "doRef": "",
                "daRef": "",
                "hideunit": "",
            }

        def _values_tuple(values_by_col: dict[str, str]) -> tuple[str, ...]:
            # Treeview values must match the current column set (IED vs IET differ).
            cols = list(tv["columns"])
            return tuple(values_by_col.get(c, "") for c in cols)

        def _insert_menu_node(parent_iid: str, menu_el: ET.Element, kind: str, *, tags: tuple[str, ...] = ()) -> str | None:
            nonlocal idx, first_top
            name = (menu_el.attrib.get("name") or "").strip()
            iid = f"m{idx}"
            idx += 1
            try:
                tv.insert(
                    parent_iid,
                    "end",
                    iid=iid,
                    text=name,
                    values=_values_tuple(_menu_values(menu_el)),
                    tags=tags,
                    open=bool(open_by_el.get(menu_el, False)),
                )
            except Exception:
                return None
            if parent_iid == "" and first_top is None:
                first_top = iid
            self._hmi_menu_iid_to_el[iid] = (root, menu_el)
            self._hmi_tree_iid_to_kind[iid] = kind
            self._hmi_tree_iid_to_node[iid] = ("menu", root, menu_el)
            return iid

        def _insert_missing_ref(parent_iid: str, ref_name: str) -> None:
            nonlocal idx
            iid = f"m{idx}"
            idx += 1
            try:
                tv.insert(parent_iid, "end", iid=iid, text=ref_name, values=_values_tuple({}))
            except Exception:
                return
            self._hmi_tree_iid_to_kind[iid] = "missing_ref"
            self._hmi_tree_iid_to_node[iid] = ("missing_ref", None, None)

        def _insert_attr_node(parent_iid: str, parent_menu_el: ET.Element, attr_el: ET.Element, *, inherited_removed: bool) -> None:
            nonlocal idx
            name = (attr_el.attrib.get("name") or "").strip() or "HMIAttr"
            iid = f"m{idx}"
            idx += 1
            try:
                tv.insert(
                    parent_iid,
                    "end",
                    iid=iid,
                    text=name,
                    values=_values_tuple(_attr_values(attr_el)),
                    tags=_tags_for_el(attr_el, inherited_removed=inherited_removed),
                )
            except Exception:
                return
            self._hmi_tree_iid_to_kind[iid] = "attr"
            self._hmi_tree_iid_to_node[iid] = ("attr", parent_menu_el, attr_el)

        def _insert_item_node(
            parent_iid: str,
            parent_menu_el: ET.Element,
            item_el: ET.Element,
            *,
            inherited_removed: bool,
        ) -> None:
            nonlocal idx
            do_ref0 = (item_el.attrib.get("doRef") or "").strip()
            if do_ref0:
                name = self._hmi_do_name_from_doref(do_ref0)
            else:
                name = (item_el.attrib.get("name") or "").strip()
            iid = f"m{idx}"
            idx += 1
            try:
                tv.insert(
                    parent_iid,
                    "end",
                    iid=iid,
                    text=name,
                    values=_values_tuple(_item_values(item_el)),
                    tags=_tags_for_el(item_el, inherited_removed=inherited_removed),
                    open=bool(open_by_el.get(item_el, False)),
                )
            except Exception:
                return
            self._hmi_tree_iid_to_kind[iid] = "item"
            self._hmi_tree_iid_to_node[iid] = ("item", parent_menu_el, item_el)

            # Level 4: DA rows under DO (HMIDataItem)
            for ch in list(item_el):
                if not (isinstance(ch.tag, str) and _local_name(ch.tag) == "HMIDataItem"):
                    continue
                da_ref0 = (ch.attrib.get("daRef") or "").strip()
                if da_ref0.startswith("."):
                    da_ref0 = da_ref0[1:]
                if "." in da_ref0:
                    da_ref0 = (da_ref0.rsplit(".", 1)[-1] or "").strip()
                di_name = da_ref0 if da_ref0 else (ch.attrib.get("name") or "").strip()
                di_iid = f"m{idx}"
                idx += 1
                try:
                    tv.insert(
                        iid,
                        "end",
                        iid=di_iid,
                        text=di_name,
                        values=_values_tuple(_data_values(ch)),
                        tags=_tags_for_el(ch, inherited_removed=inherited_removed or self._hmi_ui_is_removed(item_el)),
                    )
                except Exception:
                    continue
                self._hmi_tree_iid_to_kind[di_iid] = "data"
                self._hmi_tree_iid_to_node[di_iid] = ("data", item_el, ch)

        def _populate_menu_children(
            menu_iid: str,
            menu_el: ET.Element,
            *,
            inherited_removed: bool,
            path: tuple[str, ...],
            depth: int,
        ) -> None:
            # Menu hierarchy depth is not fixed. Expand HMIMenuItem/@ref recursively
            # based on the actual HMI file content.
            if depth > 16:
                return
            menu_removed = inherited_removed or self._hmi_ui_is_removed(menu_el)
            cur_name = (menu_el.attrib.get("name") or "").strip()
            new_path = path + ((cur_name,) if cur_name else ())
            for ch in list(menu_el):
                if not isinstance(ch.tag, str):
                    continue

                local = _local_name(ch.tag)
                if local == "HMIAttr":
                    _insert_attr_node(menu_iid, menu_el, ch, inherited_removed=menu_removed)
                    continue

                if local != "HMIMenuItem":
                    continue
                ref = (ch.attrib.get("ref") or "").strip()
                if ref:
                    ref_menu = menu_by_name.get(ref)
                    if ref_menu is None:
                        _insert_missing_ref(menu_iid, ref)
                        continue
                    sub_iid = _insert_menu_node(
                        menu_iid,
                        ref_menu,
                        kind="ref_menu",
                        tags=_tags_for_ref_node(ref_menu, ch, inherited_removed=menu_removed),
                    )
                    if sub_iid:
                        # Track the ref-link element (HMIMenuItem with ref=...) so we can delete/move/paste.
                        try:
                            self._hmi_tree_iid_to_ref_link[sub_iid] = (menu_el, ch)
                        except Exception:
                            pass
                        # Prevent infinite recursion on cyclic refs.
                        if ref not in new_path:
                            _populate_menu_children(
                                sub_iid,
                                ref_menu,
                                inherited_removed=menu_removed or self._hmi_ui_is_removed(ch),
                                path=new_path,
                                depth=depth + 1,
                            )
                    continue

                # No ref => treat as DO config row
                _insert_item_node(menu_iid, menu_el, ch, inherited_removed=menu_removed)

        for top_name in root_names:
            top_menu = menu_by_name.get(top_name)
            if top_menu is None:
                continue
            top_iid = _insert_menu_node("", top_menu, kind="menu", tags=_tags_for_el(top_menu))
            if top_iid:
                _populate_menu_children(top_iid, top_menu, inherited_removed=False, path=(), depth=0)

        if select_first:
            try:
                if first_top is not None:
                    tv.selection_set(first_top)
            except Exception:
                pass

        # Restore selection; optionally open its path.
        try:
            if prev_sel_el is not None:
                new_iid = self._hmi_find_iid_for_element(prev_sel_el)
                if new_iid:
                    tv.selection_set(new_iid)
                    if open_selection_path:
                        self._hmi_open_iid_path(new_iid, open_self=True)
            else:
                if select_first and first_top is not None:
                    if open_selection_path:
                        self._hmi_open_iid_path(first_top, open_self=True)
        except Exception:
            pass

        try:
            self._hmi_update_hmi_action_state()
        except Exception:
            pass

        try:
            self._hmi_update_fold_all_button()
        except Exception:
            pass

    def _hmi_selected_tree_iid(self) -> str | None:
        tv = self._hmi_tv_menus
        if tv is None:
            return None
        try:
            sel = tv.selection()
        except Exception:
            sel = ()
        if not sel:
            return None
        return sel[0]

    def _hmi_selected_tree_kind(self) -> str:
        iid = self._hmi_selected_tree_iid()
        if not iid:
            return ""
        try:
            return (self._hmi_tree_iid_to_kind.get(iid) or "").strip()
        except Exception:
            return ""

    def _hmi_update_hmi_action_state(self) -> None:
        """Update toolbar button enabled states based on current selection.

        Rules:
        - DA level (data) is leaf: only Delete is enabled.
        - missing_ref rows: actions disabled.
        """

        def set_btn(btn: ttk.Button | None, enabled: bool) -> None:
            if btn is None:
                return
            try:
                btn.configure(state=("normal" if enabled else "disabled"))
            except Exception:
                pass

        def can_move(delta: int) -> bool:
            try:
                return self._hmi_can_move_selected(delta)
            except Exception:
                return False

        kind = self._hmi_selected_tree_kind()
        is_leaf = kind == "data"
        is_missing = kind == "missing_ref"
        has_sel = bool(kind)

        # Default: nothing selected -> allow adding a top menu only.
        if not has_sel:
            set_btn(self._hmi_btn_add, self._hmi_root is not None)
            set_btn(self._hmi_btn_insert, False)
            set_btn(self._hmi_btn_edit, False)
            set_btn(self._hmi_btn_copy, False)
            set_btn(self._hmi_btn_cut, False)
            set_btn(self._hmi_btn_paste, self._hmi_root is not None and self._hmi_clipboard is not None)
            set_btn(self._hmi_btn_delete, False)
            set_btn(getattr(self, "_hmi_btn_up", None), False)
            set_btn(getattr(self, "_hmi_btn_down", None), False)
            return

        if is_missing:
            set_btn(self._hmi_btn_add, False)
            set_btn(self._hmi_btn_insert, False)
            set_btn(self._hmi_btn_edit, False)
            set_btn(self._hmi_btn_copy, False)
            set_btn(self._hmi_btn_cut, False)
            set_btn(self._hmi_btn_paste, False)
            set_btn(self._hmi_btn_delete, False)
            set_btn(getattr(self, "_hmi_btn_up", None), False)
            set_btn(getattr(self, "_hmi_btn_down", None), False)
            return

        if is_leaf:
            set_btn(self._hmi_btn_add, False)
            set_btn(self._hmi_btn_insert, False)
            set_btn(self._hmi_btn_edit, False)
            set_btn(self._hmi_btn_copy, False)
            set_btn(self._hmi_btn_cut, False)
            set_btn(self._hmi_btn_paste, False)
            set_btn(self._hmi_btn_delete, True)
            # Level 4 (DA rows): allow ordering.
            set_btn(getattr(self, "_hmi_btn_up", None), can_move(-1))
            set_btn(getattr(self, "_hmi_btn_down", None), can_move(1))
            return

        # Non-leaf nodes.
        set_btn(self._hmi_btn_add, self._hmi_root is not None and kind in {"menu", "ref_menu", "item", "attr"})
        set_btn(self._hmi_btn_insert, self._hmi_root is not None and kind in {"menu", "ref_menu", "item", "attr"})
        set_btn(self._hmi_btn_edit, self._hmi_root is not None and kind in {"menu", "ref_menu", "item", "attr"})
        set_btn(self._hmi_btn_copy, kind in {"menu", "ref_menu", "item", "attr"})
        set_btn(self._hmi_btn_cut, kind in {"menu", "ref_menu", "item", "attr"})
        # Paste is sibling-level: require clipboard kind matches selection kind (or no selection for top menu).
        can_paste = False
        if self._hmi_root is not None and self._hmi_clipboard is not None:
            try:
                ck, _cel = self._hmi_clipboard
            except Exception:
                ck = ""
            can_paste = (ck == kind) or (ck == "attr" and kind in {"menu", "ref_menu"})
        set_btn(self._hmi_btn_paste, can_paste)
        set_btn(self._hmi_btn_delete, kind in {"menu", "ref_menu", "item", "attr"})

        # Up/Down only for level 2-4.
        if kind in {"ref_menu", "item", "attr"}:
            set_btn(getattr(self, "_hmi_btn_up", None), can_move(-1))
            set_btn(getattr(self, "_hmi_btn_down", None), can_move(1))
        else:
            set_btn(getattr(self, "_hmi_btn_up", None), False)
            set_btn(getattr(self, "_hmi_btn_down", None), False)

    def _hmi_build_tree_context_menu(self) -> tk.Menu:
        m = tk.Menu(self, tearoff=0)
        m.add_command(label="Add", command=self._hmi_action_add)
        m.add_command(label="Insert", command=self._hmi_action_insert)
        m.add_command(label="Edit", command=self._hmi_action_edit)
        m.add_command(label="Select parent menu", command=self._hmi_action_select_parent_menu)
        m.add_separator()
        m.add_command(label="Copy", command=self._hmi_action_copy)
        m.add_command(label="Cut", command=self._hmi_action_cut)
        m.add_command(label="Paste", command=self._hmi_action_paste)
        m.add_separator()
        m.add_command(label="Delete", command=self._hmi_action_delete)
        m.add_separator()
        m.add_command(label="Up", command=self._hmi_action_move_up)
        m.add_command(label="Down", command=self._hmi_action_move_down)
        return m

    def _hmi_configure_tree_context_menu(self, kind: str) -> None:
        """Enable/disable context menu entries based on selection kind."""
        if self._hmi_tree_ctx_menu is None:
            return

        def set_state(label: str, enabled: bool) -> None:
            try:
                self._hmi_tree_ctx_menu.entryconfigure(label, state=("normal" if enabled else "disabled"))
            except Exception:
                pass

        if not kind:
            for lb in (
                "Add",
                "Insert",
                "Edit",
                "Select parent menu",
                "Copy",
                "Cut",
                "Paste",
                "Delete",
                "Up",
                "Down",
            ):
                set_state(lb, False)
            return

        if kind == "missing_ref":
            for lb in (
                "Add",
                "Insert",
                "Edit",
                "Select parent menu",
                "Copy",
                "Cut",
                "Paste",
                "Delete",
                "Up",
                "Down",
            ):
                set_state(lb, False)
            return

        if kind == "data":
            for lb in ("Add", "Insert", "Edit", "Copy", "Cut", "Paste"):
                set_state(lb, False)
            set_state("Select parent menu", False)
            set_state("Delete", True)
            set_state("Up", self._hmi_can_move_selected(-1))
            set_state("Down", self._hmi_can_move_selected(1))
            return

        if kind == "attr":
            set_state("Add", self._hmi_root is not None)
            set_state("Insert", self._hmi_root is not None)
            set_state("Edit", self._hmi_root is not None)
            set_state("Select parent menu", False)
            set_state("Copy", True)
            set_state("Cut", self._hmi_root is not None)
            can_paste = False
            if self._hmi_root is not None and self._hmi_clipboard is not None:
                try:
                    ck, _el = self._hmi_clipboard
                except Exception:
                    ck = ""
                can_paste = ck == "attr"
            set_state("Paste", can_paste)
            set_state("Delete", self._hmi_root is not None)
            set_state("Up", self._hmi_can_move_selected(-1))
            set_state("Down", self._hmi_can_move_selected(1))
            return

        set_state("Add", self._hmi_root is not None and kind in {"menu", "ref_menu", "item"})
        set_state("Insert", self._hmi_root is not None and kind in {"menu", "ref_menu", "item"})
        set_state("Edit", self._hmi_root is not None and kind in {"menu", "ref_menu", "item"})
        scope = (getattr(self, "_hmi_scope", "ied") or "ied").strip().lower()
        set_state("Select parent menu", scope == "iet" and kind in {"menu", "ref_menu"})
        set_state("Copy", kind in {"menu", "ref_menu", "item"})
        set_state("Cut", kind in {"menu", "ref_menu", "item"})
        can_paste = False
        if self._hmi_root is not None and self._hmi_clipboard is not None:
            try:
                ck, _el = self._hmi_clipboard
            except Exception:
                ck = ""
            can_paste = (ck == kind) or (ck == "attr" and kind in {"menu", "ref_menu"})
        set_state("Paste", can_paste)
        set_state("Delete", kind in {"menu", "ref_menu", "item"})

        if kind in {"ref_menu", "item"}:
            set_state("Up", self._hmi_can_move_selected(-1))
            set_state("Down", self._hmi_can_move_selected(1))
        else:
            set_state("Up", False)
            set_state("Down", False)

    def _hmi_open_iid_path(self, iid: str, *, open_self: bool) -> None:
        tv = self._hmi_tv_menus
        if tv is None:
            return

        # Open ancestors first.
        cur = iid
        parents: list[str] = []
        while True:
            try:
                p = tv.parent(cur)
            except Exception:
                p = ""
            if not p:
                break
            parents.append(p)
            cur = p

        for p in reversed(parents):
            try:
                tv.item(p, open=True)
            except Exception:
                pass

        if open_self:
            try:
                tv.item(iid, open=True)
            except Exception:
                pass

    def _hmi_fold_all(self) -> None:
        tv = self._hmi_tv_menus
        if tv is None:
            return

        def walk(iid0: str) -> None:
            try:
                tv.item(iid0, open=False)
            except Exception:
                pass
            try:
                kids = tv.get_children(iid0)
            except Exception:
                kids = ()
            for k in kids:
                walk(k)

        try:
            roots = tv.get_children("")
        except Exception:
            roots = ()
        for r in roots:
            walk(r)

        try:
            self._hmi_update_fold_all_button()
        except Exception:
            pass

    def _hmi_unfold_all(self) -> None:
        tv = self._hmi_tv_menus
        if tv is None:
            return

        def walk(iid0: str) -> None:
            try:
                tv.item(iid0, open=True)
            except Exception:
                pass
            try:
                kids = tv.get_children(iid0)
            except Exception:
                kids = ()
            for k in kids:
                walk(k)

        try:
            roots = tv.get_children("")
        except Exception:
            roots = ()
        for r in roots:
            walk(r)

        try:
            self._hmi_update_fold_all_button()
        except Exception:
            pass

    def _hmi_update_fold_all_button(self) -> None:
        btn = self._hmi_btn_fold_all
        tv = self._hmi_tv_menus
        if btn is None or tv is None:
            return

        # Find all nodes that can be folded (i.e., have children).
        foldable: list[str] = []

        def walk(iid0: str) -> None:
            try:
                kids = tv.get_children(iid0)
            except Exception:
                kids = ()
            if kids:
                foldable.append(iid0)
                for k in kids:
                    walk(k)

        try:
            roots = tv.get_children("")
        except Exception:
            roots = ()
        for r in roots:
            walk(r)

        if not foldable:
            try:
                btn.configure(text="Fold all", state="disabled")
            except Exception:
                pass
            return

        all_collapsed = True
        for iid in foldable:
            try:
                if bool(tv.item(iid, "open")):
                    all_collapsed = False
                    break
            except Exception:
                pass

        try:
            btn.configure(text=("Unfold all" if all_collapsed else "Fold all"), state="normal")
        except Exception:
            pass

    def _hmi_toggle_fold_all(self) -> None:
        tv = self._hmi_tv_menus
        if tv is None:
            return

        # Decide based on current open state.
        foldable: list[str] = []

        def walk(iid0: str) -> None:
            try:
                kids = tv.get_children(iid0)
            except Exception:
                kids = ()
            if kids:
                foldable.append(iid0)
                for k in kids:
                    walk(k)

        try:
            roots = tv.get_children("")
        except Exception:
            roots = ()
        for r in roots:
            walk(r)

        if not foldable:
            self._hmi_update_fold_all_button()
            return

        all_collapsed = True
        for iid in foldable:
            try:
                if bool(tv.item(iid, "open")):
                    all_collapsed = False
                    break
            except Exception:
                pass

        if all_collapsed:
            self._hmi_unfold_all()
        else:
            self._hmi_fold_all()

    def _hmi_rename_menu_and_refs(self, menu_el: ET.Element, new_name: str) -> None:
        """Rename a HMIMenu and update all HMIMenuItem/@ref that point to it."""
        if self._hmi_root is None:
            return
        old = (menu_el.attrib.get("name") or "").strip()
        new0 = (new_name or "").strip()
        if not new0 or (old == new0):
            if new0:
                menu_el.attrib["name"] = new0
            return

        menu_el.attrib["name"] = new0
        self._hmi_ui_tag_set(menu_el, "changed")

        for m in (self._hmi_all_menus() or []):
            for ch in list(m):
                if not (isinstance(ch.tag, str) and _local_name(ch.tag) == "HMIMenuItem"):
                    continue
                if (ch.attrib.get("ref") or "").strip() == old:
                    ch.attrib["ref"] = new0
                    self._hmi_ui_tag_set(ch, "changed")

    def _hmi_action_edit(self) -> None:
        """Edit selected node via dialog (menu/ref_menu/item/attr)."""
        if self._hmi_root is None:
            return
        tv = self._hmi_tv_menus
        if tv is None:
            return

        iid = self._hmi_selected_tree_iid()
        kind = self._hmi_selected_tree_kind()
        if not iid or kind not in {"menu", "ref_menu", "item", "attr"}:
            return

        node = self._hmi_tree_iid_to_node.get(iid)
        if node is None:
            return
        node_type, parent_el, el = node
        if el is None:
            return

        # Menus and ref_menus both point to an HMIMenu element.
        if kind in {"menu", "ref_menu"} and node_type == "menu":
            dlg = _HmiMenuEditDialog(
                self,
                title="Edit HMIMenu",
                name=el.attrib.get("name") or "",
                desc=el.attrib.get("desc") or "",
                lang_ref=el.attrib.get("langRef") or "",
                data_type=el.attrib.get("hmiMenuDataType") or "",
                view_type=el.attrib.get("hmiMenuViewType") or "",
                sub_tree_type=el.attrib.get("hmiSubTreeType") or "",
                data_type_values=[""] + list(getattr(self, "_hmi_menu_data_type_values", []) or []),
                view_type_values=[""] + list(getattr(self, "_hmi_menu_view_type_values", []) or []),
            )
            res = dlg.show()
            if not res:
                return

            self._hmi_push_undo()

            # Rename safely (update ref links).
            try:
                self._hmi_rename_menu_and_refs(el, (res.get("name") or "").strip())
            except Exception:
                pass

            for k in ("desc", "langRef", "hmiMenuDataType", "hmiMenuViewType", "hmiSubTreeType"):
                vv = (res.get(k) or "") if k == "desc" else (res.get(k) or "").strip()
                if vv:
                    el.attrib[k] = vv
                else:
                    el.attrib.pop(k, None)

            self._hmi_ui_tag_set(el, "changed")

            self._refresh_hmi_views(select_first_menu=False)
            new_iid = self._hmi_find_iid_for_element(el)
            if new_iid:
                try:
                    tv.selection_set(new_iid)
                except Exception:
                    pass
                self._hmi_open_iid_path(new_iid, open_self=True)
            self._mark_hmi_unsaved()
            return

        # DO item edit.
        if kind == "item" and node_type == "item":
            old_do_ref = (el.attrib.get("doRef") or "").strip()
            dlg = _HmiMenuItemEditDialog(
                self,
                title="Edit HMIMenuItem",
                name=el.attrib.get("name") or "",
                ref=el.attrib.get("ref") or "",
                do_ref=el.attrib.get("doRef") or "",
                da_ref=el.attrib.get("daRef") or "",
            )
            res = dlg.show()
            if not res:
                return
            self._hmi_push_undo()
            ref = (res.get("ref") or "").strip()
            for k in ("ref", "name", "doRef", "daRef"):
                el.attrib.pop(k, None)
            if ref:
                el.attrib["ref"] = ref
            else:
                nm = (res.get("name") or "").strip()
                if nm:
                    el.attrib["name"] = nm
                do_ref = (res.get("doRef") or "").strip()
                da_ref = (res.get("daRef") or "").strip()
                if do_ref:
                    el.attrib["doRef"] = do_ref
                if da_ref:
                    el.attrib["daRef"] = da_ref

                # Keep child data items consistent with the DO.
                if (do_ref or "") != (old_do_ref or ""):
                    try:
                        for ch in list(el):
                            if not (isinstance(ch.tag, str) and _local_name(ch.tag) == "HMIDataItem"):
                                continue
                            if do_ref:
                                ch.attrib["doRef"] = do_ref
                            else:
                                ch.attrib.pop("doRef", None)
                            self._hmi_ui_tag_set(ch, "changed")
                    except Exception:
                        pass

            self._hmi_ui_tag_set(el, "changed")

            self._refresh_hmi_views(select_first_menu=False)
            new_iid = self._hmi_find_iid_for_element(el)
            if new_iid:
                try:
                    tv.selection_set(new_iid)
                except Exception:
                    pass
                self._hmi_open_iid_path(new_iid, open_self=True)
            self._mark_hmi_unsaved()
            return

        # HMIAttr edit.
        if kind == "attr" and node_type == "attr":
            cur_name = (el.attrib.get("name") or "").strip()
            cur_val = el.attrib.get("value") or el.attrib.get("val") or ""
            val_values = None
            if cur_name == "IET_HARDLINK_DEFINITION":
                try:
                    val_values = self._hmi_inref_dropdown_values(current=cur_val)
                except Exception:
                    val_values = None
            dlg = _HmiAttrEditDialog(self, title="Edit HMIAttr", name=cur_name, value=cur_val, value_values=val_values)
            res = dlg.show()
            if not res:
                return

            self._hmi_push_undo()

            new_name = (res.get("name") or "").strip()
            new_val = res.get("value") or ""

            if new_name:
                el.attrib["name"] = new_name
            else:
                el.attrib.pop("name", None)

            key = "val" if ("val" in el.attrib and "value" not in el.attrib) else "value"
            if (new_val or "").strip():
                el.attrib[key] = new_val
            else:
                el.attrib.pop("value", None)
                el.attrib.pop("val", None)

            self._hmi_ui_tag_set(el, "changed")
            self._refresh_hmi_views(select_first_menu=False)
            new_iid = self._hmi_find_iid_for_element(el)
            if new_iid:
                try:
                    tv.selection_set(new_iid)
                except Exception:
                    pass
                try:
                    self._hmi_open_iid_path(new_iid, open_self=True)
                except Exception:
                    pass
            self._mark_hmi_unsaved()
            return

    def _hmi_on_tree_right_click(self, event: tk.Event) -> None:
        tv = self._hmi_tv_menus
        if tv is None:
            return

        try:
            iid = tv.identify_row(int(event.y))
        except Exception:
            iid = ""
        if not iid:
            return

        # Select row under cursor.
        try:
            tv.selection_set(iid)
        except Exception:
            pass

        kind = (self._hmi_tree_iid_to_kind.get(iid) or "").strip()

        if self._hmi_tree_ctx_menu is None:
            self._hmi_tree_ctx_menu = self._hmi_build_tree_context_menu()

        self._hmi_configure_tree_context_menu(kind)
        try:
            self._hmi_tree_ctx_menu.tk_popup(int(event.x_root), int(event.y_root))
        finally:
            try:
                self._hmi_tree_ctx_menu.grab_release()
            except Exception:
                pass

    def _hmi_action_select_parent_menu(self) -> None:
        if self._hmi_root is None:
            return
        scope = (getattr(self, "_hmi_scope", "ied") or "ied").strip().lower()
        if scope != "iet":
            return

        iid = self._hmi_selected_tree_iid()
        kind = self._hmi_selected_tree_kind()
        if not iid or kind not in {"menu", "ref_menu"}:
            return

        node = self._hmi_tree_iid_to_node.get(iid)
        if node is None:
            return
        node_type, _parent_el, menu_el = node
        if node_type != "menu" or menu_el is None:
            return

        menu_name = (menu_el.attrib.get("name") or "").strip()
        if not menu_name:
            return

        menus = list(self._hmi_all_menus() or [])
        by_name: dict[str, ET.Element] = {}
        for m in menus:
            nm = (m.attrib.get("name") or "").strip()
            if nm:
                by_name[nm] = m

        links_to_menu: list[tuple[ET.Element, ET.Element]] = []
        for pm in menus:
            for ch in list(pm):
                if not (isinstance(ch.tag, str) and _local_name(ch.tag) == "HMIMenuItem"):
                    continue
                if (ch.attrib.get("ref") or "").strip() == menu_name:
                    links_to_menu.append((pm, ch))

        cur_parent = ""
        if kind == "ref_menu":
            link0 = self._hmi_tree_iid_to_ref_link.get(iid)
            if link0 is not None:
                try:
                    pm0, _ref0 = link0
                    cur_parent = (pm0.attrib.get("name") or "").strip()
                except Exception:
                    cur_parent = ""
        elif links_to_menu:
            try:
                cur_parent = (links_to_menu[0][0].attrib.get("name") or "").strip()
            except Exception:
                cur_parent = ""

        parent_names = sorted([n for n in by_name.keys() if n != menu_name], key=str.lower)
        dlg = _SelectParentMenuDialog(
            self,
            menu_name=menu_name,
            parent_names=parent_names,
            current_parent=cur_parent,
        )
        res = dlg.show()
        if not res:
            return
        target_parent_name = (res.get("parent") or "").strip()

        if target_parent_name:
            target_menu = by_name.get(target_parent_name)
            if target_menu is None:
                messagebox.showerror("Invalid", "Selected parent menu not found.", parent=self)
                return

            descendant_names: set[str] = set()
            visited: set[str] = set()

            def walk_desc(m0: ET.Element) -> None:
                nm0 = (m0.attrib.get("name") or "").strip()
                if not nm0 or nm0 in visited:
                    return
                visited.add(nm0)
                for ch in list(m0):
                    if not (isinstance(ch.tag, str) and _local_name(ch.tag) == "HMIMenuItem"):
                        continue
                    rn = (ch.attrib.get("ref") or "").strip()
                    if not rn:
                        continue
                    if rn not in descendant_names:
                        descendant_names.add(rn)
                    rm = by_name.get(rn)
                    if rm is not None:
                        walk_desc(rm)

            walk_desc(menu_el)
            if target_parent_name in descendant_names:
                messagebox.showerror(
                    "Invalid",
                    "Selected parent would create a cyclic menu reference.",
                    parent=self,
                )
                return

        self._hmi_push_undo()

        moved_link_el: ET.Element | None = None
        for pm, link in links_to_menu:
            if moved_link_el is None:
                moved_link_el = link
            try:
                pm.remove(link)
                self._hmi_ui_tag_set(pm, "changed")
            except Exception:
                pass

        if target_parent_name:
            target_menu = by_name.get(target_parent_name)
            if target_menu is not None:
                if moved_link_el is None:
                    moved_link_el = ET.Element(_q(HMI_CUST_NS, "HMIMenuItem"))
                    moved_link_el.attrib["ref"] = menu_name
                    self._hmi_ui_tag_set(moved_link_el, "added")
                else:
                    moved_link_el.attrib["ref"] = menu_name
                try:
                    target_menu.append(moved_link_el)
                    self._hmi_ui_tag_set(target_menu, "changed")
                except Exception:
                    pass

        self._refresh_hmi_views(select_first_menu=False)
        try:
            new_iid = self._hmi_find_iid_for_element(menu_el)
            if new_iid:
                if self._hmi_tv_menus is not None:
                    self._hmi_tv_menus.selection_set(new_iid)
                self._hmi_open_iid_path(new_iid, open_self=True)
        except Exception:
            pass

        self._mark_hmi_unsaved()

    def _hmi_unique_name(self, existing: set[str], base: str) -> str:
        base0 = (base or "").strip() or "New"
        if base0 not in existing:
            return base0
        i = 2
        while True:
            cand = f"{base0}{i}"
            if cand not in existing:
                return cand
            i += 1

    def _hmi_existing_menu_names(self) -> set[str]:
        out: set[str] = set()
        for m in (self._hmi_all_menus() or []):
            try:
                nm = (m.attrib.get("name") or "").strip()
            except Exception:
                nm = ""
            if nm:
                out.add(nm)
        return out

    def _hmi_existing_item_names(self, menu_el: ET.Element) -> set[str]:
        out: set[str] = set()
        for ch in list(menu_el):
            if not (isinstance(ch.tag, str) and _local_name(ch.tag) == "HMIMenuItem"):
                continue
            if (ch.attrib.get("ref") or "").strip():
                continue
            nm = (ch.attrib.get("name") or "").strip()
            if nm:
                out.add(nm)
        return out

    def _hmi_existing_data_names(self, item_el: ET.Element) -> set[str]:
        out: set[str] = set()
        for ch in list(item_el):
            if not (isinstance(ch.tag, str) and _local_name(ch.tag) == "HMIDataItem"):
                continue
            nm = (ch.attrib.get("name") or "").strip()
            if nm:
                out.add(nm)
        return out

    def _hmi_find_iid_for_element(self, el: ET.Element) -> str | None:
        for iid, node in (self._hmi_tree_iid_to_node or {}).items():
            try:
                _k, _p, e2 = node
            except Exception:
                continue
            if e2 is el:
                return iid
        return None

    def _hmi_action_add(self) -> None:
        self._hmi_add_child(insert=False)

    def _hmi_action_insert(self) -> None:
        self._hmi_add_child(insert=True)

    def _hmi_add_child(self, *, insert: bool) -> None:
        """Add a child node under the current selection.

        - Select a menu -> add either a submenu (ref_menu) or a DO item (depending on menu type)
        - Select a DO item -> add a DA (data) row
        - DA rows: no-op
        """

        if self._hmi_root is None:
            return

        tv = self._hmi_tv_menus
        if tv is None:
            return

        iid = self._hmi_selected_tree_iid()
        kind = self._hmi_selected_tree_kind()

        # If nothing selected, Add creates a top-level menu.
        if not iid or not kind:
            self._hmi_push_undo()
            existing = self._hmi_existing_menu_names()
            nm = self._hmi_unique_name(existing, "Menu_")
            menu = ET.SubElement(self._hmi_root, _q(HMI_CUST_NS, "HMIMenu"))
            menu.attrib["name"] = nm
            self._hmi_ui_tag_set(menu, "added")
            self._refresh_hmi_views(select_first_menu=False)
            new_iid = self._hmi_find_iid_for_element(menu)
            if new_iid:
                try:
                    tv.selection_set(new_iid)
                except Exception:
                    pass
                try:
                    self._hmi_open_iid_path(new_iid, open_self=True)
                except Exception:
                    pass
            self._mark_hmi_unsaved()
            return

        if kind in {"data", "missing_ref"}:
            return

        node = self._hmi_tree_iid_to_node.get(iid)
        if node is None:
            return

        node_type, parent_el, el = node
        if el is None:
            return

        new_el: ET.Element | None = None

        # Menu -> add submenu (ref_menu) OR add DO item, depending on menu type.
        if kind in {"menu", "ref_menu"} and node_type == "menu":
            menu_el = el

            def _menu_wants_ref_children(menu0: ET.Element) -> bool:
                dt0 = (menu0.attrib.get("hmiMenuDataType") or "").strip()
                vt0 = (menu0.attrib.get("hmiMenuViewType") or "").strip()
                if dt0 == "HMI_MENU_DATA_TYPE_TAB" or vt0 == "HMI_MENU_VIEW_TYPE_TABS":
                    return True
                # If it already contains ref-links, treat it as a menu-of-menus.
                for ch0 in list(menu0):
                    if not (isinstance(ch0.tag, str) and _local_name(ch0.tag) == "HMIMenuItem"):
                        continue
                    if (ch0.attrib.get("ref") or "").strip():
                        return True
                return False

            self._hmi_push_undo()
            if _menu_wants_ref_children(menu_el):
                parent_menu_el = menu_el
                existing_menus = self._hmi_existing_menu_names()
                parent_name = (parent_menu_el.attrib.get("name") or "").strip()
                parent_dt = (parent_menu_el.attrib.get("hmiMenuDataType") or "").strip()

                # Special case: IET SectionA -> create sequential SettingN.
                is_iet_section_a = (
                    parent_dt == "IET_MENU_DATA_TYPE_SECTION"
                    and parent_name.lower().startswith("iet_protection_")
                    and parent_name.lower().endswith("_sectiona")
                )

                if is_iet_section_a:
                    # Determine next N from existing children refs: <SectionA>_Setting<N>
                    max_n = 0
                    try:
                        rx = re.compile(re.escape(parent_name) + r"_Setting(\d+)$", re.IGNORECASE)
                        for ch0 in list(parent_menu_el):
                            if not (isinstance(ch0.tag, str) and _local_name(ch0.tag) == "HMIMenuItem"):
                                continue
                            ref0 = (ch0.attrib.get("ref") or "").strip()
                            if not ref0:
                                continue
                            m0 = rx.match(ref0)
                            if not m0:
                                continue
                            try:
                                max_n = max(max_n, int(m0.group(1)))
                            except Exception:
                                continue
                    except Exception:
                        max_n = 0
                    next_n = (max_n + 1) if max_n > 0 else 1

                    # Ensure uniqueness even if name already exists.
                    while True:
                        cand = f"{parent_name}_Setting{next_n}"
                        if cand not in existing_menus:
                            new_menu_name = cand
                            break
                        next_n += 1

                    new_menu = ET.Element(_q(HMI_CUST_NS, "HMIMenu"))
                    new_menu.attrib["name"] = new_menu_name
                    new_menu.attrib["hmiMenuDataType"] = "IET_MENU_DATA_TYPE_SETTING_PARAMETERS"
                    new_menu.attrib["hmiMenuViewType"] = "IET_MENU_VIEW_TYPE_SETTING_SECTION"

                    # Auto-fill attrs like Setting1, with order=N.
                    for nm1 in ("order", "langRef", "label", "readonly", "IET_HARDLINK_DEFINITION"):
                        a = ET.SubElement(new_menu, _q(HMI_CUST_NS, "HMIAttr"))
                        a.attrib["name"] = nm1
                        if nm1 == "order":
                            a.attrib["value"] = str(next_n)
                        elif nm1 == "langRef":
                            a.attrib["value"] = "0.0"
                        else:
                            a.attrib["value"] = ""
                        # Make new HMIAttr visually stand out as newly added.
                        try:
                            self._hmi_ui_tag_set(a, "added")
                        except Exception:
                            pass

                    self._hmi_ui_tag_set(new_menu, "added")
                    try:
                        self._hmi_root.append(new_menu)
                    except Exception:
                        return

                    link = ET.Element(_q(HMI_CUST_NS, "HMIMenuItem"))
                    link.attrib["ref"] = new_menu_name
                    self._hmi_ui_tag_set(link, "added")
                    try:
                        # Always append so parent order matches numbering.
                        parent_menu_el.append(link)
                    except Exception:
                        return
                    new_el = new_menu
                else:
                    new_menu_name = self._hmi_unique_name(existing_menus, f"{(parent_name or 'Menu_')}_")
                    new_menu = ET.Element(_q(HMI_CUST_NS, "HMIMenu"))
                    new_menu.attrib["name"] = new_menu_name
                    self._hmi_ui_tag_set(new_menu, "added")
                    try:
                        self._hmi_root.append(new_menu)
                    except Exception:
                        return

                    link = ET.Element(_q(HMI_CUST_NS, "HMIMenuItem"))
                    link.attrib["ref"] = new_menu_name
                    self._hmi_ui_tag_set(link, "added")
                    try:
                        if insert:
                            parent_menu_el.insert(0, link)
                        else:
                            parent_menu_el.append(link)
                    except Exception:
                        return
                    new_el = new_menu
            else:
                existing_items = self._hmi_existing_item_names(menu_el)
                nm = self._hmi_unique_name(existing_items, "Item_")
                it = ET.Element(_q(HMI_CUST_NS, "HMIMenuItem"))
                it.attrib["name"] = nm
                self._hmi_ui_tag_set(it, "added")
                try:
                    if insert:
                        menu_el.insert(0, it)
                    else:
                        menu_el.append(it)
                except Exception:
                    return
                new_el = it

        # DO item -> add DA data row
        elif kind == "item" and node_type == "item":
            self._hmi_push_undo()
            item_el = el
            existing_data = self._hmi_existing_data_names(item_el)
            nm = self._hmi_unique_name(existing_data, "DA_")
            di = ET.Element(_q(HMI_CUST_NS, "HMIDataItem"))
            di.attrib["name"] = nm
            self._hmi_ui_tag_set(di, "added")
            try:
                do_ref = (item_el.attrib.get("doRef") or "").strip()
            except Exception:
                do_ref = ""
            if do_ref:
                di.attrib["doRef"] = do_ref
            try:
                if insert:
                    item_el.insert(0, di)
                else:
                    item_el.append(di)
            except Exception:
                return
            new_el = di

        # HMIAttr -> add another attr as sibling under the parent menu
        elif kind == "attr" and node_type == "attr":
            if parent_el is None:
                return
            self._hmi_push_undo()
            menu_el = parent_el
            try:
                existing = {
                    (ch.attrib.get("name") or "").strip()
                    for ch in list(menu_el)
                    if isinstance(ch.tag, str)
                    and _local_name(ch.tag) == "HMIAttr"
                    and (ch.attrib.get("name") or "").strip()
                }
            except Exception:
                existing = set()
            nm = self._hmi_unique_name(existing, "Attr_")
            at = ET.Element(_q(HMI_CUST_NS, "HMIAttr"))
            at.attrib["name"] = nm
            self._hmi_ui_tag_set(at, "added")

            try:
                kids = list(menu_el)
                cur_idx = kids.index(el)
            except Exception:
                cur_idx = -1
            try:
                if cur_idx >= 0:
                    if insert:
                        menu_el.insert(cur_idx, at)
                    else:
                        menu_el.insert(cur_idx + 1, at)
                else:
                    menu_el.append(at)
            except Exception:
                return
            new_el = at

        else:
            return

        self._refresh_hmi_views(select_first_menu=False)
        # Do NOT auto-select the newly-added node.
        # Keep the previous selection so the user can repeatedly Add siblings.
        self._mark_hmi_unsaved()

    def _hmi_action_copy(self) -> None:
        iid = self._hmi_selected_tree_iid()
        if not iid:
            return
        kind = self._hmi_selected_tree_kind()
        if kind not in {"menu", "ref_menu", "item", "data", "attr"}:
            return
        node = self._hmi_tree_iid_to_node.get(iid)
        if node is None:
            return
        _t, _p, el = node
        if el is None:
            return
        try:
            self._hmi_clipboard = (kind, _deepcopy_et_element(el))
        except Exception:
            self._hmi_clipboard = None
        self._hmi_update_hmi_action_state()

    def _hmi_action_cut(self) -> None:
        # Implement cut as copy + delete (no confirmation).
        iid = self._hmi_selected_tree_iid()
        if not iid:
            return
        kind = self._hmi_selected_tree_kind()
        if kind not in {"menu", "ref_menu", "item", "attr"}:
            return
        self._hmi_action_copy()
        self._hmi_delete_selected()

    def _hmi_action_paste(self) -> None:
        if self._hmi_root is None:
            return
        if self._hmi_clipboard is None:
            return

        tv = self._hmi_tv_menus
        if tv is None:
            return

        iid = self._hmi_selected_tree_iid()
        kind = self._hmi_selected_tree_kind()
        if not iid or not kind:
            # No selection: only allow pasting a top menu.
            try:
                ck, cel = self._hmi_clipboard
            except Exception:
                return
            if ck != "menu":
                return
            self._hmi_push_undo()
            el_copy = _deepcopy_et_element(cel)
            existing = self._hmi_existing_menu_names()
            new_name = self._hmi_unique_name(existing, (el_copy.attrib.get("name") or "Menu_"))
            el_copy.attrib["name"] = new_name
            self._hmi_mark_added_recursive(el_copy)
            self._hmi_root.append(el_copy)
            self._refresh_hmi_views(select_first_menu=False)
            new_iid = self._hmi_find_iid_for_element(el_copy)
            if new_iid:
                try:
                    tv.selection_set(new_iid)
                except Exception:
                    pass
                try:
                    self._hmi_open_iid_path(new_iid, open_self=True)
                except Exception:
                    pass
            self._mark_hmi_unsaved()
            return

        if kind in {"data", "missing_ref"}:
            return

        try:
            ck, cel = self._hmi_clipboard
        except Exception:
            return
        if ck != kind:
            # Allow pasting HMIAttr into a menu/ref_menu selection.
            if not (ck == "attr" and kind in {"menu", "ref_menu"}):
                return

        # Paste as sibling AFTER current selection.
        node = self._hmi_tree_iid_to_node.get(iid)
        if node is None:
            return
        node_type, parent_el, el = node
        if el is None:
            return

        pasted_el: ET.Element | None = None

        if ck == "attr" and kind in {"menu", "ref_menu"} and node_type == "menu":
            # Paste as a child under the selected menu.
            menu_el = el
            self._hmi_push_undo()
            at_copy = _deepcopy_et_element(cel)
            try:
                existing = {
                    (ch.attrib.get("name") or "").strip()
                    for ch in list(menu_el)
                    if isinstance(ch.tag, str)
                    and _local_name(ch.tag) == "HMIAttr"
                    and (ch.attrib.get("name") or "").strip()
                }
            except Exception:
                existing = set()
            new_nm = self._hmi_unique_name(existing, (at_copy.attrib.get("name") or "Attr_"))
            at_copy.attrib["name"] = new_nm
            self._hmi_mark_added_recursive(at_copy)
            try:
                menu_el.append(at_copy)
            except Exception:
                return
            pasted_el = at_copy

        elif kind == "attr" and node_type == "attr":
            if parent_el is None:
                return
            menu_el = parent_el
            self._hmi_push_undo()
            at_copy = _deepcopy_et_element(cel)
            try:
                existing = {
                    (ch.attrib.get("name") or "").strip()
                    for ch in list(menu_el)
                    if isinstance(ch.tag, str)
                    and _local_name(ch.tag) == "HMIAttr"
                    and (ch.attrib.get("name") or "").strip()
                }
            except Exception:
                existing = set()
            new_nm = self._hmi_unique_name(existing, (at_copy.attrib.get("name") or "Attr_"))
            at_copy.attrib["name"] = new_nm
            self._hmi_mark_added_recursive(at_copy)
            try:
                kids = list(menu_el)
                full_idx = kids.index(el)
            except Exception:
                full_idx = -1
            try:
                if full_idx >= 0:
                    menu_el.insert(full_idx + 1, at_copy)
                else:
                    menu_el.append(at_copy)
            except Exception:
                return
            pasted_el = at_copy

        elif kind == "menu" and node_type == "menu":
            self._hmi_push_undo()
            el_copy = _deepcopy_et_element(cel)
            existing = self._hmi_existing_menu_names()
            new_name = self._hmi_unique_name(existing, (el_copy.attrib.get("name") or "Menu_"))
            el_copy.attrib["name"] = new_name
            self._hmi_mark_added_recursive(el_copy)
            self._hmi_root.append(el_copy)
            pasted_el = el_copy

        elif kind == "ref_menu" and node_type == "menu":
            # Need to clone submenu menu definition + add a new ref-link in the parent menu.
            link = self._hmi_tree_iid_to_ref_link.get(iid)
            if link is None:
                return
            parent_menu_el, ref_link_el = link

            self._hmi_push_undo()
            menu_copy = _deepcopy_et_element(cel)
            existing = self._hmi_existing_menu_names()
            new_menu_name = self._hmi_unique_name(existing, (menu_copy.attrib.get("name") or "Menu_"))
            menu_copy.attrib["name"] = new_menu_name
            self._hmi_mark_added_recursive(menu_copy)
            self._hmi_root.append(menu_copy)

            new_ref = ET.Element(_q(HMI_CUST_NS, "HMIMenuItem"))
            new_ref.attrib["ref"] = new_menu_name
            self._hmi_ui_tag_set(new_ref, "added")
            try:
                kids = list(parent_menu_el)
                idx = kids.index(ref_link_el)
            except Exception:
                idx = -1
            try:
                if idx >= 0:
                    parent_menu_el.insert(idx + 1, new_ref)
                else:
                    parent_menu_el.append(new_ref)
            except Exception:
                return
            pasted_el = menu_copy

        elif kind == "item" and node_type == "item":
            if parent_el is None:
                return
            menu_el = parent_el
            self._hmi_push_undo()
            it_copy = _deepcopy_et_element(cel)
            # Ensure unique item name.
            existing = self._hmi_existing_item_names(menu_el)
            new_nm = self._hmi_unique_name(existing, (it_copy.attrib.get("name") or "Item_"))
            it_copy.attrib["name"] = new_nm
            self._hmi_mark_added_recursive(it_copy)
            try:
                kids = [k for k in list(menu_el) if isinstance(k.tag, str) and _local_name(k.tag) == "HMIMenuItem"]
                idx = kids.index(el)
            except Exception:
                idx = -1
            try:
                if idx >= 0:
                    # Insert relative to full child list index.
                    full_kids = list(menu_el)
                    try:
                        full_idx = full_kids.index(el)
                    except Exception:
                        full_idx = len(full_kids) - 1
                    menu_el.insert(full_idx + 1, it_copy)
                else:
                    menu_el.append(it_copy)
            except Exception:
                return
            pasted_el = it_copy

        else:
            return

        self._refresh_hmi_views(select_first_menu=False)
        if pasted_el is not None:
            new_iid = self._hmi_find_iid_for_element(pasted_el)
            if new_iid:
                try:
                    tv.selection_set(new_iid)
                except Exception:
                    pass
                try:
                    self._hmi_open_iid_path(new_iid, open_self=True)
                except Exception:
                    pass
        self._mark_hmi_unsaved()

    def _hmi_action_delete(self) -> None:
        self._hmi_delete_selected()

    def _hmi_action_move_up(self) -> None:
        self._hmi_move_selected(-1)

    def _hmi_action_move_down(self) -> None:
        self._hmi_move_selected(1)

    def _hmi_find_iid_for_ref_link(self, parent_menu_el: ET.Element, ref_link_el: ET.Element) -> str | None:
        for iid, link in (self._hmi_tree_iid_to_ref_link or {}).items():
            try:
                p, el = link
            except Exception:
                continue
            if p is parent_menu_el and el is ref_link_el:
                return iid
        return None

    def _hmi_can_move_selected(self, delta: int) -> bool:
        if self._hmi_root is None:
            return False
        iid = self._hmi_selected_tree_iid()
        kind = self._hmi_selected_tree_kind()
        if not iid:
            return False
        if kind not in {"ref_menu", "item", "data", "attr"}:
            return False

        node = self._hmi_tree_iid_to_node.get(iid)
        if node is None:
            return False
        node_type, parent_el, el = node
        if el is None:
            return False

        move_parent: ET.Element | None = None
        move_el: ET.Element | None = None

        if kind == "ref_menu" and node_type == "menu":
            link = self._hmi_tree_iid_to_ref_link.get(iid)
            if link is None:
                return False
            try:
                move_parent, move_el = link
            except Exception:
                return False
            if move_parent is None or move_el is None:
                return False
            eligible = [
                ch
                for ch in list(move_parent)
                if isinstance(ch.tag, str) and _local_name(ch.tag) in {"HMIMenuItem", "HMIAttr"}
            ]
        elif kind == "item" and node_type == "item":
            if parent_el is None:
                return False
            move_parent = parent_el
            move_el = el
            eligible = [
                ch
                for ch in list(move_parent)
                if isinstance(ch.tag, str) and _local_name(ch.tag) in {"HMIMenuItem", "HMIAttr"}
            ]
        elif kind == "attr" and node_type == "attr":
            if parent_el is None:
                return False
            move_parent = parent_el
            move_el = el
            eligible = [
                ch
                for ch in list(move_parent)
                if isinstance(ch.tag, str) and _local_name(ch.tag) in {"HMIMenuItem", "HMIAttr"}
            ]
        elif kind == "data" and node_type == "data":
            if parent_el is None:
                return False
            move_parent = parent_el
            move_el = el
            eligible = [
                ch
                for ch in list(move_parent)
                if isinstance(ch.tag, str) and _local_name(ch.tag) == "HMIDataItem"
            ]
        else:
            return False

        try:
            idx = eligible.index(move_el)
        except Exception:
            return False

        new_idx = idx + int(delta)
        return 0 <= new_idx < len(eligible)

    def _hmi_move_selected(self, delta: int) -> None:
        if self._hmi_root is None:
            return
        tv = self._hmi_tv_menus
        if tv is None:
            return

        # Commit/close any inline editing first.
        try:
            self._hmi_end_cell_edit(commit=True)
            self._hmi_end_combo_edit(commit=True)
        except Exception:
            pass

        iid = self._hmi_selected_tree_iid()
        kind = self._hmi_selected_tree_kind()
        if not iid:
            return
        if kind not in {"ref_menu", "item", "data", "attr"}:
            return
        if not self._hmi_can_move_selected(delta):
            return

        node = self._hmi_tree_iid_to_node.get(iid)
        if node is None:
            return
        node_type, parent_el, el = node
        if el is None:
            return

        move_parent: ET.Element | None = None
        move_el: ET.Element | None = None
        select_iid = None

        if kind == "ref_menu" and node_type == "menu":
            link = self._hmi_tree_iid_to_ref_link.get(iid)
            if link is None:
                return
            try:
                move_parent, move_el = link
            except Exception:
                return
            if move_parent is None or move_el is None:
                return
            eligible = [
                ch
                for ch in list(move_parent)
                if isinstance(ch.tag, str) and _local_name(ch.tag) in {"HMIMenuItem", "HMIAttr"}
            ]
            select_iid = lambda: self._hmi_find_iid_for_ref_link(move_parent, move_el)
        elif kind == "item" and node_type == "item":
            if parent_el is None:
                return
            move_parent = parent_el
            move_el = el
            eligible = [
                ch
                for ch in list(move_parent)
                if isinstance(ch.tag, str) and _local_name(ch.tag) in {"HMIMenuItem", "HMIAttr"}
            ]
            select_iid = lambda: self._hmi_find_iid_for_element(move_el)
        elif kind == "attr" and node_type == "attr":
            if parent_el is None:
                return
            move_parent = parent_el
            move_el = el
            eligible = [
                ch
                for ch in list(move_parent)
                if isinstance(ch.tag, str) and _local_name(ch.tag) in {"HMIMenuItem", "HMIAttr"}
            ]
            select_iid = lambda: self._hmi_find_iid_for_element(move_el)
        elif kind == "data" and node_type == "data":
            if parent_el is None:
                return
            move_parent = parent_el
            move_el = el
            eligible = [
                ch
                for ch in list(move_parent)
                if isinstance(ch.tag, str) and _local_name(ch.tag) == "HMIDataItem"
            ]
            select_iid = lambda: self._hmi_find_iid_for_element(move_el)
        else:
            return

        try:
            cur_idx = eligible.index(move_el)
        except Exception:
            return
        new_idx = cur_idx + int(delta)
        if not (0 <= new_idx < len(eligible)):
            return
        target = eligible[new_idx]

        self._hmi_push_undo()

        try:
            move_parent.remove(move_el)
        except Exception:
            return

        try:
            kids_now = list(move_parent)
            tgt_idx = kids_now.index(target)
        except Exception:
            # Fallback: append.
            try:
                move_parent.append(move_el)
            except Exception:
                return
        else:
            try:
                if delta < 0:
                    move_parent.insert(tgt_idx, move_el)
                else:
                    move_parent.insert(tgt_idx + 1, move_el)
            except Exception:
                return

        # Mark as changed so diff view highlights the moved row.
        try:
            self._hmi_ui_tag_set(move_el, "changed")
            try:
                self._hmi_ui_moved_el_ids.add(id(move_el))
            except Exception:
                pass
        except Exception:
            pass

        self._refresh_hmi_views(select_first_menu=False, open_selection_path=False)

        try:
            if select_iid is not None:
                new_iid = select_iid()
            else:
                new_iid = None
            if new_iid:
                tv.selection_set(new_iid)
        except Exception:
            pass

        self._mark_hmi_unsaved()

    def _hmi_delete_selected(self) -> None:
        if self._hmi_root is None:
            return
        tv = self._hmi_tv_menus
        if tv is None:
            return

        # Commit/close any inline editing first.
        try:
            self._hmi_end_cell_edit(commit=True)
            self._hmi_end_combo_edit(commit=True)
        except Exception:
            pass

        iid = self._hmi_selected_tree_iid()
        if not iid:
            return
        kind = self._hmi_selected_tree_kind()
        if kind not in {"menu", "ref_menu", "item", "data", "attr"}:
            return

        node = self._hmi_tree_iid_to_node.get(iid)
        if node is None:
            return
        node_type, parent_el, el = node
        if el is None:
            return

        self._hmi_push_undo()

        # menu: stage-delete HMIMenu definition; also stage/delete ref links pointing at it.
        if kind == "menu" and node_type == "menu":
            try:
                menu_name = (el.attrib.get("name") or "").strip()
            except Exception:
                menu_name = ""
            if self._hmi_ui_is_added(el):
                try:
                    self._hmi_root.remove(el)
                except Exception:
                    return
                if menu_name:
                    for m in (self._hmi_all_menus() or []):
                        for ch in list(m):
                            if not (isinstance(ch.tag, str) and _local_name(ch.tag) == "HMIMenuItem"):
                                continue
                            if (ch.attrib.get("ref") or "").strip() == menu_name:
                                try:
                                    m.remove(ch)
                                except Exception:
                                    pass
            else:
                self._hmi_ui_tag_set(el, "removed")
                if menu_name:
                    for m in (self._hmi_all_menus() or []):
                        for ch in list(m):
                            if not (isinstance(ch.tag, str) and _local_name(ch.tag) == "HMIMenuItem"):
                                continue
                            if (ch.attrib.get("ref") or "").strip() == menu_name:
                                if self._hmi_ui_is_added(ch):
                                    try:
                                        m.remove(ch)
                                    except Exception:
                                        pass
                                else:
                                    self._hmi_ui_tag_set(ch, "removed")

        # ref_menu: stage-delete the ref link; if this makes the submenu unreferenced (in persisted form),
        # stage-delete the submenu definition too.
        elif kind == "ref_menu" and node_type == "menu":
            link = self._hmi_tree_iid_to_ref_link.get(iid)
            if link is None:
                return
            parent_menu_el, ref_link_el = link
            try:
                menu_name = (el.attrib.get("name") or "").strip()
            except Exception:
                menu_name = ""
            if self._hmi_ui_is_added(ref_link_el):
                try:
                    parent_menu_el.remove(ref_link_el)
                except Exception:
                    return
            else:
                self._hmi_ui_tag_set(ref_link_el, "removed")

            if menu_name:
                still_ref = False
                for m in (self._hmi_all_menus() or []):
                    for ch in list(m):
                        if not (isinstance(ch.tag, str) and _local_name(ch.tag) == "HMIMenuItem"):
                            continue
                        if (ch.attrib.get("ref") or "").strip() != menu_name:
                            continue
                        if self._hmi_ui_is_removed(ch):
                            continue
                        still_ref = True
                        break
                    if still_ref:
                        break

                if not still_ref:
                    if self._hmi_ui_is_added(el):
                        try:
                            self._hmi_root.remove(el)
                        except Exception:
                            pass
                    else:
                        self._hmi_ui_tag_set(el, "removed")

        # item: stage-delete HMIMenuItem from its parent menu.
        elif kind == "item" and node_type == "item":
            if parent_el is None:
                return
            if self._hmi_ui_is_added(el):
                try:
                    parent_el.remove(el)
                except Exception:
                    return
            else:
                self._hmi_ui_tag_set(el, "removed")

        # data: stage-delete HMIDataItem from its parent HMIMenuItem.
        elif kind == "data" and node_type == "data":
            if parent_el is None:
                return
            if self._hmi_ui_is_added(el):
                try:
                    parent_el.remove(el)
                except Exception:
                    return
            else:
                self._hmi_ui_tag_set(el, "removed")

        # attr: stage-delete HMIAttr from its parent menu.
        elif kind == "attr" and node_type == "attr":
            if parent_el is None:
                return
            if self._hmi_ui_is_added(el):
                try:
                    parent_el.remove(el)
                except Exception:
                    return
            else:
                self._hmi_ui_tag_set(el, "removed")

        else:
            return

        # Refresh without forcing open-path changes (do not affect current display).
        self._refresh_hmi_views(select_first_menu=False, open_selection_path=False)
        self._mark_hmi_unsaved()

    def _hmi_on_tree_double_click(self, event: tk.Event) -> str | None:
        tv = self._hmi_tv_menus
        if tv is None:
            return None

        try:
            region = tv.identify("region", event.x, event.y)
            # Always stop Treeview's default double-click behavior (expand/collapse).
            # We'll still allow editing when double-clicking a data cell
            # or the tree label column (#0).
            if region not in {"cell", "tree"}:
                return "break"
            iid = tv.identify_row(event.y)
            if not iid:
                return "break"
            col = tv.identify_column(event.x)
            if not col:
                return "break"
        except Exception:
            return "break"

        tv.selection_set(iid)
        self._hmi_begin_cell_edit(iid, col)
        return "break"

    def _hmi_do_name_from_doref(self, do_ref: str) -> str:
        """Extract DO name from a doRef value.

        Examples:
        - "Str" -> "Str"
        - "ZNPDIS#.Str" -> "Str"
        - "bay.LLN0.SettingControl" -> "SettingControl" (best-effort)
        """
        txt = (do_ref or "").strip()
        if not txt:
            return ""
        if "." in txt:
            txt = (txt.rsplit(".", 1)[-1] or "").strip()
        if txt.lower().startswith("inref%"):
            txt = txt[len("InRef%") :].strip()
        return txt

    def _hmi_on_edit_combobox_focus_out(self, event: tk.Event) -> None:
        """Commit HMI combobox edit on focus-out.

        Avoid committing while the combobox dropdown is open (Windows focus quirks).
        """
        try:
            widget = event.widget
        except Exception:
            widget = None

        if not isinstance(widget, ttk.Combobox):
            self._hmi_end_combo_edit(commit=True)
            return

        cb = widget
        try:
            popdown = cb.tk.call("ttk::combobox::PopdownWindow", str(cb))
            focus_w = str(cb.tk.call("focus") or "")
            if popdown and focus_w and focus_w.startswith(str(popdown)):
                return
        except Exception:
            pass

        try:
            if self._combobox_is_posted(cb):
                return
        except Exception:
            pass

        self._hmi_end_combo_edit(commit=True)

    def _hmi_begin_cell_edit(self, iid: str, col: str) -> None:
        tv = self._hmi_tv_menus
        if tv is None:
            return

        self._hmi_end_cell_edit(commit=True)
        self._hmi_end_combo_edit(commit=True)

        node = self._hmi_tree_iid_to_node.get(iid)
        if node is None:
            return
        kind, parent_el, el = node
        if kind not in {"menu", "item", "data", "attr"}:
            return

        # Resolve clicked display column (#n) to the actual Treeview column id.
        col_id: str | None = None
        if col != "#0":
            try:
                col_idx = int(col[1:]) - 1
                cols = list(tv["columns"]) if hasattr(tv, "__getitem__") else []
                if col_idx < 0 or col_idx >= len(cols):
                    return
                col_id = cols[col_idx]
            except Exception:
                return

        # Map clicked column to model field.
        field: str | None = None
        scope = (getattr(self, "_hmi_scope", "ied") or "ied").strip().lower()
        if kind == "menu":
            if col == "#0":
                field = "name"
            elif col_id in {"desc", "langRef", "hmiMenuDataType", "hmiMenuViewType", "hmiSubTreeType"}:
                field = col_id
            else:
                return
        elif kind == "item":
            # IET: allow editing DO-row name from tree column.
            if col == "#0" and scope == "iet":
                field = "name"
            elif col_id in {"doRef", "daRef"}:
                field = col_id
            else:
                return
        elif kind == "attr":
            if col == "#0":
                field = "name"
            elif col_id == "value":
                field = "value"
            else:
                return
        else:
            # IET: allow editing DA-row name from tree column.
            if col == "#0" and scope == "iet":
                field = "name"
            elif col_id == "daRef":
                field = col_id
            else:
                return

        # Compute bbox.
        try:
            bbox = tv.bbox(iid, column="#0" if col == "#0" else (col_id or ""))
        except Exception:
            return
        if not bbox:
            return
        x, y, w, h = bbox

        if el is None:
            return
        if kind == "attr" and field == "value":
            cur_text = el.attrib.get("value") or el.attrib.get("val") or ""
        else:
            cur_text = el.attrib.get(field) or ""

        # Menu type columns: use dropdown values collected across all HMI files.
        if kind == "menu" and field in {"hmiMenuDataType", "hmiMenuViewType"}:
            try:
                opts = (
                    self._hmi_menu_data_type_values
                    if field == "hmiMenuDataType"
                    else self._hmi_menu_view_type_values
                )
            except Exception:
                opts = []
            values: list[str] = [""]
            if cur_text and cur_text not in values and cur_text not in (opts or []):
                values.append(cur_text)
            values.extend([v for v in (opts or []) if v not in values])

            cb = ttk.Combobox(tv, state="readonly", values=values)
            cb.place(x=x, y=y, width=w, height=h)
            cb.set(cur_text)
            cb.focus_set()
            cb.bind("<Button-1>", lambda _e, _cb=cb: (self._combobox_toggle_posted(_cb), "break")[1])
            try:
                self.after(1, lambda _cb=cb: self._combobox_post(_cb))
            except Exception:
                try:
                    self._combobox_post(cb)
                except Exception:
                    pass
            cb.bind("<<ComboboxSelected>>", lambda _e: self._hmi_end_combo_edit(commit=True))
            cb.bind("<Return>", lambda _e: self._hmi_end_combo_edit(commit=True))
            cb.bind("<Escape>", lambda _e: self._hmi_end_combo_edit(commit=False))
            cb.bind("<FocusOut>", self._hmi_on_edit_combobox_focus_out)
            self._hmi_edit_cb = cb
            self._hmi_edit_cb_iid = iid
            self._hmi_edit_cb_col = field
            return

        # doRef: use dropdown suggestions.
        if field == "doRef" and kind == "item":
            values = self._hmi_doref_dropdown_values(current=cur_text)
            cb = ttk.Combobox(tv, state="readonly", values=values)
            cb.place(x=x, y=y, width=w, height=h)
            cb.set(cur_text)
            cb.focus_set()
            cb.bind("<Button-1>", lambda _e, _cb=cb: (self._combobox_toggle_posted(_cb), "break")[1])
            try:
                self.after(1, lambda _cb=cb: self._combobox_post(_cb))
            except Exception:
                try:
                    self._combobox_post(cb)
                except Exception:
                    pass
            cb.bind("<<ComboboxSelected>>", lambda _e: self._hmi_end_combo_edit(commit=True))
            cb.bind("<Return>", lambda _e: self._hmi_end_combo_edit(commit=True))
            cb.bind("<Escape>", lambda _e: self._hmi_end_combo_edit(commit=False))
            cb.bind("<FocusOut>", self._hmi_on_edit_combobox_focus_out)
            self._hmi_edit_cb = cb
            self._hmi_edit_cb_iid = iid
            self._hmi_edit_cb_col = field
            return

        # daRef: use dropdown suggestions derived from DOType.
        if field == "daRef" and kind in {"item", "data"}:
            eff_do_ref = ""
            try:
                if kind == "item":
                    eff_do_ref = (el.attrib.get("doRef") or "").strip()
                else:
                    if parent_el is not None:
                        eff_do_ref = (parent_el.attrib.get("doRef") or "").strip()
            except Exception:
                eff_do_ref = ""

            values = self._hmi_daref_dropdown_values(do_ref=eff_do_ref, current=cur_text)
            cb = ttk.Combobox(tv, state="readonly", values=values)
            cb.place(x=x, y=y, width=w, height=h)
            cur_disp = (cur_text or "").strip()
            if cur_disp and not cur_disp.startswith("."):
                cur_disp = "." + cur_disp
            cb.set(cur_disp)
            cb.focus_set()
            cb.bind("<Button-1>", lambda _e, _cb=cb: (self._combobox_toggle_posted(_cb), "break")[1])
            try:
                self.after(1, lambda _cb=cb: self._combobox_post(_cb))
            except Exception:
                try:
                    self._combobox_post(cb)
                except Exception:
                    pass
            cb.bind("<<ComboboxSelected>>", lambda _e: self._hmi_end_combo_edit(commit=True))
            cb.bind("<Return>", lambda _e: self._hmi_end_combo_edit(commit=True))
            cb.bind("<Escape>", lambda _e: self._hmi_end_combo_edit(commit=False))
            cb.bind("<FocusOut>", self._hmi_on_edit_combobox_focus_out)
            self._hmi_edit_cb = cb
            self._hmi_edit_cb_iid = iid
            self._hmi_edit_cb_col = field
            return

        # Special-case attribute value editor: IET_HARDLINK_DEFINITION is an InRef dropdown.
        if kind == "attr" and field == "value":
            try:
                attr_name = (el.attrib.get("name") or "").strip()
            except Exception:
                attr_name = ""
            if attr_name == "IET_HARDLINK_DEFINITION":
                values = self._hmi_inref_dropdown_values(current=cur_text)
                cb = ttk.Combobox(tv, state="readonly", values=values)
                cb.place(x=x, y=y, width=w, height=h)
                cb.set(cur_text)
                cb.focus_set()
                cb.bind("<Button-1>", lambda _e, _cb=cb: (self._combobox_toggle_posted(_cb), "break")[1])
                try:
                    self.after(1, lambda _cb=cb: self._combobox_post(_cb))
                except Exception:
                    try:
                        self._combobox_post(cb)
                    except Exception:
                        pass
                cb.bind("<<ComboboxSelected>>", lambda _e: self._hmi_end_combo_edit(commit=True))
                cb.bind("<Return>", lambda _e: self._hmi_end_combo_edit(commit=True))
                cb.bind("<Escape>", lambda _e: self._hmi_end_combo_edit(commit=False))
                cb.bind("<FocusOut>", self._hmi_on_edit_combobox_focus_out)
                self._hmi_edit_cb = cb
                self._hmi_edit_cb_iid = iid
                self._hmi_edit_cb_col = field
                return

        ent = ttk.Entry(tv)
        ent.place(x=x, y=y, width=w, height=h)
        ent.insert(0, cur_text)
        ent.selection_range(0, "end")
        ent.focus_set()

        ent.bind("<Return>", lambda _e: self._hmi_end_cell_edit(commit=True))
        ent.bind("<Escape>", lambda _e: self._hmi_end_cell_edit(commit=False))
        ent.bind("<FocusOut>", lambda _e: self._hmi_end_cell_edit(commit=True))

        self._hmi_edit_entry = ent
        self._hmi_edit_iid = iid
        self._hmi_edit_col = field

    def _hmi_end_combo_edit(self, *, commit: bool) -> None:
        if self._hmi_edit_cb is None or self._hmi_edit_cb_iid is None or self._hmi_edit_cb_col is None:
            return

        cb = self._hmi_edit_cb
        iid = self._hmi_edit_cb_iid
        field = self._hmi_edit_cb_col
        self._hmi_edit_cb = None
        self._hmi_edit_cb_iid = None
        self._hmi_edit_cb_col = None

        new_text = (cb.get() or "")
        try:
            cb.place_forget()
        except Exception:
            pass
        try:
            cb.destroy()
        except Exception:
            pass

        if not commit:
            return

        node = self._hmi_tree_iid_to_node.get(iid)
        if node is None:
            return
        kind, parent_el, el = node
        if kind not in {"menu", "item", "data", "attr"} or el is None:
            return

        if kind == "menu" and field not in {"hmiMenuDataType", "hmiMenuViewType"}:
            return

        if kind == "attr" and field != "value":
            return

        if kind == "attr" and field == "value":
            old = el.attrib.get("value") or el.attrib.get("val") or ""
        else:
            old = el.attrib.get(field) or ""

        v = (new_text or "").strip()
        if field == "doRef" and kind == "item":
            # Auto-complete LN# prefix when user picked/typed bare DO name.
            try:
                ln_ref = (getattr(self, "_hmi_ln_ref", "") or "").strip()
            except Exception:
                ln_ref = ""
            if ln_ref and v and ("." not in v):
                v = f"{ln_ref}.{v}"
            elif (not ln_ref) and v and ("." not in v):
                try:
                    ln_class = (getattr(self, "_hmi_ln_class", "") or "").strip()
                except Exception:
                    ln_class = ""
                if ln_class:
                    v = f"{ln_class}#.{v}"

        if (old or "") == (v or ""):
            return

        self._hmi_push_undo()

        if kind == "attr" and field == "value":
            key = "val" if ("val" in el.attrib and "value" not in el.attrib) else "value"
            if v:
                el.attrib[key] = v
            else:
                el.attrib.pop("value", None)
                el.attrib.pop("val", None)
        else:
            if v:
                el.attrib[field] = v
            else:
                el.attrib.pop(field, None)

        self._hmi_ui_tag_set(el, "changed")

        # Enforce derived names.
        if kind == "item" and field == "doRef":
            try:
                if v:
                    el.attrib["name"] = self._hmi_do_name_from_doref(v)
                else:
                    el.attrib.pop("name", None)
            except Exception:
                pass
            try:
                if self._hmi_tv_menus is not None:
                    self._hmi_tv_menus.item(iid, text=(el.attrib.get("name") or ""))
            except Exception:
                pass

            # Sync DA list for supported measurement CDCs (DEL/WYE/SEQ/CMV/MV).
            try:
                do_name0 = self._hmi_do_name_from_doref(v)
                do_type_id0 = (getattr(self, "_hmi_ln_do_types_by_name", {}) or {}).get(do_name0) or ""
                if do_type_id0:
                    if self._hmi_sync_dataitems_for_do(el, full_do=v, do_type_id=do_type_id0, prune_extra=False):
                        if not self._hmi_ui_is_added(el):
                            self._hmi_ui_tag_set(el, "changed")
                        # Refresh view so newly added DA rows appear.
                        try:
                            self._refresh_hmi_menu_table(select_first=False)
                        except Exception:
                            pass
            except Exception:
                pass

        if kind == "data" and field == "daRef":
            try:
                if v:
                    name0 = (v or "").strip()
                    if name0.startswith("."):
                        name0 = name0[1:]
                    if "." in name0:
                        name0 = (name0.rsplit(".", 1)[-1] or "").strip()
                    el.attrib["name"] = name0
                else:
                    el.attrib.pop("name", None)
            except Exception:
                pass
            try:
                if self._hmi_tv_menus is not None:
                    self._hmi_tv_menus.item(iid, text=(el.attrib.get("name") or ""))
            except Exception:
                pass

        try:
            if self._hmi_tv_menus is not None:
                if self._hmi_ui_is_removed(el):
                    self._hmi_tv_menus.item(iid, tags=("removed",))
                elif self._hmi_ui_is_added(el):
                    self._hmi_tv_menus.item(iid, tags=("added",))
                elif self._hmi_ui_is_changed(el):
                    self._hmi_tv_menus.item(iid, tags=("changed",))
                else:
                    self._hmi_tv_menus.item(iid, tags=())
        except Exception:
            pass

        # Level-4 rows inherit the parent's DO; keep XML consistent.
        if field == "doRef" and kind == "item":
            try:
                for ch in list(el):
                    if not (isinstance(ch.tag, str) and _local_name(ch.tag) == "HMIDataItem"):
                        continue
                    if v:
                        ch.attrib["doRef"] = v
                    else:
                        ch.attrib.pop("doRef", None)
                    self._hmi_ui_tag_set(ch, "changed")
            except Exception:
                pass

            # Update visible DA row coloring too.
            try:
                if self._hmi_tv_menus is not None:
                    for di_iid in self._hmi_tv_menus.get_children(iid):
                        try:
                            self._hmi_tv_menus.item(di_iid, tags=("changed",))
                        except Exception:
                            pass
            except Exception:
                pass

        try:
            if self._hmi_tv_menus is not None:
                if kind == "menu" and field in {"hmiMenuDataType", "hmiMenuViewType"}:
                    self._hmi_tv_menus.set(iid, field, el.attrib.get(field) or "")
                if field in {"doRef", "daRef"}:
                    self._hmi_tv_menus.set(iid, field, el.attrib.get(field) or "")
                if kind == "attr" and field == "value":
                    self._hmi_tv_menus.set(iid, "value", el.attrib.get("value") or el.attrib.get("val") or "")
        except Exception:
            pass

        self._mark_hmi_unsaved()
        try:
            self._update_dirty_ui_hmi()
        except Exception:
            pass

    def _hmi_end_cell_edit(self, *, commit: bool) -> None:
        if self._hmi_edit_entry is None or self._hmi_edit_iid is None or self._hmi_edit_col is None:
            return

        tv = self._hmi_tv_menus
        if tv is None:
            return

        ent = self._hmi_edit_entry
        iid = self._hmi_edit_iid
        field = self._hmi_edit_col
        self._hmi_edit_entry = None
        self._hmi_edit_iid = None
        self._hmi_edit_col = None

        new_text = ent.get()
        try:
            ent.place_forget()
        except Exception:
            pass
        try:
            ent.destroy()
        except Exception:
            pass

        if not commit:
            return

        node = self._hmi_tree_iid_to_node.get(iid)
        if node is None:
            return
        kind, parent_el, el = node
        if kind not in {"menu", "item", "data", "attr"} or el is None:
            return

        old = el.attrib.get(field) or ""
        if (old or "") == (new_text or ""):
            return

        self._hmi_push_undo()

        v = (new_text or "").strip() if field != "desc" else (new_text or "")
        if kind == "attr" and field == "value":
            key = "val" if ("val" in el.attrib and "value" not in el.attrib) else "value"
            if v:
                el.attrib[key] = v
            else:
                el.attrib.pop("value", None)
                el.attrib.pop("val", None)
        else:
            if v:
                el.attrib[field] = v
            else:
                el.attrib.pop(field, None)

        self._hmi_ui_tag_set(el, "changed")

        try:
            if tv is not None:
                if self._hmi_ui_is_removed(el):
                    tv.item(iid, tags=("removed",))
                elif self._hmi_ui_is_added(el):
                    tv.item(iid, tags=("added",))
                elif self._hmi_ui_is_changed(el):
                    tv.item(iid, tags=("changed",))
                else:
                    tv.item(iid, tags=())
        except Exception:
            pass

        # Update row display.
        try:
            if kind in {"item", "data"} and field == "name":
                tv.item(iid, text=(el.attrib.get("name") or ""))
            else:
                if kind == "menu":
                    if field in {"desc", "langRef", "hmiMenuDataType", "hmiMenuViewType", "hmiSubTreeType"}:
                        tv.set(iid, field, el.attrib.get(field) or "")
                elif kind == "attr":
                    if field == "name":
                        tv.item(iid, text=(el.attrib.get("name") or ""))
                    elif field == "value":
                        tv.set(iid, "value", el.attrib.get("value") or el.attrib.get("val") or "")
                else:
                    if field in {"doRef", "daRef"}:
                        tv.set(iid, field, el.attrib.get(field) or "")
        except Exception:
            pass

        self._mark_hmi_unsaved()
        try:
            self._update_dirty_ui_hmi()
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
                do_ref = ch.attrib.get("doRef") or ""
                da_ref = ch.attrib.get("daRef") or ""
                do_ref0 = (do_ref or "").strip()
                if do_ref0:
                    name = self._hmi_do_name_from_doref(do_ref0)
                else:
                    name = (ch.attrib.get("name") or "").strip()
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
            da_ref0 = (ch.attrib.get("daRef") or "").strip()
            if da_ref0.startswith("."):
                da_ref0 = da_ref0[1:]
            if "." in da_ref0:
                da_ref0 = (da_ref0.rsplit(".", 1)[-1] or "").strip()
            name = da_ref0 if da_ref0 else (ch.attrib.get("name") or "")
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
        return

    def _hmi_on_item_selected(self) -> None:
        return

    def _hmi_menu_add(self) -> None:
        if self._hmi_root is None:
            messagebox.showerror("Missing", "No HMI loaded", parent=self)
            return
        dlg = _HmiMenuEditDialog(
            self,
            title="Add HMIMenu",
            name="Menu_",
            desc="",
            lang_ref="",
            data_type="",
            view_type="",
            sub_tree_type="",
            data_type_values=[""] + list(getattr(self, "_hmi_menu_data_type_values", []) or []),
            view_type_values=[""] + list(getattr(self, "_hmi_menu_view_type_values", []) or []),
        )
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
            lang_ref=menu.attrib.get("langRef") or "",
            data_type=menu.attrib.get("hmiMenuDataType") or "",
            view_type=menu.attrib.get("hmiMenuViewType") or "",
            sub_tree_type=menu.attrib.get("hmiSubTreeType") or "",
            data_type_values=[""] + list(getattr(self, "_hmi_menu_data_type_values", []) or []),
            view_type_values=[""] + list(getattr(self, "_hmi_menu_view_type_values", []) or []),
        )
        res = dlg.show()
        if not res:
            return
        try:
            self._hmi_rename_menu_and_refs(menu, (res.get("name") or "").strip())
        except Exception:
            pass
        for k in ("desc", "langRef", "hmiMenuDataType", "hmiMenuViewType", "hmiSubTreeType"):
            v = (res.get(k) or "") if k == "desc" else (res.get(k) or "").strip()
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

        app_root: ET.Element
        try:
            # Prefer in-memory application root when it matches this HMI.
            if getattr(self, "_app_root", None) is not None and getattr(self, "_app_file_path", None) is not None:
                try:
                    if Path(self._app_file_path) == Path(app_path):
                        app_root = self._app_root
                    else:
                        app_root = ET.parse(app_path).getroot()
                except Exception:
                    app_root = ET.parse(app_path).getroot()
            else:
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

        # Keep both the raw LnRef from the application file and a normalized form
        # used by some helper logic.
        ln_ref_raw = ln_ref
        try:
            ln_ref_norm = self._hmi_normalize_lnref_for_doref(ln_ref_raw)
        except Exception:
            ln_ref_norm = ""

        def _is_true(v: str | None) -> bool:
            vv = (v or "").strip().lower()
            return vv in {"true", "1", "yes", "y", "on"}

        def _src_points_to_current_ln_do(src: str, *, ln_ref_raw0: str, ln_ref0: str) -> bool:
            s = (src or "").strip()
            if not s:
                return False
            # Common form for local settings in application files.
            if s.startswith("."):
                return True
            # Some files may use fully-qualified LnRef prefix.
            if ln_ref_raw0 and s.startswith(f"{ln_ref_raw0}."):
                return True
            if ln_ref0 and s.startswith(f"{ln_ref0}."):
                return True
            return False

        outputs_raw: list[tuple[str, str]] = []
        # (display_name, full_do_ref)
        inputs_for_hmi: list[tuple[str, str]] = []
        settings: list[str] = []

        def _extract_inref_purpose(do_ref_text: str) -> str:
            txt = (do_ref_text or "").strip()
            if not txt:
                return ""
            lo = txt.lower()
            key = "inref%"
            pos = lo.find(key)
            if pos < 0:
                return ""
            return txt[pos + len(key) :].strip()

        def _input_ln_sequence(src_text: str) -> tuple[str, ...]:
            out: list[str] = []
            for part in (src_text or "").split(";"):
                p = part.strip()
                if not p:
                    continue
                ln_name = (p.split("@", 1)[0] or "").strip()
                if not ln_name:
                    continue
                out.append(ln_name)
            return tuple(out)

        seen_input_ln_seq: set[tuple[str, ...]] = set()
        for el in list(fun_block):
            if not isinstance(el.tag, str):
                continue
            local = _local_name(el.tag)
            if local == "input":
                src0 = (el.attrib.get("src") or "").strip()
                if not src0:
                    continue
                ln_seq0 = _input_ln_sequence(src0)
                if ln_seq0 in seen_input_ln_seq:
                    continue
                seen_input_ln_seq.add(ln_seq0)

                name0 = (el.attrib.get("name") or "").strip()
                do_ref_raw0 = (el.attrib.get("doRef") or "").strip()
                purpose0 = _extract_inref_purpose(do_ref_raw0)
                if not purpose0:
                    purpose0 = name0
                if not purpose0:
                    continue

                if ln_ref:
                    do_ref0 = f"{ln_ref}.InRef%{purpose0}"
                else:
                    do_ref0 = f"InRef%{purpose0}"

                display_name = name0 or purpose0
                inputs_for_hmi.append((display_name, do_ref0))
            elif local == "output":
                name = (el.attrib.get("name") or "").strip()
                do_ref = (el.attrib.get("doRef") or "").strip()
                # Skip DOs marked for faultlog output; they should not be added to HMI.
                if _is_true(el.attrib.get("faultlog")):
                    continue
                # Skip outputs without explicit doRef in the application file.
                # (Do not fall back to LnRef + name.)
                if not do_ref:
                    continue
                # Skip outputs that are marked as confpin output.
                # Some files use @confpin/@conpin, others encode it via @outPurpose.
                try:
                    out_purpose = (el.attrib.get("outPurpose") or "").strip().lower()
                except Exception:
                    out_purpose = ""
                if _is_true(el.attrib.get("confpin")) or _is_true(el.attrib.get("conpin")) or out_purpose == "confpin":
                    continue
                if name:
                    outputs_raw.append((name, do_ref))
            elif local == "setting":
                name = (el.attrib.get("name") or "").strip()
                # Skip settings generated by Confpin.
                if (el.attrib.get("src") or "").strip().lower() == "confpin":
                    continue
                # Only include settings whose src points to the current LN DO.
                # Example of external src (should be skipped): 'bay.CommonPDIS#.ChrAng'
                src0 = (el.attrib.get("src") or "").strip()
                if src0 and (not _src_points_to_current_ln_do(src0, ln_ref_raw0=ln_ref_raw, ln_ref0=ln_ref_norm)):
                    continue
                # If setting's referenced DO is empty or numeric, skip.
                # Some application files may use src like '.' or '.1' which is not a valid DO name.
                try:
                    do_from_src = self._hmi_do_name_from_doref(src0)
                except Exception:
                    do_from_src = ""
                if not do_from_src or do_from_src.isdigit():
                    continue
                if name:
                    settings.append(name)

        # Ensure some important DOs are present if the corresponding LN has them,
        # even if application file does not list them under <setting>.
        # (Examples: SetMod, StartMod)
        try:
            inst_path = self._guess_ln_instance_path_from_lnref(ln_ref_raw) if ln_ref_raw else None
        except Exception:
            inst_path = None
        try:
            if inst_path is not None and inst_path.exists():
                do_names = self._hmi_parse_ln_instance_do_names(inst_path)
                have = {s.strip().lower() for s in (settings or []) if (s or "").strip()}
                for target in ("setmod", "startmod"):
                    nm0 = next((n for n in do_names if (n or "").strip().lower() == target), "")
                    if not nm0:
                        continue
                    if nm0.strip().lower() not in have:
                        settings.append(nm0.strip())
                        have.add(nm0.strip().lower())
        except Exception:
            pass

        def _order_settings_names(values: list[str]) -> list[str]:
            """Return settings in fixed order:

            1) SettingControl (always present)
            2) SetMod (only when present)
            3) Remaining settings in original order
            """
            seq = [(v or "").strip() for v in (values or []) if (v or "").strip()]

            # De-duplicate while preserving first appearance.
            seen: set[str] = set()
            uniq: list[str] = []
            for nm in seq:
                key = nm.lower()
                if key in seen:
                    continue
                seen.add(key)
                uniq.append(nm)

            has_setmod = any((nm.lower() == "setmod") for nm in uniq)
            rest = [nm for nm in uniq if nm.lower() not in {"settingcontrol", "setmod"}]

            out = ["SettingControl"]
            if has_setmod:
                out.append("SetMod")
            out.extend(rest)
            return out

        settings = _order_settings_names(settings)

        # Precompute DO name -> DOType id mapping so Refresh can auto-populate
        # DA rows for some CDCs (e.g., ACD).
        do_types_by_name: dict[str, str] = {}
        try:
            if inst_path is not None and inst_path.exists():
                ln_type_id, _inst_prefix, _inst_ln_class = self._hmi_peek_ln_instance_ln_attrs(inst_path)
                ln_type_id = (ln_type_id or "").strip()
                if ln_type_id:
                    info = None
                    if getattr(self, "catalog", None) is not None:
                        for it in (self.catalog.lnode_types or []):
                            if it.id == ln_type_id:
                                info = it
                                break
                    if info is None:
                        try:
                            ln_dir = Path(self.iec61850_dir) / "LNodeType"
                            cand = None
                            p0 = ln_dir / f"{ln_type_id}.xml"
                            if p0.is_file():
                                cand = p0
                            else:
                                for p in ln_dir.rglob(f"{ln_type_id}.xml"):
                                    if p.is_file():
                                        cand = p
                                        break
                            if cand is not None:
                                tree = ET.parse(cand)
                                rr = tree.getroot()
                                ln = rr.find(f".//{_q(SCL_NS, 'LNodeType')}")
                                ln_class = (ln.attrib.get("lnClass") or "").strip() if ln is not None else ""
                                desc = (ln.attrib.get("desc") or "").strip() if ln is not None else ""
                                info = LNodeTypeInfo(id=ln_type_id, ln_class=ln_class, desc=desc, file_path=cand)
                        except Exception:
                            info = None
                    if info is not None:
                        mdl = load_lnode_type(info)
                        do_types_by_name = {
                            (d.name or "").strip(): (d.do_type or "").strip()
                            for d in (mdl.dos or [])
                            if (d.name or "").strip()
                        }
        except Exception:
            do_types_by_name = {}

        outputs_status: list[tuple[str, str]] = []
        outputs_meas: list[tuple[str, str]] = []
        for nm0, do_ref0 in (outputs_raw or []):
            do_name0 = self._hmi_do_name_from_doref(do_ref0)
            cdc0 = ""
            try:
                do_type_id0 = (do_types_by_name.get(do_name0) or "").strip()
                if do_type_id0:
                    cdc0 = (self._hmi_dotype_cdc(do_type_id0) or "").strip().upper()
            except Exception:
                cdc0 = ""
            if cdc0 in {"WYE", "DEL", "SEQ", "CMV", "MV"}:
                outputs_meas.append((nm0, do_ref0))
            else:
                outputs_status.append((nm0, do_ref0))

        # Merge into existing menus.
        # Refresh should affect both IED and Manual menus regardless of current active scope.
        menus: list[ET.Element] = []
        for mm in self._hmi_root.iter():
            if not (isinstance(mm.tag, str) and _local_name(mm.tag) == "HMIMenu"):
                continue
            nm0 = (mm.attrib.get("name") or "").strip()
            if nm0.startswith("IET_Protection"):
                continue
            menus.append(mm)

        manual_outputs_name = "Manual_Protection_Outputs"
        manual_inputs_name = "Manual_Protection_Inputs"
        manual_settings_name = "Manual_Protection_Settings"

        def _find_menu_by_name(name: str) -> ET.Element | None:
            key = (name or "").strip().lower()
            for m0 in menus:
                if ((m0.attrib.get("name") or "").strip().lower() == key):
                    return m0
            return None

        # Ensure Manual menus exist during Refresh so auto-fill also works on files
        # created before Manual support was introduced.
        try:
            if outputs_raw and _find_menu_by_name(manual_outputs_name) is None:
                m0 = ET.SubElement(self._hmi_root, _q(HMI_CUST_NS, "HMIMenu"))
                m0.attrib["name"] = manual_outputs_name
                menus.append(m0)
                self._hmi_ui_tag_set(m0, "added")
            if inputs_for_hmi and _find_menu_by_name(manual_inputs_name) is None:
                m0 = ET.SubElement(self._hmi_root, _q(HMI_CUST_NS, "HMIMenu"))
                m0.attrib["name"] = manual_inputs_name
                menus.append(m0)
                self._hmi_ui_tag_set(m0, "added")
            manual_settings = [nm for nm in (settings or []) if (nm or "").strip().lower() != "settingcontrol"]
            if manual_settings and _find_menu_by_name(manual_settings_name) is None:
                m0 = ET.SubElement(self._hmi_root, _q(HMI_CUST_NS, "HMIMenu"))
                m0.attrib["name"] = manual_settings_name
                menus.append(m0)
                self._hmi_ui_tag_set(m0, "added")
        except Exception:
            manual_settings = [nm for nm in (settings or []) if (nm or "").strip().lower() != "settingcontrol"]
        def _norm_view_type(vt: str | None) -> str:
            s = (vt or "").strip().upper()
            if s.startswith("HMI_MENU_VIEW_TYPE_"):
                s = s[len("HMI_MENU_VIEW_TYPE_") :]
            # Some sources may use separators; normalize to a compact token.
            s = s.replace("_", "").replace("-", "").replace(" ", "")
            return s

        def _is_settings_view_type(vt: str | None) -> bool:
            # setting tab
            return _norm_view_type(vt) == "SETTING"

        def _is_outputs_view_type(vt: str | None) -> bool:
            # output tab
            return _norm_view_type(vt) in {"STATUS", "MEASWITHCONTROL", "MEASUREGROUP", "MEASURE"}

        def _menu_is_outputs(menu: ET.Element) -> bool:
            nm = (menu.attrib.get("name") or "").strip()
            if nm.endswith("_Outputs"):
                return True
            return _is_outputs_view_type(menu.attrib.get("hmiMenuViewType"))

        def _menu_outputs_bucket(menu: ET.Element) -> str:
            nm = (menu.attrib.get("name") or "").strip()
            if nm.endswith("_Meas"):
                return "meas"
            vt = _norm_view_type(menu.attrib.get("hmiMenuViewType"))
            if vt in {"MEASURE", "MEASUREGROUP", "MEASWITHCONTROL"}:
                return "meas"
            return "status"

        def _menu_is_settings(menu: ET.Element) -> bool:
            nm = (menu.attrib.get("name") or "").strip()
            if nm.endswith("_Settings"):
                return True
            return _is_settings_view_type(menu.attrib.get("hmiMenuViewType"))

        def _is_inputs_view_type(vt: str | None) -> bool:
            return _norm_view_type(vt) == "INPUT"

        def _menu_is_inputs(menu: ET.Element) -> bool:
            nm = (menu.attrib.get("name") or "").strip()
            if nm.endswith("_Inputs"):
                return True
            return _is_inputs_view_type(menu.attrib.get("hmiMenuViewType"))

        out_menus = [m for m in menus if _menu_is_outputs(m)]
        out_status_menus = [m for m in out_menus if _menu_outputs_bucket(m) == "status"]
        out_meas_menus = [m for m in out_menus if _menu_outputs_bucket(m) == "meas"]
        in_menus = [m for m in menus if _menu_is_inputs(m)]
        set_menus = [m for m in menus if _menu_is_settings(m)]
        if not out_menus and not in_menus and not set_menus:
            # Not every HMI file is expected to have Outputs/Inputs/Settings pages.
            # If none exist, Refresh is a no-op.
            self._set_status(
                f"Refresh skipped: no Outputs/Inputs/Settings menus found in this HMI (LnRef={ln_ref})"
            )
            return

        undo_len0 = len(self._hmi_undo_stack)
        self._hmi_push_undo()

        def merge_outputs(
            menu: ET.Element,
            expected_outputs: list[tuple[str, str]],
            *,
            allow_da_autofill: bool,
        ) -> tuple[int, int, int]:
            existing_by_key: dict[str, ET.Element] = {}
            for it in list(menu):
                if not (isinstance(it.tag, str) and _local_name(it.tag) == "HMIMenuItem"):
                    continue
                if (it.attrib.get("ref") or "").strip():
                    continue
                # Use DO key derived from doRef for stable matching across refreshes,
                # even when display name differs from DO name.
                do_ref0 = (it.attrib.get("doRef") or "").strip()
                if do_ref0:
                    key = (self._hmi_do_name_from_doref(do_ref0) or "").strip().lower()
                else:
                    key = (it.attrib.get("name") or "").strip().lower()
                if key:
                    existing_by_key[key] = it

            expected_rows: list[tuple[str, str, str]] = []
            seen_expected: set[str] = set()
            for nm, do_ref_raw in expected_outputs:
                full_do = f"{ln_ref}{do_ref_raw}" if do_ref_raw.startswith(".") else do_ref_raw
                key = (self._hmi_do_name_from_doref(full_do) or "").strip().lower()
                if not key or key in seen_expected:
                    continue
                seen_expected.add(key)
                expected_rows.append((key, (nm or "").strip(), full_do))

            expected_keys = {k for k, _nm, _do in expected_rows}
            added = 0
            changed = 0
            removed = 0

            def ensure_acd_children(parent_it: ET.Element, *, do_name: str, full_do: str) -> bool:
                do_type_id = (do_types_by_name.get(do_name) or "").strip()
                if not do_type_id:
                    return False
                paths = self._hmi_acd_st_da_paths(do_type_id)
                if not paths:
                    return False

                # Conservative: only auto-populate when no DA rows exist yet.
                for ch in list(parent_it):
                    if isinstance(ch.tag, str) and _local_name(ch.tag) == "HMIDataItem":
                        return False

                added_any = False
                for p in paths:
                    di = ET.SubElement(parent_it, _q(HMI_CUST_NS, "HMIDataItem"))
                    di.attrib["name"] = (p.split(".")[-1] if p else "").strip()
                    di.attrib["doRef"] = full_do
                    di.attrib["daRef"] = f".{p}"
                    self._hmi_ui_tag_set(di, "added")
                    added_any = True

                return added_any

            def ensure_act_children(parent_it: ET.Element, *, do_name: str, full_do: str) -> bool:
                do_type_id = (do_types_by_name.get(do_name) or "").strip()
                if not do_type_id:
                    return False
                paths = self._hmi_act_st_da_paths(do_type_id)
                if not paths:
                    return False

                # Conservative: only auto-populate when no DA rows exist yet.
                for ch in list(parent_it):
                    if isinstance(ch.tag, str) and _local_name(ch.tag) == "HMIDataItem":
                        return False

                added_any = False
                for p in paths:
                    di = ET.SubElement(parent_it, _q(HMI_CUST_NS, "HMIDataItem"))
                    di.attrib["name"] = (p.split(".")[-1] if p else "").strip()
                    di.attrib["doRef"] = full_do
                    di.attrib["daRef"] = f".{p}"
                    self._hmi_ui_tag_set(di, "added")
                    added_any = True

                return added_any

            def ensure_measure_children(parent_it: ET.Element, *, do_name: str, full_do: str) -> bool:
                do_type_id = (do_types_by_name.get(do_name) or "").strip()
                if not do_type_id:
                    return False
                # On Refresh, treat these CDCs as auto-managed: add missing and remove stale.
                return self._hmi_sync_dataitems_for_do(parent_it, full_do=full_do, do_type_id=do_type_id, prune_extra=True)

            for key, nm, full_do in expected_rows:
                do_name_from_ref = self._hmi_do_name_from_doref(full_do)
                if key in existing_by_key:
                    it = existing_by_key[key]
                    cur_do = (it.attrib.get("doRef") or "").strip()
                    did_change = False
                    if cur_do != (full_do or ""):
                        it.attrib["doRef"] = full_do
                        self._hmi_ui_tag_set(it, "changed")
                        changed += 1
                        did_change = True

                    if allow_da_autofill:
                        # For ACD DOs, auto-populate ST DA list (excluding q/t).
                        try:
                            if ensure_acd_children(it, do_name=do_name_from_ref, full_do=full_do):
                                if not self._hmi_ui_is_added(it) and not did_change:
                                    self._hmi_ui_tag_set(it, "changed")
                                    changed += 1
                        except Exception:
                            pass

                        # For ACT DOs, auto-populate ST DA list (excluding q/t).
                        try:
                            if ensure_act_children(it, do_name=do_name_from_ref, full_do=full_do):
                                if not self._hmi_ui_is_added(it) and not did_change:
                                    self._hmi_ui_tag_set(it, "changed")
                                    changed += 1
                        except Exception:
                            pass

                        # For supported measurement CDCs, auto-populate DA list (adds only missing).
                        try:
                            if ensure_measure_children(it, do_name=do_name_from_ref, full_do=full_do):
                                if not self._hmi_ui_is_added(it) and not did_change:
                                    self._hmi_ui_tag_set(it, "changed")
                                    changed += 1
                        except Exception:
                            pass
                    else:
                        # Manual outputs: DO only, no DA refs/data rows.
                        if (it.attrib.get("daRef") or "").strip():
                            it.attrib.pop("daRef", None)
                            if not self._hmi_ui_is_added(it) and not did_change:
                                self._hmi_ui_tag_set(it, "changed")
                                changed += 1
                                did_change = True
                        di_children = [
                            ch for ch in list(it)
                            if isinstance(ch.tag, str) and _local_name(ch.tag) == "HMIDataItem"
                        ]
                        if di_children:
                            for ch in di_children:
                                try:
                                    it.remove(ch)
                                except Exception:
                                    pass
                            if not self._hmi_ui_is_added(it) and not did_change:
                                self._hmi_ui_tag_set(it, "changed")
                                changed += 1
                    continue
                it = ET.SubElement(menu, _q(HMI_CUST_NS, "HMIMenuItem"))
                it.attrib["name"] = nm
                it.attrib["doRef"] = full_do
                if allow_da_autofill:
                    # For SPS DOs, default DA ref should point to stVal.
                    try:
                        do_type_id0 = (do_types_by_name.get(do_name_from_ref) or "").strip()
                        if do_type_id0 and (self._hmi_dotype_cdc(do_type_id0) or "").strip().upper() == "SPS":
                            if not (it.attrib.get("daRef") or "").strip():
                                it.attrib["daRef"] = ".stVal"
                    except Exception:
                        pass
                self._hmi_ui_tag_set(it, "added")
                added += 1

                if allow_da_autofill:
                    # For ACD DOs, auto-populate ST DA list (excluding q/t).
                    try:
                        ensure_acd_children(it, do_name=do_name_from_ref, full_do=full_do)
                    except Exception:
                        pass

                    # For ACT DOs, auto-populate ST DA list (excluding q/t).
                    try:
                        ensure_act_children(it, do_name=do_name_from_ref, full_do=full_do)
                    except Exception:
                        pass

                    # For supported measurement CDCs, auto-populate DA list (adds only missing).
                    try:
                        ensure_measure_children(it, do_name=do_name_from_ref, full_do=full_do)
                    except Exception:
                        pass

            # Stage-delete items that are no longer present in the application output list.
            for key, it in existing_by_key.items():
                if key in expected_keys:
                    continue
                if self._hmi_ui_is_added(it):
                    try:
                        menu.remove(it)
                    except Exception:
                        pass
                else:
                    self._hmi_ui_tag_set(it, "removed")
                removed += 1

            return (added, changed, removed)

        def merge_settings(
            menu: ET.Element,
            settings_expected: list[str],
            *,
            preserve_setting_control: bool,
        ) -> tuple[int, int, int]:
            existing_by_name: dict[str, ET.Element] = {}
            for it in list(menu):
                if not (isinstance(it.tag, str) and _local_name(it.tag) == "HMIMenuItem"):
                    continue
                if (it.attrib.get("ref") or "").strip():
                    continue
                do_ref0 = (it.attrib.get("doRef") or "").strip()
                if do_ref0:
                    nm = (do_ref0.rsplit(".", 1)[-1] or "").strip()
                else:
                    nm = (it.attrib.get("name") or "").strip()
                if nm:
                    existing_by_name[nm] = it

            # SettingControl is managed outside Application->HMI refresh.
            # If it was previously staged as removed by an older Refresh, revive it.
            for nm0, it0 in existing_by_name.items():
                if (nm0 or "").strip().lower() == "settingcontrol" and self._hmi_ui_is_removed(it0):
                    self._hmi_ui_tag_clear(it0)
                    break
            expected_names = {nm for nm in settings_expected if (nm or "").strip()}
            added = 0
            changed = 0
            removed = 0

            def _setting_doref(nm: str) -> str:
                if (nm or "").strip().lower() == "settingcontrol":
                    return "bay.LLN0.SettingControl"
                return f"{ln_ref}.{nm}"

            for nm in settings_expected:
                if nm in existing_by_name:
                    it = existing_by_name[nm]
                    full_do = _setting_doref(nm)
                    cur_do = (it.attrib.get("doRef") or "").strip()
                    if cur_do != (full_do or ""):
                        it.attrib["doRef"] = full_do
                        self._hmi_ui_tag_set(it, "changed")
                        changed += 1
                    if (it.attrib.get("name") or "").strip() != nm:
                        it.attrib["name"] = nm
                        self._hmi_ui_tag_set(it, "changed")
                        changed += 1

                    # For ACT DOs, auto-populate ST DA list (excluding q/t).
                    try:
                        do_type_id0 = (do_types_by_name.get(nm) or "").strip()
                        if do_type_id0 and (self._hmi_dotype_cdc(do_type_id0) or "").strip().upper() == "ACT":
                            # Conservative: only auto-populate when no DA rows exist yet.
                            have_di = any(
                                isinstance(ch.tag, str) and _local_name(ch.tag) == "HMIDataItem" for ch in list(it)
                            )
                            if not have_di:
                                paths0 = self._hmi_act_st_da_paths(do_type_id0)
                                for p in paths0:
                                    di0 = ET.SubElement(it, _q(HMI_CUST_NS, "HMIDataItem"))
                                    di0.attrib["name"] = (p.split(".")[-1] if p else "").strip()
                                    di0.attrib["doRef"] = full_do
                                    di0.attrib["daRef"] = f".{p}"
                                    self._hmi_ui_tag_set(di0, "added")
                                if paths0 and not self._hmi_ui_is_added(it):
                                    self._hmi_ui_tag_set(it, "changed")
                                    changed += 1
                    except Exception:
                        pass
                    continue
                it = ET.SubElement(menu, _q(HMI_CUST_NS, "HMIMenuItem"))
                it.attrib["name"] = nm
                it.attrib["doRef"] = _setting_doref(nm)
                self._hmi_ui_tag_set(it, "added")
                added += 1

                # For ACT DOs, auto-populate ST DA list (excluding q/t).
                try:
                    do_type_id0 = (do_types_by_name.get(nm) or "").strip()
                    if do_type_id0 and (self._hmi_dotype_cdc(do_type_id0) or "").strip().upper() == "ACT":
                        paths0 = self._hmi_act_st_da_paths(do_type_id0)
                        for p in paths0:
                            di0 = ET.SubElement(it, _q(HMI_CUST_NS, "HMIDataItem"))
                            di0.attrib["name"] = (p.split(".")[-1] if p else "").strip()
                            di0.attrib["doRef"] = f"{ln_ref}.{nm}"
                            di0.attrib["daRef"] = f".{p}"
                            self._hmi_ui_tag_set(di0, "added")
                except Exception:
                    pass

            # Stage-delete items that are no longer present in the application settings list.
            for nm, it in existing_by_name.items():
                # Preserve SettingControl: do not delete it during Refresh.
                if preserve_setting_control and (nm or "").strip().lower() == "settingcontrol":
                    continue
                if nm in expected_names:
                    continue
                if self._hmi_ui_is_added(it):
                    try:
                        menu.remove(it)
                    except Exception:
                        pass
                else:
                    self._hmi_ui_tag_set(it, "removed")
                removed += 1

            # Keep menu item XML order aligned with required Setting tab order.
            # (SettingControl first, SetMod second when present, then remaining order.)
            try:
                ordered_items: list[ET.Element] = []
                by_name_ci: dict[str, ET.Element] = {}
                for it0 in list(menu):
                    if not (isinstance(it0.tag, str) and _local_name(it0.tag) == "HMIMenuItem"):
                        continue
                    if (it0.attrib.get("ref") or "").strip():
                        continue
                    nm0 = ((it0.attrib.get("name") or "").strip() or self._hmi_do_name_from_doref(it0.attrib.get("doRef") or "")).strip()
                    if nm0:
                        by_name_ci[nm0.lower()] = it0
                for nm0 in settings_expected:
                    it0 = by_name_ci.get((nm0 or "").lower())
                    if it0 is not None:
                        ordered_items.append(it0)
                for it0 in ordered_items:
                    try:
                        menu.remove(it0)
                    except Exception:
                        pass
                for it0 in ordered_items:
                    menu.append(it0)
            except Exception:
                pass

            return (added, changed, removed)

        def merge_inputs(menu: ET.Element) -> tuple[int, int, int]:
            existing_by_name: dict[str, ET.Element] = {}
            for it in list(menu):
                if not (isinstance(it.tag, str) and _local_name(it.tag) == "HMIMenuItem"):
                    continue
                if (it.attrib.get("ref") or "").strip():
                    continue
                do_ref0 = (it.attrib.get("doRef") or "").strip()
                if do_ref0:
                    nm = (self._hmi_do_name_from_doref(do_ref0) or "").strip()
                else:
                    nm = (it.attrib.get("name") or "").strip()
                if nm:
                    existing_by_name[nm] = it

            expected_names = {
                (self._hmi_do_name_from_doref(dr) or "").strip()
                for _disp, dr in (inputs_for_hmi or [])
                if (dr or "").strip()
            }
            expected_names = {n for n in expected_names if n}

            added = 0
            changed = 0
            removed = 0

            for display_name, full_do in (inputs_for_hmi or []):
                key = (self._hmi_do_name_from_doref(full_do) or "").strip()
                if not key:
                    continue
                if key in existing_by_name:
                    it = existing_by_name[key]
                    did_change = False
                    if (it.attrib.get("doRef") or "").strip() != (full_do or ""):
                        it.attrib["doRef"] = full_do
                        self._hmi_ui_tag_set(it, "changed")
                        changed += 1
                        did_change = True
                    if (it.attrib.get("name") or "").strip() != (display_name or ""):
                        it.attrib["name"] = (display_name or "").strip()
                        if not did_change:
                            self._hmi_ui_tag_set(it, "changed")
                            changed += 1
                            did_change = True
                    if (it.attrib.get("daRef") or "").strip() != ".setSrcRef":
                        it.attrib["daRef"] = ".setSrcRef"
                        if not did_change:
                            self._hmi_ui_tag_set(it, "changed")
                            changed += 1
                    continue

                it = ET.SubElement(menu, _q(HMI_CUST_NS, "HMIMenuItem"))
                it.attrib["name"] = (display_name or "").strip()
                it.attrib["doRef"] = full_do
                it.attrib["daRef"] = ".setSrcRef"
                self._hmi_ui_tag_set(it, "added")
                added += 1

            for nm, it in existing_by_name.items():
                if nm in expected_names:
                    continue
                if self._hmi_ui_is_added(it):
                    try:
                        menu.remove(it)
                    except Exception:
                        pass
                else:
                    self._hmi_ui_tag_set(it, "removed")
                removed += 1

            return (added, changed, removed)

        added_out = 0
        changed_out = 0
        removed_out = 0
        for m in out_status_menus:
            nm = (m.attrib.get("name") or "").strip().lower()
            exp = outputs_raw if nm == manual_outputs_name.lower() else outputs_status
            a, c, r = merge_outputs(m, exp, allow_da_autofill=(nm != manual_outputs_name.lower()))
            added_out += a
            changed_out += c
            removed_out += r

        for m in out_meas_menus:
            a, c, r = merge_outputs(m, outputs_meas, allow_da_autofill=True)
            added_out += a
            changed_out += c
            removed_out += r

        added_set = 0
        changed_set = 0
        removed_set = 0
        for m in set_menus:
            nm = (m.attrib.get("name") or "").strip().lower()
            if nm == manual_settings_name.lower():
                a, c, r = merge_settings(m, manual_settings, preserve_setting_control=False)
            else:
                a, c, r = merge_settings(m, settings, preserve_setting_control=True)
            added_set += a
            changed_set += c
            removed_set += r

        added_in = 0
        changed_in = 0
        removed_in = 0
        for m in in_menus:
            a, c, r = merge_inputs(m)
            added_in += a
            changed_in += c
            removed_in += r

        total_changes = (
            added_out + changed_out + removed_out
            + added_set + changed_set + removed_set
            + added_in + changed_in + removed_in
        )
        if total_changes <= 0:
            # Nothing changed: drop the undo snapshot we captured above.
            try:
                if len(self._hmi_undo_stack) > undo_len0:
                    self._hmi_undo_stack.pop()
            except Exception:
                pass
            self._set_status(f"Refresh: no changes (LnRef={ln_ref})")
            return

        # Auto-expand scopes after Refresh so newly created menus are visible immediately.
        try:
            scope0 = (getattr(self, "_hmi_scope", "ied") or "ied").strip().lower()
            if scope0 not in {"ied", "iet", "manual"}:
                scope0 = "ied"
            for s in ("ied", "manual"):
                self._hmi_set_scope(s, refresh=False)
                self._refresh_hmi_views(select_first_menu=False, open_selection_path=False)
                self._hmi_unfold_all()
            self._hmi_set_scope(scope0, refresh=False)
        except Exception:
            pass

        self._refresh_hmi_views(select_first_menu=False)
        self._mark_hmi_unsaved()
        self._set_status(
            f"Refreshed from application {os.fspath(app_path.name)} (LnRef={ln_ref}): "
            f"+{added_out} outputs, ~{changed_out} outputs, -{removed_out} outputs; "
            f"+{added_set} settings, ~{changed_set} settings, -{removed_set} settings; "
            f"+{added_in} inputs, ~{changed_in} inputs, -{removed_in} inputs"
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

            max_show = 1200
            shown = filtered[:max_show]
            self.cb_do_tmpl["values"] = shown
            suffix = "" if len(filtered) <= max_show else f" (showing first {max_show})"
            self.lbl_do_tmpl_match.configure(text=f"{len(filtered)} match{'' if len(filtered)==1 else 'es'}{suffix}")
            if raw:
                if shown:
                    self.var_do_tmpl_selected.set(shown[0])
                return
            if shown and cur not in shown:
                self.var_do_tmpl_selected.set(shown[0])

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

            max_show = 1200
            shown = filtered[:max_show]
            self.cb_app["values"] = shown
            suffix = "" if len(filtered) <= max_show else f" (showing first {max_show})"
            self.lbl_app_match.configure(text=f"{len(filtered)} match{'' if len(filtered)==1 else 'es'}{suffix}")
            if raw:
                if shown:
                    self.var_app_selected.set(shown[0])
                return
            if shown and cur not in shown:
                self.var_app_selected.set(shown[0])

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
        has_app = self._app_root is not None and self._app_funblock is not None

        btn_refresh = self.btn_app_refresh
        if btn_refresh is not None:
            try:
                btn_refresh.configure(state=("normal" if has_app else "disabled"))
            except Exception:
                pass

        # Creating a new HMI requires a loaded application file (used for the target file name).
        btn_create_hmi = getattr(self, "btn_app_create_hmi", None)
        if btn_create_hmi is not None:
            enabled = has_app and self._app_file_path is not None
            try:
                btn_create_hmi.configure(state=("normal" if enabled else "disabled"))
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

    def _create_hmi_for_this_afb(self, *, sync_from_app_ui: bool = True) -> None:
        """Create a new HMI template for the currently open application (AFB)."""

        if self._app_root is None or self._app_funblock is None or self._app_file_path is None:
            messagebox.showerror("Missing", "Open an application file first.", parent=self)
            return

        if sync_from_app_ui:
            # Commit any open inline editor so current app state is consistent.
            try:
                self._end_app_input_inline_editor(commit=True)
                self._end_app_setting_inline_editor(commit=True)
                self._end_app_output_inline_editor(commit=True)
                self._end_app_conf_inline_editor(commit=True)
                self._end_app_control_inline_editor(commit=True)
            except Exception:
                pass

            # Keep in-memory Application XML aligned with the current UI state so HMI generation
            # (and auto-created menu naming) uses the latest unsaved edits.
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

        base_dir = self._hmi_template_dir()
        base_dir.mkdir(parents=True, exist_ok=True)

        try:
            fname = (Path(self._app_file_path).name or "").strip()
        except Exception:
            fname = ""
        if not fname:
            messagebox.showerror("Missing", "Application file name is missing.", parent=self)
            return
        if not fname.lower().endswith(".xml"):
            fname = f"{fname}.xml"

        target_path = base_dir / fname
        if target_path.exists():
            ok = messagebox.askyesno(
                "Overwrite?",
                f"HMI already exists:\n\n{os.fspath(target_path)}\n\nOverwrite?",
                parent=self,
            )
            if not ok:
                return

        fun_block = self._app_funblock

        # Determine LN instance name for menu naming.
        ln_inst_name = (fun_block.attrib.get("name") or "").strip()
        if not ln_inst_name:
            ln_inst_name = (fun_block.attrib.get("LnRef") or "").strip()
        if not ln_inst_name:
            ln_inst_name = (target_path.stem or "").strip() or "LN"

        top_menu_name = f"Menu_Protection_{ln_inst_name}"
        inputs_menu_name = f"{top_menu_name}_Inputs"
        outputs_menu_name = f"{top_menu_name}_Outputs"
        meas_menu_name = f"{top_menu_name}_Meas"
        settings_menu_name = f"{top_menu_name}_Settings"

        # Peek whether this AFB has outputs/settings (reuse same rules as HMI Refresh).
        ln_ref = (fun_block.attrib.get("LnRef") or "").strip()
        ln_ref_raw = ln_ref
        try:
            ln_ref_norm = self._hmi_normalize_lnref_for_doref(ln_ref_raw) if ln_ref_raw else ""
        except Exception:
            ln_ref_norm = ""

        def _is_true(v: str | None) -> bool:
            vv = (v or "").strip().lower()
            return vv in {"true", "1", "yes", "y", "on"}

        def _src_points_to_current_ln_do(src: str, *, ln_ref_raw0: str, ln_ref0: str) -> bool:
            s = (src or "").strip()
            if not s:
                return False
            if s.startswith("."):
                return True
            if ln_ref_raw0 and s.startswith(f"{ln_ref_raw0}."):
                return True
            if ln_ref0 and s.startswith(f"{ln_ref0}."):
                return True
            return False

        outputs_status: list[tuple[str, str]] = []
        outputs_meas: list[tuple[str, str]] = []
        # (display_name, full_do_ref)
        inputs_for_hmi: list[tuple[str, str]] = []
        settings: list[str] = []
        try:
            seen_input_ln_seq: set[tuple[str, ...]] = set()
            do_types_by_name: dict[str, str] = {}

            try:
                inst_path = self._guess_ln_instance_path_from_lnref(ln_ref_raw) if ln_ref_raw else None
            except Exception:
                inst_path = None
            try:
                if inst_path is not None and inst_path.exists():
                    ln_type_id, _inst_prefix, _inst_ln_class = self._hmi_peek_ln_instance_ln_attrs(inst_path)
                    ln_type_id = (ln_type_id or "").strip()
                    if ln_type_id:
                        info = None
                        if getattr(self, "catalog", None) is not None:
                            for it in (self.catalog.lnode_types or []):
                                if it.id == ln_type_id:
                                    info = it
                                    break
                        if info is None:
                            try:
                                ln_dir = Path(self.iec61850_dir) / "LNodeType"
                                cand = None
                                p0 = ln_dir / f"{ln_type_id}.xml"
                                if p0.is_file():
                                    cand = p0
                                else:
                                    for p in ln_dir.rglob(f"{ln_type_id}.xml"):
                                        if p.is_file():
                                            cand = p
                                            break
                                if cand is not None:
                                    tree = ET.parse(cand)
                                    rr = tree.getroot()
                                    ln = rr.find(f".//{_q(SCL_NS, 'LNodeType')}")
                                    ln_class = (ln.attrib.get("lnClass") or "").strip() if ln is not None else ""
                                    desc = (ln.attrib.get("desc") or "").strip() if ln is not None else ""
                                    info = LNodeTypeInfo(id=ln_type_id, ln_class=ln_class, desc=desc, file_path=cand)
                            except Exception:
                                info = None
                        if info is not None:
                            mdl = load_lnode_type(info)
                            do_types_by_name = {
                                (d.name or "").strip(): (d.do_type or "").strip()
                                for d in (mdl.dos or [])
                                if (d.name or "").strip()
                            }
            except Exception:
                do_types_by_name = {}

            def _extract_inref_purpose(do_ref_text: str) -> str:
                txt = (do_ref_text or "").strip()
                if not txt:
                    return ""
                lo = txt.lower()
                key = "inref%"
                pos = lo.find(key)
                if pos < 0:
                    return ""
                purpose0 = txt[pos + len(key) :]
                return purpose0.strip()

            def _input_ln_sequence(src_text: str) -> tuple[str, ...]:
                """Extract ordered LN list from input src.

                Example:
                "VTBRLN#@InRef%PhVCplx;VTGAPC#@PhVCplx" -> ("VTBRLN#", "VTGAPC#")
                """

                out: list[str] = []
                for part in (src_text or "").split(";"):
                    p = part.strip()
                    if not p:
                        continue
                    ln_name = (p.split("@", 1)[0] or "").strip()
                    if not ln_name:
                        continue
                    out.append(ln_name)
                return tuple(out)

            for el in list(fun_block):
                if not isinstance(el.tag, str):
                    continue
                local = _local_name(el.tag)
                if local == "input":
                    src0 = (el.attrib.get("src") or "").strip()
                    # Rule: only include inputs with non-empty src.
                    if not src0:
                        continue
                    # Rule: dedupe by ordered LN sequence inside src (name/count/order).
                    # If LN sequence is identical, only keep the first input.
                    ln_seq0 = _input_ln_sequence(src0)
                    if ln_seq0 in seen_input_ln_seq:
                        continue
                    seen_input_ln_seq.add(ln_seq0)

                    name0 = (el.attrib.get("name") or "").strip()
                    do_ref_raw0 = (el.attrib.get("doRef") or "").strip()
                    purpose0 = _extract_inref_purpose(do_ref_raw0)
                    if not purpose0:
                        purpose0 = name0
                    if not purpose0:
                        continue

                    if ln_ref:
                        do_ref0 = f"{ln_ref}.InRef%{purpose0}"
                    else:
                        do_ref0 = f"InRef%{purpose0}"

                    display_name = name0 or purpose0
                    inputs_for_hmi.append((display_name, do_ref0))
                elif local == "output":
                    name = (el.attrib.get("name") or "").strip()
                    do_ref = (el.attrib.get("doRef") or "").strip()
                    if _is_true(el.attrib.get("faultlog")):
                        continue
                    if not do_ref:
                        continue
                    try:
                        out_purpose = (el.attrib.get("outPurpose") or "").strip().lower()
                    except Exception:
                        out_purpose = ""
                    if _is_true(el.attrib.get("confpin")) or _is_true(el.attrib.get("conpin")) or out_purpose == "confpin":
                        continue
                    if name:
                        do_name0 = self._hmi_do_name_from_doref(do_ref)
                        cdc0 = ""
                        try:
                            do_type_id0 = (do_types_by_name.get(do_name0) or "").strip()
                            if do_type_id0:
                                cdc0 = (self._hmi_dotype_cdc(do_type_id0) or "").strip().upper()
                        except Exception:
                            cdc0 = ""

                        if cdc0 in {"WYE", "DEL", "SEQ", "CMV", "MV"}:
                            outputs_meas.append((name, do_ref))
                        else:
                            # Status/default bucket (includes ACD/ACT/SPS and unknown CDCs).
                            outputs_status.append((name, do_ref))
                elif local == "setting":
                    name = (el.attrib.get("name") or "").strip()
                    if (el.attrib.get("src") or "").strip().lower() == "confpin":
                        continue
                    src0 = (el.attrib.get("src") or "").strip()
                    if src0 and (not _src_points_to_current_ln_do(src0, ln_ref_raw0=ln_ref_raw, ln_ref0=ln_ref_norm)):
                        continue
                    try:
                        do_from_src = self._hmi_do_name_from_doref(src0)
                    except Exception:
                        do_from_src = ""
                    if not do_from_src or do_from_src.isdigit():
                        continue
                    if name:
                        settings.append(name)
        except Exception:
            outputs = []
            settings = []

        # Ensure some important DOs are present (SetMod/StartMod) like Refresh does.
        try:
            inst_path = self._guess_ln_instance_path_from_lnref(ln_ref_raw) if ln_ref_raw else None
        except Exception:
            inst_path = None
        try:
            if inst_path is not None and inst_path.exists():
                do_names = self._hmi_parse_ln_instance_do_names(inst_path)
                have = {s.strip().lower() for s in (settings or []) if (s or "").strip()}
                for target in ("setmod", "startmod"):
                    nm0 = next((n for n in do_names if (n or "").strip().lower() == target), "")
                    if not nm0:
                        continue
                    if nm0.strip().lower() not in have:
                        settings.append(nm0.strip())
                        have.add(nm0.strip().lower())
        except Exception:
            pass

        # Create new in-memory HMI (same file name as application).
        root = ET.Element(_q(HMI_CUST_NS, "PowerLogicHmiCustomization"))
        root.attrib[_q(XSI_NS, "schemaLocation")] = f"{HMI_CUST_NS} SE_PowerLogic_HmiCustomization.xsd"
        root.attrib["desc"] = "yyy"
        self._hmi_root = root
        self._hmi_file_path = target_path
        self._hmi_saved_sig = None
        try:
            self._hmi_undo_stack = []
        except Exception:
            pass

        menu_top = ET.SubElement(root, _q(HMI_CUST_NS, "HMIMenu"))
        menu_top.attrib["name"] = top_menu_name
        menu_top.attrib["hmiMenuDataType"] = "HMI_MENU_DATA_TYPE_TAB"
        menu_top.attrib["hmiMenuViewType"] = "HMI_MENU_VIEW_TYPE_TABS"
        menu_top.attrib["langRef"] = "0.0"
        # hmiSubTreeType intentionally omitted (must be chosen by user before Save).

        has_out = False
        has_meas = False
        has_set = False
        has_in = False

        if outputs_meas:
            menu_meas = ET.SubElement(root, _q(HMI_CUST_NS, "HMIMenu"))
            menu_meas.attrib["name"] = meas_menu_name
            menu_meas.attrib["hmiMenuDataType"] = "HMI_MENU_DATA_TYPE_LIST"
            menu_meas.attrib["hmiMenuViewType"] = "HMI_MENU_VIEW_TYPE_MEASUREGROUP"
            menu_meas.attrib["langRef"] = "500.41"
            has_meas = True

        if outputs_status:
            menu_out = ET.SubElement(root, _q(HMI_CUST_NS, "HMIMenu"))
            menu_out.attrib["name"] = outputs_menu_name
            menu_out.attrib["hmiMenuDataType"] = "HMI_MENU_DATA_TYPE_LIST"
            menu_out.attrib["hmiMenuViewType"] = "HMI_MENU_VIEW_TYPE_STATUS"
            menu_out.attrib["langRef"] = "500.38"
            has_out = True

        if settings:
            menu_set = ET.SubElement(root, _q(HMI_CUST_NS, "HMIMenu"))
            menu_set.attrib["name"] = settings_menu_name
            menu_set.attrib["hmiMenuDataType"] = "HMI_MENU_DATA_TYPE_LIST"
            menu_set.attrib["hmiMenuViewType"] = "HMI_MENU_VIEW_TYPE_SETTING"
            menu_set.attrib["langRef"] = "500.39"
            has_set = True

        if inputs_for_hmi:
            menu_in = ET.SubElement(root, _q(HMI_CUST_NS, "HMIMenu"))
            menu_in.attrib["name"] = inputs_menu_name
            menu_in.attrib["hmiMenuDataType"] = "HMI_MENU_DATA_TYPE_LIST"
            menu_in.attrib["hmiMenuViewType"] = "HMI_MENU_VIEW_TYPE_INPUT"
            menu_in.attrib["langRef"] = "500.40"
            has_in = True

            for input_name, input_do_ref in inputs_for_hmi:
                it_in = ET.SubElement(menu_in, _q(HMI_CUST_NS, "HMIMenuItem"))
                it_in.attrib["name"] = input_name
                it_in.attrib["doRef"] = input_do_ref
                it_in.attrib["daRef"] = ".setSrcRef"

        # Manual menus for user manual export/use:
        # - Manual_Protection_Outputs: all outputs (status + meas), DO only
        # - Manual_Protection_Inputs: same presentation as IED Inputs
        # - Manual_Protection_Settings: same as IED Settings, but without SettingControl
        manual_outputs_name = "Manual_Protection_Outputs"
        manual_inputs_name = "Manual_Protection_Inputs"
        manual_settings_name = "Manual_Protection_Settings"
        manual_outputs_all = list(outputs_status or []) + list(outputs_meas or [])

        if manual_outputs_all:
            menu_m_out = ET.SubElement(root, _q(HMI_CUST_NS, "HMIMenu"))
            menu_m_out.attrib["name"] = manual_outputs_name
            for out_name, out_do_ref in manual_outputs_all:
                do_full = f"{ln_ref}.{out_do_ref}" if (out_do_ref or "").startswith(".") else (out_do_ref or "")
                if not do_full:
                    continue
                it_m_out = ET.SubElement(menu_m_out, _q(HMI_CUST_NS, "HMIMenuItem"))
                it_m_out.attrib["name"] = (out_name or "").strip() or self._hmi_do_name_from_doref(do_full)
                it_m_out.attrib["doRef"] = do_full

        if inputs_for_hmi:
            menu_m_in = ET.SubElement(root, _q(HMI_CUST_NS, "HMIMenu"))
            menu_m_in.attrib["name"] = manual_inputs_name
            for input_name, input_do_ref in inputs_for_hmi:
                it_m_in = ET.SubElement(menu_m_in, _q(HMI_CUST_NS, "HMIMenuItem"))
                it_m_in.attrib["name"] = input_name
                it_m_in.attrib["doRef"] = input_do_ref
                it_m_in.attrib["daRef"] = ".setSrcRef"

        manual_settings = [nm for nm in (settings or []) if (nm or "").strip().lower() != "settingcontrol"]
        if manual_settings:
            menu_m_set = ET.SubElement(root, _q(HMI_CUST_NS, "HMIMenu"))
            menu_m_set.attrib["name"] = manual_settings_name
            for nm in manual_settings:
                nm0 = (nm or "").strip()
                if not nm0:
                    continue
                it_m_set = ET.SubElement(menu_m_set, _q(HMI_CUST_NS, "HMIMenuItem"))
                it_m_set.attrib["name"] = nm0
                it_m_set.attrib["doRef"] = f"{ln_ref}.{nm0}"

        # IED tab default order: Meas, Output, Setting, Input.
        if has_meas:
            it_ref = ET.SubElement(menu_top, _q(HMI_CUST_NS, "HMIMenuItem"))
            it_ref.attrib["ref"] = meas_menu_name
        if has_out:
            it_ref = ET.SubElement(menu_top, _q(HMI_CUST_NS, "HMIMenuItem"))
            it_ref.attrib["ref"] = outputs_menu_name
        if has_set:
            it_ref = ET.SubElement(menu_top, _q(HMI_CUST_NS, "HMIMenuItem"))
            it_ref.attrib["ref"] = settings_menu_name
        if has_in:
            it_ref = ET.SubElement(menu_top, _q(HMI_CUST_NS, "HMIMenuItem"))
            it_ref.attrib["ref"] = inputs_menu_name

        # Auto-create a default IET menu scaffold when creating HMI from Application.
        # Naming rules:
        #  L1: IET_Protection_<ApplicationName>
        #  L2: <L1>_TAB
        #  L3: <L1>_SECTIONA.._SECTIONE
        #  L4: only under _SECTIONA: <L1>_SECTIONA_Setting1
        # Also add HMIAttr structure (names based on ATeleProt template), but leave values empty.
        try:
            app_name = ""
            if self.instance_editor is not None:
                app_name = (self.instance_editor.var_app_name.get() or "").strip()
            if not app_name:
                app_name = (fun_block.attrib.get("name") or "").strip()
            if not app_name:
                app_name = (target_path.stem or "").strip()
            if not app_name:
                app_name = "AFB"

            l1 = f"IET_Protection_{app_name}"
            l2 = f"{l1}_TAB"
            sections = {k: f"{l1}_SECTION{k}" for k in ("A", "B", "C", "D", "E")}
            l4 = f"{sections['A']}_Setting1"

            def _mk_menu(name: str, *, dt: str = "", vt: str = "") -> ET.Element:
                m = ET.SubElement(root, _q(HMI_CUST_NS, "HMIMenu"))
                m.attrib["name"] = name
                if dt:
                    m.attrib["hmiMenuDataType"] = dt
                if vt:
                    m.attrib["hmiMenuViewType"] = vt
                return m

            def _add_attrs(menu_el: ET.Element, names: list[str], *, values_by_name: dict[str, str] | None = None) -> None:
                values_by_name = values_by_name or {}
                for nm0 in (names or []):
                    nm1 = (nm0 or "").strip()
                    if not nm1:
                        continue
                    a = ET.SubElement(menu_el, _q(HMI_CUST_NS, "HMIAttr"))
                    a.attrib["name"] = nm1
                    a.attrib["value"] = values_by_name.get(nm1, "")

            # Resolve LN prefix/lnClass from the referenced LN instance (preferred),
            # falling back to parsing LnRef (prefix + last-4-char lnClass heuristic).
            inst_prefix = ""
            inst_ln_class = ""
            try:
                lnref_raw = (self._lnref_from_application() or "").strip()
            except Exception:
                lnref_raw = ""
            try:
                inst_path = self._guess_ln_instance_path_from_lnref(lnref_raw) if lnref_raw else None
            except Exception:
                inst_path = None
            if inst_path is not None and inst_path.exists():
                try:
                    _ln_type, inst_prefix, inst_ln_class = self._hmi_peek_ln_instance_ln_attrs(inst_path)
                except Exception:
                    inst_prefix, inst_ln_class = "", ""
            if not (inst_prefix or inst_ln_class):
                try:
                    lnref_norm = self._normalize_lnref(lnref_raw)
                except Exception:
                    lnref_norm = (lnref_raw or "").strip()
                try:
                    guess_class = lnref_norm[-4:] if len(lnref_norm) >= 5 else ""
                    guess_prefix = lnref_norm[: -4] if guess_class else lnref_norm
                    inst_prefix = (guess_prefix or "").strip()
                    inst_ln_class = (guess_class or "").strip()
                except Exception:
                    inst_prefix, inst_ln_class = "", ""

            # Common defaults (apply when the HMIAttr exists on that menu).
            common_attr_defaults: dict[str, str] = {
                "langRef": "0.0",
            }

            # Level 1: TOPIC
            menu_l1 = _mk_menu(l1, dt="IET_MENU_DATA_TYPE_TOPIC")
            menu_l1.attrib["hmiSubTreeType"] = "IET_MENU_SUBTREE_PROTECTION_APPLICATION"
            _add_attrs(menu_l1, ["id", "langRef", "label", "description", "chiptext"], values_by_name=common_attr_defaults)

            # Level 2: TAB
            menu_l2 = _mk_menu(l2, dt="IET_MENU_DATA_TYPE_TAB")
            it = ET.SubElement(menu_l1, _q(HMI_CUST_NS, "HMIMenuItem"))
            it.attrib["ref"] = l2

            # Level 3: SECTION (A..E)
            section_attrs_by_letter: dict[str, list[str]] = {
                # Based on ep7_datamodel/datamodel/hmi_template/application/ATeleProt.xml
                "A": [
                    "id",
                    "langRef",
                    "label",
                    "fc",
                    "dlabel",
                    "prefix",
                    "lnClass",
                    "inst",
                    "sectiontype",
                    "viewtype",
                    "reboot",
                    "caeObjectName",
                    "caeOperationName",
                ],
                "B": [
                    "id",
                    "langRef",
                    "label",
                    "dlabel",
                    "prefix",
                    "lnClass",
                    "inst",
                    "sectiontype",
                    "caeObjectName",
                    "caeOperationName",
                ],
                "C": [
                    "id",
                    "langRef",
                    "label",
                    "dlabel",
                    "prefix",
                    "lnClass",
                    "inst",
                    "sectiontype",
                    "caeObjectName",
                    "caeOperationName",
                ],
                "D": [
                    "id",
                    "langRef",
                    "label",
                    "fc",
                    "dlabel",
                    "prefix",
                    "lnClass",
                    "inst",
                    "sectiontype",
                    "caeObjectName",
                    "caeOperationName",
                ],
                "E": [
                    "id",
                    "langRef",
                    "label",
                    "dlabel",
                    "prefix",
                    "lnClass",
                    "inst",
                    "sectiontype",
                    "reboot",
                    "caeObjectName",
                    "caeOperationName",
                ],
            }

            # User-specified defaults for IET SECTION menus.
            section_id_by_letter: dict[str, str] = {"A": "1", "B": "2", "C": "3", "D": "4", "E": "5"}
            sectiontype_by_letter: dict[str, str] = {
                "A": "setting-section",
                "B": "setting-group-section",
                "C": "setting-section",
                "D": "setting-section",
                "E": "setting-matrix",
            }

            menu_sections: dict[str, ET.Element] = {}
            for k in ("A", "B", "C", "D", "E"):
                nm = sections[k]
                menu_sections[k] = _mk_menu(nm, dt="IET_MENU_DATA_TYPE_SECTION")

                section_defaults: dict[str, str] = dict(common_attr_defaults)
                section_defaults.update(
                    {
                        "id": section_id_by_letter.get(k, ""),
                        "dlabel": "LD/LN.NamPlt.d",
                        "prefix": inst_prefix,
                        "lnClass": inst_ln_class,
                        "inst": "*",
                        "sectiontype": sectiontype_by_letter.get(k, ""),
                        "viewtype": "Routing",
                        "reboot": "true",
                        "caeObjectName": "BAYPROTECTION",
                        "caeOperationName": "editBayProtection",
                    }
                )
                if k in ("A", "D"):
                    section_defaults["fc"] = "configuration"
                _add_attrs(menu_sections[k], section_attrs_by_letter.get(k, []), values_by_name=section_defaults)
                it2 = ET.SubElement(menu_l2, _q(HMI_CUST_NS, "HMIMenuItem"))
                it2.attrib["ref"] = nm

            # Level 4: SETTING PARAMETERS
            menu_l4 = _mk_menu(l4, dt="IET_MENU_DATA_TYPE_SETTING_PARAMETERS", vt="IET_MENU_VIEW_TYPE_SETTING_SECTION")
            setting_defaults: dict[str, str] = dict(common_attr_defaults)
            setting_defaults["order"] = "1"
            _add_attrs(menu_l4, ["order", "langRef", "label", "readonly", "IET_HARDLINK_DEFINITION"], values_by_name=setting_defaults)
            it3 = ET.SubElement(menu_sections["A"], _q(HMI_CUST_NS, "HMIMenuItem"))
            it3.attrib["ref"] = l4
        except Exception:
            pass

        # Switch to HMI tab and show the newly created file.
        try:
            if self.notebook is not None and self.tab_hmi is not None:
                self.notebook.select(self.tab_hmi)
        except Exception:
            pass

        self._refresh_hmi_views(select_first_menu=True)
        self._mark_hmi_unsaved()
        try:
            rel = os.fspath(target_path.relative_to(base_dir))
        except Exception:
            rel = os.fspath(target_path.name)
        self._refresh_hmi_search_list(select_rel=rel)

        # Populate outputs/settings items using the same rules as Refresh.
        try:
            self._hmi_generate_from_application()
        except Exception:
            pass

        # Mark all freshly created content as added so all scopes show green background.
        try:
            self._hmi_mark_added_recursive(self._hmi_root)
            self._refresh_hmi_views(select_first_menu=True, open_selection_path=False)
        except Exception:
            pass

        # For newly created HMI from AFB, default to fully expanded trees
        # in IED/IET/Manual scopes.
        try:
            scope0 = (getattr(self, "_hmi_scope", "ied") or "ied").lower()
            for s in ("ied", "iet", "manual"):
                self._hmi_set_scope(s, refresh=False)
                self._refresh_hmi_views(select_first_menu=True, open_selection_path=False)
                self._hmi_unfold_all()
        except Exception:
            pass
        finally:
            try:
                self._hmi_set_scope(scope0, refresh=False)
            except Exception:
                pass
            try:
                self._update_hmi_tree_action_state()
                self._hmi_update_fold_button_state()
            except Exception:
                pass

        self._set_status(f"Created HMI: {os.fspath(target_path)}")

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
        m_file.add_command(label="Exit", command=self._on_exit)

        menubar.add_cascade(label="File", menu=m_file)

        m_tools = tk.Menu(menubar, tearoff=False)
        m_tools.add_command(label="Open DBMBuilder (Build)", command=self.open_builder_callback)

        menubar.add_cascade(label="Tools", menu=m_tools)

        self.config(menu=menubar)

        bar = ttk.Label(self, textvariable=self.status, anchor="w")
        bar.pack(side="bottom", fill="x")

    def _set_status(self, text: str) -> None:
        self.status.set(text)

    def _ui_state_path(self) -> Path:
        # Prefer %APPDATA% on Windows; fallback to user home.
        try:
            base = os.getenv("APPDATA")
            if base:
                return Path(base) / APP_TITLE / "ui_state.json"
        except Exception:
            pass
        return Path.home() / f".{APP_TITLE.lower()}" / "ui_state.json"

    def _load_ui_state(self) -> dict[str, object]:
        p = self._ui_state_path()
        try:
            if not p.exists():
                return {}
            obj = json.loads(p.read_text(encoding="utf-8"))
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}

    def _schedule_save_ui_state(self) -> None:
        try:
            if self._ui_state_save_after_id is not None:
                self.after_cancel(self._ui_state_save_after_id)
        except Exception:
            pass

        try:
            self._ui_state_save_after_id = self.after(200, self._save_ui_state_now)
        except Exception:
            self._ui_state_save_after_id = None

    def _save_ui_state_now(self) -> None:
        self._ui_state_save_after_id = None
        p = self._ui_state_path()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(p.suffix + ".tmp")
            tmp.write_text(json.dumps(self._ui_state, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(p)
        except Exception:
            pass

    def _on_exit(self) -> None:
        try:
            self._hmi_capture_column_widths()
        except Exception:
            pass
        try:
            self._save_ui_state_now()
        except Exception:
            pass

        try:
            super().destroy()
        except Exception:
            try:
                tk.Tk.destroy(self)
            except Exception:
                pass

    def _hmi_restore_column_widths(self) -> None:
        tv = self._hmi_tv_menus
        if tv is None:
            return
        pref = getattr(self, "_hmi_pref_col_widths", None) or {}
        if not pref:
            return
        try:
            if "#0" in pref:
                tv.column("#0", width=int(pref["#0"]))
            for c in tv["columns"]:
                if c in pref:
                    tv.column(c, width=int(pref[c]))
        except Exception:
            pass

    def _hmi_capture_column_widths(self) -> None:
        tv = self._hmi_tv_menus
        if tv is None:
            return
        widths: dict[str, int] = {}
        try:
            widths["#0"] = int(tv.column("#0").get("width") or 0)
            for c in tv["columns"]:
                widths[c] = int(tv.column(c).get("width") or 0)
        except Exception:
            return
        self._hmi_pref_col_widths = {k: v for k, v in widths.items() if v > 0}
        self._ui_state["hmi_column_widths"] = dict(self._hmi_pref_col_widths)
        self._schedule_save_ui_state()

    def _hmi_on_tree_mouse_release(self, e: tk.Event) -> None:
        tv = self._hmi_tv_menus
        if tv is None:
            return
        try:
            region = tv.identify_region(int(e.x), int(e.y))
            if region == "separator":
                self._hmi_capture_column_widths()
                self._hmi_schedule_column_resize()
                try:
                    self.after(140, self._hmi_capture_column_widths)
                except Exception:
                    pass
                return

            # Single-click dropdown editing for DO/DA refs.
            if region != "cell":
                return
            iid = tv.identify_row(int(e.y))
            if not iid:
                return
            col = tv.identify_column(int(e.x))

            def _col_id_from_ident(col0: str) -> str:
                if col0 == "#0":
                    return "#0"
                try:
                    if not (col0 or "").startswith("#"):
                        return ""
                    idx = int(col0[1:]) - 1
                    cols0 = list(tv["columns"])
                    if 0 <= idx < len(cols0):
                        return str(cols0[idx] or "")
                except Exception:
                    return ""
                return ""

            col_id = _col_id_from_ident(col)

            if col_id == "instantiate":
                # Not instantiate checkbox: only for level-1/2 menus (menu + ref_menu).
                node = self._hmi_tree_iid_to_node.get(iid)
                if node is None:
                    return
                node_type, _parent_el, el = node
                tree_kind = (self._hmi_tree_iid_to_kind.get(iid) or "").strip()
                if node_type != "menu" or el is None:
                    return
                if tree_kind not in {"menu", "ref_menu"}:
                    return

                cur = (el.attrib.get("instantiate") or "").strip().lower()
                has = cur == "false"

                self._hmi_push_undo()
                if has:
                    el.attrib.pop("instantiate", None)
                else:
                    el.attrib["instantiate"] = "false"

                self._hmi_ui_tag_set(el, "changed")

                try:
                    if self._hmi_ui_is_removed(el):
                        tv.item(iid, tags=("removed",))
                    elif self._hmi_ui_is_added(el):
                        tv.item(iid, tags=("added",))
                    elif self._hmi_ui_is_changed(el):
                        tv.item(iid, tags=("changed",))
                    else:
                        tv.item(iid, tags=())
                except Exception:
                    pass

                try:
                    tv.set(iid, "instantiate", "☑" if (not has) else "☐")
                except Exception:
                    pass

                self._mark_hmi_unsaved()
                try:
                    self._update_dirty_ui_hmi()
                except Exception:
                    pass
                return

            if col_id == "hideunit":
                # hideunit checkbox: only on DO rows.
                node = self._hmi_tree_iid_to_node.get(iid)
                if node is None:
                    return
                kind, _parent_el, el = node
                if kind != "item" or el is None:
                    return

                try:
                    opt = (el.attrib.get("attrOption") or "").strip()
                except Exception:
                    opt = ""

                parts = [p for p in re.split(r"[\s,;|]+", opt) if p]
                has = any(p.strip().lower() == "hideunits" for p in parts)

                self._hmi_push_undo()
                if has:
                    parts = [p for p in parts if p.strip().lower() != "hideunits"]
                else:
                    parts.append("hideunits")

                delim = " "
                if "|" in opt:
                    delim = "|"
                elif "," in opt:
                    delim = ","
                elif ";" in opt:
                    delim = ";"

                new_opt = delim.join([p for p in parts if p.strip()])
                if new_opt:
                    el.attrib["attrOption"] = new_opt
                else:
                    el.attrib.pop("attrOption", None)

                self._hmi_ui_tag_set(el, "changed")

                try:
                    if self._hmi_ui_is_removed(el):
                        tv.item(iid, tags=("removed",))
                    elif self._hmi_ui_is_added(el):
                        tv.item(iid, tags=("added",))
                    elif self._hmi_ui_is_changed(el):
                        tv.item(iid, tags=("changed",))
                    else:
                        tv.item(iid, tags=())
                except Exception:
                    pass

                try:
                    tv.set(iid, "hideunit", "☑" if (not has) else "☐")
                except Exception:
                    pass

                self._mark_hmi_unsaved()
                try:
                    self._update_dirty_ui_hmi()
                except Exception:
                    pass
                return

            if col_id not in {"doRef", "daRef"}:
                return

            field = col_id
            if (
                isinstance(getattr(self, "_hmi_edit_cb", None), ttk.Combobox)
                and getattr(self, "_hmi_edit_cb_iid", None) == iid
                and getattr(self, "_hmi_edit_cb_col", None) == field
            ):
                try:
                    self._combobox_toggle_posted(self._hmi_edit_cb)  # type: ignore[arg-type]
                except Exception:
                    pass
                return

            node = self._hmi_tree_iid_to_node.get(iid)
            if node is None:
                return
            kind, _parent_el, _el = node
            if kind == "item":
                pass
            elif kind == "data":
                # Level-4 rows only allow daRef editing.
                if field != "daRef":
                    return
            else:
                return

            try:
                tv.selection_set(iid)
            except Exception:
                pass
            self._hmi_begin_cell_edit(iid, col)
        except Exception:
            pass
