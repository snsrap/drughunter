"""Coercion of untrusted request values, with errors the user can act on.

Shared by the request schemas and the detectors' own options, so every field
is checked the same way and a bad value is a :class:`BadRequest` naming the
field rather than a ``ValueError`` from deep inside a tool.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .errors import BadRequest

__all__ = ["number", "integer", "choice", "text", "names"]

_MISSING = object()


def _get(payload: Mapping[str, Any], key: str, default: Any) -> Any:
    """The value, or ``default`` when it is absent, null or empty. An empty
    value means "use the default" -- the viewer sends ``""`` for an unset
    field."""
    v = payload.get(key, _MISSING)
    return default if v is _MISSING or v is None or v == "" else v


def number(payload: Mapping[str, Any], key: str, default: float, *,
           lo: float | None = None, hi: float | None = None) -> float:
    v = _get(payload, key, default)
    if isinstance(v, bool):
        raise BadRequest("%s must be a number" % key)
    try:
        v = float(v)
    except (TypeError, ValueError):
        raise BadRequest("%s must be a number, not %r" % (key, v)) from None
    if v != v or v in (float("inf"), float("-inf")):
        raise BadRequest("%s must be a finite number" % key)
    _check_range(key, v, lo, hi)
    return v


def integer(payload: Mapping[str, Any], key: str, default: int, *,
            lo: int | None = None, hi: int | None = None) -> int:
    v = number(payload, key, default)
    if v != int(v):
        raise BadRequest("%s must be a whole number" % key)
    v = int(v)
    _check_range(key, v, lo, hi)
    return v


def _check_range(key: str, v: float, lo: float | None, hi: float | None) -> None:
    if lo is not None and v < lo:
        raise BadRequest("%s must be at least %g" % (key, lo)
                         if hi is None else
                         "%s must be between %g and %g" % (key, lo, hi))
    if hi is not None and v > hi:
        raise BadRequest("%s must be at most %g" % (key, hi)
                         if lo is None else
                         "%s must be between %g and %g" % (key, lo, hi))


def choice(payload: Mapping[str, Any], key: str, options: Iterable[str],
           default: str, *, what: str | None = None) -> str:
    """A case-insensitive pick from ``options``."""
    options = tuple(options)
    v = str(_get(payload, key, default)).strip().lower()
    if v not in options:
        raise BadRequest("unknown %s %r (want %s)"
                         % (what or key, v, " or ".join(options)))
    return v


def text(payload: Mapping[str, Any], key: str, default: str = "") -> str:
    v = _get(payload, key, default)
    if not isinstance(v, str):
        raise BadRequest("%s must be a string" % key)
    return v


def names(payload: Mapping[str, Any], key: str) -> frozenset[str]:
    """A list of residue names: trimmed, upper-cased, blanks dropped."""
    v = _get(payload, key, [])
    if isinstance(v, str) or not isinstance(v, Iterable):
        raise BadRequest("%s must be a list of residue names" % key)
    return frozenset(str(x).strip().upper() for x in v if str(x).strip())
