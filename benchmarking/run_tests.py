#!/usr/bin/env python3
from __future__ import annotations

import concurrent.futures
import csv
import os
import random
import shutil
import string
import sys
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.concurrency import init_concurrency
from core.metrics import Telemetry
from core.schema import FieldType, RecordSerializer, SchemaField
from engine.core import StorageEngine, seq_full_scan
from indexing.bplus_tree import BPlusTreeIndex
from indexing.extendible_hash import ExtendibleHashIndex
from indexing.sequential_file import SequentialIndex
from parsing.sql_analyzer import QueryCompiler


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATA_DIR        = os.path.join(_ROOT, "data")
CSV_AIRPORTS    = os.path.join(DATA_DIR, "airports_3376.csv")
CSV_DISASTERS   = os.path.join(DATA_DIR, "global_natural_disasters_2000_2025_46419.csv")
CSV_SCHOOLS     = os.path.join(DATA_DIR, "us-public-schools_101884.csv")

SEARCH_K        = 20       # keys averaged
RANGE_PCT       = 10       # % of N spanning del range query
JOIN_N          = 5_000    # rows por tabla en join benchmark
RESULTS_DIR     = os.path.join(_ROOT, "results")
BENCH_TIMEOUT_S = 600      # fallback timeout para join benchmarks
BENCH_TIMEOUT   = {        # per-index timeouts (segundos)
    "_bench_sequential": 1200,
    "_bench_btree":       300,
    "_bench_exthash":     120,
}
BENCH_WORKSPACE = os.path.join(_ROOT, "workspaces", "bench_run")


# ---------------------------------------------------------------------------
# Real dataset
# ---------------------------------------------------------------------------

# retorna serializer de 4 columnas: ID INTEGER(4) + Name VARCHAR(20) + Lat FLOAT(4) + Lon FLOAT(4) = 32 B
def _ser_4col() -> RecordSerializer:
    return RecordSerializer([
        SchemaField("ID",   FieldType.INTEGER),
        SchemaField("Name", FieldType.STRING, str_size=20),
        SchemaField("Lat",  FieldType.FLOAT),
        SchemaField("Lon",  FieldType.FLOAT),
    ])


def _load_csv_records(
    csv_path: str,
    name_col: str,
    lat_col: str,
    lon_col: str,
    delimiter: str = ",",
    encoding: str = "utf-8",
) -> List[Dict[str, Any]]:
    """Lee un CSV real y retorna lista de dicts con esquema {ID, Name, Lat, Lon}."""
    records: List[Dict[str, Any]] = []
    with open(csv_path, newline="", encoding=encoding, errors="replace") as fh:
        reader = csv.DictReader(fh, delimiter=delimiter)
        for i, row in enumerate(reader, start=1):
            try:
                lat = float(row.get(lat_col, "") or 0)
                lon = float(row.get(lon_col, "") or 0)
            except (ValueError, TypeError):
                continue
            name = (row.get(name_col, "") or "")[:20]
            records.append({"ID": i, "Name": name, "Lat": lat, "Lon": lon})
    return records


def load_airports() -> List[Dict[str, Any]]:
    """Carga airports_3376.csv → N ≈ 3 376 registros."""
    return _load_csv_records(CSV_AIRPORTS, name_col="name", lat_col="latitude", lon_col="longitude")


def load_disasters() -> List[Dict[str, Any]]:
    """Carga global_natural_disasters_2000_2025_46419.csv → N ≈ 46 419 registros."""
    return _load_csv_records(
        CSV_DISASTERS, name_col="disaster_type", lat_col="latitude", lon_col="longitude"
    )


def load_schools() -> List[Dict[str, Any]]:
    """Carga us-public-schools_101884.csv (delimitador ';') → N ≈ 101 884 registros."""
    return _load_csv_records(
        CSV_SCHOOLS, name_col="NAME", lat_col="LATITUDE", lon_col="LONGITUDE",
        delimiter=";", encoding="utf-8-sig",
    )


# helper para join benchmarks (sigue usando datos sintéticos por reproducibilidad)
def _rnd_name(rng: random.Random, length: int = 14) -> str:
    return "".join(rng.choices(string.ascii_lowercase, k=length))


