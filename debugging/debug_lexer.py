"""
debug_lexer.py — pruebas del SQLScanner (lexer) y nodos AST.

Commit relacionado:
  H+00 (Camila #02): todos los tests de este archivo
"""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from parsing.lexer import SQLScanner, ScanError, TokenKind
from parsing.ast_nodes import (
    CreateSchemaNode, ColumnSpec, IndexDirective,
    QueryNode, AggregateExpr, JoinClause, FilterExpr,
    InsertNode, DeleteNode, ValueLiteral, SpatialFilterExpr,
)


def _tokenize(sql):
    return SQLScanner(sql).tokenize()


def _kinds(sql):
    return [t.kind for t in _tokenize(sql) if t.kind != TokenKind.EOF]


def test_tokenize_create_table():
    kinds = _kinds("CREATE TABLE Alumnos (ID INTEGER, Nombre VARCHAR(20))")
    assert TokenKind.KW_CREATE in kinds
    assert TokenKind.KW_TABLE in kinds
    assert TokenKind.IDENTIFIER in kinds
    print("  [OK] test_tokenize_create_table")


def test_tokenize_select_where():
    kinds = _kinds("SELECT * FROM T WHERE id = 5")
    assert TokenKind.KW_SELECT in kinds
    assert TokenKind.PUNCT_STAR in kinds
    assert TokenKind.KW_WHERE in kinds
    assert TokenKind.OP_EQ in kinds
    print("  [OK] test_tokenize_select_where")


def test_tokenize_negative_number():
    tokens = _tokenize("WHERE lon = -73.9")
    kinds = [t.kind for t in tokens]
    assert TokenKind.OP_MINUS in kinds
    assert TokenKind.LIT_FLOAT in kinds
    print("  [OK] test_tokenize_negative_number")


def test_tokenize_string_literal():
    tokens = _tokenize("VALUES (1, 'Juan')")
    string_tokens = [t for t in tokens if t.kind == TokenKind.LIT_STRING]
    assert len(string_tokens) == 1
    assert string_tokens[0].lexeme == "Juan"
    print("  [OK] test_tokenize_string_literal")


def test_tokenize_keywords_case_insensitive():
    kinds = _kinds("select * from T where id = 1")
    assert TokenKind.KW_SELECT in kinds
    assert TokenKind.KW_FROM in kinds
    assert TokenKind.KW_WHERE in kinds
    print("  [OK] test_tokenize_keywords_case_insensitive")


def test_scan_error_unknown_char():
    try:
        _tokenize("SELECT @ FROM T")
        assert False, "Debia lanzar ScanError"
    except ScanError:
        pass
    print("  [OK] test_scan_error_unknown_char")


def test_tokenize_spatial_keywords():
    kinds = _kinds("WHERE x IN (POINT(1.0, 2.0), RADIUS 5)")
    assert TokenKind.KW_IN in kinds
    assert TokenKind.KW_POINT in kinds
    assert TokenKind.KW_RADIUS in kinds
    print("  [OK] test_tokenize_spatial_keywords")


def test_tokenize_comparison_operators():
    sql = "a != b AND c >= d AND e <= f"
    kinds = _kinds(sql)
    assert TokenKind.OP_NEQ in kinds
    assert TokenKind.OP_GTE in kinds
    assert TokenKind.OP_LTE in kinds
    print("  [OK] test_tokenize_comparison_operators")


def test_all_ast_node_constructors():
    # CreateSchemaNode
    node1 = CreateSchemaNode(
        table_name="T",
        column_specs=[ColumnSpec("id", "INTEGER")],
        index_directives=[IndexDirective("BTREE", "id")],
    )
    assert node1.table_name == "T"

    # QueryNode
    node2 = QueryNode(targets=["*"], source="T")
    assert node2.source == "T"
    assert node2.join_target is None

    # InsertNode
    node3 = InsertNode(table_name="T", value_list=[ValueLiteral(1)])
    assert node3.value_list[0].raw_value == 1

    # DeleteNode
    node4 = DeleteNode(table_name="T", filter=FilterExpr("id", "=", 1))
    assert node4.filter.operator == "="

    # SpatialFilterExpr
    node5 = SpatialFilterExpr(columns=["x", "y"], cx=10.0, cy=20.0, radius=5.0)
    assert node5.radius == 5.0

    # AggregateExpr
    agg = AggregateExpr(func_name="COUNT", argument="id")
    assert agg.func_name == "COUNT"

    # JoinClause
    jc = JoinClause(right_table="B", left_col="A.id", right_col="B.fk")
    assert jc.left_col == "A.id"

    print("  [OK] test_all_ast_node_constructors")


if __name__ == "__main__":
    print("=== debug_lexer.py ===")
    test_tokenize_create_table()
    test_tokenize_select_where()
    test_tokenize_negative_number()
    test_tokenize_string_literal()
    test_tokenize_keywords_case_insensitive()
    test_scan_error_unknown_char()
    test_tokenize_spatial_keywords()
    test_tokenize_comparison_operators()
    test_all_ast_node_constructors()
    print("Todos los tests pasaron.")
