from __future__ import annotations
from typing import Any, List, Optional, Tuple

from .lexer import SQLScanner, Token, TokenKind
from .ast_nodes import (
    InstructionNode,
    CreateSchemaNode, ColumnSpec, IndexDirective,
    QueryNode, AggregateExpr, JoinClause, FilterExpr,
    InsertNode, DeleteNode, ValueLiteral,
    SpatialFilterExpr,
)


class ParseError(Exception):
    pass


_COMPARISON_OPS = frozenset({
    TokenKind.OP_EQ, TokenKind.OP_NEQ,
    TokenKind.OP_LT, TokenKind.OP_GT,
    TokenKind.OP_LTE, TokenKind.OP_GTE,
})

_AGGREGATE_FUNCS = frozenset({
    TokenKind.KW_COUNT, TokenKind.KW_SUM,
    TokenKind.KW_AVG,   TokenKind.KW_MIN, TokenKind.KW_MAX,
})

_INDEX_KINDS = frozenset({
    TokenKind.KW_BTREE, TokenKind.KW_HASH,
    TokenKind.KW_SEQUENTIAL, TokenKind.KW_RTREE,
})

# palabras identificadores (nombres de tabla o columna)
_SOFT_KEYWORDS = frozenset({
    TokenKind.KW_BTREE, TokenKind.KW_HASH, TokenKind.KW_SEQUENTIAL, TokenKind.KW_RTREE,
    TokenKind.KW_COUNT, TokenKind.KW_SUM,  TokenKind.KW_AVG,
    TokenKind.KW_MIN,   TokenKind.KW_MAX,  TokenKind.KW_BIGINT,
    TokenKind.KW_POINT, TokenKind.KW_RADIUS,
})