# ---------------------------------------------------------------------------
# Telemetry helpers
# ---------------------------------------------------------------------------

# resetea contadores de telemetry antes de cada medición
def _reset() -> None:
    Telemetry().reset()


# Snapshot de pages_read y pages_written del Telemetry
def _snap() -> Tuple[int, int]:
    t = Telemetry()
    return t.pages_read, t.pages_written


# ---------------------------------------------------------------------------
# Timeout helpers  (non-blocking — prints warning and continues on slow tests)
# ---------------------------------------------------------------------------

# lanza fn(records, tmpdir) en un thread con timeout, usa subdir uuid en bench_workspace
def _run_with_timeout(fn, records: List[Dict], label: str = "") -> Optional[Dict]:
    td = os.path.join(BENCH_WORKSPACE, f"idx_{uuid.uuid4().hex[:8]}")
    os.makedirs(td, exist_ok=True)
    timeout = BENCH_TIMEOUT.get(fn.__name__, BENCH_TIMEOUT_S)

    def _worker():
        return fn(records, td)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as exe:
        fut = exe.submit(_worker)
        try:
            return fut.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            print(f"\n  [AVISO] {label}: superó {timeout}s — omitiendo, continuando con las demás pruebas.")
            return None
        except Exception as exc:
            print(f"\n  [ERROR] {label}: {exc!r} — omitiendo, continuando con las demás pruebas.")
            return None


# lanza fn(tmpdir) en un thread con timeout, variante para join benchmarks
def _run_join_with_timeout(fn, label: str = "") -> Optional[Dict]:
    td = os.path.join(BENCH_WORKSPACE, f"join_{uuid.uuid4().hex[:8]}")
    os.makedirs(td, exist_ok=True)

    def _worker():
        return fn(td)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as exe:
        fut = exe.submit(_worker)
        try:
            return fut.result(timeout=BENCH_TIMEOUT_S)
        except concurrent.futures.TimeoutError:
            print(f"\n  [AVISO] {label}: superó {BENCH_TIMEOUT_S}s — omitiendo, continuando con las demás pruebas.")
            return None
        except Exception as exc:
            print(f"\n  [ERROR] {label}: {exc!r} — omitiendo, continuando con las demás pruebas.")
            return None


# ---------------------------------------------------------------------------
# Index benchmarks
# ---------------------------------------------------------------------------

