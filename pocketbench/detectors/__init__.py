"""Pocket detectors: one interface, any number of tools behind it.

A detector takes a :class:`~pocketbench.structure.Structure` and returns a
:class:`~pocketbench.pockets.DetectionResult` whose pockets are resolved to
residues with :func:`~pocketbench.pockets.select_residues`. Everything that
differs between tools -- how they are found, what options they take, what
extra numbers they report -- stays inside the detector.

Each detector declares its options as data (:class:`Option`), so a program
driving pocketbench through ``pocketbench detectors --json`` can check a
request against them before starting a run -- ProteinViewer's server does,
to answer a bad value at once and to key its cache on the options in full.

Adding a detector
-----------------
Subclass :class:`PocketDetector` in a module of this package and add an
instance in :func:`default_detectors`. ``pocketbench detect <name>`` runs it,
and ProteinViewer offers it once its page has an ``<option>`` for it.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

from ..errors import BadInput
from ..pockets import DEFAULT_CUTOFF, DetectionResult
from ..structure import Structure

__all__ = ["Option", "PocketDetector", "DetectorRegistry", "default_detectors"]


@dataclass(frozen=True)
class Option:
    """One option a detector takes: a pick from ``choices``, or an integer
    between ``lo`` and ``hi``."""
    name: str
    default: Any
    choices: tuple[str, ...] = ()
    lo: int | None = None
    hi: int | None = None

    def check(self, raw: Mapping[str, Any]) -> Any:
        """The value ``raw`` gives, checked; absent, null or empty means the
        default."""
        v = raw.get(self.name)
        if v is None or v == "":
            return self.default
        if self.choices:
            v = str(v).strip().lower()
            if v not in self.choices:
                raise BadInput("unknown %s %r (want %s)" % (self.name, v, " or ".join(self.choices)))
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


class PocketDetector(ABC):
    """One pocket-detection tool."""

    #: The name a run asks for it by.
    name: ClassVar[str]
    #: The options it takes beyond the cutoff and the ligands to delete.
    OPTIONS: ClassVar[tuple[Option, ...]] = ()

    @property
    @abstractmethod
    def available(self) -> bool:
        """Whether the tool can be run at all."""

    @abstractmethod
    def describe(self) -> dict:
        """JSON status: at least ``available``."""

    @abstractmethod
    def status_line(self) -> str:
        """One line saying what this installation can do."""

    def options(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        """This detector's options from ``raw``, checked and with defaults
        filled in. Unknown keys are left alone: ``raw`` may be a whole request."""
        return {o.name: o.check(raw) for o in self.OPTIONS}

    @abstractmethod
    def run(self, structure: Structure, workdir: str, *,
            cutoff: float = DEFAULT_CUTOFF,
            ligand_resnames: frozenset[str] = frozenset(),
            **options: Any) -> DetectionResult:
        """Detect pockets in ``structure``, writing scratch files under
        ``workdir``. ``options`` are what :meth:`options` returned."""

    # -- shared by the implementations ------------------------------------
    @staticmethod
    def write_receptor(structure: Structure, workdir: str, filename: str,
                       ligand_resnames: frozenset[str]) -> tuple[Structure, str]:
        """Derive the receptor and write it as all-``ATOM`` PDB.

        Done here, inside every detector, so no caller can forget it: solvent
        and additives go, a named ligand goes, and hetero atoms are promoted
        (fpocket silently ignores ``HETATM``, so a cofactor left as hetero is
        detected as empty cavity).
        """
        receptor = structure.receptor(drop_extra=ligand_resnames)
        if len(receptor) == 0:
            raise BadInput("nothing left to search after removing solvent")
        os.makedirs(workdir, exist_ok=True)
        path = os.path.join(workdir, filename)
        with open(path, "w") as fh:
            fh.write(receptor.to_pdb())
        return receptor, path


class DetectorRegistry:
    """Detectors by name, in the order registered."""

    def __init__(self, detectors: Iterable[PocketDetector] = ()):
        self._by_name: dict[str, PocketDetector] = {}
        for d in detectors:
            self.register(d)

    def register(self, detector: PocketDetector) -> None:
        if detector.name in self._by_name:
            raise ValueError("detector %r registered twice" % detector.name)
        self._by_name[detector.name] = detector

    def get(self, name: str) -> PocketDetector:
        try:
            return self._by_name[name]
        except KeyError:
            raise BadInput("unknown method %r (want %s)"
                           % (name, " or ".join(self._by_name))) from None

    def names(self) -> list[str]:
        return list(self._by_name)

    def __iter__(self):
        return iter(self._by_name.values())

    def describe(self) -> dict[str, dict]:
        """Each detector's status, its options and its one-line summary."""
        return {d.name: {**d.describe(), "options": {o.name: o.as_dict() for o in d.OPTIONS},
                         "status": d.status_line()} for d in self}


def default_detectors(fpocket_bin: str | None = None,
                      lacuna_bin: str | None = None) -> DetectorRegistry:
    """Every detector pocketbench offers. A binary given here wins over
    ``FPOCKET_BIN`` / ``LACUNA_BIN``, which win over where each tool usually
    lives."""
    from .fpocket import FpocketDetector
    from .lacuna import LacunaDetector
    return DetectorRegistry([
        FpocketDetector(fpocket_bin or os.environ.get("FPOCKET_BIN")),
        LacunaDetector(lacuna_bin or os.environ.get("LACUNA_BIN")),
    ])
