from __future__ import annotations

import os
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass
import hashlib
from pathlib import Path
from tkinter import filedialog, messagebox
from tkinter import ttk

import xml.etree.ElementTree as ET

from iec61850_scanner import LNodeTypeInfo, scan_type_catalog

from ln_instance_scanner import (
    LNInstanceDocument,
    LangRefRef,
    ValueRef,
    compute_signature,
    create_application_file_for_ln_instance,
    create_ln_instance_from_template,
    ensure_all_dai_present_from_template,
    extract_ln_private_refs,
    extract_langref_refs,
    extract_value_refs,
    ensure_ln_private_element,
    get_ln_private_text,
    load_ln_instance_document,
    remove_ln_private_elements,
    save_ln_instance_document,
    update_ln_header,
)


APP_TITLE = "DBMEditor"


_INVALID_FILENAME_CHARS = set('<>:"/\\|?*')
_BLANK_SOURCE_OPTION = "(Blank)"


_PRIV_TYPE_LNNAME = "SchneiderElectric-PowerLogic-LNName"
_PRIV_TYPE_NOMATRIX = "SchneiderElectric-PowerLogic-NoMatrix"
_PRIV_TYPE_PRIVATELN = "SchneiderElectric-PowerLogic-PrivateLN"
_PRIV_TYPE_NAMEANSI = "SchneiderElectric-PowerLogic-NameANSI"
_PRIV_TYPE_PACKAGELN = "SchneiderElectric-PowerLogic-PackageLN"

_MANAGED_PRIV_TYPES: tuple[str, ...] = (
    _PRIV_TYPE_LNNAME,
    _PRIV_TYPE_NAMEANSI,
    _PRIV_TYPE_PACKAGELN,
    _PRIV_TYPE_NOMATRIX,
    _PRIV_TYPE_PRIVATELN,
)


def _sanitize_filename_stem(stem: str) -> str:
    s = (stem or "").strip()
    if not s:
        return ""
    out: list[str] = []
    for ch in s:
        if ch in _INVALID_FILENAME_CHARS:
            out.append("_")
        else:
            out.append(ch)
    s2 = "".join(out).strip().strip(".")
    # Avoid Windows reserved names (very small safeguard)
    if s2.upper() in {"CON", "PRN", "AUX", "NUL", "COM1", "LPT1"}:
        s2 = s2 + "_"
    return s2


def _suggest_instance_filename(prefix: str, ln_class: str) -> str:
    stem = f"{(prefix or '').strip()}{(ln_class or '').strip()}"
    stem = _sanitize_filename_stem(stem)
    return f"{stem}.xml" if stem else ""


def _suggest_application_filename(prefix: str, ln_class: str) -> str:
    stem = f"A{(prefix or '').strip()}{(ln_class or '').strip()}"
    stem = _sanitize_filename_stem(stem)
    return f"{stem}.xml" if stem else ""


def _pick_unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    base = path.stem
    suffix = path.suffix
    parent = path.parent
    for i in range(2, 1000):
        cand = parent / f"{base}_{i}{suffix}"
        if not cand.exists():
            return cand
    return path


def _ensure_backup(path: Path) -> None:
    try:
        bak = path.with_suffix(path.suffix + ".bak")
        if not bak.exists() and path.exists():
            bak.write_bytes(path.read_bytes())
    except Exception:
        # Best-effort only
        return


def _token_match(haystack: str, query: str) -> bool:
    q = (query or "").strip().lower()
    if not q:
        return True
    tokens = [t for t in q.split() if t]
    h = (haystack or "").lower()
    return all(t in h for t in tokens)


class ValueEditDialog(tk.Toplevel):
    # Deprecated: value editing is now done directly in the Values table.
    pass


class _NewInstanceChoiceDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc):
        super().__init__(parent)
        self.title("New LN instance")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self._result: str | None = None

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="Create a new LN instance:").pack(anchor="w")

        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(12, 0))

        ttk.Button(btns, text="Create from template", command=lambda: self._set("template"))\
            .pack(side="top", fill="x")
        ttk.Button(btns, text="Copy existing instance", command=lambda: self._set("copy"))\
            .pack(side="top", fill="x", pady=(8, 0))

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


class _CreateFromTemplateDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        templates: list[LNodeTypeInfo],
        initial_template_id: str = "",
        initial_prefix: str = "",
        initial_inst: str = "0",
        initial_desc: str = "",
    ):
        super().__init__(parent)
        self.title("Create instance from template")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self._templates = list(templates)
        self._template_by_id = {t.id: t for t in self._templates}
        self._all_ids = [t.id for t in self._templates]

        self._result: dict[str, str] | None = None

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text="Template").grid(row=0, column=0, sticky="w", pady=4)
        self.var_filter = tk.StringVar(value="")
        self.var_template = tk.StringVar(value=(initial_template_id or ""))
        self.var_prefix = tk.StringVar(value=(initial_prefix or ""))
        self.var_inst = tk.StringVar(value=(initial_inst or "0"))
        self.var_desc = tk.StringVar(value=(initial_desc or ""))
        self.var_suggested_filename = tk.StringVar(value="")

        filter_row = ttk.Frame(frm)
        filter_row.grid(row=0, column=1, sticky="we", pady=4)
        filter_row.columnconfigure(1, weight=1)
        ttk.Label(filter_row, text="Filter").grid(row=0, column=0, sticky="w")
        ent_filter = ttk.Entry(filter_row, textvariable=self.var_filter)
        ent_filter.grid(row=0, column=1, sticky="we", padx=(8, 0))

        self.cb_template = ttk.Combobox(frm, textvariable=self.var_template, values=self._all_ids, width=72)
        self.cb_template.grid(row=1, column=1, sticky="we", pady=(0, 4))
        ttk.Label(frm, text="").grid(row=1, column=0)  # spacer

        ttk.Label(frm, text="prefix").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=self.var_prefix, width=26).grid(row=2, column=1, sticky="w", pady=4)

        ttk.Label(frm, text="inst").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=self.var_inst, width=26).grid(row=3, column=1, sticky="w", pady=4)

        ttk.Label(frm, text="desc").grid(row=4, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=self.var_desc, width=72).grid(row=4, column=1, sticky="we", pady=4)

        ttk.Label(frm, text="File name").grid(row=5, column=0, sticky="w", pady=4)
        ttk.Label(frm, textvariable=self.var_suggested_filename).grid(row=5, column=1, sticky="w", pady=4)

        self.lbl_hint = ttk.Label(frm, text="(Auto: prefix + lnClass)", foreground="#666")
        self.lbl_hint.grid(row=6, column=0, columnspan=2, sticky="w", pady=(6, 0))

        btns = ttk.Frame(frm)
        btns.grid(row=7, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Cancel", command=self._cancel).pack(side="right")
        ttk.Button(btns, text="Create", command=self._ok).pack(side="right", padx=(0, 8))

        def apply_filter(*_args) -> None:
            raw = (self.var_filter.get() or "").strip().lower()
            if not raw:
                filtered = self._templates
            else:
                tokens = [t for t in raw.split() if t]

                def ok(info: LNodeTypeInfo) -> bool:
                    hay = f"{info.id} {info.ln_class} {info.desc}".lower()
                    return all(t in hay for t in tokens)

                filtered = [x for x in self._templates if ok(x)]

            ids = [x.id for x in filtered]
            cur = (self.var_template.get() or "").strip()
            self.cb_template["values"] = ids[:1500]
            if raw and ids:
                if cur != ids[0]:
                    self.var_template.set(ids[0])
            elif (not raw) and ids and (cur not in ids):
                self.var_template.set(ids[0])

        def sync_filename(*_args) -> None:
            tid = (self.var_template.get() or "").strip()
            info = self._template_by_id.get(tid)
            if info is None:
                return
            suggested = _suggest_instance_filename(self.var_prefix.get(), info.ln_class)
            self.var_suggested_filename.set(suggested)

        self.var_filter.trace_add("write", apply_filter)
        self.var_template.trace_add("write", sync_filename)
        self.var_prefix.trace_add("write", sync_filename)
        apply_filter()
        sync_filename()

        self.cb_template.bind("<Return>", lambda _e: self._ok())
        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Control-f>", lambda _e: ent_filter.focus_set())
        ent_filter.focus_set()

    def _ok(self) -> None:
        tid = (self.var_template.get() or "").strip()
        if not tid:
            messagebox.showerror("Missing", "Template is required", parent=self)
            return
        if tid not in self._template_by_id:
            messagebox.showerror("Invalid", "Template not found in catalog", parent=self)
            return
        info = self._template_by_id.get(tid)
        if info is None:
            messagebox.showerror("Invalid", "Template not found", parent=self)
            return
        filename = _suggest_instance_filename(self.var_prefix.get(), info.ln_class)
        if not filename:
            messagebox.showerror("Missing", "prefix and lnClass must produce a valid filename", parent=self)
            return

        inst = (self.var_inst.get() or "").strip() or "0"
        if not inst.isdigit():
            messagebox.showerror("Invalid", "inst must be digits", parent=self)
            return

        self._result = {
            "template_id": tid,
            "prefix": (self.var_prefix.get() or "").strip(),
            "inst": inst,
            "desc": (self.var_desc.get() or "").strip(),
            "filename": filename,
        }
        self.destroy()

    def _cancel(self) -> None:
        self._result = None
        self.destroy()

    def show(self) -> dict[str, str] | None:
        self.wait_window(self)
        return self._result


class _CopyInstanceDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        instance_relpaths: list[str],
        suggested_filename: str = "",
    ):
        super().__init__(parent)
        self.title("Copy existing instance")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self._relpaths = list(instance_relpaths)
        self._source_values = [_BLANK_SOURCE_OPTION] + self._relpaths
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

        self.var_src = tk.StringVar(value=_BLANK_SOURCE_OPTION)
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
            if raw and filtered:
                if cur != filtered[0]:
                    self.var_src.set(filtered[0])
            elif (not raw) and filtered and (cur not in filtered):
                self.var_src.set(filtered[0])

        self.var_filter.trace_add("write", apply_filter)
        apply_filter()

        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Control-f>", lambda _e: ent_filter.focus_set())
        cb.bind("<Return>", lambda _e: self._ok())

    def _ok(self) -> None:
        src = (self.var_src.get() or "").strip()
        if not src:
            messagebox.showerror("Missing", "Source instance is required", parent=self)
            return
        if (src != _BLANK_SOURCE_OPTION) and (src not in self._relpaths):
            messagebox.showerror("Invalid", "Source instance not found", parent=self)
            return
        filename = (self.var_filename.get() or "").strip()
        if not filename:
            messagebox.showerror("Missing", "File name is required", parent=self)
            return
        if "." not in filename:
            filename = filename + ".xml"
        if any(ch in filename for ch in _INVALID_FILENAME_CHARS):
            messagebox.showerror("Invalid", "File name contains invalid characters", parent=self)
            return
        self._result = {"src": src, "filename": filename}
        self.destroy()

    def _cancel(self) -> None:
        self._result = None
        self.destroy()

    def show(self) -> dict[str, str] | None:
        self.wait_window(self)
        return self._result


@dataclass
class _Row:
    ref: ValueRef


@dataclass
class _LangRow:
    ref: LangRefRef


@dataclass
class _PrivRow:
    private_type: str
    value_text: str
    enabled: bool
    is_custom: bool
    has_nested_xml: bool = False


