"""持久化适配器。"""

from .json_store import JsonFileRepository
from .memory import InMemoryRepository

__all__ = ["InMemoryRepository", "JsonFileRepository"]