class QueryCompiler:

    # inicializa el compiler con vacios
    def __init__(self):
        self._tokens: List[Token] = []
        self._cursor: int = 0

   
    # Public API                                                           
    #===============================

    # tokeniza el SQL y ejecuta el recursive-descent parser retornando nodos AST
    def compile(self, query_text: str) -> List[InstructionNode]:
        scanner = SQLScanner(query_text)
        self._tokens = scanner.tokenize()
        self._cursor = 0

        instructions: List[InstructionNode] = []
        while not self._at_end():
            if self._current_kind() == TokenKind.PUNCT_SEMICOLON:
                self._advance()
                continue
            instructions.append(self._parse_statement())
        return instructions

    
    # Statement dispatching                                                
    #============================
    

    # despacha al parser correspondiente según el keyword inicial del statement
    def _parse_statement(self) -> InstructionNode:
        kind = self._current_kind()
        if kind == TokenKind.KW_CREATE:
            return self._parse_create()
        if kind == TokenKind.KW_SELECT:
            return self._parse_select()
        if kind == TokenKind.KW_INSERT:
            return self._parse_insert()
        if kind == TokenKind.KW_DELETE:
            return self._parse_delete()
        tok = self._current_token()
        raise ParseError(
            f"Unknown statement starting with '{tok.lexeme}' at position {tok.position}"
        )

    
    # CREATE TABLE                         
    #============================

    # parsea CREATE TABLE con definición de columnas e índices opcionales
    def _parse_create(self) -> CreateSchemaNode:
        self._expect(TokenKind.KW_CREATE)
        self._expect(TokenKind.KW_TABLE)
        table_name = self._consume_identifier()

        self._expect(TokenKind.PUNCT_LPAREN)
        col_specs = self._parse_column_definitions()
        self._expect(TokenKind.PUNCT_RPAREN)

        index_directives: List[IndexDirective] = []
        if self._current_kind() == TokenKind.KW_INDEX:
            self._advance()
            self._expect(TokenKind.PUNCT_LPAREN)
            index_directives = self._parse_index_directives()
            self._expect(TokenKind.PUNCT_RPAREN)

        from_file_path: Optional[str] = None
        if self._current_kind() == TokenKind.KW_FROM:
            self._advance()  # consume FROM
            file_tok = self._current_token()
            if file_tok.kind != TokenKind.IDENTIFIER or file_tok.lexeme.upper() != "FILE":
                raise ParseError(
                    f"Expected FILE after FROM in CREATE TABLE, got '{file_tok.lexeme}' "
                    f"at position {file_tok.position}"
                )
            self._advance()  # consume FILE
            path_tok = self._expect(TokenKind.LIT_STRING)
            from_file_path = path_tok.lexeme

        return CreateSchemaNode(
            table_name=table_name,
            column_specs=col_specs,
            index_directives=index_directives,
            from_file=from_file_path,
        )

    # parsea la lista de column definitions separadas por coma
    def _parse_column_definitions(self) -> List[ColumnSpec]:
        defs = [self._parse_one_column()]
        while self._current_kind() == TokenKind.PUNCT_COMMA:
            self._advance()
            defs.append(self._parse_one_column())
        return defs

    # parsea una columna con nombre tipo y tamaño opcional entre paréntesis
    def _parse_one_column(self) -> ColumnSpec:
        col_name  = self._consume_identifier()
        data_type = self._consume_type_name()
        size: Optional[int] = None
        if self._current_kind() == TokenKind.PUNCT_LPAREN:
            self._advance()
            size_tok = self._expect(TokenKind.LIT_INTEGER)
            size = int(size_tok.lexeme)
            self._expect(TokenKind.PUNCT_RPAREN)
        return ColumnSpec(col_name=col_name, data_type=data_type, size=size)

    # parsea la lista de index directives separadas por coma
    def _parse_index_directives(self) -> List[IndexDirective]:
        directives = [self._parse_one_index()]
        while self._current_kind() == TokenKind.PUNCT_COMMA:
            self._advance()
            directives.append(self._parse_one_index())
        return directives

    # parsea un index directive validando que el tipo sea BTREE HASH SEQUENTIAL o RTREE
    def _parse_one_index(self) -> IndexDirective:
        tok = self._advance()
        if tok.kind not in _INDEX_KINDS:
            raise ParseError(f"Expected index type (BTREE/HASH/SEQUENTIAL/RTREE), got '{tok.lexeme}'")
        index_kind = tok.lexeme.upper()
        col_name   = self._consume_identifier()
        return IndexDirective(index_kind=index_kind, col_name=col_name)

    
    # SELECT                                                               
    #============================
    # parsea una expresión SELECT con targets FROM JOIN WHERE y GROUP BY
    def _parse_select(self):
    #por implementar
        raise ParseError("SELECT no implementado aún")


    # parsea la target list del SELECT acumulando columnas y aggregate exprs
    def _parse_target_list(self) -> Tuple[List[str], List[AggregateExpr]]:
        targets: List[str]             = []
        aggregations: List[AggregateExpr] = []

        label, agg = self._parse_one_target()
        targets.append(label)
        if agg:
            aggregations.append(agg)

        while self._current_kind() == TokenKind.PUNCT_COMMA:
            self._advance()
            label, agg = self._parse_one_target()
            targets.append(label)
            if agg:
                aggregations.append(agg)

        return targets, aggregations

    # parsea un único target que puede ser star aggregate function o column name
    def _parse_one_target(self) -> Tuple[str, Optional[AggregateExpr]]:
        if self._current_kind() == TokenKind.PUNCT_STAR:
            self._advance()
            return "*", None

        # solo entra al path de aggregate si el siguiente token es '(' — evita
        # confundir columnas llamadas 'count', 'sum', etc. con funciones de agregación
        if (self._current_kind() in _AGGREGATE_FUNCS
                and self._peek_kind() == TokenKind.PUNCT_LPAREN):
            func_tok  = self._advance()
            func_name = func_tok.lexeme.upper()
            self._expect(TokenKind.PUNCT_LPAREN)
            argument = self._consume_identifier()
            self._expect(TokenKind.PUNCT_RPAREN)
            agg   = AggregateExpr(func_name=func_name, argument=argument)
            label = f"{func_name}({argument})"
            return label, agg

        col = self._parse_qualified_name()
        return col, None

    # ------------------------------------------------------------------ #
    # JOIN clause                                                          #
    # ------------------------------------------------------------------ #

    # parsea JOIN table ON left_col op right_col retornando tabla y JoinClause
    def _parse_join_clause(self) -> Tuple[str, JoinClause]:
        join_table = self._consume_identifier()
        self._expect(TokenKind.KW_ON)
        left_col  = self._parse_qualified_name()
        op_tok    = self._advance()
        if op_tok.kind not in _COMPARISON_OPS:
            raise ParseError(f"Expected comparison operator in JOIN ON, got '{op_tok.lexeme}'")
        right_col = self._parse_qualified_name()
        return join_table, JoinClause(
            right_table=join_table,
            left_col=left_col,
            right_col=right_col,
        )

    # ------------------------------------------------------------------ #
    # WHERE / filter chain                                                 #
    # ------------------------------------------------------------------ #

    # parsea el WHERE clause recursivo con AND/OR construyendo árbol de FilterExpr
    def _parse_filter_chain(self) -> FilterExpr:
        node = self._parse_simple_condition()
        # cada AND/OR encadena el nodo actual como left_operand del nuevo nodo
        while self._current_kind() in (TokenKind.KW_AND, TokenKind.KW_OR):
            logic_tok = self._advance()
            right     = self._parse_simple_condition()
            node = FilterExpr(
                left_operand=node,
                operator=logic_tok.lexeme.upper(),
                right_operand=right,
            )
        return node

    # parsea una condición simple — soporta tupla (col_x, col_y) IN POINT para multi-columna
    def _parse_simple_condition(self):
        # detecta (col_x, col_y) IN (POINT(...), ...) — sintaxis multi-columna
        if self._current_kind() == TokenKind.PUNCT_LPAREN:
            self._advance()                          # consume (
            col_x = self._consume_identifier()
            self._expect(TokenKind.PUNCT_COMMA)
            col_y = self._consume_identifier()
            self._expect(TokenKind.PUNCT_RPAREN)
            self._expect(TokenKind.KW_IN)
            return self._parse_spatial_body([col_x, col_y])

        left_col = self._parse_qualified_name()

        # ── col BETWEEN low AND high ─────────────────────────────────
        if self._current_kind() == TokenKind.KW_BETWEEN:
            self._advance()                          # consume BETWEEN
            low  = self._parse_rhs_value()
            self._expect(TokenKind.KW_AND)
            high = self._parse_rhs_value()
            # desugar: col BETWEEN lo AND hi → col >= lo AND col <= hi
            return FilterExpr(
                left_operand  = FilterExpr(left_col, ">=", low),
                operator      = "AND",
                right_operand = FilterExpr(left_col, "<=", high),
            )

        # ── col IN (POINT(cx, cy), RADIUS r | K k) — single-col compat
        if self._current_kind() == TokenKind.KW_IN:
            self._advance()                          # consume IN
            return self._parse_spatial_body([left_col])

        # ── Standard comparison ──────────────────────────────────────
        op_tok = self._advance()
        if op_tok.kind not in _COMPARISON_OPS:
            raise ParseError(
                f"Expected comparison operator in WHERE, got '{op_tok.lexeme}' "
                f"at position {op_tok.position}"
            )
        right_val = self._parse_rhs_value()
        return FilterExpr(
            left_operand=left_col,
            operator=op_tok.lexeme,
            right_operand=right_val,
        )

    # parsea el cuerpo POINT(cx, cy), RADIUS r | K k y retorna SpatialFilterExpr
    def _parse_spatial_body(self, columns: List[str]) -> SpatialFilterExpr:
        self._expect(TokenKind.PUNCT_LPAREN)
        self._expect(TokenKind.KW_POINT)
        self._expect(TokenKind.PUNCT_LPAREN)
        cx = float(self._expect_number())
        self._expect(TokenKind.PUNCT_COMMA)
        cy = float(self._expect_number())
        self._expect(TokenKind.PUNCT_RPAREN)
        self._expect(TokenKind.PUNCT_COMMA)
        if self._current_kind() == TokenKind.KW_RADIUS:
            self._advance()
            r = float(self._expect_number())
            self._expect(TokenKind.PUNCT_RPAREN)
            return SpatialFilterExpr(columns=columns, cx=cx, cy=cy, radius=r)
        # K k — K es IDENTIFIER porque no está en KEYWORD_MAP
        tok = self._current_token()
        if tok.kind == TokenKind.IDENTIFIER and tok.lexeme.upper() == "K":
            self._advance()
            k = int(float(self._expect_number()))
            self._expect(TokenKind.PUNCT_RPAREN)
            return SpatialFilterExpr(columns=columns, cx=cx, cy=cy, k=k)
        raise ParseError(
            f"Expected RADIUS or K after POINT, got '{tok.lexeme}' at position {tok.position}"
        )

    # consume un literal numérico con signo opcional — soporta coordenadas negativas
    def _expect_number(self) -> str:
        sign = ""
        if self._current_kind() == TokenKind.OP_MINUS:
            self._advance()
            sign = "-"
        tok = self._current_token()
        if tok.kind not in (TokenKind.LIT_INTEGER, TokenKind.LIT_FLOAT):
            raise ParseError(
                f"Expected numeric literal, got '{tok.lexeme}' at position {tok.position}"
            )
        return sign + self._advance().lexeme

    # parsea el rhs de comparación con signo opcional — soporta valores negativos
    def _parse_rhs_value(self) -> Any:
        neg = False
        if self._current_kind() == TokenKind.OP_MINUS:
            self._advance()
            neg = True
        tok = self._current_token()
        if tok.kind == TokenKind.LIT_INTEGER:
            self._advance()
            v = int(tok.lexeme)
            return -v if neg else v
        if tok.kind == TokenKind.LIT_FLOAT:
            self._advance()
            v = float(tok.lexeme)
            return -v if neg else v
        if neg:
            raise ParseError(
                f"Expected numeric literal after '-', got '{tok.lexeme}' at position {tok.position}"
            )
        if tok.kind == TokenKind.LIT_STRING:
            self._advance()
            return tok.lexeme
        return self._parse_qualified_name()

    # ------------------------------------------------------------------ #
    # INSERT INTO                                                          #
    # ------------------------------------------------------------------ #

    # parsea INSERT INTO table VALUES con lista de literales entre paréntesis
    def _parse_insert(self) -> InsertNode:
        self._expect(TokenKind.KW_INSERT)
        self._expect(TokenKind.KW_INTO)
        table_name = self._consume_identifier()
        self._expect(TokenKind.KW_VALUES)
        self._expect(TokenKind.PUNCT_LPAREN)
        values = self._parse_value_list()
        self._expect(TokenKind.PUNCT_RPAREN)
        return InsertNode(table_name=table_name, value_list=values)

    # parsea la lista de value literals separados por coma
    def _parse_value_list(self) -> List[ValueLiteral]:
        vals = [self._parse_one_literal()]
        while self._current_kind() == TokenKind.PUNCT_COMMA:
            self._advance()
            vals.append(self._parse_one_literal())
        return vals

    # parsea un literal individual int float o string y lo envuelve en ValueLiteral
    def _parse_one_literal(self) -> ValueLiteral:
        neg = False
        if self._current_kind() == TokenKind.OP_MINUS:
            self._advance()
            neg = True
        tok = self._current_token()
        if tok.kind == TokenKind.LIT_INTEGER:
            self._advance()
            v = int(tok.lexeme)
            return ValueLiteral(raw_value=-v if neg else v)
        if tok.kind == TokenKind.LIT_FLOAT:
            self._advance()
            v = float(tok.lexeme)
            return ValueLiteral(raw_value=-v if neg else v)
        if tok.kind == TokenKind.LIT_STRING and not neg:
            self._advance()
            return ValueLiteral(raw_value=tok.lexeme)
        raise ParseError(
            f"Expected a value literal, got '{tok.lexeme}' at position {tok.position}"
        )

   
    
    def _parse_delete(self):
    # por implementar
        raise ParseError("DELETE no implementado aún")

    # ------------------------------------------------------------------ #
    # Low-level helpers                                                    #
    # ------------------------------------------------------------------ #

    # parsea un nombre qualified como identifier o table.column
    def _parse_qualified_name(self) -> str:
        name = self._consume_identifier()
        if self._current_kind() == TokenKind.PUNCT_DOT:
            self._advance()
            right = self._consume_identifier()
            return f"{name}.{right}"
        return name

    # parsea una lista de nombres (posiblemente calificados) separados por coma
    def _parse_identifier_list(self) -> List[str]:
        cols = [self._parse_qualified_name()]
        while self._current_kind() == TokenKind.PUNCT_COMMA:
            self._advance()
            cols.append(self._parse_qualified_name())
        return cols

    # retorna el token actual sin consumirlo
    def _current_token(self) -> Token:
        return self._tokens[self._cursor]

    # retorna el kind del token actual
    def _current_kind(self) -> TokenKind:
        return self._tokens[self._cursor].kind

    # lookahead de un token sin consumir — retorna EOF si no hay siguiente
    def _peek_kind(self) -> TokenKind:
        nxt = self._cursor + 1
        if nxt < len(self._tokens):
            return self._tokens[nxt].kind
        return TokenKind.EOF

    # indica si el cursor llegó al token EOF
    def _at_end(self) -> bool:
        return self._current_kind() == TokenKind.EOF

    # avanza el cursor y retorna el token que estaba en la posición actual
    def _advance(self) -> Token:
        tok = self._tokens[self._cursor]
        if not self._at_end():
            self._cursor += 1
        return tok

    # consume el token actual validando su kind o lanza ParseError
    def _expect(self, kind: TokenKind) -> Token:
        tok = self._current_token()
        if tok.kind != kind:
            raise ParseError(
                f"Expected {kind.name}, got '{tok.lexeme}' at position {tok.position}"
            )
        return self._advance()

    # consume el token actual como identifier — también acepta soft keywords como nombres
    def _consume_identifier(self) -> str:
        tok = self._current_token()
        if tok.kind == TokenKind.IDENTIFIER or tok.kind in _SOFT_KEYWORDS:
            return self._advance().lexeme
        raise ParseError(
            f"Expected identifier, got '{tok.lexeme}' (kind={tok.kind.name}) "
            f"at position {tok.position}"
        )

    # consume un nombre de tipo — acepta IDENTIFIER o KW_BIGINT para CREATE TABLE
    def _consume_type_name(self) -> str:
        tok = self._current_token()
        if tok.kind in (TokenKind.IDENTIFIER, TokenKind.KW_BIGINT):
            return self._advance().lexeme
        raise ParseError(
            f"Expected type name, got '{tok.lexeme}' (kind={tok.kind.name}) "
            f"at position {tok.position}"
        )
