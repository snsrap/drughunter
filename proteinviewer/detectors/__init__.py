"""Pocket detectors: one interface, any number of tools behind it.

A detector takes a :class:`~proteinviewer.structure.Structure` and returns a
:class:`~proteinviewer.pockets.DetectionResult` whose pockets are resolved to
residues with :func:`~proteinviewer.pockets.select_residues`. Everything that
differs between tools -- how they are found, what options they take, what
extra numbers they report -- stays inside the detector, so the service and the
web layer never branch on which one is running.

Adding a detector
-----------------
Subclass :class:`PocketDetector` in a module of this package, then register an
instance in ``server.build_service``. The viewer's ``/api/status`` lists it
from then on, and ``POST /api/pockets`` with ``{"method": "<name>"}`` runs it.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping
from typing import Any, ClassVar

from ..errors import BadRequest
from ..pockets import DEFAULT_CUTOFF, DetectionResult
from ..structure import Structure

__all__ = ["PocketDetector", "DetectorRegistry"]


class PocketDetector(ABC):
    """One pocket-detection tool."""

    #: The ``method`` a request names it by.
    name: ClassVar[str]

    @property
    @abstractmethod
    def available(self) -> bool:
        """Whether the tool can be run at all."""

    @abstractmethod
    def describe(self) -> dict:
        """JSON status for ``/api/status``: at least ``available``."""

    @abstractmethod
    def status_line(self) -> str:
        """One line for the server's startup banner."""

    def options(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        """Validate and fill in this detector's own request options.

        ``raw`` is the whole request; take what is yours and ignore the rest.
        The result is part of a run's identity -- two runs with different
        options are different results -- so return every option, defaults
        included, and only hashable values.
        """
        return {}

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
            raise BadRequest("nothing left to search after removing solvent")
        os.makedirs(workdir, exist_ok=True)
        path = os.path.join(workdir, filename)
        with open(path, "w") as fh:
            fh.write(receptor.to_pdb())
        return receptor, path


class DetectorRegistry:
    """The detectors a server offers, by name, in the order registered."""

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
            raise BadRequest("unknown method %r (want %s)"
                             % (name, " or ".join(self._by_name))) from None

    def names(self) -> list[str]:
        return list(self._by_name)

    def __iter__(self):
        return iter(self._by_name.values())

    def describe(self) -> dict[str, dict]:
        return {d.name: d.describe() for d in self}
