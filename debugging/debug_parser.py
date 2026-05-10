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


def test_parse_join():
    nodes = _compile("SELECT * FROM A JOIN B ON A.id = B.fk")
    node = nodes[0]
    assert node.join_target == "B"
    assert node.join_condition is not None
    assert isinstance(node.join_condition, JoinClause)
    assert node.join_condition.left_col == "A.id"
    assert node.join_condition.right_col == "B.fk"
    print("  [OK] test_parse_join")


def test_parse_between():
    nodes = _compile("SELECT * FROM T WHERE score BETWEEN 50 AND 100")
    node = nodes[0]
    filt = node.filter
    # desazucarado como score >= 50 AND score <= 100
    assert filt.operator == "AND"
    assert isinstance(filt.left_operand, FilterExpr)
    assert filt.left_operand.operator == ">="
    assert filt.right_operand.operator == "<="
    print("  [OK] test_parse_between")


def test_parse_spatial_radius():
    nodes = _compile("SELECT * FROM Ciudades WHERE x IN (POINT(10.0, 20.0), RADIUS 5)")
    node = nodes[0]
    sf = node.filter
    assert isinstance(sf, SpatialFilterExpr)
    assert abs(sf.cx - 10.0) < 0.001
    assert abs(sf.cy - 20.0) < 0.001
    assert abs(sf.radius - 5.0) < 0.001
    assert sf.k == 0
    print("  [OK] test_parse_spatial_radius")


def test_parse_spatial_knn():
    nodes = _compile("SELECT * FROM Ciudades WHERE (lat, lon) IN (POINT(-12.0, -77.0), K 3)")
    node = nodes[0]
    sf = node.filter
    assert isinstance(sf, SpatialFilterExpr)
    assert sf.k == 3
    assert len(sf.columns) == 2
    assert sf.columns[0] == "lat"
    assert sf.columns[1] == "lon"
    print("  [OK] test_parse_spatial_knn")


def test_parse_spatial_negative_coords():
    nodes = _compile("SELECT * FROM T WHERE x IN (POINT(-73.9, 40.7), RADIUS 10)")
    sf = nodes[0].filter
    assert abs(sf.cx - (-73.9)) < 0.001
    assert abs(sf.cy - 40.7) < 0.001
    print("  [OK] test_parse_spatial_negative_coords")


def test_parse_delete_with_filter():
    nodes = _compile("DELETE FROM T WHERE id = 99")
    node = nodes[0]
    assert isinstance(node, DeleteNode)
    assert node.table_name == "T"
    assert node.filter is not None
    assert node.filter.right_operand == 99
    print("  [OK] test_parse_delete_with_filter")


def test_parse_group_by():
    nodes = _compile("SELECT campo, COUNT(campo) FROM T GROUP BY campo")
    node = nodes[0]
    assert len(node.group_by_cols) == 1
    assert node.group_by_cols[0] == "campo"
    assert len(node.aggregations) == 1
    assert node.aggregations[0].func_name == "COUNT"
    print("  [OK] test_parse_group_by")


if __name__ == "__main__":
    print("=== debug_parser.py ===")
    test_parse_create_basic()
    test_parse_create_with_index()
    test_parse_create_rtree()
    test_parse_insert()
    test_parse_multiple_statements()
    test_parse_select_star()
    test_parse_select_where_eq()
    test_parse_select_where_and()
    test_parse_select_aggregate()
    test_parse_join()
    test_parse_between()
    test_parse_spatial_radius()
    test_parse_spatial_knn()
    test_parse_spatial_negative_coords()
    test_parse_delete_with_filter()
    test_parse_group_by()
    print("Todos los tests pasaron.")
