"""Configuration loading for the service."""

from __future__ import annotations

import os

from codeview.models import Settings

DEFAULT_LANGUAGES = ("python", "json", "plaintext")
DEFAULT_MAX_FILE_SIZE = 2_000_000
DEFAULT_CACHE_CAPACITY = 256
DEFAULT_SESSION_CAPACITY = 1024
DEFAULT_TIMEOUT_MS = 5000


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= 0 else default


def load_settings(
    languages: tuple[str, ...] | None = None,
    max_file_size: int | None = None,
    cache_capacity: int | None = None,
    session_capacity: int | None = None,
    timeout_ms: int | None = None,
) -> Settings:
    """Build settings, falling back to env vars then built-in defaults.

    Environment variables: ``CODEVIEW_LANGUAGES`` (comma separated),
    ``CODEVIEW_MAX_FILE_SIZE``, ``CODEVIEW_CACHE_CAPACITY``,
    ``CODEVIEW_SESSION_CAPACITY``, ``CODEVIEW_TIMEOUT_MS``.
    """
    if languages is None:
        env_langs = os.environ.get("CODEVIEW_LANGUAGES")
        languages = (
            tuple(x.strip() for x in env_langs.split(",") if x.strip())
            if env_langs
            else DEFAULT_LANGUAGES
        )
    return Settings(
        languages=frozenset(languages),
        max_file_size=max_file_size
        if max_file_size is not None
        else _env_int("CODEVIEW_MAX_FILE_SIZE", DEFAULT_MAX_FILE_SIZE),
        cache_capacity=cache_capacity
        if cache_capacity is not None
        else _env_int("CODEVIEW_CACHE_CAPACITY", DEFAULT_CACHE_CAPACITY),
        session_capacity=session_capacity
        if session_capacity is not None
        else _env_int("CODEVIEW_SESSION_CAPACITY", DEFAULT_SESSION_CAPACITY),
        timeout_ms=timeout_ms
        if timeout_ms is not None
        else _env_int("CODEVIEW_TIMEOUT_MS", DEFAULT_TIMEOUT_MS),
    )
