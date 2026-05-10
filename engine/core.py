from __future__ import annotations

import json
import os
from typing import Any, Dict, Iterator, List, Optional, Tuple

from core.schema import FieldType, RecordSerializer, SchemaField
from core.storage import PAGE_SIZE
from indexing.bplus_tree import (
    BPlusTreeIndex,
    LEAF_HDR_SIZE,
    NO_NEXT,
    NODE_LEAF,
)
from indexing.sequential_file import SequentialIndex
from indexing.extendible_hash import ExtendibleHashIndex
from indexing.rtree import RTreeIndex
from parsing.ast_nodes import (
    AggregateExpr,
    CreateSchemaNode,
    DeleteNode,
    FilterExpr,
    InstructionNode,
    InsertNode,
    JoinClause,
    QueryNode,
    SpatialFilterExpr,
)

from engine.operations import hash_join, merge_join, nested_loop_join
from engine.external_sort import ExternalSorter
from core.concurrency import atomic_transaction, table_lock
_EXTERNAL_SORT_THRESHOLD = 500

_TYPE_MAP = {
    "INTEGER": "INTEGER", "INT": "INTEGER",
    "BIGINT":  "BIGINT",
    "FLOAT":   "FLOAT",   "REAL": "FLOAT", "DOUBLE": "FLOAT",
    "STRING":  "STRING",  "VARCHAR": "STRING", "TEXT": "STRING", "CHAR": "STRING",
}


# construye un RecordSerializer desde la lista de fields del catalog
def _build_serializer(fields_meta: List[Dict]) -> RecordSerializer:
    schema_fields = []
    for f in fields_meta:
        ft = FieldType[f["type"]]
        sf = SchemaField(name=f["name"], field_type=ft, str_size=f.get("str_size"))
        schema_fields.append(sf)
    return RecordSerializer(schema_fields)


# convierte los column_specs del AST a la lista de dicts que guarda el catalog
def _fields_from_create(node: CreateSchemaNode) -> List[Dict]:
    result = []
    for col in node.column_specs:
        ft = _TYPE_MAP.get(col.data_type.upper(), "STRING")
        entry: Dict[str, Any] = {"name": col.col_name, "type": ft}
        if ft == "STRING":
            entry["str_size"] = col.size or 50
        result.append(entry)
    return result

