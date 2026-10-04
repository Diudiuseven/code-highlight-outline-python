"""Service configuration."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ServiceConfig:
    """Tunable limits. `languages=None` means every registered language."""
    languages: frozenset[str] | None = None
    cache_capacity: int = 256
    max_file_size: int = 2 * 1024 * 1024  # bytes
    timeout_seconds: float = 10.0
