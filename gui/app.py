from __future__ import annotations

import csv
import math
import os
import queue
import re
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Dict, List, Optional

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)

from core.workspace_manager import WorkspaceManager
from gui.bridge   import QueryBridge, QueryResult
from gui.explorer import PageViewer

from parsing.sql_analyzer import QueryCompiler
from parsing.ast_nodes import CreateSchemaNode

# ---------------------------------------------------------------------------
# Colour palette
# ---------------------------------------------------------------------------
_C = {
    "bg":        "#1e1e2e",
    "surface":   "#181825",
    "mantle":    "#11111b",
    "overlay0":  "#6c7086",
    "overlay2":  "#9399b2",
    "text":      "#cdd6f4",
    "lavender":  "#b4befe",
    "blue":      "#89b4fa",
    "sapphire":  "#74c7ec",
    "sky":       "#89dceb",
    "teal":      "#94e2d5",
    "green":     "#a6e3a1",
    "yellow":    "#f9e2af",
    "peach":     "#fab387",
    "red":       "#f38ba8",
    "mauve":     "#cba6f7",
    "violet":    "#7c3aed",
    "row_alt":   "#313244",
}

_FONT_MONO = ("Consolas",  10)
_FONT_UI   = ("Segoe UI",  10)
_FONT_BOLD = ("Segoe UI",  10, "bold")
_FONT_H    = ("Segoe UI",  12, "bold")
_FONT_TINY = ("Consolas",   8)

_EXAMPLE_QUERIES = [
    ("SELECT *",
     "SELECT * FROM Canciones WHERE Year = 2023"),
    ("BETWEEN",
     "SELECT * FROM Canciones WHERE Year BETWEEN 2018 AND 2023"),
    ("INSERT",
     "INSERT INTO Canciones VALUES (999, 2024, 'Peru', 'NuevoArtista', 'NuevaCancion', 200)"),
    ("CREATE TABLE",
     "CREATE TABLE Artistas (ID INTEGER, Nombre VARCHAR(40), Pais VARCHAR(20))"),
    ("SPATIAL RADIUS",
     "SELECT * FROM Ciudades WHERE Lat IN (POINT(30.9, 71.7), RADIUS 5)"),
    ("KNN",
     "SELECT * FROM Ciudades WHERE Lat IN (POINT(30.9, 71.7), K 3)"),
]


# ---------------------------------------------------------------------------
# SQL Syntax
# ---------------------------------------------------------------------------
_HL_COMMENT  = re.compile(r'--[^\n]*')
_HL_STRING   = re.compile(r"'[^']*'")
_HL_KEYWORD  = re.compile(
    r'\b(?:SELECT|FROM|WHERE|INSERT|INTO|VALUES|DELETE|CREATE|TABLE|DROP|'
    r'UPDATE|SET|AND|OR|NOT|IN|ON|AS|JOIN|BETWEEN|GROUP|BY|ORDER|HAVING|'
    r'LIMIT|DISTINCT|NULL|TRUE|FALSE|INDEX)\b',
    re.IGNORECASE,
)
_HL_TYPE     = re.compile(
    r'\b(?:INTEGER|INT|BIGINT|FLOAT|REAL|DOUBLE|STRING|VARCHAR|TEXT|CHAR|BOOLEAN)\b',
    re.IGNORECASE,
)
_HL_SPATIAL  = re.compile(
    r'\b(?:POINT|RADIUS|RTREE|BTREE|HASH|SEQUENTIAL|KNN)\b',
    re.IGNORECASE,
)
_HL_NUMBER   = re.compile(r'\b\d+(?:\.\d+)?\b')
_HL_STAR     = re.compile(r'\*')

# ---------------------------------------------------------------------------
# Spatial index
# ---------------------------------------------------------------------------

def _ask_spatial_cols(parent: tk.Tk, headers: List[str]) -> Optional[tuple]:
    # modal dialog para elegir el coordinate pair (X, Y) del RTree
    result = [None]

    dlg = tk.Toplevel(parent)
    dlg.title("Configurar Índice Espacial")
    dlg.resizable(False, False)
    dlg.configure(bg=_C["bg"])
    dlg.grab_set()
    dlg.transient(parent)

    pad = {"padx": 12, "pady": 6}

    tk.Label(
        dlg, text="Columna X  (latitud / coordenada principal):",
        bg=_C["bg"], fg=_C["text"], font=_FONT_UI,
    ).grid(row=0, column=0, sticky="w", **pad)
    x_var = tk.StringVar(value=headers[0] if headers else "")
    ttk.Combobox(
        dlg, textvariable=x_var, values=headers, state="readonly", width=30,
    ).grid(row=0, column=1, sticky="w", **pad)

    tk.Label(
        dlg, text="Columna Y  (longitud / coordenada secundaria):",
        bg=_C["bg"], fg=_C["text"], font=_FONT_UI,
    ).grid(row=1, column=0, sticky="w", **pad)
    y_var = tk.StringVar(
        value=headers[1] if len(headers) > 1 else (headers[0] if headers else "")
    )
    ttk.Combobox(
        dlg, textvariable=y_var, values=headers, state="readonly", width=30,
    ).grid(row=1, column=1, sticky="w", **pad)

    def _ok():
        if x_var.get() and y_var.get():
            result[0] = (x_var.get(), y_var.get())
        dlg.destroy()

    def _cancel():
        dlg.destroy()

    btn_frame = tk.Frame(dlg, bg=_C["bg"])
    btn_frame.grid(row=2, column=0, columnspan=2, pady=(4, 12))
    ttk.Button(btn_frame, text="Crear índice", command=_ok).pack(side="left", padx=8)
    ttk.Button(btn_frame, text="Cancelar",     command=_cancel).pack(side="left", padx=8)

    dlg.bind("<Return>", lambda _e: _ok())
    dlg.bind("<Escape>", lambda _e: _cancel())
    parent.wait_window(dlg)
    return result[0]


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

