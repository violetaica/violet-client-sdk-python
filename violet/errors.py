"""HTTP / API error hierarchy for the Violet client.

    VioletError                      (base; alias: nothing)
      └─ APIError                    (base for everything from the API layer)
           ├─ APIConnectionError     (network failed before a response)
           │     └─ APITimeoutError
           └─ APIStatusError         (a non-2xx HTTP response)
                 ├─ BadRequestError            400
                 ├─ AuthenticationError        401
                 ├─ PermissionDeniedError      403
                 ├─ NotFoundError              404
                 ├─ UnprocessableEntityError   422
                 ├─ RateLimitError             429
                 └─ InternalServerError        >= 500

`APIError` is exported as itself; catch most-specific-first. Each status error
also surfaces Violet's machine-readable `.code` (e.g. "user_not_provisioned",
"subscription_required").
"""
from __future__ import annotations

from typing import Any, Optional


class VioletError(Exception):
    """Base class for every error raised by this SDK."""


class AuthError(VioletError):
    """Auth0 token acquisition/refresh failed (login flow), distinct from a
    gateway HTTP 401 (`AuthenticationError`)."""


class APIError(VioletError):
    """Base for errors originating from the API layer."""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        body: Any = None,
        request_id: Optional[str] = None,
    ):
        self.message = message
        self.status_code = status_code
        self.status = status_code  # alias of status_code
        self.body = body
        self.request_id = request_id
        self.code = body.get("code") if isinstance(body, dict) else None
        self.type = self._error_type(body)
        super().__init__(message)

    @staticmethod
    def _error_type(body: Any) -> Optional[str]:
        if isinstance(body, dict):
            err = body.get("error")
            if isinstance(err, dict) and err.get("type"):
                return err["type"]
            if body.get("type") == "error":
                return body.get("type")
            if body.get("code"):
                return body["code"]
        return None


class APIConnectionError(APIError):
    """The request could not reach the gateway."""

    def __init__(self, message: str = "Connection error.", *, request_id: Optional[str] = None):
        super().__init__(message, request_id=request_id)


class APITimeoutError(APIConnectionError):
    """The request timed out."""

    def __init__(self, message: str = "Request timed out.", *, request_id: Optional[str] = None):
        super().__init__(message, request_id=request_id)


class APIStatusError(APIError):
    """The gateway returned a non-2xx HTTP status."""

    status_code: int = 0  # overridden per subclass

    def __init__(self, message: str, *, status_code: int, body: Any = None, request_id: Optional[str] = None):
        super().__init__(message, status_code=status_code, body=body, request_id=request_id)


class BadRequestError(APIStatusError):
    pass


class AuthenticationError(APIStatusError):
    pass


class PermissionDeniedError(APIStatusError):
    pass


class NotFoundError(APIStatusError):
    pass


class UnprocessableEntityError(APIStatusError):
    pass


class RateLimitError(APIStatusError):
    pass


class InternalServerError(APIStatusError):
    pass


_STATUS_MAP = {
    400: BadRequestError,
    401: AuthenticationError,
    403: PermissionDeniedError,
    404: NotFoundError,
    422: UnprocessableEntityError,
    429: RateLimitError,
}


def _message_from_body(status: int, body: Any) -> str:
    code = body.get("code") if isinstance(body, dict) else None
    msg = None
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            msg = err.get("message")
        elif isinstance(err, str):
            msg = err
        elif body.get("type") == "error":
            msg = str(body.get("message") or "")
    if not msg:
        msg = str(body)[:300] if body else f"HTTP {status}"
    return f"{code}: {msg}" if code and code not in msg else msg


def error_from_status(status: int, body: Any, request_id: Optional[str]) -> APIStatusError:
    """Map an HTTP status + parsed body to the matching exception class."""
    message = _message_from_body(status, body)
    cls = _STATUS_MAP.get(status)
    if cls is None:
        cls = InternalServerError if status >= 500 else APIStatusError
    return cls(message, status_code=status, body=body, request_id=request_id)