class LNInstanceEditorFrame(ttk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        workspace_root: Path,
        lndm_dir: Path,
        show_status_bar: bool = False,
        status_callback: Callable[[str], None] | None = None,
        initial_path: Path | None = None,
    ):
        super().__init__(parent)

        self.workspace_root = Path(workspace_root)
        self.lndm_dir = Path(lndm_dir)
        self._show_status_bar = bool(show_status_bar)
        self._status_callback = status_callback

        self.doc: LNInstanceDocument | None = None
        self._rows_all: list[_Row] = []
        self._rows_filtered: list[_Row] = []
        self._lang_rows_all: list[_LangRow] = []
        self._lang_rows_filtered: list[_LangRow] = []
        self._priv_rows: list[_PrivRow] = []
        self._saved_sig: str | None = None
        # Per-table snapshots for row-level highlighting (changed vs last saved).
        self._saved_value_sig_by_path: dict[str, str] | None = None
        self._saved_lang_sig_by_path: dict[str, str] | None = None
        self._saved_priv_sigs: list[str] | None = None
        self._current_ln_index: int = 0

        # Undo (Ctrl+Z): snapshot XML bytes before each committed mutation.
        self._undo_stack: list[bytes] = []
        self._undo_max = 50
        self._undoing: bool = False
        self._undo_suspended: bool = False

        self._edit_entry: ttk.Entry | None = None
        self._edit_iid: str | None = None
        self._edit_col: str | None = None
        self._meta_edit_cb: ttk.Combobox | None = None
        self._meta_edit_iid: str | None = None
        self._meta_edit_col: str | None = None
        self._lang_edit_entry: ttk.Entry | None = None
        self._lang_edit_iid: str | None = None
        self._lang_edit_col: str | None = None
        self._priv_edit_entry: ttk.Entry | None = None
        self._priv_edit_iid: str | None = None
        self._priv_edit_col: str | None = None
        self._tree_menu: tk.Menu | None = None

        self._iid_to_ref: dict[str, ValueRef] = {}
        self._header_key_by_iid: dict[str, str] = {}
        self._collapsed_groups: set[str] = set()

        self._header_iid_by_key: dict[str, str] = {}

        self._lang_iid_to_ref: dict[str, LangRefRef] = {}
        self._lang_header_key_by_iid: dict[str, str] = {}
        self._lang_header_iid_by_key: dict[str, str] = {}
        self._lang_collapsed_groups: set[str] = set()

        self._priv_iid_to_row: dict[str, _PrivRow] = {}
        self._priv_tree_menu: tk.Menu | None = None

        self.var_path = tk.StringVar(value="")
        self.var_instance_filter = tk.StringVar(value="")
        self.var_instance_selected = tk.StringVar(value="")
        # Each instance file contains exactly one LN; we always edit the first found.
        self.var_value_filter = tk.StringVar(value="")
        self.var_lang_filter = tk.StringVar(value="")

        self._all_instance_relpaths: list[str] = []

        self._type_catalog = None

        # Template default maps (for display + right-click Apply)
        self._tpl_default_values: dict[str, str] = {}
        self._tpl_default_langref_ids: dict[str, str] = {}

        # Template-defined row order (for UI display)
        self._tpl_value_order_index: dict[str, int] = {}
        self._tpl_lang_order_index: dict[str, int] = {}

        # Template-defined DOI order (for showing empty DOI headers)
        self._tpl_doi_names: list[str] = []

        self._col_resize_after: str | None = None
        self._priv_col_resize_after: str | None = None

        self.var_lnClass = tk.StringVar(value="")
        self.var_inst = tk.StringVar(value="")
        self.var_prefix = tk.StringVar(value="")
        self.var_lnType = tk.StringVar(value="")
        self.var_app_name = tk.StringVar(value="")
        self.var_app_class = tk.StringVar(value="")
        self.var_app_seqNb = tk.StringVar(value="50")
        self.var_app_LnRef = tk.StringVar(value="")
        self.var_app_desc = tk.StringVar(value="")
        self._app_last_auto_lnref: str = ""
        self._app_last_auto_desc: str = ""

        self._build_ui()

        # No document loaded at startup.
        self._update_doc_dependent_ui()

        self.refresh_instance_list()

        if initial_path is not None:
            self.load_file(initial_path)

    def _build_ui(self) -> None:
        def _bind_undo(w: tk.Misc) -> None:
            try:
                w.bind("<Control-z>", lambda _e: (self.undo(), "break")[1])
                w.bind("<Control-Z>", lambda _e: (self.undo(), "break")[1])
            except Exception:
                pass

        row1 = ttk.Frame(self, padding=(10, 10, 10, 0))
        row1.pack(fill="x")

        btn_new = ttk.Button(row1, text="New", command=self.new_instance)
        btn_new.pack(side="left")
        _bind_undo(btn_new)

        btn_open = ttk.Button(row1, text="Open", command=self.open_dialog)
        btn_open.pack(side="left", padx=(8, 0))
        _bind_undo(btn_open)

        self.btn_save = ttk.Button(row1, text="Save", command=self.save)
        self.btn_save.pack(side="left", padx=(8, 0))
        _bind_undo(self.btn_save)

        btn_save_as = ttk.Button(row1, text="Save As", command=self.save_as)
        btn_save_as.pack(side="left", padx=(8, 0))
        _bind_undo(btn_save_as)

        self.btn_refresh = ttk.Button(row1, text="Refresh", command=self.refresh_from_template)
        self.btn_refresh.pack(side="left", padx=(8, 0))
        _bind_undo(self.btn_refresh)

        self.btn_create_app = ttk.Button(
            row1,
            text="Create application file with this template",
            command=self.create_application_file_with_template,
        )
        self.btn_create_app.pack(side="left", padx=(8, 0))
        _bind_undo(self.btn_create_app)

        row2 = ttk.Frame(self, padding=(10, 8, 10, 0))
        row2.pack(fill="x")

        ttk.Label(row2, text="Search").pack(side="left")
        ent_filter = ttk.Entry(row2, textvariable=self.var_instance_filter, width=28)
        ent_filter.pack(side="left", padx=(8, 0))
        _bind_undo(ent_filter)

        self.cb_instance = ttk.Combobox(
            row2,
            textvariable=self.var_instance_selected,
            values=[],
            width=66,
        )
        self.cb_instance.pack(side="left", padx=(10, 0))
        self.cb_instance.bind("<Return>", lambda _e: self.load_selected_instance())
        _bind_undo(self.cb_instance)
        ttk.Button(row2, text="Load", command=self.load_selected_instance).pack(side="left", padx=(8, 0))

        self.lbl_instance_match = ttk.Label(row2, text="")
        self.lbl_instance_match.pack(side="left", padx=(10, 0))

        self.lbl_meta = ttk.Label(row2, textvariable=self.var_path)
        self.lbl_meta.pack(side="left", padx=(12, 0))

        body = ttk.Frame(self, padding=8)
        body.pack(fill="both", expand=True)

        # LN selection / header
        frm = ttk.LabelFrame(body, text="LN header", padding=8)
        frm.pack(fill="x")
        frm.columnconfigure(1, weight=1)
        frm.columnconfigure(3, weight=1)

        ttk.Label(frm, text="lnClass").grid(row=0, column=0, sticky="w")
        ent_lnClass = ttk.Entry(frm, textvariable=self.var_lnClass)
        ent_lnClass.grid(row=0, column=1, sticky="we", padx=(8, 16))
        _bind_undo(ent_lnClass)

        ttk.Label(frm, text="inst").grid(row=0, column=2, sticky="w")
        ent_inst = ttk.Entry(frm, textvariable=self.var_inst)
        ent_inst.grid(row=0, column=3, sticky="we", padx=(8, 0))
        _bind_undo(ent_inst)

        ttk.Label(frm, text="prefix").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ent_prefix = ttk.Entry(frm, textvariable=self.var_prefix)
        ent_prefix.grid(row=1, column=1, sticky="we", padx=(8, 16), pady=(6, 0))
        _bind_undo(ent_prefix)

        ttk.Label(frm, text="lnType").grid(row=1, column=2, sticky="w", pady=(6, 0))
        ent_lnType = ttk.Entry(frm, textvariable=self.var_lnType)
        ent_lnType.grid(row=1, column=3, sticky="we", padx=(8, 0), pady=(6, 0))
        _bind_undo(ent_lnType)

        # Details notebook
        self.details_nb = ttk.Notebook(body)
        self.details_nb.pack(fill="both", expand=True, pady=(10, 0))
        self.details_nb.bind("<<NotebookTabChanged>>", lambda _e: self._commit_any_edit())
        _bind_undo(self.details_nb)

        tab_values = ttk.Frame(self.details_nb)
        tab_lang = ttk.Frame(self.details_nb)
        tab_priv = ttk.Frame(self.details_nb)
        self.details_nb.add(tab_values, text="Values")
        self.details_nb.add(tab_lang, text="Language reference")
        self.details_nb.add(tab_priv, text="Private")

        # Values tab
        valbox = ttk.LabelFrame(tab_values, text="Values (<Val>)", padding=8)
        valbox.pack(fill="both", expand=True)
        valbox.columnconfigure(0, weight=1)
        valbox.rowconfigure(1, weight=1)

        frow = ttk.Frame(valbox)
        frow.grid(row=0, column=0, sticky="we")
        frow.columnconfigure(1, weight=1)
        ttk.Label(frow, text="Filter").grid(row=0, column=0, sticky="w")
        ent_vfilter = ttk.Entry(frow, textvariable=self.var_value_filter)
        ent_vfilter.grid(row=0, column=1, sticky="we", padx=(8, 0))
        _bind_undo(ent_vfilter)
        ttk.Button(frow, text="Clear", command=self._clear_filter).grid(row=0, column=2, padx=(8, 0))

        self.lbl_value_match = ttk.Label(frow, text="")
        self.lbl_value_match.grid(row=0, column=3, sticky="w", padx=(10, 0))

        self.btn_fold_all = ttk.Button(frow, text="Fold all", command=self.toggle_fold_all)
        self.btn_fold_all.grid(row=0, column=4, sticky="e", padx=(10, 0))

        self.tree = ttk.Treeview(
            valbox,
            columns=("path", "val", "def", "lr", "vk", "vi"),
            show="headings",
            selectmode="browse",
        )
        self.tree.heading("path", text="Path")
        self.tree.heading("val", text="Value")
        self.tree.heading("def", text="Value in template")
        self.tree.heading("lr", text="langRef")
        self.tree.heading("vk", text="valKind")
        self.tree.heading("vi", text="valImport")
        # Widths are set dynamically by proportion to keep the layout readable.
        # Keep the first 3 columns narrower so metadata columns stay visible.
        self.tree.column("path", width=320, minwidth=180, anchor="w", stretch=False)
        self.tree.column("val", width=280, minwidth=160, anchor="w", stretch=False)
        self.tree.column("def", width=240, minwidth=160, anchor="w", stretch=False)
        self.tree.column("lr", width=90, minwidth=80, anchor="w", stretch=False)
        self.tree.column("vk", width=90, minwidth=80, anchor="w", stretch=False)
        self.tree.column("vi", width=100, minwidth=90, anchor="w", stretch=False)
        self.tree.grid(row=1, column=0, sticky="nsew", pady=(8, 0))

        try:
            self.tree.tag_configure("changed", background="lemon chiffon")
        except Exception:
            pass

        try:
            self.tree.tag_configure("added", background="honeydew2")
        except Exception:
            pass

        vsb = ttk.Scrollbar(valbox, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(valbox, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.grid(row=1, column=1, sticky="ns", pady=(8, 0))
        hsb.grid(row=2, column=0, sticky="we")

        self.tree.bind("<Double-1>", self._on_tree_double_click)
        self.tree.bind("<Button-1>", self._on_tree_left_click)
        self.tree.bind("<Button-3>", self._on_tree_right_click)
        self.tree.bind("<Configure>", lambda _e: self._schedule_value_column_resize())
        self.tree.bind("<Control-z>", lambda _e: (self.undo(), "break")[1])
        self.tree.bind("<Control-Z>", lambda _e: (self.undo(), "break")[1])

        # First layout pass after widgets are realized.
        self.after_idle(self._resize_value_columns)

        # Context menu for grouped values (sGroup)
        self._tree_menu = tk.Menu(self, tearoff=0)
        self._tree_menu.add_command(label="Apply to all groups", command=self.apply_to_all_groups)
        self._tree_menu.add_command(label="Apply template value", command=self.apply_template_value_to_selected)

        # Language reference tab
        langbox = ttk.LabelFrame(tab_lang, text="Language reference (<Private type=...LangRef>)", padding=8)
        langbox.pack(fill="both", expand=True)
        langbox.columnconfigure(0, weight=1)
        langbox.rowconfigure(1, weight=1)

        lrow = ttk.Frame(langbox)
        lrow.grid(row=0, column=0, sticky="we")
        lrow.columnconfigure(1, weight=1)
        ttk.Label(lrow, text="Filter").grid(row=0, column=0, sticky="w")
        ent_lfilter = ttk.Entry(lrow, textvariable=self.var_lang_filter)
        ent_lfilter.grid(row=0, column=1, sticky="we", padx=(8, 0))
        _bind_undo(ent_lfilter)
        ttk.Button(lrow, text="Clear", command=self._clear_lang_filter).grid(row=0, column=2, padx=(8, 0))

        self.lbl_lang_match = ttk.Label(lrow, text="")
        self.lbl_lang_match.grid(row=0, column=3, sticky="w", padx=(10, 0))

        self.btn_lang_fold_all = ttk.Button(langbox, text="Fold all", command=self.toggle_lang_fold_all)
        self.btn_lang_fold_all.grid(row=0, column=1, sticky="e")

        self.tree_lang = ttk.Treeview(
            langbox,
            # Keep label text on the far right.
            columns=("path", "id", "def", "text"),
            show="headings",
            selectmode="browse",
        )
        self.tree_lang.heading("path", text="Path")
        self.tree_lang.heading("id", text="LangRef ID")
        self.tree_lang.heading("def", text="Template")
        self.tree_lang.heading("text", text="Label (Val)")
        self.tree_lang.column("path", width=420, anchor="w")
        self.tree_lang.column("id", width=140, anchor="w")
        self.tree_lang.column("def", width=140, anchor="w")
        self.tree_lang.column("text", width=360, anchor="w")
        self.tree_lang.grid(row=1, column=0, sticky="nsew", pady=(8, 0))

        try:
            self.tree_lang.tag_configure("changed", background="lemon chiffon")
        except Exception:
            pass

        lvsb = ttk.Scrollbar(langbox, orient="vertical", command=self.tree_lang.yview)
        lhsb = ttk.Scrollbar(langbox, orient="horizontal", command=self.tree_lang.xview)
        self.tree_lang.configure(yscrollcommand=lvsb.set, xscrollcommand=lhsb.set)
        lvsb.grid(row=1, column=1, sticky="ns", pady=(8, 0))
        lhsb.grid(row=2, column=0, sticky="we")

        self.tree_lang.bind("<Double-1>", self._on_lang_tree_double_click)
        self.tree_lang.bind("<Button-1>", self._on_lang_tree_left_click)
        self.tree_lang.bind("<Button-3>", self._on_lang_tree_right_click)
        self.tree_lang.bind("<Control-z>", lambda _e: (self.undo(), "break")[1])
        self.tree_lang.bind("<Control-Z>", lambda _e: (self.undo(), "break")[1])

        self._lang_tree_menu = tk.Menu(self, tearoff=0)
        self._lang_tree_menu.add_command(label="Apply template ID", command=self.apply_template_langref_to_selected)

        # Private tab (LN-level <Private> before DOI)
        privbox = ttk.LabelFrame(tab_priv, text="Private (LN-level <Private> before <DOI>)", padding=8)
        privbox.pack(fill="both", expand=True)
        privbox.columnconfigure(0, weight=1)
        privbox.rowconfigure(1, weight=1)

        priv_btns = ttk.Frame(privbox)
        priv_btns.grid(row=0, column=0, sticky="we")
        ttk.Button(priv_btns, text="Add", command=self.priv_add).pack(side="left")
        self.btn_priv_delete = ttk.Button(priv_btns, text="Delete", command=self.priv_delete_selected)
        self.btn_priv_delete.pack(side="left", padx=(8, 0))

        self.tree_priv = ttk.Treeview(
            privbox,
            columns=("type", "value", "enabled"),
            show="headings",
            selectmode="browse",
        )
        self.tree_priv.heading("type", text="Private type")
        self.tree_priv.heading("value", text="Value")
        self.tree_priv.heading("enabled", text="Use")
        # Widths are set dynamically; these are safe defaults.
        self.tree_priv.column("type", width=360, minwidth=220, anchor="w", stretch=True)
        self.tree_priv.column("value", width=360, minwidth=220, anchor="w", stretch=True)
        # Keep checkbox column always visible.
        self.tree_priv.column("enabled", width=70, minwidth=70, anchor="center", stretch=False)
        self.tree_priv.grid(row=1, column=0, sticky="nsew", pady=(8, 0))

        try:
            self.tree_priv.tag_configure("changed", background="lemon chiffon")
        except Exception:
            pass

        try:
            self.tree_priv.tag_configure("added", background="honeydew2")
            self.tree_priv.tag_configure("removed", background="misty rose")
        except Exception:
            pass

        pvsb = ttk.Scrollbar(privbox, orient="vertical", command=self.tree_priv.yview)
        phsb = ttk.Scrollbar(privbox, orient="horizontal", command=self.tree_priv.xview)
        self.tree_priv.configure(yscrollcommand=pvsb.set, xscrollcommand=phsb.set)
        pvsb.grid(row=1, column=1, sticky="ns", pady=(8, 0))
        phsb.grid(row=2, column=0, sticky="we")

        self.tree_priv.bind("<Double-1>", self._on_priv_tree_double_click)
        self.tree_priv.bind("<Button-1>", self._on_priv_tree_left_click)
        self.tree_priv.bind("<Button-3>", self._on_priv_tree_right_click)
        self.tree_priv.bind("<space>", lambda _e: self.priv_toggle_selected())
        self.tree_priv.bind("<<TreeviewSelect>>", lambda _e: self._update_priv_delete_button_state())
        self.tree_priv.bind("<Configure>", lambda _e: self._schedule_priv_column_resize())
        self.tree_priv.bind("<Control-z>", lambda _e: (self.undo(), "break")[1])
        self.tree_priv.bind("<Control-Z>", lambda _e: (self.undo(), "break")[1])

        self._priv_tree_menu = tk.Menu(self, tearoff=0)
        self._priv_tree_menu.add_command(label="Add", command=self.priv_add)
        self._priv_tree_menu.add_command(label="Delete", command=self.priv_delete_selected)

        # First layout pass after widgets are realized.
        self.after_idle(self._resize_priv_columns)

        self.status = tk.StringVar(value="")
        if self._show_status_bar:
            ttk.Label(self, textvariable=self.status, anchor="w").pack(side="bottom", fill="x")

        # Track dirty on header edits too
        for v in (self.var_lnClass, self.var_inst, self.var_prefix, self.var_lnType):
            v.trace_add("write", lambda *_args: self._update_dirty_ui())

        def apply_instance_filter(*_args) -> None:
            raw = self.var_instance_filter.get().strip().lower()
            if not raw:
                filtered = list(self._all_instance_relpaths)
            else:
                tokens = [t for t in raw.split() if t]

                def ok(v: str) -> bool:
                    lv = v.lower()
                    return all(t in lv for t in tokens)

                filtered = [p for p in self._all_instance_relpaths if ok(p)]

            cur = (self.var_instance_selected.get() or "").strip()

            max_show = 1200
            shown = filtered[:max_show]
            self.cb_instance["values"] = shown
            if raw and filtered:
                if cur != filtered[0]:
                    self.var_instance_selected.set(filtered[0])
            elif (not raw) and filtered and (cur not in filtered):
                self.var_instance_selected.set(filtered[0])
            suffix = "" if len(filtered) <= max_show else f" (showing first {max_show})"
            self.lbl_instance_match.configure(text=f"{len(filtered)} match{'' if len(filtered)==1 else 'es'}{suffix}")

        self.var_instance_filter.trace_add("write", apply_instance_filter)
        self._apply_instance_filter = apply_instance_filter
        apply_instance_filter()

        # Focus shortcut like template
        self.bind_all("<Control-f>", lambda _e: ent_filter.focus_set())

        # Live filter (no Apply button)
        self.var_value_filter.trace_add("write", lambda *_args: self._apply_filter())
        self.var_lang_filter.trace_add("write", lambda *_args: self._apply_lang_filter())

    def _auto_application_lnref(self) -> str:
        prefix = (self.var_prefix.get() or "").strip()
        ln_class = (self.var_lnClass.get() or "").strip()
        return f"{prefix}{ln_class}#" if (prefix or ln_class) else "#"

    def _sync_application_autofill(self, *, force: bool = False) -> None:
        auto_lnref = self._auto_application_lnref()
        auto_desc = ""
        try:
            if self.doc and self.doc.ln_elements:
                auto_desc = (self.doc.ln_elements[self._current_ln_index].attrib.get("desc") or "").strip()
        except Exception:
            auto_desc = ""

        cur_lnref = (self.var_app_LnRef.get() or "").strip()
        cur_desc = (self.var_app_desc.get() or "")

        if force or (not cur_lnref) or (cur_lnref == (self._app_last_auto_lnref or "")):
            self.var_app_LnRef.set(auto_lnref)
            self._app_last_auto_lnref = auto_lnref
        if force or (not cur_desc.strip()) or (cur_desc == (self._app_last_auto_desc or "")):
            self.var_app_desc.set(auto_desc)
            self._app_last_auto_desc = auto_desc

        suggested = _suggest_application_filename(self.var_prefix.get(), self.var_lnClass.get())
        suggested_stem = (suggested[:-4] if suggested.lower().endswith(".xml") else suggested).strip()
        if suggested_stem:
            if force or not (self.var_app_name.get() or "").strip():
                self.var_app_name.set(suggested_stem)
            if force or not (self.var_app_class.get() or "").strip():
                self.var_app_class.set(suggested_stem)

    def _schedule_value_column_resize(self) -> None:
        try:
            if self._col_resize_after is not None:
                self.after_cancel(self._col_resize_after)
        except Exception:
            pass

        try:
            self._col_resize_after = self.after(50, self._resize_value_columns)
        except Exception:
            self._col_resize_after = None

    def _resize_value_columns(self) -> None:
        # Proportions:
        # - Path: 1 unit
        # - Value: 1 unit
        # - Value in template: 1 unit
        # - langRef: 0.5 unit
        # - valKind: 0.5 unit
        # - valImport: 0.5 unit
        # Total: 4.5 units
        try:
            w = int(self.tree.winfo_width())
        except Exception:
            return
        if w <= 50:
            return

        # Use a small padding to avoid horizontal scrollbar flicker.
        avail = max(0, w - 6)

        mins = {
            "path": 180,
            "val": 160,
            "def": 160,
            "lr": 80,
            "vk": 80,
            "vi": 90,
        }
        min_sum = sum(mins.values())

        # If the widget is too narrow, stick to min widths (hsb will allow scrolling).
        if avail <= min_sum:
            for k, mw in mins.items():
                try:
                    self.tree.column(k, width=mw)
                except Exception:
                    pass
            return

        unit = avail / 4.5
        widths = {
            "path": int(round(unit * 1.0)),
            "val": int(round(unit * 1.0)),
            "def": int(round(unit * 1.0)),
            "lr": int(round(unit * 0.5)),
            "vk": int(round(unit * 0.5)),
            "vi": int(round(unit * 0.5)),
        }
        # Enforce mins.
        for k in widths:
            widths[k] = max(mins[k], int(widths[k]))

        # Fix rounding drift: make sum match avail by adjusting Path.
        drift = avail - sum(widths.values())
        if drift != 0:
            widths["path"] = max(mins["path"], widths["path"] + drift)

        # Apply.
        for k, ww in widths.items():
            try:
                self.tree.column(k, width=int(ww))
            except Exception:
                pass

    def _schedule_priv_column_resize(self) -> None:
        try:
            if self._priv_col_resize_after is not None:
                self.after_cancel(self._priv_col_resize_after)
        except Exception:
            pass

        try:
            self._priv_col_resize_after = self.after(50, self._resize_priv_columns)
        except Exception:
            self._priv_col_resize_after = None

    def _resize_priv_columns(self) -> None:
        if not hasattr(self, "tree_priv"):
            return
        try:
            w = int(self.tree_priv.winfo_width())
        except Exception:
            return
        if w <= 50:
            return

        avail = max(0, w - 6)

        enabled_w = 70
        type_min = 220
        value_min = 220

        # If too narrow, keep checkbox visible and let horizontal scrollbar handle the rest.
        if avail <= enabled_w + type_min + value_min:
            try:
                self.tree_priv.column("enabled", width=enabled_w)
                self.tree_priv.column("type", width=type_min)
                self.tree_priv.column("value", width=value_min)
            except Exception:
                pass
            return

        rem = avail - enabled_w
        type_w = int(round(rem * 0.55))
        value_w = rem - type_w

        type_w = max(type_min, type_w)
        value_w = max(value_min, value_w)

        # Fix rounding drift by adjusting Value.
        drift = avail - (enabled_w + type_w + value_w)
        if drift != 0:
            value_w = max(value_min, value_w + drift)

        try:
            self.tree_priv.column("enabled", width=enabled_w)
            self.tree_priv.column("type", width=type_w)
            self.tree_priv.column("value", width=value_w)
        except Exception:
            pass

    def _update_priv_delete_button_state(self) -> None:
        if not hasattr(self, "tree_priv") or not hasattr(self, "btn_priv_delete"):
            return
        can_delete = False
        try:
            sel = self.tree_priv.selection()
            if sel and (sel[0] in self._priv_iid_to_row):
                row = self._priv_iid_to_row[sel[0]]
                can_delete = bool(row.is_custom) and (not bool(getattr(row, "__ui_deleted", False)))
        except Exception:
            can_delete = False
        try:
            self.btn_priv_delete.configure(state=("normal" if can_delete else "disabled"))
        except Exception:
            pass

    def bind_shortcuts_to(self, widget: tk.Misc) -> None:
        widget.bind("<Control-o>", lambda _e: self.open_dialog())
        widget.bind("<Control-O>", lambda _e: self.open_dialog())
        widget.bind("<Control-s>", lambda _e: self.save())
        widget.bind("<Control-S>", lambda _e: self.save())
        widget.bind("<Control-Shift-s>", lambda _e: self.save_as())
        widget.bind("<Control-Shift-S>", lambda _e: self.save_as())
        widget.bind("<Control-z>", lambda _e: (self.undo(), "break")[1])
        widget.bind("<Control-Z>", lambda _e: (self.undo(), "break")[1])
        widget.bind("<F2>", lambda _e: self.start_edit_selected())

    def new_instance(self) -> None:
        if self.is_dirty():
            if not messagebox.askyesno("Unsaved", "Discard unsaved changes before creating a new instance?", parent=self):
                return

        dlg = _NewInstanceChoiceDialog(self.winfo_toplevel())
        choice = dlg.show()
        if not choice:
            return
        if choice == "template":
            self.create_instance_from_template_dialog()
        elif choice == "copy":
            self.copy_existing_instance_dialog()

    def _ensure_type_catalog(self):
        if self._type_catalog is not None:
            return
        iec61850_dir = self.workspace_root / "ep7_datamodel" / "datamodel" / "iec61850"
        if not iec61850_dir.exists():
            raise FileNotFoundError(f"IEC61850 folder not found: {iec61850_dir}")
        self._type_catalog = scan_type_catalog(iec61850_dir)

    def create_instance_from_template_dialog(self, *, template_id: str = "") -> None:
        try:
            self._ensure_type_catalog()
        except Exception as e:
            messagebox.showerror("Scan failed", str(e), parent=self)
            return

        templates = list(getattr(self._type_catalog, "lnode_types", []) or [])
        if not templates:
            messagebox.showerror("Missing", "No LNodeType templates found in catalog.", parent=self)
            return

        dlg = _CreateFromTemplateDialog(self.winfo_toplevel(), templates=templates, initial_template_id=template_id)
        res = dlg.show()
        if not res:
            return

        tid = res["template_id"]
        info = next((x for x in templates if x.id == tid), None)
        if info is None:
            messagebox.showerror("Invalid", "Template not found", parent=self)
            return

        target_path = self.lndm_dir / res["filename"]
        target_path = _pick_unique_path(target_path)

        try:
            from iec61850_scanner import load_lnode_type

            model = load_lnode_type(info)
            iec61850_dir = self.workspace_root / "ep7_datamodel" / "datamodel" / "iec61850"
            doc = create_ln_instance_from_template(
                iec61850_dir=iec61850_dir,
                template=model,
                target_path=target_path,
                prefix=res.get("prefix", ""),
                inst=res.get("inst", "0"),
                ln_desc=res.get("desc", ""),
            )
            save_ln_instance_document(doc, target_path=target_path, make_backup=False)
        except Exception as e:
            messagebox.showerror("Create failed", str(e), parent=self)
            return

        self.refresh_instance_list()
        self.load_file(target_path)

    def create_instance_with_template_model(self, model) -> None:
        # Called from LN template page: template is already loaded.
        try:
            tid = getattr(getattr(model, "info", None), "id", "") or ""
        except Exception:
            tid = ""

        # Reuse the same dialog but restrict list to this one template.
        try:
            info = getattr(model, "info", None)
            if not isinstance(info, LNodeTypeInfo):
                raise ValueError("Invalid template model")
        except Exception as e:
            messagebox.showerror("Invalid", str(e), parent=self)
            return

        templates = [info]
        dlg = _CreateFromTemplateDialog(self.winfo_toplevel(), templates=templates, initial_template_id=tid)
        # Disable template selection UI (fixed)
        try:
            dlg.cb_template.configure(state="disabled")
            dlg.var_filter.set("")
        except Exception:
            pass

        res = dlg.show()
        if not res:
            return

        target_path = self.lndm_dir / res["filename"]
        target_path = _pick_unique_path(target_path)

        try:
            iec61850_dir = self.workspace_root / "ep7_datamodel" / "datamodel" / "iec61850"
            doc = create_ln_instance_from_template(
                iec61850_dir=iec61850_dir,
                template=model,
                target_path=target_path,
                prefix=res.get("prefix", ""),
                inst=res.get("inst", "0"),
                ln_desc=res.get("desc", ""),
            )
            save_ln_instance_document(doc, target_path=target_path, make_backup=False)
        except Exception as e:
            messagebox.showerror("Create failed", str(e), parent=self)
            return

        self.refresh_instance_list()
        self.load_file(target_path)

    def copy_existing_instance_dialog(self) -> None:
        self.refresh_instance_list()
        suggested = "copy.xml"
        if self.doc is not None:
            suggested = _sanitize_filename_stem(self.doc.file_path.stem + "_copy") + self.doc.file_path.suffix

        dlg = _CopyInstanceDialog(self.winfo_toplevel(), instance_relpaths=self._all_instance_relpaths, suggested_filename=suggested)
        res = dlg.show()
        if not res:
            return

        target_path = self.lndm_dir / res["filename"]
        target_path = _pick_unique_path(target_path)

        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            if res["src"] == _BLANK_SOURCE_OPTION:
                ns = "http://www.iec.ch/61850/2003/SCL"

                def q(name: str) -> str:
                    return f"{{{ns}}}{name}"

                ln = ET.Element(q("LN"))
                ln.attrib["prefix"] = ""
                ln.attrib["lnClass"] = "LLN0"
                ln.attrib["inst"] = "0"
                tree = ET.ElementTree(ln)
                doc = LNInstanceDocument(file_path=target_path, tree=tree, ns=ns, ln_elements=[ln])
                save_ln_instance_document(doc, target_path=target_path, make_backup=False)
            else:
                src_path = self.lndm_dir / res["src"]
                target_path.write_bytes(src_path.read_bytes())
        except Exception as e:
            messagebox.showerror("Copy failed", str(e), parent=self)
            return

        self.refresh_instance_list()
        self.load_file(target_path)

    def open_dialog(self, *, initialdir: Path | None = None) -> None:
        path = filedialog.askopenfilename(
            parent=self.winfo_toplevel(),
            title="Open LN instance file",
            initialdir=(os.fspath(initialdir) if initialdir else os.fspath(self.lndm_dir)),
            filetypes=[("XML", "*.xml"), ("All", "*")],
        )
        if not path:
            return
        self.load_file(Path(path))

    def refresh_instance_list(self) -> None:
        if not self.lndm_dir.exists():
            self._all_instance_relpaths = []
            try:
                self._apply_instance_filter()
            except Exception:
                pass
            return

        # Recursive scan but keep UI responsive
        rels: list[str] = []
        try:
            for p in self.lndm_dir.rglob("*.xml"):
                try:
                    rels.append(os.fspath(p.relative_to(self.lndm_dir)))
                except Exception:
                    rels.append(os.fspath(p))
                if len(rels) >= 5000:
                    break
        except Exception:
            # Fallback to non-recursive
            rels = [os.fspath(p.name) for p in self.lndm_dir.glob("*.xml")]

        rels.sort(key=lambda s: s.lower())
        self._all_instance_relpaths = rels
        try:
            self._apply_instance_filter()
        except Exception:
            self.cb_instance["values"] = rels

    def load_selected_instance(self) -> None:
        raw = (self.var_instance_selected.get() or "").strip()
        if not raw:
            return
        if self.is_dirty() and not messagebox.askyesno("Unsaved", "Discard unsaved changes?", parent=self.winfo_toplevel()):
            return

        p = (self.lndm_dir / raw)
        if not p.exists():
            # Allow absolute
            p2 = Path(raw)
            if p2.exists():
                p = p2
            else:
                messagebox.showerror("Not found", f"File not found:\n\n{raw}", parent=self.winfo_toplevel())
                return
        self.load_file(p)

    def load_file(self, path: Path) -> None:
        try:
            doc = load_ln_instance_document(path)
        except Exception as e:
            messagebox.showerror("Open failed", str(e), parent=self)
            return

        # On open/load: show only what is in the file.
        # Template-defined defaults are still computed for the "Value in template" column + right-click Apply.
        # Missing DOI/DAI placeholders are ONLY hydrated when the user clicks Refresh.
        self._tpl_default_values = {}
        self._tpl_default_langref_ids = {}
        self._tpl_value_order_index = {}
        self._tpl_lang_order_index = {}
        self._tpl_doi_names = []
        try:
            ln0 = doc.ln_elements[0]
            ln_type_id = (ln0.attrib.get("lnType") or "").strip()
            if ln_type_id:
                self._ensure_type_catalog()
                templates = list(getattr(self._type_catalog, "lnode_types", []) or [])
                info = next((x for x in templates if x.id == ln_type_id), None)
                if info is not None:
                    from iec61850_scanner import load_lnode_type

                    model = load_lnode_type(info)
                    iec61850_dir = self.workspace_root / "ep7_datamodel" / "datamodel" / "iec61850"

                    # Build template order index (uses skeleton placeholder doc, preserves DO order)
                    try:
                        odoc = create_ln_instance_from_template(
                            iec61850_dir=iec61850_dir,
                            template=model,
                            target_path=Path("__template_order__.xml"),
                            prefix="",
                            inst="0",
                            ln_desc="",
                        )

                        # Keep template DOI order so we can display empty DOI headers like Str/Op.
                        try:
                            ln_tpl = odoc.ln_elements[0]
                            tpl_dois: list[str] = []
                            for ch in list(ln_tpl):
                                if not isinstance(ch.tag, str):
                                    continue
                                tag = ch.tag
                                if tag.startswith("{"):
                                    tag = tag.split("}", 1)[1]
                                if tag != "DOI":
                                    continue
                                nm = (ch.attrib.get("name") or "").strip()
                                if nm:
                                    tpl_dois.append(nm)
                            self._tpl_doi_names = tpl_dois
                        except Exception:
                            self._tpl_doi_names = []

                        def _base(p: str) -> str:
                            return (p or "").split("/Val:", 1)[0]

                        # Index full paths (preferred) so Struct members keep correct relative order.
                        # Also index the base (without /Val:sGroup=...) as a fallback.
                        for i, r in enumerate(extract_value_refs(odoc, 0, sort=False)):
                            if r.path and r.path not in self._tpl_value_order_index:
                                self._tpl_value_order_index[r.path] = i
                            b = _base(r.path)
                            if b and b not in self._tpl_value_order_index:
                                self._tpl_value_order_index[b] = i

                        for i, lr in enumerate(extract_langref_refs(odoc, 0, sort=False)):
                            if lr.path not in self._tpl_lang_order_index:
                                self._tpl_lang_order_index[lr.path] = i
                    except Exception:
                        pass

                    # Build template-default maps (only type-provided values / IDs, no placeholders)
                    try:
                        from ln_instance_scanner import create_ln_instance_from_template

                        tdoc = create_ln_instance_from_template(
                            iec61850_dir=iec61850_dir,
                            template=model,
                            target_path=Path("__template_defaults__.xml"),
                            prefix="",
                            inst="0",
                            ln_desc="",
                            include_type_langref_ids=True,
                            copy_d_val_from_type=True,
                            create_empty_val_for_edit=False,
                        )
                        for r in extract_value_refs(tdoc, 0):
                            v = (r.get_value_text() or "")
                            # Keep even if empty: if the template defines an explicit empty Val, Apply should clear.
                            self._tpl_default_values[r.path] = v
                        for lr in extract_langref_refs(tdoc, 0):
                            self._tpl_default_langref_ids[lr.path] = lr.get_private_text()
                    except Exception:
                        pass
        except Exception:
            # Never block opening; worst case is fewer placeholders shown.
            pass

        self.doc = doc
        self._undo_stack = []
        self._undoing = False
        self.var_path.set(os.fspath(path))

        # During initial UI population, header variable traces may call _update_dirty_ui(),
        # which calls is_dirty() and _apply_header_to_doc(). If _saved_sig still reflects
        # the previous document, that can write stale header values into the newly loaded
        # document (and the UI may appear not to update). Clear _saved_sig to disable
        # is_dirty() writeback until after the UI is populated.
        self._saved_sig = None

        # Always edit the first LN element found.
        self._current_ln_index = 0
        if len(doc.ln_elements) > 1:
            self._set_status(
                f"Loaded: {os.fspath(path)} (warning: {len(doc.ln_elements)} LN elements found; editing the first one)"
            )

        self._load_current_ln_into_ui()

        # Normalize header writeback once after load so we don't become dirty on open.
        self._apply_header_to_doc()
        self._saved_sig = compute_signature(doc)
        self.mark_saved()
        self._update_doc_dependent_ui()
        self._update_dirty_ui()
        if self.status.get().strip() == "":
            self._set_status(f"Loaded: {os.fspath(path)}")

        # Keep selector in sync if under lndm_dir
        try:
            rel = os.fspath(Path(path).resolve().relative_to(self.lndm_dir.resolve()))
            self.var_instance_selected.set(rel)
        except Exception:
            self.var_instance_selected.set(os.fspath(path))

    def reload(self) -> None:
        if not self.doc:
            return
        if self.is_dirty():
            if not messagebox.askyesno("Discard changes?", "Reload will discard unsaved changes. Continue?", parent=self):
                return
        self.load_file(self.doc.file_path)

    def _try_load_template_model_for_current_ln(self):
        if not self.doc:
            return None

        idx = getattr(self, "_current_ln_index", 0) or 0
        if idx < 0 or idx >= len(self.doc.ln_elements):
            idx = 0

        try:
            ln = self.doc.ln_elements[idx]
        except Exception:
            return None

        ln_type_id = (ln.attrib.get("lnType") or "").strip()
        if not ln_type_id:
            return None

        try:
            self._ensure_type_catalog()
            templates = list(getattr(self._type_catalog, "lnode_types", []) or [])
            info = next((x for x in templates if x.id == ln_type_id), None)
            if info is None:
                return None

            from iec61850_scanner import load_lnode_type

            model = load_lnode_type(info)
            iec61850_dir = self.workspace_root / "ep7_datamodel" / "datamodel" / "iec61850"
            return (iec61850_dir, model)
        except Exception:
            return None

    def refresh_from_template(self) -> None:
        if not self.doc:
            return

        got = self._try_load_template_model_for_current_ln()
        if not got:
            self._set_status("Refresh: template not found (lnType missing or not in catalog)")
            return

        iec61850_dir, model = got
        try:
            # Commit any in-progress edits, but keep Refresh as a single undo step.
            try:
                self._undo_suspended = True
                self._commit_any_edit()
            finally:
                self._undo_suspended = False

            idx = getattr(self, "_current_ln_index", 0) or 0

            snap0 = self._doc_xml_bytes()
            n0 = len(self._undo_stack)
            self._push_undo()
            pushed = len(self._undo_stack) != n0

            ensure_all_dai_present_from_template(
                self.doc,
                idx,
                iec61850_dir=iec61850_dir,
                template=model,
                copy_dai_metadata=False,
                reorder_doi=True,
            )

            # If refresh produced no XML change, drop the undo snapshot.
            try:
                if pushed and snap0 and self._doc_xml_bytes() == snap0:
                    self._undo_stack.pop()
            except Exception:
                pass

            self._load_current_ln_into_ui()
            self._update_dirty_ui()
            self._set_status("Refreshed from LN template")
        except Exception as e:
            try:
                # Avoid leaving a useless undo entry if refresh failed.
                if 'pushed' in locals() and pushed:
                    self._undo_stack.pop()
            except Exception:
                pass
            messagebox.showerror("Refresh failed", str(e), parent=self)

    def refresh_from_template_model(self, model) -> None:
        """Refresh the loaded instance using a known template model.

        Used by the LN template editor when the template structure changes.
        """
        if not self.doc or model is None:
            return

        try:
            # Commit any in-progress edits, but keep Refresh as a single undo step.
            try:
                self._undo_suspended = True
                self._commit_any_edit()
            finally:
                self._undo_suspended = False

            idx = getattr(self, "_current_ln_index", 0) or 0
            if idx < 0 or idx >= len(self.doc.ln_elements):
                idx = 0

            # Safety: only apply if lnType matches.
            ln_type_id = (self.doc.ln_elements[idx].attrib.get("lnType") or "").strip()
            try:
                model_id = (getattr(getattr(model, "info", None), "id", "") or "").strip()
            except Exception:
                model_id = ""
            if ln_type_id and model_id and ln_type_id != model_id:
                return

            iec61850_dir = self.workspace_root / "ep7_datamodel" / "datamodel" / "iec61850"

            snap0 = self._doc_xml_bytes()
            n0 = len(self._undo_stack)
            self._push_undo()
            pushed = len(self._undo_stack) != n0

            ensure_all_dai_present_from_template(
                self.doc,
                idx,
                iec61850_dir=iec61850_dir,
                template=model,
                copy_dai_metadata=False,
                reorder_doi=True,
            )

            # If refresh produced no XML change, drop the undo snapshot.
            try:
                if pushed and snap0 and self._doc_xml_bytes() == snap0:
                    self._undo_stack.pop()
            except Exception:
                pass

            self._load_current_ln_into_ui()
            self._update_dirty_ui()
            self._set_status("Refreshed from LN template")
        except Exception as e:
            try:
                try:
                    # Avoid leaving a useless undo entry if refresh failed.
                    if 'pushed' in locals() and pushed:
                        self._undo_stack.pop()
                except Exception:
                    pass
                self._set_status(f"Refresh from template failed: {e}")
            except Exception:
                pass

    def _load_current_ln_into_ui(self) -> None:
        if not self.doc:
            return
        idx = self._current_ln_index
        if idx < 0 or idx >= len(self.doc.ln_elements):
            idx = 0
            self._current_ln_index = 0

        ln = self.doc.ln_elements[idx]
        self.var_lnClass.set((ln.attrib.get("lnClass") or "").strip())
        self.var_inst.set((ln.attrib.get("inst") or "").strip())
        self.var_prefix.set((ln.attrib.get("prefix") or "").strip())
        self.var_lnType.set((ln.attrib.get("lnType") or "").strip())

        self._rows_all = []
        self._lang_rows_all = []
        self._priv_rows = []
        try:
            refs = extract_value_refs(self.doc, idx, sort=False)
        except Exception as e:
            self._set_status(f"Extract values failed: {e}")
            refs = []

        # Sort by template DO order when possible.
        try:
            if self._tpl_value_order_index:
                def _base(p: str) -> str:
                    return (p or "").split("/Val:", 1)[0]

                refs = sorted(
                    list(refs),
                    key=lambda r: (
                        self._tpl_value_order_index.get(r.path, self._tpl_value_order_index.get(_base(r.path), 10**9)),
                        r.path,
                    ),
                )
        except Exception:
            pass

        for r in refs:
            self._rows_all.append(_Row(ref=r))

        try:
            lrefs = extract_langref_refs(self.doc, idx, sort=False)
        except Exception as e:
            self._set_status(f"Extract language refs failed: {e}")
            lrefs = []

        try:
            if self._tpl_lang_order_index:
                lrefs = sorted(
                    list(lrefs),
                    key=lambda lr: (
                        self._tpl_lang_order_index.get(lr.path, 10**9),
                        lr.path,
                    ),
                )
        except Exception:
            pass
        for lr in lrefs:
            self._lang_rows_all.append(_LangRow(ref=lr))

        self._apply_filter()
        self._apply_lang_filter()

        self._load_priv_rows_from_doc()
        self._render_priv_tree()

    def _langref_id_for_value_path(self, value_path: str) -> str:
        base = (value_path or "").split("/Val:", 1)[0]
        try:
            for row in (self._lang_rows_all or []):
                if (row.ref.path or "") == base:
                    return (row.ref.get_private_text() or "").strip()
        except Exception:
            return ""
        return ""

    def _set_item_tag(self, tree: ttk.Treeview, iid: str, tag: str, on: bool) -> None:
        try:
            tags = list(tree.item(iid, "tags") or ())
            tags = [t for t in tags if t != tag]
            if on:
                tags.append(tag)
            tree.item(iid, tags=tuple(tags))
        except Exception:
            return

    def _value_row_sig(self, ref: ValueRef) -> str:
        v = ref.get_value_text()
        vk = (ref.dai_element.attrib.get("valKind") or "").strip()
        vi = (ref.dai_element.attrib.get("valImport") or "").strip().lower()
        raw = "\x1f".join((ref.path or "", v or "", vk, vi))
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def _lang_row_sig(self, ref: LangRefRef) -> str:
        raw = "\x1f".join(
            (
                ref.path or "",
                ref.private_type or "",
                ref.get_private_text() or "",
                ref.get_label_text() or "",
            )
        )
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def _priv_row_sig(self, row: _PrivRow) -> str:
        raw = "\x1f".join(
            (
                row.private_type or "",
                row.value_text or "",
                "1" if row.enabled else "0",
                "1" if row.is_custom else "0",
                "1" if row.has_nested_xml else "0",
            )
        )
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def _value_row_is_changed(self, ref: ValueRef) -> bool:
        if self._saved_value_sig_by_path is None:
            return False
        saved = self._saved_value_sig_by_path.get(ref.path)
        if saved is None:
            return True
        return self._value_row_sig(ref) != saved

    def _value_row_change_kind(self, ref: ValueRef) -> str:
        """Return 'same' | 'changed' | 'added' relative to the last saved baseline."""
        if self._saved_value_sig_by_path is None:
            return "same"
        saved = self._saved_value_sig_by_path.get(ref.path)
        if saved is None:
            return "added"
        return "changed" if (self._value_row_sig(ref) != saved) else "same"

    def _lang_row_is_changed(self, ref: LangRefRef) -> bool:
        if self._saved_lang_sig_by_path is None:
            return False
        saved = self._saved_lang_sig_by_path.get(ref.path)
        if saved is None:
            return True
        return self._lang_row_sig(ref) != saved

    def _priv_row_is_changed(self, iid: str, row: _PrivRow) -> bool:
        if self._saved_priv_sigs is None:
            return False
        try:
            idx = int((iid or "").split("_", 1)[1])
        except Exception:
            return False
        if idx < 0:
            return False
        if idx >= len(self._saved_priv_sigs):
            return True
        return self._priv_row_sig(row) != self._saved_priv_sigs[idx]

    def _priv_row_tags(self, iid: str, row: _PrivRow) -> tuple[str, ...]:
        # Precedence: removed > added > changed
        if bool(getattr(row, "__ui_deleted", False)):
            return ("removed",)

        if self._saved_priv_sigs is not None:
            try:
                idx = int((iid or "").split("_", 1)[1])
            except Exception:
                idx = -1
            if idx >= len(self._saved_priv_sigs):
                return ("added",)

        if self._priv_row_is_changed(iid, row):
            return ("changed",)

        return ()

    def _reapply_changed_tags_values(self) -> None:
        if not hasattr(self, "tree"):
            return

        def _segments(p: str) -> list[str]:
            return [s for s in (p or "").split("/") if s]

        def _is_hdr(seg: str) -> bool:
            return seg.startswith("DOI:") or seg.startswith("SDI:")

        def _header_keys_for_path(path: str) -> list[str]:
            keys: list[str] = []
            acc: list[str] = []
            for seg in _segments(path):
                if _is_hdr(seg):
                    acc.append(seg)
                    keys.append("/".join(acc))
            return keys

        header_added: dict[str, bool] = {k: False for k in (self._header_iid_by_key or {})}
        header_changed: dict[str, bool] = {k: False for k in (self._header_iid_by_key or {})}

        # Compute header highlights from the *data*, not only visible leaf rows.
        # When a DOI/SDI group is folded, its DAI rows are not rendered, but the
        # parent header should still reflect that something inside changed/added.
        try:
            for row in list(getattr(self, "_rows_filtered", []) or []):
                ref = row.ref
                kind = self._value_row_change_kind(ref)
                if kind == "same":
                    continue
                for hk in _header_keys_for_path(ref.path):
                    if hk not in header_changed:
                        continue
                    if kind == "added":
                        header_added[hk] = True
                    else:
                        header_changed[hk] = True
        except Exception:
            pass

        # Leaf rows: highlight only rows that are currently visible.
        for iid, ref in list(self._iid_to_ref.items()):
            kind = self._value_row_change_kind(ref)
            is_added = kind == "added"
            is_changed = kind == "changed"
            self._set_item_tag(self.tree, iid, "added", is_added)
            self._set_item_tag(self.tree, iid, "changed", (is_changed and (not is_added)))

        for hk in header_changed.keys():
            iid = self._header_iid_by_key.get(hk)
            if not iid:
                continue
            is_added = bool(header_added.get(hk, False))
            is_changed = bool(header_changed.get(hk, False)) and (not is_added)
            self._set_item_tag(self.tree, iid, "added", is_added)
            self._set_item_tag(self.tree, iid, "changed", is_changed)

    def _reapply_changed_tags_lang(self) -> None:
        if not hasattr(self, "tree_lang"):
            return
        for iid, ref in list(self._lang_iid_to_ref.items()):
            self._set_item_tag(self.tree_lang, iid, "changed", self._lang_row_is_changed(ref))

    def _reapply_changed_tags_priv(self) -> None:
        if not hasattr(self, "tree_priv"):
            return
        for iid, row in list(self._priv_iid_to_row.items()):
            try:
                self.tree_priv.item(iid, tags=self._priv_row_tags(iid, row))
            except Exception:
                pass

    def mark_saved(self) -> None:
        # Snapshot the current UI/doc state as the new baseline (clear all "changed" highlights).
        try:
            self._saved_value_sig_by_path = {r.ref.path: self._value_row_sig(r.ref) for r in self._rows_all}
        except Exception:
            self._saved_value_sig_by_path = None

        try:
            self._saved_lang_sig_by_path = {r.ref.path: self._lang_row_sig(r.ref) for r in self._lang_rows_all}
        except Exception:
            self._saved_lang_sig_by_path = None

        try:
            kept: list[_PrivRow] = []
            for r in (self._priv_rows or []):
                if bool(getattr(r, "__ui_deleted", False)):
                    continue
                # Clear UI-only flags at save boundary.
                try:
                    if hasattr(r, "__ui_deleted"):
                        delattr(r, "__ui_deleted")
                except Exception:
                    pass
                kept.append(r)
            self._priv_rows = kept
            self._saved_priv_sigs = [self._priv_row_sig(r) for r in self._priv_rows]
        except Exception:
            self._saved_priv_sigs = None

        self._reapply_changed_tags_values()
        self._reapply_changed_tags_lang()
        self._render_priv_tree()

    def _apply_filter(self) -> None:
        q = self.var_value_filter.get().strip()
        if not q:
            self._rows_filtered = list(self._rows_all)
        else:
            out: list[_Row] = []
            for row in self._rows_all:
                # Filter should search DOI/DAI names (path) only, not values.
                hay = f"{row.ref.path}"
                if _token_match(hay, q):
                    out.append(row)
            self._rows_filtered = out

        try:
            self.lbl_value_match.configure(
                text=f"{len(self._rows_filtered)} match{'' if len(self._rows_filtered)==1 else 'es'}"
            )
        except Exception:
            pass

        self._update_fold_all_button()

        self._render_tree()

    def _clear_filter(self) -> None:
        self.var_value_filter.set("")
        # _apply_filter is triggered by trace

    def _clear_lang_filter(self) -> None:
        self.var_lang_filter.set("")
        # _apply_lang_filter is triggered by trace

    def _apply_lang_filter(self) -> None:
        q = self.var_lang_filter.get().strip()
        if not q:
            self._lang_rows_filtered = list(self._lang_rows_all)
        else:
            out: list[_LangRow] = []
            for row in self._lang_rows_all:
                g, l = row.ref.get_group_label()
                hay = f"{row.ref.path} {g} {l} {row.ref.get_label_text()}"
                if _token_match(hay, q):
                    out.append(row)
            self._lang_rows_filtered = out

        try:
            self.lbl_lang_match.configure(
                text=f"{len(self._lang_rows_filtered)} match{'' if len(self._lang_rows_filtered)==1 else 'es'}"
            )
        except Exception:
            pass

        self._update_lang_fold_all_button()
        self._render_lang_tree()

    def _commit_any_edit(self) -> None:
        self._end_cell_edit(commit=True)
        self._end_meta_edit(commit=True)
        self._end_lang_cell_edit(commit=True)
        self._end_priv_cell_edit(commit=True)

    def _doc_xml_bytes(self) -> bytes:
        if not self.doc:
            return b""
        try:
            root = self.doc.tree.getroot()
            return ET.tostring(root, encoding="utf-8", short_empty_elements=True)
        except Exception:
            try:
                root = self.doc.tree.getroot()
                return (ET.tostring(root, encoding="unicode") or "").encode("utf-8", errors="ignore")
            except Exception:
                return b""

    def _doc_from_xml_bytes(self, xml_bytes: bytes, *, file_path: Path) -> LNInstanceDocument:
        root = ET.fromstring(xml_bytes)
        tree = ET.ElementTree(root)
        ns = root.tag.split("}", 1)[0][1:] if isinstance(root.tag, str) and root.tag.startswith("{") else ""

        def q(name: str) -> str:
            return f"{{{ns}}}{name}" if ns else name

        ln_elements: list[ET.Element] = []
        root_ln = root.tag.split("}", 1)[1] if isinstance(root.tag, str) and root.tag.startswith("{") else root.tag
        if root_ln in {"LN", "LN0", "LNode"}:
            ln_elements.append(root)
        else:
            ln_elements.extend(list(root.iter(q("LN"))))
            if not ln_elements:
                ln_elements.extend(list(root.iter(q("LN0"))))
            if not ln_elements:
                ln_elements.extend(list(root.iter(q("LNode"))))
        if not ln_elements:
            raise ValueError("No LN/LN0/LNode element found")

        return LNInstanceDocument(file_path=file_path, tree=tree, ns=ns, ln_elements=ln_elements)

    def _push_undo(self) -> None:
        if self._undoing or self._undo_suspended:
            return
        if not self.doc:
            return
        snap = self._doc_xml_bytes()
        if not snap:
            return
        self._undo_stack.append(snap)
        if len(self._undo_stack) > self._undo_max:
            self._undo_stack = self._undo_stack[-self._undo_max :]

    def undo(self) -> None:
        if not self._undo_stack or not self.doc:
            return

        # Cancel inline edits to avoid committing stale values after undo.
        try:
            self._end_cell_edit(commit=False)
            self._end_meta_edit(commit=False)
            self._end_lang_cell_edit(commit=False)
            self._end_priv_cell_edit(commit=False)
        except Exception:
            pass

        snap = self._undo_stack.pop()
        self._undoing = True
        try:
            restored = self._doc_from_xml_bytes(snap, file_path=self.doc.file_path)
            self.doc = restored
            self._current_ln_index = 0
            self._load_current_ln_into_ui()
            self._update_dirty_ui()
        except Exception:
            # If restore fails, do not lose the snapshot.
            self._undo_stack.append(snap)
        finally:
            self._undoing = False

    def _default_lnname_text(self) -> str:
        prefix = (self.var_prefix.get() or "").strip()
        ln_class = (self.var_lnClass.get() or "").strip()
        return f"{prefix}{ln_class}#" if (prefix or ln_class) else "#"

    def _priv_checkbox_text(self, enabled: bool) -> str:
        return "☑" if enabled else "☐"

    def _is_priv_type_editable(self, row: _PrivRow) -> bool:
        return bool(row.is_custom)

    def _is_priv_value_editable(self, row: _PrivRow) -> bool:
        if row.has_nested_xml:
            return False
        if row.is_custom:
            return True
        return row.private_type in {_PRIV_TYPE_LNNAME, _PRIV_TYPE_NAMEANSI}

    def _load_priv_rows_from_doc(self) -> None:
        if not self.doc:
            return
        idx = self._current_ln_index

        managed_set = set(_MANAGED_PRIV_TYPES)
        out: list[_PrivRow] = []

        # Managed rows (always visible)
        for t in _MANAGED_PRIV_TYPES:
            txt = get_ln_private_text(self.doc, idx, t, before_first_doi_only=True)
            enabled = txt is not None
            val = ""
            if t == _PRIV_TYPE_LNNAME:
                val = (txt or "").strip() if txt is not None else self._default_lnname_text()
            elif t == _PRIV_TYPE_NAMEANSI:
                val = (txt or "").strip() if txt is not None else ""
            else:
                val = (txt or "").strip() if txt is not None else ""
            out.append(_PrivRow(private_type=t, value_text=val, enabled=bool(enabled), is_custom=False))

        # Existing custom rows (from file)
        try:
            refs = extract_ln_private_refs(self.doc, idx, before_first_doi_only=True)
        except Exception:
            refs = []

        seen_custom: set[str] = set()
        for r in refs:
            t = (r.get_type() or "").strip()
            if not t or t in managed_set:
                continue
            if t in seen_custom:
                continue
            seen_custom.add(t)
            out.append(
                _PrivRow(
                    private_type=t,
                    value_text=r.get_compact_content(),
                    enabled=True,
                    is_custom=True,
                    has_nested_xml=bool(r.has_child_elements()),
                )
            )

        self._priv_rows = out

    def _render_priv_tree(self) -> None:
        if not hasattr(self, "tree_priv"):
            return
        try:
            self.tree_priv.delete(*self.tree_priv.get_children())
        except Exception:
            return

        self._priv_iid_to_row.clear()
        for i, row in enumerate(self._priv_rows):
            iid = f"p_{i:05d}"
            try:
                self.tree_priv.insert(
                    "",
                    "end",
                    iid=iid,
                    values=(
                        row.private_type,
                        row.value_text,
                        self._priv_checkbox_text(row.enabled),
                    ),
                    tags=self._priv_row_tags(iid, row),
                )
                self._priv_iid_to_row[iid] = row
            except Exception:
                pass

            self._update_priv_delete_button_state()

        self._reapply_changed_tags_priv()

    def _apply_priv_row_to_doc(self, row: _PrivRow, *, old_type: str | None = None) -> None:
        if not self.doc:
            return
        idx = self._current_ln_index

        t = (row.private_type or "").strip()
        if old_type is not None and (old_type or "").strip() and (old_type or "").strip() != t:
            remove_ln_private_elements(self.doc, idx, (old_type or "").strip())

        if not t:
            return

        if row.enabled:
            # Normalize: keep a single node per type.
            remove_ln_private_elements(self.doc, idx, t)
            ensure_ln_private_element(self.doc, idx, t, text=(row.value_text or ""))
        else:
            remove_ln_private_elements(self.doc, idx, t)

    def _on_priv_tree_left_click(self, event: tk.Event) -> None:
        # Commit any in-progress edit first.
        self._end_priv_cell_edit(commit=True)

        try:
            region = self.tree_priv.identify("region", event.x, event.y)
            if region != "cell":
                return
            col = self.tree_priv.identify_column(event.x)
            iid = self.tree_priv.identify_row(event.y)
            if not iid:
                return
            self.tree_priv.selection_set(iid)
            if col != "#3":
                return
            row = self._priv_iid_to_row.get(iid)
            if row is None:
                return
            if bool(getattr(row, "__ui_deleted", False)):
                return
            self._push_undo()
            row.enabled = not row.enabled
            self._apply_priv_row_to_doc(row)
            try:
                self.tree_priv.item(iid, values=(row.private_type, row.value_text, self._priv_checkbox_text(row.enabled)))
            except Exception:
                pass
            self._update_dirty_ui()
            try:
                self.tree_priv.item(iid, tags=self._priv_row_tags(iid, row))
            except Exception:
                pass
        except Exception:
            return

    def priv_toggle_selected(self) -> None:
        if not hasattr(self, "tree_priv"):
            return
        sel = self.tree_priv.selection()
        if not sel:
            return
        iid = sel[0]
        row = self._priv_iid_to_row.get(iid)
        if row is None:
            return
        if bool(getattr(row, "__ui_deleted", False)):
            return
        self._push_undo()
        row.enabled = not row.enabled
        self._apply_priv_row_to_doc(row)
        try:
            self.tree_priv.item(iid, values=(row.private_type, row.value_text, self._priv_checkbox_text(row.enabled)))
        except Exception:
            pass
        self._update_dirty_ui()
        try:
            self.tree_priv.item(iid, tags=self._priv_row_tags(iid, row))
        except Exception:
            pass

    def priv_add(self) -> None:
        # Adds a new custom private row; user will edit the type/value.
        self._end_priv_cell_edit(commit=True)
        self._push_undo()
        self._priv_rows.append(_PrivRow(private_type="", value_text="", enabled=True, is_custom=True))
        self._render_priv_tree()
        try:
            iid = f"p_{(len(self._priv_rows)-1):05d}"
            self.tree_priv.selection_set(iid)
            self.tree_priv.see(iid)
            self._begin_priv_cell_edit(iid, "type")
        except Exception:
            pass

    def priv_delete_selected(self) -> None:
        self._end_priv_cell_edit(commit=True)
        if not hasattr(self, "tree_priv"):
            return
        sel = self.tree_priv.selection()
        if not sel:
            return
        iid = sel[0]
        row = self._priv_iid_to_row.get(iid)
        if row is None:
            return
        if not row.is_custom:
            self._set_status("Managed Private rows cannot be deleted (uncheck to remove from file).")
            return

        # Determine whether this row is newly added (not yet saved).
        is_added = False
        try:
            idx = int((iid or "").split("_", 1)[1])
        except Exception:
            idx = -1
        try:
            if self._saved_priv_sigs is not None and idx >= len(self._saved_priv_sigs):
                is_added = True
        except Exception:
            is_added = False

        # Deleting a custom private row is always undoable (even if it doesn't map to file XML).
        self._push_undo()
        try:
            t = (row.private_type or "").strip()
            if t and self.doc:
                remove_ln_private_elements(self.doc, self._current_ln_index, t)
        except Exception:
            pass

        # Added-then-deleted before save: cancel the addition (no red removed state).
        if is_added:
            try:
                if 0 <= idx < len(self._priv_rows):
                    self._priv_rows.pop(idx)
            except Exception:
                pass
            self._render_priv_tree()
            self._update_dirty_ui()
            return

        # Soft-delete: keep visible until save.
        try:
            setattr(row, "__ui_deleted", True)
        except Exception:
            pass

        self._render_priv_tree()
        self._update_dirty_ui()

    def _on_priv_tree_right_click(self, event: tk.Event) -> None:
        if self._priv_tree_menu is None:
            return
        self._end_priv_cell_edit(commit=True)
        try:
            iid = self.tree_priv.identify_row(event.y)
            if iid:
                self.tree_priv.selection_set(iid)
            # Disable Delete when selection is managed.
            can_delete = False
            try:
                sel = self.tree_priv.selection()
                if sel and (sel[0] in self._priv_iid_to_row):
                    row = self._priv_iid_to_row[sel[0]]
                    can_delete = bool(row.is_custom) and (not bool(getattr(row, "__ui_deleted", False)))
            except Exception:
                can_delete = False
            try:
                self._priv_tree_menu.entryconfigure(1, state=("normal" if can_delete else "disabled"))
            except Exception:
                pass
            self._priv_tree_menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                self._priv_tree_menu.grab_release()
            except Exception:
                pass

    def _on_priv_tree_double_click(self, event: tk.Event) -> None:
        try:
            region = self.tree_priv.identify("region", event.x, event.y)
            if region != "cell":
                return
            col = self.tree_priv.identify_column(event.x)
            iid = self.tree_priv.identify_row(event.y)
            if not iid or iid not in self._priv_iid_to_row:
                return
            self.tree_priv.selection_set(iid)
            if bool(getattr(self._priv_iid_to_row.get(iid), "__ui_deleted", False)):
                return
            column_id = "type" if col == "#1" else ("value" if col == "#2" else "enabled")
            if column_id == "enabled":
                self.priv_toggle_selected()
                return
            self._begin_priv_cell_edit(iid, column_id)
        except Exception:
            return

    def _begin_priv_cell_edit(self, iid: str, column_id: str) -> None:
        row = self._priv_iid_to_row.get(iid)
        if row is None:
            return
        if bool(getattr(row, "__ui_deleted", False)):
            return

        self._end_priv_cell_edit(commit=False)
        self._end_lang_cell_edit(commit=True)
        self._end_cell_edit(commit=True)

        if column_id == "type" and not self._is_priv_type_editable(row):
            return
        if column_id == "value" and not self._is_priv_value_editable(row):
            if row.has_nested_xml:
                self._set_status("This <Private> contains nested XML; value editing is disabled.")
            return

        bbox = self.tree_priv.bbox(iid, column=column_id)
        if not bbox:
            return
        x, y, w, h = bbox

        value_text = row.private_type if column_id == "type" else row.value_text

        ent = ttk.Entry(self.tree_priv)
        ent.place(x=x, y=y, width=w, height=h)
        ent.insert(0, value_text)
        ent.selection_range(0, "end")
        ent.focus_set()

        ent.bind("<Return>", lambda _e: self._end_priv_cell_edit(commit=True))
        ent.bind("<Escape>", lambda _e: self._end_priv_cell_edit(commit=False))
        ent.bind("<FocusOut>", lambda _e: self._end_priv_cell_edit(commit=True))
        ent.bind("<Control-z>", lambda _e: (self.undo(), "break")[1])
        ent.bind("<Control-Z>", lambda _e: (self.undo(), "break")[1])

        self._priv_edit_entry = ent
        self._priv_edit_iid = iid
        self._priv_edit_col = column_id

    def _end_priv_cell_edit(self, *, commit: bool) -> None:
        if self._priv_edit_entry is None or self._priv_edit_iid is None or self._priv_edit_col is None:
            return

        ent = self._priv_edit_entry
        iid = self._priv_edit_iid
        col = self._priv_edit_col
        self._priv_edit_entry = None
        self._priv_edit_iid = None
        self._priv_edit_col = None

        new_text = (ent.get() or "")
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

        row = self._priv_iid_to_row.get(iid)
        if row is None:
            return

        old_type: str | None = None

        if col == "type":
            if not self._is_priv_type_editable(row):
                return
            old_type = (row.private_type or "").strip()
            new_type = (new_text or "").strip()
            if old_type == new_type:
                return
            self._push_undo()
            row.private_type = new_type
            # Prevent custom rows from shadowing managed ones.
            if row.private_type in set(_MANAGED_PRIV_TYPES):
                self._set_status("This private type is managed; edit the managed row instead.")
                row.private_type = old_type
                return
        elif col == "value":
            if not self._is_priv_value_editable(row):
                return
            new_value = (new_text or "")
            if (row.value_text or "") == new_value:
                return
            self._push_undo()
            row.value_text = new_value

        if row.enabled:
            self._apply_priv_row_to_doc(row, old_type=old_type)

        try:
            self.tree_priv.item(
                iid,
                values=(row.private_type, row.value_text, self._priv_checkbox_text(row.enabled)),
            )
        except Exception:
            pass

        self._update_dirty_ui()
        self._set_item_tag(self.tree_priv, iid, "changed", self._priv_row_is_changed(iid, row))

    def _render_lang_tree(
        self,
        *,
        anchor_kind: str | None = None,
        anchor_key: str | None = None,
        anchor_offset: int | None = None,
    ) -> None:
        sel = self.tree_lang.selection()
        sel_iid = sel[0] if sel else None

        top_anchor_kind: str | None = None
        top_anchor_key: str | None = None
        if anchor_kind and anchor_key:
            top_anchor_kind = anchor_kind
            top_anchor_key = anchor_key
        else:
            try:
                top_iid = self.tree_lang.identify_row(0)
                if top_iid:
                    if top_iid in self._lang_header_key_by_iid:
                        top_anchor_kind = "h"
                        top_anchor_key = self._lang_header_key_by_iid.get(top_iid)
                    elif top_iid in self._lang_iid_to_ref:
                        top_anchor_kind = "v"
                        top_anchor_key = self._lang_iid_to_ref[top_iid].path
            except Exception:
                pass

        self.tree_lang.delete(*self.tree_lang.get_children())
        self._lang_iid_to_ref.clear()
        self._lang_header_key_by_iid.clear()
        self._lang_header_iid_by_key.clear()

        def _iid_for_header(key: str) -> str:
            h = hashlib.sha1((key or "").encode("utf-8")).hexdigest()[:12]
            return f"lh_{h}"

        def _iid_for_value(path: str) -> str:
            h = hashlib.sha1((path or "").encode("utf-8")).hexdigest()[:12]
            return f"lv_{h}"

        def _segments(p: str) -> list[str]:
            return [s for s in (p or "").split("/") if s]

        def _is_hdr(seg: str) -> bool:
            return seg.startswith("DOI:") or seg.startswith("SDI:")

        class _Node:
            __slots__ = ("seg", "key", "depth", "children", "child_map", "leaves")

            def __init__(self, *, seg: str, key: str, depth: int):
                self.seg = seg
                self.key = key
                self.depth = depth
                self.children: list[_Node] = []
                self.child_map: dict[str, _Node] = {}
                self.leaves: list[tuple[str, LangRefRef]] = []

            def get_child(self, seg: str, key: str) -> "_Node":
                if key in self.child_map:
                    return self.child_map[key]
                n = _Node(seg=seg, key=key, depth=self.depth + 1)
                self.child_map[key] = n
                self.children.append(n)
                return n

        root = _Node(seg="", key="", depth=-1)

        def _ln(tag: str) -> str:
            if tag.startswith("{"):
                return tag.split("}", 1)[1]
            return tag

        # Ensure DOI/SDI headers exist even when there are no DAI rows under them.
        # (Otherwise empty DOI containers disappear from the table.)
        try:
            # If a filter is active, keep behavior focused on matching value rows.
            if not (self.var_value_filter.get().strip()):
                # Prefer template order so template-only DOIs still appear.
                for nm in list(getattr(self, "_tpl_doi_names", []) or []):
                    nm2 = (nm or "").strip()
                    if not nm2:
                        continue
                    seg0 = f"DOI:{nm2}"
                    root.get_child(seg0, seg0)

                # Pre-create nested SDI groups from template path structure.
                try:
                    for p in (getattr(self, "_tpl_lang_order_index", {}) or {}).keys():
                        node0 = root
                        key_parts0: list[str] = []
                        for seg0 in _segments(p):
                            if not _is_hdr(seg0):
                                continue
                            key_parts0.append(seg0)
                            key0 = "/".join(key_parts0)
                            node0 = node0.get_child(seg0, key0)
                except Exception:
                    pass

                if self.doc is not None:
                    idx0 = getattr(self, "_current_ln_index", 0) or 0
                    if idx0 < 0 or idx0 >= len(self.doc.ln_elements):
                        idx0 = 0
                    ln_el = self.doc.ln_elements[idx0]

                    def _walk_sdi(parent_el, parent_key_parts: list[str], parent_node: _Node) -> None:
                        for ch in list(parent_el):
                            if not isinstance(ch.tag, str):
                                continue
                            if _ln(ch.tag) != "SDI":
                                continue
                            nm = (ch.attrib.get("name") or "").strip()
                            if not nm:
                                continue
                            seg = f"SDI:{nm}"
                            key_parts2 = parent_key_parts + [seg]
                            key2 = "/".join(key_parts2)
                            n2 = parent_node.get_child(seg, key2)
                            _walk_sdi(ch, key_parts2, n2)

                    for ch in list(ln_el):
                        if not isinstance(ch.tag, str):
                            continue
                        if _ln(ch.tag) != "DOI":
                            continue
                        nm = (ch.attrib.get("name") or "").strip()
                        if not nm:
                            continue
                        seg = f"DOI:{nm}"
                        key_parts = [seg]
                        key = seg
                        n = root.get_child(seg, key)
                        _walk_sdi(ch, key_parts, n)
        except Exception:
            pass

        for row in self._lang_rows_filtered:
            parts = _segments(row.ref.path)
            node = root
            key_parts: list[str] = []
            last_hdr_idx = -1
            for idx, seg in enumerate(parts):
                if _is_hdr(seg):
                    key_parts.append(seg)
                    key = "/".join(key_parts)
                    node = node.get_child(seg, key)
                    last_hdr_idx = idx

            rest = "/".join(parts[last_hdr_idx + 1 :]) if last_hdr_idx >= 0 else row.ref.path
            node.leaves.append((rest, row.ref))

        def _render_node(node: _Node) -> None:
            if node.depth >= 0:
                indent = "    " * node.depth
                collapsed = node.key in self._lang_collapsed_groups
                symbol = "▸" if collapsed else "▾"
                iid = _iid_for_header(node.key)
                self._lang_header_key_by_iid[iid] = node.key
                self._lang_header_iid_by_key[node.key] = iid
                self.tree_lang.insert("", "end", iid=iid, values=(f"{indent}{symbol} {node.seg}", "", "", ""))
                if collapsed:
                    return

            for rest, ref in node.leaves:
                leaf_indent = "    " * (node.depth + 1)
                disp_path = f"{leaf_indent}{rest}" if rest else leaf_indent
                cur_id = ref.get_private_text()
                txt = ref.get_label_text()
                def_id = self._tpl_default_langref_ids.get(ref.path, "")
                iid = _iid_for_value(ref.path)
                self._lang_iid_to_ref[iid] = ref
                self.tree_lang.insert("", "end", iid=iid, values=(disp_path, cur_id, def_id, txt))

            for ch in node.children:
                _render_node(ch)

        for ch in root.children:
            _render_node(ch)

        try:
            if sel_iid and self.tree_lang.exists(sel_iid):
                self.tree_lang.selection_set(sel_iid)
        except Exception:
            pass
        if not self.tree_lang.selection():
            for iid in self.tree_lang.get_children():
                if iid in self._lang_iid_to_ref:
                    self.tree_lang.selection_set(iid)
                    break

        try:
            anchor_iid: str | None = None
            if top_anchor_kind == "h" and top_anchor_key:
                anchor_iid = self._lang_header_iid_by_key.get(top_anchor_key)
            elif top_anchor_kind == "v" and top_anchor_key:
                anchor_iid = _iid_for_value(top_anchor_key)

            if anchor_iid and self.tree_lang.exists(anchor_iid):
                items = list(self.tree_lang.get_children())
                idx = items.index(anchor_iid)
                top_idx = idx
                if anchor_offset is not None:
                    top_idx = max(0, min(idx - int(anchor_offset), max(0, len(items) - 1)))
                denom = max(1, len(items))
                self.tree_lang.yview_moveto(top_idx / denom)
        except Exception:
            pass

        self._end_lang_cell_edit(commit=False)
        self._update_lang_fold_all_button()
        self._reapply_changed_tags_lang()

    def _lang_group_keys_in_filtered(self) -> set[str]:
        keys: set[str] = set()

        def _segments(p: str) -> list[str]:
            return [s for s in (p or "").split("/") if s]

        for row in self._lang_rows_filtered:
            acc: list[str] = []
            for seg in _segments(row.ref.path):
                if seg.startswith("DOI:") or seg.startswith("SDI:"):
                    acc.append(seg)
                    keys.add("/".join(acc))
        return keys

    def _update_lang_fold_all_button(self) -> None:
        if not hasattr(self, "btn_lang_fold_all"):
            return
        keys = self._lang_group_keys_in_filtered()
        if not keys:
            try:
                self.btn_lang_fold_all.configure(text="Fold all", state="disabled")
            except Exception:
                pass
            return

        all_collapsed = keys.issubset(self._lang_collapsed_groups)
        try:
            self.btn_lang_fold_all.configure(text=("Unfold all" if all_collapsed else "Fold all"), state="normal")
        except Exception:
            pass

    def toggle_lang_fold_all(self) -> None:
        self._commit_any_edit()
        keys = self._lang_group_keys_in_filtered()
        if not keys:
            return
        if keys.issubset(self._lang_collapsed_groups):
            self._lang_collapsed_groups.difference_update(keys)
        else:
            self._lang_collapsed_groups.update(keys)
        self._render_lang_tree()

    def _on_lang_tree_left_click(self, event: tk.Event) -> str | None:
        try:
            region = self.tree_lang.identify("region", event.x, event.y)
            if region != "cell":
                return None
            col = self.tree_lang.identify_column(event.x)
            if col != "#1":
                return None
            iid = self.tree_lang.identify_row(event.y)
            if not iid:
                return None
            if iid not in self._lang_header_key_by_iid:
                return None

            self._commit_any_edit()
            self.tree_lang.selection_set(iid)

            # Capture current pixel position of the clicked row so we can restore it.
            old_y = 0
            row_h = 1
            try:
                bb0 = self.tree_lang.bbox(iid, column="path")
                if bb0:
                    _x, y0, _w, h0 = bb0
                    old_y = int(y0)
                    row_h = max(1, int(h0))
            except Exception:
                old_y = 0
                row_h = 1

            key = self._lang_header_key_by_iid.get(iid)
            if not key:
                return "break"

            # Preserve row offset of clicked header within the viewport.
            anchor_offset = 0
            try:
                items0 = list(self.tree_lang.get_children())
                top0 = self.tree_lang.identify_row(0)
                if top0 and top0 in items0 and iid in items0:
                    anchor_offset = items0.index(iid) - items0.index(top0)
            except Exception:
                anchor_offset = 0

            if key in self._lang_collapsed_groups:
                self._lang_collapsed_groups.remove(key)
            else:
                self._lang_collapsed_groups.add(key)
            # Anchor on the clicked header to avoid scroll jumping.
            self._render_lang_tree(anchor_kind="h", anchor_key=key)

            # After rebuild, adjust scroll so the clicked row stays at the same pixel Y.
            try:
                iid2 = self._lang_header_iid_by_key.get(key)
                if iid2 and self.tree_lang.exists(iid2):
                    self.tree_lang.see(iid2)
                    bb1 = self.tree_lang.bbox(iid2, column="path")
                    if bb1:
                        _x, y1, _w, _h = bb1
                        delta_units = int(round((int(y1) - old_y) / float(row_h)))
                        if delta_units:
                            self.tree_lang.yview_scroll(delta_units, "units")
            except Exception:
                pass

            return "break"
        except Exception:
            return None

    def _on_lang_tree_double_click(self, event: tk.Event) -> None:
        try:
            region = self.tree_lang.identify("region", event.x, event.y)
            if region != "cell":
                return
            col = self.tree_lang.identify_column(event.x)
            if col != "#2":
                return
            iid = self.tree_lang.identify_row(event.y)
            if not iid:
                return
            if iid not in self._lang_iid_to_ref:
                return
            self.tree_lang.selection_set(iid)
            self._begin_lang_cell_edit(iid, col)
        except Exception:
            return

    def _begin_lang_cell_edit(self, iid: str, col: str) -> None:
        ref = self._lang_iid_to_ref.get(iid)
        if ref is None:
            return

        self._end_lang_cell_edit(commit=False)
        self._end_cell_edit(commit=True)

        column_id = "id"
        bbox = self.tree_lang.bbox(iid, column=column_id)
        if not bbox:
            return
        x, y, w, h = bbox

        value_text = ref.get_private_text()

        ent = ttk.Entry(self.tree_lang)
        ent.place(x=x, y=y, width=w, height=h)
        ent.insert(0, value_text)
        ent.selection_range(0, "end")
        ent.focus_set()

        ent.bind("<Return>", lambda _e: self._end_lang_cell_edit(commit=True))
        ent.bind("<Escape>", lambda _e: self._end_lang_cell_edit(commit=False))
        ent.bind("<FocusOut>", lambda _e: self._end_lang_cell_edit(commit=True))
        ent.bind("<Control-z>", lambda _e: (self.undo(), "break")[1])
        ent.bind("<Control-Z>", lambda _e: (self.undo(), "break")[1])

        self._lang_edit_entry = ent
        self._lang_edit_iid = iid
        self._lang_edit_col = column_id

    def _end_lang_cell_edit(self, *, commit: bool) -> None:
        if self._lang_edit_entry is None or self._lang_edit_iid is None or self._lang_edit_col is None:
            return

        ent = self._lang_edit_entry
        iid = self._lang_edit_iid
        col = self._lang_edit_col
        self._lang_edit_entry = None
        self._lang_edit_iid = None
        self._lang_edit_col = None

        new_text = ent.get().strip()
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

        ref = self._lang_iid_to_ref.get(iid)
        if ref is None:
            return

        raw = (new_text or "").strip()
        if (ref.get_private_text() or "").strip() == raw:
            return

        # Allow empty, or digits, or digits.digits
        if not raw:
            g2, l2 = "", ""
        elif "." in raw:
            g2, l2 = (x.strip() for x in raw.split(".", 1))
        else:
            g2, l2 = raw, ""

        def _ok(v: str) -> bool:
            v = (v or "").strip()
            return (not v) or v.isdigit()

        if not (_ok(g2) and _ok(l2)):
            messagebox.showerror("Invalid value", "LangRef ID must be digits or digits.digits (or blank).", parent=self)
            return

        self._push_undo()
        ref.set_group_label(g2, l2)
        cur_id = ref.get_private_text()
        try:
            old = self.tree_lang.item(iid, "values")
            disp = old[0] if old else ref.path
            txt = ref.get_label_text()
            def_id = self._tpl_default_langref_ids.get(ref.path, "")
            self.tree_lang.item(iid, values=(disp, cur_id, def_id, txt))
        except Exception:
            pass

        self._update_dirty_ui()
        self._set_item_tag(self.tree_lang, iid, "changed", self._lang_row_is_changed(ref))

        # Keep Values tab's langRef column in sync (best-effort; only updates currently rendered rows).
        try:
            if hasattr(self, "tree") and getattr(self, "tree", None) is not None:
                for vid, vref in list((self._iid_to_ref or {}).items()):
                    try:
                        base = (vref.path or "").split("/Val:", 1)[0]
                    except Exception:
                        base = ""
                    if base != (ref.path or ""):
                        continue
                    oldv = self.tree.item(vid, "values")
                    if not oldv or len(oldv) < 6:
                        continue
                    self.tree.item(vid, values=(oldv[0], oldv[1], oldv[2], cur_id, oldv[4], oldv[5]))
        except Exception:
            pass

    def _on_lang_tree_right_click(self, event: tk.Event) -> None:
        if getattr(self, "_lang_tree_menu", None) is None:
            return

        self._end_lang_cell_edit(commit=True)

        try:
            iid = self.tree_lang.identify_row(event.y)
            if iid:
                self.tree_lang.selection_set(iid)

            sel = self.tree_lang.selection()
            ref = self._lang_iid_to_ref.get(sel[0]) if sel else None
            has_default = bool(ref and (ref.path in self._tpl_default_langref_ids) and self._tpl_default_langref_ids.get(ref.path, "").strip())
            try:
                self._lang_tree_menu.entryconfigure(0, state=("normal" if has_default else "disabled"))
            except Exception:
                pass

            self._lang_tree_menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                self._lang_tree_menu.grab_release()
            except Exception:
                pass

    def apply_template_langref_to_selected(self) -> None:
        self._end_lang_cell_edit(commit=True)

        sel = self.tree_lang.selection()
        if not sel:
            return
        ref = self._lang_iid_to_ref.get(sel[0])
        if ref is None:
            return

        raw = (self._tpl_default_langref_ids.get(ref.path, "") or "").strip()
        if not raw:
            self._set_status("No template LangRef ID for selected DAI")
            return

        if (ref.get_private_text() or "").strip() == raw:
            return

        if "." in raw:
            g, l = (x.strip() for x in raw.split(".", 1))
        else:
            g, l = raw, ""
        self._push_undo()
        ref.set_group_label(g, l)
        self._render_lang_tree()
        self._update_dirty_ui()

    def _render_tree(
        self,
        *,
        anchor_kind: str | None = None,
        anchor_key: str | None = None,
        anchor_offset: int | None = None,
        preserve_yview_fraction: bool = False,
    ) -> None:
        # Preserve scroll position and selection where possible.
        sel = self.tree.selection()
        sel_iid = sel[0] if sel else None

        yview0: float | None = None
        if preserve_yview_fraction:
            try:
                yview0 = float(self.tree.yview()[0])
            except Exception:
                yview0 = None

        # Anchor on the current top-visible item (keeps rows from jumping when the
        # number of visible rows changes due to fold/unfold).
        # For pure value updates, preserve the yview fraction instead.
        top_anchor_kind: str | None = None
        top_anchor_key: str | None = None
        if not preserve_yview_fraction:
            if anchor_kind and anchor_key:
                top_anchor_kind = anchor_kind
                top_anchor_key = anchor_key
            else:
                try:
                    top_iid = self.tree.identify_row(0)
                    if top_iid:
                        if top_iid in self._header_key_by_iid:
                            top_anchor_kind = "h"
                            top_anchor_key = self._header_key_by_iid.get(top_iid)
                        elif top_iid in self._iid_to_ref:
                            top_anchor_kind = "v"
                            top_anchor_key = self._iid_to_ref[top_iid].path
                except Exception:
                    pass

        self.tree.delete(*self.tree.get_children())
        self._iid_to_ref.clear()
        self._header_key_by_iid.clear()
        self._header_iid_by_key.clear()

        def _iid_for_header(key: str) -> str:
            h = hashlib.sha1((key or "").encode("utf-8")).hexdigest()[:12]
            return f"h_{h}"

        def _iid_for_value(path: str) -> str:
            h = hashlib.sha1((path or "").encode("utf-8")).hexdigest()[:12]
            return f"v_{h}"

        def _segments(p: str) -> list[str]:
            return [s for s in (p or "").split("/") if s]

        def _is_hdr(seg: str) -> bool:
            return seg.startswith("DOI:") or seg.startswith("SDI:")

        class _Node:
            __slots__ = ("seg", "key", "depth", "children", "child_map", "leaves")

            def __init__(self, *, seg: str, key: str, depth: int):
                self.seg = seg
                self.key = key
                self.depth = depth
                self.children: list[_Node] = []
                self.child_map: dict[str, _Node] = {}
                self.leaves: list[tuple[str, ValueRef]] = []

            def get_child(self, seg: str, key: str) -> "_Node":
                if key in self.child_map:
                    return self.child_map[key]
                n = _Node(seg=seg, key=key, depth=self.depth + 1)
                self.child_map[key] = n
                self.children.append(n)
                return n

        root = _Node(seg="", key="", depth=-1)

        # Ensure DOI/SDI headers exist even when there are no DAI rows under them.
        # (Otherwise empty DOI containers disappear from the table.)
        try:
            # If a filter is active, keep behavior focused on matching value rows.
            if not (self.var_value_filter.get().strip()):
                # Prefer template order so template-only DOIs (e.g. Str/Op) still appear.
                for nm in list(getattr(self, "_tpl_doi_names", []) or []):
                    nm2 = (nm or "").strip()
                    if not nm2:
                        continue
                    seg0 = f"DOI:{nm2}"
                    root.get_child(seg0, seg0)

                # Pre-create nested SDI groups from template path structure.
                try:
                    for p in (getattr(self, "_tpl_value_order_index", {}) or {}).keys():
                        node0 = root
                        key_parts0: list[str] = []
                        for seg0 in _segments(p):
                            if not _is_hdr(seg0):
                                continue
                            key_parts0.append(seg0)
                            key0 = "/".join(key_parts0)
                            node0 = node0.get_child(seg0, key0)
                except Exception:
                    pass

                # Also include DOI/SDI containers that exist in the instance file.
                if self.doc is not None:
                    idx0 = getattr(self, "_current_ln_index", 0) or 0
                    if idx0 < 0 or idx0 >= len(self.doc.ln_elements):
                        idx0 = 0
                    ln_el = self.doc.ln_elements[idx0]

                    def _ln(tag: str) -> str:
                        if tag.startswith("{"):
                            return tag.split("}", 1)[1]
                        return tag

                    def _walk_sdi(parent_el, parent_key_parts: list[str], parent_node: _Node) -> None:
                        for ch in list(parent_el):
                            if not isinstance(ch.tag, str):
                                continue
                            if _ln(ch.tag) != "SDI":
                                continue
                            nm = (ch.attrib.get("name") or "").strip()
                            if not nm:
                                continue
                            seg = f"SDI:{nm}"
                            key_parts2 = parent_key_parts + [seg]
                            key2 = "/".join(key_parts2)
                            n2 = parent_node.get_child(seg, key2)
                            _walk_sdi(ch, key_parts2, n2)

                    for ch in list(ln_el):
                        if not isinstance(ch.tag, str):
                            continue
                        if _ln(ch.tag) != "DOI":
                            continue
                        nm = (ch.attrib.get("name") or "").strip()
                        if not nm:
                            continue
                        seg = f"DOI:{nm}"
                        n = root.get_child(seg, seg)
                        _walk_sdi(ch, [seg], n)
        except Exception:
            pass

        for row in self._rows_filtered:
            parts = _segments(row.ref.path)
            node = root
            key_parts: list[str] = []
            last_hdr_idx = -1
            for idx, seg in enumerate(parts):
                if _is_hdr(seg):
                    key_parts.append(seg)
                    key = "/".join(key_parts)
                    node = node.get_child(seg, key)
                    last_hdr_idx = idx

            rest = "/".join(parts[last_hdr_idx + 1 :]) if last_hdr_idx >= 0 else row.ref.path
            node.leaves.append((rest, row.ref))

        def _render_node(node: _Node) -> None:
            if node.depth >= 0:
                indent = "    " * node.depth
                collapsed = node.key in self._collapsed_groups
                symbol = "▸" if collapsed else "▾"
                iid = _iid_for_header(node.key)
                self._header_key_by_iid[iid] = node.key
                self._header_iid_by_key[node.key] = iid
                self.tree.insert("", "end", iid=iid, values=(f"{indent}{symbol} {node.seg}", "", "", "", "", ""))
                if collapsed:
                    return

            # Leaves first (DAI rows), then nested SDI blocks.
            for rest, ref in node.leaves:
                leaf_indent = "    " * (node.depth + 1)
                disp_path = f"{leaf_indent}{rest}" if rest else leaf_indent
                val = ref.get_value_text().replace("\n", " ")
                if len(val) > 200:
                    val = val[:200] + "..."

                d0 = self._tpl_default_values.get(ref.path, "")
                d = (d0 or "").replace("\n", " ")
                if len(d) > 200:
                    d = d[:200] + "..."
                iid = _iid_for_value(ref.path)
                self._iid_to_ref[iid] = ref
                lr = self._langref_id_for_value_path(ref.path)
                vk = (ref.dai_element.attrib.get("valKind") or "").strip()
                vi = (ref.dai_element.attrib.get("valImport") or "").strip().lower()
                self.tree.insert("", "end", iid=iid, values=(disp_path, val, d, lr, vk, vi))

            for ch in node.children:
                _render_node(ch)

        for ch in root.children:
            _render_node(ch)

        # Restore selection.
        try:
            if sel_iid and self.tree.exists(sel_iid):
                self.tree.selection_set(sel_iid)
        except Exception:
            pass
        if not self.tree.selection():
            # Prefer the first value row.
            for iid in self.tree.get_children():
                if iid in self._iid_to_ref:
                    self.tree.selection_set(iid)
                    break

        # Restore scroll position.
        if preserve_yview_fraction and yview0 is not None:
            try:
                self.tree.yview_moveto(yview0)
            except Exception:
                pass
        else:
            # Restore scroll anchor.
            try:
                anchor_iid: str | None = None
                if top_anchor_kind == "h" and top_anchor_key:
                    anchor_iid = self._header_iid_by_key.get(top_anchor_key)
                elif top_anchor_kind == "v" and top_anchor_key:
                    anchor_iid = _iid_for_value(top_anchor_key)

                if anchor_iid and self.tree.exists(anchor_iid):
                    items = list(self.tree.get_children())
                    idx = items.index(anchor_iid)
                    top_idx = idx
                    if anchor_offset is not None:
                        top_idx = max(0, min(idx - int(anchor_offset), max(0, len(items) - 1)))
                    denom = max(1, len(items))
                    self.tree.yview_moveto(top_idx / denom)
            except Exception:
                pass

        # If we were editing, end edit when the table refreshes.
        self._end_cell_edit(commit=False)
        self._end_meta_edit(commit=False)

        self._update_fold_all_button()

        self._reapply_changed_tags_values()

    def _group_keys_in_filtered(self) -> set[str]:
        keys: set[str] = set()

        def _segments(p: str) -> list[str]:
            return [s for s in (p or "").split("/") if s]

        for row in self._rows_filtered:
            acc: list[str] = []
            for seg in _segments(row.ref.path):
                if seg.startswith("DOI:") or seg.startswith("SDI:"):
                    acc.append(seg)
                    keys.add("/".join(acc))
        return keys

    def _update_fold_all_button(self) -> None:
        if not hasattr(self, "btn_fold_all"):
            return
        keys = self._group_keys_in_filtered()
        if not keys:
            try:
                self.btn_fold_all.configure(text="Fold all", state="disabled")
            except Exception:
                pass
            return

        all_collapsed = keys.issubset(self._collapsed_groups)
        try:
            self.btn_fold_all.configure(text=("Unfold all" if all_collapsed else "Fold all"), state="normal")
        except Exception:
            pass

    def toggle_fold_all(self) -> None:
        # Toggle fold state for all DOI/SDI groups currently shown.
        self._end_cell_edit(commit=True)

        keys = self._group_keys_in_filtered()
        if not keys:
            return

        if keys.issubset(self._collapsed_groups):
            # Unfold all
            self._collapsed_groups.difference_update(keys)
        else:
            # Fold all
            self._collapsed_groups.update(keys)

        self._render_tree()

    def _selected_ref(self) -> ValueRef | None:
        sel = self.tree.selection()
        if not sel:
            return None
        return self._iid_to_ref.get(sel[0])

    def _on_tree_double_click(self, event: tk.Event) -> None:
        # Double-click behavior:
        # - DOI/SDI header row: fold/unfold
        # - Value rows: start editing when double-clicking specific columns.
        try:
            region = self.tree.identify("region", event.x, event.y)
            if region != "cell":
                return
            col = self.tree.identify_column(event.x)
            iid = self.tree.identify_row(event.y)
            if not iid:
                return

            # DOI/SDI header rows: fold/unfold on double click (Path column).
            if col == "#1" and iid in self._header_key_by_iid:
                # Commit any in-progress edit first.
                self._end_cell_edit(commit=True)
                self._end_meta_edit(commit=True)
                self.tree.selection_set(iid)

                # Capture current pixel position of the clicked row so we can restore it.
                old_y = 0
                row_h = 1
                try:
                    bb0 = self.tree.bbox(iid, column="path")
                    if bb0:
                        _x, y0, _w, h0 = bb0
                        old_y = int(y0)
                        row_h = max(1, int(h0))
                except Exception:
                    old_y = 0
                    row_h = 1

                key = self._header_key_by_iid.get(iid)
                if not key:
                    return

                # Preserve row offset of clicked header within the viewport.
                anchor_offset = 0
                try:
                    items0 = list(self.tree.get_children())
                    top0 = self.tree.identify_row(0)
                    if top0 and top0 in items0 and iid in items0:
                        anchor_offset = items0.index(iid) - items0.index(top0)
                except Exception:
                    anchor_offset = 0

                if key in self._collapsed_groups:
                    self._collapsed_groups.remove(key)
                else:
                    self._collapsed_groups.add(key)

                # Anchor on the clicked header to avoid scroll jumping.
                self._render_tree(anchor_kind="h", anchor_key=key, anchor_offset=anchor_offset)

                # After rebuild, adjust scroll so the clicked row stays at the same pixel Y.
                try:
                    iid2 = self._header_iid_by_key.get(key)
                    if iid2 and self.tree.exists(iid2):
                        bb1 = self.tree.bbox(iid2, column="path")
                        if not bb1:
                            self.tree.see(iid2)
                            bb1 = self.tree.bbox(iid2, column="path")
                        if bb1:
                            _x, y1, _w, _h = bb1
                            delta_units = int(round((int(y1) - old_y) / float(row_h)))
                            if delta_units:
                                self.tree.yview_scroll(delta_units, "units")
                except Exception:
                    pass

                return

            if iid not in self._iid_to_ref:
                return
            self.tree.selection_set(iid)

            if col == "#2":
                self._begin_cell_edit(iid, col="val")
                return
            if col == "#4":
                try:
                    ref2 = self._iid_to_ref.get(iid)
                    dai_name = (ref2.dai_element.attrib.get("name") or "").strip() if ref2 else ""
                except Exception:
                    dai_name = ""
                if dai_name != "d":
                    return
                self._begin_cell_edit(iid, col="lr")
                return
            if col == "#5":
                self._begin_meta_edit(iid, "vk")
                return
            if col == "#6":
                self._begin_meta_edit(iid, "vi")
                return
        except Exception:
            return

    def _on_tree_left_click(self, event: tk.Event) -> str | None:
        # Single click should not fold/unfold; keep default behavior.
        # But for valKind/valImport, single click should open dropdown (consistent with DO template UI).
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

            if iid not in self._iid_to_ref:
                return None

            if col == "#5":
                col_key = "vk"
            elif col == "#6":
                col_key = "vi"
            else:
                return None

            # If already editing this cell with a combobox, toggle dropdown.
            if (
                isinstance(self._meta_edit_cb, ttk.Combobox)
                and self._meta_edit_iid == iid
                and self._meta_edit_col == col_key
            ):
                self._combobox_toggle_posted(self._meta_edit_cb)
                return "break"

            self._begin_meta_edit(iid, col_key)
            return "break"
        except Exception:
            return None

    def start_edit_selected(self) -> None:
        # F2 edits the current tab's selected row.
        try:
            cur = self.details_nb.select()
        except Exception:
            cur = ""

        if cur:
            try:
                tab_text = self.details_nb.tab(cur, "text")
            except Exception:
                tab_text = ""
        else:
            tab_text = ""

        if tab_text == "Language reference":
            sel = self.tree_lang.selection()
            if not sel:
                return
            iid = sel[0]
            if iid not in self._lang_iid_to_ref:
                return
            # Default to editing Group ID.
            self._begin_lang_cell_edit(iid, "#2")
            return

        if tab_text == "Private":
            sel = self.tree_priv.selection() if hasattr(self, "tree_priv") else ()
            if not sel:
                return
            iid = sel[0]
            row = self._priv_iid_to_row.get(iid)
            if row is None:
                return
            # Default to editing value; for new custom rows, type first.
            if row.is_custom and not (row.private_type or "").strip():
                self._begin_priv_cell_edit(iid, "type")
                return
            self._begin_priv_cell_edit(iid, "value")
            return

        sel = self.tree.selection()
        if not sel:
            return
        if sel[0] not in self._iid_to_ref:
            return
        self._begin_cell_edit(sel[0])

    def _on_tree_right_click(self, event: tk.Event) -> None:
        if self._tree_menu is None:
            return

        # Commit any in-progress edit first.
        self._end_cell_edit(commit=True)
        self._end_meta_edit(commit=True)

        try:
            iid = self.tree.identify_row(event.y)
            if iid:
                self.tree.selection_set(iid)

            # Enable only when on a grouped Val row.
            ref = self._selected_ref()
            is_grouped = bool(ref and "/Val:sGroup=" in ref.path)
            try:
                self._tree_menu.entryconfigure(0, state=("normal" if is_grouped else "disabled"))
            except Exception:
                pass

            has_default = bool(ref and (ref.path in self._tpl_default_values))
            try:
                self._tree_menu.entryconfigure(1, state=("normal" if has_default else "disabled"))
            except Exception:
                pass

            self._tree_menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                self._tree_menu.grab_release()
            except Exception:
                pass

    def apply_to_all_groups(self) -> None:
        self._end_meta_edit(commit=True)
        ref0 = self._selected_ref()
        if not ref0:
            return

        path = ref0.path
        marker = "/Val:sGroup="
        if marker not in path:
            messagebox.showinfo("Apply to all groups", "Selected row is not a grouped <Val sGroup>.", parent=self)
            return

        base = path.split(marker, 1)[0]
        new_value = ref0.get_value_text()

        # Apply to all ValueRefs under the same DAI base.
        targets = [r.ref for r in self._rows_all if r.ref.path.startswith(base + marker)]
        if not any(((t.get_value_text() or "") != (new_value or "")) for t in targets):
            return

        self._push_undo()

        changed_paths: set[str] = set()
        for r in self._rows_all:
            if r.ref.path.startswith(base + marker):
                r.ref.set_value_text(new_value)
                changed_paths.add(r.ref.path)

        # Re-render to keep indentation/fold rows consistent.
        self._render_tree(preserve_yview_fraction=True)

        self._update_dirty_ui()

    def apply_template_value_to_selected(self) -> None:
        # Copy the template-defined default value into the current value.
        self._end_cell_edit(commit=True)
        self._end_meta_edit(commit=True)

        ref = self._selected_ref()
        if not ref:
            return

        if ref.path not in self._tpl_default_values:
            self._set_status("No template default for selected DAI")
            return

        new_val = self._tpl_default_values.get(ref.path, "")
        if (ref.get_value_text() or "") == (new_val or ""):
            return

        self._push_undo()
        ref.set_value_text(new_val)
        self._render_tree(preserve_yview_fraction=True)
        self._update_dirty_ui()

    def _begin_cell_edit(self, iid: str, *, col: str = "val") -> None:
        ref = self._iid_to_ref.get(iid)
        if ref is None:
            return

        self._end_cell_edit(commit=False)
        self._end_meta_edit(commit=False)

        if col not in {"val", "lr"}:
            return

        if col == "lr":
            try:
                dai_name = (ref.dai_element.attrib.get("name") or "").strip()
            except Exception:
                dai_name = ""
            if dai_name != "d":
                return

        column_id = "val" if col == "val" else "lr"
        bbox = self.tree.bbox(iid, column=column_id)
        if not bbox:
            return
        x, y, w, h = bbox

        if col == "val":
            value_text = ref.get_value_text().replace("\n", " ")
        else:
            value_text = (self._langref_id_for_value_path(ref.path) or "").strip()
        ent = ttk.Entry(self.tree)
        ent.place(x=x, y=y, width=w, height=h)
        ent.insert(0, value_text)
        ent.selection_range(0, "end")
        ent.focus_set()

        ent.bind("<Return>", lambda _e: self._end_cell_edit(commit=True))
        ent.bind("<Escape>", lambda _e: self._end_cell_edit(commit=False))
        ent.bind("<FocusOut>", lambda _e: self._end_cell_edit(commit=True))
        ent.bind("<Control-z>", lambda _e: (self.undo(), "break")[1])
        ent.bind("<Control-Z>", lambda _e: (self.undo(), "break")[1])

        self._edit_entry = ent
        self._edit_iid = iid
        self._edit_col = col

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

    def _on_meta_combobox_focus_out(self, event: tk.Event) -> None:
        """Commit meta edit on focus-out, but avoid committing while the dropdown is open.

        On Windows, clicking the combobox dropdown list can trigger <FocusOut> on the combobox
        before <<ComboboxSelected>> fires; committing/destroying at that time breaks selection.
        """
        try:
            widget = event.widget
        except Exception:
            widget = None

        if isinstance(widget, ttk.Combobox) and self._combobox_is_posted(widget):
            return

        self._end_meta_edit(commit=True)

    def _begin_meta_edit(self, iid: str, col: str) -> None:
        ref = self._iid_to_ref.get(iid)
        if ref is None:
            return

        self._end_cell_edit(commit=True)
        self._end_meta_edit(commit=False)

        if col not in {"vk", "vi"}:
            return

        bbox = self.tree.bbox(iid, column=col)
        if not bbox:
            return
        x, y, w, h = bbox

        def norm_vk(v: str) -> str:
            v0 = (v or "").strip()
            if not v0:
                return ""
            u = v0.upper()
            if u == "SET":
                return "Set"
            if u == "CONF":
                return "Conf"
            if u == "RO":
                return "RO"
            return v0

        def norm_vi(v: str) -> str:
            v0 = (v or "").strip().lower()
            if not v0:
                return ""
            if v0 in {"true", "false"}:
                return v0
            return ""

        if col == "vk":
            base_values = ("", "Set", "Conf", "RO")
            cur = norm_vk((ref.dai_element.attrib.get("valKind") or ""))
            # Preserve unknown values if present by injecting them.
            values = (cur,) + base_values if (cur and cur not in base_values) else base_values
        else:
            base_values = ("", "true", "false")
            cur = norm_vi((ref.dai_element.attrib.get("valImport") or ""))
            values = (cur,) + base_values if (cur and cur not in base_values) else base_values

        cb = ttk.Combobox(self.tree, state="readonly", values=values)
        cb.place(x=x, y=y, width=w, height=h)
        cb.set(cur)
        cb.focus_set()

        cb.bind("<<ComboboxSelected>>", lambda _e: self._end_meta_edit(commit=True))
        cb.bind("<Return>", lambda _e: self._end_meta_edit(commit=True))
        cb.bind("<Escape>", lambda _e: self._end_meta_edit(commit=False))
        cb.bind("<FocusOut>", self._on_meta_combobox_focus_out)
        cb.bind("<Control-z>", lambda _e: (self.undo(), "break")[1])
        cb.bind("<Control-Z>", lambda _e: (self.undo(), "break")[1])

        # Single-click toggles dropdown while editing, and we auto-post on start.
        cb.bind("<Button-1>", lambda _e: (self._combobox_toggle_posted(cb), "break")[1])
        try:
            self.tree.after_idle(lambda: self._combobox_post(cb))
        except Exception:
            self._combobox_post(cb)

        self._meta_edit_cb = cb
        self._meta_edit_iid = iid
        self._meta_edit_col = col

    def _end_meta_edit(self, *, commit: bool) -> None:
        if self._meta_edit_cb is None or self._meta_edit_iid is None or self._meta_edit_col is None:
            return

        cb = self._meta_edit_cb
        iid = self._meta_edit_iid
        col = self._meta_edit_col
        self._meta_edit_cb = None
        self._meta_edit_iid = None
        self._meta_edit_col = None

        new_text = (cb.get() or "").strip()
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

        ref = self._iid_to_ref.get(iid)
        if ref is None:
            return

        if col == "vk":
            if new_text not in {"", "Set", "Conf", "RO"}:
                return
            cur = (ref.dai_element.attrib.get("valKind") or "").strip()
            if cur == new_text:
                return
            self._push_undo()
            if not new_text:
                try:
                    ref.dai_element.attrib.pop("valKind", None)
                except Exception:
                    pass
            else:
                ref.dai_element.attrib["valKind"] = new_text
        else:
            if new_text not in {"", "true", "false"}:
                return
            cur = (ref.dai_element.attrib.get("valImport") or "").strip().lower()
            if cur == new_text:
                return
            self._push_undo()
            if not new_text:
                try:
                    ref.dai_element.attrib.pop("valImport", None)
                except Exception:
                    pass
            else:
                ref.dai_element.attrib["valImport"] = new_text

        # Update display for the row.
        try:
            old = self.tree.item(iid, "values")
            disp = old[0] if old else ref.path
            val_shown = old[1] if old and len(old) > 1 else ref.get_value_text().replace("\n", " ")
            def_shown = old[2] if old and len(old) > 2 else (self._tpl_default_values.get(ref.path, "") or "").replace("\n", " ")
            lr_shown = old[3] if old and len(old) > 3 else self._langref_id_for_value_path(ref.path)
            if len(val_shown) > 200:
                val_shown = val_shown[:200] + "..."
            if len(def_shown) > 200:
                def_shown = def_shown[:200] + "..."
            vk_shown = (ref.dai_element.attrib.get("valKind") or "").strip()
            vi_shown = (ref.dai_element.attrib.get("valImport") or "").strip().lower()
            self.tree.item(iid, values=(disp, val_shown, def_shown, lr_shown, vk_shown, vi_shown))
        except Exception:
            pass

        self._update_dirty_ui()
        self._reapply_changed_tags_values()

    def _end_cell_edit(self, *, commit: bool) -> None:
        if self._edit_entry is None or self._edit_iid is None or self._edit_col is None:
            return

        ent = self._edit_entry
        iid = self._edit_iid
        col = self._edit_col
        self._edit_entry = None
        self._edit_iid = None
        self._edit_col = None

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

        ref = self._iid_to_ref.get(iid)
        if ref is None:
            return

        if col == "val":
            if (ref.get_value_text() or "") == (new_text or ""):
                return

            self._push_undo()
            ref.set_value_text(new_text)

            # Update display (truncated to 200 chars).
            shown = ref.get_value_text().replace("\n", " ")
            if len(shown) > 200:
                shown = shown[:200] + "..."
            try:
                old = self.tree.item(iid, "values")
                disp = old[0] if old else ref.path
                d0 = self._tpl_default_values.get(ref.path, "")
                d = (d0 or "").replace("\n", " ")
                if len(d) > 200:
                    d = d[:200] + "..."
                lr = old[3] if old and len(old) > 3 else self._langref_id_for_value_path(ref.path)
                vk = (ref.dai_element.attrib.get("valKind") or "").strip()
                vi = (ref.dai_element.attrib.get("valImport") or "").strip().lower()
                self.tree.item(iid, values=(disp, shown, d, lr, vk, vi))
            except Exception:
                pass
            self._update_dirty_ui()
            self._reapply_changed_tags_values()
            return

        if col == "lr":
            raw = (new_text or "").strip()
            cur = (self._langref_id_for_value_path(ref.path) or "").strip()
            if cur == raw:
                return

            # Allow empty, or digits, or digits.digits
            if not raw:
                g2, l2 = "", ""
            elif "." in raw:
                g2, l2 = (x.strip() for x in raw.split(".", 1))
            else:
                g2, l2 = raw, ""

            def _ok(v: str) -> bool:
                v = (v or "").strip()
                return (not v) or v.isdigit()

            if not (_ok(g2) and _ok(l2)):
                messagebox.showerror("Invalid value", "LangRef ID must be digits or digits.digits (or blank).", parent=self)
                return

            # Apply to the corresponding DAI-level <Private type=...LangRef>.
            base = (ref.path or "").split("/Val:", 1)[0]
            lrref: LangRefRef | None = None
            try:
                for row in (self._lang_rows_all or []):
                    if (row.ref.path or "") == base:
                        lrref = row.ref
                        break
            except Exception:
                lrref = None

            if lrref is None:
                # Create a new ref; set_group_label() will ensure the <Private> element exists.
                val_el: ET.Element | None = None
                try:
                    for ch in list(ref.dai_element):
                        if not isinstance(ch.tag, str):
                            continue
                        if ch.tag.split("}", 1)[-1] == "Val":
                            val_el = ch
                            break
                except Exception:
                    val_el = None

                lrref = LangRefRef(path=base, dai_element=ref.dai_element, val_element=val_el)
                try:
                    self._lang_rows_all.append(_LangRow(ref=lrref))
                except Exception:
                    pass

            self._push_undo()
            lrref.set_group_label(g2, l2)
            new_id = lrref.get_private_text()

            # Update Values display for this row.
            try:
                old = self.tree.item(iid, "values")
                disp = old[0] if old else ref.path
                val_shown = old[1] if old and len(old) > 1 else (ref.get_value_text() or "").replace("\n", " ")
                def_shown = old[2] if old and len(old) > 2 else (self._tpl_default_values.get(ref.path, "") or "").replace("\n", " ")
                if len(val_shown) > 200:
                    val_shown = val_shown[:200] + "..."
                if len(def_shown) > 200:
                    def_shown = def_shown[:200] + "..."
                vk = (ref.dai_element.attrib.get("valKind") or "").strip()
                vi = (ref.dai_element.attrib.get("valImport") or "").strip().lower()
                self.tree.item(iid, values=(disp, val_shown, def_shown, new_id, vk, vi))
            except Exception:
                pass

            # Refresh lang tree (keeps existing tab; best-effort to make the change visible there too).
            try:
                self._apply_lang_filter()
            except Exception:
                pass

            self._update_dirty_ui()
            return

    def _apply_header_to_doc(self) -> None:
        if not self.doc:
            return
        update_ln_header(
            self.doc,
            self._current_ln_index,
            lnClass=self.var_lnClass.get(),
            inst=self.var_inst.get(),
            prefix=self.var_prefix.get(),
            lnType=self.var_lnType.get(),
        )

    def is_dirty(self) -> bool:
        if not self.doc or self._saved_sig is None:
            return False
        # Ensure doc reflects header edits
        self._apply_header_to_doc()
        return compute_signature(self.doc) != self._saved_sig

    def _update_dirty_ui(self) -> None:
        dirty = self.is_dirty()
        self.btn_save.configure(text=("Save *" if dirty else "Save"))

        try:
            self.btn_save.configure(style=("Dirty.TButton" if dirty else "TButton"))
        except Exception:
            pass

    def _update_doc_dependent_ui(self) -> None:
        """Enable/disable actions that require an open LN instance."""

        has_doc = self.doc is not None
        try:
            if hasattr(self, "btn_create_app") and self.btn_create_app is not None:
                self.btn_create_app.configure(state=("normal" if has_doc else "disabled"))
        except Exception:
            pass

        try:
            if hasattr(self, "btn_refresh") and self.btn_refresh is not None:
                self.btn_refresh.configure(state=("normal" if has_doc else "disabled"))
        except Exception:
            pass

    def save(self) -> None:
        if not self.doc:
            # Silent no-op for Ctrl+S
            return

        self._apply_header_to_doc()

        # If your naming convention is <prefix><lnClass>.xml, offer to sync filename.
        ln_class = self.var_lnClass.get().strip()
        prefix = self.var_prefix.get().strip()
        suggested_name = _suggest_instance_filename(prefix, ln_class)

        original_path = self.doc.file_path
        target_path = original_path

        if suggested_name:
            desired_path = original_path.with_name(suggested_name)

            # If file is not under lndm_dir, move it into lndm_dir when syncing.
            try:
                if original_path.resolve().is_relative_to(self.lndm_dir.resolve()):
                    desired_path = self.lndm_dir / suggested_name
                else:
                    desired_path = self.lndm_dir / suggested_name
            except Exception:
                desired_path = self.lndm_dir / suggested_name

            try:
                same = desired_path.resolve() == original_path.resolve()
            except Exception:
                same = os.fspath(desired_path) == os.fspath(original_path)

            if not same:
                if desired_path.exists():
                    choice = messagebox.askyesnocancel(
                        "Rename instance file?",
                        "LN instance filename rule is <prefix><lnClass>.xml\n\n"
                        f"Suggested: {os.fspath(desired_path)}\n\n"
                        "File already exists.\n\n"
                        "Yes = overwrite\nNo = save to a unique name\nCancel = keep current name",
                        parent=self.winfo_toplevel(),
                    )
                    if choice is None:
                        target_path = original_path
                    elif choice is True:
                        target_path = desired_path
                    else:
                        target_path = _pick_unique_path(desired_path)
                else:
                    if messagebox.askyesno(
                        "Rename instance file?",
                        "LN instance filename rule is <prefix><lnClass>.xml\n\n"
                        f"Rename file to:\n{os.fspath(desired_path)}\n\n"
                        "(This will also move it under the lndm folder)",
                        parent=self.winfo_toplevel(),
                    ):
                        target_path = desired_path
                    else:
                        target_path = original_path

        try:
            if target_path != original_path:
                _ensure_backup(original_path)
                save_ln_instance_document(self.doc, target_path=target_path, make_backup=False)
                # If we successfully wrote the new file, remove the old one.
                try:
                    if original_path.exists():
                        original_path.unlink()
                except Exception:
                    # If we cannot delete, leave it; user can clean up manually.
                    pass

                self.doc.file_path = target_path
                self.var_path.set(os.fspath(target_path))
                self.refresh_instance_list()
                try:
                    rel = os.fspath(target_path.resolve().relative_to(self.lndm_dir.resolve()))
                    self.var_instance_selected.set(rel)
                except Exception:
                    self.var_instance_selected.set(os.fspath(target_path))
            else:
                save_ln_instance_document(self.doc, make_backup=True)
        except Exception as e:
            messagebox.showerror("Save failed", str(e), parent=self)
            return
        self._saved_sig = compute_signature(self.doc)
        self.mark_saved()
        self._update_dirty_ui()
        self._set_status(f"Saved: {os.fspath(self.doc.file_path)}")

    def save_as(self) -> None:
        if not self.doc:
            return

        initialdir = self.doc.file_path.parent if self.doc.file_path else self.lndm_dir
        target = filedialog.asksaveasfilename(
            parent=self,
            title="Save LN instance as",
            defaultextension=".xml",
            initialdir=os.fspath(initialdir),
            initialfile=self.doc.file_path.name,
            filetypes=[("XML", "*.xml"), ("All", "*")],
        )
        if not target:
            return

        target_path = Path(target)
        if target_path.exists():
            if not messagebox.askyesno("Overwrite?", f"File exists:\n\n{os.fspath(target_path)}\n\nOverwrite?", parent=self):
                return

        self._apply_header_to_doc()
        try:
            save_ln_instance_document(self.doc, target_path=target_path, make_backup=False)
        except Exception as e:
            messagebox.showerror("Save As failed", str(e), parent=self)
            return

        # Switch current doc path
        self.doc.file_path = target_path
        self.var_path.set(os.fspath(target_path))
        self._saved_sig = compute_signature(self.doc)
        self.mark_saved()
        self._update_dirty_ui()
        self._set_status(f"Saved As: {os.fspath(target_path)}")

    def create_application_file_with_template(self) -> None:
        if not self.doc:
            return

        # Ensure header edits are applied before deriving LnRef/desc.
        self._apply_header_to_doc()

        # Only auto-fill Application-related defaults when the user explicitly
        # creates an application file (no linkage with other actions).
        self._sync_application_autofill(force=True)

        app_dir = self.workspace_root / "ep7_datamodel" / "datamodel" / "application"
        if not app_dir.exists():
            messagebox.showerror(
                "Missing folder",
                f"Application folder not found:\n\n{os.fspath(app_dir)}",
                parent=self,
            )
            return

        suggested = _suggest_application_filename(self.var_prefix.get(), self.var_lnClass.get())
        if not suggested:
            suggested = "Application.xml"

        target = filedialog.asksaveasfilename(
            parent=self,
            title="Create application file",
            defaultextension=".xml",
            initialdir=os.fspath(app_dir),
            initialfile=suggested,
            filetypes=[("XML", "*.xml"), ("All", "*")],
        )
        if not target:
            return

        target_path = Path(target)
        try:
            # Enforce requirement: file must be created under application folder.
            target_path.resolve().relative_to(app_dir.resolve())
        except Exception:
            messagebox.showerror(
                "Invalid location",
                "Application file must be under:\n\n"
                f"{os.fspath(app_dir)}\n\n"
                "Please choose a filename in that folder.",
                parent=self,
            )
            return

        if target_path.exists():
            if not messagebox.askyesno(
                "Overwrite?",
                f"File exists:\n\n{os.fspath(target_path)}\n\nOverwrite?",
                parent=self,
            ):
                return

        try:
            create_application_file_for_ln_instance(
                self.doc,
                target_path=target_path,
                funblock_name=(self.var_app_name.get() or "").strip() or None,
                funblock_class=(self.var_app_class.get() or "").strip() or None,
                seq_nb=(self.var_app_seqNb.get() or "").strip() or "50",
                ln_ref=(self.var_app_LnRef.get() or "").strip() or None,
                desc=(self.var_app_desc.get() or ""),
            )
        except Exception as e:
            messagebox.showerror("Create application failed", str(e), parent=self)
            return

        self._set_status(f"Created application: {os.fspath(target_path)}")

    def _set_status(self, text: str) -> None:
        self.status.set(text)
        if self._status_callback is not None:
            try:
                self._status_callback(text)
            except Exception:
                pass


class LNInstanceEditor(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        workspace_root: Path | None = None,
        lndm_dir: Path | None = None,
        initial_path: Path | None = None,
    ):
        super().__init__(parent)
        self.title("LN instance")
        # Wider default so Values columns (including Template + valKind/valImport) are visible.
        self.geometry("1550x820")
        try:
            self.minsize(1200, 700)
        except Exception:
            pass

        if workspace_root is None:
            workspace_root = Path(__file__).resolve().parent.parent
        if lndm_dir is None:
            lndm_dir = Path(workspace_root) / "ep7_datamodel" / "datamodel" / "lndm"

        self.frame = LNInstanceEditorFrame(
            self,
            workspace_root=Path(workspace_root),
            lndm_dir=Path(lndm_dir),
            show_status_bar=True,
            initial_path=initial_path,
        )
        self.frame.pack(fill="both", expand=True)

        self.frame.bind_shortcuts_to(self)
        self.bind("<Escape>", lambda _e: self.destroy())


def open_ln_instance_editor(
    parent: tk.Misc,
    *,
    workspace_root: Path | None = None,
    lndm_dir: Path | None = None,
    initial_path: Path | None = None,
) -> LNInstanceEditor:
    win = LNInstanceEditor(parent, workspace_root=workspace_root, lndm_dir=lndm_dir, initial_path=initial_path)
    win.focus_set()
    return win
