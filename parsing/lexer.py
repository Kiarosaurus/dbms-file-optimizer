from __future__ import annotations
from dataclasses import dataclass
from enum import Enum, auto
from typing import List


class TokenKind(Enum):
    # Keywords
    KW_CREATE     = auto()
    KW_TABLE      = auto()
    KW_SELECT     = auto()
    KW_FROM       = auto()
    KW_JOIN       = auto()
    KW_ON         = auto()
    KW_WHERE      = auto()
    KW_GROUP      = auto()
    KW_BY         = auto()
    KW_INSERT     = auto()
    KW_INTO       = auto()
    KW_VALUES     = auto()
    KW_DELETE     = auto()
    KW_INDEX      = auto()
    KW_BTREE      = auto()
    KW_HASH       = auto()
    KW_SEQUENTIAL = auto()
    KW_RTREE      = auto()
    KW_AND        = auto()
    KW_OR         = auto()
    KW_BETWEEN    = auto()
    KW_IN         = auto()
    KW_POINT      = auto()
    KW_RADIUS     = auto()
    KW_COUNT      = auto()
    KW_SUM        = auto()
    KW_AVG        = auto()
    KW_MIN        = auto()
    KW_MAX        = auto()
    KW_BIGINT     = auto()
    # Comparison operators
    OP_EQ  = auto()
    OP_NEQ = auto()
    OP_LT  = auto()
    OP_GT  = auto()
    OP_LTE = auto()
    OP_GTE   = auto()
    OP_MINUS = auto()
    # Punctuation
    PUNCT_LPAREN    = auto()
    PUNCT_RPAREN    = auto()
    PUNCT_COMMA     = auto()
    PUNCT_DOT       = auto()
    PUNCT_STAR      = auto()
    PUNCT_SEMICOLON = auto()
    # Literals
    LIT_INTEGER = auto()
    LIT_FLOAT   = auto()
    LIT_STRING  = auto()
    # General
    IDENTIFIER = auto()
    EOF        = auto()


KEYWORD_MAP: dict[str, TokenKind] = {
    "CREATE":     TokenKind.KW_CREATE,
    "TABLE":      TokenKind.KW_TABLE,
    "SELECT":     TokenKind.KW_SELECT,
    "FROM":       TokenKind.KW_FROM,
    "JOIN":       TokenKind.KW_JOIN,
    "ON":         TokenKind.KW_ON,
    "WHERE":      TokenKind.KW_WHERE,
    "GROUP":      TokenKind.KW_GROUP,
    "BY":         TokenKind.KW_BY,
    "INSERT":     TokenKind.KW_INSERT,
    "INTO":       TokenKind.KW_INTO,
    "VALUES":     TokenKind.KW_VALUES,
    "DELETE":     TokenKind.KW_DELETE,
    "INDEX":      TokenKind.KW_INDEX,
    "BTREE":      TokenKind.KW_BTREE,
    "HASH":       TokenKind.KW_HASH,
    "SEQUENTIAL": TokenKind.KW_SEQUENTIAL,
    "RTREE":      TokenKind.KW_RTREE,
    "AND":        TokenKind.KW_AND,
    "OR":         TokenKind.KW_OR,
    "BETWEEN":    TokenKind.KW_BETWEEN,
    "IN":         TokenKind.KW_IN,
    "POINT":      TokenKind.KW_POINT,
    "RADIUS":     TokenKind.KW_RADIUS,
    "COUNT":      TokenKind.KW_COUNT,
    "SUM":        TokenKind.KW_SUM,
    "AVG":        TokenKind.KW_AVG,
    "MIN":        TokenKind.KW_MIN,
    "MAX":        TokenKind.KW_MAX,
    "BIGINT":     TokenKind.KW_BIGINT,
}


@dataclass
class Token:
    kind: TokenKind
    lexeme: str
    position: int   


class ScanError(Exception):
    pass


