"""What can go wrong, by whose problem it is.

The command line turns each into its own exit code (cli.py), so a program
driving ligandbench -- ProteinViewer's server does -- can tell a file that
cannot be used from an installation that cannot run.
"""

from __future__ import annotations

__all__ = ["LigandbenchError", "BadInput", "ToolUnavailable"]


class LigandbenchError(Exception):
    """Base for every error whose message is meant for the user."""


class BadInput(LigandbenchError, ValueError):
    """The file or the choices made about it cannot be used: a ragged CSV, a
    column that holds no molecules, a target with a single class.
    """


class ToolUnavailable(LigandbenchError):
    """RDKit is not importable here."""
