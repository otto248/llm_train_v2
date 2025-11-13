"""FastAPI dependency injection helpers."""

from __future__ import annotations

from functools import lru_cache

from src.storage import FileStorage
from .config import METADATA_STORE_PATH


@lru_cache(maxsize=1)
def get_storage() -> FileStorage:
    """Return a singleton instance of the storage backend."""

    storage = FileStorage(METADATA_STORE_PATH)
    return storage


__all__ = ["get_storage"]
