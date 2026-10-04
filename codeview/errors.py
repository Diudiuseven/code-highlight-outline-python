"""Service-level errors mapped to distinguishable reason codes."""

from __future__ import annotations

from codeview.models import Reason


class ServiceError(Exception):
    """A request that cannot be served.

    ``http_status`` is the suggested HTTP status; ``range_dict`` carries an
    optional precise line/column location.
    """

    def __init__(
        self,
        reason: Reason,
        message: str,
        http_status: int = 400,
        range_dict: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.http_status = http_status
        self.range_dict = range_dict

    def to_dict(self) -> dict:
        d = {"status": "error", "reason": self.reason.value, "message": self.message}
        if self.range_dict is not None:
            d["range"] = self.range_dict
        return d
