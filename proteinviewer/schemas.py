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

__all__ = [
    "BufferRequest",
    "PocketRequest",
    "SuperposeRequest",
    "PrepRequest",
    "GenerateRequest",
    "JobQuery",
    "ScoreRequest",
    "ScoreQuery",
    "ProjectRequest",
    "ProjectSaveRequest",
    "DatasetUploadRequest",
    "DatasetResolveRequest",
    "DatasetIdRequest",
    "DatasetRowsRequest",
    "DatasetSummaryRequest",
    "DEFAULT_CUTOFF",
    "DEFAULT_PAGE",
    "MAX_PAGE",
]

#: Residues within this of a site point make the pocket, Å. pocketbench's
#: default too; sent with every run, so the two never have to agree silently.
DEFAULT_CUTOFF = 6.0

#: Rows of a dataset sent at once, by default and at most. A page is what the
#: browser mounts and what gets drawn, so the ceiling is the server's.
DEFAULT_PAGE = 50
MAX_PAGE = 100

TARGET_KINDS = ("regression", "classification")
TRANSFORMS = ("identity", "pki")


@dataclass(frozen=True)
class BufferRequest:
    """``POST /api/structure``: hold this structure for later runs."""

    token: str
    data: str
    fmt: str | None

    @classmethod
    def parse(cls, p: Mapping[str, Any]) -> BufferRequest:
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
    # The session's output directory, if it has one: every request that
    # produces a file may name it, and the artifacts are written there.
    project: str = ""

    @classmethod
    def parse(cls, p: Mapping[str, Any]) -> PocketRequest:
        return cls(
            token=validate.text(p, "token"),
            method=validate.text(p, "method", "fpocket").strip().lower(),
            cutoff=validate.number(p, "cutoff", DEFAULT_CUTOFF, lo=1.0, hi=20.0),
            ligands=validate.names(p, "ligand"),
            raw=MappingProxyType(dict(p)),
            project=validate.text(p, "project"),
        )


def _residues(p: Mapping[str, Any], key: str) -> tuple[tuple[str, int, str], ...]:
    """[[chain, number, insertion code], ...] -- the code may be left off."""
    raw = p.get(key) or []
    if not isinstance(raw, (list, tuple)):
        raise BadRequest("%s must be a list of [chain, number] residues" % key)
    out = []
    for r in raw:
        if not isinstance(r, (list, tuple)) or len(r) not in (2, 3):
            raise BadRequest("%s must be a list of [chain, number] residues" % key)
        chain, num, icode = str(r[0]), r[1], str(r[2]) if len(r) == 3 else ""
        if isinstance(num, bool):
            raise BadRequest("%s residue numbers must be integers" % key)
        try:
            num = int(num)
        except (TypeError, ValueError):
            raise BadRequest("%s residue numbers must be integers" % key) from None
        # These end up in a residue spec on pocketbench's command line:
        # nothing that would end or change one may get through.
        if (
            not chain
            or len(chain) > 4
            or not chain.isalnum()
            or not (icode == "" or icode.isalpha())
            or len(icode) > 1
        ):
            raise BadRequest("%s has an invalid residue %r" % (key, list(r)))
        out.append((chain, num, icode))
    return tuple(out)


@dataclass(frozen=True)
class SuperposeRequest:
    """``POST /api/superpose``: move one buffered structure onto another.

    ``on`` and ``mobileOn`` restrict the fit to residues of the reference and
    of the structure being moved, as ``[chain, number, insertion code]``;
    empty means the whole structure.
    """

    token: str
    reference: str
    on: tuple[tuple[str, int, str], ...]
    mobile_on: tuple[tuple[str, int, str], ...]

    @classmethod
    def parse(cls, p: Mapping[str, Any]) -> SuperposeRequest:
        token, reference = validate.text(p, "token"), validate.text(p, "reference")
        if not token or not reference:
            raise BadRequest("name the structure to move and the reference")
        return cls(token, reference, _residues(p, "on"), _residues(p, "mobileOn"))


@dataclass(frozen=True)
class PrepRequest:
    """``POST /api/prep``: prepare a buffered structure with quickprep."""

    token: str
    ph: float
    # Residues per chain that must not be rebuilt as loops -- see prep.py.
    no_rebuild: Mapping[str, tuple[int, ...]]
    project: str = ""

    @classmethod
    def parse(cls, p: Mapping[str, Any]) -> PrepRequest:
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
        return cls(
            validate.text(p, "token"),
            validate.number(p, "ph", 7.0, lo=0.0, hi=14.0),
            MappingProxyType(no_rebuild),
            validate.text(p, "project"),
        )


