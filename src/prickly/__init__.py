"""Prickly: talk to a smart home through Cactus Whistle and Needle."""

import os


def main() -> None:
    import uvicorn

    uvicorn.run(
        "prickly.main:app",
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
        reload=os.environ.get("PRICKLY_RELOAD") == "1",
    )
