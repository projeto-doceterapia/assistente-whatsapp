from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

from app import __version__
from app.logging import configure_logging


class HealthResponse(BaseModel):
    status: str
    version: str


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="orquestrador", version=__version__)

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(status="ok", version=__version__)

    return app


app = create_app()
