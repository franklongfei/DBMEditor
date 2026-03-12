from __future__ import annotations

import os
import re
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
        self.var_langref = tk.StringVar(value=(initial.get("langRef") or ""))

        ttk.Label(frm, text="ord").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=self.var_ord, width=18).grid(row=0, column=1, sticky="we", padx=(10, 0), pady=4)
        ttk.Label(frm, text="val").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=self.var_val, width=52).grid(row=1, column=1, sticky="we", padx=(10, 0), pady=4)
        ttk.Label(frm, text="desc").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=self.var_desc, width=52).grid(row=2, column=1, sticky="we", padx=(10, 0), pady=4)

        ttk.Label(frm, text="langRef").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=self.var_langref, width=24).grid(row=3, column=1, sticky="w", padx=(10, 0), pady=4)

        btns = ttk.Frame(frm)
        btns.grid(row=4, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Cancel", command=self._cancel).pack(side="right")
        ttk.Button(btns, text="OK", command=self._ok).pack(side="right", padx=(0, 8))

        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Return>", lambda _e: self._ok())

    def _ok(self) -> None:
        ord0 = (self.var_ord.get() or "").strip()
        if ord0 and not ord0.isdigit():
            messagebox.showerror("Invalid", "ord must be an integer", parent=self)
            return
        lr = (self.var_langref.get() or "").strip()
        if lr and not re.fullmatch(r"\d+(?:\.\d+)?", lr):
            messagebox.showerror(
                "Invalid",
                "langRef must be empty or like 12 or 12.34",
                parent=self,
            )
            return
        self._result = {
            "ord": ord0,
            "val": (self.var_val.get() or ""),
            "desc": (self.var_desc.get() or ""),
            "langRef": lr,
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

        cols = ["ord", "desc", "val", "langRef"]
        self.tree = ttk.Treeview(content, columns=cols, show="headings", selectmode="browse")
        self.tree.heading("ord", text="ord")
        self.tree.heading("desc", text="desc")
        self.tree.heading("val", text="val")
        self.tree.heading("langRef", text="langRef")
        self.tree.column("ord", width=70, anchor="w", stretch=False)
        self.tree.column("desc", width=520, anchor="w")
        self.tree.column("val", width=240, anchor="w")
        self.tree.column("langRef", width=120, anchor="w", stretch=False)

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

    def mark_saved(self) -> None:
        try:
            self.rows = [r for r in (self.rows or []) if not self._row_is_deleted(r)]
            for r in (self.rows or []):
                r.pop(self._UI_ADDED, None)
                r.pop(self._UI_DELETED, None)
        except Exception:
            pass
        self._saved_sigs = self._snapshot_sigs()
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
            self._end_inline(commit=True)
        except Exception:
            pass

    def set_rows(self, rows: list[dict[str, str]]) -> None:
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
                values=[row.get("ord", ""), row.get("desc", ""), row.get("val", ""), row.get("langRef", "")],
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
        cols = ["ord", "desc", "val", "langRef"]
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

        if col == "langRef":
            v = (value or "").strip()
            if v and not re.fullmatch(r"\d+(?:\.\d+)?", v):
                messagebox.showerror(
                    "Invalid",
                    "langRef must be empty or like 12 or 12.34",
                    parent=self,
                )
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
        get_enum_type_preview: Callable[[str], str] | None = None,
    ):
        super().__init__(parent)
        self.title("New EnumType")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self._result: dict[str, str] | None = None
        self._enum_type_ids = list(enum_type_ids or [])
        self._get_enum_type_preview = get_enum_type_preview
        self._preview_after_id: str | None = None

        self._id_internal_update = False
        self._id_user_modified = False
        self._last_suggested_id = ""

        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text="Create from").grid(row=0, column=0, sticky="w", pady=4)
        self.var_base = tk.StringVar(value="(Blank)")
        base_values = ["(Blank)"] + list(self._enum_type_ids)
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
            # Allow opening the dropdown by clicking anywhere on the combobox,
            # not only on the arrow button.
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

        hint = ttk.Label(frm, text="Tip: If you pick an existing EnumType, EnumVal + LangRef will be copied, then id updated.")
        hint.grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))

        preview_box = ttk.Frame(frm)
        preview_box.grid(row=4, column=0, columnspan=2, sticky="nsew", pady=(10, 0))
        ttk.Label(preview_box, text="EnumType template preview").pack(anchor="w")

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

        frm.rowconfigure(4, weight=1)

        btns = ttk.Frame(frm)
        btns.grid(row=5, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Cancel", command=self._cancel).pack(side="right")
        ttk.Button(btns, text="Create", command=self._ok).pack(side="right", padx=(0, 8))

        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Control-f>", lambda _e: ent_filter.focus_set())

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
        self.var_base.trace_add("write", schedule_preview_update)
        self.var_filter.trace_add("write", apply_filter)
        apply_filter()
        prefill()
        self._update_preview()

        try:
            ent_filter.focus_set()
            ent_filter.select_range(0, tk.END)
        except Exception:
            pass

    def _update_preview(self) -> None:
        get_preview = getattr(self, "_get_enum_type_preview", None)
        txt = getattr(self, "txt_preview", None)
        if txt is None:
            return

        enum_type = (self.var_base.get() or "").strip()

        if enum_type == "(Blank)" or not enum_type:
            preview = ""
        elif not callable(get_preview):
            preview = ""
        else:
            try:
                preview = str(get_preview(enum_type) or "")
            except Exception as e:
                preview = f"(Failed to load preview: {e})"

        if callable(get_preview) and enum_type and enum_type != "(Blank)" and not preview.strip():
            preview = "(EnumType not found)"

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
        base_id = (self.var_base.get() or "").strip()

        if not new_id:
            messagebox.showerror("Missing", "File name is required", parent=self)
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


class EnumTab(ttk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        workspace_root: Path,
        catalog: TypeCatalog,
        set_status: Callable[[str], None] | None = None,
    ):
        super().__init__(parent)
        self.workspace_root = Path(workspace_root)
        self.catalog = catalog
        self._set_status_cb = set_status

        self._type_file_cache: dict[tuple[str, str], Path | None] = {}

        # EnumType editor state
        self._enum_file_path: Path | None = None
        self._enum_root: ET.Element | None = None
        self._enum_enumtype: ET.Element | None = None
        self._enum_preserved_before: list[ET.Element] = []
        self._enum_preserved_after: list[ET.Element] = []
        self._enum_id = tk.StringVar(value="")
        self._enum_table: EnumValTable | None = None
        self._enum_saved_sig: str | None = None
        self._enum_loading: bool = False

        # EnumType: Language reference UI state
        self._enum_details_nb: ttk.Notebook | None = None
        self.var_enum_lang_filter = tk.StringVar(value="")
        self.lbl_enum_lang_match: ttk.Label | None = None
        self._enum_lang_tree: ttk.Treeview | None = None
        self._enum_lang_rows_all: list[dict[str, str]] = []
        self._enum_lang_rows_filtered: list[dict[str, str]] = []
        self._enum_lang_inline: ttk.Entry | None = None
        self._enum_lang_inline_iid: str | None = None

        # EnumType "Search" UI state
        self._all_enum_files: list[str] = []
        self.var_enum_filter = tk.StringVar(value="")
        self.var_enum_selected = tk.StringVar(value="")
        self.cb_enum: ttk.Combobox | None = None
        self.lbl_enum_match: ttk.Label | None = None

        self.btn_save: ttk.Button | None = None

        self._build_ui()

        try:
            self._enum_id.trace_add("write", lambda *_args: self._on_enum_view_changed())
        except Exception:
            pass

        self.refresh_search_list(select_rel=None)
        self.mark_saved()

    # --- UI helpers ---

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

    def _set_status(self, text: str) -> None:
        if self._set_status_cb is None:
            return
        try:
            self._set_status_cb(text)
        except Exception:
            pass

    def _enum_type_dir(self) -> Path:
        return self.workspace_root / "ep7_datamodel" / "datamodel" / "iec61850" / "EnumType"

    def _build_ui(self) -> None:
        toolbar = ttk.Frame(self, padding=(10, 10, 10, 0))
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="New", command=self.new_enum_type_dialog).pack(side="left")
        ttk.Button(toolbar, text="Open", command=self.open_enum_type).pack(side="left", padx=(8, 0))
        self.btn_save = ttk.Button(toolbar, text="Save", command=self.save_enum_type)
        self.btn_save.pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="Save As", command=self.save_enum_type_as).pack(side="left", padx=(8, 0))

        row2 = ttk.Frame(self, padding=(10, 8, 10, 0))
        row2.pack(fill="x")
        ttk.Label(row2, text="Search").pack(side="left")
        ent_filter = ttk.Entry(row2, textvariable=self.var_enum_filter, width=28)
        ent_filter.pack(side="left", padx=(8, 0))

        self.cb_enum = ttk.Combobox(row2, textvariable=self.var_enum_selected, values=[], width=66)
        self.cb_enum.pack(side="left", padx=(10, 0))

        def _open_search_dropdown(_event: tk.Event | None = None) -> None:
            try:
                self.cb_enum.focus_set()
            except Exception:
                pass
            try:
                self.cb_enum.after_idle(lambda: self.cb_enum.event_generate("<Down>"))
            except Exception:
                pass

        try:
            self.cb_enum.bind("<Button-1>", _open_search_dropdown, add="+")
        except Exception:
            pass

        ttk.Button(row2, text="Load", command=self.open_enum_type_from_search).pack(side="left", padx=(8, 0))

        self.lbl_enum_match = ttk.Label(row2, text="")
        self.lbl_enum_match.pack(side="left", padx=(10, 0))

        body = ttk.Frame(self, padding=10)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)

        meta = ttk.LabelFrame(body, text="EnumType", padding=10)
        meta.grid(row=0, column=0, sticky="we")
        meta.columnconfigure(1, weight=1)

        ttk.Label(meta, text="id").grid(row=0, column=0, sticky="w")
        ttk.Entry(meta, textvariable=self._enum_id, width=62).grid(row=0, column=1, sticky="we", padx=(6, 0))

        self._enum_details_nb = None

        self._enum_table = EnumValTable(body)
        self._enum_table.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        try:
            self._enum_table.on_change = lambda: self._on_enum_view_changed()
        except Exception:
            pass

        self.lbl_enum_lang_match = None
        self._enum_lang_tree = None

        try:
            self.cb_enum.bind("<Return>", lambda _e: self.open_enum_type_from_search())
        except Exception:
            pass

    # --- Dirty tracking ---

    def _enum_signature_from_view(self) -> str:
        enum_id = (self._enum_id.get() or "").strip()
        rows: list[dict[str, str]] = []
        if self._enum_table is not None:
            try:
                self._enum_table.commit_any_edit()
            except Exception:
                pass
            try:
                rows = self._enum_table.get_rows()
            except Exception:
                rows = []

        norm_rows = [
            (
                (r.get("ord") or ""),
                (r.get("desc") or ""),
                (r.get("val") or ""),
                (r.get("langRef") or ""),
            )
            for r in (rows or [])
        ]
        return repr((enum_id, norm_rows))

    def update_dirty_ui(self) -> None:
        if getattr(self, "_enum_loading", False):
            return
        cur = self._enum_signature_from_view()
        dirty = (self._enum_saved_sig is None) or (cur != self._enum_saved_sig)
        self._set_save_button_dirty(dirty=dirty)

    def _on_enum_view_changed(self) -> None:
        if getattr(self, "_enum_loading", False):
            return
        self.update_dirty_ui()

    def mark_saved(self) -> None:
        try:
            self._enum_saved_sig = self._enum_signature_from_view()
        except Exception:
            self._enum_saved_sig = ""
        try:
            if self._enum_table is not None:
                self._enum_table.mark_saved()
        except Exception:
            pass
        self.update_dirty_ui()

    def mark_unsaved(self) -> None:
        self._enum_saved_sig = None
        self.update_dirty_ui()

    # --- Search list ---

    def refresh_search_list(self, *, select_rel: str | None) -> None:
        if self.cb_enum is None or self.lbl_enum_match is None:
            return
        enum_dir = self._enum_type_dir()
        self._all_enum_files = scan_xml_relpaths(enum_dir)

        def apply_filter(*_args) -> None:
            raw = (self.var_enum_filter.get() or "").strip().lower()
            if not raw:
                filtered = list(self._all_enum_files)
            else:
                tokens = [t for t in raw.split() if t]

                def ok(v: str) -> bool:
                    lv = (v or "").lower()
                    return all(t in lv for t in tokens)

                filtered = [v for v in self._all_enum_files if ok(v)]
                filtered = _sort_filter_matches(raw, filtered)

            cur = (self.var_enum_selected.get() or "").strip()

            max_show = 1200
            shown = filtered[:max_show]
            self.cb_enum["values"] = shown
            if raw and filtered:
                if cur != filtered[0]:
                    self.var_enum_selected.set(filtered[0])
            elif (not raw) and cur and (cur not in filtered):
                self.var_enum_selected.set("")
            suffix = "" if len(filtered) <= max_show else f" (showing first {max_show})"
            self.lbl_enum_match.configure(text=f"{len(filtered)} match{'' if len(filtered)==1 else 'es'}{suffix}")

        if getattr(self, "_enum_apply_filter", None) is None:
            try:
                self.var_enum_filter.trace_add("write", apply_filter)
            except Exception:
                pass
            setattr(self, "_enum_apply_filter", apply_filter)
        else:
            apply_filter = getattr(self, "_enum_apply_filter")

        if select_rel:
            try:
                self.var_enum_selected.set(select_rel)
            except Exception:
                pass
        apply_filter()

    # --- Core actions (New/Open/Save) ---

    def _enum_langref_private_type(self) -> str:
        return "SchneiderElectric-PowerLogic-LangRef"

    def new_enum_type_dialog(self) -> None:
        if self._enum_table is None:
            return
        enum_ids = list(self.catalog.enum_types or [])
        dlg = NewEnumTypeDialog(
            self,
            enum_type_ids=enum_ids,
            get_enum_type_preview=self._enum_type_preview_text,
        )
        res = dlg.show()
        if not res:
            return
        base_id = (res.get("base_id") or "").strip()
        new_id = (res.get("id") or "").strip()

        if base_id and base_id != "(Blank)":
            enum_dir = self._enum_type_dir()
            src_path = find_type_file(kind_dir=enum_dir, type_id=base_id, cache=self._type_file_cache)
            if src_path is None:
                messagebox.showerror("Missing", f"Source EnumType not found: {base_id}", parent=self)
                return
            self.open_enum_type_from_path(src_path)
            self._enum_file_path = None
            try:
                self.var_enum_selected.set("")
            except Exception:
                pass
            try:
                if self._enum_table is not None:
                    self._enum_table.mark_all_rows_added()
            except Exception:
                pass
        else:
            self.new_enum_type()

        try:
            self._enum_id.set(new_id)
        except Exception:
            pass

        self.mark_unsaved()
        self._set_status(
            (f"New EnumType created from {base_id} (unsaved)" if base_id and base_id != "(Blank)" else "New EnumType created (unsaved)")
        )

    def new_enum_type(self) -> None:
        if self._enum_table is None:
            return
        ns = SCL_NS
        root = ET.Element(qname(ns, "SCL"))
        enum_el = ET.SubElement(root, qname(ns, "EnumType"))
        enum_el.attrib["id"] = ""

        self._enum_root = root
        self._enum_enumtype = enum_el
        self._enum_preserved_before = []
        self._enum_preserved_after = []

        self._enum_loading = True
        try:
            self._enum_id.set("")
            self._enum_table.set_rows([])
            self._enum_file_path = None
            try:
                self.refresh_language_reference()
            except Exception:
                pass
        finally:
            self._enum_loading = False

        self.mark_unsaved()

    def open_enum_type(self) -> None:
        enum_dir = self._enum_type_dir()
        initialdir = enum_dir if enum_dir.exists() else self.workspace_root
        target = filedialog.askopenfilename(
            parent=self,
            title="Open EnumType file",
            initialdir=os.fspath(initialdir),
            filetypes=[("XML", "*.xml"), ("All", "*")],
        )
        if not target:
            return
        self.open_enum_type_from_path(Path(target))

    def open_enum_type_from_search(self) -> None:
        rel = (self.var_enum_selected.get() or "").strip()
        if not rel:
            return
        enum_dir = self._enum_type_dir()
        target = enum_dir / rel
        if not target.exists():
            messagebox.showerror("Missing", f"File not found:\n\n{os.fspath(target)}", parent=self)
            return
        self.open_enum_type_from_path(target)

    def open_enum_type_from_path(self, path: Path) -> None:
        path = Path(path)
        if self._enum_table is None:
            return

        try:
            tree = ET.parse(path)
            root = tree.getroot()
        except Exception as e:
            messagebox.showerror("Open failed", str(e), parent=self)
            return

        ns = ""
        if isinstance(root.tag, str) and root.tag.startswith("{"):
            ns = root.tag.split("}", 1)[0][1:]
        ns = ns or SCL_NS

        enum_el = None
        for cand in root.iter():
            if not isinstance(cand.tag, str):
                continue
            if local_name(cand.tag) == "EnumType":
                enum_el = cand
                break
        if enum_el is None:
            messagebox.showerror("Invalid", "No <EnumType> found in file", parent=self)
            return

        preserved_before: list[ET.Element] = []
        preserved_after: list[ET.Element] = []
        rows: list[dict[str, str]] = []

        seen_first_enumval = False
        lang_privs: list[ET.Element] = []
        for ch in list(enum_el):
            if not isinstance(ch.tag, str):
                (preserved_after if seen_first_enumval else preserved_before).append(deepcopy_et_element(ch))
                continue
            ln = local_name(ch.tag)
            if ln == "EnumVal":
                seen_first_enumval = True
                rows.append(
                    {
                        "ord": (ch.attrib.get("ord") or ""),
                        "desc": (ch.attrib.get("desc") or ""),
                        "val": (ch.text or ""),
                        "langRef": "",
                    }
                )
                continue
            if ln == "Private" and (ch.attrib.get("type") or "").strip() == self._enum_langref_private_type():
                lang_privs.append(ch)
                continue
            (preserved_after if seen_first_enumval else preserved_before).append(deepcopy_et_element(ch))

        for i in range(len(rows)):
            txt = ""
            if i < len(lang_privs):
                try:
                    txt = (lang_privs[i].text or "").strip()
                except Exception:
                    txt = ""
            rows[i]["langRef"] = txt

        self._enum_root = root
        self._enum_enumtype = enum_el
        self._enum_preserved_before = preserved_before
        self._enum_preserved_after = preserved_after

        self._enum_loading = True
        try:
            self._enum_id.set((enum_el.attrib.get("id") or "").strip())
            self._enum_table.set_rows(rows)
            self._enum_file_path = path
        finally:
            self._enum_loading = False

        try:
            enum_dir = self._enum_type_dir()
            rel = os.fspath(path.resolve().relative_to(enum_dir.resolve()))
            self.var_enum_selected.set(rel)
            self.refresh_search_list(select_rel=rel)
        except Exception:
            self.refresh_search_list(select_rel=None)

        try:
            self.refresh_language_reference()
        except Exception:
            pass

        self.mark_saved()
        self._set_status(f"Opened EnumType: {os.fspath(path)}")

    def _enum_type_preview_text(self, enum_type_id: str) -> str:
        enum_type_id = (enum_type_id or "").strip()
        if not enum_type_id:
            return ""

        enum_dir = self._enum_type_dir()
        p = find_type_file(kind_dir=enum_dir, type_id=enum_type_id, cache=self._type_file_cache)
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

    def save_enum_type(self) -> None:
        if self._enum_table is None:
            return

        try:
            self._end_enum_lang_inline(commit=True)
        except Exception:
            pass
        try:
            self._enum_table.commit_any_edit()
        except Exception:
            pass

        enum_id = (self._enum_id.get() or "").strip()
        if not enum_id:
            messagebox.showerror("Missing", "EnumType id is required (used as file name)", parent=self)
            return

        rows = self._enum_table.get_rows()
        for r in rows:
            o = (r.get("ord") or "").strip()
            if o and not o.isdigit():
                messagebox.showerror("Invalid", f"ord must be an integer: {o}", parent=self)
                return

        stem = re.sub(r'[<>:"/\\|?*]', "_", enum_id).strip() or "EnumType"
        target_path = self._enum_type_dir() / f"{stem}.xml"

        try:
            self._apply_enum_ui_to_xml()
            self._write_enum_type_xml(target_path)
        except Exception as e:
            messagebox.showerror("Save failed", str(e), parent=self)
            return

        self._enum_file_path = target_path
        try:
            enum_dir = self._enum_type_dir()
            rel = os.fspath(target_path.relative_to(enum_dir))
        except Exception:
            rel = os.fspath(target_path.name)
        self.refresh_search_list(select_rel=rel)

        try:
            if enum_id not in self.catalog.enum_types:
                self.catalog.enum_types.append(enum_id)
                self.catalog.enum_types.sort(key=lambda s: (s or "").lower())
        except Exception:
            pass

        self._set_status(f"Saved EnumType: {os.fspath(target_path)}")
        self.mark_saved()

    def save_enum_type_as(self) -> None:
        cur_id = (self._enum_id.get() or "").strip()
        initial = f"{cur_id}_copy" if cur_id else ""
        dlg = _EnumTypeSaveAsDialog(self, initial_id=initial)
        new_id = dlg.show()
        if not new_id:
            return

        stem = re.sub(r'[<>:"/\\|?*]', "_", new_id).strip() or "EnumType"
        target_path = self._enum_type_dir() / f"{stem}.xml"

        try:
            cur_path = self._enum_file_path.resolve() if self._enum_file_path is not None else None
        except Exception:
            cur_path = self._enum_file_path
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
        old_path = self._enum_file_path
        try:
            self._enum_id.set(new_id)
        except Exception:
            return

        self.save_enum_type()

        try:
            saved_ok = self._enum_file_path is not None and self._enum_file_path.resolve() == tgt_resolved
        except Exception:
            saved_ok = self._enum_file_path == target_path

        if not saved_ok:
            try:
                self._enum_id.set(old_id)
            except Exception:
                pass
            self._enum_file_path = old_path

    def _apply_enum_ui_to_xml(self) -> None:
        if self._enum_table is None:
            return
        if self._enum_root is None or self._enum_enumtype is None:
            self.new_enum_type()
        if self._enum_root is None or self._enum_enumtype is None:
            return

        root = self._enum_root
        enum_el = self._enum_enumtype

        ns = ""
        if isinstance(root.tag, str) and root.tag.startswith("{"):
            ns = root.tag.split("}", 1)[0][1:]
        ns = ns or SCL_NS

        enum_el.attrib["id"] = (self._enum_id.get() or "").strip()

        for ch in list(enum_el):
            enum_el.remove(ch)

        for el in (self._enum_preserved_before or []):
            enum_el.append(deepcopy_et_element(el))

        rows = self._enum_table.get_rows()
        for r in rows:
            p = ET.Element(qname(ns, "Private"))
            p.attrib["type"] = self._enum_langref_private_type()
            p.text = (r.get("langRef") or "").strip()
            enum_el.append(p)

        for r in rows:
            ev = ET.Element(qname(ns, "EnumVal"))
            o = (r.get("ord") or "").strip()
            if o:
                ev.attrib["ord"] = o
            d = r.get("desc") or ""
            if (d or "").strip() or "desc" in ev.attrib:
                if (d or "") != "":
                    ev.attrib["desc"] = d
            ev.text = r.get("val") or ""
            enum_el.append(ev)

        for el in (self._enum_preserved_after or []):
            enum_el.append(deepcopy_et_element(el))

    def _write_enum_type_xml(self, path: Path) -> None:
        if self._enum_root is None or self._enum_enumtype is None:
            raise ValueError("No EnumType loaded")

        root = self._enum_root

        ns = ""
        if isinstance(root.tag, str) and root.tag.startswith("{"):
            ns = root.tag.split("}", 1)[0][1:]

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

    # --- Language reference ---

    def _on_enum_details_tab_changed(self) -> None:
        try:
            if self._enum_details_nb is None:
                return
            idx = self._enum_details_nb.index("current")
        except Exception:
            return
        if idx == 1:
            try:
                self.refresh_language_reference()
            except Exception:
                pass

    def refresh_language_reference(self) -> None:
        if self._enum_lang_tree is None or self._enum_table is None:
            return

        try:
            self._enum_lang_tree.tag_configure("added", background="honeydew2")
            self._enum_lang_tree.tag_configure("removed", background="misty rose")
            self._enum_lang_tree.tag_configure("changed", background="lemon chiffon")
        except Exception:
            pass

        rows = list(self._enum_table.rows or [])
        view_rows: list[dict[str, str]] = []
        for i, r in enumerate(rows):
            ord0 = (r.get("ord") or "").strip()
            val0 = (r.get("val") or "").strip()
            name = f"{ord0}: {val0}" if ord0 or val0 else f"#{i}"
            view_rows.append(
                {
                    "idx": str(i),
                    "name": name,
                    "id": (r.get("langRef") or "").strip(),
                    "desc": (r.get("desc") or ""),
                }
            )

        self._enum_lang_rows_all = view_rows
        self._apply_enum_lang_filter()

    def _clear_enum_lang_filter(self) -> None:
        try:
            self.var_enum_lang_filter.set("")
        except Exception:
            pass

    def _apply_enum_lang_filter(self) -> None:
        if self._enum_lang_tree is None or self.lbl_enum_lang_match is None:
            return
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
                            changed = bool(self._enum_table._row_is_changed_at_index(src_idx))
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
            self.refresh_language_reference()
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
            self.refresh_language_reference()
        except Exception:
            pass
