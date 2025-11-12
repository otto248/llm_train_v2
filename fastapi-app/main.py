"""Application entry point for running with `python -m`."""

from __future__ import annotations

import uvicorn

from app import create_app


def main() -> None:
    app = create_app()
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
