"""Application logging initialisation."""

from __future__ import annotations

import logging
from logging.config import dictConfig


DEFAULT_LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {
            "format": "%(levelname)s [%(asctime)s] %(name)s: %(message)s",
        }
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "default",
        }
    },
    "root": {
        "level": "INFO",
        "handlers": ["console"],
    },
}


def setup_logging(config: dict | None = None) -> None:
    """Configure the logging system for the service."""

    dictConfig(config or DEFAULT_LOGGING_CONFIG)


__all__ = ["setup_logging"]