# benchmark de inserción, búsqueda puntual y range search para SequentialIndex
def _bench_sequential(records: List[Dict], tmpdir: str) -> Dict[str, Any]:
    n   = len(records)
    ser = _ser_4col()
    idx = SequentialIndex(
        main_path     = os.path.join(tmpdir, "seq_main.bin"),
        overflow_path = os.path.join(tmpdir, "seq_ovfl.bin"),
        serializer    = ser,
        K_threshold   = max(50, n // 20),
    )

    # INSERT
    _reset()
    t0 = time.perf_counter()
    for rec in records:
        idx.add(rec)
    ins_ms = (time.perf_counter() - t0) * 1_000
    ins_r, ins_w = _snap()

    # flush overflow
    idx.reorganize()

    # POINT SEARCH (SEARCH_K keys, averaged)
    rng  = random.Random(99)
    keys = rng.sample(range(1, n + 1), SEARCH_K)
    _reset()
    t0 = time.perf_counter()
    for k in keys:
        idx.search(k)
    srch_ms = (time.perf_counter() - t0) * 1_000 / SEARCH_K
    srch_r, _ = _snap()
    srch_r_avg = srch_r / SEARCH_K

    # RANGE SEARCH (~RANGE_PCT % de N via full scan + filter)
    lo = n // 5
    hi = lo + max(1, n * RANGE_PCT // 100)
    _reset()
    t0 = time.perf_counter()
    hits = idx.range_search(lo, hi)
    rng_ms = (time.perf_counter() - t0) * 1_000
    rng_r, _ = _snap()

    return {
        "index_type":         "Sequential",
        "N":                  n,
        "record_size_B":      ser.record_size,
        "rpp":                idx.records_per_page,
        "insert_writes":      ins_w,
        "insert_reads":       ins_r,
        "insert_ms":          round(ins_ms, 2),
        "search_avg_reads":   round(srch_r_avg, 2),
        "search_avg_ms":      round(srch_ms, 4),
        "range_lo":           lo,
        "range_hi":           hi,
        "range_hits":         len(hits),
        "range_reads":        rng_r,
        "range_ms":           round(rng_ms, 2),
    }


# Benchmark de inserción, búsqueda puntual y range search para BPlusTreeIndex
def _bench_btree(records: List[Dict], tmpdir: str) -> Dict[str, Any]:
    n   = len(records)
    ser = _ser_4col()
    idx = BPlusTreeIndex(
        file_path  = os.path.join(tmpdir, "btree.bin"),
        serializer = ser,
        key_field  = "ID",
    )

    # INSERT
    _reset()
    t0 = time.perf_counter()
    for rec in records:
        idx.add(rec)
    ins_ms = (time.perf_counter() - t0) * 1_000
    ins_r, ins_w = _snap()

    # POINT SEARCH
    rng  = random.Random(99)
    keys = rng.sample(range(1, n + 1), SEARCH_K)
    _reset()
    t0 = time.perf_counter()
    for k in keys:
        idx.search(k)
    srch_ms = (time.perf_counter() - t0) * 1_000 / SEARCH_K
    srch_r, _ = _snap()
    srch_r_avg = srch_r / SEARCH_K

    # RANGE SEARCH (leaf-chain traversal)
    lo = n // 5
    hi = lo + max(1, n * RANGE_PCT // 100)
    _reset()
    t0 = time.perf_counter()
    hits = idx.range_search(lo, hi)
    rng_ms = (time.perf_counter() - t0) * 1_000
    rng_r, _ = _snap()

    _, total_nodes = idx._read_meta()
    return {
        "index_type":         "BPlusTree",
        "N":                  n,
        "record_size_B":      ser.record_size,
        "rpp":                idx.leaf_cap,
        "tree_nodes":         total_nodes,
        "insert_writes":      ins_w,
        "insert_reads":       ins_r,
        "insert_ms":          round(ins_ms, 2),
        "search_avg_reads":   round(srch_r_avg, 2),
        "search_avg_ms":      round(srch_ms, 4),
        "range_lo":           lo,
        "range_hi":           hi,
        "range_hits":         len(hits),
        "range_reads":        rng_r,
        "range_ms":           round(rng_ms, 2),
    }


# benchmark de inserción y búsqueda puntual para ExtendibleHashIndex, sin range search
def _bench_exthash(records: List[Dict], tmpdir: str) -> Dict[str, Any]:
    n   = len(records)
    ser = _ser_4col()
    idx = ExtendibleHashIndex(
        file_path  = os.path.join(tmpdir, "hash.bin"),
        serializer = ser,
        key_field  = "ID",
    )

    # INSERT
    _reset()
    t0 = time.perf_counter()
    for rec in records:
        idx.add(rec)
    ins_ms = (time.perf_counter() - t0) * 1_000
    ins_r, ins_w = _snap()

    # POINT SEARCH
    rng  = random.Random(99)
    keys = rng.sample(range(1, n + 1), SEARCH_K)
    _reset()
    t0 = time.perf_counter()
    for k in keys:
        idx.search(k)
    srch_ms = (time.perf_counter() - t0) * 1_000 / SEARCH_K
    srch_r, _ = _snap()
    srch_r_avg = srch_r / SEARCH_K

    return {
        "index_type":         "ExtHash",
        "N":                  n,
        "record_size_B":      ser.record_size,
        "rpp":                idx.bucket_cap,
        "insert_writes":      ins_w,
        "insert_reads":       ins_r,
        "insert_ms":          round(ins_ms, 2),
        "search_avg_reads":   round(srch_r_avg, 2),
        "search_avg_ms":      round(srch_ms, 4),
        "range_lo":           None,
        "range_hi":           None,
        "range_hits":         None,
        "range_reads":        None,
        "range_ms":           None,
    }


# Carga los tres datasets reales y ejecuta los tres índices sobre cada uno
def run_index_benchmarks() -> List[Dict]:
    datasets = [
        ("airports",   CSV_AIRPORTS,   load_airports),
        ("disasters",  CSV_DISASTERS,  load_disasters),
        ("schools",    CSV_SCHOOLS,    load_schools),
    ]
    results = []
    for ds_name, csv_path, loader_fn in datasets:
        if not os.path.exists(csv_path):
            print(f"  [SKIP] {ds_name}: archivo no encontrado — {csv_path}")
            continue
        print(f"  Cargando {ds_name} ...")
        records = loader_fn()
        n = len(records)
        print(f"  Dataset: {ds_name}  N = {n:,}")
        for fn in (_bench_sequential, _bench_btree, _bench_exthash):
            label = f"{fn.__name__} {ds_name} N={n:,}"
            r = _run_with_timeout(fn, records, label=label)
            if r is None:
                continue
            results.append(r)
            rng_disp = str(r["range_reads"]) if r["range_reads"] is not None else "N/A"
            print(f"    {r['index_type']:<12} ins_writes={r['insert_writes']:>6}  "
                  f"srch_reads={r['search_avg_reads']:>5.2f}  "
                  f"range_reads={rng_disp}")
    return results


# ---------------------------------------------------------------------------
# Join benchmarks
# ---------------------------------------------------------------------------

# inserta filas directo al index sin pasar por el SQL parser, reduce overhead
def _bulk_insert(engine: StorageEngine, table: str, rows: List[Dict]) -> None:
    idx = engine._get_index(table)
    for row in rows:
        idx.add(row)


# mide el join MERGE sobre tablas MergeA y MergeB ya ordenadas por PK
def _bench_merge_join(tmpdir: str) -> Dict[str, Any]:
    init_concurrency(os.path.join(tmpdir, "journal.log"))
    engine   = StorageEngine(tmpdir)
    compiler = QueryCompiler()

    for sql in [
        "CREATE TABLE MergeA (ID INTEGER, Name VARCHAR(20))",
        "CREATE TABLE MergeB (ID INTEGER, Score FLOAT)",
    ]:
        engine.execute(compiler.compile(sql)[0])

    rng = random.Random(7)
    _bulk_insert(engine, "MergeA",
                 [{"ID": i, "Name": _rnd_name(rng, 14)} for i in range(1, JOIN_N + 1)])
    _bulk_insert(engine, "MergeB",
                 [{"ID": i, "Score": round(rng.uniform(0, 100), 2)} for i in range(1, JOIN_N + 1)])

    # Flush overflow so both tables are fully sorted before the join measurement
    engine._get_index("MergeA").reorganize()
    engine._get_index("MergeB").reorganize()

    sql = ("SELECT MergeA.ID, MergeA.Name, MergeB.Score "
           "FROM MergeA JOIN MergeB ON MergeA.ID = MergeB.ID")
    _reset()
    t0 = time.perf_counter()
    result_rows = engine.execute(compiler.compile(sql)[0])
    join_ms = (time.perf_counter() - t0) * 1_000
    r, w = _snap()

    return {
        "join_strategy":  "MERGE",
        "N_per_table":    JOIN_N,
        "result_rows":    len(result_rows),
        "pages_read":     r,
        "pages_written":  w,
        "total_accesses": r + w,
        "elapsed_ms":     round(join_ms, 2),
    }


# mide el join EXTERNAL_SORT_MERGE sobre columna no-PK ExtA.DeptID = ExtB.ID
def _bench_ext_sort_merge_join(tmpdir: str) -> Dict[str, Any]:
    init_concurrency(os.path.join(tmpdir, "journal.log"))
    engine   = StorageEngine(tmpdir)
    compiler = QueryCompiler()

    for sql in [
        "CREATE TABLE ExtA (ID INTEGER, DeptID INTEGER, Name VARCHAR(20))",
        "CREATE TABLE ExtB (ID INTEGER, DName VARCHAR(20))",
    ]:
        engine.execute(compiler.compile(sql)[0])

    rng     = random.Random(13)
    n_depts = 50
    _bulk_insert(engine, "ExtA",
                 [{"ID": i, "DeptID": (i % n_depts) + 1, "Name": _rnd_name(rng, 14)}
                  for i in range(1, JOIN_N + 1)])
    _bulk_insert(engine, "ExtB",
                 [{"ID": d, "DName": _rnd_name(rng, 10)}
                  for d in range(1, n_depts + 1)])

    sql = ("SELECT ExtA.ID, ExtA.DeptID, ExtB.DName "
           "FROM ExtA JOIN ExtB ON ExtA.DeptID = ExtB.ID")
    _reset()
    t0 = time.perf_counter()
    result_rows = engine.execute(compiler.compile(sql)[0])
    join_ms = (time.perf_counter() - t0) * 1_000
    r, w = _snap()

    return {
        "join_strategy":  "EXTERNAL_SORT_MERGE",
        "N_per_table":    JOIN_N,
        "result_rows":    len(result_rows),
        "pages_read":     r,
        "pages_written":  w,
        "total_accesses": r + w,
        "elapsed_ms":     round(join_ms, 2),
    }


# ejecuta ambos join benchmarks con timeout y acumula métricas
def run_join_benchmarks() -> List[Dict]:
    results = []
    for label, fn in [
        ("MERGE", _bench_merge_join),
        ("EXTERNAL_SORT_MERGE", _bench_ext_sort_merge_join),
    ]:
        print(f"  {label} (N={JOIN_N:,}/table)")
        r = _run_join_with_timeout(fn, label=f"{label} N={JOIN_N:,}")
        if r is None:
            continue
        results.append(r)
        print(f"    pages_read={r['pages_read']}  pages_written={r['pages_written']}  "
              f"result_rows={r['result_rows']:,}  {r['elapsed_ms']:.1f} ms")
    return results


# ---------------------------------------------------------------------------
# Markdown table printer
# ---------------------------------------------------------------------------

# imprime tabla markdown con anchos de columna dinámicos según contenido
def _md_table(headers: List[str], rows: List[List[Any]]) -> str:
    col_w = [len(h) for h in headers]
    str_rows = [
        [str(v) if v is not None else "—" for v in row]
        for row in rows
    ]
    for row in str_rows:
        for i, cell in enumerate(row):
            col_w[i] = max(col_w[i], len(cell))

    def fmt(cells: List[str]) -> str:
        return "| " + " | ".join(c.ljust(col_w[i]) for i, c in enumerate(cells)) + " |"

    sep = "|-" + "-|-".join("-" * w for w in col_w) + "-|"
    return "\n".join([fmt(headers), sep] + [fmt(r) for r in str_rows])


# imprime tablas markdown de insert cost, point search y range search para índices
def print_index_tables(results: List[Dict]) -> None:
    print("\n### Insert Cost")
    print(_md_table(
        ["Index", "N", "Rec(B)", "RPP", "Ins_Writes", "Ins_Reads", "Ins_ms"],
        [[r["index_type"], f"{r['N']:,}", r["record_size_B"], r["rpp"],
          r["insert_writes"], r["insert_reads"], r["insert_ms"]]
         for r in results],
    ))

    print("\n### Point Search — avg over 20 random keys")
    print(_md_table(
        ["Index", "N", "Avg_Reads", "Avg_ms"],
        [[r["index_type"], f"{r['N']:,}",
          f"{r['search_avg_reads']:.2f}", f"{r['search_avg_ms']:.4f}"]
         for r in results],
    ))

    range_rows = [r for r in results if r["range_reads"] is not None]
    if range_rows:
        print(f"\n### Range Search — ~{RANGE_PCT}% of N  (Sequential & B+Tree only)")
        print(_md_table(
            ["Index", "N", "Lo", "Hi", "Hits", "Reads", "ms"],
            [[r["index_type"], f"{r['N']:,}",
              r["range_lo"], r["range_hi"],
              r["range_hits"], r["range_reads"], r["range_ms"]]
             for r in range_rows],
        ))


# imprime tabla markdown de join strategies e indica ahorro de page accesses
def print_join_table(results: List[Dict]) -> None:
    print("\n### Join Strategy Comparison  (N=5,000 per table)")
    print(_md_table(
        ["Strategy", "N/table", "Result_rows", "Pages_R", "Pages_W", "Total_Acc", "ms"],
        [[r["join_strategy"], f"{r['N_per_table']:,}", f"{r['result_rows']:,}",
          r["pages_read"], r["pages_written"], r["total_accesses"], r["elapsed_ms"]]
         for r in results],
    ))

    merge = next((r for r in results if r["join_strategy"] == "MERGE"), None)
    ext   = next((r for r in results if r["join_strategy"] == "EXTERNAL_SORT_MERGE"), None)
    if merge and ext:
        saved = ext["total_accesses"] - merge["total_accesses"]
        if saved > 0:
            desc = f"MERGE saves {saved} page accesses over EXTERNAL_SORT_MERGE"
        elif saved < 0:
            desc = f"EXTERNAL_SORT_MERGE saves {-saved} page accesses over MERGE"
        else:
            desc = "Both strategies use equal page accesses"
        print(f"\n> Page savings: {desc}")


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------

# exporta índices a index_metrics.csv y joins a join_metrics.csv — sin columnas vacías
def export_csvs(index_rows: List[Dict], join_rows: List[Dict]) -> tuple:
    os.makedirs(RESULTS_DIR, exist_ok=True)
    idx_path  = os.path.join(RESULTS_DIR, "index_metrics.csv")
    join_path = os.path.join(RESULTS_DIR, "join_metrics.csv")

    if index_rows:
        idx_fields = [
            "index_type", "N", "record_size_B", "rpp",
            "insert_writes", "insert_reads", "insert_ms",
            "search_avg_reads", "search_avg_ms",
            "range_reads", "range_hits", "range_ms",
        ]
        idx_out = []
        for r in index_rows:
            idx_out.append({
                "index_type":      r["index_type"],
                "N":               r["N"],
                "record_size_B":   r["record_size_B"],
                "rpp":             r["rpp"],
                "insert_writes":   r["insert_writes"],
                "insert_reads":    r["insert_reads"],
                "insert_ms":       r["insert_ms"],
                "search_avg_reads": r["search_avg_reads"],
                "search_avg_ms":   r["search_avg_ms"],
                "range_reads":     r["range_reads"] if r["range_reads"] is not None else "",
                "range_hits":      r["range_hits"]  if r["range_hits"]  is not None else "",
                "range_ms":        r["range_ms"]    if r["range_ms"]    is not None else "",
            })
        with open(idx_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=idx_fields)
            writer.writeheader()
            writer.writerows(idx_out)
        print(f"CSV guardado -> {idx_path}")
    else:
        print("[AVISO] Sin datos de índices — index_metrics.csv no generado.")

    if join_rows:
        join_fields = [
            "join_strategy", "N_per_table", "result_rows",
            "pages_read", "pages_written", "total_accesses", "elapsed_ms",
        ]
        join_out = []
        for r in join_rows:
            join_out.append({
                "join_strategy":  r["join_strategy"],
                "N_per_table":    r["N_per_table"],
                "result_rows":    r["result_rows"],
                "pages_read":     r["pages_read"],
                "pages_written":  r["pages_written"],
                "total_accesses": r["total_accesses"],
                "elapsed_ms":     r["elapsed_ms"],
            })
        with open(join_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=join_fields)
            writer.writeheader()
            writer.writerows(join_out)
        print(f"CSV guardado -> {join_path}")
    else:
        print("[AVISO] Sin datos de joins — join_metrics.csv no generado.")

    return idx_path, join_path


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

# orquesta todos los benchmarks, imprime tablas y limpia workspace al final
def main() -> None:
    os.makedirs(BENCH_WORKSPACE, exist_ok=True)
    print("=" * 64)
    print("JupiterDB  —  Performance Benchmarks")
    print("=" * 64)
    print(f"Workspace: {BENCH_WORKSPACE}")

    try:
        print("\n[1/2] INDEX BENCHMARKS")
        index_rows = run_index_benchmarks()

        print("\n[2/2] JOIN BENCHMARKS")
        join_rows = run_join_benchmarks()

        print("\n" + "=" * 64)
        print("RESULTS")
        print("=" * 64)
        print("\n## Index Benchmarks")
        print_index_tables(index_rows)
        print("\n## Join Benchmarks")
        print_join_table(join_rows)

        print()
        export_csvs(index_rows, join_rows)
    finally:
        shutil.rmtree(BENCH_WORKSPACE, ignore_errors=True)
        print(f"\nWorkspace limpiado: {BENCH_WORKSPACE}")


if __name__ == "__main__":
    main()