@dataclass(frozen=True)
class GenerateRequest:
    """``POST /api/generate``: start NEAT-POCKET on pockets of the last run."""

    token: str
    ranks: frozenset[int]
    n: int
    cfg: float
    batch_size: int
    project: str = ""

    @classmethod
    def parse(cls, p: Mapping[str, Any]) -> GenerateRequest:
        raw = p.get("ranks") or []
        if not isinstance(raw, (list, tuple)):
            raise BadRequest("ranks must be a list of pocket ranks")
        try:
            ranks = frozenset(int(r) for r in raw)
        except (TypeError, ValueError):
            raise BadRequest("ranks must be a list of pocket ranks") from None
        if not ranks:
            raise BadRequest("no pockets selected")
        return cls(
            validate.text(p, "token"),
            ranks,
            validate.integer(p, "n", 50, lo=1, hi=500),
            validate.number(p, "cfg", 0.5),
            validate.integer(p, "batch_size", 50, lo=1, hi=500),
            validate.text(p, "project"),
        )


@dataclass(frozen=True)
class ScoreQuery:
    """``GET /api/score-job?id=…``."""

    job_id: str

    @classmethod
    def parse(cls, q: Mapping[str, str]) -> ScoreQuery:
        return cls(q.get("id", ""))


@dataclass(frozen=True)
class JobQuery:
    """``GET /api/job?id=…[&mols=1][&table=1]``."""

    job_id: str
    molecules: bool
    table: bool

    @classmethod
    def parse(cls, q: Mapping[str, str]) -> JobQuery:
        return cls(q.get("id", ""), q.get("mols") == "1", q.get("table") == "1")


@dataclass(frozen=True)
class ScoreRequest:
    """``POST /api/score-poses``: score a finished job's poses in their pocket.

    ``token`` names the structure whose pocket run the poses were generated
    for: its points set each box. The scorer's own options (the mode, the room
    around the points) are checked against what pocketbench publishes, not
    here.
    """

    job: str
    token: str
    scorer: str
    raw: Mapping[str, Any]
    ranks: frozenset[int] = frozenset()
    project: str = ""

    @classmethod
    def parse(cls, p: Mapping[str, Any]) -> ScoreRequest:
        raw = p.get("ranks") or []
        if not isinstance(raw, (list, tuple)):
            raise BadRequest("ranks must be a list of pocket ranks")
        try:
            ranks = frozenset(int(r) for r in raw)
        except (TypeError, ValueError):
            raise BadRequest("ranks must be a list of pocket ranks") from None
        return cls(
            validate.text(p, "job"),
            validate.text(p, "token"),
            validate.text(p, "scorer"),
            p,
            ranks,
            validate.text(p, "project"),
        )


@dataclass(frozen=True)
class ProjectRequest:
    """``POST /api/project``: start an output directory for this session."""

    name: str

    @classmethod
    def parse(cls, p: Mapping[str, Any]) -> ProjectRequest:
        return cls(validate.text(p, "name"))


@dataclass(frozen=True)
class ProjectSaveRequest:
    """``POST /api/project/save``: copy the project to a path the user gave.

    ``session`` is the viewer's own reopenable state, which only the page
    has; it is written into the project before the copy, so what lands on
    disk includes it.
    """

    project: str
    path: str
    session: Mapping[str, Any] | None
    overwrite: bool

    @classmethod
    def parse(cls, p: Mapping[str, Any]) -> ProjectSaveRequest:
        project = validate.text(p, "project")
        if not project:
            raise BadRequest("no project to save")
        session = p.get("session")
        if session is not None and not isinstance(session, Mapping):
            raise BadRequest("session must be the viewer's state object")
        return cls(
            project,
            validate.text(p, "path"),
            MappingProxyType(dict(session)) if session is not None else None,
            bool(p.get("overwrite")),
        )


@dataclass(frozen=True)
class DatasetUploadRequest:
    """``POST /api/dataset/upload``: keep a CSV, staged until its columns are
    chosen. ``name`` is the file's own name, shown back to the user.
    """

    name: str
    data: str

    @classmethod
    def parse(cls, p: Mapping[str, Any]) -> DatasetUploadRequest:
        data = validate.text(p, "data")
        if not data.strip():
            raise BadRequest("the file is empty")
        name = validate.text(p, "name", "dataset.csv").replace("\\", "/").rsplit("/", 1)[-1]
        return cls(name.strip()[:200] or "dataset.csv", data)


