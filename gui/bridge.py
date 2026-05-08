from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)

# Import guard - engine/core.py aún no está disponible
try:
    from engine.core import StorageEngine
    from parsing.sql_analyzer import QueryCompiler
    _ENGINE_AVAILABLE = True
except ImportError:
    _ENGINE_AVAILABLE = False
    StorageEngine = None
    QueryCompiler = None


# Resultado de query
@dataclass
class QueryResult:
    kind:         str
    columns:      List[str]       = field(default_factory=list)
    rows:         List[Dict]      = field(default_factory=list)
    message:      str             = ""
    telemetry:    Dict[str, Any]  = field(default_factory=dict)


# Puente SQL: compila queries y ejecuta contra el engine de storage
class QueryBridge:

    # Inicializa engine (placeholder mientras Camila lo implementa)
    def __init__(self, data_dir: str):
        self._data_dir = data_dir
        if _ENGINE_AVAILABLE:
            self._engine = StorageEngine(data_dir)
            self._compiler = QueryCompiler()
        else:
            self._engine = None
            self._compiler = None

    # TODO: Implementar luego - carga automática desde CSVs
    def _auto_seed(self) -> None:
        pass

    # TODO: Implementar luego - reinicio de engine
    def reinit(self, data_dir: str) -> None:
        raise NotImplementedError("Implementado en COMMIT 17")

    # Ejecuta SQL directo (placeholder)
    def _exec_sql_raw(self, sql: str) -> None:
        if not _ENGINE_AVAILABLE:
            return
        for node in self._compiler.compile(sql):
            self._engine.execute(node)

    # Retorna nombres de tablas (placeholder)
    def get_tables(self) -> List[str]:
        if not _ENGINE_AVAILABLE:
            return []
        return list(self._engine._catalog["tables"].keys())

    # TODO: Implementar luego - bulk-load desde CSV
    def massive_ingest(self, csv_path: str, table_name: str, **kwargs) -> int:
        raise NotImplementedError("Implementado en COMMIT 17")

    # Punto de entrada público (placeholder)
    def execute(self, sql_text: str) -> QueryResult:
        if not _ENGINE_AVAILABLE:
            return QueryResult("message", message="engine no disponible aún")
        sql = sql_text.strip()
        if not sql:
            return QueryResult("message", message="Empty query")
        return QueryResult("message", message="SQL execution placeholder")

    # TODO: Implementar luego - ejecución SQL completa
    def _exec_sql(self, sql: str) -> QueryResult:
        raise NotImplementedError("Implementado en COMMIT 17")

    # TODO: Implementar luego - renderización espacial
    def _build_spatial_result(self, node, hit_rows: List[Dict], tel: Dict) -> QueryResult:
        raise NotImplementedError("Implementado en COMMIT 17")

    # Snapshot de telemetría (placeholder)
    def _snapshot(self) -> Dict[str, Any]:
        return {"reads": 0, "writes": 0, "accesses": 0, "ms": 0}