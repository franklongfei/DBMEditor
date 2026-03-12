from __future__ import annotations

import os
import re
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox
from tkinter import ttk
from typing import Callable
import xml.etree.ElementTree as ET

try:
    from iec61850_scanner import TypeCatalog
    from ui.common import SCL_NS, deepcopy_et_element, find_type_file, local_name, qname, scan_xml_relpaths
except ModuleNotFoundError as e:
    if getattr(e, "name", None) not in {"iec61850_scanner", "ui"}:
        raise
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from iec61850_scanner import TypeCatalog
    from ui.common import SCL_NS, deepcopy_et_element, find_type_file, local_name, qname, scan_xml_relpaths


def _sort_filter_matches(raw: str, values: list[str]) -> list[str]:
    q = (raw or "").strip().lower()
    vals = list(values or [])
    if not q:
        return vals

    def rank(item: tuple[int, str]) -> tuple[int, int]:
        idx, v = item
        text = (v or "").strip().lower()
        stem = Path(text).stem.lower() if text else ""
        if text == q:
            pri = 0
        elif stem == q:
            pri = 1
        elif text == f"{q}.xml":
            pri = 2
        elif stem.startswith(q):
            pri = 3
        elif q in stem:
            pri = 4
        elif text.startswith(q):
            pri = 5
        else:
            pri = 6
        return (pri, idx)

    return [v for _i, v in sorted(enumerate(vals), key=rank)]


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


class NewDOTypeDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        do_type_ids: list[str],
        cdc_values: list[str],
        get_base_cdc: Callable[[str], str] | None = None,
        get_do_type_preview: Callable[[str], str] | None = None,
    ):
        super().__init__(parent)
        self.title("New DO template (DOType)")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self._result: dict[str, str] | None = None
        self._do_type_ids = list(do_type_ids or [])
        self._cdc_values = list(cdc_values or [])
        self._get_base_cdc = get_base_cdc
        self._get_do_type_preview = get_do_type_preview
        self._preview_after_id: str | None = None

        self._id_internal_update = False
        self._id_user_modified = False
        self._last_suggested_id = ""

        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text="Create from").grid(row=0, column=0, sticky="w", pady=4)
        self.var_base = tk.StringVar(value="(Blank)")
        base_values = ["(Blank)"] + list(self._do_type_ids)
        self.var_filter = tk.StringVar(value="")
        filter_row = ttk.Frame(frm)
        filter_row.grid(row=0, column=1, sticky="we", padx=(10, 0), pady=4)
        filter_row.columnconfigure(1, weight=1)
        ttk.Label(filter_row, text="Filter").grid(row=0, column=0, sticky="w")
        ent_filter = ttk.Entry(filter_row, textvariable=self.var_filter)
        ent_filter.grid(row=0, column=1, sticky="we", padx=(8, 0))

        cb_base = ttk.Combobox(frm, textvariable=self.var_base, values=base_values, width=62)
        cb_base.grid(row=1, column=1, sticky="we", padx=(10, 0), pady=(0, 4))
        ttk.Label(frm, text="").grid(row=1, column=0)

        def _open_base_dropdown(_event: tk.Event | None = None) -> None:
            try:
                cb_base.focus_set()
            except Exception:
                pass
            try:
                cb_base.after_idle(lambda: cb_base.event_generate("<Down>"))
            except Exception:
                pass

        try:
            cb_base.bind("<Button-1>", _open_base_dropdown, add="+")
        except Exception:
            pass

        ttk.Label(frm, text="File name").grid(row=2, column=0, sticky="w", pady=4)
        self.var_id = tk.StringVar(value="")
        ent_id = ttk.Entry(frm, textvariable=self.var_id, width=64)
        ent_id.grid(row=2, column=1, sticky="we", padx=(10, 0), pady=4)

        def _mark_user_modified(*_args) -> None:
            if self._id_internal_update:
                return
            self._id_user_modified = True

        self.var_id.trace_add("write", _mark_user_modified)

        ttk.Label(frm, text="CDC").grid(row=3, column=0, sticky="w", pady=4)
        self.var_cdc = tk.StringVar(value="")
        cb_cdc = ttk.Combobox(frm, textvariable=self.var_cdc, values=list(self._cdc_values), width=62)
        try:
            cb_cdc.configure(state="readonly")
        except Exception:
            pass
        cb_cdc.grid(row=3, column=1, sticky="we", padx=(10, 0), pady=4)

        ttk.Label(frm, text="desc (optional)").grid(row=4, column=0, sticky="w", pady=4)
        self.var_desc = tk.StringVar(value="")
        ent_desc = ttk.Entry(frm, textvariable=self.var_desc, width=64)
        ent_desc.grid(row=4, column=1, sticky="we", padx=(10, 0), pady=4)

        hint = ttk.Label(
            frm,
            text=(
                "Tip: If you pick an existing DOType, DA/Private blocks will be copied, then id/CDC/desc updated."
            ),
        )
        hint.grid(row=5, column=0, columnspan=2, sticky="w", pady=(8, 0))

        preview_box = ttk.Frame(frm)
        preview_box.grid(row=6, column=0, columnspan=2, sticky="nsew", pady=(10, 0))
        ttk.Label(preview_box, text="DOType template preview").pack(anchor="w")

        preview_inner = ttk.Frame(preview_box)
        preview_inner.pack(fill="both", expand=True, pady=(6, 0))
        preview_inner.columnconfigure(0, weight=1)
        preview_inner.rowconfigure(0, weight=1)

        self.txt_preview = tk.Text(preview_inner, height=12, wrap="none")
        y = ttk.Scrollbar(preview_inner, orient="vertical", command=self.txt_preview.yview)
        self.txt_preview.configure(yscrollcommand=y.set)
        self.txt_preview.grid(row=0, column=0, sticky="nsew")
        y.grid(row=0, column=1, sticky="ns")
        try:
            self.txt_preview.configure(state="disabled")
        except Exception:
            pass

        frm.rowconfigure(6, weight=1)

        btns = ttk.Frame(frm)
        btns.grid(row=7, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Cancel", command=self._cancel).pack(side="right")
        ttk.Button(btns, text="Create", command=self._ok).pack(side="right", padx=(0, 8))

        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Control-f>", lambda _e: ent_filter.focus_set())

        def unique_copy_name(base: str) -> str:
            existing = set(self._do_type_ids)
            candidate = f"{base}_copy"
            if candidate not in existing:
                return candidate
            i = 2
            while True:
                candidate = f"{base}_copy{i}"
                if candidate not in existing:
                    return candidate
                i += 1

        def prefill(*_args) -> None:
            base_id = self.var_base.get().strip()
            if base_id == "(Blank)":
                return

            cur = self.var_id.get().strip()
            if (not self._id_user_modified) or (not cur) or (cur == self._last_suggested_id):
                suggested = unique_copy_name(base_id)
                self._id_internal_update = True
                try:
                    self.var_id.set(suggested)
                    self._last_suggested_id = suggested
                finally:
                    self._id_internal_update = False

            # Always sync CDC from selected base DOType so users see it immediately
            # when switching "Create from" entries.
            if callable(self._get_base_cdc):
                try:
                    cdc0 = (self._get_base_cdc(base_id) or "").strip()
                except Exception:
                    cdc0 = ""
                self.var_cdc.set(cdc0)

        def apply_filter(*_args) -> None:
            raw = (self.var_filter.get() or "").strip().lower()
            if not raw:
                filtered = list(base_values)
            else:
                tokens = [t for t in raw.split() if t]

                def ok(v: str) -> bool:
                    lv = (v or "").lower()
                    return all(t in lv for t in tokens)

                filtered = [x for x in base_values if ok(x)]
                filtered = _sort_filter_matches(raw, filtered)

            cur = (self.var_base.get() or "").strip()
            cb_base["values"] = filtered[:1500]
            if raw and filtered:
                if cur != filtered[0]:
                    self.var_base.set(filtered[0])
            elif (not raw) and cur and (cur not in filtered):
                self.var_base.set("")

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

        self.var_base.trace_add("write", prefill)
        self.var_base.trace_add("write", schedule_preview_update)
        self.var_filter.trace_add("write", apply_filter)
        apply_filter()
        prefill()
        self._update_preview()

        ent_filter.focus_set()

    def _update_preview(self) -> None:
        get_preview = getattr(self, "_get_do_type_preview", None)
        txt = getattr(self, "txt_preview", None)
        if txt is None:
            return

        do_type = (self.var_base.get() or "").strip()

        if do_type == "(Blank)" or not do_type:
            preview = ""
        elif not callable(get_preview):
            preview = ""
        else:
            try:
                preview = str(get_preview(do_type) or "")
            except Exception as e:
                preview = f"(Failed to load preview: {e})"

        if callable(get_preview) and do_type and do_type != "(Blank)" and not preview.strip():
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
        new_id = (self.var_id.get() or "").strip()
        cdc = (self.var_cdc.get() or "").strip()
        desc = (self.var_desc.get() or "").strip()
        base_id = (self.var_base.get() or "").strip()

        if not new_id:
            messagebox.showerror("Missing", "File name is required", parent=self)
            return
        if not cdc:
            messagebox.showerror("Missing", "CDC is required", parent=self)
            return

        self._result = {
            "base_id": base_id,
            "id": new_id,
            "cdc": cdc,
            "desc": desc,
        }
        self.destroy()

    def _cancel(self) -> None:
        self._result = None
        self.destroy()

    def show(self) -> dict[str, str] | None:
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

        self._all_btypes = [x for x in (btype_options or []) if (x or "").strip()]
        self._all_enum_ids = [x for x in (enum_type_ids or []) if (x or "").strip()]
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
        self.var_langRef = tk.StringVar(value=(init.get("langRef") or ""))

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

        ttk.Label(frm, text="langRef").grid(row=9, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=self.var_langRef, width=16).grid(row=9, column=1, sticky="w", pady=4)

        preview_box = ttk.Frame(frm)
        preview_box.grid(row=10, column=0, columnspan=2, sticky="nsew", pady=(10, 0))
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

        frm.rowconfigure(10, weight=1)

        btns = ttk.Frame(frm)
        btns.grid(row=11, column=0, columnspan=2, sticky="e", pady=(12, 0))
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
                filtered = _sort_filter_matches(raw, filtered)

            cur = (self.var_type.get() or "").strip()

            max_show = 1500
            shown = filtered[:max_show]
            try:
                self.cb_enum_type["values"] = tuple(shown)
            except Exception:
                pass
            if raw and filtered:
                if cur != filtered[0]:
                    self.var_type.set(filtered[0])
            elif (not raw) and cur and (cur not in filtered):
                self.var_type.set("")
            suffix = "" if len(filtered) <= max_show else f" (showing first {max_show})"
            try:
                self.lbl_enum_match.configure(text=f"{len(filtered)} match{'' if len(filtered)==1 else 'es'}{suffix}")
            except Exception:
                pass

        def update_val_widget() -> None:
            bt_enum = is_enum()
            enum_id = (self.var_type.get() or "").strip() if bt_enum else ""

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

        lr = (self.var_langRef.get() or "").strip()
        if lr and not re.fullmatch(r"\d+(?:\.\d+)?", lr):
            messagebox.showerror(
                "Invalid Language reference",
                "Language reference must be empty or like 12 or 12.34",
                parent=self,
            )
            return

        self._result = {
            "name": name,
            "fc": fc,
            "bType": bt,
            "type": (self.var_type.get() or "").strip(),
            "valKind": vk,
            "valImport": vi,
            "dchg": (self.var_dchg.get() or "").strip(),
            "val": (self.var_val.get() or ""),
            "langRef": lr,
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

        self._saved_sig_by_name: dict[str, tuple[str, ...]] = {}

        self._UI_ADDED = "__ui_added"
        self._UI_DELETED = "__ui_deleted"

        self._inline: ttk.Entry | None = None
        self._inline_iid: str | None = None
        self._inline_col: str | None = None
        self._inline_started_at: float | None = None

        self.get_enum_type_ids: Callable[[], list[str]] | None = None
        self.get_enum_values: Callable[[str], list[str]] | None = None
        self.get_enum_preview: Callable[[str], str] | None = None

        self.get_btype_options: Callable[[], list[str]] | None = None

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

        cols = ["name", "langRef", "fc", "bType", "type", "valKind", "valImport", "dchg", "val", "desc"]
        self.tree = ttk.Treeview(content, columns=cols, show="headings", selectmode="browse")
        for c in cols:
            self.tree.heading(c, text=c)
            if c in {"name", "fc", "bType", "langRef"}:
                if c == "fc":
                    self.tree.column(c, width=52, anchor="w", stretch=False)
                elif c == "langRef":
                    self.tree.column(c, width=90, anchor="w", stretch=False)
                else:
                    self.tree.column(c, width=120, anchor="w")
            elif c in {"valKind", "valImport", "dchg"}:
                self.tree.column(c, width=90, anchor="w")
            elif c == "type":
                self.tree.column(c, width=220, anchor="w")
            elif c == "val":
                self.tree.column(c, width=220, anchor="w")
            else:
                self.tree.column(c, width=320, anchor="w")

        content.columnconfigure(0, weight=1)
        content.rowconfigure(0, weight=1)

        y = ttk.Scrollbar(content, orient="vertical", command=self.tree.yview)
        x = ttk.Scrollbar(content, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=y.set, xscrollcommand=x.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        y.grid(row=0, column=1, sticky="ns")
        x.grid(row=1, column=0, columnspan=2, sticky="ew")

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

    def _row_sig(self, r: dict[str, str]) -> tuple[str, ...]:
        keys = ["name", "langRef", "fc", "bType", "type", "valKind", "valImport", "dchg", "val", "desc"]
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

    def _row_tags(self, r: dict[str, str]) -> tuple[str, ...]:
        if self._row_is_deleted(r):
            return ("removed",)
        if self._row_is_added(r):
            return ("added",)
        return ("changed",) if self._row_is_changed(r) else ()

    def mark_saved(self) -> None:
        try:
            self.rows = [r for r in (self.rows or []) if not self._row_is_deleted(r)]
            for r in (self.rows or []):
                r.pop(self._UI_ADDED, None)
                r.pop(self._UI_DELETED, None)
        except Exception:
            pass

        self._saved_sig_by_name = self._snapshot_sig_by_name()
        self.refresh()

    def mark_all_rows_added(self) -> None:
        for r in (self.rows or []):
            try:
                r[self._UI_ADDED] = "1"
                r.pop(self._UI_DELETED, None)
            except Exception:
                pass
        self.refresh()

    def commit_any_edit(self) -> None:
        try:
            self._end_inline_edit(commit=True)
        except Exception:
            pass

    def _fire_change(self) -> None:
        cb = getattr(self, "on_change", None)
        if cb is None:
            return
        try:
            cb()
        except Exception:
            pass

    def set_rows(self, rows: list[dict[str, str]]) -> None:
        self.rows = [self._strip_ui_flags(dict(r)) for r in (rows or [])]
        self._undo_stack = []
        self._saved_sig_by_name = self._snapshot_sig_by_name()
        self.refresh()
        self._fire_change()

    def get_rows(self) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for r in (self.rows or []):
            if self._row_is_deleted(r):
                continue
            out.append(self._strip_ui_flags(r))
        return out

    def refresh(self) -> None:
        for item in self.tree.get_children(""):
            self.tree.delete(item)
        cols = ["name", "langRef", "fc", "bType", "type", "valKind", "valImport", "dchg", "val", "desc"]
        for idx, row in enumerate(self.rows):
            self.tree.insert(
                "",
                "end",
                iid=str(idx),
                values=[row.get(c, "") for c in cols],
                tags=self._row_tags(row),
            )

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
            "langRef": "",
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
            "langRef": "",
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
                "langRef": (res.get("langRef") or "").strip(),
                "desc": (res.get("desc") or ""),
            }
        )

        if (bt or "").strip().upper() == "ENUM":
            if not typ:
                new_row["val"] = ""
            else:
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
        try:
            if self.get_btype_options is not None:
                opts = [str(x) for x in (self.get_btype_options() or [])]
                opts = [o.strip() for o in opts if (o or "").strip()]
                if opts:
                    return opts
        except Exception:
            pass

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
            return bool(enum_id)
        return False

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

        # UX rule: for ENUM DA rows, double-clicking "type" should open
        # the full Edit dialog instead of inline dropdown editing.
        try:
            row = self._row_by_iid(iid)
            if row is not None:
                bt = (row.get("bType") or "").strip().upper()
                if bt == "ENUM" and col_name == "type":
                    self.edit_selected()
                    return
        except Exception:
            pass

        if self._is_dropdown_cell(iid, col_name):
            return
        self._begin_inline_edit(iid, col_name)

    def _on_left_click(self, event: tk.Event) -> str | None:
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

            # UX rule: enum type is edited from dialog on double-click;
            # single-click should not open inline dropdown.
            try:
                row = self._row_by_iid(iid)
                if row is not None:
                    bt = (row.get("bType") or "").strip().upper()
                    if bt == "ENUM" and col_name == "type":
                        return None
            except Exception:
                pass

            if not self._is_dropdown_cell(iid, col_name):
                return None

            if isinstance(self._inline, ttk.Combobox) and self._inline_iid == iid and self._inline_col == col_name:
                self._combobox_toggle_posted(self._inline)
                return "break"

            self._begin_inline_edit(iid, col_name)
            return "break"
        except Exception:
            return None

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
        try:
            widget = event.widget
        except Exception:
            widget = None

        if not isinstance(widget, ttk.Combobox):
            self._end_inline_edit(commit=True)
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
                        self._end_inline_edit(commit=True)
                        if self._inline is cb and not self._combobox_is_posted(cb)
                        else None
                    ),
                )
            except Exception:
                pass
            return

        self._end_inline_edit(commit=True)

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

        if col_name == "bType":
            opts = list(self._btype_options())
            cur0 = (current or "").strip()
            if cur0 and cur0 not in opts:
                opts = [cur0] + opts

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

        if col_name == "fc":
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

        if col_name == "langRef":
            v = (new_val or "").strip()
            if v and not re.fullmatch(r"\d+(?:\.\d+)?", v):
                messagebox.showerror(
                    "Invalid Language reference",
                    "Language reference must be empty or like 12 or 12.34",
                    parent=self,
                )
                return
            if (self.rows[idx].get(col_name) or "") == v:
                return
            self._push_undo()
            self.rows[idx][col_name] = v
            self.refresh()
            try:
                self.tree.selection_set(str(idx))
            except Exception:
                pass
            self._fire_change()
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

            if old_is_enum and not new_is_enum:
                self.rows[idx]["type"] = ""
                self.rows[idx]["val"] = ""
            if new_is_enum:
                enum_id = (self.rows[idx].get("type") or "").strip()
                if not enum_id or (enum_id not in set(self._enum_type_ids())):
                    self.rows[idx]["type"] = ""
                    self.rows[idx]["val"] = ""
        else:
            if (self.rows[idx].get(col_name) or "") == (new_val or ""):
                return
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


