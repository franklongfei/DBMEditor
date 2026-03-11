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

try:
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
except ModuleNotFoundError as e:
    if getattr(e, 'name', None) != 'iec61850_scanner':
        raise
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
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


__all__ = ['LNodeTypeEditor']


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
                filtered = _sort_filter_matches(raw, filtered)

            cur = self.var_type.get().strip()
            max_show = 1500
            shown = filtered[:max_show]
            self.cb["values"] = shown
            if raw and filtered:
                if cur != filtered[0]:
                    self.var_type.set(filtered[0])
            elif (not raw) and cur and (cur not in filtered):
                self.var_type.set("")
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


class DOTable(ttk.Frame):
    def __init__(self, parent: tk.Misc, *, do_types: list[str], get_do_type_preview: Callable[[str], str] | None = None):
        super().__init__(parent)
        self.do_types = do_types
        self._get_do_type_preview = get_do_type_preview
        self.rows: list[DOItem] = []
        self._clipboard: DOItem | None = None
        self._undo_stack: list[list[DOItem]] = []
        self._undo_max = 50

        # Changed-row highlighting (vs last saved snapshot)
        self._saved_sig_by_name: dict[str, tuple] = {}

        # UI-only row states (persist until Save)
        self._UI_ADDED = "__ui_added"
        self._UI_DELETED = "__ui_deleted"

        # Optional callback: invoked when user chooses Rules... on a selected DO.
        # Signature: callback(selected_index: int) -> None
        self.on_rules = None

        self._inline: ttk.Entry | None = None
        self._inline_iid: str | None = None

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
        self._menu.add_command(label="Edit", command=self.edit_selected)
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

        try:
            self.tree.tag_configure("added", background="honeydew2")
            self.tree.tag_configure("removed", background="misty rose")
            self.tree.tag_configure("changed", background="lemon chiffon")
        except Exception:
            pass

    def _row_is_added(self, x: DOItem) -> bool:
        try:
            return bool(getattr(x, self._UI_ADDED, False))
        except Exception:
            return False

    def _row_is_deleted(self, x: DOItem) -> bool:
        try:
            return bool(getattr(x, self._UI_DELETED, False))
        except Exception:
            return False

    def _strip_ui_flags_inplace(self, x: DOItem) -> None:
        try:
            if hasattr(x, self._UI_ADDED):
                delattr(x, self._UI_ADDED)
        except Exception:
            pass
        try:
            if hasattr(x, self._UI_DELETED):
                delattr(x, self._UI_DELETED)
        except Exception:
            pass

        # Also strip UI-only flags from DO-level privates (rules).
        try:
            for p in (getattr(x, "privates", []) or []):
                for flag in ("__ui_added", "__ui_deleted", "__ui_saved_sig"):
                    try:
                        if hasattr(p, flag):
                            delattr(p, flag)
                    except Exception:
                        pass
        except Exception:
            pass

    def _row_tags(self, x: DOItem) -> tuple[str, ...]:
        if self._row_is_deleted(x):
            return ("removed",)
        if self._row_is_added(x):
            return ("added",)
        return ("changed",) if self._row_is_changed(x) else ()

    def _private_sig(self, p: PrivateItem) -> tuple:
        try:
            attrib_items = tuple(sorted((p.attrib or {}).items()))
        except Exception:
            attrib_items = ()
        inner = getattr(p, "inner_xml", "") or ""
        return (attrib_items, inner)

    def _do_sig(self, x: DOItem) -> tuple:
        name = (x.name or "")
        do_type = (x.do_type or "")
        privs = tuple(self._private_sig(p) for p in (getattr(x, "privates", []) or []))
        return (name, do_type, privs)

    def _snapshot_sig_by_name(self) -> dict[str, tuple]:
        out: dict[str, tuple] = {}
        for x in (self.rows or []):
            if self._row_is_deleted(x):
                continue
            k = (x.name or "").strip()
            if not k:
                continue
            out[k] = self._do_sig(x)
        return out

    def _row_is_changed(self, x: DOItem) -> bool:
        k = (x.name or "").strip()
        if not k:
            return False
        cur = self._do_sig(x)
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
        # Apply pending deletions + clear "added" state
        try:
            self.rows = [x for x in (self.rows or []) if not self._row_is_deleted(x)]
            for x in (self.rows or []):
                # Purge deleted rules, clear rule add/delete flags, and set a saved signature.
                try:
                    new_privs: list[PrivateItem] = []
                    for p in (getattr(x, "privates", []) or []):
                        try:
                            if bool(getattr(p, "__ui_deleted", False)):
                                continue
                        except Exception:
                            pass
                        try:
                            if hasattr(p, "__ui_added"):
                                delattr(p, "__ui_added")
                        except Exception:
                            pass
                        try:
                            if hasattr(p, "__ui_deleted"):
                                delattr(p, "__ui_deleted")
                        except Exception:
                            pass
                        try:
                            setattr(p, "__ui_saved_sig", self._private_sig(p))
                        except Exception:
                            pass
                        new_privs.append(p)
                    x.privates = new_privs
                except Exception:
                    pass

                # Clear DO-level add/delete flags.
                try:
                    if hasattr(x, self._UI_ADDED):
                        delattr(x, self._UI_ADDED)
                except Exception:
                    pass
                try:
                    if hasattr(x, self._UI_DELETED):
                        delattr(x, self._UI_DELETED)
                except Exception:
                    pass
        except Exception:
            pass
        self._saved_sig_by_name = self._snapshot_sig_by_name()
        self.refresh()

    def mark_all_rows_added(self) -> None:
        for x in (self.rows or []):
            try:
                setattr(x, self._UI_ADDED, True)
                if hasattr(x, self._UI_DELETED):
                    delattr(x, self._UI_DELETED)
            except Exception:
                pass
        self.refresh()

    def edit_selected(self) -> None:
        """Edit the selected DO using the same UI as double-clicking the type cell."""
        self._end_inline_name_edit(commit=True)
        self._edit_type_for_selected()

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
        # Load rows as clones, resetting UI-only state.
        cloned = self._clone_rows(list(rows or []))
        for x in cloned:
            # Reset DO-level UI state.
            try:
                if hasattr(x, self._UI_ADDED):
                    delattr(x, self._UI_ADDED)
            except Exception:
                pass
            try:
                if hasattr(x, self._UI_DELETED):
                    delattr(x, self._UI_DELETED)
            except Exception:
                pass

            # Initialize per-rule saved signatures (and clear rule add/delete flags).
            try:
                for p in (getattr(x, "privates", []) or []):
                    for flag in ("__ui_added", "__ui_deleted"):
                        try:
                            if hasattr(p, flag):
                                delattr(p, flag)
                        except Exception:
                            pass
                    try:
                        setattr(p, "__ui_saved_sig", self._private_sig(p))
                    except Exception:
                        pass
            except Exception:
                pass
        self.rows = cloned
        self._undo_stack = []
        self._saved_sig_by_name = self._snapshot_sig_by_name()
        self.refresh()

    def get_rows(self) -> list[DOItem]:
        out: list[DOItem] = []
        for x in (self.rows or []):
            if self._row_is_deleted(x):
                continue
            c = self._clone_do_item(x)
            self._strip_ui_flags_inplace(c)
            out.append(c)
        return out

    def _clone_private_item(self, p: PrivateItem) -> PrivateItem:
        item = PrivateItem(attrib=dict(p.attrib), inner_xml=p.inner_xml)
        # Preserve UI-only flags (if any are ever added)
        for flag in ("__ui_added", "__ui_deleted", "__ui_saved_sig"):
            try:
                if hasattr(p, flag):
                    setattr(item, flag, getattr(p, flag))
            except Exception:
                pass
        return item

    def _clone_do_item(self, x: DOItem) -> DOItem:
        privs = [self._clone_private_item(p) for p in (getattr(x, "privates", []) or [])]
        item = DOItem(name=x.name, do_type=x.do_type, privates=privs)
        # Preserve UI-only flags for undo.
        for flag in (self._UI_ADDED, self._UI_DELETED):
            try:
                if hasattr(x, flag):
                    setattr(item, flag, getattr(x, flag))
            except Exception:
                pass
        return item

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
            tags = self._row_tags(row)
            self.tree.insert("", "end", iid=str(idx), values=[row.name, row.do_type], tags=tags)

    def _begin_inline_name_edit(self, iid: str) -> None:
        if iid is None:
            return
        try:
            idx = int(iid)
        except Exception:
            return
        if idx < 0 or idx >= len(self.rows):
            return

        if self._row_is_deleted(self.rows[idx]):
            return

        self._end_inline_name_edit(commit=True)

        bbox = self.tree.bbox(iid, column="#1")
        if not bbox:
            return
        x, y, w, h = bbox
        ent = ttk.Entry(self.tree)
        try:
            ent.insert(0, self.rows[idx].name or "")
        except Exception:
            pass
        ent.place(x=x, y=y, width=w, height=h)
        ent.focus_set()
        try:
            # Match Application inline-edit behavior: prefill and select all.
            ent.selection_range(0, tk.END)
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

        if self._row_is_deleted(self.rows[idx]):
            return

        if not new_name:
            messagebox.showerror("Missing", "DO name is required", parent=self)
            return

        current = self.rows[idx]
        if new_name != current.name and any((not self._row_is_deleted(x)) and x.name == new_name for x in self.rows):
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
        existing = {x.name for x in self.rows if not self._row_is_deleted(x)}
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
        if self._row_is_deleted(self.rows[idx]):
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
        if self._row_is_deleted(self.rows[idx]):
            return
        # Added-then-deleted before save: cancel the addition (no red removed state).
        try:
            if self._row_is_added(self.rows[idx]):
                self._push_undo()
                self.rows.pop(idx)
                self.refresh()
                if self.rows:
                    sel = min(idx, len(self.rows) - 1)
                    self.tree.selection_set(str(sel))
                return
        except Exception:
            pass
        self._push_undo()
        try:
            setattr(self.rows[idx], self._UI_DELETED, True)
        except Exception:
            pass
        try:
            if hasattr(self.rows[idx], self._UI_ADDED):
                delattr(self.rows[idx], self._UI_ADDED)
        except Exception:
            pass
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
        try:
            setattr(new_item, self._UI_ADDED, True)
        except Exception:
            pass
        try:
            if hasattr(new_item, self._UI_DELETED):
                delattr(new_item, self._UI_DELETED)
        except Exception:
            pass

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
        is_deleted = False
        try:
            if idx is not None and 0 <= idx < len(self.rows):
                is_deleted = self._row_is_deleted(self.rows[idx])
        except Exception:
            is_deleted = False

        can_edit = can_copy and (not is_deleted)
        can_delete = can_copy and (not is_deleted)
        can_paste = self._clipboard is not None
        can_up = idx is not None and idx > 0 and (not is_deleted)
        can_down = idx is not None and idx < (len(self.rows) - 1) and (not is_deleted)
        can_rules = idx is not None and (not is_deleted)
        self._menu.entryconfigure("Copy", state=("normal" if can_copy else "disabled"))
        self._menu.entryconfigure("Cut", state=("normal" if can_copy else "disabled"))
        self._menu.entryconfigure("Paste", state=("normal" if can_paste else "disabled"))
        self._menu.entryconfigure("Delete", state=("normal" if can_delete else "disabled"))
        try:
            self._menu.entryconfigure("Edit", state=("normal" if can_edit else "disabled"))
        except Exception:
            pass
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
        dlg = DOEditDialog(
            self,
            title="Add",
            do_types=self.do_types,
            initial=None,
            get_do_type_preview=self._get_do_type_preview,
        )
        res = dlg.show()
        if res is None:
            return
        if any((not self._row_is_deleted(x)) and x.name == res.name for x in self.rows):
            messagebox.showerror("Duplicate", f"DO name already exists: {res.name}", parent=self)
            return
        self._push_undo()
        try:
            setattr(res, self._UI_ADDED, True)
        except Exception:
            pass
        self.rows.append(res)
        self.refresh()

    def _insert(self) -> None:
        """Insert a new DO near the current selection.

        Behavior:
        - If a row is selected: insert AFTER it.
        - If nothing is selected: append at the end.
        """
        self._end_inline_name_edit(commit=True)
        dlg = DOEditDialog(
            self,
            title="Insert",
            do_types=self.do_types,
            initial=None,
            get_do_type_preview=self._get_do_type_preview,
        )
        res = dlg.show()
        if res is None:
            return
        if any((not self._row_is_deleted(x)) and x.name == res.name for x in self.rows):
            messagebox.showerror("Duplicate", f"DO name already exists: {res.name}", parent=self)
            return

        try:
            setattr(res, self._UI_ADDED, True)
        except Exception:
            pass

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
        if self._row_is_deleted(self.rows[idx]):
            return
        current = self.rows[idx]
        dlg = DOEditDialog(
            self,
            title="Edit type",
            do_types=self.do_types,
            initial=current,
            edit_name=True,
            get_do_type_preview=self._get_do_type_preview,
        )
        res = dlg.show()
        if res is None:
            return
        new_name = (res.name or "").strip()
        new_type = (res.do_type or "").strip()
        if not new_name:
            messagebox.showerror("Missing", "DO name is required", parent=self)
            return
        if not new_type:
            messagebox.showerror("Missing", "DO type is required", parent=self)
            return

        if new_name != current.name and any(i != idx and x.name == new_name for i, x in enumerate(self.rows)):
            messagebox.showerror("Duplicate", f"DO name already exists: {new_name}", parent=self)
            return

        if new_name == current.name and new_type == current.do_type:
            return

        self._push_undo()
        self.rows[idx] = DOItem(
            name=new_name,
            do_type=new_type,
            privates=list(getattr(current, "privates", []) or []),
        )
        self.refresh()
        self.tree.selection_set(str(idx))

    def _move(self, delta: int) -> None:
        idx = self._selected_index()
        if idx is None:
            return
        try:
            if 0 <= idx < len(self.rows) and self._row_is_deleted(self.rows[idx]):
                return
        except Exception:
            pass
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
        self._undo_stack: list[list[PrivateItem]] = []
        self._undo_max = 50

        # Changed-row highlighting (vs last saved snapshot)
        self._saved_sigs: list[tuple] = []

        # UI-only row states (persist until Save)
        self._UI_ADDED = "__ui_added"
        self._UI_DELETED = "__ui_deleted"

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

        self.tree.bind("<Control-z>", lambda _e: self.undo())
        self.tree.bind("<Control-Z>", lambda _e: self.undo())

        try:
            self.tree.tag_configure("added", background="honeydew2")
            self.tree.tag_configure("removed", background="misty rose")
            self.tree.tag_configure("changed", background="lemon chiffon")
        except Exception:
            pass

    def _row_is_added(self, x: PrivateItem) -> bool:
        try:
            return bool(getattr(x, self._UI_ADDED, False))
        except Exception:
            return False

    def _row_is_deleted(self, x: PrivateItem) -> bool:
        try:
            return bool(getattr(x, self._UI_DELETED, False))
        except Exception:
            return False

    def _strip_ui_flags_inplace(self, x: PrivateItem) -> None:
        try:
            if hasattr(x, self._UI_ADDED):
                delattr(x, self._UI_ADDED)
        except Exception:
            pass
        try:
            if hasattr(x, self._UI_DELETED):
                delattr(x, self._UI_DELETED)
        except Exception:
            pass

    def _row_tags_at_index(self, idx: int) -> tuple[str, ...]:
        if idx < 0 or idx >= len(self.rows):
            return ()
        r = self.rows[idx]
        if self._row_is_deleted(r):
            return ("removed",)
        if self._row_is_added(r):
            return ("added",)
        return ("changed",) if self._row_is_changed_at_index(idx) else ()

    def _row_sig(self, row: PrivateItem) -> tuple:
        try:
            attrib_items = tuple(sorted((row.attrib or {}).items()))
        except Exception:
            attrib_items = ()
        inner = (row.inner_xml or "")
        return (attrib_items, inner)

    def _snapshot_sigs(self) -> list[tuple]:
        return [self._row_sig(r) for r in (self.rows or []) if not self._row_is_deleted(r)]

    def _row_is_changed_at_index(self, idx: int) -> bool:
        if idx < 0 or idx >= len(self.rows):
            return False
        cur = self._row_sig(self.rows[idx])
        if idx >= len(self._saved_sigs):
            return True
        return self._saved_sigs[idx] != cur

    def _reapply_row_tags(self) -> None:
        try:
            for iid in self.tree.get_children(""):
                try:
                    idx = int(iid)
                except Exception:
                    continue
                tags = self._row_tags_at_index(idx)
                try:
                    self.tree.item(iid, tags=tags)
                except Exception:
                    pass
        except Exception:
            pass

    def mark_saved(self) -> None:
        # Apply pending deletions + clear "added" state
        try:
            self.rows = [x for x in (self.rows or []) if not self._row_is_deleted(x)]
            for x in (self.rows or []):
                self._strip_ui_flags_inplace(x)
        except Exception:
            pass
        self._saved_sigs = self._snapshot_sigs()
        self.refresh()

    def mark_all_rows_added(self) -> None:
        for x in (self.rows or []):
            try:
                setattr(x, self._UI_ADDED, True)
                if hasattr(x, self._UI_DELETED):
                    delattr(x, self._UI_DELETED)
            except Exception:
                pass
        self.refresh()

    def set_rows(self, rows: list[PrivateItem]) -> None:
        # Loading rows resets UI-only state.
        self.rows = [PrivateItem(attrib=dict(x.attrib), inner_xml=x.inner_xml) for x in (rows or [])]
        for x in self.rows:
            self._strip_ui_flags_inplace(x)
        self._saved_sigs = self._snapshot_sigs()
        self._undo_stack = []
        self.refresh()

    def get_rows(self) -> list[PrivateItem]:
        out: list[PrivateItem] = []
        for x in (self.rows or []):
            if self._row_is_deleted(x):
                continue
            item = PrivateItem(attrib=dict(x.attrib), inner_xml=x.inner_xml)
            out.append(item)
        return out

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
            tags = self._row_tags_at_index(idx)
            self.tree.insert("", "end", iid=str(idx), values=[typ, preview], tags=tags)

    def _add(self) -> None:
        dlg = PrivateEditDialog(self, title="Add Private", initial=None)
        res = dlg.show()
        if res is None:
            return
        self._push_undo()
        try:
            setattr(res, self._UI_ADDED, True)
        except Exception:
            pass
        self.rows.append(res)
        self.refresh()
        self.tree.selection_set(str(len(self.rows) - 1))

    def _edit(self) -> None:
        idx = self._selected_index()
        if idx is None or idx < 0 or idx >= len(self.rows):
            return
        if self._row_is_deleted(self.rows[idx]):
            return
        current = self.rows[idx]
        dlg = PrivateEditDialog(self, title="Edit Private", initial=current)
        res = dlg.show()
        if res is None:
            return
        if self._row_sig(res) == self._row_sig(current):
            return
        self._push_undo()
        # Preserve UI-only flags on edit.
        try:
            if self._row_is_added(current):
                setattr(res, self._UI_ADDED, True)
        except Exception:
            pass
        try:
            if self._row_is_deleted(current):
                setattr(res, self._UI_DELETED, True)
        except Exception:
            pass
        self.rows[idx] = res
        self.refresh()
        self.tree.selection_set(str(idx))

    def _delete(self) -> None:
        idx = self._selected_index()
        if idx is None or idx < 0 or idx >= len(self.rows):
            return
        if self._row_is_deleted(self.rows[idx]):
            return
        # Added-then-deleted before save: cancel the addition (no red removed state).
        try:
            if self._row_is_added(self.rows[idx]):
                self._push_undo()
                self.rows.pop(idx)
                self.refresh()
                if self.rows:
                    self.tree.selection_set(str(min(idx, len(self.rows) - 1)))
                return
        except Exception:
            pass
        self._push_undo()
        try:
            setattr(self.rows[idx], self._UI_DELETED, True)
        except Exception:
            pass
        try:
            if hasattr(self.rows[idx], self._UI_ADDED):
                delattr(self.rows[idx], self._UI_ADDED)
        except Exception:
            pass
        self.refresh()
        if self.rows:
            self.tree.selection_set(str(min(idx, len(self.rows) - 1)))

    def _clone_private_item(self, p: PrivateItem) -> PrivateItem:
        item = PrivateItem(attrib=dict(p.attrib), inner_xml=p.inner_xml)
        for flag in (self._UI_ADDED, self._UI_DELETED):
            try:
                if hasattr(p, flag):
                    setattr(item, flag, getattr(p, flag))
            except Exception:
                pass
        return item

    def _clone_rows(self, rows: list[PrivateItem]) -> list[PrivateItem]:
        return [self._clone_private_item(x) for x in (rows or [])]

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

        self._rows: list[tuple[str, int, PrivateItem, bool]] = []  # (do_name, private_index, item, do_deleted)
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

        try:
            self.tree.tag_configure("added", background="honeydew2")
            self.tree.tag_configure("removed", background="misty rose")
            self.tree.tag_configure("changed", background="lemon chiffon")
        except Exception:
            pass

        self._menu = tk.Menu(self, tearoff=False)
        self._menu.add_command(label="Add", command=self._cmd_add)
        self._menu.add_separator()
        self._menu.add_command(label="Copy", command=self._cmd_copy)
        self._menu.add_command(label="Cut", command=self._cmd_cut)
        self._menu.add_command(label="Paste", command=self._cmd_paste)
        self._menu.add_command(label="Delete", command=self._cmd_delete)

    def _clone_private_item(self, p: PrivateItem) -> PrivateItem:
        return PrivateItem(attrib=dict(p.attrib), inner_xml=p.inner_xml)

    def _private_sig(self, p: PrivateItem) -> tuple:
        try:
            attrib_items = tuple(sorted((p.attrib or {}).items()))
        except Exception:
            attrib_items = ()
        inner = (getattr(p, "inner_xml", "") or "")
        return (attrib_items, inner)

    def _row_tags(self, *, do_deleted: bool, item: PrivateItem) -> tuple[str, ...]:
        if do_deleted:
            return ("removed",)
        try:
            if bool(getattr(item, "__ui_deleted", False)):
                return ("removed",)
        except Exception:
            pass
        try:
            if bool(getattr(item, "__ui_added", False)):
                return ("added",)
        except Exception:
            pass
        try:
            saved = getattr(item, "__ui_saved_sig", None)
        except Exception:
            saved = None
        cur = self._private_sig(item)
        if saved is None:
            return ("changed",)
        return ("changed",) if saved != cur else ()

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
        do_name, private_index, cur, _do_deleted = self._rows[idx]
        return (do_name, private_index, cur)

    def _show_context_menu(self, event: tk.Event) -> None:
        try:
            row_id = self.tree.identify_row(event.y)
            if row_id:
                self.tree.selection_set(row_id)
        except Exception:
            pass

        idx = self._selected_index()
        has_sel = idx is not None and 0 <= idx < len(self._rows)
        can_edit = False
        if has_sel and idx is not None:
            try:
                _do_name, _pi, it, do_deleted = self._rows[idx]
                can_edit = (not do_deleted) and (not bool(getattr(it, "__ui_deleted", False)))
            except Exception:
                can_edit = False
        can_paste = self._clipboard is not None and can_edit
        self._menu.entryconfigure("Copy", state=("normal" if can_edit else "disabled"))
        self._menu.entryconfigure("Cut", state=("normal" if can_edit else "disabled"))
        self._menu.entryconfigure("Paste", state=("normal" if can_paste else "disabled"))
        self._menu.entryconfigure("Delete", state=("normal" if can_edit else "disabled"))

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
        idx = self._selected_index()
        if idx is None or idx < 0 or idx >= len(self._rows):
            return
        do_name, private_index, cur, do_deleted = self._rows[idx]
        try:
            if do_deleted or bool(getattr(cur, "__ui_deleted", False)):
                return
        except Exception:
            pass
        if self._on_edit_rule is not None:
            self._on_edit_rule(do_name, private_index)

    def _cmd_copy(self) -> None:
        idx = self._selected_index()
        if idx is None or idx < 0 or idx >= len(self._rows):
            return
        _do_name, _private_index, cur, do_deleted = self._rows[idx]
        try:
            if do_deleted or bool(getattr(cur, "__ui_deleted", False)):
                return
        except Exception:
            pass
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
        idx = self._selected_index()
        if idx is None or idx < 0 or idx >= len(self._rows):
            return
        do_name, private_index, cur, do_deleted = self._rows[idx]
        try:
            if do_deleted or bool(getattr(cur, "__ui_deleted", False)):
                return
        except Exception:
            pass
        if self._on_paste_rule is not None:
            self._on_paste_rule(do_name, private_index, self._clone_private_item(self._clipboard))

    def _cmd_delete(self) -> None:
        idx = self._selected_index()
        if idx is None or idx < 0 or idx >= len(self._rows):
            return
        do_name, private_index, cur, do_deleted = self._rows[idx]
        try:
            if do_deleted or bool(getattr(cur, "__ui_deleted", False)):
                return
        except Exception:
            pass
        if self._on_delete_rule is not None:
            self._on_delete_rule(do_name, private_index)

    def set_from_dos(self, dos: list[DOItem]) -> None:
        rows: list[tuple[str, int, PrivateItem, bool]] = []
        for do in sorted(list(dos or []), key=lambda x: (x.name or "")):
            try:
                do_deleted = bool(getattr(do, "__ui_deleted", False))
            except Exception:
                do_deleted = False
            privs = list(getattr(do, "privates", []) or [])
            for i, p in enumerate(privs):
                rows.append((do.name, i, p, do_deleted))
        self._rows = rows
        self.refresh()

    def refresh(self) -> None:
        for item in self.tree.get_children(""):
            self.tree.delete(item)
        for idx, (do_name, _i, row, do_deleted) in enumerate(self._rows):
            typ = (row.attrib.get("type") or "")
            preview = (row.inner_xml or "").replace("\r", "").replace("\n", " ").strip()
            if len(preview) > 120:
                preview = preview[:120] + "..."
            tags = self._row_tags(do_deleted=do_deleted, item=row)
            self.tree.insert("", "end", iid=str(idx), values=[do_name, typ, preview], tags=tags)

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

        ttk.Label(frm, text="lnClass").grid(row=3, column=0, sticky="w", pady=4)
        self.var_lnclass = tk.StringVar(value="")
        ent_ln = ttk.Entry(frm, textvariable=self.var_lnclass, width=64)
        ent_ln.grid(row=3, column=1, sticky="we", padx=(10, 0), pady=4)

        ttk.Label(frm, text="desc (optional)").grid(row=4, column=0, sticky="w", pady=4)
        self.var_desc = tk.StringVar(value="")
        ent_desc = ttk.Entry(frm, textvariable=self.var_desc, width=64)
        ent_desc.grid(row=4, column=1, sticky="we", padx=(10, 0), pady=4)

        hint = ttk.Label(
            frm,
            text=(
                "Tip: If you pick an existing template, DO/Private blocks will be copied, then name/lnClass/desc updated."
            ),
        )
        hint.grid(row=5, column=0, columnspan=2, sticky="w", pady=(8, 0))

        btns = ttk.Frame(frm)
        btns.grid(row=6, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Cancel", command=self._cancel).pack(side="right")
        ttk.Button(btns, text="Create", command=self._ok).pack(side="right", padx=(0, 8))

        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Control-f>", lambda _e: ent_filter.focus_set())

        def unique_copy_name(base: str) -> str:
            # Keep generated IDs consistent with other copy dialogs.
            existing = {x.id for x in self._lnode_infos}
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

        self.var_base.trace_add("write", prefill)
        self.var_filter.trace_add("write", apply_filter)
        apply_filter()
        prefill()

        ent_filter.focus_set()

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


class NewDOTypeDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        do_type_ids: list[str],
        cdc_values: list[str],
        get_base_cdc: Callable[[str], str] | None = None,
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

        self._id_internal_update = False
        self._id_user_modified = False
        self._last_suggested_id = ""

        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text="Create from").grid(row=0, column=0, sticky="w", pady=4)
        self.var_base = tk.StringVar(value="(Blank)")
        base_values = ["(Blank)"] + list(self._do_type_ids)
        cb_base = ttk.Combobox(frm, textvariable=self.var_base, values=base_values, width=62)
        cb_base.grid(row=0, column=1, sticky="we", padx=(10, 0), pady=4)

        ttk.Label(frm, text="id (file name)").grid(row=1, column=0, sticky="w", pady=4)
        self.var_id = tk.StringVar(value="")
        ent_id = ttk.Entry(frm, textvariable=self.var_id, width=64)
        ent_id.grid(row=1, column=1, sticky="we", padx=(10, 0), pady=4)

        def _mark_user_modified(*_args) -> None:
            if self._id_internal_update:
                return
            self._id_user_modified = True

        self.var_id.trace_add("write", _mark_user_modified)

        ttk.Label(frm, text="CDC").grid(row=2, column=0, sticky="w", pady=4)
        self.var_cdc = tk.StringVar(value="")
        cb_cdc = ttk.Combobox(frm, textvariable=self.var_cdc, values=list(self._cdc_values), width=62)
        try:
            cb_cdc.configure(state="readonly")
        except Exception:
            pass
        cb_cdc.grid(row=2, column=1, sticky="we", padx=(10, 0), pady=4)

        ttk.Label(frm, text="desc (optional)").grid(row=3, column=0, sticky="w", pady=4)
        self.var_desc = tk.StringVar(value="")
        ent_desc = ttk.Entry(frm, textvariable=self.var_desc, width=64)
        ent_desc.grid(row=3, column=1, sticky="we", padx=(10, 0), pady=4)

        hint = ttk.Label(
            frm,
            text=(
                "Tip: If you pick an existing DOType, DA/Private blocks will be copied, then id/CDC/desc updated."
            ),
        )
        hint.grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))

        btns = ttk.Frame(frm)
        btns.grid(row=5, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Cancel", command=self._cancel).pack(side="right")
        ttk.Button(btns, text="Create", command=self._ok).pack(side="right", padx=(0, 8))

        self.bind("<Escape>", lambda _e: self._cancel())

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

            # Prefill CDC from base if available.
            if not self.var_cdc.get().strip() and callable(self._get_base_cdc):
                try:
                    cdc0 = (self._get_base_cdc(base_id) or "").strip()
                except Exception:
                    cdc0 = ""
                if cdc0:
                    # keep original casing in list (typically uppercase)
                    self.var_cdc.set(cdc0)

        self.var_base.trace_add("write", prefill)
        prefill()

        ent_id.focus_set()

    def _ok(self) -> None:
        new_id = (self.var_id.get() or "").strip()
        cdc = (self.var_cdc.get() or "").strip()
        desc = (self.var_desc.get() or "").strip()
        base_id = (self.var_base.get() or "").strip()

        if not new_id:
            messagebox.showerror("Missing", "id is required", parent=self)
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


class _EnumValEditDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, *, initial: dict[str, str]):
        super().__init__(parent)
        self.title("Edit EnumVal")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self._result: dict[str, str] | None = None

        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        self.var_ord = tk.StringVar(value=(initial.get("ord") or ""))
        self.var_val = tk.StringVar(value=(initial.get("val") or ""))
        self.var_desc = tk.StringVar(value=(initial.get("desc") or ""))

        ttk.Label(frm, text="ord").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=self.var_ord, width=18).grid(row=0, column=1, sticky="we", padx=(10, 0), pady=4)
        ttk.Label(frm, text="val").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=self.var_val, width=52).grid(row=1, column=1, sticky="we", padx=(10, 0), pady=4)
        ttk.Label(frm, text="desc").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=self.var_desc, width=52).grid(row=2, column=1, sticky="we", padx=(10, 0), pady=4)

        btns = ttk.Frame(frm)
        btns.grid(row=3, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Cancel", command=self._cancel).pack(side="right")
        ttk.Button(btns, text="OK", command=self._ok).pack(side="right", padx=(0, 8))

        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Return>", lambda _e: self._ok())

    def _ok(self) -> None:
        ord0 = (self.var_ord.get() or "").strip()
        if ord0 and not ord0.isdigit():
            messagebox.showerror("Invalid", "ord must be an integer", parent=self)
            return
        self._result = {
            "ord": ord0,
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


class EnumValTable(ttk.Frame):
    def __init__(self, parent: tk.Misc):
        super().__init__(parent)
        self.rows: list[dict[str, str]] = []
        self._clipboard: dict[str, str] | None = None
        self._undo_stack: list[list[dict[str, str]]] = []
        self._undo_max = 50

        # Changed-row highlighting (vs last saved snapshot)
        self._saved_sigs: list[tuple[str, str, str, str]] = []

        # UI-only row states (persist until Save)
        self._UI_ADDED = "__ui_added"
        self._UI_DELETED = "__ui_deleted"

        # Optional callback invoked after any user-visible mutation.
        # Signature: callback() -> None
        self.on_change: Callable[[], None] | None = None

        self._inline: ttk.Entry | None = None
        self._inline_iid: str | None = None
        self._inline_col: str | None = None

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
        content.columnconfigure(0, weight=1)
        content.rowconfigure(0, weight=1)

        cols = ["ord", "desc", "val"]
        self.tree = ttk.Treeview(content, columns=cols, show="headings", selectmode="browse")
        self.tree.heading("ord", text="ord")
        self.tree.heading("desc", text="desc")
        self.tree.heading("val", text="val")
        self.tree.column("ord", width=70, anchor="w", stretch=False)
        self.tree.column("desc", width=520, anchor="w")
        self.tree.column("val", width=240, anchor="w")

        y = ttk.Scrollbar(content, orient="vertical", command=self.tree.yview)
        x = ttk.Scrollbar(content, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=y.set, xscrollcommand=x.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        y.grid(row=0, column=1, sticky="ns")
        x.grid(row=1, column=0, columnspan=2, sticky="ew")

        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<Button-1>", self._on_left_click)
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

    def _row_tags_at_index(self, idx: int) -> tuple[str, ...]:
        if idx < 0 or idx >= len(self.rows):
            return ()
        r = self.rows[idx]
        if self._row_is_deleted(r):
            return ("removed",)
        if self._row_is_added(r):
            return ("added",)
        return ("changed",) if self._row_is_changed_at_index(idx) else ()

    def _row_sig(self, r: dict[str, str]) -> tuple[str, str, str, str]:
        return (
            (r.get("ord") or ""),
            (r.get("desc") or ""),
            (r.get("val") or ""),
            (r.get("langRef") or ""),
        )

    def _snapshot_sigs(self) -> list[tuple[str, str, str, str]]:
        return [self._row_sig(r) for r in (self.rows or [])]

    def _row_is_changed_at_index(self, idx: int) -> bool:
        if idx < 0 or idx >= len(self.rows):
            return False
        cur = self._row_sig(self.rows[idx])
        if idx >= len(self._saved_sigs):
            return True
        return self._saved_sigs[idx] != cur

    def _reapply_row_tags(self) -> None:
        try:
            for iid in self.tree.get_children(""):
                try:
                    idx = int(iid)
                except Exception:
                    continue
                tags = self._row_tags_at_index(idx)
                try:
                    self.tree.item(iid, tags=tags)
                except Exception:
                    pass
        except Exception:
            pass

    def mark_saved(self) -> None:
        # Apply pending deletions + clear "added" state
        try:
            self.rows = [r for r in (self.rows or []) if not self._row_is_deleted(r)]
            for r in (self.rows or []):
                r.pop(self._UI_ADDED, None)
                r.pop(self._UI_DELETED, None)
        except Exception:
            pass
        self._saved_sigs = self._snapshot_sigs()
        self.refresh()

    def commit_any_edit(self) -> None:
        try:
            self._end_inline(commit=True)
        except Exception:
            pass

    def set_rows(self, rows: list[dict[str, str]]) -> None:
        # Loading rows resets UI-only state.
        self.rows = [self._strip_ui_flags(dict(r)) for r in (rows or [])]
        self._undo_stack = []
        self._saved_sigs = self._snapshot_sigs()
        self.refresh()

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
        for idx, row in enumerate(self.rows):
            tags = self._row_tags_at_index(idx)
            self.tree.insert(
                "",
                "end",
                iid=str(idx),
                values=[row.get("ord", ""), row.get("desc", ""), row.get("val", "")],
                tags=tags,
            )

    def _selected_index(self) -> int | None:
        try:
            sel = self.tree.selection()
            if not sel:
                return None
            return int(sel[0])
        except Exception:
            return None

    def _push_undo(self) -> None:
        self._undo_stack.append([dict(r) for r in (self.rows or [])])
        if len(self._undo_stack) > self._undo_max:
            self._undo_stack = self._undo_stack[-self._undo_max :]

    def undo(self) -> None:
        self._end_inline(commit=True)
        if not self._undo_stack:
            return
        self.rows = [dict(r) for r in self._undo_stack.pop()]
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

    def _default_new_ord(self) -> str:
        nums: list[int] = []
        for r in (self.rows or []):
            raw = (r.get("ord") or "").strip()
            if raw.isdigit():
                nums.append(int(raw))
        if not nums:
            return "0"
        return str(max(nums) + 1)

    def _add(self) -> None:
        self._end_inline(commit=True)
        self._push_undo()
        r = {"ord": self._default_new_ord(), "desc": "", "val": "", "langRef": ""}
        r[self._UI_ADDED] = "1"
        self.rows.append(r)
        self.refresh()
        try:
            self.tree.selection_set(str(len(self.rows) - 1))
        except Exception:
            pass

        self._fire_change()

    def _insert(self) -> None:
        self._end_inline(commit=True)
        self._push_undo()
        idx = self._selected_index()
        row = {"ord": self._default_new_ord(), "desc": "", "val": "", "langRef": ""}
        row[self._UI_ADDED] = "1"
        if idx is None or idx < 0 or idx >= len(self.rows):
            self.rows.insert(0, row)
            self.refresh()
            try:
                self.tree.selection_set("0")
            except Exception:
                pass

            self._fire_change()
            return
        self.rows.insert(idx, row)
        self.refresh()
        try:
            self.tree.selection_set(str(idx))
        except Exception:
            pass

        self._fire_change()

    def copy_selected(self) -> None:
        self._end_inline(commit=True)
        idx = self._selected_index()
        if idx is None or idx < 0 or idx >= len(self.rows):
            return
        self._clipboard = dict(self.rows[idx])
        try:
            self.clipboard_clear()
            self.clipboard_append((self.rows[idx].get("val") or "") + "\t" + (self.rows[idx].get("desc") or ""))
        except Exception:
            pass

    def cut_selected(self) -> None:
        self.copy_selected()
        self.delete_selected()

    def delete_selected(self) -> None:
        self._end_inline(commit=True)
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
        self._end_inline(commit=True)
        if self._clipboard is None:
            return
        self._push_undo()
        new_row = dict(self._clipboard)
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
        self._end_inline(commit=True)
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

    def edit_selected(self) -> None:
        self._end_inline(commit=True)
        idx = self._selected_index()
        if idx is None or idx < 0 or idx >= len(self.rows):
            return
        dlg = _EnumValEditDialog(self, initial=dict(self.rows[idx]))
        res = dlg.show()
        if not res:
            return
        self._push_undo()
        self.rows[idx].update(res)
        self.refresh()
        try:
            self.tree.selection_set(str(idx))
        except Exception:
            pass

        self._fire_change()

    def _show_context_menu(self, event: tk.Event) -> None:
        self._end_inline(commit=True)

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

    def _on_left_click(self, _event: tk.Event) -> None:
        self._end_inline(commit=True)

    def _on_double_click(self, event: tk.Event) -> None:
        self._end_inline(commit=True)
        try:
            iid = self.tree.identify_row(event.y)
            col = self.tree.identify_column(event.x)
        except Exception:
            return
        if not iid or not col:
            return
        try:
            idx = int(iid)
        except Exception:
            return
        if idx < 0 or idx >= len(self.rows):
            return
        col_idx = int(col.replace("#", "")) - 1
        cols = ["ord", "desc", "val"]
        if col_idx < 0 or col_idx >= len(cols):
            return
        self._start_inline(iid, cols[col_idx])

    def _start_inline(self, iid: str, col_name: str) -> None:
        try:
            idx0 = int(iid)
            if 0 <= idx0 < len(self.rows) and self._row_is_deleted(self.rows[idx0]):
                return
        except Exception:
            pass
        try:
            bbox = self.tree.bbox(iid, col_name)
        except Exception:
            bbox = None
        if not bbox:
            return
        x, y, w, h = bbox
        val = ""
        try:
            idx = int(iid)
            val = self.rows[idx].get(col_name, "")
        except Exception:
            val = ""

        ent = ttk.Entry(self.tree)
        ent.insert(0, val)
        ent.place(x=x, y=y, width=w, height=h)
        self._inline = ent
        self._inline_iid = iid
        self._inline_col = col_name

        ent.focus_set()
        ent.select_range(0, tk.END)
        ent.bind("<Return>", lambda _e: self._end_inline(commit=True))
        ent.bind("<Escape>", lambda _e: self._end_inline(commit=False))
        ent.bind("<FocusOut>", lambda _e: self._end_inline(commit=True))

    def _end_inline(self, *, commit: bool) -> None:
        if self._inline is None:
            return
        ent = self._inline
        iid = self._inline_iid
        col = self._inline_col

        try:
            value = ent.get()
        except Exception:
            value = ""

        try:
            ent.destroy()
        except Exception:
            pass
        self._inline = None
        self._inline_iid = None
        self._inline_col = None

        if not commit or iid is None or col is None:
            return

        try:
            idx = int(iid)
        except Exception:
            return
        if idx < 0 or idx >= len(self.rows):
            return

        if self._row_is_deleted(self.rows[idx]):
            return

        if col == "ord":
            v = (value or "").strip()
            if v and not v.isdigit():
                messagebox.showerror("Invalid", "ord must be an integer", parent=self)
                return
            value = v

        self._push_undo()
        self.rows[idx][col] = value
        self.refresh()
        try:
            self.tree.selection_set(iid)
        except Exception:
            pass

        self._fire_change()


class NewEnumTypeDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        enum_type_ids: list[str],
    ):
        super().__init__(parent)
        self.title("New EnumType")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self._result: dict[str, str] | None = None
        self._enum_type_ids = list(enum_type_ids or [])

        self._id_internal_update = False
        self._id_user_modified = False
        self._last_suggested_id = ""

        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text="Create from").grid(row=0, column=0, sticky="w", pady=4)
        self.var_base = tk.StringVar(value="(Blank)")
        base_values = ["(Blank)"] + list(self._enum_type_ids)
        cb_base = ttk.Combobox(frm, textvariable=self.var_base, values=base_values, width=62)
        cb_base.grid(row=0, column=1, sticky="we", padx=(10, 0), pady=4)

        ttk.Label(frm, text="id (file name)").grid(row=1, column=0, sticky="w", pady=4)
        self.var_id = tk.StringVar(value="")
        ent_id = ttk.Entry(frm, textvariable=self.var_id, width=64)
        ent_id.grid(row=1, column=1, sticky="we", padx=(10, 0), pady=4)

        def _mark_user_modified(*_args) -> None:
            if self._id_internal_update:
                return
            self._id_user_modified = True

        self.var_id.trace_add("write", _mark_user_modified)

        hint = ttk.Label(frm, text="Tip: If you pick an existing EnumType, EnumVal + LangRef will be copied, then id updated.")
        hint.grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))

        btns = ttk.Frame(frm)
        btns.grid(row=3, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Cancel", command=self._cancel).pack(side="right")
        ttk.Button(btns, text="Create", command=self._ok).pack(side="right", padx=(0, 8))

        self.bind("<Escape>", lambda _e: self._cancel())

        def unique_copy_name(base: str) -> str:
            existing = set(self._enum_type_ids)
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

        self.var_base.trace_add("write", prefill)
        prefill()

        ent_id.focus_set()

    def _ok(self) -> None:
        new_id = (self.var_id.get() or "").strip()
        base_id = (self.var_base.get() or "").strip()

        if not new_id:
            messagebox.showerror("Missing", "id is required", parent=self)
            return

        self._result = {
            "base_id": base_id,
            "id": new_id,
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
        template_structure_changed_callback=None,
    ):
        super().__init__(parent)
        self.catalog = catalog
        self.iec61850_dir = Path(iec61850_dir)
        self._create_instance_callback = create_instance_callback
        self._template_structure_changed_callback = template_structure_changed_callback
        self.model: LNodeTypeModel | None = None
        self.dirty = False
        self._saved_sig_full: tuple | None = None

        self._in_load = False
        self._last_do_name_set: set[str] = set()

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

        def _open_search_dropdown(_event: tk.Event | None = None) -> None:
            try:
                self.cb.focus_set()
            except Exception:
                pass
            try:
                self.cb.after_idle(lambda: self.cb.event_generate("<Down>"))
            except Exception:
                pass

        try:
            self.cb.bind("<Button-1>", _open_search_dropdown, add="+")
        except Exception:
            pass

        ttk.Button(row2, text="Load", command=self.load_selected).pack(side="left", padx=(8, 0))

        self.lbl_ln_match = ttk.Label(row2, text="")
        self.lbl_ln_match.pack(side="left", padx=(10, 0))

        self.lbl_meta = ttk.Label(row2, text="")
        self.lbl_meta.pack(side="left", padx=(12, 0))

        self.lbl_saved = ttk.Label(row2, text="")
        self.lbl_saved.pack(side="left", padx=(12, 0))

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=10, pady=10)

        self.table = DOTable(self.nb, do_types=catalog.do_types, get_do_type_preview=self._do_type_preview_text)
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

        # Ctrl+Z undo while focused on the Rule list.
        try:
            self.rule_panel.tree.bind("<Control-z>", lambda _e: (self.table.undo(), "break")[1])
            self.rule_panel.tree.bind("<Control-Z>", lambda _e: (self.table.undo(), "break")[1])
        except Exception:
            pass

        self.private_table = PrivateTable(self.nb)
        self.nb.add(self.private_table, text="Private")

        # Mark dirty when table changes by wrapping refresh
        orig_refresh = self.table.refresh

        def refresh_with_dirty() -> None:
            orig_refresh()
            self._update_dirty_from_view()
            self._refresh_all_rules_view()

            # Propagate DO add/remove/rename into the instance editor (if configured).
            try:
                rows = self.table.get_rows()
                cur_names = {(x.name or "").strip() for x in (rows or []) if (x.name or "").strip()}
            except Exception:
                cur_names = set()

            if self.model is not None:
                try:
                    self.model.dos = rows  # keep in-memory model consistent with view
                except Exception:
                    pass

            if cur_names != self._last_do_name_set:
                self._last_do_name_set = set(cur_names)
                if not self._in_load and self.model is not None:
                    cb = getattr(self, "_template_structure_changed_callback", None)
                    if cb is not None:
                        try:
                            cb(self.model)
                        except Exception:
                            pass

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
            if raw:
                filtered_ids = _sort_filter_matches(raw, filtered_ids)

            cur = self.var_selected.get().strip()
            max_show = 1200
            shown = filtered_ids[:max_show]
            self.cb["values"] = shown
            if raw and filtered_ids:
                if cur != filtered_ids[0]:
                    self.var_selected.set(filtered_ids[0])
            elif (not raw) and cur and (cur not in filtered_ids):
                self.var_selected.set("")
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

        self._in_load = True
        try:
            self.table.set_rows(self.model.dos)
            self.private_table.set_rows(getattr(self.model, "privates", []) or [])
            try:
                self._last_do_name_set = {(x.name or "").strip() for x in (self.model.dos or []) if (x.name or "").strip()}
            except Exception:
                self._last_do_name_set = set()
        finally:
            self._in_load = False

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
        try:
            self.table.mark_saved()
        except Exception:
            pass
        try:
            self.private_table.mark_saved()
        except Exception:
            pass
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
        try:
            self.table.mark_saved()
        except Exception:
            pass
        try:
            self.private_table.mark_saved()
        except Exception:
            pass
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

        # Load into UI
        self.var_selected.set(new_id)
        self._in_load = True
        try:
            self.table.set_rows(self.model.dos)
            self.private_table.set_rows(self.model.privates)
            try:
                self._last_do_name_set = {(x.name or "").strip() for x in (self.model.dos or []) if (x.name or "").strip()}
            except Exception:
                self._last_do_name_set = set()
        finally:
            self._in_load = False

        try:
            if self.table.rows:
                self.table.tree.selection_set("0")
        except Exception:
            pass
        self._refresh_all_rules_view()

        try:
            self.table.mark_all_rows_added()
        except Exception:
            pass
        try:
            self.private_table.mark_all_rows_added()
        except Exception:
            pass

        self._saved_sig_full = None
        self.dirty = True
        self._update_save_button()
        self._update_create_instance_button()
        meta = f"lnClass={info.ln_class}  file={os.fspath(info.file_path)}"
        if info.desc:
            meta = meta + f"  desc={info.desc}"
        self.lbl_meta.configure(text=meta)
        self._set_status("New LN template created (unsaved)")

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
            # Use live rows so Rule tab can show UI-only add/delete states.
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

        # Any rule mutation participates in the DO table undo stack.
        try:
            self.table._push_undo()
        except Exception:
            pass

        txt = (text or "").rstrip("\n")
        if not txt.strip():
            # delete
            if existing_i is None:
                return
            try:
                existing = privs[existing_i]
                # Added-then-deleted before save: cancel the addition (no red removed state).
                if bool(getattr(existing, "__ui_added", False)):
                    privs.pop(existing_i)
                else:
                    try:
                        setattr(existing, "__ui_deleted", True)
                    except Exception:
                        pass
                    try:
                        if hasattr(existing, "__ui_added"):
                            delattr(existing, "__ui_added")
                    except Exception:
                        pass
            except Exception:
                privs.pop(existing_i)
            self.table.rows[idx] = DOItem(name=cur.name, do_type=cur.do_type, privates=privs)
            try:
                self.table.refresh()
            except Exception:
                pass
            return

        item = PrivateItem(attrib={"type": private_type}, inner_xml=txt)
        self._normalize_rule_item_inplace(item)
        if existing_i is None:
            try:
                setattr(item, "__ui_added", True)
            except Exception:
                pass
            privs.append(item)
        else:
            # Preserve UI-only state from the old item.
            try:
                old = privs[existing_i]
            except Exception:
                old = None
            if old is not None:
                try:
                    if bool(getattr(old, "__ui_added", False)):
                        setattr(item, "__ui_added", True)
                except Exception:
                    pass
                try:
                    if hasattr(old, "__ui_saved_sig"):
                        setattr(item, "__ui_saved_sig", getattr(old, "__ui_saved_sig"))
                except Exception:
                    pass
                try:
                    if hasattr(item, "__ui_deleted"):
                        delattr(item, "__ui_deleted")
                except Exception:
                    pass
            privs[existing_i] = item
        self.table.rows[idx] = DOItem(name=cur.name, do_type=cur.do_type, privates=privs)
        try:
            self.table.refresh()
        except Exception:
            pass

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

        try:
            self.table._push_undo()
        except Exception:
            pass

        # Remove old if editing existing.
        if old_do_name and old_private_index is not None:
            old_idx = self._do_index_by_name(old_do_name)
            if old_idx is not None:
                cur = self.table.rows[old_idx]
                privs = list(getattr(cur, "privates", []) or [])
                if 0 <= old_private_index < len(privs):
                    try:
                        existing = privs[old_private_index]
                        if bool(getattr(existing, "__ui_added", False)):
                            privs.pop(old_private_index)
                        else:
                            try:
                                setattr(existing, "__ui_deleted", True)
                            except Exception:
                                pass
                            try:
                                if hasattr(existing, "__ui_added"):
                                    delattr(existing, "__ui_added")
                            except Exception:
                                pass
                    except Exception:
                        privs.pop(old_private_index)
                    self.table.rows[old_idx] = DOItem(name=cur.name, do_type=cur.do_type, privates=privs)

        # Insert into target.
        new_idx = self._do_index_by_name(new_do_name)
        if new_idx is None:
            return
        cur2 = self.table.rows[new_idx]
        privs2 = list(getattr(cur2, "privates", []) or [])

        # New insertions are treated as added until saved.
        try:
            setattr(new_item, "__ui_added", True)
        except Exception:
            pass
        try:
            if hasattr(new_item, "__ui_deleted"):
                delattr(new_item, "__ui_deleted")
        except Exception:
            pass
        if insert_after_index is None:
            privs2.append(new_item)
        else:
            at = max(0, min(len(privs2), insert_after_index + 1))
            privs2.insert(at, new_item)
        self.table.rows[new_idx] = DOItem(name=cur2.name, do_type=cur2.do_type, privates=privs2)

        try:
            self.table.refresh()
        except Exception:
            pass

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
        try:
            self.table._push_undo()
        except Exception:
            pass
        try:
            existing = privs[private_index]
            # Added-then-deleted before save: cancel the addition (no red removed state).
            if bool(getattr(existing, "__ui_added", False)):
                privs.pop(private_index)
            else:
                try:
                    setattr(existing, "__ui_deleted", True)
                except Exception:
                    pass
                try:
                    if hasattr(existing, "__ui_added"):
                        delattr(existing, "__ui_added")
                except Exception:
                    pass
        except Exception:
            privs.pop(private_index)
        self.table.rows[idx] = DOItem(name=cur.name, do_type=cur.do_type, privates=privs)
        try:
            self.table.refresh()
        except Exception:
            pass

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

    def _do_type_preview_text(self, do_type_id: str) -> str:
        """Return a readable XML snippet for the selected DOType."""
        do_type_id = (do_type_id or "").strip()
        if not do_type_id:
            return ""

        p = self._do_type_file_path(do_type_id)
        if p is None:
            return ""

        try:
            root = ET.parse(p).getroot()
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

        max_lines = 600
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

        # Fast path: many EnumType files are named exactly as their id.
        # This avoids depending on the full-folder index scan.
        direct = enum_dir / f"{enum_type_id}.xml"
        if direct.is_file():
            cache[enum_type_id] = direct
            return direct

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

    def _enum_type_preview_text(self, enum_type_id: str) -> str:
        """Return a readable XML snippet for the selected EnumType."""
        enum_type_id = (enum_type_id or "").strip()
        if not enum_type_id:
            return ""

        p = self._enum_type_file_path(enum_type_id)
        if p is None:
            return ""

        try:
            root = ET.parse(p).getroot()
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


