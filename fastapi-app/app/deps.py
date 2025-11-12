"""FastAPI dependency injection helpers."""

from __future__ import annotations

from functools import lru_cache

from src.storage import DatabaseStorage
from .config import METADATA_DATABASE_URL, METADATA_DB_PATH


@lru_cache(maxsize=1)
def get_storage() -> DatabaseStorage:
    """Return a singleton instance of the storage backend."""

    storage = DatabaseStorage(METADATA_DATABASE_URL, METADATA_DB_PATH)
    return storage


__all__ = ["get_storage"]
