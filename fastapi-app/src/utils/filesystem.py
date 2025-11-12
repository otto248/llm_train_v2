"""Filesystem helper utilities."""

from __future__ import annotations

from pathlib import Path


def ensure_directories(*paths: Path | str) -> None:
    """Ensure that the provided directories exist."""

    for path in paths:
        if path is None:
            continue
        p = Path(path)
        p.mkdir(parents=True, exist_ok=True)


def resolve_under_base(base: Path, relative: str) -> Path:
    """Resolve *relative* under *base* ensuring it does not escape."""

    base = Path(base).resolve()
    candidate = (base / relative).resolve()
    candidate.relative_to(base)  # Raises ValueError if outside base
    return candidate


__all__ = ["ensure_directories", "resolve_under_base"]
