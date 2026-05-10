#!/usr/bin/env python3
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # no hacemos display
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import pandas as pd
import numpy as np

_ROOT          = Path(__file__).resolve().parent.parent
INDEX_CSV_PATH = _ROOT / "results" / "index_metrics.csv"
JOIN_CSV_PATH  = _ROOT / "results" / "join_metrics.csv"
TEMPLATE_PATH  = _ROOT / "docs" / "report_template.md"
OUTPUT_PATH    = _ROOT / "docs" / "FINAL_REPORT.md"
CHARTS_DIR     = _ROOT / "docs" / "charts"

_N_REF        = 46_419  # Basados en los benchmarks usados
_NS           = [3_376, 46_419, 101_884]
_N_KEYS       = {3_376: "3k", 46_419: "46k", 101_884: "101k"}
_INDEX_TYPES  = ["Sequential", "BPlusTree", "ExtHash"]
_IDX_KEYS     = {"Sequential": "seq", "BPlusTree": "bt", "ExtHash": "hash"}
_INDEX_COLORS = {"Sequential": "#4C72B0", "BPlusTree": "#DD8452", "ExtHash": "#55A868"}
_IDX_LABELS   = {"Sequential": "Sequential", "BPlusTree": "B+ Tree", "ExtHash": "Ext. Hash"}


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def _load(index_csv: Path, join_csv: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    idx  = pd.read_csv(index_csv, dtype=str) if index_csv.exists() else pd.DataFrame()
    join = pd.read_csv(join_csv,  dtype=str) if join_csv.exists()  else pd.DataFrame()
    return idx, join


def _idx_val(df: pd.DataFrame, index_type: str, n: int, col: str) -> str:
    if df.empty or col not in df.columns:
        return "N/A"
    row = df[(df["index_type"] == index_type) & (df["N"] == str(n))]
    if row.empty:
        return "N/A"
    v = row[col].values[0]
    return "N/A" if (pd.isna(v) or str(v).strip() == "") else str(v)


def _idx_float(df: pd.DataFrame, index_type: str, n: int, col: str) -> float:
    v = _idx_val(df, index_type, n, col)
    try:
        return float(v)
    except (ValueError, TypeError):
        return 0.0


def _join_val(df: pd.DataFrame, strategy: str, col: str) -> str:
    """col usa los nombres de las columnas en join_metrics.csv"""
    if df.empty:
        return "N/A"
    row = df[df["join_strategy"] == strategy]
    if row.empty or col not in df.columns:
        return "N/A"
    v = row[col].values[0]
    return "N/A" if (pd.isna(v) or str(v).strip() == "") else str(v)


def _join_float(df: pd.DataFrame, strategy: str, col: str) -> float:
    v = _join_val(df, strategy, col)
    try:
        return float(v)
    except (ValueError, TypeError):
        return 0.0


# ---------------------------------------------------------------------------
# Constructores de marcadores
# ---------------------------------------------------------------------------

def _conclusion(idx: pd.DataFrame, join: pd.DataFrame) -> str:
    try:
        m  = float(_join_val(join, "MERGE",               "total_accesses"))
        ex = float(_join_val(join, "EXTERNAL_SORT_MERGE", "total_accesses"))
        diff = abs(int(m - ex))
        winner = "External Sort Merge" if ex < m else "Merge Join"
        loser  = "Merge Join" if ex < m else "External Sort Merge"
        return (
            f"**{winner}** requiere {diff:,} accesos a disco menos que {loser}. "
            f"La eficiencia del External Sort Merge se explica por el acceso secuencial "
            f"paginado en ambas fases del TPMMS, mientras que el Merge Join sobre "
            f"Sequential File debe recorrer páginas de ambas tablas completas."
        )
    except Exception:
        return (
            "El External Sort Merge supera al Merge Join indexado gracias al "
            "acceso secuencial del TPMMS, que minimiza los seeks en disco."
        )


def _build_subs(idx: pd.DataFrame, join: pd.DataFrame) -> dict[str, str]:
    subs: dict[str, str] = {
        "date":    datetime.now().strftime("%Y-%m-%d %H:%M"),
        "n_val":   f"{_N_REF:,}",
        # Marcadores antiguos de un solo N (compatibilidad)
        "seq_reads":         _idx_val(idx, "Sequential", _N_REF, "search_avg_reads"),
        "seq_writes":        _idx_val(idx, "Sequential", _N_REF, "insert_writes"),
        "seq_ms":            _idx_val(idx, "Sequential", _N_REF, "insert_ms"),
        "btree_reads":       _idx_val(idx, "BPlusTree",  _N_REF, "search_avg_reads"),
        "btree_writes":      _idx_val(idx, "BPlusTree",  _N_REF, "insert_writes"),
        "btree_ms":          _idx_val(idx, "BPlusTree",  _N_REF, "insert_ms"),
        "hash_reads":        _idx_val(idx, "ExtHash",    _N_REF, "search_avg_reads"),
        "hash_writes":       _idx_val(idx, "ExtHash",    _N_REF, "insert_writes"),
        "hash_ms":           _idx_val(idx, "ExtHash",    _N_REF, "insert_ms"),
        # join base - columnas de join_metrics.csv (sin prefijo 'join_')
        "merge_pages_read":      _join_val(join, "MERGE",               "pages_read"),
        "merge_pages_written":   _join_val(join, "MERGE",               "pages_written"),
        "merge_accesses":        _join_val(join, "MERGE",               "total_accesses"),
        "merge_ms":              _join_val(join, "MERGE",               "elapsed_ms"),
        "ext_sort_pages_read":   _join_val(join, "EXTERNAL_SORT_MERGE", "pages_read"),
        "ext_sort_pages_written":_join_val(join, "EXTERNAL_SORT_MERGE", "pages_written"),
        "ext_sort_accesses":     _join_val(join, "EXTERNAL_SORT_MERGE", "total_accesses"),
        "ext_sort_ms":           _join_val(join, "EXTERNAL_SORT_MERGE", "elapsed_ms"),
        "conclusion_auto": _conclusion(idx, join),
    }

    # Marcadores por N e índice
    col_map = {
        "ins_reads":   "insert_reads",
        "ins_writes":  "insert_writes",
        "ins_ms":      "insert_ms",
        "srch_reads":  "search_avg_reads",
        "srch_ms":     "search_avg_ms",
        "range_reads": "range_reads",
        "range_ms":    "range_ms",
    }
    for n_int, n_key in _N_KEYS.items():
        for itype, ikey in _IDX_KEYS.items():
            for field_key, col in col_map.items():
                subs[f"{ikey}_{field_key}_{n_key}"] = _idx_val(idx, itype, n_int, col)
            # Accesos totales de inserción
            try:
                r = float(_idx_val(idx, itype, n_int, "insert_reads"))
                w = float(_idx_val(idx, itype, n_int, "insert_writes"))
                subs[f"{ikey}_ins_total_{n_key}"] = str(int(r + w))
            except (ValueError, TypeError):
                subs[f"{ikey}_ins_total_{n_key}"] = "N/A"

    return subs


def _fill(template_path: Path, subs: dict[str, str]) -> str:
    content = template_path.read_text(encoding="utf-8")
    for key, value in subs.items():
        content = content.replace(f"{{{{{key}}}}}", value)
    return content


# ---------------------------------------------------------------------------
# GRÁFICOS
# ---------------------------------------------------------------------------

def _style_ax(ax: plt.Axes) -> None:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y", linestyle="--", alpha=0.4)


# Gráfico de barras: accesos totales de inserción por tipo de índice × N
def _chart_disk_accesses(idx: pd.DataFrame, join: pd.DataFrame, out: Path) -> None:
    ns     = sorted(int(n) for n in idx["N"].unique())
    labels = [f"N={n // 1_000}k" for n in ns]
    x      = np.arange(len(ns))
    width  = 0.25

    fig, ax = plt.subplots(figsize=(10, 5))
    for i, itype in enumerate(_INDEX_TYPES):
        vals = []
        for n in ns:
            r = _idx_float(idx, itype, n, "insert_reads")
            w = _idx_float(idx, itype, n, "insert_writes")
            vals.append(int(r + w))
        offset = (i - 1) * width
        bars = ax.bar(x + offset, vals, width=width,
                      label=_IDX_LABELS[itype], color=_INDEX_COLORS[itype])
        ax.bar_label(bars, padding=3, fontsize=7,
                     labels=[f"{v:,}" for v in vals])

    merge_acc = _join_val(join, "MERGE", "total_accesses")
    ext_acc   = _join_val(join, "EXTERNAL_SORT_MERGE", "total_accesses")
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylabel("Accesos a Disco (Lecturas + Escrituras de Inserción)")
    ax.set_title("Accesos a Disco por Estructura de Índice (Inserción)")
    ax.legend()
    ax.annotate(
        f"Joins @ N=5k - MERGE: {merge_acc} acc  |  Ext.Sort: {ext_acc} acc",
        xy=(0.5, 0.98), xycoords="axes fraction",
        ha="center", va="top", fontsize=8, color="#555555",
    )
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{int(v):,}"))
    _style_ax(ax)
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    plt.close(fig)
    print(f"  [chart] {out.name}")


# Gráfico de barras: tiempo de inserción por tipo de índice × N
def _chart_execution_time(idx: pd.DataFrame, join: pd.DataFrame, out: Path) -> None:
    ns     = sorted(int(n) for n in idx["N"].unique())
    labels = [f"N={n // 1_000}k" for n in ns]
    x      = np.arange(len(ns))
    width  = 0.25

    fig, ax = plt.subplots(figsize=(10, 5))
    for i, itype in enumerate(_INDEX_TYPES):
        vals = [_idx_float(idx, itype, n, "insert_ms") for n in ns]
        offset = (i - 1) * width
        bars = ax.bar(x + offset, vals, width=width,
                      label=_IDX_LABELS[itype], color=_INDEX_COLORS[itype])
        ax.bar_label(bars, fmt="%.0f ms", padding=3, fontsize=7)

    merge_ms = _join_val(join, "MERGE", "elapsed_ms")
    ext_ms   = _join_val(join, "EXTERNAL_SORT_MERGE", "elapsed_ms")
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylabel("Tiempo de Inserción (ms)")
    ax.set_title("Tiempo de Ejecución - Inserción por Estructura de Índice")
    ax.legend()
    ax.annotate(
        f"Joins @ N=5k - MERGE: {merge_ms} ms  |  Ext.Sort: {ext_ms} ms",
        xy=(0.5, 0.98), xycoords="axes fraction",
        ha="center", va="top", fontsize=8, color="#555555",
    )
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    _style_ax(ax)
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    plt.close(fig)
    print(f"  [chart] {out.name}")


# Gráfico de líneas: lecturas promedio de búsqueda vs N para los 3 índices
def _chart_search_reads(idx: pd.DataFrame, out: Path) -> None:
    ns     = sorted(int(n) for n in idx["N"].unique())
    labels = [f"{n // 1_000}k" for n in ns]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

    # izquierda: lecturas promedio
    for itype in _INDEX_TYPES:
        vals = [_idx_float(idx, itype, n, "search_avg_reads") for n in ns]
        ax1.plot(labels, vals, marker="o", label=_IDX_LABELS[itype],
                 color=_INDEX_COLORS[itype], linewidth=2, markersize=6)
        for xi, yi in zip(labels, vals):
            ax1.annotate(f"{yi:.1f}", (xi, yi), textcoords="offset points",
                         xytext=(0, 6), ha="center", fontsize=8)

    ax1.set_xlabel("N (registros)")
    ax1.set_ylabel("Lecturas Promedio por Búsqueda")
    ax1.set_title("Búsqueda Puntual - Lecturas Promedio vs N")
    ax1.legend()
    _style_ax(ax1)

    # derecha: tiempo promedio (ms)
    for itype in _INDEX_TYPES:
        vals = [_idx_float(idx, itype, n, "search_avg_ms") for n in ns]
        ax2.plot(labels, vals, marker="s", label=_IDX_LABELS[itype],
                 color=_INDEX_COLORS[itype], linewidth=2, markersize=6)
        for xi, yi in zip(labels, vals):
            ax2.annotate(f"{yi:.3f}", (xi, yi), textcoords="offset points",
                         xytext=(0, 6), ha="center", fontsize=7)

    ax2.set_xlabel("N (registros)")
    ax2.set_ylabel("Tiempo Promedio (ms)")
    ax2.set_title("Búsqueda Puntual - Tiempo Promedio vs N")
    ax2.legend()
    _style_ax(ax2)

    fig.tight_layout()
    fig.savefig(out, dpi=130)
    plt.close(fig)
    print(f"  [chart] {out.name}")


# Gráfico de líneas: lecturas de range search vs N (solo Sequential + BPlusTree)
def _chart_range_reads(idx: pd.DataFrame, out: Path) -> None:
    ns     = sorted(int(n) for n in idx["N"].unique())
    labels = [f"{n // 1_000}k" for n in ns]
    range_types = ["Sequential", "BPlusTree"]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

    for itype in range_types:
        reads = [_idx_float(idx, itype, n, "range_reads") for n in ns]
        ax1.plot(labels, reads, marker="o", label=_IDX_LABELS[itype],
                 color=_INDEX_COLORS[itype], linewidth=2, markersize=6)
        for xi, yi in zip(labels, reads):
            if yi > 0:
                ax1.annotate(f"{int(yi)}", (xi, yi), textcoords="offset points",
                             xytext=(0, 6), ha="center", fontsize=8)

    ax1.set_xlabel("N (registros)")
    ax1.set_ylabel("Páginas Leídas")
    ax1.set_title("Range Search - Accesos a Disco vs N")
    ax1.legend()
    _style_ax(ax1)

    for itype in range_types:
        times = [_idx_float(idx, itype, n, "range_ms") for n in ns]
        ax2.plot(labels, times, marker="s", label=_IDX_LABELS[itype],
                 color=_INDEX_COLORS[itype], linewidth=2, markersize=6)
        for xi, yi in zip(labels, times):
            if yi > 0:
                ax2.annotate(f"{yi:.2f}", (xi, yi), textcoords="offset points",
                             xytext=(0, 6), ha="center", fontsize=8)

    ax2.set_xlabel("N (registros)")
    ax2.set_ylabel("Tiempo (ms)")
    ax2.set_title("Range Search - Tiempo de Ejecución vs N")
    ax2.legend()
    _style_ax(ax2)

    fig.suptitle("Range Search (~10% de N) - Sequential vs B+ Tree\n"
                 "(Extendible Hash no soporta range search)",
                 fontsize=10, y=1.02)
    fig.tight_layout()
    fig.savefig(out, dpi=130, bbox_inches="tight")
    plt.close(fig)
    print(f"  [chart] {out.name}")


# Gráfico de barras agrupadas horizontales: comparación de estrategias de join (accesos + ms)
def _chart_joins(join: pd.DataFrame, out: Path) -> None:
    strategies = ["MERGE", "EXTERNAL_SORT_MERGE"]
    labels_str = ["Merge Join\n(Sequential)", "Ext. Sort\nMerge"]
    colors     = ["#C44E52", "#4C72B0"]

    reads    = [_join_float(join, s, "pages_read")    for s in strategies]
    writes   = [_join_float(join, s, "pages_written") for s in strategies]
    times    = [_join_float(join, s, "elapsed_ms")    for s in strategies]
    totals   = [_join_float(join, s, "total_accesses") for s in strategies]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 5))

    # stacked bar: reads + writes
    x     = np.arange(len(strategies))
    width = 0.5
    bars_r = ax1.bar(x, reads,  width, label="Lecturas",   color="#5DA5DA")
    bars_w = ax1.bar(x, writes, width, label="Escrituras", color="#FAA43A",
                     bottom=reads)
    for xi, t in zip(x, totals):
        ax1.text(xi, t + max(totals) * 0.01, f"{int(t):,}", ha="center",
                 va="bottom", fontsize=9, fontweight="bold")

    ax1.set_xticks(x)
    ax1.set_xticklabels(labels_str)
    ax1.set_ylabel("Accesos a Disco (páginas)")
    ax1.set_title("Join - Total Accesos a Disco")
    ax1.legend()
    ax1.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{int(v):,}"))
    _style_ax(ax1)

    # bar: time
    bars_t = ax2.bar(x, times, width, color=colors)
    for xi, t in zip(x, times):
        ax2.text(xi, t + max(times) * 0.01, f"{t:.1f} ms", ha="center",
                 va="bottom", fontsize=9, fontweight="bold")

    ax2.set_xticks(x)
    ax2.set_xticklabels(labels_str)
    ax2.set_ylabel("Tiempo de Ejecución (ms)")
    ax2.set_title("Join - Tiempo de Ejecución")
    _style_ax(ax2)

    fig.suptitle("Comparativa de Estrategias de Join (N=5 000 registros/tabla)",
                 fontsize=11)
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    plt.close(fig)
    print(f"  [chart] {out.name}")


