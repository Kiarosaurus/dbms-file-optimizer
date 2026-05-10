# JupiterDB

Simulador de SGBD con arquitectura de almacenamiento paginado (4KB).

Motor de base de datos implementado desde cero que opera integramente en memoria secundaria. Toda lectura y escritura se realiza en bloques de 4096 bytes -- sin carga masiva de archivos en RAM.

---

## Arquitectura del Sistema

```
JupiterDB/
    core/           Esquema, motor de paginas (4KB), concurrencia, telemetria, gestion de workspaces
    indexing/       B+ Tree Clustered, Extendible Hash, Sequential File, R-Tree (Quadratic Split)
    parsing/        Lexer, AST y analizador sintactico descendente recursivo para SQL
    engine/         Ejecucion de consultas, ordenamiento externo (TPMMS), generador de reportes
    gui/            GUI principal, bridge con el motor, explorador de paginas con vistas Decoded y Esquema
    benchmarking/   Pruebas de rendimiento con workspace aislado (bench_run)
    workspaces/     Proyectos aislados -- cada subdirectorio es un entorno independiente
        default_testing/   Proyecto por defecto; se siembra con tablas demo al iniciar
        <proyecto>/        Cualquier proyecto adicional creado desde la GUI
```

| Modulo | Responsabilidad principal |
|--------|--------------------------|
| `core/` | `StorageEngine(base_dir)`, paginacion 4KB, `Telemetry` (conteo de I/O), journaling, `WorkspaceManager` |
| `indexing/` | Cuatro estructuras de indice; el B+ Tree es clustered (registros reales en hojas) |
| `parsing/` | Soporta `CREATE TABLE`, `SELECT`, `INSERT`, `DELETE`, `JOIN`, `BETWEEN`, `IN (POINT, RADIUS/KNN)`, `GROUP BY`, `AND/OR`, `BIGINT` |
| `engine/` | Algoritmos de join (MERGE, HASH, EXTERNAL_SORT_MERGE), TPMMS para datos fuera de RAM |
| `gui/` | Visualizacion espacial del R-Tree, explorador de paginas (Decoded + Esquema Visual), selector de proyecto |

---

## Tipos de Datos Soportados

| Tipo | Bytes | Formato interno | Notas |
|------|-------|-----------------|-------|
| INTEGER | 4 | `struct 'i'` (int32) | Rango: -2,147,483,648 a 2,147,483,647 |
| BIGINT | 8 | `struct 'q'` (int64) | Se infiere automaticamente en SUPER INSERT si el valor desborda INTEGER |
| FLOAT | 4 | `struct 'f'` (float32) | |
| VARCHAR(N) | N | `struct 'Ns'` (bytes fijos) | Se trunca o rellena con nulos hasta N bytes |

---

## Sintaxis SQL Soportada

### DDL y DML

```sql
CREATE TABLE Tabla (id INTEGER, nombre VARCHAR(50)) USING BTREE
INSERT INTO Tabla VALUES (1, 'Alice')
DELETE FROM Tabla WHERE id = 1
```

### Consulta basica

```sql
SELECT * FROM Tabla WHERE campo = valor
SELECT * FROM Tabla WHERE campo > 10 AND estado = 'activo'
SELECT campo1, campo2 FROM Tabla WHERE campo BETWEEN a AND b
SELECT * FROM T1 JOIN T2 ON T1.id = T2.fk
SELECT campo, COUNT(*) FROM Tabla GROUP BY campo
SELECT campo, SUM(valor), AVG(valor), MIN(valor), MAX(valor) FROM Tabla GROUP BY campo
```

### Consulta espacial -- columna unica (inferida)

```sql
SELECT * FROM Ciudades WHERE X IN (POINT(20.0, 30.0), RADIUS 10)
SELECT * FROM Puntos WHERE coords IN (POINT(-73.9, 40.7), KNN 5)
```

La columna `X` debe estar indexada con R-Tree. El motor infiere la columna Y por metadatos del catalogo.

### Consulta espacial -- columnas explicitas

```sql
SELECT * FROM Ciudades WHERE (lat, lon) IN (POINT(-12.0, -77.0), RADIUS 50)
SELECT * FROM Lugares WHERE (x, y) IN (POINT(0.0, 0.0), KNN 3)
```

Permite especificar los dos ejes directamente cuando los nombres de columna no siguen la convencion por defecto.

Las coordenadas pueden ser negativas: `POINT(-73.9, 40.7)` es valido.

---

## Workspaces (Proyectos Aislados)

Cada proyecto es un directorio bajo `workspaces/` con su propio `catalog.json` y archivos `.bin`. Los proyectos no comparten tablas ni datos.

```
workspaces/
    default_testing/
        catalog.json          metadatos de tablas (rutas relativas al workspace)
        Estudiantes_main.bin  paginas 4KB del indice principal
        Notas_main.bin
        Ciudades_main.bin
        journal.log           log de transacciones
    mi_proyecto/
        catalog.json
        ...
```

**Comportamiento por workspace:**

