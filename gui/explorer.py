from __future__ import annotations

import json
import os
import struct
import sys
import tkinter as tk
from tkinter import ttk
from typing import List, Optional, Tuple

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)

from core.schema import FieldType, RecordSerializer, SchemaField
from core.storage import DiskController, PAGE_SIZE

_FONT_MONO = ("Consolas", 9)
_FONT_UI   = ("Segoe UI", 10)
_FONT_BOLD = ("Segoe UI", 10, "bold")

# Default palette — overridden by app.py at construction time
_PALETTE = {
    "bg":       "#1e1e2e",
    "surface":  "#181825",
    "mantle":   "#11111b",
    "text":     "#cdd6f4",
    "overlay0": "#6c7086",
    "overlay2": "#9399b2",
    "blue":     "#89b4fa",
    "green":    "#a6e3a1",
    "red":      "#f38ba8",
    "yellow":   "#f9e2af",
    "mauve":    "#cba6f7",
    "violet":   "#7c3aed",
}


class PageViewer(ttk.Frame):

    # inicializa el widget con DiskController y construye el layout
    def __init__(self, parent, data_dir: str, palette: dict = None, **kw):
        super().__init__(parent, **kw)
        self._data_dir = data_dir
        self._disk     = DiskController()
        self._C        = palette or _PALETTE
        self._mode     = tk.StringVar(value="hex")
        self._current_bytes: Optional[bytes] = None

        self._build()
        self._refresh_files()

    # ------------------------------------------------------------------ #
    # Layout                                                               #
    # ------------------------------------------------------------------ #

    # construye toolbar, spinbox de página, radios y text area con scrollbars
    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        top = ttk.Frame(self)
        top.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))

        ttk.Label(top, text="Archivo:").grid(row=0, column=0, padx=(0, 4))

        # asegura que el estilo Light.TCombobox exista (puede ser el primer build si se corre standalone)
        _s = ttk.Style()
        _s.configure("Light.TCombobox",
                      foreground="#000000", fieldbackground="#dde1f0",
                      selectbackground="#b8bfe8", selectforeground="#000000",
                      bordercolor=self._C.get("overlay0", "#6c7086"),
                      arrowcolor=self._C.get("overlay0", "#6c7086"))
        _s.map("Light.TCombobox",
               foreground=[("readonly", "#000000"), ("disabled", "#9399b2")],
               fieldbackground=[("readonly", "#dde1f0"), ("disabled", "#181825")])
        _s.configure("Light.TSpinbox",
                      foreground="#000000", fieldbackground="#dde1f0",
                      background="#e0e3f0", arrowcolor="#555555",
                      bordercolor=self._C.get("overlay0", "#6c7086"))
        _s.map("Light.TSpinbox",
               foreground=[("disabled", "#666666"), ("readonly", "#000000")],
               fieldbackground=[("disabled", "#c0c4d6"), ("readonly", "#dde1f0")])

        self._file_var = tk.StringVar()
        self._file_cb  = ttk.Combobox(
            top, textvariable=self._file_var,
            state="readonly", width=32, style="Light.TCombobox",
        )
        self._file_cb.grid(row=0, column=1, sticky="ew", padx=(0, 4))
        self._file_cb.bind("<<ComboboxSelected>>", self._on_file_select)
        top.columnconfigure(1, weight=1)

        ttk.Button(top, text="↻", width=3,
                   command=self._refresh_files).grid(row=0, column=2, padx=(0, 12))

        ttk.Label(top, text="Página:").grid(row=0, column=3, padx=(0, 4))

        self._page_var  = tk.StringVar(value="0")
        self._page_spin = ttk.Spinbox(
            top, textvariable=self._page_var,
            from_=0, to=0, width=6, state="disabled",
            command=self._read_page, style="Light.TSpinbox",
        )
        self._page_spin.grid(row=0, column=4)

        ttk.Label(top, text="/").grid(row=0, column=5, padx=4)
        self._page_max_lbl = ttk.Label(top, text="0")
        self._page_max_lbl.grid(row=0, column=6, padx=(0, 12))

        ttk.Radiobutton(top, text="Hex",     variable=self._mode,
                        value="hex",     command=self._redraw).grid(row=0, column=7, padx=2)
        ttk.Radiobutton(top, text="ASCII",   variable=self._mode,
                        value="ascii",   command=self._redraw).grid(row=0, column=8, padx=2)
        ttk.Radiobutton(top, text="Decoded", variable=self._mode,
                        value="decoded", command=self._redraw).grid(row=0, column=9, padx=2)
        ttk.Radiobutton(top, text="Esquema", variable=self._mode,
                        value="schema",  command=self._redraw).grid(row=0, column=10, padx=(2, 12))

        self._read_btn = ttk.Button(
            top, text="Leer Página",
            command=self._read_page, state="disabled",
        )
        self._read_btn.grid(row=0, column=11)

        self._info_lbl = ttk.Label(top, text="  Selecciona un archivo para comenzar")
        self._info_lbl.grid(row=0, column=12, padx=(10, 0))

        # ── Text area ────────────────────────────────────────────────────
        body = ttk.Frame(self)
        body.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)

        self._text = tk.Text(
            body,
            bg=self._C["mantle"], fg=self._C["text"],
            insertbackground=self._C["blue"],
            font=_FONT_MONO, relief="flat",
            wrap="none", state="disabled",
            selectbackground=self._C.get("violet", "#7c3aed"),
            selectforeground="#ffffff",
        )
        vsb = ttk.Scrollbar(body, orient="vertical",   command=self._text.yview)
        hsb = ttk.Scrollbar(body, orient="horizontal", command=self._text.xview)
        self._text.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self._text.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        # colour tags para el render hex y decoded
        self._text.tag_configure("addr",    foreground=self._C["overlay2"])
        self._text.tag_configure("hex_a",   foreground=self._C["blue"])
        self._text.tag_configure("hex_b",   foreground=self._C["mauve"])
        self._text.tag_configure("ascii_p", foreground=self._C["green"])
        self._text.tag_configure("ascii_n", foreground=self._C["overlay0"])
        self._text.tag_configure("null",    foreground="#2a2a3e")
        self._text.tag_configure("fname",   foreground=self._C["yellow"])
        self._text.tag_configure("fval",    foreground=self._C["text"])
        self._text.tag_configure("ftype",   foreground=self._C["overlay2"])
        self._text.tag_configure("recno",   foreground=self._C["mauve"])

        # schema visual tags — header / even records / odd records / page padding
        # header page (block 0): rojo pastel
        self._text.tag_configure("sch_hdr",   background="#4a1010", foreground="#f38ba8")
        self._text.tag_configure("sch_hdr_n", background="#1e1020", foreground="#3a1a1a")
        # even record field 0 — azul oscuro
        self._text.tag_configure("sch_r0_f0",   background="#0d1e3a", foreground="#89b4fa")
        self._text.tag_configure("sch_r0_f0_n", background="#0d1e3a", foreground="#1e2e4a")
        # even record field 1 — azul ligeramente distinto (field shading)
        self._text.tag_configure("sch_r0_f1",   background="#0f2240", foreground="#74c7ec")
        self._text.tag_configure("sch_r0_f1_n", background="#0f2240", foreground="#182a3a")
        # odd record field 0 — verde oscuro
        self._text.tag_configure("sch_r1_f0",   background="#0d2e1a", foreground="#a6e3a1")
        self._text.tag_configure("sch_r1_f0_n", background="#0d2e1a", foreground="#1a3a22")
        # odd record field 1 — verde/teal ligeramente distinto
        self._text.tag_configure("sch_r1_f1",   background="#0f3520", foreground="#94e2d5")
        self._text.tag_configure("sch_r1_f1_n", background="#0f3520", foreground="#183028")
        # page padding (bytes sin usar al final de la página) — negro casi puro
        self._text.tag_configure("sch_pad", background="#111118", foreground="#28283c")

        # ── Legend (schema visual, oculta por defecto) ─────────────────────
        self._legend_frame = tk.Frame(self, bg=self._C["mantle"])
        self._legend_frame.grid(row=2, column=0, sticky="ew", padx=8, pady=(0, 6))
        self._build_schema_legend()
        self._legend_frame.grid_remove()   # oculta hasta que se active el modo schema

    # ------------------------------------------------------------------ #
    # File management                                                      #
    # ------------------------------------------------------------------ #

    # entry point público para que app.py refresque después de un CSV ingest
    def refresh(self):
        self._refresh_files()

    # actualiza el combobox de archivos sin leer ninguna page
    def refresh_list_only(self) -> None:
        try:
            files = sorted(f for f in os.listdir(self._data_dir) if f.endswith(".bin"))
        except FileNotFoundError:
            files = []
        prev = self._file_var.get()
        self._file_cb["values"] = files
        if prev not in files:
            self._file_var.set("")

    # cambia el workspace activo y recarga la lista de archivos
    def set_data_dir(self, data_dir: str) -> None:
        self._data_dir = data_dir
        self._current_bytes = None
        self._refresh_files()

    # escanea data_dir y repopula el combobox manteniendo selección previa
    def _refresh_files(self):
        try:
            files = sorted(
                f for f in os.listdir(self._data_dir)
                if f.endswith(".bin")
            )
        except FileNotFoundError:
            files = []

        prev = self._file_var.get()
        self._file_cb["values"] = files
        if prev in files:
            self._file_var.set(prev)
            self._on_file_select()
        elif files:
            self._file_cb.current(0)
            self._on_file_select()

    # calcula el total de páginas del archivo y habilita los controles
    def _on_file_select(self, _event=None):
        fname = self._file_var.get()
        if not fname:
            return
        path = os.path.join(self._data_dir, fname)
        size = os.path.getsize(path) if os.path.exists(path) else 0
        max_page = max(0, (size // PAGE_SIZE) - 1)
        self._page_spin.configure(from_=0, to=max_page, state="normal")
        self._page_max_lbl.configure(text=str(max_page))
        self._read_btn.configure(state="normal")
        self._page_var.set("0")
        self._info_lbl.configure(
            text=f"  {size:,} bytes  •  {max_page + 1} página(s)",
        )
        self._read_page()

    # ------------------------------------------------------------------ #
    # Page I/O                                                             #
    # ------------------------------------------------------------------ #

    # lee un block via DiskController y dispara el render
    def _read_page(self, *_):
        fname = self._file_var.get()
        if not fname:
            return
        try:
            pid = int(self._page_var.get())
        except ValueError:
            return

        path = os.path.join(self._data_dir, fname)
        try:
            data = self._disk.read_block(path, pid)
            self._current_bytes = data
            non_null = sum(1 for b in data if b != 0)
            self._info_lbl.configure(
                text=f"  Página {pid}  •  {len(data)} bytes  •  {non_null} no-nulos",
            )
            self._redraw()
        except Exception as exc:
            self._info_lbl.configure(text=f"  Error: {exc}")
            self._set_text(f"[Error al leer página {pid}]\n{exc}", error=True)

    # ------------------------------------------------------------------ #
    # Rendering                                                            #
    # ------------------------------------------------------------------ #

    # despacha al renderer correcto y muestra/oculta la leyenda del schema
    def _redraw(self):
        if self._current_bytes is None:
            return
        m = self._mode.get()
        if m == "schema":
            self._legend_frame.grid()
        else:
            self._legend_frame.grid_remove()
        if m == "hex":
            self._render_hex(self._current_bytes)
        elif m == "decoded":
            self._render_decoded(self._current_bytes)
        elif m == "schema":
            self._render_schema(self._current_bytes)
        else:
            self._render_ascii(self._current_bytes)

    # renderiza dump hex estilo xxd con sidebar ASCII
    def _render_hex(self, data: bytes):
        self._text.configure(state="normal")
        self._text.delete("1.0", "end")

        for base in range(0, len(data), 16):
            chunk = data[base: base + 16]

            # Address
            self._text.insert("end", f"{base:08x}  ", "addr")

            # hex bytes con color alternante por grupo de 8
            for i, b in enumerate(chunk):
                tag = "hex_a" if (i // 8) % 2 == 0 else "hex_b"
                byte_tag = "null" if b == 0 else tag
                self._text.insert("end", f"{b:02x} ", byte_tag)
                if i == 7:
                    self._text.insert("end", " ")

            # padding para la última línea corta
            pad = 16 - len(chunk)
            if pad:
                self._text.insert("end", "   " * pad)
                if pad > 8:
                    self._text.insert("end", " ")

            # sidebar ASCII
            self._text.insert("end", " |")
            for b in chunk:
                if 32 <= b < 127:
                    self._text.insert("end", chr(b), "ascii_p")
                else:
                    self._text.insert("end", ".", "ascii_n")
            self._text.insert("end", "|\n")

        self._text.configure(state="disabled")
        self._text.see("1.0")

    # renderiza bytes como texto ASCII en filas de 80 chars
    def _render_ascii(self, data: bytes):
        self._text.configure(state="normal")
        self._text.delete("1.0", "end")

        lines = []
        for base in range(0, len(data), 80):
            chunk = data[base: base + 80]
            lines.append("".join(chr(b) if 32 <= b < 127 else "." for b in chunk))

        self._text.insert("end", "\n".join(lines))
        self._text.configure(state="disabled")
        self._text.see("1.0")

    # ------------------------------------------------------------------ #
    # Schema Visual                                                        #
    # ------------------------------------------------------------------ #

    # leyenda interactiva con swatches de color para cada tipo y region
    def _build_schema_legend(self):
        items = [
            ("sch_hdr",   "H  Header",   "#f38ba8", "#4a1010"),
            ("sch_r0_f0", "I  Integer",  "#89b4fa", "#0d1e3a"),
            ("sch_r0_f0", "B  BigInt",   "#89b4fa", "#0d1e3a"),
            ("sch_r0_f0", "F  Float",    "#89b4fa", "#0d1e3a"),
            ("sch_r0_f0", "S  String",   "#74c7ec", "#0f2240"),
            ("sch_r1_f0", "Par  Rec",    "#a6e3a1", "#0d2e1a"),
            ("sch_r1_f1", "Impar  Rec",  "#94e2d5", "#0f3520"),
            ("sch_pad",   "·  Padding",  "#28283c", "#111118"),
        ]
        tk.Label(self._legend_frame,
                 text="Leyenda: ",
                 bg=self._C["mantle"], fg=self._C["overlay2"],
                 font=("Segoe UI", 8)).pack(side="left", padx=(4, 8))
        for _, label, fg, bg in items:
            box = tk.Label(self._legend_frame,
                           text=f" {label} ",
                           bg=bg, fg=fg,
                           font=("Consolas", 8), relief="flat", padx=2)
            box.pack(side="left", padx=2, pady=2)

    # precomputa lista (byte_start, byte_end_excl, field_idx, letter) para un serializer
    def _schema_field_ranges(self, ser: RecordSerializer) -> List[Tuple]:
        from core.schema import FieldType
        ranges = []
        off = 0
        for fi, field in enumerate(ser.fields):
            sz = struct.calcsize("!" + field.struct_char)
            ft = field.field_type
            if   ft == FieldType.INTEGER: ltr = "I"
            elif ft == FieldType.BIGINT:  ltr = "B"   # 8 bytes — 'i' a 'q'
            elif ft == FieldType.FLOAT:   ltr = "F"
            else:                         ltr = "S"
            ranges.append((off, off + sz, fi, ltr))
            off += sz
        return ranges

    # mapea cada byte de la página a (letter, tag) según schema y posición
    def _build_byte_map(self, data: bytes) -> List[Tuple[str, str]]:
        page_id = int(self._page_var.get() or 0)
        ser     = self._load_serializer()

        # block 0 es el header page — 4 bytes count + resto padding
        if page_id == 0 or ser is None:
            result = []
            for i, b in enumerate(data):
                if page_id == 0 and i < 4:
                    result.append(("H", "sch_hdr" if b else "sch_hdr_n"))
                else:
                    result.append(("H", "sch_hdr_n"))
            return result

        rs  = ser.record_size
        rpp = PAGE_SIZE // rs           # max slots por page
        data_end = rpp * rs             # byte offset donde empieza el page padding

        field_ranges = self._schema_field_ranges(ser)

        result = []
        for bi, b in enumerate(data):
            if bi >= data_end:
                result.append(("·", "sch_pad"))
                continue

            slot     = bi // rs
            fb       = bi % rs

            # busca a qué field pertenece este byte dentro del record
            ltr, fi = "?", 0
            for (fstart, fend, fidx, fletter) in field_ranges:
                if fstart <= fb < fend:
                    ltr, fi = fletter, fidx
                    break

            rp  = slot % 2   # 0=par 1=impar
            fp  = fi   % 2   # field shading dentro del record
            tag = f"sch_r{rp}_f{fp}" + ("_n" if b == 0 else "")
            result.append((ltr, tag))

        return result

    # render schema visual — letras por tipo de campo con background coloreado por record
    def _render_schema(self, data: bytes):
        byte_map = self._build_byte_map(data)

        self._text.configure(state="normal")
        self._text.delete("1.0", "end")

        for base in range(0, len(data), 16):
            row = byte_map[base: base + 16]

            self._text.insert("end", f"{base:08x}  ", "addr")

            # batch consecutive same-tag chars para reducir inserts
            pending_txt = ""
            pending_tag: Optional[str] = None

            def flush():
                nonlocal pending_txt, pending_tag
                if pending_txt:
                    self._text.insert("end", pending_txt, pending_tag)
                pending_txt = ""
                pending_tag = None

            for i, (ltr, tag) in enumerate(row):
                char = ltr + " "
                if tag == pending_tag:
                    pending_txt += char
                else:
                    flush()
                    pending_txt = char
                    pending_tag = tag
                if i == 7:   # separador de grupo igual que hex view
                    flush()
                    self._text.insert("end", " ")

            flush()

            # padding para la última fila corta
            pad = 16 - len(row)
            if pad:
                self._text.insert("end", "   " * pad)
                if pad > 8:
                    self._text.insert("end", " ")

            self._text.insert("end", "\n")

        self._text.configure(state="disabled")
        self._text.see("1.0")

    # ------------------------------------------------------------------ #
    # Decoded view                                                         #
    # ------------------------------------------------------------------ #

    # carga el RecordSerializer desde catalog.json para el archivo seleccionado
    def _load_serializer(self) -> Optional[RecordSerializer]:
        fname = self._file_var.get()
        if not fname:
            return None
        # extrae table_name desde "TableName_main.bin" o "TableName_ovfl.bin"
        base = fname.replace("_main.bin", "").replace("_ovfl.bin", "").replace("_overflow.bin", "")
        catalog_path = os.path.join(self._data_dir, "catalog.json")
        if not os.path.exists(catalog_path):
            return None
        try:
            with open(catalog_path, encoding="utf-8") as fh:
                catalog = json.load(fh)
            meta = catalog.get("tables", {}).get(base)
            if not meta:
                return None
            fields = []
            for f in meta.get("fields", []):
                ft = FieldType[f["type"]]
                fields.append(SchemaField(name=f["name"], field_type=ft,
                                          str_size=f.get("str_size")))
            return RecordSerializer(fields)
        except Exception:
            return None

    # decodifica registros usando el schema del catalog; muestra BIGINT como entero de 64 bits
    def _render_decoded(self, data: bytes):
        self._text.configure(state="normal")
        self._text.delete("1.0", "end")

        ser = self._load_serializer()
        if ser is None:
            self._text.insert("end",
                "No se encontró schema para este archivo.\n"
                "Verifica que catalog.json exista en el workspace\n"
                "y que el nombre del archivo coincida con el de la tabla.\n",
                "ascii_n")
            self._text.configure(state="disabled")
            return

        rs = ser.record_size
        n_records = len(data) // rs
        shown = 0

        _FT_LABEL = {
            FieldType.INTEGER: "INT",
            FieldType.BIGINT:  "BIGINT",   # 'i'→'q': 8 bytes, previene overflow
            FieldType.FLOAT:   "FLOAT",
            FieldType.STRING:  "STRING",
        }

        for idx in range(n_records):
            chunk = data[idx * rs: idx * rs + rs]
            if all(b == 0 for b in chunk):
                continue   # slot vacío / padding
            try:
                rec = ser.deserialize(chunk)
            except Exception:
                continue
            shown += 1
            self._text.insert("end", f"[{idx:4d}]  ", "recno")
            for i, field in enumerate(ser.fields):
                sep = "  " if i else ""
                self._text.insert("end", sep + field.name + "=", "fname")
                self._text.insert("end", repr(rec[field.name]), "fval")
                self._text.insert("end",
                    f" ({_FT_LABEL.get(field.field_type, '?')})", "ftype")
            self._text.insert("end", "\n")

        if shown == 0:
            self._text.insert("end", "(página vacía — todos los slots son null)\n", "ascii_n")
        else:
            self._text.insert("end",
                f"\n— {shown} registro(s) decodificado(s) · "
                f"record_size={rs} B · page={len(data)} B —\n", "addr")

        self._text.configure(state="disabled")
        self._text.see("1.0")

    # escribe mensaje de error o info en el text widget con tag de color
    def _set_text(self, msg: str, error: bool = False):
        self._text.configure(state="normal")
        self._text.delete("1.0", "end")
        tag = "hex_b" if error else "ascii_p"
        self._text.insert("end", msg, tag)
        self._text.configure(state="disabled")