# ---------------------------------------------------------------------------
# Punto de entrada
# ---------------------------------------------------------------------------

def generate_report(
    index_csv: Path = INDEX_CSV_PATH,
    join_csv: Path = JOIN_CSV_PATH,
    template_path: Path = TEMPLATE_PATH,
    output_path: Path = OUTPUT_PATH,
    charts_dir: Path = CHARTS_DIR,
) -> None:
    if not index_csv.exists() and not join_csv.exists():
        sys.exit(
            f"[ERROR] No se encontraron CSVs de benchmark en: {index_csv.parent}\n"
            "        Ejecuta primero: python benchmarking/run_tests.py"
        )
    if not template_path.exists():
        sys.exit(f"[ERROR] No se encontró la plantilla: {template_path}")

    charts_dir.mkdir(parents=True, exist_ok=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print("Cargando CSVs de benchmark ...")
    idx, join = _load(index_csv, join_csv)

    print("Generando gráficos ...")
    _chart_disk_accesses(idx, join, charts_dir / "disk_accesses.png")
    _chart_execution_time(idx, join, charts_dir / "execution_time.png")
    _chart_search_reads(idx,        charts_dir / "search_reads.png")
    _chart_range_reads(idx,         charts_dir / "range_reads.png")
    _chart_joins(join,              charts_dir / "joins.png")

    print("Rellenando plantilla ...")
    subs    = _build_subs(idx, join)
    content = _fill(template_path, subs)
    output_path.write_text(content, encoding="utf-8")

    print(f"\n[OK] Informe generado: {output_path}")
    print(f"     Gráficos en:       {charts_dir}")


if __name__ == "__main__":
    generate_report()
