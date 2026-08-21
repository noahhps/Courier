"""`python -m app` -- start the server and print how to reach it."""

from __future__ import annotations

import uvicorn

from .config import load_settings
from .main import create_app


def main() -> None:
    settings = load_settings()
    app = create_app(settings)

    print()
    print(f"  unified-llm on http://{settings.bind_host}:{settings.bind_port}")
    print(f"  token: {settings.auth_token}")
    print(f"  db:    {settings.db_path}")
    print(f"  model: {settings.ollama_model} via {settings.ollama_url}")
    if settings.bind_host in ("127.0.0.1", "localhost"):
        print()
        print("  Bound to loopback -- phone and laptop can't reach this yet.")
        print("  Set BIND_HOST to an address they can dial to let them in.")
    print()

    uvicorn.run(app, host=settings.bind_host, port=settings.bind_port, log_level="info")


if __name__ == "__main__":
    main()
