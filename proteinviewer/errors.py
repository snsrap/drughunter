"""Errors the viewer reports to its client.

Each class names what went wrong, not how it is sent: the HTTP status for each
lives in ``web.py``, so nothing below the web layer knows about HTTP. Anything
that is not one of these is a bug, and the web layer reports it as one.
"""

from __future__ import annotations

__all__ = [
    "ViewerError",
    "BadRequest",
    "PayloadTooLarge",
    "NotFound",
    "Conflict",
    "ToolUnavailable",
    "ToolFailed",
]


class ViewerError(Exception):
    """Base for every error whose message is meant for the user."""


class BadRequest(ViewerError):
    """The request is malformed, or asks for something that cannot be."""


class PayloadTooLarge(BadRequest):
    """The request body is over the server's limit."""


class NotFound(ViewerError):
    """No such structure, job or endpoint."""


class Conflict(ViewerError):
    """The request is valid, but not in the current state -- e.g. generation
    asked for before any pocket run on that structure.
    """


class ToolError(ViewerError):
    """An external program is involved. ``tool`` names it."""

    def __init__(self, message: str, tool: str = ""):
        super().__init__(message)
        self.tool = tool


class ToolUnavailable(ToolError):
    """The external program is not installed, or cannot do what was asked."""


class ToolFailed(ToolError):
    """The external program ran and failed, timed out, or wrote nothing usable."""
