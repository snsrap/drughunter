"""One option an external tool takes, declared as data.

Every adapter -- a pocket detector, a pose scorer -- publishes its options
this way (``pocketbench detectors --json``), so a program driving pocketbench
can check a request against them before starting a run. ProteinViewer's server
does, to answer a bad value at once and to key its cache on the options in
full.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .errors import BadInput

__all__ = ["Option"]


@dataclass(frozen=True)
class Option:
    """One option a tool takes: a pick from ``choices``, or an integer
    between ``lo`` and ``hi``.
    """

    name: str
    default: Any
    choices: tuple[str, ...] = ()
    lo: int | None = None
    hi: int | None = None

    def check(self, raw: Mapping[str, Any]) -> Any:
        """The value ``raw`` gives, checked; absent, null or empty means the
        default.
        """
        v = raw.get(self.name)
        if v is None or v == "":
            return self.default
        if self.choices:
            v = str(v).strip().lower()
            if v not in self.choices:
                raise BadInput(
                    "unknown %s %r (want %s)" % (self.name, v, " or ".join(self.choices))
                )
            return v
        try:
            f = float(v)
        except (TypeError, ValueError):
            raise BadInput("%s must be a number, not %r" % (self.name, v)) from None
        if isinstance(v, bool) or f != int(f):
            raise BadInput("%s must be a whole number" % self.name)
        if (self.lo is not None and f < self.lo) or (self.hi is not None and f > self.hi):
            raise BadInput("%s must be between %s and %s" % (self.name, self.lo, self.hi))
        return int(f)

    def as_dict(self) -> dict:
        if self.choices:
            return {"default": self.default, "choices": list(self.choices)}
        return {"default": self.default, "integer": True, "min": self.lo, "max": self.hi}
