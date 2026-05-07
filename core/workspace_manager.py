from __future__ import annotations

import json
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKSPACES_ROOT = os.path.join(_ROOT, "workspaces")
# gestiona workspaces aislados bajo el directorio raiz workspaces
class WorkspaceManager:
    # inicializa el root de workspaces y lo crea si no existe
    def __init__(self, workspaces_root: str = WORKSPACES_ROOT):
        self.root = workspaces_root
        os.makedirs(self.root, exist_ok=True)
    # lista los subdirectorios existentes dentro de workspaces
    def get_projects(self) -> list[str]:
        return sorted(
            name for name in os.listdir(self.root)
            if os.path.isdir(os.path.join(self.root, name))
        )
    # devuelve la ruta absoluta del directorio de un proyecto
    def get_project_path(self, project_name: str) -> str:
        return os.path.join(self.root, project_name)
    # crea el directorio del proyecto y un catalog.json vacio si no existen
    def ensure_project(self, project_name: str) -> str:
        path = os.path.join(self.root, project_name)
        if not os.path.isdir(path):
            os.makedirs(path, exist_ok=True)
        catalog = os.path.join(path, "catalog.json")
        if not os.path.exists(catalog):
            with open(catalog, "w") as f:
                json.dump({"tables": {}}, f, indent=2)
        return path
    # garantiza que el workspace default_testing este listo antes de usarlo
    def ensure_default(self) -> str:
        return self.ensure_project("default_testing")
