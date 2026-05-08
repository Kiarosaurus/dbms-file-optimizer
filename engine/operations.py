from __future__ import annotations

from typing import Any, Callable, Dict, Iterator, List


# nested loop join: fallback O(N*M), materializa el lado derecho en memoria
def nested_loop_join(
    iter_a: Iterator[Dict],
    iter_b: Iterator[Dict],
    condition: Callable[[Dict, Dict], bool],
) -> Iterator[Dict]:
    rows_b: List[Dict] = list(iter_b)
    for row_a in iter_a:
        for row_b in rows_b:
            if condition(row_a, row_b):
                yield {**row_a, **row_b}


# merge join streaming: inputs deben venir pre-ordenados por col_a y col_b.
# solo bufferiza el grupo izquierdo del key actual; el lado derecho se consume en streaming.
def merge_join(
    iter_a: Iterator[Dict],
    iter_b: Iterator[Dict],
    col_a: str,
    col_b: str,
) -> Iterator[Dict]:
    _DONE = object()
    a = next(iter_a, _DONE)
    b = next(iter_b, _DONE)

    while a is not _DONE and b is not _DONE:
        ka = a[col_a]
        kb = b[col_b]

        if ka == kb:
            # bufferiza todas las filas izquierdas con este key (O(left group size))
            match_key = ka
            group_a: List[Dict] = []
            while a is not _DONE and a[col_a] == match_key:
                group_a.append(a)
                a = next(iter_a, _DONE)
            # emite el producto cartesiano streamando el lado derecho
            while b is not _DONE and b[col_b] == match_key:
                for ra in group_a:
                    yield {**ra, **b}
                b = next(iter_b, _DONE)
        elif ka < kb:
            a = next(iter_a, _DONE)
        else:
            b = next(iter_b, _DONE)


# hash join: construye hash table del lado izquierdo, probe con el derecho en O(1) promedio
def hash_join(
    iter_a: Iterator[Dict],
    iter_b: Iterator[Dict],
    col_a: str,
    col_b: str,
) -> Iterator[Dict]:
    # fase build: agrupa rows de iter_a por clave en un dict de listas
    build_map: Dict[Any, List[Dict]] = {}
    for row in iter_a:
        key = row[col_a]
        build_map.setdefault(key, []).append(row)

    # fase probe: stream de iter_b, cada row busca su clave en el map
    for row_b in iter_b:
        key = row_b[col_b]
        for row_a in build_map.get(key, []):
            yield {**row_a, **row_b}
