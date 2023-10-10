from typing import Optional, Dict, Any
from abc import ABC, abstractmethod


class SessionStorage(ABC):
    @abstractmethod
    def get(self, key: str) -> Optional[Dict[str, Any]]:
        """Get some data associated to a key"""

    @abstractmethod
    def set(self, key: str, data: Dict[str, Any], ttl_sec: int) -> None:
        """Set some data associated to a key"""

    def set_protocol(self, connection_id: str, protocol: str, ttl_sec: int) -> None:
        self.set(f"{connection_id}-protocol", {"protocol": protocol}, ttl_sec)

    def get_protocol(self, connection_id: str) -> Optional[str]:
        data = self.get(f"{connection_id}-protocol")
        if data:
            return data.get("protocol", None)
        return None

    def set_connection_params(
        self, connection_id: str, connection_params: Dict[str, Any], ttl_sec: int
    ) -> None:
        self.set(f"{connection_id}-params", connection_params, ttl_sec)

    def get_connection_params(self, connection_id: str) -> Optional[Dict[str, Any]]:
        return self.get(f"{connection_id}-params")