class SQLScanner:

    # inicializa el scanner con el source SQL y estado interno del tokenizer
    def __init__(self, source: str):
        self._src = source
        self._pos = 0
        self._tokens: List[Token] = []

    # recorre el source completo y retorna la lista de tokens con EOF al final
    def tokenize(self) -> List[Token]:
        while self._pos < len(self._src):
            self._skip_whitespace()
            if self._pos < len(self._src):
                self._read_next_token()
        self._tokens.append(Token(TokenKind.EOF, "", self._pos))
        return self._tokens

    
    # Internal helpers                                                   

    # avanza el cursor sobre whitespace sin emitir tokens
    def _skip_whitespace(self):
        while self._pos < len(self._src) and self._src[self._pos].isspace():
            self._pos += 1

    # retorna el caracter actual sin avanzar el cursor
    def _ch(self) -> str:
        return self._src[self._pos] if self._pos < len(self._src) else "\0"

    # retorna el caracter siguiente para lookahead de dos caracteres
    def _ch_ahead(self) -> str:
        nxt = self._pos + 1
        return self._src[nxt] if nxt < len(self._src) else "\0"

    # consume y retorna el caracter actual avanzando el cursor
    def _eat(self) -> str:
        ch = self._src[self._pos]
        self._pos += 1
        return ch

    # construye y agrega un Token a la lista interna
    def _emit(self, kind: TokenKind, lexeme: str, start: int):
        self._tokens.append(Token(kind, lexeme, start))

    
    # Token readers                                                       
    # ------------------------------------------------------------------ #

    # despacha la lectura del próximo token según el caracter actual
    def _read_next_token(self):
        start = self._pos
        ch = self._ch()

        if ch == "=" :
            self._eat(); self._emit(TokenKind.OP_EQ,  "=",  start)
        elif ch == "!" and self._ch_ahead() == "=":
            self._eat(); self._eat(); self._emit(TokenKind.OP_NEQ, "!=", start)
        elif ch == "<" and self._ch_ahead() == "=":
            self._eat(); self._eat(); self._emit(TokenKind.OP_LTE, "<=", start)
        elif ch == ">" and self._ch_ahead() == "=":
            self._eat(); self._eat(); self._emit(TokenKind.OP_GTE, ">=", start)
        elif ch == "<":
            self._eat(); self._emit(TokenKind.OP_LT, "<", start)
        elif ch == ">":
            self._eat(); self._emit(TokenKind.OP_GT, ">", start)
        elif ch == "-":
            self._eat(); self._emit(TokenKind.OP_MINUS, "-", start)
        elif ch == "(":
            self._eat(); self._emit(TokenKind.PUNCT_LPAREN,    "(", start)
        elif ch == ")":
            self._eat(); self._emit(TokenKind.PUNCT_RPAREN,    ")", start)
        elif ch == ",":
            self._eat(); self._emit(TokenKind.PUNCT_COMMA,     ",", start)
        elif ch == ".":
            self._eat(); self._emit(TokenKind.PUNCT_DOT,       ".", start)
        elif ch == "*":
            self._eat(); self._emit(TokenKind.PUNCT_STAR,      "*", start)
        elif ch == ";":
            self._eat(); self._emit(TokenKind.PUNCT_SEMICOLON, ";", start)
        elif ch in ("'", '"'):
            self._read_string(ch, start)
        elif ch.isdigit():
            self._read_number(start)
        elif ch.isalpha() or ch == "_":
            self._read_word(start)
        else:
            raise ScanError(f"Unexpected character '{ch}' at position {start}")

    # escanea un string literal delimitado por comillas simples o dobles
    # soporta '' como escape de comilla simple dentro del string
    def _read_string(self, quote: str, start: int):
        self._eat() 
        buf: list[str] = []
        while self._pos < len(self._src):
            ch = self._ch()
            if ch == quote:
                self._eat()
                if self._pos < len(self._src) and self._ch() == quote:
                    buf.append(quote)
                    self._eat()
                else:
                    break
            else:
                buf.append(ch)
                self._eat()
        else:
            raise ScanError(f"Unterminated string literal starting at position {start}")
        self._emit(TokenKind.LIT_STRING, "".join(buf), start)

    # escanea un literal numérico entero o float con punto decimal
    def _read_number(self, start: int):
        while self._ch().isdigit():
            self._eat()
        if self._ch() == "." and self._ch_ahead().isdigit():
            self._eat()
            while self._ch().isdigit():
                self._eat()
            self._emit(TokenKind.LIT_FLOAT, self._src[start : self._pos], start)
        else:
            self._emit(TokenKind.LIT_INTEGER, self._src[start : self._pos], start)

    # escanea una palabra y la resuelve como keyword o identifier via KEYWORD_MAP
    def _read_word(self, start: int):
        while self._ch().isalnum() or self._ch() == "_":
            self._eat()
        word = self._src[start : self._pos]
        kind = KEYWORD_MAP.get(word.upper(), TokenKind.IDENTIFIER)
        self._emit(kind, word, start)
