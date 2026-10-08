"""FastAPI HTTP layer.

Endpoints:
- POST /analyze?language=<name>   body = raw source bytes -> AnalysisResult
- POST /edit                      JSON {language, old_b64, edit, new_b64}
                                  -> IncrementalResult
- GET  /languages                 -> registered language names
- GET  /config                    -> effective configuration
"""
from __future__ import annotations

import base64
from dataclasses import asdict
from enum import Enum
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.config import ServiceConfig
from app.errors import ServiceError
from app.languages.registry import default_registry
from app.models import Position, TextEdit
from app.service import CodeService


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "__dataclass_fields__"):
        return {k: _jsonable(v) for k, v in asdict(value).items()}
    return value


class PositionModel(BaseModel):
    line: int
    col: int


class EditModel(BaseModel):
    start: PositionModel
    end: PositionModel
    new_text: str


class EditRequest(BaseModel):
    language: str
    old_b64: str
    edit: EditModel
    new_b64: str


def create_app(
    config: ServiceConfig | None = None, service: CodeService | None = None
) -> FastAPI:
    if service is None:
        service = CodeService(config or ServiceConfig(), default_registry())
    app = FastAPI(title="code-highlight-outline-service")
    app.state.service = service

    @app.exception_handler(ServiceError)
    async def service_error_handler(_req: Request, exc: ServiceError):
        return JSONResponse(
            status_code=400,
            content={"error": {"code": exc.code, "message": str(exc)}},
        )

    @app.post("/analyze")
    async def analyze(request: Request, language: str = Query(...)):
        data = await request.body()
        result = await service.analyze(language, data)
        return _jsonable(result)

    @app.post("/edit")
    async def edit(req: EditRequest):
        old = base64.b64decode(req.old_b64)
        new = base64.b64decode(req.new_b64)
        text_edit = TextEdit(
            start=Position(req.edit.start.line, req.edit.start.col),
            end=Position(req.edit.end.line, req.edit.end.col),
            new_text=req.edit.new_text,
        )
        result = await service.apply_edit(req.language, old, text_edit, new)
        return _jsonable(result)

    @app.get("/languages")
    async def languages():
        enabled = service._config.languages
        if enabled is None:
            names = service._registry.names()
        else:
            # only registered AND enabled languages are usable
            names = sorted(n for n in service._registry.names() if n in enabled)
        return {"languages": names}

    @app.get("/config")
    async def get_config():
        cfg = service._config
        return {
            "languages": sorted(cfg.languages) if cfg.languages else None,
            "cache_capacity": cfg.cache_capacity,
            "max_file_size": cfg.max_file_size,
            "timeout_seconds": cfg.timeout_seconds,
        }

    return app
