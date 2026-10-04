"""FastAPI HTTP application wiring the service to REST endpoints."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from codeview.errors import ServiceError
from codeview.service import CodeViewService
from codeview.textutil import decode_source


class AnalyzeRequest(BaseModel):
    language: str = Field(..., description="target language id")
    content: str | None = Field(None, description="source text (UTF-8)")
    content_base64: str | None = Field(
        None, description="raw source bytes; used to detect illegal encoding"
    )


class EditRequest(BaseModel):
    language: str
    session_id: str | None = None
    old_content: str | None = None
    new_content: str | None = None
    new_content_base64: str | None = None
    edit_start: int = Field(..., ge=0)
    edit_end_old: int = Field(..., ge=0)
    edit_end_new: int = Field(..., ge=0)


def _decode_body(req: AnalyzeRequest | EditRequest, *, field: str = "content") -> str:
    """Extract source text, surfacing illegal encodings as a precise error."""
    b64_field = "content_base64" if field == "content" else "new_content_base64"
    raw_b64 = getattr(req, b64_field, None)
    if raw_b64 is not None:
        import base64

        from codeview.models import Reason

        try:
            raw = base64.b64decode(raw_b64, validate=True)
        except ValueError:
            raise ServiceError(
                Reason.ILLEGAL_ENCODING,
                "content_base64 is not valid base64",
                http_status=400,
            )
        text, reason = decode_source(raw)
        if reason is not None:
            raise ServiceError(
                Reason.ILLEGAL_ENCODING,
                "source bytes are not valid UTF-8",
                http_status=400,
            )
        return text
    value = getattr(req, field, None)
    return value if value is not None else ""


def create_app(service: CodeViewService | None = None) -> FastAPI:
    svc = service or CodeViewService()
    app = FastAPI(title="codeview", version="0.1.0")
    app.state.service = svc

    @app.exception_handler(ServiceError)
    async def _service_error_handler(request: Request, exc: ServiceError):
        return JSONResponse(status_code=exc.http_status, content=exc.to_dict())

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "languages": svc.registry.ids(),
            "enabled_languages": sorted(svc.settings.languages),
            "max_file_size": svc.settings.max_file_size,
        }

    @app.post("/analyze")
    async def analyze(req: AnalyzeRequest):
        text = _decode_body(req)
        return await svc.analyze_async(req.language, text)

    @app.post("/edit")
    async def edit(req: EditRequest):
        new_text = _decode_body(req, field="new_content")
        return await svc.apply_edit_async(
            req.language,
            new_text,
            req.edit_start,
            req.edit_end_old,
            req.edit_end_new,
            req.old_content,
            req.session_id,
        )

    return app
