#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import tkinter as tk
from tkinter import ttk
from typing import Dict, List, Optional

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)

from gui.bridge import QueryBridge, QueryResult

# Paleta Catppuccin-Mocha
_C = {
    "bg":        "#1e1e2e",
    "surface":   "#181825",
    "mantle":    "#11111b",
    "overlay0":  "#6c7086",
    "overlay2":  "#9399b2",
    "text":      "#cdd6f4",
    "lavender":  "#b4befe",
    "blue":      "#89b4fa",
    "violet":    "#7c3aed",
    "green":     "#a6e3a1",
}

_FONT_MONO = ("Consolas", 10)
_FONT_UI   = ("Segoe UI", 10)
_FONT_BOLD = ("Segoe UI", 10, "bold")
_FONT_H    = ("Segoe UI", 12, "bold")


class JupiterApp(tk.Tk):
    """Ventana principal de JupiterDB — editor SQL y tabla de resultados."""

    def __init__(self):
        super().__init__()
        self.title("JupiterDB — Query Engine")
        self.geometry("1200x700")
        self.minsize(900, 500)
        self.configure(bg=_C["bg"])

        # Inicializar engine con workspace default
        _default_path = os.path.join(_BASE, "workspaces", "default_testing")
        os.makedirs(_default_path, exist_ok=True)
        self._bridge = QueryBridge(_default_path)

        self._apply_theme()
        self._build_ui()

    def _apply_theme(self):
        """Aplica el tema ttk Catppuccin-Mocha."""
        s = ttk.Style(self)
        s.theme_use("clam")

        base = dict(
            background=_C["bg"], foreground=_C["text"],
            fieldbackground=_C["mantle"], bordercolor=_C["overlay0"],
            troughcolor=_C["surface"], selectbackground=_C["violet"],
            selectforeground="#ffffff", font=_FONT_UI
        )
        s.configure(".", **base)

        s.configure("TFrame", background=_C["bg"])
        s.configure("TLabel", background=_C["bg"], foreground=_C["text"], font=_FONT_UI)
        s.configure("Header.TLabel", background=_C["bg"], foreground=_C["lavender"], font=_FONT_H)

        s.configure("Run.TButton",
                    background=_C["violet"], foreground="#ffffff",
                    font=_FONT_BOLD, padding=(12, 6), relief="flat", borderwidth=0)
        s.map("Run.TButton",
              background=[("active", "#6d28d9"), ("pressed", "#5b21b6")])

        s.configure("TNotebook", background=_C["surface"])
        s.configure("TNotebook.Tab", background=_C["surface"], foreground=_C["overlay2"],
                    font=_FONT_UI, padding=[12, 6])
        s.map("TNotebook.Tab",
              background=[("selected", _C["bg"])],
              foreground=[("selected", _C["text"])])

        s.configure("Treeview",
                    background=_C["bg"], foreground=_C["text"],
                    fieldbackground=_C["bg"], rowheight=22, font=_FONT_MONO)
        s.configure("Treeview.Heading",
                    background=_C["surface"], foreground=_C["blue"],
                    font=_FONT_BOLD, relief="flat")
        s.map("Treeview",
              background=[("selected", _C["violet"])],
              foreground=[("selected", "#ffffff")])

        s.configure("TScrollbar", background=_C["surface"], 
                    arrowcolor=_C["overlay0"], troughcolor=_C["mantle"])

    def _build_ui(self):
        """Orquesta la construcción de todos los paneles."""
        # Barra superior
        self._build_toolbar()

        # Contenedor principal
        main = ttk.Frame(self)
        main.pack(fill="both", expand=True, padx=8, pady=8)

        # Panel SQL
        self._build_sql_panel(main)

        # Panel de resultados
        self._build_result_panel(main)

    def _build_toolbar(self):
        """Construye la barra superior con nombre del proyecto."""
        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x", padx=8, pady=8)

        lbl = ttk.Label(toolbar, text="JupiterDB — Query Engine",
                       style="Header.TLabel")
        lbl.pack(side="left")

        ttk.Label(toolbar, text="default_testing",
                 style="Dim.TLabel", foreground=_C["overlay0"],
                 font=("Segoe UI", 9)).pack(side="left", padx=12)

    def _build_sql_panel(self, parent):
        """Construye el área de texto SQL y botón Ejecutar."""
        panel = ttk.Frame(parent)
        panel.pack(fill="both", expand=True, pady=(0, 12))

        # Label
        ttk.Label(panel, text="SQL Query", style="Header.TLabel").pack(anchor="w", pady=(0, 4))

        # Text + scroll
        frame = ttk.Frame(panel)
        frame.pack(fill="both", expand=True)

        scrollbar = ttk.Scrollbar(frame)
        scrollbar.pack(side="right", fill="y")

        self._editor = tk.Text(
            frame,
            width=100, height=10,
            bg=_C["mantle"], fg=_C["text"],
            insertbackground=_C["lavender"],
            font=_FONT_MONO, wrap="word",
            yscrollcommand=scrollbar.set
        )
        self._editor.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self._editor.yview)

        # Botón Ejecutar
        ttk.Button(parent, text="Ejecutar", command=self._run_query,
                  style="Run.TButton").pack(pady=(8, 0))

    def _build_result_panel(self, parent):
        """Construye la tabla de resultados con scroll."""
        panel = ttk.Frame(parent)
        panel.pack(fill="both", expand=True, pady=(12, 0))

        # Label
        ttk.Label(panel, text="Resultados", style="Header.TLabel").pack(anchor="w", pady=(0, 4))

        # Frame con scrollbars
        frame = ttk.Frame(panel)
        frame.pack(fill="both", expand=True)

        vsb = ttk.Scrollbar(frame, orient="vertical")
        hsb = ttk.Scrollbar(frame, orient="horizontal")
        vsb.pack(side="right", fill="y")
        hsb.pack(side="bottom", fill="x")

        self._result_tree = ttk.Treeview(
            frame, yscrollcommand=vsb.set, xscrollcommand=hsb.set
        )
        self._result_tree.pack(side="left", fill="both", expand=True)

        vsb.config(command=self._result_tree.yview)
        hsb.config(command=self._result_tree.xview)

    def _run_query(self):
        """Ejecuta el SQL desde el editor y muestra el resultado."""
        sql = self._editor.get("1.0", "end-1c").strip()
        if not sql:
            return

        result = self._bridge.execute(sql)
        self._display_results(result)

    def _display_results(self, result: QueryResult):
        """Renderiza el resultado en el Treeview."""
        # Limpiar treeview
        for item in self._result_tree.get_children():
            self._result_tree.delete(item)
        self._result_tree.delete(*self._result_tree.get_children())
        for col in self._result_tree["columns"]:
            self._result_tree.delete(col)

        # Según el kind, mostrar el contenido
        if result.kind == "message":
            self._result_tree["columns"] = ("msg",)
            self._result_tree.column("#0", width=0, stretch=False)
            self._result_tree.column("msg", anchor="w", width=800)
            self._result_tree.heading("#0", text="")
            self._result_tree.heading("msg", text=result.message)
            return

        if result.kind == "error":
            self._result_tree["columns"] = ("error",)
            self._result_tree.column("#0", width=0, stretch=False)
            self._result_tree.column("error", anchor="w", width=800)
            self._result_tree.heading("#0", text="")
            self._result_tree.heading("error", text=f"ERROR: {result.message}")
            return

        if result.kind == "table":
            # Configurar columnas
            cols = result.columns or []
            self._result_tree["columns"] = cols
            self._result_tree.column("#0", width=0, stretch=False)

            col_width = max(100, 800 // len(cols)) if cols else 100
            for col in cols:
                self._result_tree.column(col, anchor="w", width=col_width)
                self._result_tree.heading(col, text=col)

            # Insertar filas
            for row in result.rows:
                values = [str(row.get(col, "")) for col in cols]
                self._result_tree.insert("", "end", values=values)


def main():
    app = JupiterApp()
    app.mainloop()


if __name__ == "__main__":
    main()