@dataclass(frozen=True)
class DatasetResolveRequest:
    """``POST /api/dataset/resolve``: the choices that turn a staged CSV (or a
    ready dataset, afresh) into a dataset.

    ``config`` is what ligandbench takes: the structure column, and a target --
    or None, for a dataset used only for prediction. Which task a column is for
    and what its qualifier column and units are cannot be inferred, so the
    request says: ``qualifier_column`` must be present, null meaning none.
    """

    dataset_id: str
    config: Mapping[str, Any]

    @classmethod
    def parse(cls, p: Mapping[str, Any]) -> DatasetResolveRequest:
        dataset_id = validate.text(p, "dataset_id")
        structure = validate.text(p, "structure_column")
        if not dataset_id or not structure:
            raise BadRequest("name the dataset and its structure column")
        column = validate.text(p, "target_column")
        if not column:
            return cls(dataset_id, {"structure_column": structure, "target": None})
        kind = validate.text(p, "target_kind")
        if kind not in TARGET_KINDS:
            raise BadRequest("say whether %r is %s" % (column, " or ".join(TARGET_KINDS)))
        if "qualifier_column" not in p:
            raise BadRequest("confirm the qualifier column for %r, or send null for none" % column)
        qualifier = p["qualifier_column"]
        if qualifier is not None and (not isinstance(qualifier, str) or not qualifier):
            raise BadRequest("qualifier_column must be a column name or null")
        target: dict[str, Any] = {
            "column": column,
            "kind": kind,
            "qualifier_column": qualifier,
            "positive_class": validate.text(p, "positive_class") or None,
        }
        if kind == "regression":
            if not isinstance(p.get("source_units"), str):
                raise BadRequest("confirm the units of %r (an empty string for none)" % column)
            transform = validate.choice(p, "transform", TRANSFORMS, "identity")
            target.update(transform=transform, source_units=p["source_units"])
        return cls(dataset_id, MappingProxyType({"structure_column": structure, "target": target}))


@dataclass(frozen=True)
class DatasetIdRequest:
    """``POST /api/dataset/cancel`` and ``GET /api/dataset?id=...``."""

    dataset_id: str

    @classmethod
    def parse(cls, p: Mapping[str, Any]) -> DatasetIdRequest:
        dataset_id = validate.text(p, "dataset_id") or validate.text(p, "id")
        if not dataset_id:
            raise BadRequest("name the dataset")
        return cls(dataset_id)


@dataclass(frozen=True)
class DatasetRowsRequest:
    """``POST /api/dataset/rows``: one page of a dataset, sorted and filtered.

    ``sort`` is ``{key, dir}`` and ``filters`` a list of ``{key, op, value}``;
    both are checked against the dataset's columns where those are known
    (see ``rowquery``). ``depict`` asks for each row's drawing too.
    """

    dataset_id: str
    offset: int
    limit: int
    sort: Mapping[str, Any] | None
    filters: tuple[Mapping[str, Any], ...]
    depict: bool

    @classmethod
    def parse(cls, p: Mapping[str, Any]) -> DatasetRowsRequest:
        dataset_id = validate.text(p, "dataset_id")
        if not dataset_id:
            raise BadRequest("name the dataset")
        sort = p.get("sort")
        if sort is not None and not isinstance(sort, Mapping):
            raise BadRequest("sort must be {key, dir}")
        filters = p.get("filters") or []
        if not isinstance(filters, (list, tuple)) or not all(
            isinstance(f, Mapping) for f in filters
        ):
            raise BadRequest("filters must be a list of {key, op, value}")
        return cls(
            dataset_id,
            validate.integer(p, "offset", 0, lo=0),
            validate.integer(p, "limit", DEFAULT_PAGE, lo=1, hi=MAX_PAGE),
            MappingProxyType(dict(sort)) if sort is not None else None,
            tuple(MappingProxyType(dict(f)) for f in filters),
            p.get("depict", True) is not False,
        )


@dataclass(frozen=True)
class DatasetSummaryRequest:
    """``GET /api/dataset/summary?id=...``: the statistics of a ready dataset."""

    dataset_id: str

    @classmethod
    def parse(cls, p: Mapping[str, Any]) -> DatasetSummaryRequest:
        dataset_id = validate.text(p, "dataset_id") or validate.text(p, "id")
        if not dataset_id:
            raise BadRequest("name the dataset")
        return cls(dataset_id)
