"""Violet — Python client for the Violet Messages API.

    from violet import Violet
    client = Violet(workspace_id="ws_…")        # Auth0 default; or api_key="vio_sk_…"
    msg = client.messages.create(
        model="Violet Test Model", max_tokens=64,  # gateway sets the model
        messages=[{"role": "user", "content": "Say hi"}],
    )
    print(msg.content[0].text)

See README for the full surface map. `model=` is required by the API shape,
but the gateway sets the served model.
"""
from .auth import Authenticator
from .client import Violet
from ._async import AsyncViolet
from .errors import (
    APIConnectionError,
    APIError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    AuthError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    UnprocessableEntityError,
    VioletError,
)
from .types import (
    ContentBlock,
    Event,
    Message,
    TextBlock,
    ThinkingBlock,
    TokenCount,
    ToolUseBlock,
    Usage,
    UsageReport,
)

__all__ = [
    # clients
    "Violet",
    "AsyncViolet",
    "Authenticator",
    # objects
    "Message",
    "ContentBlock",
    "TextBlock",
    "ThinkingBlock",
    "ToolUseBlock",
    "Usage",
    "TokenCount",
    "Event",
    "UsageReport",
    # errors
    "VioletError",
    "AuthError",
    "APIError",
    "APIStatusError",
    "APIConnectionError",
    "APITimeoutError",
    "BadRequestError",
    "AuthenticationError",
    "PermissionDeniedError",
    "NotFoundError",
    "UnprocessableEntityError",
    "RateLimitError",
    "InternalServerError",
]

__version__ = "0.1.0"