class JupiterDBApp(tk.Tk):

    # inicializa la app, crea workspace manager y construye toda la UI
    def __init__(self):
        super().__init__()
        self.title("JupiterDB  -  Query Engine")
        self.geometry("1200x780")
        self.minsize(900, 600)
        self.configure(bg=_C["bg"])

        self._ws_manager       = WorkspaceManager()
        _default_path          = self._ws_manager.ensure_default()
        self._active_project   = tk.StringVar(value="default_testing")
        self._active_data_dir  = _default_path

        self._bridge               = QueryBridge(_default_path)
        self.after(400, self._poll_seed)
        self._last_spatial         = None
        self._ingest_q: Optional[queue.Queue] = None
        self._editor_has_placeholder = False

        # zoom/pan state para el mapa espacial
        self.zoom_level          = 1.0
        self.offset_x            = 0.0
        self.offset_y            = 0.0
        self._show_labels        = tk.BooleanVar(value=True)
        self._equal_aspect       = tk.BooleanVar(value=False)
        self._active_example: str       = ""
        self._example_edits: dict       = {}
        self._pan_start          = None
        self._pan_offset_at_start = (0.0, 0.0)

        self._apply_theme()
        self._build_menu()
        self._build_ui()
        self._draw_empty_canvas()
        self.after(150, self._refresh_schema)



    # aplica el tema ttk a todos los widgets
    def _apply_theme(self):
        s = ttk.Style(self)
        s.theme_use("clam")

        base = dict(background=_C["bg"], foreground=_C["text"],
                    fieldbackground=_C["mantle"], bordercolor=_C["overlay0"],
                    troughcolor=_C["surface"], selectbackground=_C["violet"],
                    selectforeground="#ffffff", font=_FONT_UI)
        s.configure(".", **base)

        s.configure("TFrame",  background=_C["bg"])
        s.configure("Surface.TFrame", background=_C["surface"])

        s.configure("TLabel",  background=_C["bg"], foreground=_C["text"],
                    font=_FONT_UI)
        s.configure("Dim.TLabel",     background=_C["bg"],      foreground=_C["overlay0"],  font=("Segoe UI", 9))
        s.configure("Surf.TLabel",    background=_C["surface"],  foreground=_C["text"],      font=_FONT_UI)
        s.configure("SurfDim.TLabel", background=_C["surface"],  foreground=_C["overlay0"],  font=("Segoe UI", 9))
        s.configure("Metric.TLabel",  background=_C["surface"],  foreground=_C["overlay2"],  font=_FONT_UI)
        s.configure("MetricV.TLabel", background=_C["surface"],  foreground=_C["blue"],
                    font=("Consolas", 13, "bold"))
        s.configure("Header.TLabel",  background=_C["bg"],       foreground=_C["mauve"],     font=_FONT_H)
        s.configure("SurfHead.TLabel",background=_C["surface"],  foreground=_C["mauve"],     font=_FONT_H)

        s.configure("Run.TButton",
                    background=_C["violet"], foreground="#ffffff",
                    font=_FONT_BOLD, padding=(14, 6), relief="flat", borderwidth=0)
        s.map("Run.TButton",
              background=[("active", "#6d28d9"), ("pressed", "#5b21b6"),
                          ("disabled", _C["overlay0"])])

        s.configure("CSV.TButton",
                    background="#065f46", foreground="#d1fae5",
                    font=_FONT_BOLD, padding=(14, 6), relief="flat", borderwidth=0)
        s.map("CSV.TButton",
              background=[("active", "#047857"), ("pressed", "#064e3b"),
                          ("disabled", _C["overlay0"])])

        s.configure("Sec.TButton",
                    background=_C["surface"], foreground=_C["lavender"],
                    font=_FONT_UI, padding=(8, 4), relief="flat", borderwidth=0)
        s.map("Sec.TButton",
              background=[("active", _C["row_alt"])])

        s.configure("Danger.TButton",
                    background="#7f1d1d", foreground="#fecaca",
                    font=_FONT_UI, padding=(8, 4), relief="flat", borderwidth=0)
        s.map("Danger.TButton",
              background=[("active", "#991b1b"), ("pressed", "#450a0a"),
                          ("disabled", _C["overlay0"])])

        s.configure("TNotebook",     background=_C["surface"], tabmargins=[0, 4, 0, 0])
        s.configure("TNotebook.Tab",
                    background=_C["surface"], foreground=_C["overlay2"],
                    font=_FONT_UI, padding=[14, 6])
        s.map("TNotebook.Tab",
              background=[("selected", _C["bg"])],
              foreground=[("selected", _C["text"])])

        s.configure("Treeview",
                    background=_C["bg"], foreground=_C["text"],
                    fieldbackground=_C["bg"], rowheight=23, font=_FONT_MONO)
        s.configure("Treeview.Heading",
                    background=_C["surface"], foreground=_C["blue"],
                    font=_FONT_BOLD, relief="flat")
        s.map("Treeview",
              background=[("selected", _C["violet"])],
              foreground=[("selected", "#ffffff")])

        s.configure("TScrollbar",
                    background=_C["surface"], arrowcolor=_C["overlay0"],
                    troughcolor=_C["mantle"], borderwidth=0, relief="flat")

        s.configure("Ingest.Horizontal.TProgressbar",
                    troughcolor=_C["surface"], background=_C["green"],
                    thickness=8)

        s.configure("TSpinbox",
                    background=_C["mantle"], foreground=_C["text"],
                    fieldbackground=_C["mantle"], bordercolor=_C["overlay0"],
                    arrowcolor=_C["overlay2"])
        s.configure("TCombobox",
                    background=_C["mantle"], foreground=_C["text"],
                    fieldbackground=_C["mantle"], bordercolor=_C["overlay0"],
                    arrowcolor=_C["overlay2"])
        # combobox con texto negro sobre fondo claro - para project selector y file picker
        s.configure("Light.TCombobox",
                    foreground="#000000", fieldbackground="#dde1f0",
                    selectbackground="#b8bfe8", selectforeground="#000000",
                    bordercolor=_C["overlay0"], arrowcolor=_C["overlay0"])
        s.map("Light.TCombobox",
              foreground=[("readonly", "#000000"), ("disabled", _C["overlay0"])],
              fieldbackground=[("readonly", "#dde1f0"), ("disabled", _C["surface"])])
        # spinbox con texto negro sobre fondo claro - para selector de página en Auditoría
        s.configure("Light.TSpinbox",
                    foreground="#000000", fieldbackground="#dde1f0",
                    background="#e0e3f0", arrowcolor="#555555",
                    bordercolor=_C["overlay0"])
        s.map("Light.TSpinbox",
              foreground=[("disabled", "#666666"), ("readonly", "#000000")],
              fieldbackground=[("disabled", "#c0c4d6"), ("readonly", "#dde1f0")])
        s.configure("TRadiobutton",
                    background=_C["bg"], foreground=_C["text"], font=_FONT_UI)
        s.map("TRadiobutton",
              background=[("active", _C["bg"])],
              foreground=[("active", _C["blue"])])

    # ------------------------------------------------------------------
    # Menú
    # ------------------------------------------------------------------

    # construye el menu bar con submenu Ver y sus comandos
    def _build_menu(self):
        mb = tk.Menu(
            self,
            bg=_C["surface"], fg=_C["text"],
            activebackground=_C["violet"], activeforeground="#ffffff",
            relief="flat", bd=0,
        )

        ver = tk.Menu(
            mb, tearoff=0,
            bg=_C["surface"], fg=_C["text"],
            activebackground=_C["violet"], activeforeground="#ffffff",
        )
        ver.add_command(label="Ver Log de Transacciones", command=self._open_log)
        ver.add_separator()
        ver.add_command(label="Refrescar Tablas",         command=self._refresh_table_list)

        mb.add_cascade(label="Ver", menu=ver)
        self.configure(menu=mb)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    # paneles del layout principal
    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        self._build_header()
        self._build_editor()
        self._build_notebook()
        self._build_metrics()


    # construye la barra de header con selector de proyecto y label de tablas
    def _build_header(self):
        bar = tk.Frame(self, bg=_C["surface"], height=40)
        bar.grid(row=0, column=0, sticky="ew")
        bar.grid_propagate(False)

        tk.Label(bar, text="⧡  JupiterDB",
                 bg=_C["surface"], fg=_C["mauve"],
                 font=("Segoe UI", 13, "bold")).pack(side="left", padx=14, pady=6)
        tk.Label(bar, text="Disk-Based Relational Engine  •  v1.0",
                 bg=_C["surface"], fg=_C["overlay0"],
                 font=("Segoe UI", 9)).pack(side="left", pady=6)

        self._tbl_var = tk.StringVar(value="Tablas: cargando…")
        tk.Label(bar, textvariable=self._tbl_var,
                 bg=_C["surface"], fg=_C["overlay2"],
                 font=("Segoe UI", 9)).pack(side="right", padx=14)

        # project selector con combobox vinculado a _switch_project
        tk.Label(bar, text="Proyecto Activo:",
                 bg=_C["surface"], fg=_C["overlay2"],
                 font=("Segoe UI", 9)).pack(side="right", pady=6)
        projects = self._ws_manager.get_projects()
        self._proj_cb = ttk.Combobox(
            bar, textvariable=self._active_project,
            values=projects, state="readonly", width=20,
            font=("Segoe UI", 9), style="Light.TCombobox",
        )
        self._proj_cb.pack(side="right", padx=(0, 8), pady=4)
        self._proj_cb.bind(
            "<<ComboboxSelected>>",
            lambda _: self._switch_project(self._active_project.get()),
        )

        # boton reset - solo visible si el proyecto activo es default_testing
        self._reset_btn = ttk.Button(
            bar, text="↺  RESETEAR DEFAULT",
            style="Danger.TButton",
            command=self._reset_default,
        )
        self._update_reset_visibility()

        self.after(100, self._refresh_table_list)

    # -- SQL Editor ----------------------------------------------------

    # construye el editor SQL con botones de ejemplo, progressbar de ingest y boton CSV
    def _build_editor(self):
        outer = ttk.Frame(self, padding=(10, 6, 10, 2))
        outer.grid(row=1, column=0, sticky="ew")
        outer.columnconfigure(0, weight=1)

        # Label row
        lbl_row = ttk.Frame(outer)
        lbl_row.grid(row=0, column=0, sticky="ew", pady=(0, 3))
        ttk.Label(lbl_row, text="SQL Editor", style="Header.TLabel").pack(side="left")
        ttk.Label(lbl_row, text="  Ctrl+Enter para ejecutar",
                  style="Dim.TLabel").pack(side="left")

        # Quick-example buttons (right side of same row)
        ex_frame = ttk.Frame(outer)
        ex_frame.grid(row=0, column=0, sticky="e")
        for label, _ in _EXAMPLE_QUERIES:
            ttk.Button(ex_frame, text=label, style="Sec.TButton",
                       command=lambda q=label: self._load_example(q)
                       ).pack(side="left", padx=2)

        # Text widget
        border = tk.Frame(outer, bg=_C["overlay0"], bd=1)
        border.grid(row=1, column=0, sticky="ew")
        border.columnconfigure(0, weight=1)

        self._editor = tk.Text(
            border, height=5,
            bg=_C["mantle"], fg=_C["text"],
            insertbackground=_C["blue"],
            font=_FONT_MONO, relief="flat",
            wrap="none", padx=10, pady=6,
            selectbackground=_C["violet"], selectforeground="#ffffff",
            undo=True,
        )
        self._editor.grid(row=0, column=0, sticky="ew")
        self._editor.bind("<Control-Return>", lambda _e: self._execute())
        self._editor.bind("<FocusIn>",  self._on_editor_focus_in)
        self._editor.bind("<FocusOut>", self._on_editor_focus_out)
        self._editor.bind("<KeyRelease>", lambda _e: self._highlight_sql())

        # syntax-highlight tag colours
        self._editor.tag_configure("hl_kw",      foreground=_C["mauve"])
        self._editor.tag_configure("hl_type",     foreground=_C["blue"])
        self._editor.tag_configure("hl_spatial",  foreground=_C["yellow"])
        self._editor.tag_configure("hl_str",      foreground=_C["green"])
        self._editor.tag_configure("hl_num",      foreground=_C["peach"])
        self._editor.tag_configure("hl_comment",  foreground=_C["overlay0"])
        self._editor.tag_configure("hl_star",     foreground=_C["teal"])

        self.after(50, lambda: self._load_example("INSERT"))

        hsb_ed = ttk.Scrollbar(border, orient="horizontal",
                               command=self._editor.xview)
        hsb_ed.grid(row=1, column=0, sticky="ew")
        self._editor.configure(xscrollcommand=hsb_ed.set)

        # Button row
        btn_row = ttk.Frame(outer)
        btn_row.grid(row=2, column=0, sticky="ew", pady=(5, 0))

        self._run_btn = ttk.Button(btn_row, text="▶  EJECUTAR",
                                   style="Run.TButton",
                                   command=self._execute)
        self._run_btn.pack(side="left")

        ttk.Button(btn_row, text="Limpiar", style="Sec.TButton",
                   command=lambda: self._editor.delete("1.0", "end")
                   ).pack(side="left", padx=(6, 0))

        # SUPER INSERT CSV button
        self._super_btn = ttk.Button(
            btn_row, text="⬆  SUPER INSERT (CSV)",
            style="CSV.TButton",
            command=self._super_insert,
        )
        self._super_btn.pack(side="left", padx=(12, 0))

        self._status_bar = tk.Label(
            btn_row, text="", bg=_C["bg"], fg=_C["overlay0"],
            font=("Segoe UI", 9))
        self._status_bar.pack(side="left", padx=12)

        # progress bar row oculta por defecto hasta que el ingest arranca
        self._progress_frame = ttk.Frame(outer)
        self._progress_frame.grid(row=3, column=0, sticky="ew", pady=(4, 0))
        self._progress_frame.grid_remove()   # hidden by default
        self._progress_frame.columnconfigure(1, weight=1)

        self._progress_lbl = ttk.Label(self._progress_frame, text="Cargando CSV…",
                                       style="Dim.TLabel")
        self._progress_lbl.grid(row=0, column=0, padx=(0, 8))

        self._progress_var = tk.DoubleVar(value=0)
        self._progress_bar = ttk.Progressbar(
            self._progress_frame, variable=self._progress_var,
            orient="horizontal", mode="determinate",
            style="Ingest.Horizontal.TProgressbar",
        )
        self._progress_bar.grid(row=0, column=1, sticky="ew")

        self._progress_pct = ttk.Label(self._progress_frame, text="0%",
                                       style="Dim.TLabel", width=6)
        self._progress_pct.grid(row=0, column=2, padx=(6, 0))

    # -- Notebook ------------------------------------------------------

    # construye el notebook con tabs de resultados, mapa espacial y auditoria
    def _build_notebook(self):
        self._nb = ttk.Notebook(self)
        self._nb.grid(row=2, column=0, sticky="nsew", padx=10, pady=4)

        # -- Tab 0: Resultados ----------------------------------------
        tab0 = ttk.Frame(self._nb)
        self._nb.add(tab0, text="  Resultados  ")
        tab0.rowconfigure(0, weight=1)
        tab0.columnconfigure(0, weight=1)

        self._tree = ttk.Treeview(tab0, show="headings", selectmode="browse")
        vsb = ttk.Scrollbar(tab0, orient="vertical",   command=self._tree.yview)
        hsb = ttk.Scrollbar(tab0, orient="horizontal", command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self._tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        self._tree_status = tk.Label(
            tab0, text="", bg=_C["bg"], fg=_C["overlay0"],
            font=("Segoe UI", 9), anchor="w")
        self._tree_status.grid(row=2, column=0, sticky="ew", padx=4, pady=2)

        # -- Tab 1: Mapa Espacial -------------------------------------
        tab1 = ttk.Frame(self._nb)
        self._nb.add(tab1, text="  Mapa Espacial  ")
        tab1.rowconfigure(1, weight=1)
        tab1.columnconfigure(0, weight=1)

        # toolbar con zoom, reset y toggle de etiquetas
        toolbar = tk.Frame(tab1, bg=_C["surface"])
        toolbar.grid(row=0, column=0, sticky="ew", padx=6, pady=(4, 0))

        _btn_kw = dict(
            bg=_C["surface"], fg=_C["lavender"],
            activebackground=_C["row_alt"], activeforeground=_C["text"],
            relief="flat", font=("Segoe UI", 12), bd=0,
            padx=7, pady=2, cursor="hand2",
        )
        tk.Button(toolbar, text="+", command=self._zoom_in,    **_btn_kw).pack(side="left", padx=(0, 1))
        tk.Button(toolbar, text="−", command=self._zoom_out,   **_btn_kw).pack(side="left", padx=(0, 1))
        tk.Button(toolbar, text="⌂", command=self._reset_view, **_btn_kw).pack(side="left", padx=(0, 10))

        self._lbl_toggle = tk.Checkbutton(
            toolbar, text="🏷 Etiquetas",
            variable=self._show_labels,
            bg=_C["surface"], fg=_C["overlay2"],
            selectcolor=_C["surface"],
            activebackground=_C["surface"], activeforeground=_C["text"],
            relief="flat", font=("Segoe UI", 9),
            command=self._redraw_spatial,
        )
        self._lbl_toggle.pack(side="left")

        self._aspect_toggle = tk.Checkbutton(
            toolbar, text="1:1",
            variable=self._equal_aspect,
            bg=_C["surface"], fg=_C["overlay2"],
            selectcolor=_C["surface"],
            activebackground=_C["surface"], activeforeground=_C["text"],
            relief="flat", font=("Segoe UI", 9),
            command=self._toggle_aspect,
        )
        self._aspect_toggle.pack(side="left", padx=(8, 0))

        tk.Label(toolbar,
                 text="WHERE col IN (POINT(cx,cy), RADIUS r)   |   WHERE col IN (POINT(cx,cy), K k)  ",
                 bg=_C["surface"], fg=_C["overlay0"],
                 font=("Segoe UI", 9)).pack(side="right")

        self._canvas = tk.Canvas(
            tab1, bg=_C["mantle"], bd=0, highlightthickness=0,
            cursor="fleur")
        self._canvas.grid(row=1, column=0, sticky="nsew", padx=6, pady=(2, 6))
        self._canvas.bind("<Configure>",      self._on_canvas_resize)
        self._canvas.bind("<Button-1>",       self._on_pan_start)
        self._canvas.bind("<B1-Motion>",      self._on_pan_drag)
        self._canvas.bind("<ButtonRelease-1>",self._on_pan_end)
        self._canvas.bind("<MouseWheel>",     self._on_mouse_wheel)

        # -- Tab 2: Auditoria de Almacenamiento -----------------------
        tab2 = ttk.Frame(self._nb)
        self._nb.add(tab2, text="  Auditoria de Almacenamiento  ")
        tab2.rowconfigure(0, weight=1)
        tab2.columnconfigure(0, weight=1)

        self._page_viewer = PageViewer(tab2, self._active_data_dir, palette=_C)
        self._page_viewer.grid(row=0, column=0, sticky="nsew")

        # -- Tab 3: Esquema y Archivos ---------------------------------
        tab3 = ttk.Frame(self._nb)
        self._nb.add(tab3, text="  Esquema y Archivos  ")
        self._build_schema_tab(tab3)

        self._nb.bind("<<NotebookTabChanged>>", self._on_tab_change)

    # -- Metrics -------------------------------------------------------

    # construye el panel de metricas con labels de lecturas, escrituras y tiempo
    def _build_metrics(self):
        panel = tk.Frame(self, bg=_C["surface"])
        panel.grid(row=3, column=0, sticky="ew")

        tk.Label(panel, text="  Metricas del Sistema",
                 bg=_C["surface"], fg=_C["mauve"],
                 font=_FONT_BOLD).pack(side="left", padx=(6, 18), pady=6)

        self._metric_labels: Dict[str, tk.Label] = {}
        defs = [
            ("reads",    "Lecturas de Disco"),
            ("writes",   "Escrituras"),
            ("accesses", "Accesos Totales"),
            ("ms",       "Tiempo (ms)"),
        ]
        for key, label in defs:
            tk.Label(panel, text=label + ":",
                     bg=_C["surface"], fg=_C["overlay2"],
                     font=_FONT_UI).pack(side="left")
            lbl = tk.Label(panel, text="-",
                           bg=_C["surface"], fg=_C["blue"],
                           font=("Consolas", 12, "bold"), width=6, anchor="w")
            lbl.pack(side="left", padx=(2, 18))
            self._metric_labels[key] = lbl

    # ------------------------------------------------------------------
    # Execute
    # ------------------------------------------------------------------

    # lee el SQL del editor, lo ejecuta via bridge y despacha el resultado
    def _execute(self):
        if self._editor_has_placeholder:
            return
        sql = self._editor.get("1.0", "end-1c").strip()
        if not sql:
            return

        self._run_btn.configure(state="disabled", text="…")
        self._status_bar.configure(text="Ejecutando…", fg=_C["yellow"])
        self.update_idletasks()

        # Detectar CREATE TABLE FROM FILE para mostrar barra de progreso
        compiler = QueryCompiler()
        nodes = compiler.compile(sql)
        if (len(nodes) == 1 and isinstance(nodes[0], CreateSchemaNode) and nodes[0].from_file):
            # Es CREATE TABLE FROM FILE, hacer carga con progreso
            node = nodes[0]
            table_name = node.table_name
            csv_path = node.from_file
            # Resolver path relativo
            if not os.path.isabs(csv_path):
                for candidate_dir in (self._active_data_dir, "data", os.path.join(os.path.dirname(self._active_data_dir), "data")):
                    candidate = os.path.join(candidate_dir, csv_path)
                    if os.path.exists(candidate):
                        csv_path = candidate
                        break

            # Mostrar barra de progreso
            self._progress_var.set(0)
            self._progress_pct.configure(text="0%")
            self._progress_lbl.configure(text=f"Cargando '{table_name}'…")
            self._progress_frame.grid()
            self._super_btn.configure(state="disabled")

            q: queue.Queue = queue.Queue()
            self._ingest_q = q

            def worker():
                try:
                    def cb(done, total):
                        pct = int(done / total * 100) if total else 0
                        q.put(("progress", pct, done, total))
                    n = self._bridge.massive_ingest(csv_path, table_name, cb)
                    q.put(("done", n))
                except Exception as exc:
                    q.put(("error", str(exc)))

            threading.Thread(target=worker, daemon=True).start()
            self.after(150, lambda: self._poll_ingest(q, table_name))
        else:
            # SQL normal
            result = self._bridge.execute(sql)
            self._handle_result(result)
            self._refresh_table_list()
            self._refresh_schema()
            self._run_btn.configure(state="normal", text="▶  EJECUTAR")

    # rutea el QueryResult segun su kind a la vista correspondiente
    def _handle_result(self, result: QueryResult):
        self._update_metrics(result.telemetry)

        if result.kind == "error":
            messagebox.showerror("Error SQL / Motor", result.message, parent=self)
            self._set_status(f"Error: {result.message[:90]}", _C["red"])
            return

        if result.kind == "message":
            self._clear_tree()
            if result.message and result.message != "OK":
                self._populate_tree([{"info": result.message}], ["info"])
            self._set_status(result.message, _C["green"])
            return

        if result.kind == "table":
            self._populate_tree(result.rows, result.columns)
            n = len(result.rows)
            self._set_status(f"{n} fila(s) devuelta(s)", _C["green"])
            self._nb.select(0)
            return

        if result.kind in ("spatial_radius", "spatial_knn"):
            self._populate_tree(result.rows, result.columns)
            n  = len(result.rows)
            qt = "radio" if result.kind == "spatial_radius" else "KNN"
            self._set_status(f"{n} punto(s) encontrado(s)  [{qt}]", _C["yellow"])
            # guarda spatial_data para poder redibujar en resize del canvas
            self._last_spatial = result.spatial_data
            self._draw_spatial(result.spatial_data)
            self._nb.select(1)

    # ------------------------------------------------------------------
    # Project switching
    # ------------------------------------------------------------------

    # reinicia el engine con el nuevo workspace y refresca todo el estado UI
    def _switch_project(self, name: str) -> None:
        path = self._ws_manager.ensure_project(name)
        self._active_data_dir = path
        self._active_project.set(name)
        self._bridge.reinit(path)
        self._page_viewer.set_data_dir(path)
        self._proj_cb["values"] = self._ws_manager.get_projects()
        self._example_edits.clear()
        self._active_example = ""
        self._load_example("INSERT")
        self._clear_tree()
        self._refresh_table_list()
        self._refresh_schema()
        self._update_reset_visibility()

    # ------------------------------------------------------------------
    # SUPER INSERT (CSV)
    # ------------------------------------------------------------------

    # pregunta el proyecto destino antes de abrir el file dialog del CSV
    def _super_insert(self):
        projects = self._ws_manager.get_projects()
        proj_name = simpledialog.askstring(
            "Proyecto de Importación",
            f"Nombre del proyecto destino:\n(Existentes: {', '.join(projects)})",
            initialvalue=self._active_project.get(),
            parent=self,
        )
        if not proj_name or not proj_name.strip():
            return
        proj_name = proj_name.strip()

        if proj_name != self._active_project.get():
            self._switch_project(proj_name)

        csv_path = filedialog.askopenfilename(
            title="Seleccionar dataset CSV",
            filetypes=[("CSV files", "*.csv"), ("Todos los archivos", "*.*")],
            initialdir=os.path.expanduser("~"),
            parent=self,
        )
        if not csv_path:
            return

        default_name = os.path.splitext(os.path.basename(csv_path))[0][:25]
        table_name = simpledialog.askstring(
            "Nombre de Tabla",
            "Nombre para la tabla destino\n(se crea automaticamente si no existe):",
            initialvalue=default_name,
            parent=self,
        )
        if not table_name or not table_name.strip():
            return
        table_name = table_name.strip()

        # header peek para el dialogo de selección de columnas espaciales
        try:
            with open(csv_path, newline="", encoding="utf-8", errors="replace") as _fh:
                _hdrs = list(csv.DictReader(_fh).fieldnames or [])
        except Exception:
            _hdrs = []

        spatial_cols: Optional[List[str]] = None
        if _hdrs and messagebox.askyesno(
            "Índice Espacial",
            "¿Desea crear un índice espacial (R-Tree) para esta tabla?",
            parent=self,
        ):
            pair = _ask_spatial_cols(self, _hdrs)
            if pair:
                spatial_cols = list(pair)

        # muestra la progress bar y deshabilita botones durante el ingest
        self._progress_var.set(0)
        self._progress_pct.configure(text="0%")
        self._progress_lbl.configure(text=f"Cargando '{table_name}'…")
        self._progress_frame.grid()
        self._super_btn.configure(state="disabled")
        self._run_btn.configure(state="disabled")

        q: queue.Queue = queue.Queue()
        self._ingest_q = q

        def worker():
            try:
                def cb(done, total):
                    pct = int(done / total * 100) if total else 0
                    q.put(("progress", pct, done, total))
                n = self._bridge.massive_ingest(csv_path, table_name, cb, spatial_cols)
                q.put(("done", n))
            except Exception as exc:
                q.put(("error", str(exc)))

        # lanza hilo worker para la carga CSV y empieza a pollear con after
        threading.Thread(target=worker, daemon=True).start()
        self.after(150, lambda: self._poll_ingest(q, table_name))

    # drena la queue del worker cada 150 ms y actualiza progressbar o cierra
    def _poll_ingest(self, q: queue.Queue, table_name: str):
        try:
            while True:
                msg = q.get_nowait()
                kind = msg[0]
                if kind == "progress":
                    _, pct, done, total = msg
                    self._progress_var.set(pct)
                    self._progress_pct.configure(text=f"{pct}%")
                    self._set_status(
                        f"Cargando '{table_name}'… {done:,} / {total:,} filas",
                        _C["yellow"],
                    )
                elif kind == "done":
                    n = msg[1]
                    self._progress_var.set(100)
                    self._progress_pct.configure(text="100%")
                    self._set_status(
                        f"Carga completa: {n:,} filas en '{table_name}'",
                        _C["green"],
                    )
                    self._progress_frame.grid_remove()
                    self._super_btn.configure(state="normal")
                    self._run_btn.configure(state="normal", text="▶  EJECUTAR")
                    self._show_placeholder()
                    self._clear_tree()
                    self._refresh_table_list()
                    self._refresh_schema()
                    self._page_viewer.refresh()
                    self._ingest_q = None
                    return
                elif kind == "error":
                    self._set_status(f"Error de carga: {msg[1][:80]}", _C["red"])
                    self._progress_frame.grid_remove()
                    self._super_btn.configure(state="normal")
                    self._run_btn.configure(state="normal", text="▶  EJECUTAR")
                    messagebox.showerror(
                        "Error SUPER INSERT", msg[1], parent=self)
                    self._ingest_q = None
                    return
        except queue.Empty:
            pass
        self.after(150, lambda: self._poll_ingest(q, table_name))

    # ------------------------------------------------------------------
    # Treeview
    # ------------------------------------------------------------------

    # limpia el treeview borrando filas y columns
    def _clear_tree(self):
        self._tree.delete(*self._tree.get_children())
        self._tree["columns"] = ()

    # popula el treeview con rows y columnas dinamicas con ancho auto-calculado
    def _populate_tree(self, rows: List[Dict], columns: List[str] = None):
        self._clear_tree()
        if not rows:
            self._tree_status.configure(text="0 fila(s)")
            return

        cols = columns or list(rows[0].keys())
        self._tree["columns"] = cols
        for col in cols:
            w = max(90, min(220, len(col) * 10 + 20))
            self._tree.heading(col, text=col, anchor="w")
            self._tree.column(col, width=w, anchor="w", stretch=True)

        self._tree.tag_configure("odd",  background=_C["bg"])
        self._tree.tag_configure("even", background=_C["row_alt"])

        for i, row in enumerate(rows):
            vals = [str(row.get(c, "")) for c in cols]
            self._tree.insert("", "end", values=vals,
                              tags=("odd" if i % 2 == 0 else "even",))

        self._tree_status.configure(text=f"{len(rows)} fila(s)")

    # ------------------------------------------------------------------
    # Spatial canvas
    # ------------------------------------------------------------------

    # redibuja el canvas al resize manteniendo zoom/pan actuales
    def _on_canvas_resize(self, _event=None):
        self.after_idle(self._redraw_spatial)

    # refresca el panel correspondiente al entrar en Auditoria o Esquema
    def _on_tab_change(self, _event=None):
        idx = self._nb.index(self._nb.select())
        if idx == 2:
            self._page_viewer.refresh()
        elif idx == 3:
            self._refresh_schema()

    # placeholder cuando no hay datos espaciales
    def _draw_empty_canvas(self):
        c = self._canvas
        c.delete("all")
        W = c.winfo_width()  or 500
        H = c.winfo_height() or 400
        c.create_text(W // 2, H // 2,
                      text="Sin datos espaciales\n\n"
                           "Ejemplos:\n"
                           "  SELECT * FROM Ciudades WHERE Lat IN (POINT(30.9, 71.7), RADIUS 5)\n"
                           "  SELECT * FROM Ciudades WHERE Lat IN (POINT(30.9, 71.7), K 3)",
                      fill=_C["overlay0"], font=_FONT_MONO, justify="center")

    # despacha redibujado sin necesitar data explícita (usa _last_spatial)
    def _redraw_spatial(self):
        if self._last_spatial:
            self._draw_spatial(self._last_spatial)
        else:
            self._draw_empty_canvas()

    # -- Zoom / Pan controls -------------------------------------------

    # zoom in centrado en el centro del viewport
    def _zoom_in(self):
        W = self._canvas.winfo_width() or 500
        H = self._canvas.winfo_height() or 400
        self._do_zoom(1.3, W / 2, H / 2)

    # zoom out centrado en el centro del viewport
    def _zoom_out(self):
        W = self._canvas.winfo_width() or 500
        H = self._canvas.winfo_height() or 400
        self._do_zoom(1 / 1.3, W / 2, H / 2)

    # reset zoom y offset a estado inicial
    def _reset_view(self):
        self.zoom_level = 1.0
        self.offset_x   = 0.0
        self.offset_y   = 0.0
        self._redraw_spatial()

    # toggle 1:1 - resetea view para que el centrado sea coherente
    def _toggle_aspect(self):
        self.zoom_level = 1.0
        self.offset_x   = 0.0
        self.offset_y   = 0.0
        self._redraw_spatial()

    # zoom manteniendo el punto de datos bajo pivot_cx/pivot_cy fijo en pantalla
    # en modo 1:1 el origen efectivo incluye el offset de centrado ix/iy
    def _do_zoom(self, factor: float, pivot_cx: float, pivot_cy: float):
        ML, MR, MT, MB = 44, 16, 20, 34
        W = self._canvas.winfo_width() or 500
        H = self._canvas.winfo_height() or 400
        PW = W - ML - MR
        PH = H - MT - MB
        if self._equal_aspect.get():
            s0 = min(PW / 85.0, PH / 105.0)
            eff_ox = ML + (PW - 85.0 * s0) / 2
            eff_oy = MT + (PH - 105.0 * s0) / 2
        else:
            eff_ox, eff_oy = float(ML), float(MT)
        self.offset_x   = (pivot_cx - eff_ox) * (1 - factor) + factor * self.offset_x
        self.offset_y   = (pivot_cy - eff_oy) * (1 - factor) + factor * self.offset_y
        self.zoom_level = max(0.05, min(80.0, self.zoom_level * factor))
        self._redraw_spatial()

    # guarda posicion inicial del arrastre
    def _on_pan_start(self, event):
        self._pan_start           = (event.x, event.y)
        self._pan_offset_at_start = (self.offset_x, self.offset_y)

    # actualiza offset mientras se arrastra
    def _on_pan_drag(self, event):
        if self._pan_start is None:
            return
        dx = event.x - self._pan_start[0]
        dy = event.y - self._pan_start[1]
        self.offset_x = self._pan_offset_at_start[0] + dx
        self.offset_y = self._pan_offset_at_start[1] + dy
        self._redraw_spatial()

    # limpia estado de arrastre
    def _on_pan_end(self, _event):
        self._pan_start = None

    # zoom con scroll del mouse usando cursor como pivot
    def _on_mouse_wheel(self, event):
        factor = 1.12 if event.delta > 0 else 1 / 1.12
        self._do_zoom(factor, event.x, event.y)

    # -- Smart legend -------------------------------------------------

    # dibuja leyenda con 20px de gap real entre items usando canvas.bbox()
    # retorna el x final para que el caller pueda ajustar el fondo
    def _draw_legend_smart(self, c, items, x0: float, y: float) -> float:
        GAP = 20
        DOT = 8
        x = x0
        for color, text in items:
            c.create_oval(x, y - DOT // 2, x + DOT, y + DOT // 2,
                          fill=color, outline="")
            x += DOT + 4
            tid = c.create_text(x, y, text=text,
                                 fill=_C["overlay2"], font=_FONT_TINY, anchor="w")
            bb  = c.bbox(tid)           # (x1,y1,x2,y2)
            x   = bb[2] + GAP           # siguiente dot empieza 20px tras el final del texto
        return x

    # dibuja la escena espacial con zoom/pan aplicado
    def _draw_spatial(self, data: dict):
        c = self._canvas
        c.delete("all")
        W = c.winfo_width()  or 600
        H = c.winfo_height() or 450
        if W < 50 or H < 50:
            return

        ML, MR, MT, MB = 44, 16, 20, 34
        PW = W - ML - MR
        PH = H - MT - MB

        X_MIN, X_MAX = 0.0,  85.0
        Y_MIN, Y_MAX = 0.0, 105.0
        xrange = X_MAX - X_MIN
        yrange = Y_MAX - Y_MIN

        Z  = self.zoom_level
        OX = self.offset_x
        OY = self.offset_y

        equal_aspect = self._equal_aspect.get()
        if equal_aspect:
            # escala uniforme - el eje más comprimido fija el tamaño
            s0 = min(PW / xrange, PH / yrange)
            s  = s0 * Z
            ix = (PW - xrange * s0) / 2   # offset centrado horizontal
            iy = (PH - yrange * s0) / 2   # offset centrado vertical
            def to_px(x, y):
                return (ML + ix + (x - X_MIN) * s + OX,
                        MT + iy + (Y_MAX - y) * s + OY)
            def to_data_x(cx_): return X_MIN + (cx_ - ML - ix - OX) / s
            def to_data_y(cy_): return Y_MAX - (cy_ - MT - iy - OY) / s
        else:
            # escala independiente por eje - datos llenan el área del plot
            def to_px(x, y):
                nx = (x - X_MIN) / xrange
                ny = (y - Y_MIN) / yrange
                return (ML + nx * PW * Z + OX,
                        MT + PH * Z * (1 - ny) + OY)
            def to_data_x(cx_): return X_MIN + (cx_ - ML - OX) / (PW * Z) * xrange
            def to_data_y(cy_): return Y_MIN + (1 - (cy_ - MT - OY) / (PH * Z)) * yrange

        # coordenadas de datos visibles en los bordes del plot
        vis_x0 = to_data_x(ML)
        vis_x1 = to_data_x(ML + PW)
        vis_y0 = to_data_y(MT + PH)
        vis_y1 = to_data_y(MT)

        # step dinamico: ~8 lineas de grid en la vista
        def nice_step(rng: float) -> float:
            if rng <= 0:
                return 10.0
            raw = rng / 8
            mag = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1
            n   = raw / mag
            if   n < 1.5: return mag
            elif n < 3.5: return 2 * mag
            elif n < 7.5: return 5 * mag
            else:          return 10 * mag

        step_x = nice_step(vis_x1 - vis_x0)
        step_y = nice_step(vis_y1 - vis_y0)

        # -- Clip region (plot frame) ----------------------------------
        # lineas de grid solo dentro del frame ML..ML+PW, MT..MT+PH
        c.create_rectangle(ML, MT, ML + PW, MT + PH,
                           fill=_C["mantle"], outline="")

        # -- Grid vertical (X) -----------------------------------------
        gx_start = math.ceil(vis_x0 / step_x) * step_x
        gx = gx_start
        while gx <= vis_x1 + step_x:
            lx, _ = to_px(gx, 0)
            if ML <= lx <= ML + PW:
                c.create_line(lx, MT, lx, MT + PH, fill="#252535", width=1)
                label = f"{int(gx)}" if gx == int(gx) else f"{gx:.1f}"
                c.create_text(lx, MT + PH + 14, text=label,
                              fill=_C["overlay0"], font=_FONT_TINY)
            gx += step_x

        # -- Grid horizontal (Y) ---------------------------------------
        gy_start = math.ceil(vis_y0 / step_y) * step_y
        gy = gy_start
        while gy <= vis_y1 + step_y:
            _, ly = to_px(0, gy)
            if MT <= ly <= MT + PH:
                c.create_line(ML, ly, ML + PW, ly, fill="#252535", width=1)
                label = f"{int(gy)}" if gy == int(gy) else f"{gy:.1f}"
                c.create_text(ML - 4, ly, text=label,
                              fill=_C["overlay0"], font=_FONT_TINY, anchor="e")
            gy += step_y

        # -- Axes ------------------------------------------------------
        c.create_line(ML, MT + PH, ML + PW, MT + PH, fill=_C["overlay0"], width=1)
        c.create_line(ML, MT,      ML,      MT + PH, fill=_C["overlay0"], width=1)

        qt       = data["query_type"]
        all_pts  = {rid: (x, y, lbl) for rid, x, y, lbl in data["all_points"]}
        show_lbl = self._show_labels.get()

        if qt == "radius":
            result_ids = {rid for _, _, rid    in data["results"]}
        else:
            result_ids = {rid for _, _, rid, _ in data["results"]}

        # puntos de fondo - tamaño fijo en pantalla (no crece con zoom)
        for rid, (x, y, lbl) in all_pts.items():
            if rid not in result_ids:
                bx, by = to_px(x, y)
                c.create_oval(bx - 4, by - 4, bx + 4, by + 4,
                              fill=_C["sapphire"], outline=_C["overlay0"], width=1)
                if show_lbl:
                    c.create_text(bx + 7, by - 4,
                                  text=lbl, fill=_C["overlay2"],
                                  font=_FONT_TINY, anchor="w")

        if qt == "radius":
            cx_d, cy_d, r_d = data["cx"], data["cy"], data["radius"]
            qx, qy = to_px(cx_d, cy_d)

            # en modo 1:1: radio uniforme (círculo real)
            # en modo ajustado: radios independientes por eje (elipse = círculo de datos correcto)
            if equal_aspect:
                rx_px = r_d * s
                ry_px = r_d * s
            else:
                rx_px = r_d / xrange * PW * Z
                ry_px = r_d / yrange * PH * Z
            c.create_oval(qx - rx_px, qy - ry_px, qx + rx_px, qy + ry_px,
                          outline=_C["green"], width=2, dash=(10, 5))

            for x, y, rid in data["results"]:
                bx, by = to_px(x, y)
                _, _, lbl = all_pts.get(rid, (0, 0, str(rid)))
                c.create_oval(bx - 6, by - 6, bx + 6, by + 6,
                              fill=_C["red"], outline=_C["mantle"], width=1)
                if show_lbl:
                    c.create_text(bx + 9, by - 8, text=lbl,
                                  fill=_C["red"], font=_FONT_TINY, anchor="w")

            c.create_oval(qx - 7, qy - 7, qx + 7, qy + 7,
                          fill=_C["yellow"], outline="#ffffff", width=1)
            if show_lbl:
                c.create_text(qx + 10, qy - 10,
                              text=f"centro ({cx_d},{cy_d})",
                              fill=_C["yellow"], font=_FONT_TINY, anchor="w")

        elif qt == "knn":
            qx_d, qy_d = data["qx"], data["qy"]
            qx, qy = to_px(qx_d, qy_d)

            for rank, (x, y, rid, dist) in enumerate(data["results"], 1):
                bx, by = to_px(x, y)
                _, _, lbl = all_pts.get(rid, (0, 0, str(rid)))
                c.create_line(qx, qy, bx, by,
                              fill=_C["green"], width=1, dash=(6, 4))
                c.create_oval(bx - 6, by - 6, bx + 6, by + 6,
                              fill=_C["red"], outline=_C["mantle"], width=1)
                if show_lbl:
                    c.create_text(bx + 9, by - 8,
                                  text=f"#{rank} {lbl}  d={dist:.1f}",
                                  fill=_C["red"], font=_FONT_TINY, anchor="w")

            c.create_oval(qx - 7, qy - 7, qx + 7, qy + 7,
                          fill=_C["yellow"], outline="#ffffff", width=1)
            if show_lbl:
                c.create_text(qx + 10, qy - 10,
                              text=f"Q({qx_d},{qy_d})",
                              fill=_C["yellow"], font=_FONT_TINY, anchor="w")

        # -- Legend (smart spacing via bbox) ---------------------------
        leg_items = [
            (_C["blue"],   "Todos los puntos"),
            (_C["red"],    "Resultado"),
            (_C["yellow"], "Centro / Query"),
            (_C["green"],  "Radio / Conector"),
        ]
        leg_x0 = ML + 8
        leg_y  = MT + 13
        # placeholder rect - expandido despues de dibujar los items con bbox real
        bg_rect  = c.create_rectangle(leg_x0 - 4, MT + 4,
                                      leg_x0 + 4, MT + 22,
                                      fill=_C["mantle"], outline="")
        x_end = self._draw_legend_smart(c, leg_items, leg_x0, leg_y)
        # sube el rect por encima de los items de leyenda en z-order
        c.tag_lower(bg_rect)
        c.coords(bg_rect, leg_x0 - 4, MT + 4, x_end - 20 + 4, MT + 22)

    # ------------------------------------------------------------------
    # Ver Log
    # ------------------------------------------------------------------

    # abre ventana Toplevel con el journal.log coloreado por tipo de transaccion
    def _open_log(self):
        log_path = os.path.join(self._active_data_dir, "journal.log")
        if not os.path.exists(log_path):
            messagebox.showinfo(
                "Log de Transacciones",
                "No existe journal.log todavia.\n"
                "Ejecuta operaciones INSERT/SELECT primero.",
                parent=self,
            )
            return

        win = tk.Toplevel(self)
        win.title("Journal Log - Transacciones JupiterDB")
        win.geometry("860x520")
        win.configure(bg=_C["bg"])
        win.resizable(True, True)

        toolbar = tk.Frame(win, bg=_C["surface"])
        toolbar.pack(fill="x")
        tk.Label(toolbar, text=" journal.log",
                 bg=_C["surface"], fg=_C["mauve"],
                 font=_FONT_BOLD).pack(side="left", padx=8, pady=4)
        tk.Button(toolbar, text="Actualizar",
                  bg=_C["surface"], fg=_C["lavender"],
                  relief="flat", font=_FONT_UI,
                  command=lambda: _reload()).pack(side="right", padx=8, pady=4)

        txt = tk.Text(
            win,
            bg=_C["mantle"], fg=_C["text"],
            font=("Consolas", 9), relief="flat",
            wrap="none",
            selectbackground=_C["violet"], selectforeground="#ffffff",
        )
        vsb = ttk.Scrollbar(win, command=txt.yview)
        hsb = ttk.Scrollbar(win, orient="horizontal", command=txt.xview)
        txt.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.pack(side="right",  fill="y")
        hsb.pack(side="bottom", fill="x")
        txt.pack(fill="both", expand=True, padx=4, pady=4)

        # tags de color para distinguir BEGIN, COMMIT, ROLLBACK y WAITING
        txt.tag_configure("begin",    foreground=_C["blue"])
        txt.tag_configure("commit",   foreground=_C["green"])
        txt.tag_configure("rollback", foreground=_C["red"])
        txt.tag_configure("waiting",  foreground=_C["yellow"])

        def _reload():
            txt.configure(state="normal")
            txt.delete("1.0", "end")
            try:
                with open(log_path, "r", errors="replace") as f:
                    for line in f:
                        upper = line.upper()
                        if "BEGIN"    in upper: tag = "begin"
                        elif "COMMIT" in upper: tag = "commit"
                        elif "ROLLBACK" in upper: tag = "rollback"
                        elif "WAITING" in upper or "RESUMED" in upper: tag = "waiting"
                        else: tag = ""
                        txt.insert("end", line, tag)
            except Exception as e:
                txt.insert("end", f"[Error leyendo log: {e}]")
            txt.configure(state="disabled")
            txt.see("end")

        _reload()

    # ------------------------------------------------------------------
    # Metrics  +  helpers
    # ------------------------------------------------------------------

    # actualiza los labels de telemetria con los valores del ultimo query
    def _update_metrics(self, tel: dict):
        if not tel:
            return
        self._metric_labels["reads"].configure(   text=str(tel.get("reads",    "-")))
        self._metric_labels["writes"].configure(  text=str(tel.get("writes",   "-")))
        self._metric_labels["accesses"].configure(text=str(tel.get("accesses", "-")))
        self._metric_labels["ms"].configure(      text=f"{tel.get('ms', 0.0):.1f}")

    # actualiza el status bar con mensaje y color opcionales
    def _set_status(self, msg: str, color: str = None):
        self._status_bar.configure(text=msg, fg=(color or _C["overlay0"]))

    # refresca el label de tablas consultando el bridge
    def _refresh_table_list(self):
        tables = self._bridge.get_tables()
        self._tbl_var.set(
            "Tablas: " + "  •  ".join(tables) if tables else "Sin tablas"
        )

    # poll hasta que el seed thread termine; actualiza status y tabla list
    def _poll_seed(self):
        if self._bridge.is_seeding():
            self._set_status("Cargando datos iniciales…")
            self.after(500, self._poll_seed)
        else:
            self._refresh_table_list()
            self._set_status("Listo")

    # ------------------------------------------------------------------
    # Esquema y Archivos tab
    # ------------------------------------------------------------------

    # construye el layout del tab: treeview izquierda + detail text derecha
    def _build_schema_tab(self, parent):
        parent.rowconfigure(0, weight=1)
        parent.columnconfigure(0, weight=1)

        pane = ttk.PanedWindow(parent, orient="horizontal")
        pane.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)

        # -- izquierda: schema treeview --------------------------------
        left = ttk.Frame(pane)
        left.rowconfigure(1, weight=1)
        left.columnconfigure(0, weight=1)
        pane.add(left, weight=2)

        ttk.Label(left, text="Tablas y Columnas",
                  style="Header.TLabel").grid(row=0, column=0, columnspan=2,
                                              sticky="w", padx=6, pady=(4, 2))

        self._schema_tree = ttk.Treeview(
            left, columns=("tipo",), show="tree headings",
            selectmode="browse",
        )
        vsb_s = ttk.Scrollbar(left, orient="vertical",
                               command=self._schema_tree.yview)
        self._schema_tree.configure(yscrollcommand=vsb_s.set)
        self._schema_tree.grid(row=1, column=0, sticky="nsew")
        vsb_s.grid(row=1, column=1, sticky="ns")

        self._schema_tree.heading("#0",   text="Tabla / Columna", anchor="w")
        self._schema_tree.heading("tipo", text="Tipo",            anchor="w")
        self._schema_tree.column("#0",   width=190, stretch=True)
        self._schema_tree.column("tipo", width=130, stretch=False)

        self._schema_tree.tag_configure("table", foreground=_C["mauve"],
                                         font=_FONT_BOLD)
        self._schema_tree.tag_configure("rtree", foreground=_C["green"],
                                         font=_FONT_BOLD)
        self._schema_tree.tag_configure("field", foreground=_C["blue"])

        self._schema_tree.bind("<<TreeviewSelect>>", self._on_schema_select)

        # -- derecha: detalle de archivos físicos ----------------------
        right = ttk.Frame(pane)
        right.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)
        pane.add(right, weight=3)

        ttk.Label(right, text="Archivos Físicos en Workspace",
                  style="Header.TLabel").grid(row=0, column=0, columnspan=2,
                                              sticky="w", padx=6, pady=(4, 2))

        self._schema_detail = tk.Text(
            right,
            bg=_C["mantle"], fg=_C["text"],
            font=_FONT_MONO, relief="flat",
            wrap="word", state="disabled",
            selectbackground=_C["violet"], selectforeground="#ffffff",
            padx=12, pady=10,
        )
        vsb_d = ttk.Scrollbar(right, orient="vertical",
                               command=self._schema_detail.yview)
        self._schema_detail.configure(yscrollcommand=vsb_d.set)
        self._schema_detail.grid(row=1, column=0, sticky="nsew")
        vsb_d.grid(row=1, column=1, sticky="ns")

        self._schema_detail.tag_configure("title",  foreground=_C["mauve"],
                                           font=_FONT_BOLD)
        self._schema_detail.tag_configure("bin",    foreground=_C["green"],
                                           font=_FONT_MONO)
        self._schema_detail.tag_configure("json",   foreground=_C["yellow"],
                                           font=_FONT_MONO)
        self._schema_detail.tag_configure("dim",    foreground=_C["overlay0"])
        self._schema_detail.tag_configure("info",   foreground=_C["lavender"])

    # puebla el schema_tree con tablas y columnas del catalog en memoria
    def _refresh_schema(self):
        if not hasattr(self, "_schema_tree"):
            return
        tree = self._schema_tree
        tree.delete(*tree.get_children())

        catalog = self._bridge._engine._catalog
        for tbl_name, meta in catalog.get("tables", {}).items():
            is_spatial = bool(meta.get("rtree_path"))
            idx_type   = "RTREE" if is_spatial else meta.get("index", "SEQUENTIAL")
            tbl_tag    = "rtree" if is_spatial else "table"
            tbl_id = tree.insert("", "end",
                                 text=f"  {tbl_name}",
                                 values=(idx_type,),
                                 tags=(tbl_tag,), open=True)
            for fld in meta.get("fields", []):
                fname = fld["name"]
                ftype = fld["type"]
                if ftype == "STRING" and "str_size" in fld:
                    ftype = f"VARCHAR({fld['str_size']})"
                tree.insert(tbl_id, "end",
                            text=f"    {fname}",
                            values=(ftype,),
                            tags=("field",))

    # al seleccionar una tabla o columna muestra los bin files en el panel derecho
    def _on_schema_select(self, _event=None):
        sel = self._schema_tree.selection()
        if not sel:
            return
        item = sel[0]
        tags = self._schema_tree.item(item, "tags")
        if "table" in tags or "rtree" in tags:
            tbl_name = self._schema_tree.item(item, "text").strip()
        elif "field" in tags:
            parent = self._schema_tree.parent(item)
            tbl_name = self._schema_tree.item(parent, "text").strip()
        else:
            return
        self._show_table_files(tbl_name)

    # escribe en schema_detail los bin files asociados a la tabla con tamaños
    def _show_table_files(self, table_name: str):
        d = self._schema_detail
        d.configure(state="normal")
        d.delete("1.0", "end")

        catalog = self._bridge._engine._catalog
        meta = catalog.get("tables", {}).get(table_name)
        if meta is None:
            d.insert("end", "Tabla no encontrada en el catalog\n")
            d.configure(state="disabled")
            return

        idx_type = meta.get("index", "SEQUENTIAL")
        idx_col  = meta.get("index_col", "-")

        d.insert("end", f"Tabla: {table_name}\n", "title")
        d.insert("end", f"Índice: {idx_type}   key col: {idx_col}\n\n", "info")

        def write_file(path: str, label: str):
            basename = os.path.basename(path)
            exists   = os.path.exists(path)
            size_str = f"{os.path.getsize(path):,} bytes" if exists else "archivo no existe aún"
            is_bin   = path.endswith(".bin")
            d.insert("end", f"  {label}\n", "dim")
            d.insert("end", f"  📄 {basename}\n", "bin" if is_bin else "json")
            d.insert("end", f"     {size_str}\n\n", "dim")

        if idx_type == "BTREE":
            write_file(meta["path"], "B+ Tree data file (.bin)")
        elif idx_type == "HASH":
            write_file(meta["path"], "Hash index data file (.bin)")
        else:  # SEQUENTIAL
            write_file(meta["main_path"],     "Main file - datos principales (.bin)")
            write_file(meta["overflow_path"], "Overflow file - inserciones recientes (.bin)")

        # RTree secundario - presente solo si la tabla se creó con INDEX (RTREE col)
        if meta.get("rtree_path"):
            rtree_col   = meta.get("rtree_col",   "?")
            rtree_col_y = meta.get("rtree_col_y", "?")
            write_file(meta["rtree_path"],
                       f"R-Tree spatial index  [{rtree_col}, {rtree_col_y}]  (.bin)")

        # catalog.json siempre presente si la tabla existe
        write_file(os.path.join(self._active_data_dir, "catalog.json"),
                   "Catalog metadata (.json)")

        d.configure(state="disabled")

    # carga un query de ejemplo; recuerda la última edición por label
    # force_default=True limpia la memoria y recarga el SQL original
    def _highlight_sql(self):
        if self._editor_has_placeholder:
            return
        text = self._editor.get("1.0", "end-1c")

        _TAGS = ("hl_kw", "hl_type", "hl_spatial", "hl_str", "hl_num", "hl_comment", "hl_star")
        for tag in _TAGS:
            self._editor.tag_remove(tag, "1.0", "end")

        def _pos(offset: int) -> str:
            before = text[:offset]
            line   = before.count("\n") + 1
            col    = offset - (before.rfind("\n") + 1)
            return f"{line}.{col}"

        # collect protected ranges (strings & comments) - keywords inside them are not coloured
        protected: list = []

        def _apply(pattern, tag, safe: bool = True):
            for m in pattern.finditer(text):
                s, e = m.start(), m.end()
                if safe and any(ps <= s and e <= pe for ps, pe in protected):
                    continue
                self._editor.tag_add(tag, _pos(s), _pos(e))
                if not safe:
                    protected.append((s, e))

        _apply(_HL_COMMENT, "hl_comment", safe=False)
        _apply(_HL_STRING,  "hl_str",     safe=False)
        _apply(_HL_KEYWORD, "hl_kw")
        _apply(_HL_TYPE,    "hl_type")
        _apply(_HL_SPATIAL, "hl_spatial")
        _apply(_HL_NUMBER,  "hl_num")
        _apply(_HL_STAR,    "hl_star")

    def _load_example(self, label: str, force_default: bool = False):
        # guarda el texto actual en la memoria del ejemplo activo
        if self._active_example and not self._editor_has_placeholder:
            self._example_edits[self._active_example] = self._editor.get("1.0", "end-1c")

        self._active_example = label

        # usa texto guardado si existe y no se fuerza reset
        if not force_default:
            saved = self._example_edits.get(label, "").strip()
            if saved:
                self._editor_has_placeholder = False
                self._editor.configure(fg=_C["text"])
                self._editor.delete("1.0", "end")
                self._editor.insert("1.0", saved)
                self._highlight_sql()
                self._editor.focus_set()
                return

        # sin texto guardado: default_testing carga SQL; otros workspaces muestran placeholder
        self._example_edits.pop(label, None)
        if self._active_project.get() == "default_testing":
            for lbl, sql in _EXAMPLE_QUERIES:
                if lbl == label:
                    self._editor_has_placeholder = False
                    self._editor.configure(fg=_C["text"])
                    self._editor.delete("1.0", "end")
                    self._editor.insert("1.0", sql)
                    self._highlight_sql()
                    self._editor.focus_set()
                    break
        else:
            self._show_placeholder()

    # ------------------------------------------------------------------
    # Editor placeholder
    # ------------------------------------------------------------------

    _PLACEHOLDER = (
        "SELECT * FROM Tabla WHERE ...\n"
        "INSERT INTO Tabla VALUES (ID, Atributo, ...)"
    )

    # muestra el texto fantasma en gris si el editor esta vacio
    def _show_placeholder(self):
        self._editor.delete("1.0", "end")
        self._editor.configure(fg=_C["overlay0"])
        self._editor.insert("1.0", self._PLACEHOLDER)
        self._editor_has_placeholder = True

    # borra el placeholder y restaura el color normal para escritura
    def _hide_placeholder(self):
        if self._editor_has_placeholder:
            self._editor.delete("1.0", "end")
            self._editor.configure(fg=_C["text"])
            self._editor_has_placeholder = False

    # FocusIn - limpia placeholder al hacer click/tab al editor
    def _on_editor_focus_in(self, _event=None):
        self._hide_placeholder()

    # FocusOut - si quedo vacio, vuelve a mostrar el placeholder
    def _on_editor_focus_out(self, _event=None):
        if not self._editor.get("1.0", "end-1c").strip():
            self._show_placeholder()

    # ------------------------------------------------------------------
    # Reset default workspace
    # ------------------------------------------------------------------

    # muestra/oculta el boton segun si el proyecto activo es default_testing
    def _update_reset_visibility(self):
        if self._active_project.get() == "default_testing":
            self._reset_btn.pack(side="right", padx=(0, 6), pady=4)
        else:
            self._reset_btn.pack_forget()

    # borra todos los .bin y catalog.json de default_testing y re-seedea
    def _reset_default(self):
        if self._active_project.get() != "default_testing":
            return
        ok = messagebox.askyesno(
            "Resetear default_testing",
            "Se borrarán todos los archivos .bin y catalog.json de\n"
            "'workspaces/default_testing/' y se volverán a cargar\nlos datos de ejemplo.\n\n¿Continuar?",
            parent=self,
        )
        if not ok:
            return

        import glob
        data_dir = self._active_data_dir
        for f in glob.glob(os.path.join(data_dir, "*.bin")):
            try:
                os.remove(f)
            except OSError:
                pass
        catalog_p = os.path.join(data_dir, "catalog.json")
        if os.path.exists(catalog_p):
            os.remove(catalog_p)

        self._bridge.reinit(data_dir)
        self._last_spatial = None
        self._example_edits.clear()
        self._active_example = ""
        self._load_example("INSERT")
        self._clear_tree()
        self._draw_empty_canvas()
        self._refresh_table_list()
        self._refresh_schema()
        self._set_status("Workspace 'default_testing' reiniciado con datos de ejemplo", _C["green"])


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------

def main():
    app = JupiterDBApp()
    app.mainloop()


if __name__ == "__main__":
    main()
