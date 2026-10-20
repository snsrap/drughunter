"""What can go wrong, by whose problem it is.

The command line turns each into its own exit code (cli.py), so a program
driving pocketbench -- ProteinViewer's server does -- can tell a bad input
from a tool that is not installed from a tool that ran and failed.
"""

from __future__ import annotations

__all__ = ["PocketbenchError", "BadInput", "ToolError", "ToolUnavailable", "ToolFailed"]


class PocketbenchError(Exception):
    """Base for every error whose message is meant for the user."""


class BadInput(PocketbenchError, ValueError):
    """The input cannot be used: no atoms, nothing left to search, an option
    out of range.
    """


class ToolError(PocketbenchError):
    """An external program is involved. ``tool`` names it."""

    def __init__(self, message: str, tool: str = ""):
        super().__init__(message)
        self.tool = tool


class ToolUnavailable(ToolError):
    """The external program is not installed, or cannot do what was asked."""


class ToolFailed(ToolError):
    """The external program ran and failed, timed out, or wrote nothing usable."""
