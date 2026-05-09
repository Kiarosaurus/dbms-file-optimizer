from __future__ import annotations

import glob
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))

# directorios que deben existir antes de que la GUI pueda correr
_REQUIRED_DIRS = [
    os.path.join(_HERE, "workspaces"),
    os.path.join(_HERE, "workspaces", "default_testing"),
    os.path.join(_HERE, "docs", "charts"),
    os.path.join(_HERE, "results"),
]

DEFAULT_WORKSPACE = os.path.join(_HERE, "workspaces", "default_testing")


def ensure_dirs() -> None:
    # crea los directorios del proyecto si no existen
    for d in _REQUIRED_DIRS:
        if not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
            print(f"[run_all] Creado: {os.path.relpath(d, _HERE)}/")


def clean_data() -> int:
    # elimina archivos bin,json y log del workspace default_testing
    if not os.path.isdir(DEFAULT_WORKSPACE):
        print("[run_all] Workspace default_testing no encontrado, saltando limpieza.")
        return 0
    removed = 0
    for pattern in ("*.bin", "*.json", "*.log"):
        for path in glob.glob(os.path.join(DEFAULT_WORKSPACE, pattern)):
            os.remove(path)
            print(f"  eliminado: {os.path.basename(path)}")
            removed += 1
    print(f"[run_all] {removed} archivo(s) eliminado(s) de default_testing/\n")
    return removed


def launch_gui():
    # agrega la raiz del proyecto al path y lanza la app principal
    sys.path.insert(0, _HERE)
    from gui.app import main
    main()


if __name__ == "__main__":
    ensure_dirs()
    keep = "--keep" in sys.argv
    if not keep:
        clean_data()
    else:
        print("[run_all] Flag --keep: saltando limpieza.\n")
    launch_gui()
