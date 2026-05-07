from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


# interfaz abstracta para todos los índices
class BaseIndex(ABC):

    # insert de un record en el índice
    @abstractmethod
    def add(self, data_dict: Dict[str, Any]) -> None:
        ...

    # busca por primary key, retorna None si no existe
    @abstractmethod
    def search(self, key: Any) -> Optional[Dict[str, Any]]:
        ...

    # elimina todos los records con esa key, retorna count removido
    @abstractmethod
    def remove(self, key: Any) -> int:
        ...

    # merge de main + overflow y reescritura ordenada
    @abstractmethod
    def reorganize(self) -> None:
        ...

    # retorna records con start_key <= key <= end_key; índices sin soporte retornan []
    def range_search(self, start_key: Any, end_key: Any) -> List[Dict[str, Any]]:
        return []