- `default_testing`: al iniciar con catalogo vacio, se siembran automaticamente las tablas demo `Estudiantes`, `Notas` y `Ciudades`. Los botones de ejemplo muestran consultas predefinidas.
- Otros workspaces: se crean vacios. Los botones de ejemplo muestran un placeholder hasta que el usuario escribe algo. La última edición por botón se conserva mientras dure la sesión.

**Crear o cambiar proyecto:** Usa el Combobox "Proyecto Activo" en la barra superior de la GUI. Al seleccionar un nombre nuevo, el workspace se crea automaticamente con un catalogo vacío.

**SUPER INSERT:** Al hacer clic en el boton `SUPER INSERT (CSV)`, la GUI pregunta el proyecto destino antes de abrir el selector de archivo. Si el proyecto no existe, se crea en ese momento. La siembra demo no se ejecuta en proyectos distintos de `default_testing`.

---

## Auditor de Almacenamiento (Page Explorer)

Herramienta integrada en la GUI para inspeccionar el contenido binario de cualquier archivo `.bin` del workspace activo.

### Modos de visualizacion

**Hex** (default): muestra los bytes crudos de la page en formato hexadecimal clásico, 16 bytes por fila.

**Decoded**: muestra los registros de la page interpretados segun el esquema de la tabla. Cada fila muestra el numero de slot, los nombres de campo, los valores decodificados y el tipo de dato. Los slots vacios (todos bytes en cero) se omiten.

**Esquema Visual**: muestra cada byte de la page coloreado segun el campo al que pertenece y la paridad del registro. Permite identificar rapidamente la distribucion de campos dentro de cada slot y distinguir registros pares e impares. Incluye una leyenda interactiva con el significado de cada color.

- Letras de tipo por byte: `I` = INTEGER, `B` = BIGINT, `F` = FLOAT, `S` = STRING, `H` = header/padding
- Colores: azul para registros pares, verde para registros impares; tonos alternos por campo dentro del mismo registro
- Bytes de padding de page: color oscuro neutro con punto centrado

---

## Requisitos

- Python 3.10+
- `matplotlib` -- generacion de gráficos de benchmark
- `pandas` -- procesamiento de metricas CSV

---

## Instalacion

```bash
pip install -r requirements.txt
```

---

## Como Ejecutar

### Lanzador principal (limpieza + GUI)

```bash
cd dbms-file-optimizer
python run_all.py          # limpia default_testing y lanza la GUI
python run_all.py --keep   # salta la limpieza, solo lanza la GUI
```

`run_all.py` elimina todos los `.bin`, `.json` y `.log` del workspace `default_testing` antes de iniciar, para garantizar un entorno limpio. Con `--keep` se omite ese paso. Los demas workspaces no se tocan.

### Solo GUI (sin limpieza)

```bash
cd dbms-file-optimizer
python run_gui.py
```

Lanza la GUI directamente. Util cuando ya existe data en `default_testing` que se quiere conservar.

---

### Benchmark

```bash
cd dbms-file-optimizer
python benchmarking/run_tests.py
```

Ejecuta benchmarks de insercion, busqueda puntual, busqueda por rango y estrategias de JOIN para N en {1000, 5000, 10000} registros. Los datos temporales se escriben en `workspaces/bench_run/` y se eliminan al finalizar.

Salida: `results/metrics.csv`

---

### Informe técnico

```bash
cd dbms-file-optimizer
python engine/report_generator.py
```

Lee `results/metrics.csv` y la plantilla `docs/report_template.md`. Sustituye los placeholders con los valores reales del benchmark, genera 2 graficos en `docs/charts/` y escribe el informe final en `docs/FINAL_REPORT.md`.

---

## Estructura de Resultados

```
results/
    metrics.csv            metricas crudas: inserciones, busquedas, accesos a disco, joins

docs/
    report_template.md     plantilla con placeholders para sustitucion automatica
    FINAL_REPORT.md        informe generado -- listo para entregar
    charts/
        disk_accesses.png  accesos a disco por indice y tamano de dataset
        execution_time.png tiempo de ejecucion por indice y tamano de dataset
```

---

## Resetear un workspace manualmente

```bash
# Windows
rmdir /s /q workspaces\default_testing

# Linux / macOS
rm -rf workspaces/default_testing
```

La GUI lo recrea con catalogo vacio la proxima vez que ese proyecto es seleccionado. En `default_testing` se vuelven a sembrar las tablas demo automaticamente.

---

## Despliegue con Docker

El `docker-compose.yml` incluye dos servicios:

| Servicio | Comando | Descripcion |
| :--- | :--- | :--- |
| `gui` | `python run_gui.py` | GUI Tkinter con reenvio X11 |
| `bench` | `python run_all.py` | Benchmarks headless (perfil `bench`) |

### Linux

```bash
xhost +local:docker
docker compose up gui
```

### macOS