class DoTemplateTab(ttk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        workspace_root: Path,
        catalog: TypeCatalog,
        get_btype_options: Callable[[], list[str]] | None = None,
        set_status: Callable[[str], None] | None = None,
        on_do_type_saved: Callable[[str], None] | None = None,
    ):
        super().__init__(parent)
        self.workspace_root = Path(workspace_root)
        self.catalog = catalog
        self._get_btype_options_cb = get_btype_options
        self._set_status_cb = set_status
        self._on_do_type_saved_cb = on_do_type_saved

        self._type_file_cache: dict[tuple[str, str], Path | None] = {}

        self._do_tmpl_file_path: Path | None = None
        self._do_tmpl_root: ET.Element | None = None
        self._do_tmpl_dotype: ET.Element | None = None
        self._do_tmpl_child_specs: list[tuple[str, object]] = []
        self._do_tmpl_da_elements: list[ET.Element] = []

        self._do_tmpl_id = tk.StringVar(value="")
        self._do_tmpl_cdc = tk.StringVar(value="")
        self._do_tmpl_desc = tk.StringVar(value="")
        self._do_tmpl_private_enabled = tk.BooleanVar(value=False)

        self._do_tmpl_table: DATable | None = None

        self._do_tmpl_saved_sig: str | None = None
        self._do_tmpl_loading: bool = False

        self._do_tmpl_cdc_cb: ttk.Combobox | None = None
        self.btn_save: ttk.Button | None = None

        self._do_cdc_types_cache: list[str] | None = None
        self._do_cdc_qt_presence_cache: dict[str, tuple[int, int, int]] | None = None

        self._do_tmpl_details_nb: ttk.Notebook | None = None

        # Search UI state
        self._all_do_tmpl_files: list[str] = []
        self.var_do_tmpl_filter = tk.StringVar(value="")
        self.var_do_tmpl_selected = tk.StringVar(value="")
        self.cb_do_tmpl: ttk.Combobox | None = None
        self.lbl_do_tmpl_match: ttk.Label | None = None

        # Language reference UI state
        self.var_do_tmpl_lang_filter = tk.StringVar(value="")
        self.lbl_do_tmpl_lang_match: ttk.Label | None = None
        self._do_tmpl_lang_tree: ttk.Treeview | None = None
        self._do_tmpl_lang_rows_all: list[dict[str, str]] = []
        self._do_tmpl_lang_rows_filtered: list[dict[str, str]] = []
        self._do_tmpl_lang_inline: ttk.Entry | None = None
        self._do_tmpl_lang_inline_iid: str | None = None
        self._do_tmpl_lang_saved_by_name: dict[str, str] = {}

        self._build_ui()

        try:
            self._do_tmpl_id.trace_add("write", lambda *_args: self._on_view_changed())
            self._do_tmpl_cdc.trace_add("write", lambda *_args: self._on_view_changed())
            self._do_tmpl_desc.trace_add("write", lambda *_args: self._on_view_changed())
            self._do_tmpl_private_enabled.trace_add("write", lambda *_args: self._on_view_changed())
        except Exception:
            pass

        self.refresh_search_list(select_rel=None)
        self.mark_saved()

    # --- UI ---

    def _do_type_dir(self) -> Path:
        return self.workspace_root / "ep7_datamodel" / "datamodel" / "iec61850" / "DOType"

    def _enum_type_dir(self) -> Path:
        return self.workspace_root / "ep7_datamodel" / "datamodel" / "iec61850" / "EnumType"

    def _set_status(self, text: str) -> None:
        if self._set_status_cb is None:
            return
        try:
            self._set_status_cb(text)
        except Exception:
            pass

    def _ensure_dirty_button_style(self) -> None:
        try:
            style = ttk.Style(self)
            style.configure("Dirty.TButton", foreground="#C00000")
        except Exception:
            pass

    def _set_save_button_dirty(self, *, dirty: bool) -> None:
        if self.btn_save is None:
            return
        self._ensure_dirty_button_style()
        try:
            if dirty:
                self.btn_save.configure(text="Save *", style="Dirty.TButton")
            else:
                self.btn_save.configure(text="Save", style="TButton")
        except Exception:
            pass

    def _build_ui(self) -> None:
        toolbar = ttk.Frame(self, padding=(10, 10, 10, 0))
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="New", command=self.new_do_template_dialog).pack(side="left")
        ttk.Button(toolbar, text="Open", command=self.open_do_template).pack(side="left", padx=(8, 0))
        self.btn_save = ttk.Button(toolbar, text="Save", command=self.save_do_template)
        self.btn_save.pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="Save As", command=self.save_do_template_as).pack(side="left", padx=(8, 0))

        row2 = ttk.Frame(self, padding=(10, 8, 10, 0))
        row2.pack(fill="x")
        ttk.Label(row2, text="Search").pack(side="left")
        ent_filter = ttk.Entry(row2, textvariable=self.var_do_tmpl_filter, width=28)
        ent_filter.pack(side="left", padx=(8, 0))

        self.cb_do_tmpl = ttk.Combobox(row2, textvariable=self.var_do_tmpl_selected, values=[], width=66)
        self.cb_do_tmpl.pack(side="left", padx=(10, 0))

        def _open_search_dropdown(_event: tk.Event | None = None) -> None:
            try:
                self.cb_do_tmpl.focus_set()
            except Exception:
                pass
            try:
                self.cb_do_tmpl.after_idle(lambda: self.cb_do_tmpl.event_generate("<Down>"))
            except Exception:
                pass

        try:
            self.cb_do_tmpl.bind("<Button-1>", _open_search_dropdown, add="+")
        except Exception:
            pass

        ttk.Button(row2, text="Load", command=self.open_do_template_from_search).pack(side="left", padx=(8, 0))

        self.lbl_do_tmpl_match = ttk.Label(row2, text="")
        self.lbl_do_tmpl_match.pack(side="left", padx=(10, 0))

        body = ttk.Frame(self, padding=10)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)

        meta = ttk.LabelFrame(body, text="DOType", padding=10)
        meta.grid(row=0, column=0, sticky="we")
        for col in (1, 3, 5):
            meta.columnconfigure(col, weight=1)

        ttk.Label(meta, text="id").grid(row=0, column=0, sticky="w")
        ttk.Entry(meta, textvariable=self._do_tmpl_id, width=42).grid(row=0, column=1, sticky="we", padx=(6, 12))
        ttk.Label(meta, text="cdc").grid(row=0, column=2, sticky="w")
        self._do_tmpl_cdc_cb = ttk.Combobox(
            meta,
            textvariable=self._do_tmpl_cdc,
            values=self._get_do_cdc_types(),
            width=12,
        )
        try:
            self._do_tmpl_cdc_cb.configure(state="readonly")
        except Exception:
            pass
        self._do_tmpl_cdc_cb.grid(row=0, column=3, sticky="w", padx=(6, 12))
        ttk.Label(meta, text="desc").grid(row=0, column=4, sticky="w")
        ttk.Entry(meta, textvariable=self._do_tmpl_desc, width=52).grid(row=0, column=5, sticky="we", padx=(6, 0))

        ttk.Checkbutton(
            meta,
            text="Private",
            variable=self._do_tmpl_private_enabled,
            command=self._on_private_toggle,
        ).grid(row=1, column=0, sticky="w", pady=(8, 0))

        self._do_tmpl_details_nb = None

        self._do_tmpl_table = DATable(body)
        self._do_tmpl_table.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        try:
            self._do_tmpl_table.on_change = lambda: self._on_view_changed()
        except Exception:
            pass

        # Providers (Enum + bType)
        try:
            self._do_tmpl_table.get_enum_type_ids = lambda: list(self.catalog.enum_types)
            self._do_tmpl_table.get_enum_values = lambda enum_id: self._enum_values_for_enum_type(enum_id)
            self._do_tmpl_table.get_enum_preview = lambda enum_id: self._enum_type_preview_text(enum_id)
            self._do_tmpl_table.get_btype_options = lambda: self._all_btype_options()
        except Exception:
            pass

        self.lbl_do_tmpl_lang_match = None
        self._do_tmpl_lang_tree = None

        try:
            if self.cb_do_tmpl is not None:
                self.cb_do_tmpl.bind("<Return>", lambda _e: self.open_do_template_from_search())
        except Exception:
            pass

    # --- Dirty tracking ---

    def _signature_from_view(self) -> str:
        do_id = (self._do_tmpl_id.get() or "").strip()
        cdc = (self._do_tmpl_cdc.get() or "").strip()
        desc = (self._do_tmpl_desc.get() or "")
        priv = bool(self._do_tmpl_private_enabled.get())

        rows: list[dict[str, str]] = []
        if self._do_tmpl_table is not None:
            try:
                self._do_tmpl_table.commit_any_edit()
            except Exception:
                pass
            try:
                rows = self._do_tmpl_table.get_rows()
            except Exception:
                rows = []

        keys = ["name", "fc", "bType", "type", "valKind", "valImport", "dchg", "val", "langRef", "desc"]
        norm_rows = [tuple((r.get(k) or "") for k in keys) for r in (rows or [])]
        return repr((do_id, cdc, desc, priv, norm_rows))

    def update_dirty_ui(self) -> None:
        if getattr(self, "_do_tmpl_loading", False):
            return
        cur = self._signature_from_view()
        dirty = (self._do_tmpl_saved_sig is None) or (cur != self._do_tmpl_saved_sig)
        self._set_save_button_dirty(dirty=dirty)

    def _on_view_changed(self) -> None:
        if getattr(self, "_do_tmpl_loading", False):
            return
        self.update_dirty_ui()

    def mark_saved(self) -> None:
        try:
            self._do_tmpl_saved_sig = self._signature_from_view()
        except Exception:
            self._do_tmpl_saved_sig = ""
        try:
            if self._do_tmpl_table is not None:
                self._do_tmpl_table.mark_saved()
        except Exception:
            pass

        try:
            base: dict[str, str] = {}
            if self._do_tmpl_table is not None:
                for r in (self._do_tmpl_table.get_rows() or []):
                    nm = (r.get("name") or "").strip()
                    if not nm:
                        continue
                    base[nm] = (r.get("langRef") or "").strip()
            self._do_tmpl_lang_saved_by_name = base
        except Exception:
            self._do_tmpl_lang_saved_by_name = {}

        self.update_dirty_ui()

    def mark_unsaved(self) -> None:
        self._do_tmpl_saved_sig = None
        self.update_dirty_ui()

    # --- Search ---

    def refresh_search_list(self, *, select_rel: str | None) -> None:
        if self.cb_do_tmpl is None or self.lbl_do_tmpl_match is None:
            return
        do_dir = self._do_type_dir()
        self._all_do_tmpl_files = scan_xml_relpaths(do_dir)

        def apply_filter(*_args) -> None:
            raw = (self.var_do_tmpl_filter.get() or "").strip().lower()
            if not raw:
                filtered = list(self._all_do_tmpl_files)
            else:
                tokens = [t for t in raw.split() if t]

                def ok(v: str) -> bool:
                    lv = (v or "").lower()
                    return all(t in lv for t in tokens)

                filtered = [v for v in self._all_do_tmpl_files if ok(v)]
                filtered = _sort_filter_matches(raw, filtered)

            cur = (self.var_do_tmpl_selected.get() or "").strip()

            max_show = 1200
            shown = filtered[:max_show]
            self.cb_do_tmpl["values"] = shown
            if raw and filtered:
                if cur != filtered[0]:
                    self.var_do_tmpl_selected.set(filtered[0])
            elif (not raw) and cur and (cur not in filtered):
                self.var_do_tmpl_selected.set("")
            suffix = "" if len(filtered) <= max_show else f" (showing first {max_show})"
            self.lbl_do_tmpl_match.configure(text=f"{len(filtered)} match{'' if len(filtered)==1 else 'es'}{suffix}")

        if getattr(self, "_do_tmpl_apply_filter", None) is None:
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

    # --- Language reference ---

    def _on_details_tab_changed(self) -> None:
        try:
            if self._do_tmpl_details_nb is None:
                return
            current = self._do_tmpl_details_nb.select()
            tab_text = self._do_tmpl_details_nb.tab(current, "text")
            if tab_text == "Language reference":
                self.refresh_language_reference()
        except Exception:
            pass

    def _clear_lang_filter(self) -> None:
        try:
            self.var_do_tmpl_lang_filter.set("")
        except Exception:
            pass
        self._apply_lang_filter()

    def _langref_private_type(self) -> str:
        return "SchneiderElectric-PowerLogic-LangRef"

    def refresh_language_reference(self) -> None:
        if self._do_tmpl_lang_tree is None:
            return

        try:
            self._do_tmpl_lang_tree.tag_configure("added", background="honeydew2")
            self._do_tmpl_lang_tree.tag_configure("removed", background="misty rose")
            self._do_tmpl_lang_tree.tag_configure("changed", background="lemon chiffon")
        except Exception:
            pass

        try:
            if self._do_tmpl_table is not None:
                self._do_tmpl_table.commit_any_edit()
        except Exception:
            pass

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

            rows.append({"iid": str(idx), "name": name, "id": lang_id, "desc": desc_txt})

        self._do_tmpl_lang_rows_all = rows
        self._apply_lang_filter()

    def _apply_lang_filter(self) -> None:
        if self._do_tmpl_lang_tree is None:
            return

        flt = ""
        try:
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
                            saved_lr = (self._do_tmpl_lang_saved_by_name or {}).get(nm)
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
                self.lbl_do_tmpl_lang_match.configure(text=f"{len(filtered)}/{len(self._do_tmpl_lang_rows_all or [])}")
        except Exception:
            pass

    def _on_lang_left_click(self, _evt=None) -> None:
        try:
            self._end_lang_inline(commit=True)
        except Exception:
            pass

    def _on_lang_double_click(self, evt) -> None:
        if self._do_tmpl_lang_tree is None:
            return
        try:
            iid = self._do_tmpl_lang_tree.identify_row(evt.y)
            col = self._do_tmpl_lang_tree.identify_column(evt.x)
        except Exception:
            return

        if not iid or col != "#2":
            return

        self._start_lang_inline(iid)

    def _start_lang_inline(self, iid: str) -> None:
        if self._do_tmpl_lang_tree is None:
            return
        self._end_lang_inline(commit=True)

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

        ent.bind("<Return>", lambda _e: self._end_lang_inline(commit=True))
        ent.bind("<Escape>", lambda _e: self._end_lang_inline(commit=False))
        ent.bind("<FocusOut>", lambda _e: self._end_lang_inline(commit=True))
        ent.bind("<Control-z>", lambda _e: (self._lang_undo(), "break")[1])
        ent.bind("<Control-Z>", lambda _e: (self._lang_undo(), "break")[1])

    def _end_lang_inline(self, commit: bool) -> None:
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
            self.refresh_language_reference()
        except Exception:
            pass

        try:
            self._on_view_changed()
        except Exception:
            pass

    def _lang_undo(self) -> None:
        if self._do_tmpl_table is None:
            return
        try:
            self._end_lang_inline(commit=False)
        except Exception:
            pass
        try:
            self._do_tmpl_table.undo()
        except Exception:
            return
        try:
            self.refresh_language_reference()
        except Exception:
            pass

    # --- Enum integration for DATable ---

    def _enum_values_for_enum_type(self, enum_type_id: str) -> list[str]:
        enum_type_id = (enum_type_id or "").strip()
        if not enum_type_id:
            return []

        enum_dir = self._enum_type_dir()
        path = find_type_file(kind_dir=enum_dir, type_id=enum_type_id, cache=self._type_file_cache)
        if path is None:
            return []

        try:
            root = ET.parse(path).getroot()
        except Exception:
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

        if items and all(o is not None for o, _t in items):
            items.sort(key=lambda x: int(x[0] or 0))

        return [t for _o, t in items]

    def _enum_type_preview_text(self, enum_type_id: str) -> str:
        enum_type_id = (enum_type_id or "").strip()
        if not enum_type_id:
            return ""

        enum_dir = self._enum_type_dir()
        path = find_type_file(kind_dir=enum_dir, type_id=enum_type_id, cache=self._type_file_cache)
        if path is None:
            return ""

        try:
            root = ET.parse(path).getroot()
        except Exception:
            return ""

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
            return ""

        def strip_ns(tag: str) -> str:
            if not isinstance(tag, str):
                return str(tag)
            if "}" in tag:
                return tag.split("}", 1)[1]
            return tag

        def fmt_attrs(attrib: dict) -> str:
            if not attrib:
                return ""
            parts: list[str] = []
            for k in sorted(attrib.keys()):
                v = attrib.get(k)
                if v is None:
                    continue
                parts.append(f'{k}="{v}"')
            return (" " + " ".join(parts)) if parts else ""

        max_lines = 400
        lines: list[str] = []

        def render(el: ET.Element, level: int = 0) -> None:
            if len(lines) >= max_lines:
                return
            tag = strip_ns(el.tag)
            attrs = fmt_attrs(getattr(el, "attrib", {}) or {})
            children = list(el)
            text = (el.text or "").strip()
            indent = "  " * level

            if not children and not text:
                lines.append(f"{indent}<{tag}{attrs} />")
                return

            if not children and text:
                lines.append(f"{indent}<{tag}{attrs}>{text}</{tag}>")
                return

            lines.append(f"{indent}<{tag}{attrs}>")
            if text:
                lines.append(f"{indent}  {text}")
            for ch in children:
                if len(lines) >= max_lines:
                    break
                render(ch, level + 1)
            lines.append(f"{indent}</{tag}>")

        render(enum_el, 0)
        if len(lines) >= max_lines:
            lines = lines[: max_lines - 1] + ["...(truncated)..."]

        return "\n".join(lines) + "\n"

    def _all_btype_options(self) -> list[str]:
        cb = self._get_btype_options_cb
        if cb is None:
            return []
        try:
            return list(cb() or [])
        except Exception:
            return []

    # --- DO CDC scanning ---

    def _scan_do_cdc_types(self) -> list[str]:
        do_dir = self._do_type_dir()
        if not do_dir.exists():
            return []
        types: set[str] = set()
        for rel in scan_xml_relpaths(do_dir):
            p = do_dir / rel
            try:
                root = ET.parse(p).getroot()
            except Exception:
                continue
            for el in root.iter():
                if not isinstance(el.tag, str):
                    continue
                if local_name(el.tag) != "DOType":
                    continue
                cdc = (el.attrib.get("cdc") or "").strip()
                if cdc:
                    types.add(cdc.strip().upper())
                break
        return sorted(types, key=lambda s: s.lower())

    def _get_do_cdc_types(self) -> list[str]:
        if self._do_cdc_types_cache is None:
            self._do_cdc_types_cache = self._scan_do_cdc_types()
        return list(self._do_cdc_types_cache)

    def _do_type_cdc_for_id(self, do_type_id: str) -> str:
        do_type_id = (do_type_id or "").strip()
        if not do_type_id:
            return ""
        do_dir = self._do_type_dir()
        path = find_type_file(kind_dir=do_dir, type_id=do_type_id, cache=self._type_file_cache)
        if path is None:
            return ""
        try:
            root = ET.parse(path).getroot()
        except Exception:
            return ""
        for el in root.iter():
            if isinstance(el.tag, str) and local_name(el.tag) == "DOType":
                return (el.attrib.get("cdc") or "").strip().upper()
        return ""

    def _do_type_preview_text(self, do_type_id: str) -> str:
        do_type_id = (do_type_id or "").strip()
        if not do_type_id:
            return ""

        do_dir = self._do_type_dir()
        path = find_type_file(kind_dir=do_dir, type_id=do_type_id, cache=self._type_file_cache)
        if path is None:
            return ""
        try:
            root = ET.parse(path).getroot()
        except Exception:
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
            return ""

        def strip_ns(tag: str) -> str:
            if not isinstance(tag, str):
                return str(tag)
            if "}" in tag:
                return tag.split("}", 1)[1]
            return tag

        def fmt_attrs(attrib: dict) -> str:
            if not attrib:
                return ""
            parts: list[str] = []
            for k in sorted(attrib.keys()):
                v = attrib.get(k)
                if v is None:
                    continue
                parts.append(f'{k}="{v}"')
            return (" " + " ".join(parts)) if parts else ""

        max_lines = 400
        lines: list[str] = []

        def render(el: ET.Element, level: int = 0) -> None:
            if len(lines) >= max_lines:
                return
            tag = strip_ns(el.tag)
            attrs = fmt_attrs(getattr(el, "attrib", {}) or {})
            children = list(el)
            text = (el.text or "").strip()
            indent = "  " * level

            if not children and not text:
                lines.append(f"{indent}<{tag}{attrs} />")
                return

            if not children and text:
                lines.append(f"{indent}<{tag}{attrs}>{text}</{tag}>")
                return

            lines.append(f"{indent}<{tag}{attrs}>")
            if text:
                lines.append(f"{indent}  {text}")
            for ch in children:
                if len(lines) >= max_lines:
                    break
                render(ch, level + 1)
            lines.append(f"{indent}</{tag}>")

        render(do_el, 0)
        if len(lines) >= max_lines:
            lines = lines[: max_lines - 1] + ["...(truncated)..."]

        return "\n".join(lines) + "\n"

    def _scan_do_cdc_qt_presence(self) -> dict[str, tuple[int, int, int]]:
        do_dir = self._do_type_dir()
        if not do_dir.exists():
            return {}

        stats: dict[str, list[int]] = {}
        for rel in scan_xml_relpaths(do_dir):
            p = do_dir / rel
            try:
                root = ET.parse(p).getroot()
            except Exception:
                continue

            do_el: ET.Element | None = None
            for el in root.iter():
                if isinstance(el.tag, str) and local_name(el.tag) == "DOType":
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
                    if not isinstance(child.tag, str) or local_name(child.tag) != "DA":
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
        if self._do_cdc_qt_presence_cache is None:
            self._do_cdc_qt_presence_cache = self._scan_do_cdc_qt_presence()
        return dict(self._do_cdc_qt_presence_cache)

    def _default_new_da_names_for_cdc(self, cdc: str) -> list[str]:
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

        if q_all and t_all:
            return ["q", "t", "d"]
        if t_all and not q_any:
            return ["t", "d"]
        return names

    # --- New/Open/Save ---

    def new_do_template_dialog(self) -> None:
        do_ids = list(self.catalog.do_types) if self.catalog is not None else []
        cdc_values = self._get_do_cdc_types()

        dlg = NewDOTypeDialog(
            self,
            do_type_ids=do_ids,
            cdc_values=cdc_values,
            get_base_cdc=self._do_type_cdc_for_id,
            get_do_type_preview=self._do_type_preview_text,
        )
        res = dlg.show()
        if not res:
            return

        base_id = (res.get("base_id") or "").strip()
        new_id = (res.get("id") or "").strip()
        new_cdc = (res.get("cdc") or "").strip().upper()
        new_desc = res.get("desc") or ""

        if base_id and base_id != "(Blank)":
            do_dir = self._do_type_dir()
            src_path = find_type_file(kind_dir=do_dir, type_id=base_id, cache=self._type_file_cache)
            if src_path is None:
                messagebox.showerror("Missing", f"Source DOType not found: {base_id}", parent=self)
                return
            self.open_do_template_from_path(src_path)
            self._do_tmpl_file_path = None
            try:
                self.var_do_tmpl_selected.set("")
            except Exception:
                pass
            try:
                if self._do_tmpl_table is not None:
                    self._do_tmpl_table.mark_all_rows_added()
            except Exception:
                pass
        else:
            self._new_do_template(default_cdc=new_cdc)

        try:
            self._do_tmpl_id.set(new_id)
            self._do_tmpl_cdc.set(new_cdc)
            self._do_tmpl_desc.set(new_desc)
        except Exception:
            pass

        self.mark_unsaved()

        try:
            if self._do_tmpl_cdc_cb is not None:
                self._do_tmpl_cdc_cb["values"] = self._get_do_cdc_types()
        except Exception:
            pass

        self._set_status(
            (
                f"New DO template created from {base_id} (unsaved)"
                if base_id and base_id != "(Blank)"
                else "New DO template created (unsaved)"
            )
        )

    def _new_do_template(self, *, default_cdc: str = "") -> None:
        self._do_tmpl_file_path = None
        self._do_tmpl_root = None
        self._do_tmpl_dotype = None
        self._do_tmpl_child_specs = []
        self._do_tmpl_da_elements = []
        if self._do_tmpl_table is None:
            return

        ET.register_namespace("", SCL_NS)
        ET.register_namespace("xsi", "http://www.w3.org/2001/XMLSchema-instance")
        ET.register_namespace("xsd", "http://www.w3.org/2001/XMLSchema")

        root = ET.Element(qname(SCL_NS, "SCL"))
        root.attrib[qname("http://www.w3.org/2001/XMLSchema-instance", "schemaLocation")] = f"{SCL_NS} SCL.xsd"
        do_el = ET.SubElement(root, qname(SCL_NS, "DOType"))
        do_el.attrib["id"] = ""
        do_el.attrib["cdc"] = (default_cdc or "").strip().upper()
        do_el.attrib["desc"] = ""

        chosen_names = self._default_new_da_names_for_cdc(do_el.attrib.get("cdc") or "")
        da_elements: list[ET.Element] = []
        table_rows: list[dict[str, str]] = []

        def add_q() -> None:
            da = ET.Element(qname(SCL_NS, "DA"))
            da.attrib.update({"name": "q", "fc": "ST", "bType": "Quality", "qchg": "true", "desc": "The quality of the value"})
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
            da = ET.Element(qname(SCL_NS, "DA"))
            da.attrib.update({"name": "t", "fc": "ST", "bType": "Timestamp", "desc": "Timestamp of the last change in state"})
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
            da = ET.Element(qname(SCL_NS, "DA"))
            da.attrib.update({"name": "d", "fc": "DC", "bType": "VisString255", "valKind": "RO", "valImport": "false", "desc": "English label"})
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
                self.refresh_language_reference()
            except Exception:
                pass
            try:
                self._do_tmpl_private_enabled.set(False)
            except Exception:
                pass
            try:
                self.var_do_tmpl_selected.set("")
            except Exception:
                pass
        finally:
            self._do_tmpl_loading = False

        try:
            if self._do_tmpl_table is not None:
                self._do_tmpl_table.mark_all_rows_added()
        except Exception:
            pass

        self.mark_unsaved()
        self._set_status("New DO template created (unsaved)")

    def _on_private_toggle(self) -> None:
        if self._do_tmpl_table is None:
            return

        enabled = bool(self._do_tmpl_private_enabled.get())
        if not enabled:
            try:
                rows0 = self._do_tmpl_table.get_rows()
            except Exception:
                rows0 = []
            rows = [r for r in rows0 if (r.get("name") or "").strip() != "dataNs"]
            if len(rows) != len(rows0):
                self._do_tmpl_table.set_rows(rows)
                try:
                    self._do_tmpl_child_specs = [x for x in (self._do_tmpl_child_specs or []) if x[0] == "ELEM"]
                except Exception:
                    pass
                try:
                    self._apply_ui_to_xml()
                except Exception:
                    pass
                try:
                    self._on_view_changed()
                except Exception:
                    pass
            return

        if self._do_tmpl_root is None or self._do_tmpl_dotype is None:
            self._new_do_template()
        if self._do_tmpl_root is None or self._do_tmpl_dotype is None:
            return

        try:
            self._apply_ui_to_xml()
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

        da_el = ET.Element(qname(ns, "DA"))
        da_el.attrib.update({"name": "dataNs", "fc": "EX", "bType": "VisString255", "desc": "Private name space"})
        v = ET.SubElement(da_el, qname(ns, "Val"))
        v.text = "SE_PowerLogic_dataNs_V001:2016"
        self._do_tmpl_da_elements.append(da_el)
        try:
            self._on_view_changed()
        except Exception:
            pass

    def open_do_template_from_path(self, path: Path) -> None:
        path = Path(path)
        if self._do_tmpl_table is None:
            return

        try:
            root = ET.parse(path).getroot()
        except Exception as e:
            messagebox.showerror("Open failed", str(e), parent=self)
            return

        do_el = None
        for cand in root.iter():
            if not isinstance(cand.tag, str):
                continue
            if local_name(cand.tag) == "DOType":
                do_el = cand
                break
        if do_el is None:
            messagebox.showerror("Invalid", "No <DOType> found in file", parent=self)
            return

        specs: list[tuple[str, object]] = []
        da_elems: list[ET.Element] = []
        da_rows: list[dict[str, str]] = []
        for ch in list(do_el):
            if not isinstance(ch.tag, str):
                specs.append(("ELEM", deepcopy_et_element(ch)))
                continue
            if local_name(ch.tag) != "DA":
                if local_name(ch.tag) == "Private":
                    t = (ch.attrib.get("type") or "").strip()
                    if (
                        t == "SchneiderElectric-PowerLogic-PrivateDOType"
                        and not list(ch)
                        and not (ch.text or "").strip()
                    ):
                        continue
                specs.append(("ELEM", deepcopy_et_element(ch)))
                continue

            idx = len(da_elems)
            da_copy = deepcopy_et_element(ch)
            da_elems.append(da_copy)
            specs.append(("DA", idx))

            val_text = ""
            lang_ref = ""
            try:
                for sub in list(ch):
                    if isinstance(sub.tag, str) and local_name(sub.tag) == "Val":
                        val_text = sub.text or ""
                    if isinstance(sub.tag, str) and local_name(sub.tag) == "Private":
                        if (sub.attrib.get("type") or "").strip() == self._langref_private_type():
                            lang_ref = (sub.text or "").strip()
            except Exception:
                val_text = ""
            val_text = (val_text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\n", " ")

            da_rows.append(
                {
                    "name": (ch.attrib.get("name") or ""),
                    "fc": (ch.attrib.get("fc") or ""),
                    "bType": (ch.attrib.get("bType") or ""),
                    "type": (ch.attrib.get("type") or ""),
                    "valKind": (ch.attrib.get("valKind") or ""),
                    "valImport": (ch.attrib.get("valImport") or ""),
                    "dchg": (ch.attrib.get("dchg") or ""),
                    "val": val_text,
                    "langRef": lang_ref,
                    "desc": (ch.attrib.get("desc") or ""),
                }
            )

        self._do_tmpl_root = root
        self._do_tmpl_dotype = do_el
        self._do_tmpl_child_specs = specs
        self._do_tmpl_da_elements = da_elems

        self._do_tmpl_loading = True
        try:
            self._do_tmpl_id.set((do_el.attrib.get("id") or "").strip())
            self._do_tmpl_cdc.set((do_el.attrib.get("cdc") or "").strip())
            self._do_tmpl_desc.set((do_el.attrib.get("desc") or ""))

            try:
                if self._do_tmpl_cdc_cb is not None:
                    self._do_tmpl_cdc_cb["values"] = self._get_do_cdc_types()
            except Exception:
                pass

            self._do_tmpl_table.set_rows(da_rows)
            try:
                self._do_tmpl_private_enabled.set(any((r.get("name") or "").strip() == "dataNs" for r in da_rows))
            except Exception:
                pass

            self._do_tmpl_file_path = path
        finally:
            self._do_tmpl_loading = False

        try:
            do_dir = self._do_type_dir()
            rel = os.fspath(path.resolve().relative_to(do_dir.resolve()))
            self.var_do_tmpl_selected.set(rel)
            self.refresh_search_list(select_rel=rel)
        except Exception:
            self.refresh_search_list(select_rel=None)

        try:
            self.refresh_language_reference()
        except Exception:
            pass

        self.mark_saved()
        self._set_status(f"Opened DO template: {os.fspath(path)}")

    def open_do_template(self) -> None:
        do_dir = self._do_type_dir()
        initialdir = do_dir if do_dir.exists() else self.workspace_root
        target = filedialog.askopenfilename(
            parent=self,
            title="Open DO template file",
            initialdir=os.fspath(initialdir),
            filetypes=[("XML", "*.xml"), ("All", "*")],
        )
        if not target:
            return
        self.open_do_template_from_path(Path(target))

    def open_do_template_from_search(self) -> None:
        rel = (self.var_do_tmpl_selected.get() or "").strip()
        if not rel:
            return
        do_dir = self._do_type_dir()
        target = do_dir / rel
        if not target.exists():
            messagebox.showerror("Missing", f"File not found:\n\n{os.fspath(target)}", parent=self)
            return
        self.open_do_template_from_path(target)

    def save_do_template(self) -> None:
        if self._do_tmpl_table is None:
            return

        try:
            self._end_lang_inline(commit=True)
        except Exception:
            pass
        try:
            self._do_tmpl_table.commit_any_edit()
        except Exception:
            pass

        do_id = (self._do_tmpl_id.get() or "").strip()
        if not do_id:
            messagebox.showerror("Missing", "DOType id is required (used as file name)", parent=self)
            return

        rows = self._do_tmpl_table.get_rows()
        for r in rows:
            name = (r.get("name") or "").strip() or "(unnamed)"
            fc = (r.get("fc") or "").strip()
            if not fc:
                messagebox.showerror("Missing", f"FC is required for DA: {name}", parent=self)
                return
            bt = (r.get("bType") or "").strip()
            if not bt:
                messagebox.showerror("Missing", f"bType is required for DA: {name}", parent=self)
                return

        stem = re.sub(r"[<>:\"/\\|?*]", "_", do_id).strip() or "DOType"
        target_path = self._do_type_dir() / f"{stem}.xml"

        try:
            self._apply_ui_to_xml()
            try:
                da_count = sum(1 for k, _p in (self._do_tmpl_child_specs or []) if k == "DA")
                if da_count != len(rows):
                    self._do_tmpl_child_specs = [x for x in (self._do_tmpl_child_specs or []) if x[0] == "ELEM"]
            except Exception:
                pass

            self._write_do_template_xml(target_path)
        except Exception as e:
            messagebox.showerror("Save failed", str(e), parent=self)
            return

        try:
            self._ensure_do_type_in_list(do_id)
        except Exception as e:
            messagebox.showwarning(
                "DoTypeList.xml",
                "DO template was saved, but DoTypeList.xml could not be updated.\n\n" f"{e}",
                parent=self,
            )

        self._do_tmpl_file_path = target_path

        try:
            do_dir = self._do_type_dir()
            rel = os.fspath(target_path.relative_to(do_dir))
        except Exception:
            rel = os.fspath(target_path.name)

        self.refresh_search_list(select_rel=rel)
        self._set_status(f"Saved DO template: {os.fspath(target_path)}")
        self.mark_saved()

        # Notify host so dependent UIs (e.g. LN template DO type dropdown)
        # can pick up new DOType ids immediately without app restart.
        try:
            if self._on_do_type_saved_cb is not None:
                self._on_do_type_saved_cb(do_id)
        except Exception:
            pass

    def save_do_template_as(self) -> None:
        cur_id = (self._do_tmpl_id.get() or "").strip()
        initial = f"{cur_id}_copy" if cur_id else ""
        dlg = _DoTemplateSaveAsDialog(self, initial_id=initial)
        new_id = dlg.show()
        if not new_id:
            return

        stem = re.sub(r"[<>:\"/\\|?*]", "_", new_id).strip() or "DOType"
        target_path = self._do_type_dir() / f"{stem}.xml"

        try:
            cur_path = self._do_tmpl_file_path.resolve() if self._do_tmpl_file_path is not None else None
        except Exception:
            cur_path = self._do_tmpl_file_path

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

        old_id = cur_id
        old_path = self._do_tmpl_file_path
        try:
            self._do_tmpl_id.set(new_id)
        except Exception:
            return

        self.save_do_template()

        try:
            saved_ok = self._do_tmpl_file_path is not None and self._do_tmpl_file_path.resolve() == tgt_resolved
        except Exception:
            saved_ok = self._do_tmpl_file_path == target_path

        if not saved_ok:
            try:
                self._do_tmpl_id.set(old_id)
            except Exception:
                pass
            self._do_tmpl_file_path = old_path

    # --- XML apply/write ---

    def _set_da_langref_id(self, da_el: ET.Element, value: str) -> None:
        value = (value or "").strip()
        ptype = self._langref_private_type()

        ns = ""
        try:
            if isinstance(da_el.tag, str) and da_el.tag.startswith("{"):
                ns = da_el.tag.split("}", 1)[0][1:]
        except Exception:
            ns = ""

        p_el: ET.Element | None = None
        try:
            for ch in list(da_el):
                if not isinstance(ch.tag, str) or local_name(ch.tag) != "Private":
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
            p_el = ET.SubElement(da_el, qname(ns, "Private"))
            p_el.set("type", ptype)
        p_el.text = value

    def _apply_ui_to_xml(self) -> None:
        if self._do_tmpl_table is None:
            return
        if self._do_tmpl_root is None or self._do_tmpl_dotype is None:
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

        rows = self._do_tmpl_table.get_rows()
        new_da_elems: list[ET.Element] = []
        for i, row in enumerate(rows):
            base = None
            if i < len(self._do_tmpl_da_elements):
                base = deepcopy_et_element(self._do_tmpl_da_elements[i])
            if base is None:
                base = ET.Element(qname(ns, "DA"))

            for k in ["name", "fc", "bType", "type", "valKind", "valImport", "dchg", "desc"]:
                val = (row.get(k) or "").strip() if k != "desc" else (row.get(k) or "")
                if val:
                    base.attrib[k] = val
                else:
                    if k in base.attrib:
                        del base.attrib[k]

            raw_val = row.get("val") or ""
            if (raw_val or "").strip():
                val_el = None
                for sub in list(base):
                    if isinstance(sub.tag, str) and local_name(sub.tag) == "Val":
                        val_el = sub
                        break
                if val_el is None:
                    val_el = ET.SubElement(base, qname(ns, "Val"))
                val_el.text = raw_val
            else:
                for sub in list(base):
                    if isinstance(sub.tag, str) and local_name(sub.tag) == "Val":
                        try:
                            base.remove(sub)
                        except Exception:
                            pass

            try:
                self._set_da_langref_id(base, (row.get("langRef") or "").strip())
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

        for ch in list(do_el):
            do_el.remove(ch)

        rows = self._do_tmpl_table.get_rows() if self._do_tmpl_table is not None else []
        if len(self._do_tmpl_da_elements) != len(rows):
            self._apply_ui_to_xml()

        # Keep non-DA children in their original positions, but always map DA slots
        # to the current UI row order. Stored DA indices can become stale after
        # delete/insert operations (especially with duplicate DA names).
        specs = list(self._do_tmpl_child_specs or [])
        if not specs:
            for da in self._do_tmpl_da_elements:
                do_el.append(deepcopy_et_element(da))
        else:
            da_pos = 0
            for kind, payload in specs:
                if kind == "ELEM":
                    try:
                        do_el.append(deepcopy_et_element(payload))  # type: ignore[arg-type]
                    except Exception:
                        continue
                    continue

                if kind != "DA":
                    continue

                if da_pos >= len(self._do_tmpl_da_elements):
                    continue
                do_el.append(deepcopy_et_element(self._do_tmpl_da_elements[da_pos]))
                da_pos += 1

            for extra in self._do_tmpl_da_elements[da_pos:]:
                do_el.append(deepcopy_et_element(extra))

        schema_ns = "http://www.w3.org/2001/XMLSchema"
        xsi_ns = "http://www.w3.org/2001/XMLSchema-instance"

        root.attrib[qname(xsi_ns, "schemaLocation")] = f"{SCL_NS} SCL.xsd"

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

    # --- DoTypeList.xml ---

    def _do_type_list_path(self) -> Path:
        return self._do_type_dir() / "DoTypeList.xml"

    def _ensure_do_type_in_list(self, do_type_id: str) -> None:
        do_type_id = (do_type_id or "").strip()
        if not do_type_id:
            return

        path = self._do_type_list_path()
        if not path.exists():
            raise FileNotFoundError(f"DoTypeList.xml not found: {os.fspath(path)}")

        text = path.read_text(encoding="utf-8", errors="ignore")
        newline = "\r\n" if "\r\n" in text else "\n"

        try:
            root = ET.fromstring(text)
            for el in root.iter():
                if not isinstance(el.tag, str) or local_name(el.tag) != "Type":
                    continue
                if (el.attrib.get("ref") or "").strip() == do_type_id:
                    return
        except Exception:
            pass

        try:
            pat = rf"<Type\b[^>]*\bref\s*=\s*(['\"])\s*{re.escape(do_type_id)}\s*\1"
            if re.search(pat, text, flags=re.IGNORECASE):
                return
        except Exception:
            pass

        max_id = 0
        try:
            root = ET.fromstring(text)
            for el in root.iter():
                if not isinstance(el.tag, str) or local_name(el.tag) != "Type":
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

        indent = "    "
        try:
            m_indent = re.search(r"^[ \t]*<Type\b", text, flags=re.IGNORECASE | re.MULTILINE)
            if m_indent is not None:
                indent = re.match(r"^[ \t]*", m_indent.group(0)).group(0)  # type: ignore[union-attr]
        except Exception:
            indent = "    "

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
