# Informe Técnico: JupiterDB - Simulador de SGBD Paginado (4 KB)

**Curso:** Base de Datos 2 · UTEC · Ciclo 2026-1  
**Generado:** {{date}}

---

## 1. Introducción y Objetivo

JupiterDB es un mini-gestor de bases de datos implementado íntegramente en Python que opera sobre memoria secundaria con acceso estrictamente paginado (bloques de 4 096 bytes). Ninguna operación carga el archivo completo en RA. Toda lectura y escritura pasa por `DiskController`, que contabiliza cada acceso mediante el singleton `Telemetry`.

El sistema en su fase actual integra: (i) dos estructuras de indexación sobre disco (Sequential File y B+ Tree), (ii) un parser SQL de descendente recursivo con soporte para operaciones DML y DDL, (iii) un simulador de control de concurrencia con bloqueos a nivel de page, y (iv) un framework para almacenamiento paginado y serialización de registros.

---

## 2. Arquitectura de Almacenamiento

### 2.1. DiskController y Telemetría

El `DiskController` es el punto único de acceso a disco. Implementa:

- **Lectura paginada:** `read_page(file_path, page_id)` → bytearray (4 096 B)
- **Escritura paginada:** `write_page(file_path, page_id, data)` → void
- **Contabilización:** cada operación incrementa contadores en el singleton `Telemetry()`

```
[SQL Query]
    |
    v
[StorageEngine]
    |
    v
[Index (BTree / Sequential)]
    |
    +---> [DiskController]
    |          |
    |          +---> Telemetry().pages_read++
    |          +---> Telemetry().pages_written++
    |
    v
[Archivo binario en disco]
```

**Contadores de Telemetría:**
- `pages_read`: total de lecturas de page
- `pages_written`: total de escrituras de page
- `_disk_accesses`: suma de lecturas + escrituras
- `_total_ms`: tiempo acumulado en operaciones de disco

### 2.2. Serialización de Registros

`RecordSerializer` implementa conversión bidireccional entre objetos Python y bytes:

```python
# Esquema de ejemplo
schema = {
    "ID":   ("INTEGER", 4),
    "Name": ("VARCHAR", 20),
    "Age":  ("INTEGER", 4),
}
# Record: {"ID": 1, "Name": "Alice", "Age": 30}
# Bytes: [0x00 0x00 0x00 0x01] ["Alice" padded 20B] [0x00 0x00 0x00 0x1E]
```

**Tipos soportados:**
- `INTEGER` (4 B, big-endian)
- `BIGINT` (8 B, big-endian)
- `FLOAT` (8 B, IEEE 754)
- `VARCHAR(n)` (n bytes, null-padded)

**Registros por page:** Con PAGE_SIZE=4 096 B y record de 28 B promedio, capacidad ≈ 146 registros/page.

---

## 3. Estructuras de Indexación

### 3.1. Sequential File

El archivo principal mantiene registros ordenados por clave primaria. Las inserciones se dirigen a un **archivo de desbordamiento (overflow)** sin ordenar. Cuando el overflow alcanza un umbral, se dispara una **reorganización física**.

**Operaciones implementadas (COMMIT 14):**

| Operación | Algoritmo | Estado |
| :--- | :--- | :--- |
| `search(k)` | Búsqueda binaria en main + escaneo lineal en overflow |  Completo |
| `add(r)` | Append en overflow |  Completo |
| `reorganize()` | Fusión de main + overflow, re-ordenamiento |  Completo (H+22) |
| `remove(k)` | Marca lógica |  Completo |

**Estructura en disco:**

```
seq_main.bin:
  [Header: Page 0 -> n_records]
  [Pages 1..N -> registros ordenados]

seq_overflow.bin:
  [Header: Page 0 -> n_records]
  [Pages 1..M -> registros sin orden (append-only)]
```

**Coste teórico:**

