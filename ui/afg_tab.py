from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any


__all__ = ["AfgTab"]


class AfgTab(ttk.Frame):
    """AFG tab UI.

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
        ttk.Button(toolbar, text="New", command=o._new_afg).pack(side="left")
        ttk.Button(toolbar, text="Open", command=o._open_afg).pack(side="left", padx=(8, 0))
        o.btn_afg_save = ttk.Button(toolbar, text="Save", command=o._save_afg)
        o.btn_afg_save.pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="Save As", command=o._save_afg_as).pack(side="left", padx=(8, 0))
        o.btn_afg_refresh = ttk.Button(toolbar, text="Refresh", command=o._refresh_afg_from_latest_ln_instance)
        o.btn_afg_refresh.pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="Graph", command=o._afg_arrow_graph).pack(side="left", padx=(18, 0))

        row2 = ttk.Frame(self, padding=(10, 8, 10, 0))
        row2.pack(fill="x")
        ttk.Label(row2, text="Search").pack(side="left")
        o.var_afg_filter = tk.StringVar(value="")
        ent_filter = ttk.Entry(row2, textvariable=o.var_afg_filter, width=28)
        ent_filter.pack(side="left", padx=(8, 0))

        o.var_afg_selected = tk.StringVar(value="")
        o.cb_afg = ttk.Combobox(row2, textvariable=o.var_afg_selected, values=[], width=66)
        o.cb_afg.pack(side="left", padx=(10, 0))
        ttk.Button(row2, text="Load", command=o._open_afg_from_search).pack(side="left", padx=(8, 0))

        o.lbl_afg_match = ttk.Label(row2, text="")
        o.lbl_afg_match.pack(side="left", padx=(10, 0))

        try:
            o.cb_afg.bind("<Return>", lambda _e: o._open_afg_from_search())
        except Exception:
            pass

        body = ttk.Frame(self, padding=10)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)

        meta = ttk.LabelFrame(body, text="AFG", padding=10)
        meta.grid(row=0, column=0, sticky="we")
        for col in (1, 3, 5, 7):
            meta.columnconfigure(col, weight=1)

        o._afg_meta_name = tk.StringVar(value="")
        o._afg_meta_proxy = tk.StringVar(value="")
        o._afg_meta_chapter = tk.StringVar(value="")
        o._afg_meta_topic = tk.StringVar(value="")

        try:
            o._afg_meta_name.trace_add("write", lambda *_a: o._sync_afg_meta_vars_to_root())
            o._afg_meta_proxy.trace_add("write", lambda *_a: o._sync_afg_meta_vars_to_root())
            o._afg_meta_chapter.trace_add("write", lambda *_a: o._sync_afg_meta_vars_to_root())
            o._afg_meta_topic.trace_add("write", lambda *_a: o._sync_afg_meta_vars_to_root())
        except Exception:
            pass

        ttk.Label(meta, text="name").grid(row=0, column=0, sticky="w")
        e_name = ttk.Entry(meta, textvariable=o._afg_meta_name, width=18)
        e_name.grid(row=0, column=1, sticky="we", padx=(6, 12))
        ttk.Label(meta, text="proxyName").grid(row=0, column=2, sticky="w")
        e_proxy = ttk.Entry(meta, textvariable=o._afg_meta_proxy, width=18)
        e_proxy.grid(row=0, column=3, sticky="we", padx=(6, 12))
        ttk.Label(meta, text="chapterName").grid(row=0, column=4, sticky="w")
        e_chapter = ttk.Entry(meta, textvariable=o._afg_meta_chapter, width=18)
        e_chapter.grid(row=0, column=5, sticky="we", padx=(6, 12))
        ttk.Label(meta, text="topicName").grid(row=0, column=6, sticky="w")
        e_topic = ttk.Entry(meta, textvariable=o._afg_meta_topic, width=18)
        e_topic.grid(row=0, column=7, sticky="we", padx=(6, 0))

        for ent in (e_name, e_proxy, e_chapter, e_topic):
            try:
                ent.bind("<FocusOut>", o._afg_end_meta_undo_capture)
                ent.bind("<Return>", o._afg_end_meta_undo_capture)
            except Exception:
                pass

        sub = ttk.Notebook(body)
        sub.grid(row=1, column=0, sticky="nsew", pady=(10, 0))

        tab_fb = ttk.Frame(sub)
        tab_in = ttk.Frame(sub)
        tab_out = ttk.Frame(sub)
        tab_arrows = ttk.Frame(sub)
        sub.add(tab_fb, text="AFBs")
        sub.add(tab_in, text="AFG Inputs")
        sub.add(tab_out, text="AFG Outputs")
        sub.add(tab_arrows, text="AFG Arrows")

        # AFBs tab
        fb_wrap = ttk.Frame(tab_fb)
        fb_wrap.pack(fill="both", expand=True)
        fb_wrap.columnconfigure(0, weight=1)
        fb_wrap.rowconfigure(1, weight=1)

        fb_toolbar = ttk.Frame(fb_wrap, padding=(0, 6, 0, 6))
        fb_toolbar.grid(row=0, column=0, sticky="we")
        ttk.Button(fb_toolbar, text="Add", command=o._afg_fb_add).pack(side="left")
        ttk.Button(fb_toolbar, text="Insert", command=o._afg_fb_insert).pack(side="left", padx=(6, 0))
        ttk.Button(fb_toolbar, text="Edit", command=o._afg_fb_edit).pack(side="left", padx=(6, 0))
        ttk.Button(fb_toolbar, text="Copy", command=o._afg_fb_copy).pack(side="left", padx=(6, 0))
        ttk.Button(fb_toolbar, text="Cut", command=o._afg_fb_cut).pack(side="left", padx=(6, 0))
        ttk.Button(fb_toolbar, text="Paste", command=o._afg_fb_paste).pack(side="left", padx=(6, 0))
        ttk.Button(fb_toolbar, text="Delete", command=o._afg_fb_delete).pack(side="left", padx=(6, 0))
        ttk.Button(fb_toolbar, text="Up", command=o._afg_fb_up).pack(side="left", padx=(18, 0))
        ttk.Button(fb_toolbar, text="Down", command=o._afg_fb_down).pack(side="left", padx=(6, 0))

        left = ttk.LabelFrame(fb_wrap, text="AFBs", padding=6)
        left.grid(row=1, column=0, sticky="nsew")
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)
        o._afg_tv_fb = ttk.Treeview(
            left,
            columns=("name", "posX", "posY", "inputs", "outputs"),
            show="headings",
            selectmode="browse",
        )
        o._afg_tv_fb.heading("name", text="name")
        o._afg_tv_fb.heading("posX", text="posX")
        o._afg_tv_fb.heading("posY", text="posY")
        o._afg_tv_fb.heading("inputs", text="inputs")
        o._afg_tv_fb.heading("outputs", text="outputs")
        o._afg_tv_fb.column("name", width=260, anchor="w")
        o._afg_tv_fb.column("posX", width=90, anchor="e")
        o._afg_tv_fb.column("posY", width=90, anchor="e")
        o._afg_tv_fb.column("inputs", width=70, anchor="center")
        o._afg_tv_fb.column("outputs", width=70, anchor="center")
        o._afg_tv_fb.grid(row=0, column=0, sticky="nsew")
        sb_fb = ttk.Scrollbar(left, orient="vertical", command=o._afg_tv_fb.yview)
        o._afg_tv_fb.configure(yscrollcommand=sb_fb.set)
        sb_fb.grid(row=0, column=1, sticky="ns")

        try:
            o._afg_tv_fb.bind("<Double-1>", o._on_afg_fb_double_click)
            o._afg_tv_fb.bind("<Escape>", lambda _e: o._end_afg_fb_inline_editor(commit=False))
            o._afg_tv_fb.bind("<Button-3>", o._on_afg_fb_right_click)
        except Exception:
            pass

        # AFG Inputs tab
        in_wrap = ttk.Frame(tab_in)
        in_wrap.pack(fill="both", expand=True)
        in_wrap.columnconfigure(0, weight=1)
        in_wrap.rowconfigure(1, weight=1)

        in_toolbar = ttk.Frame(in_wrap, padding=(0, 6, 0, 6))
        in_toolbar.grid(row=0, column=0, columnspan=2, sticky="we")
        ttk.Button(in_toolbar, text="Add", command=o._afg_in_add).pack(side="left")
        ttk.Button(in_toolbar, text="Insert", command=o._afg_in_insert).pack(side="left", padx=(6, 0))
        ttk.Button(in_toolbar, text="Edit", command=o._afg_in_edit).pack(side="left", padx=(6, 0))
        ttk.Button(in_toolbar, text="Copy", command=o._afg_in_copy).pack(side="left", padx=(6, 0))
        ttk.Button(in_toolbar, text="Cut", command=o._afg_in_cut).pack(side="left", padx=(6, 0))
        ttk.Button(in_toolbar, text="Paste", command=o._afg_in_paste).pack(side="left", padx=(6, 0))
        ttk.Button(in_toolbar, text="Delete", command=o._afg_in_delete).pack(side="left", padx=(6, 0))
        ttk.Button(in_toolbar, text="Up", command=o._afg_in_up).pack(side="left", padx=(18, 0))
        ttk.Button(in_toolbar, text="Down", command=o._afg_in_down).pack(side="left", padx=(6, 0))
        o._afg_tv_in = ttk.Treeview(
            in_wrap,
            columns=("name", "posX", "posY", "src", "doRef", "confpin", "softlink"),
            show="headings",
            selectmode="browse",
        )
        for c, h, w, a in (
            ("name", "name", 130, "w"),
            ("posX", "posX", 80, "e"),
            ("posY", "posY", 80, "e"),
            ("src", "src", 320, "w"),
            ("doRef", "doRef", 180, "w"),
            ("confpin", "confpin", 70, "center"),
            ("softlink", "softlink", 70, "center"),
        ):
            o._afg_tv_in.heading(c, text=h)
            o._afg_tv_in.column(c, width=w, anchor=a)
        o._afg_tv_in.grid(row=1, column=0, sticky="nsew")
        sb_io_in = ttk.Scrollbar(in_wrap, orient="vertical", command=o._afg_tv_in.yview)
        o._afg_tv_in.configure(yscrollcommand=sb_io_in.set)
        sb_io_in.grid(row=1, column=1, sticky="ns")

        # AFG Outputs tab
        out_wrap = ttk.Frame(tab_out)
        out_wrap.pack(fill="both", expand=True)
        out_wrap.columnconfigure(0, weight=1)
        out_wrap.rowconfigure(1, weight=1)

        out_toolbar = ttk.Frame(out_wrap, padding=(0, 6, 0, 6))
        out_toolbar.grid(row=0, column=0, columnspan=2, sticky="we")
        ttk.Button(out_toolbar, text="Add", command=o._afg_out_add).pack(side="left")
        ttk.Button(out_toolbar, text="Insert", command=o._afg_out_insert).pack(side="left", padx=(6, 0))
        ttk.Button(out_toolbar, text="Edit", command=o._afg_out_edit).pack(side="left", padx=(6, 0))
        ttk.Button(out_toolbar, text="Copy", command=o._afg_out_copy).pack(side="left", padx=(6, 0))
        ttk.Button(out_toolbar, text="Cut", command=o._afg_out_cut).pack(side="left", padx=(6, 0))
        ttk.Button(out_toolbar, text="Paste", command=o._afg_out_paste).pack(side="left", padx=(6, 0))
        ttk.Button(out_toolbar, text="Delete", command=o._afg_out_delete).pack(side="left", padx=(6, 0))
        ttk.Button(out_toolbar, text="Up", command=o._afg_out_up).pack(side="left", padx=(18, 0))
        ttk.Button(out_toolbar, text="Down", command=o._afg_out_down).pack(side="left", padx=(6, 0))
        o._afg_tv_out = ttk.Treeview(
            out_wrap,
            columns=("name", "posX", "posY", "doRef", "confpin"),
            show="headings",
            selectmode="browse",
        )
        for c, h, w, a in (
            ("name", "name", 130, "w"),
            ("posX", "posX", 80, "e"),
            ("posY", "posY", 80, "e"),
            ("doRef", "doRef", 200, "w"),
            ("confpin", "confpin", 70, "center"),
        ):
            o._afg_tv_out.heading(c, text=h)
            o._afg_tv_out.column(c, width=w, anchor=a)
        o._afg_tv_out.grid(row=1, column=0, sticky="nsew")
        sb_io_out = ttk.Scrollbar(out_wrap, orient="vertical", command=o._afg_tv_out.yview)
        o._afg_tv_out.configure(yscrollcommand=sb_io_out.set)
        sb_io_out.grid(row=1, column=1, sticky="ns")

        try:
            o._afg_tv_in.bind("<Button-1>", o._on_afg_in_click)
            o._afg_tv_in.bind("<Double-1>", o._on_afg_in_double_click)
            o._afg_tv_in.bind("<Escape>", lambda _e: o._end_afg_in_inline_editor(commit=False))
            o._afg_tv_in.bind("<Button-3>", o._on_afg_in_right_click)
            o._afg_tv_out.bind("<Button-1>", o._on_afg_out_click)
            o._afg_tv_out.bind("<Double-1>", o._on_afg_out_double_click)
            o._afg_tv_out.bind("<Escape>", lambda _e: o._end_afg_out_inline_editor(commit=False))
            o._afg_tv_out.bind("<Button-3>", o._on_afg_out_right_click)
        except Exception:
            pass

        # AFG Arrows tab
        arrows_wrap = ttk.Frame(tab_arrows)
        arrows_wrap.pack(fill="both", expand=True)
        arrows_wrap.columnconfigure(0, weight=1)
        arrows_wrap.rowconfigure(1, weight=1)

        arrows_toolbar = ttk.Frame(arrows_wrap, padding=(0, 6, 0, 6))
        arrows_toolbar.grid(row=0, column=0, columnspan=2, sticky="we")
        ttk.Button(arrows_toolbar, text="Add", command=o._afg_arrow_add).pack(side="left")
        ttk.Button(arrows_toolbar, text="Insert", command=o._afg_arrow_insert).pack(side="left", padx=(6, 0))
        ttk.Button(arrows_toolbar, text="Edit", command=o._afg_arrow_edit).pack(side="left", padx=(6, 0))
        ttk.Button(arrows_toolbar, text="Delete", command=o._afg_arrow_delete).pack(side="left", padx=(6, 0))
        ttk.Button(arrows_toolbar, text="Up", command=o._afg_arrow_up).pack(side="left", padx=(18, 0))
        ttk.Button(arrows_toolbar, text="Down", command=o._afg_arrow_down).pack(side="left", padx=(6, 0))

        o._afg_tv_arrows = ttk.Treeview(
            arrows_wrap,
            columns=("startPinID", "startName", "endPinID", "endName"),
            show="headings",
            selectmode="browse",
        )
        for c, h, w, a in (
            ("startPinID", "startPinID", 90, "e"),
            ("startName", "startName", 380, "w"),
            ("endPinID", "endPinID", 90, "e"),
            ("endName", "endName", 380, "w"),
        ):
            o._afg_tv_arrows.heading(c, text=h)
            o._afg_tv_arrows.column(c, width=w, anchor=a)
        o._afg_tv_arrows.grid(row=1, column=0, sticky="nsew")
        sb_ar = ttk.Scrollbar(arrows_wrap, orient="vertical", command=o._afg_tv_arrows.yview)
        o._afg_tv_arrows.configure(yscrollcommand=sb_ar.set)
        sb_ar.grid(row=1, column=1, sticky="ns")

        try:
            o._afg_tv_arrows.bind("<Double-1>", lambda _e: o._afg_arrow_edit())
            o._afg_tv_arrows.bind("<Button-3>", o._on_afg_arrow_right_click)
            o._afg_tv_arrows.bind("<<TreeviewSelect>>", lambda _e: o._on_afg_arrow_select_changed())
        except Exception:
            pass

        o._refresh_afg_search_list(select_rel=None)

        for _tv in (o._afg_tv_fb, o._afg_tv_in, o._afg_tv_out, o._afg_tv_arrows):
            if _tv is None:
                continue
            try:
                _tv.tag_configure("added", background="honeydew2")
                _tv.tag_configure("removed", background="misty rose")
                _tv.tag_configure("changed", background="lemon chiffon")
            except Exception:
                pass

        try:
            o._mark_afg_saved()
        except Exception:
            pass
