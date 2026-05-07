#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path
from datetime import datetime
    
_ROOT = Path(__file__).resolve().parent.parent
METRICS_CSV_DEFAULT = _ROOT / "results" / "metrics.csv"
TEMPLATE_DEFAULT = _ROOT / "docs" / "report_template.md"


def generate_report(
    metrics_csv_path: Path | str | None = None,
    template_path: Path | str | None = None,
    output_path: Path | str | None = None,
) -> str:
    """
    Lee metrics.csv y report_template.md para generar el informe final.
    TODO: implementar sustitución de placeholders y generación de gráficos.
    """
    metrics_csv_path = Path(metrics_csv_path) if metrics_csv_path is not None else METRICS_CSV_DEFAULT
    template_path = Path(template_path) if template_path is not None else TEMPLATE_DEFAULT

    print("[report_generator] Skeleton. Implementación completa en commit posterior.")
    print(f"  metrics_csv_path = {metrics_csv_path}")
    print(f"  template_path = {template_path}")

    if not template_path.exists():
        sys.exit(f"[ERROR] No se encontró la plantilla: {template_path}")

    if not metrics_csv_path.exists():
        print(f"[WARN] metrics.csv no encontrado en: {metrics_csv_path}")

    # Placeholder: por ahora se retorna el path de la plantilla como dato de salida.
    return str(template_path)


if __name__ == "__main__":
    generate_report()
