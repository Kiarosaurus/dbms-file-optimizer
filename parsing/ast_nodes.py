from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, List, Optional


# base class para todos los nodos statement del AST
class InstructionNode:
    pass


@dataclass
class ColumnSpec:
    col_name: str
    data_type: str
    size: Optional[int] = None


@dataclass
class IndexDirective:
    index_kind: str   # BTREE | HASH | SEQUENTIAL | RTREE
    col_name: str


# nodo AST para CREATE TABLE con columnas e índices opcionales y carga CSV opcional
@dataclass
class CreateSchemaNode(InstructionNode):
    table_name: str
    column_specs: List[ColumnSpec]
    index_directives: List[IndexDirective] = field(default_factory=list)
    from_file: Optional[str] = None   # path del CSV si se usó FROM FILE


# nodo para aggregate functions COUNT SUM AVG MIN MAX con su argumento
@dataclass
class AggregateExpr:
    func_name: str   # COUNT | SUM | AVG | MIN | MAX
    argument: str


# nodo para JOIN clause con tabla derecha y columnas qualified de ambos lados
@dataclass
class JoinClause:
    right_table: str
    left_col: str    # qualified: table.column
    right_col: str   # qualified: table.column


# nodo para expresión de filtro con soporte de cadenas AND/OR recursivas
@dataclass
class FilterExpr:
    left_operand: Any    # str (column) or nested FilterExpr for AND/OR chains
    operator: str        # =  !=  <  >  <=  >=  AND  OR
    right_operand: Any   # str | int | float | FilterExpr


# nodo AST para SELECT con targets aggregations join filter group by y order by
@dataclass
class QueryNode(InstructionNode):
    targets: List[str]                        # plain columns + aggregate labels
    source: str                               # primary table
    join_target: Optional[str] = None
    join_condition: Optional[JoinClause] = None
    filter: Optional[FilterExpr] = None
    group_by_cols: List[str] = field(default_factory=list)
    aggregations: List[AggregateExpr] = field(default_factory=list)
    order_by_cols: List[str] = field(default_factory=list)


# envuelve un valor literal escalar para la lista de VALUES en INSERT
@dataclass
class ValueLiteral:
    raw_value: Any   # int | float | str


# nodo AST para INSERT INTO con tabla y lista de value literals
@dataclass
class InsertNode(InstructionNode):
    table_name: str
    value_list: List[ValueLiteral]


# nodo AST para DELETE FROM con filtro WHERE opcional
@dataclass
class DeleteNode(InstructionNode):
    table_name: str
    filter: Optional[FilterExpr] = None


# nodo para predicado espacial WHERE (col_x, col_y) IN (POINT(cx, cy), RADIUS r | K k)
@dataclass
class SpatialFilterExpr:
    columns: List[str]    # [x_col] single-col o [x_col, y_col] tuple syntax
    cx:      float
    cy:      float
    radius:  float = 0.0   # >0 para radius mode
    k:       int   = 0     # >0 para knn mode
