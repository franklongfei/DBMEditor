from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any


__all__ = ["HmiTab"]


class HmiTab(ttk.Frame):
    """HMI tab UI.

    UI-only wrapper extracted from `ln_template_editor_ui.py`.
    All existing handlers / model logic remain on the owning window.
    """

    def __init__(self, parent: tk.Misc, *, owner: Any):
        super().__init__(parent)
        self.owner = owner
        self._build_ui()

    def _build_ui(self) -> None:
        o = self.owner

        toolbar = ttk.Frame(self, padding=(10, 10, 10, 0))
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="New", command=o._new_hmi).pack(side="left")
        ttk.Button(toolbar, text="Open", command=o._open_hmi).pack(side="left", padx=(8, 0))
        o.btn_hmi_save = ttk.Button(toolbar, text="Save", command=o._save_hmi)
        o.btn_hmi_save.pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="Save As", command=o._save_hmi_as).pack(side="left", padx=(8, 0))
        o.btn_hmi_refresh = ttk.Button(toolbar, text="Refresh", command=o._hmi_generate_from_application)
        o.btn_hmi_refresh.pack(side="left", padx=(8, 0))

        row2 = ttk.Frame(self, padding=(10, 8, 10, 0))
        row2.pack(fill="x")
        ttk.Label(row2, text="Search").pack(side="left")
        o.var_hmi_filter = tk.StringVar(value="")
        ent_filter = ttk.Entry(row2, textvariable=o.var_hmi_filter, width=28)
        ent_filter.pack(side="left", padx=(8, 0))

        o.var_hmi_selected = tk.StringVar(value="")
        o.cb_hmi = ttk.Combobox(row2, textvariable=o.var_hmi_selected, values=[], width=66)
        o.cb_hmi.pack(side="left", padx=(10, 0))
        ttk.Button(row2, text="Load", command=o._open_hmi_from_search).pack(side="left", padx=(8, 0))

        o.lbl_hmi_match = ttk.Label(row2, text="")
        o.lbl_hmi_match.pack(side="left", padx=(10, 0))

        try:
            if o.cb_hmi is not None:
                o.cb_hmi.bind("<Return>", lambda _e: o._open_hmi_from_search())
        except Exception:
            pass

        body = ttk.Frame(self, padding=10)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)

        sub = ttk.Notebook(body)
        sub.grid(row=0, column=0, sticky="nsew")
        tab_ied = ttk.Frame(sub)
        tab_iet = ttk.Frame(sub)
        tab_manual = ttk.Frame(sub)
        sub.add(tab_ied, text="IED")
        sub.add(tab_iet, text="IET")
        sub.add(tab_manual, text="Manual")

        def _make_action_row(parent0: tk.Misc, *, prefix: str) -> None:
            btns = ttk.Frame(parent0)
            btns.pack(fill="x", pady=(0, 8))

            setattr(o, f"_hmi_btn_add_{prefix}", ttk.Button(btns, text="Add", command=o._hmi_action_add))
            getattr(o, f"_hmi_btn_add_{prefix}").pack(side="left")

            setattr(o, f"_hmi_btn_insert_{prefix}", ttk.Button(btns, text="Insert", command=o._hmi_action_insert))
            getattr(o, f"_hmi_btn_insert_{prefix}").pack(side="left", padx=(6, 0))

            setattr(o, f"_hmi_btn_edit_{prefix}", ttk.Button(btns, text="Edit", command=o._hmi_action_edit))
            getattr(o, f"_hmi_btn_edit_{prefix}").pack(side="left", padx=(6, 0))

            setattr(o, f"_hmi_btn_copy_{prefix}", ttk.Button(btns, text="Copy", command=o._hmi_action_copy))
            getattr(o, f"_hmi_btn_copy_{prefix}").pack(side="left", padx=(6, 0))

            setattr(o, f"_hmi_btn_cut_{prefix}", ttk.Button(btns, text="Cut", command=o._hmi_action_cut))
            getattr(o, f"_hmi_btn_cut_{prefix}").pack(side="left", padx=(6, 0))

            setattr(o, f"_hmi_btn_paste_{prefix}", ttk.Button(btns, text="Paste", command=o._hmi_action_paste))
            getattr(o, f"_hmi_btn_paste_{prefix}").pack(side="left", padx=(6, 0))

            setattr(o, f"_hmi_btn_delete_{prefix}", ttk.Button(btns, text="Delete", command=o._hmi_action_delete))
            getattr(o, f"_hmi_btn_delete_{prefix}").pack(side="left", padx=(6, 0))

            setattr(o, f"_hmi_btn_up_{prefix}", ttk.Button(btns, text="Up", command=o._hmi_action_move_up))
            getattr(o, f"_hmi_btn_up_{prefix}").pack(side="left", padx=(18, 0))

            setattr(o, f"_hmi_btn_down_{prefix}", ttk.Button(btns, text="Down", command=o._hmi_action_move_down))
            getattr(o, f"_hmi_btn_down_{prefix}").pack(side="left", padx=(6, 0))

            # Fold/Unfold all: single toggle button (same UX as LN instance).
            setattr(o, f"_hmi_btn_fold_all_{prefix}", ttk.Button(btns, text="Fold all", command=o._hmi_toggle_fold_all))
            getattr(o, f"_hmi_btn_fold_all_{prefix}").pack(side="right")

        def _make_menu_tv(parent0: tk.Misc, *, columns: tuple[str, ...]) -> ttk.Treeview:
            box = ttk.Frame(parent0, padding=6)
            box.pack(fill="both", expand=True)
            box.columnconfigure(0, weight=1)
            box.rowconfigure(0, weight=1)
            tv = ttk.Treeview(
                box,
                columns=columns,
                show="tree headings",
                selectmode="browse",
            )
            try:
                tv.heading("#0", text="name")
                tv.column("#0", width=240, minwidth=60, anchor="w", stretch=True)
                # Configure headings/widths for known columns (others use defaults).
                cfg = {
                    "desc": ("desc", 160, "w"),
                    "value": ("value", 120, "w"),
                    "instantiate": ("Not instantiate", 110, "center"),
                    "langRef": ("langRef", 120, "w"),
                    "hmiMenuDataType": ("hmiMenuDataType", 140, "w"),
                    "hmiMenuViewType": ("hmiMenuViewType", 140, "w"),
                    "hmiSubTreeType": ("hmiSubTreeType", 140, "w"),
                    "doRef": ("doRef", 200, "w"),
                    "daRef": ("daRef", 110, "w"),
                    "hideunit": ("In PU value", 90, "center"),
                }
                for c in columns:
                    h, w, anchor = cfg.get(c, (c, 110, "w"))
                    tv.heading(c, text=h)
                    tv.column(c, width=w, minwidth=40, anchor=anchor, stretch=True)
            except Exception:
                pass
            try:
                tv.tag_configure("added", background="honeydew2")
                tv.tag_configure("changed", background="lemon chiffon")
                tv.tag_configure("removed", background="misty rose")
            except Exception:
                pass

            tv.grid(row=0, column=0, sticky="nsew")
            sb = ttk.Scrollbar(box, orient="vertical", command=tv.yview)
            tv.configure(yscrollcommand=sb.set)
            sb.grid(row=0, column=1, sticky="ns")

            try:
                tv.bind("<Double-1>", o._hmi_on_tree_double_click)
                tv.bind("<Configure>", lambda _e: o._hmi_schedule_column_resize())
                tv.bind("<ButtonRelease-1>", o._hmi_on_tree_mouse_release)
                tv.bind("<Button-3>", o._hmi_on_tree_right_click)
                tv.bind("<<TreeviewSelect>>", lambda _e: o._hmi_update_hmi_action_state())
                tv.bind("<Delete>", lambda _e: (o._hmi_action_delete(), "break"))
            except Exception:
                pass
            return tv

        # Each sub-tab owns its own action buttons row (like Application input/output).
        _make_action_row(tab_ied, prefix="ied")
        _make_action_row(tab_iet, prefix="iet")
        _make_action_row(tab_manual, prefix="manual")

        o._hmi_tv_menus_ied = _make_menu_tv(
            tab_ied,
            columns=(
                "desc",
                "instantiate",
                "langRef",
                "hmiMenuDataType",
                "hmiMenuViewType",
                "hmiSubTreeType",
                "doRef",
                "daRef",
                "hideunit",
            ),
        )
        # IET view: reduced columns.
        o._hmi_tv_menus_iet = _make_menu_tv(
            tab_iet,
            columns=(
                "desc",
                "value",
                "hmiMenuDataType",
                "hmiMenuViewType",
                "hmiSubTreeType",
            ),
        )

        # Manual view: only keep requested columns.
        o._hmi_tv_menus_manual = _make_menu_tv(
            tab_manual,
            columns=(
                "doRef",
                "daRef",
                "hideunit",
            ),
        )

        def _on_sub_tab_changed(_e=None) -> None:
            try:
                cur = sub.select()
            except Exception:
                cur = ""
            if cur == str(tab_ied):
                scope = "ied"
            elif cur == str(tab_iet):
                scope = "iet"
            else:
                scope = "manual"
            try:
                o._hmi_set_scope(scope)
            except Exception:
                pass

        try:
            sub.bind("<<NotebookTabChanged>>", _on_sub_tab_changed)
        except Exception:
            pass

        try:
            o._refresh_hmi_search_list(select_rel=None)
        except Exception:
            pass

        try:
            o._mark_hmi_saved()
        except Exception:
            pass

        # Default: start with IED scope.
        try:
            o._hmi_set_scope("ied")
        except Exception:
            pass