| Operación | Complejidad |
| :--- | :--- |
| Búsqueda puntual | O(log(N/B) + K/B) |
| Inserción | O(1) amortizado |
| Rango search | O(log(N/B) + R/B) |
| Borrado | O(log(N/B)) |

donde B = registros por page, K = registros en overflow, R = registros en resultado.

---

### 3.2. B+ Tree

Árbol balanceado con nodos de tamaño fijo (una page = 4 096 B). Las hojas contienen los registros reales. Los nodos internos almacenan claves de separación y punteros a hijos.

**Estructura:**

```plaintext
                        [ Raíz ]
                       /      \
          [ Nodo Int ]          [ Nodo Int ]
          /           \         /          \
    [ Hoja ] <--> [ Hoja ]  [ Hoja ] <--> [ Hoja ]
   (1-145)      (146-290)  (291-...)         (Final)
```

**Capacidades (PAGE_SIZE = 4 096 B, record = 28 B):**
- Hojas: ≈ 146 registros/hoja
- Internos: ≈ 340 claves/nodo

**Operaciones implementadas (COMMIT 09):**

| Operación | Coste (pages) | Estado |
| :--- | :--- | :--- |
| `search(k)` | `⌈log_{146}(N)⌉` |  Completo |
| `add(r)` | `⌈log(N)⌉` + splits |  Completo |
| `range_search(lo, hi)` | `⌈log(N)⌉ + ⌈result/146⌉` |  Completo |
| `remove(k)` | `⌈log(N)⌉` |  Completo |

**Profundidades esperadas:**

```
N =      1 000  ->  h = 2–3
N =     10 000  ->  h = 3–4
N =    100 000  ->  h = 4–5
```

---

## 4. Parser SQL

### 4.1. Arquitectura (COMMIT 11)

```
SQL Text  ->  [Lexer]  ->  Token stream
                                |
                                v
                    [Recursive Descent Parser]
                                |
                                v
                        AST (InstructionNode)
                                |
                                v
                     [StorageEngine.execute()]
```

### 4.2. Gramática Formal (EBNF)

```ebnf
program       = statement { ';' statement } [ ';' ] ;

statement     = create_stmt | select_stmt | insert_stmt | delete_stmt ;

create_stmt   = 'CREATE' 'TABLE' IDENT '(' col_def { ',' col_def } ')' ;
col_def       = IDENT type_spec [ 'INDEX' index_type ] ;
type_spec     = 'INTEGER' | 'BIGINT' | 'FLOAT' | 'VARCHAR' '(' INT ')' ;
index_type    = 'BTREE' | 'SEQUENTIAL' ;

select_stmt   = 'SELECT' target_list 'FROM' IDENT
                [ where_clause ] [ groupby_clause ] ;
target_list   = '*' | target { ',' target } ;
target        = agg_expr | qualified_col ;
agg_expr      = ( 'COUNT' | 'SUM' | 'AVG' | 'MIN' | 'MAX' ) '(' target ')' ;

where_clause  = 'WHERE' filter_expr ;
filter_expr   = cmp_cond { 'AND' cmp_cond } ;
cmp_cond      = IDENT ( '=' | '!=' | '<' | '>' | '<=' | '>=' ) literal ;

insert_stmt   = 'INSERT' 'INTO' IDENT 'VALUES' '(' literal { ',' literal } ')' ;
delete_stmt   = 'DELETE' 'FROM' IDENT [ 'WHERE' cmp_cond ] ;

literal       = INT | FLOAT | STRING ;
```

**Características del parser (COMMIT 11):**
-  CREATE TABLE con tipos INTEGER, FLOAT, VARCHAR, BIGINT
-  INDEX BTREE / SEQUENTIAL en creación
-  INSERT INTO con validación de tipos
-  SELECT con * y columnas específicas
-  WHERE con predicados simples y AND
-  DELETE con WHERE opcional
-  GROUP BY con agregaciones COUNT, SUM, AVG, MIN, MAX

---

### 4.3. Lexer - DFA Simplificado