Requiere [XQuartz](https://www.xquartz.org/) instalado y corriendo:

```bash
# Instalar XQuartz, luego:
defaults write org.xquartz.X11 enable_iglx -bool true
xhost +127.0.0.1
DISPLAY=host.docker.internal:0 docker compose up gui
```

### Windows

Requiere un servidor X11 como [VcXsrv](https://sourceforge.net/projects/vcxsrv/) o [Xming](https://sourceforge.net/projects/xming/):

```powershell
# Con VcXsrv corriendo (opcion "Disable access control" activada):
$env:DISPLAY = "host.docker.internal:0.0"
docker compose up gui
```

Alternativa sin X11 — ejecutar directamente en el entorno local:

```bash
pip install -r requirements.txt
python run_gui.py
```

### Solo benchmarks (sin GUI)

```bash
docker compose --profile bench up bench
```

Los resultados quedan en `results/metrics.csv` y los graficos en `docs/charts/`.

---

## Reproducibilidad

Todos los benchmarks usan semillas fijas (`seed=42`, `seed=99`). Ejecutar `run_tests.py` dos veces en el mismo entorno produce el mismo `metrics.csv`.

---

## Preguntas Frecuentes (Exposicion)

### Por que el B+ Tree es Clustered?

Un B+ Tree **clustered** almacena los registros completos directamente en los nodos hoja, en lugar de guardar un puntero hacia un heap file separado. Esto elimina un I/O extra por busqueda: en un indice no-clustered, encontrar la clave cuesta `O(log N)` lecturas de arbol mas **1 lectura adicional** al heap file para recuperar el registro. Con clustered, ese acceso al heap desaparece -- el registro ya esta en la hoja.

```
No-clustered:  buscar clave -> nodo hoja (puntero) -> heap file  [2 accesos finales]
Clustered:     buscar clave -> nodo hoja (registro)              [1 acceso final]
```

En JupiterDB cada nodo ocupa exactamente una pagina de 4096 bytes. El ahorro se multiplica en joins y escaneos de rango, donde se leen decenas o cientos de hojas consecutivas.

---

### Como elige el motor la estrategia de JOIN?

Logica en `engine/core.py` — seleccion segun tamano y tipo de indice:

| Condicion | Estrategia |
|-----------|-----------|
| Ambas tablas ordenadas por la join key (B+ Tree o Sequential File) | **MERGE** — streaming, sin materializar |
| Alguna tabla supera `_EXTERNAL_SORT_THRESHOLD` = 500 filas | **EXTERNAL_SORT_MERGE** — ordena en disco via TPMMS, luego merge |
| Dataset pequeno (ambas < 500 filas) | **HASH** — build map en memoria, probe en O(1) promedio |

---

### Como permite el TPMMS ordenar datos mayores a la RAM?

**Two-Phase Multiway Merge Sort (TPMMS)** divide el problema en dos fases:

**Fase 1 -- Generacion de Runs:**
Se leen bloques del archivo de entrada hasta llenar el buffer disponible (`BUFFER_SIZE` paginas). Ese bloque se ordena en memoria y se escribe como un *run* ordenado en disco. Se repite hasta agotar la entrada. Con `N` paginas totales y buffer de `B` paginas, se generan `ceil(N/B)` runs.

**Fase 2 -- Merge K-vias:**
Se abren todos los runs simultaneamente. Se mantiene un puntero al frente de cada uno. En cada paso se emite el menor registro de todos los frentes y se avanza ese puntero. Un min-heap de tamano `K` (numero de runs) hace esto en `O(log K)` por registro.

```
Disco -> [Run 1 ordenado] [Run 2 ordenado] ... [Run K ordenado]
                 \               |               /
               Merge K-vias en memoria (min-heap)
                               |
                    Archivo de salida ordenado
```

Resultado: el dataset completo se ordena con solo **2 pasadas** sobre el disco (`2N` I/Os), sin importar cuanto supere la RAM. JupiterDB usa esto en `engine/external_sort.py` para los joins `EXTERNAL_SORT_MERGE`.

---

### Que es el Global Depth en el Extendible Hashing?

El **Global Depth** (`g`) es el numero de bits del hash que usa el **directorio** para enrutar a los buckets. El directorio tiene siempre `2^g` entradas. Cada bucket tiene tambien un **Local Depth** (`l <= g`) que indica cuantos bits definen de forma unica a ese bucket.

```
Global Depth g = 2  ->  directorio con 4 entradas (00, 01, 10, 11)

Directorio          Buckets
  00  ->  B0        B0 (l=1): 0x   <- compartido por 00 y 10
  01  ->  B1        B1 (l=2): 01
  10  ->  B0        B2 (l=2): 11
  11  ->  B2
```

Cuando un bucket se llena y `l < g`: se **divide** ese bucket sin tocar el directorio. Cuando `l = g`: se **duplica** el directorio (`g -> g+1`, de `2^g` a `2^(g+1)` entradas) antes de dividir. En JupiterDB el directorio cabe en una sola pagina de 4096 bytes hasta `g = 9` (512 entradas x 4 bytes + cabecera = 2060 bytes).
