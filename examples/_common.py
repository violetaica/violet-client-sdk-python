"""Shared helpers for the Violet SDK samples.

Every sample imports DEFAULT_MODEL + make_client from here so they stay
consistent. Prereqs for all samples:

  * A provisioned Violet workspace and a one-time browser login on first call
    (or `VIOLET_API_KEY`).

These are ORIGINAL implementations of common LLM-app patterns built against the
Violet SDK — not copied from third-party sample repos.
"""
import os

from violet import Violet

# Placeholder only — `messages.create` requires a `model=` argument,
# but the Violet gateway chooses the served model. The client cannot set it.
DEFAULT_MODEL = os.environ.get("MESSAGES_TEST_MODEL", "Violet Test Model")

PREREQS = (
    "Prereqs: paid workspace + Auth0 login or `VIOLET_API_KEY`. "
    "The gateway sets the model (client `model=` is ignored)."
)


def make_client(**kwargs) -> Violet:
    """Construct a Violet client with sample-friendly defaults."""
    return Violet(**kwargs)
