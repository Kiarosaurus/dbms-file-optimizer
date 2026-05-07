import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from parsing.sql_analyzer import QueryCompiler, ParseError
from parsing.ast_nodes import (
    CreateSchemaNode, QueryNode, InsertNode, DeleteNode,
    FilterExpr, SpatialFilterExpr, JoinClause,
)


def _compile(sql):
    return QueryCompiler().compile(sql)


def test_parse_create_basic():
    nodes = _compile("CREATE TABLE T (id INTEGER, nombre VARCHAR(30))")
    assert len(nodes) == 1
    node = nodes[0]
    assert isinstance(node, CreateSchemaNode)
    assert node.table_name == "T"
    assert len(node.column_specs) == 2
    assert node.column_specs[0].col_name == "id"
    assert node.column_specs[1].col_name == "nombre"
    print("  [OK] test_parse_create_basic")


def test_parse_create_with_index():
    nodes = _compile("CREATE TABLE T (id INTEGER) INDEX (BTREE id)")
    node = nodes[0]
    assert len(node.index_directives) == 1
    assert node.index_directives[0].index_kind == "BTREE"
    assert node.index_directives[0].col_name == "id"
    print("  [OK] test_parse_create_with_index")


def test_parse_create_rtree():
    nodes = _compile("CREATE TABLE Ciudades (id INTEGER, x FLOAT, y FLOAT) INDEX (RTREE x)")
    node = nodes[0]
    assert node.index_directives[0].index_kind == "RTREE"
    assert node.index_directives[0].col_name == "x"
    print("  [OK] test_parse_create_rtree")


def test_parse_insert():
    nodes = _compile("INSERT INTO T VALUES (1, 'Ana', 9.5)")
    assert len(nodes) == 1
    node = nodes[0]
    assert isinstance(node, InsertNode)
    assert node.table_name == "T"
    assert len(node.value_list) == 3
    assert node.value_list[0].raw_value == 1
    assert node.value_list[1].raw_value == "Ana"
    assert abs(node.value_list[2].raw_value - 9.5) < 0.001
    print("  [OK] test_parse_insert")


def test_parse_multiple_statements():
    sql = "CREATE TABLE A (id INTEGER); CREATE TABLE B (id INTEGER)"
    nodes = _compile(sql)
    assert len(nodes) == 2
    assert all(isinstance(n, CreateSchemaNode) for n in nodes)
    print("  [OK] test_parse_multiple_statements")


# ─── Commit H+09 ──────────────────────────────────────────────────────────────

def test_parse_select_star():
    nodes = _compile("SELECT * FROM Estudiantes")
    node = nodes[0]
    assert isinstance(node, QueryNode)
    assert node.targets == ["*"]
    assert node.source == "Estudiantes"
    assert node.filter is None
    print("  [OK] test_parse_select_star")


def test_parse_select_where_eq():
    nodes = _compile("SELECT nombre FROM T WHERE id = 42")
    node = nodes[0]
    assert node.filter is not None
    assert isinstance(node.filter, FilterExpr)
    assert node.filter.operator == "="
    assert node.filter.right_operand == 42
    print("  [OK] test_parse_select_where_eq")


def test_parse_select_where_and():
    nodes = _compile("SELECT * FROM T WHERE a > 1 AND b < 10")
    node = nodes[0]
    filt = node.filter
    assert filt.operator == "AND"
    assert isinstance(filt.left_operand, FilterExpr)
    assert isinstance(filt.right_operand, FilterExpr)
    print("  [OK] test_parse_select_where_and")


def test_parse_select_aggregate():
    nodes = _compile("SELECT COUNT(id) FROM T")
    node = nodes[0]
    assert len(node.aggregations) == 1
    assert node.aggregations[0].func_name == "COUNT"
    assert node.aggregations[0].argument == "id"
    print("  [OK] test_parse_select_aggregate")