```
Estado INIT:
  [a-zA-Z_]  ->  IDENT_STATE
  [0-9]      ->  NUM_STATE
  '\''       ->  STR_STATE
  '<', '>'   ->  CMP_STATE
  '='        ->  emit(EQ)
  otros      ->  emit(PUNCT)
```

**Palabras reservadas:**
CREATE, TABLE, INDEX, BTREE, SEQUENTIAL, SELECT, FROM, WHERE, INSERT, INTO, VALUES, DELETE, AND, BY, COUNT, SUM, AVG, MIN, MAX, GROUP, INTEGER, FLOAT, VARCHAR, BIGINT.

---

## 5. Sistema de Concurrencia (Fase 1)

### 5.1. Page-Level Write Locks (COMMIT 05)

`PageLockManager` implementa bloqueos exclusivos a nivel de page:

```python
def acquire_write(file_path, page_id, txn_id):
    lock = _get_or_create_lock(file_path, page_id)
    if lock.is_held():
        register_wait(current_thread, lock.holder)
        lock.wait()  # Bloquea hasta liberación
    lock.holder = current_thread
    Telemetry().mark_locked_page(file_path, page_id)

def release_write(file_path, page_id):
    lock = _locks[(file_path, page_id)]
    lock.holder = None
    lock.notify()  # Despierta threads esperando
```

**Características (COMMIT 05):**
-  Bloqueos exclusivos por (file, page_id)
-  Queue simple FIFO de espera
-  Timeout de 5 segundos
-  Journal de transacciones (`journal.log`)

**No implementado aún:**
- Read locks (se implementarán en COMMIT 33)
- Detección de deadlock (se implementará en COMMIT 33)

---

## 6. Evaluación Teórica

### 6.1. Tabla Comparativa B+Tree vs Sequential File

| Operación | B+Tree | Sequential File |
| :--- | :---: | :---: |
| Búsqueda puntual | **O(log_t N)** | O(log(N/B) + K/B) |
| Inserción | O(log_t N) amort. | O(1) amort.$^1$ |
| Range search | O(log_t N + R/t) | O(log(N/B) + R/B) |
| Borrado | O(log_t N) | O(log(N/B)) |

$^1$ Amortizado en inserciones pequeñas; O(N/B) en reorganización, pero con frecuencia 1/K.

### 6.2. Profundidad de B+Tree

Con `leaf_cap = 146`:

```
h = ⌈log_146(N)⌉ + 1

N =      1 000  ->  h = 2
N =     10 000  ->  h = 3
N =    100 000  ->  h = 4
```

---

## 7. Estado Actual del Proyecto

### Completado 

| Módulo | Componente | Status |
| :--- | :--- | :--- |
| **core/** | storage.py, schema.py, metrics.py | Completo |
| **core/** | workspace_manager.py | Completo |
| **core/** | concurrency.py (write locks básicos) | Completo |
| **indexing/** | base_index.py | Completo |
| **indexing/** | sequential_file.py |  (sin range search push-down) |
| **indexing/** | bplus_tree.py |  Completo |
| **parsing/** | lexer.py, ast_nodes.py |  |
| **parsing/** | sql_analyzer.py |  Completo |
| **engine/** | core.py (básico) |  Parcial |

## 8. Archivos de Debugging

Cada commit va acompañado de tests en `debugging/`:

```
debug_storage.py       → RecordSerializer, DiskController, schema
debug_schema.py        → Type validation, edge cases
debug_lexer.py         → Token scanning, keyword recognition
debug_parser.py        → AST construction, grammar validation
debug_sequential.py    → Sequential File operations, binary search
debug_bplus.py         → B+Tree insert, split, search, range
debug_concurrency.py   → Page locks, wait queues, transaction journal
debug_engine.py        → StorageEngine CRUD, catalog management
debug_queries.py       → End-to-end SQL queries (próximo)
debug_report.py        → Report generation (próximo)
```

Ejecutar: `python debugging/debug_X.py` desde raíz del proyecto.

---
