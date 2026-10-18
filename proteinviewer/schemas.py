"""The API's requests: parsed and checked once, at the edge.

Each request type turns an untrusted JSON payload into typed fields, filling
in defaults and raising :class:`~proteinviewer.errors.BadRequest` for anything
unusable. The service below only ever sees these, never raw dicts.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from . import validate
from .errors import BadRequest
from .pockets import DEFAULT_CUTOFF

__all__ = ["BufferRequest", "PocketRequest", "PrepRequest", "GenerateRequest",
           "JobQuery"]


@dataclass(frozen=True)
class BufferRequest:
    """``POST /api/structure``: hold this structure for later runs."""
    token: str
    data: str
    fmt: str | None

    @classmethod
    def parse(cls, p: Mapping[str, Any]) -> "BufferRequest":
        data = validate.text(p, "data")
        if not data.strip():
            raise BadRequest("no structure data supplied")
        token = validate.text(p, "id") or validate.text(p, "token") or "structure"
        return cls(token, data, validate.text(p, "fmt") or None)


@dataclass(frozen=True)
class PocketRequest:
    """``POST /api/pockets``: detect pockets in a buffered structure.

    ``raw`` is the whole payload, read-only: the chosen detector takes its
    own options from it (see ``PocketDetector.options``).
    """
    token: str
    method: str
    cutoff: float
    ligands: frozenset[str]
    raw: Mapping[str, Any] = field(compare=False, repr=False)

    @classmethod
    def parse(cls, p: Mapping[str, Any]) -> "PocketRequest":
        return cls(
            token=validate.text(p, "token"),
            method=validate.text(p, "method", "fpocket").strip().lower(),
            cutoff=validate.number(p, "cutoff", DEFAULT_CUTOFF, lo=1.0, hi=20.0),
            ligands=validate.names(p, "ligand"),
            raw=MappingProxyType(dict(p)),
        )


@dataclass(frozen=True)
class PrepRequest:
    """``POST /api/prep``: prepare a buffered structure with quickprep."""
    token: str
    ph: float
    # Residues per chain that must not be rebuilt as loops -- see prep.py.
    no_rebuild: Mapping[str, tuple[int, ...]]

    @classmethod
    def parse(cls, p: Mapping[str, Any]) -> "PrepRequest":
        raw = p.get("noRebuild") or {}
        if not isinstance(raw, Mapping):
            raise BadRequest("noRebuild must map chain ids to residue numbers")
        no_rebuild = {}
        for chain, nums in raw.items():
            if isinstance(nums, (str, bytes)) or not isinstance(nums, (list, tuple)):
                raise BadRequest("noRebuild must map chain ids to residue numbers")
            try:
                no_rebuild[str(chain)] = tuple(int(n) for n in nums)
            except (TypeError, ValueError):
                raise BadRequest("noRebuild residue numbers must be integers") from None
        return cls(validate.text(p, "token"),
                   validate.number(p, "ph", 7.0, lo=0.0, hi=14.0),
                   MappingProxyType(no_rebuild))


@dataclass(frozen=True)
class GenerateRequest:
    """``POST /api/generate``: start NEAT-POCKET on pockets of the last run."""
    token: str
    ranks: frozenset[int]
    n: int
    cfg: float
    batch_size: int

    @classmethod
    def parse(cls, p: Mapping[str, Any]) -> "GenerateRequest":
        raw = p.get("ranks") or []
        if not isinstance(raw, (list, tuple)):
            raise BadRequest("ranks must be a list of pocket ranks")
        try:
            ranks = frozenset(int(r) for r in raw)
        except (TypeError, ValueError):
            raise BadRequest("ranks must be a list of pocket ranks") from None
        if not ranks:
            raise BadRequest("no pockets selected")
        return cls(validate.text(p, "token"), ranks,
                   validate.integer(p, "n", 50, lo=1, hi=500),
                   validate.number(p, "cfg", 0.5),
                   validate.integer(p, "batch_size", 50, lo=1, hi=500))


@dataclass(frozen=True)
class JobQuery:
    """``GET /api/job?id=…[&mols=1][&table=1]``."""
    job_id: str
    molecules: bool
    table: bool

    @classmethod
    def parse(cls, q: Mapping[str, str]) -> "JobQuery":
        return cls(q.get("id", ""), q.get("mols") == "1", q.get("table") == "1")
