from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any


__all__ = ["ApplicationTab"]


class ApplicationTab(ttk.Frame):
    """Application tab UI.

    This is intentionally UI-only: it wires widgets to the existing handler
    methods that live on the owning window (currently `MainWindow` in
    `ln_template_editor_ui.py`).

    Splitting like this matches the existing tab extraction style used by
    `EnumTab` / `DoTemplateTab` while minimizing behavioral risk.
    """

    def __init__(self, parent: tk.Misc, *, owner: Any):
        super().__init__(parent)
        self.owner = owner
        self._build_ui()

    def _build_ui(self) -> None:
        o = self.owner

        if getattr(o, "instance_editor", None) is None:
            # Keep the tab constructible even if created early.
            ttk.Label(self, text="(LN instance editor not initialized)").pack(anchor="w", padx=10, pady=10)
            return

        toolbar = ttk.Frame(self, padding=(10, 10, 10, 0))
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="New", command=o._new_application).pack(side="left")
        ttk.Button(toolbar, text="Open", command=o._open_application).pack(side="left", padx=(8, 0))
        o.btn_app_save = ttk.Button(toolbar, text="Save", command=o._save_application)
        o.btn_app_save.pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="Save As", command=o._save_application_as).pack(side="left", padx=(8, 0))

        o.btn_app_refresh = ttk.Button(toolbar, text="Refresh", command=o._refresh_application_from_latest_ln_instance)
        o.btn_app_refresh.pack(side="left", padx=(8, 0))

        row2 = ttk.Frame(self, padding=(10, 8, 10, 0))
        row2.pack(fill="x")
        ttk.Label(row2, text="Search").pack(side="left")
        o.var_app_filter = tk.StringVar(value="")
        ent_filter = ttk.Entry(row2, textvariable=o.var_app_filter, width=28)
        ent_filter.pack(side="left", padx=(8, 0))

        o.var_app_selected = tk.StringVar(value="")
        o.cb_app = ttk.Combobox(row2, textvariable=o.var_app_selected, values=[], width=66)
        o.cb_app.pack(side="left", padx=(10, 0))
        ttk.Button(row2, text="Load", command=o._open_application_from_search).pack(side="left", padx=(8, 0))

        o.lbl_app_match = ttk.Label(row2, text="")
        o.lbl_app_match.pack(side="left", padx=(10, 0))

        # Keep behavior consistent with previous inlined UI: Ctrl+F focuses Search.
        try:
            o.bind("<Control-f>", lambda _e: ent_filter.focus_set())
        except Exception:
            pass

        try:
            o.cb_app.bind("<Return>", lambda _e: o._open_application_from_search())
        except Exception:
            pass

        body = ttk.Frame(self, padding=10)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)

        fb = ttk.LabelFrame(body, text="funBlock", padding=10)
        fb.grid(row=0, column=0, sticky="we")
        # One-row fields
        for col in (1, 3, 7, 9):
            fb.columnconfigure(col, weight=1)

        ttk.Label(fb, text="name").grid(row=0, column=0, sticky="w")
        ttk.Entry(fb, textvariable=o.instance_editor.var_app_name, width=18).grid(
            row=0, column=1, sticky="we", padx=(6, 12)
        )

        ttk.Label(fb, text="class").grid(row=0, column=2, sticky="w")
        ttk.Entry(fb, textvariable=o.instance_editor.var_app_class, width=18).grid(
            row=0, column=3, sticky="we", padx=(6, 12)
        )

        ttk.Label(fb, text="seqNb").grid(row=0, column=4, sticky="w")
        ttk.Entry(fb, textvariable=o.instance_editor.var_app_seqNb, width=6).grid(
            row=0, column=5, sticky="w", padx=(6, 12)
        )

        ttk.Label(fb, text="LnRef").grid(row=0, column=6, sticky="w")
        ttk.Entry(fb, textvariable=o.instance_editor.var_app_LnRef, width=22).grid(
            row=0, column=7, sticky="we", padx=(6, 12)
        )

        ttk.Label(fb, text="desc").grid(row=0, column=8, sticky="w")
        ttk.Entry(fb, textvariable=o.instance_editor.var_app_desc, width=30).grid(
            row=0, column=9, sticky="we", padx=(6, 0)
        )

        try:
            o._wire_application_funblock_traces()
        except Exception:
            pass

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

        def _make_tv(parent0: tk.Misc, cols: list[str], heads: list[str]) -> ttk.Treeview:
            wrap = ttk.Frame(parent0)
            wrap.pack(fill="both", expand=True)
            wrap.columnconfigure(0, weight=1)
            wrap.rowconfigure(1, weight=1)

            # Toolbar
            tb = ttk.Frame(wrap, padding=(0, 6, 0, 6))
            tb.grid(row=0, column=0, columnspan=2, sticky="we")

            def _btn(label: str, cmd, padx=(0, 0)) -> None:
                ttk.Button(tb, text=label, command=cmd).pack(side="left", padx=padx)

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

        o._app_tv_input = _make_tv(
            tab_in,
            ["name", "type", "src", "doRef", "softlink", "confpin"],
            ["name", "type", "src", "doRef", "softlink", "confpin"],
        )
        o._app_tv_setting = _make_tv(tab_set, ["name", "type", "src", "desc"], ["name", "type", "src", "desc"])
        o._app_tv_output = _make_tv(
            tab_out,
            ["name", "type", "doRef", "MaxContiguous", "Overlap", "persist", "faultlog", "desc"],
            ["name", "type", "doRef", "MaxContiguous", "Overlap", "persist", "faultlog", "desc"],
        )
        o._app_tv_conf = _make_tv(tab_conf, ["name", "type", "src", "desc"], ["name", "type", "src", "desc"])
        o._app_tv_control = _make_tv(tab_ctl, ["name", "type", "src", "desc"], ["name", "type", "src", "desc"])

        # Diff highlight tags (used by Application Refresh)
        for _tv in (o._app_tv_input, o._app_tv_setting, o._app_tv_output, o._app_tv_conf, o._app_tv_control):
            if _tv is None:
                continue
            try:
                _tv.tag_configure("added", background="honeydew2")
                _tv.tag_configure("removed", background="misty rose")
                _tv.tag_configure("changed", background="lemon chiffon")
            except Exception:
                pass

        # Hook toolbars + context menus
        o._init_app_table_ui("input", o._app_tv_input)

        # Populate application list for search combobox.
        o._refresh_application_search_list(select_rel=None)

        try:
            o._mark_application_saved()
        except Exception:
            pass

        try:
            o._update_app_refresh_button_state()
        except Exception:
            pass

        o._init_app_table_ui("setting", o._app_tv_setting)
        o._init_app_table_ui("output", o._app_tv_output)
        o._init_app_table_ui("conf", o._app_tv_conf)
        o._init_app_table_ui("control", o._app_tv_control)

        # Inline edit bindings
        if o._app_tv_input is not None:
            o._app_tv_input.bind("<Button-1>", o._on_app_input_click)
            o._app_tv_input.bind("<Escape>", lambda _e: o._end_app_input_inline_editor(commit=False))
            o._app_tv_input.bind("<Double-1>", o._on_app_input_double_click)

        if o._app_tv_setting is not None:
            o._app_tv_setting.bind("<Button-1>", o._on_app_setting_click)
            o._app_tv_setting.bind("<Double-1>", o._on_app_setting_double_click)
            o._app_tv_setting.bind("<Escape>", lambda _e: o._end_app_setting_inline_editor(commit=False))

        if o._app_tv_output is not None:
            o._app_tv_output.bind("<Button-1>", o._on_app_output_click)
            o._app_tv_output.bind("<Double-1>", o._on_app_output_double_click)
            o._app_tv_output.bind("<Escape>", lambda _e: o._end_app_output_inline_editor(commit=False))

        if o._app_tv_conf is not None:
            o._app_tv_conf.bind("<Button-1>", o._on_app_conf_click)
            o._app_tv_conf.bind("<Double-1>", o._on_app_conf_double_click)
            o._app_tv_conf.bind("<Escape>", lambda _e: o._end_app_conf_inline_editor(commit=False))

        if o._app_tv_control is not None:
            o._app_tv_control.bind("<Button-1>", o._on_app_control_click)
            o._app_tv_control.bind("<Double-1>", o._on_app_control_double_click)
            o._app_tv_control.bind("<Escape>", lambda _e: o._end_app_control_inline_editor(commit=False))
