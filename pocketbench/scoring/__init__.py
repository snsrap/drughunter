"""Pose scorers: one interface, any number of tools behind it.

A scorer takes a receptor and an SDF of poses *as they were generated* and
says, per molecule, how the pose sits in that receptor. It never docks: the
pose is the thing being judged, so the most it does is let the molecule
relax where it already is (see :data:`MODES`).

Everything that differs between tools -- how they are found, what their flags
are called, what they write back -- stays inside the scorer. Options are
declared as data (:class:`~pocketbench.options.Option`) and published by
``pocketbench detectors --json``, the same way a detector's are, so a program
driving pocketbench can check a request before starting a run.

What a score is not
-------------------
It is a scoring function's value in kcal/mol, not a measured affinity and not
a prediction of one. A large positive value means atoms overlapping the
receptor -- a clash, not weak binding.

Adding a scorer
---------------
Subclass :class:`PoseScorer` in a module of this package and add an instance
in :func:`default_scorers`. ``pocketbench score-poses --scorer <name>`` runs
it.
"""

from __future__ import annotations

import glob
import os
import time
from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, ClassVar

from .. import poses as sdf
from ..errors import BadInput, ToolFailed, ToolUnavailable
from ..options import Option
from ..process import run
from ..structure import ADDITIVES, Structure

__all__ = [
    "MODES",
    "MODE",
    "PADDING",
    "PoseScore",
    "ScoreRun",
    "PoseScorer",
    "ScorerRegistry",
    "default_scorers",
    "write_receptor",
    "site_points",
]

#: What can be done to a pose, and what each answers.
MODES = {
    "rescore": "score the pose exactly as generated",
    "minimize": "let the pose relax, kept inside a box around the pocket",
    "minimize-free": "let the pose relax with no box, held only by the receptor",
}

MODE = Option("mode", "minimize", choices=tuple(MODES))
#: Room around the pocket's own points, Å. Only the boxed mode uses it.
PADDING = Option("padding", 4, lo=0, hi=20)


@dataclass
class PoseScore:
    """One pose's numbers. ``index`` is its position in the SDF it came from,
    which is how a caller joins this back onto its own table.
    """

    index: int
    name: str = ""
    heavy: int | None = None
    score: float | None = None
    le: float | None = None
    rmsd_min: float | None = None
    error: str = ""

    def as_dict(self) -> dict:
        out: dict[str, Any] = {"index": self.index, "name": self.name}
        for k in ("heavy", "score", "le", "rmsd_min"):
            out[k] = getattr(self, k)
        if self.error:
            out["error"] = self.error
        return out


@dataclass
class ScoreRun:
    """Every pose's numbers, and what produced them."""

    tool: str
    version: str
    mode: str
    command: Sequence[str]
    elapsed: float
    poses: list[PoseScore]
    site: str = ""
    minimized: str = ""
    warnings: list[str] = field(default_factory=list)

    def __iter__(self):
        return iter(self.poses)

    def __len__(self) -> int:
        return len(self.poses)

    @property
    def failed(self) -> int:
        return sum(1 for p in self.poses if p.score is None)

    def meta(self) -> dict:
        return {
            "tool": self.tool,
            "version": self.version,
            "scorer": "%s %s" % (self.tool, self.version) if self.version else self.tool,
            "mode": self.mode,
            "command": " ".join(self.command),
            "elapsed": round(self.elapsed, 2),
            "scored": len(self) - self.failed,
            "failed": self.failed,
            "site": self.site,
            "minimized": self.minimized,
            "warnings": list(self.warnings),
        }


def write_receptor(
    path: str, workdir: str, ligand_resnames: frozenset[str] = frozenset()
) -> tuple[str, list[str]]:
    """The receptor as PDB, written into ``workdir``, with what to warn about.

    Solvent and crystallisation additives go, and so does any residue named
    in ``ligand_resnames`` -- but **hydrogens stay**: a scoring function types
    hydrogen-bond donors by them, so dropping them (as pocket detection does)
    would quietly change every number. A cofactor is left in place for the
    same reason: on a haem protein it is part of the wall.
    """
    structure = Structure.read(path)
    skip = ADDITIVES | ligand_resnames
    kept = Structure([a for a in structure.atoms if a.resn not in skip], fmt=structure.fmt)
    if len(kept) == 0:
        raise BadInput("nothing left of the receptor after removing solvent and ligands")
    os.makedirs(workdir, exist_ok=True)
    out = os.path.join(workdir, "receptor.pdb")
    with open(out, "w") as fh:
        fh.write(kept.to_pdb())
    warnings = []
    if not any(a.element == "H" for a in kept.atoms):
        warnings.append(
            "the receptor has no hydrogens: donors cannot be typed, so the scores are "
            "only indicative -- prepare the structure first"
        )
    return out, warnings


def site_points(path: str, rank: int | None, workdir: str) -> str:
    """A box definition for one pocket, written into ``workdir``.

    ``path`` is a file of pocket points -- the ``*.spheres.pdb`` a
    ProteinViewer project already writes, one residue per pocket numbered by
    rank. ``rank`` picks one of them; None keeps the file whole.
    """
    structure = Structure.read(path)
    atoms = structure.atoms if rank is None else [a for a in structure.atoms if a.resi == rank]
    if not atoms:
        raise BadInput("no pocket %s in %s" % (rank, os.path.basename(path)))
    os.makedirs(workdir, exist_ok=True)
    out = os.path.join(workdir, "site.pdb")
    with open(out, "w") as fh:
        fh.write(Structure(atoms, fmt=structure.fmt).to_pdb())
    return out


class PoseScorer(ABC):
    """One pose-scoring tool."""

    #: The name a run asks for it by.
    name: ClassVar[str]
    #: Every scorer takes the same two: what to do, and how much room.
    OPTIONS: ClassVar[tuple[Option, ...]] = (MODE, PADDING)
    #: The tag the tool writes its score into, and the one it writes the
    #: distance moved into. smina and gnina share these names.
    SCORE_TAG: ClassVar[str] = "minimizedAffinity"
    RMSD_TAG: ClassVar[str] = "minimizedRMSD"

    binary: str | None
    timeout: float
    pose_timeout: float

    @property
    def available(self) -> bool:
        return self.binary is not None

    @abstractmethod
    def describe(self) -> dict:
        """JSON status: at least ``available``."""

    @abstractmethod
    def status_line(self) -> str:
        """One line saying what this installation can do."""

    @property
    @abstractmethod
    def version(self) -> str:
        """What the tool calls itself, for the record."""

    def options(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        """The scorer's options from ``raw``, checked and with defaults filled
        in. Unknown keys are left alone: ``raw`` may be a whole request.
        """
        return {o.name: o.check(raw) for o in self.OPTIONS}

    # -- the flags, per tool -------------------------------------------------
    def base_flags(self) -> list[str]:
        """Flags every run of this tool takes."""
        return []

    def mode_flags(self, mode: str, site: str | None, padding: int) -> list[str]:
        """The flags for one mode.

        One flag per mode, never two: ``--score_only`` leaves the pose alone,
        ``--local_only`` relaxes it inside the autobox, and ``--minimize``
        relaxes it with no box at all.
        """
        if mode == "rescore":
            return ["--score_only"]
        if mode == "minimize-free":
            return ["--minimize"]
        if not site:
            raise BadInput(
                "minimize needs a box: give --site (a pocket's points) or use --mode minimize-free"
            )
        return ["--local_only", "--autobox_ligand", site, "--autobox_add", "%g" % padding]

    # -- running it ----------------------------------------------------------
    def score(
        self,
        receptor: str,
        poses_path: str,
        workdir: str,
        *,
        mode: str = MODE.default,
        padding: int = PADDING.default,
        site: str | None = None,
        site_rank: int | None = None,
        ligand_resnames: frozenset[str] = frozenset(),
        output: str | None = None,
    ) -> ScoreRun:
        """Score every pose in ``poses_path`` against ``receptor``.

        ``site`` is a file of pocket points for the boxed mode, ``site_rank``
        which pocket in it. ``output`` keeps the relaxed poses -- never on top
        of the poses that went in.
        """
        if not self.available:
            raise ToolUnavailable(self._missing(), self.name)
        if mode not in MODES:
            raise BadInput("unknown mode %r (want %s)" % (mode, " or ".join(MODES)))
        records = sdf.split(_read(poses_path))
        if not records:
            raise BadInput("%s holds no molecules" % os.path.basename(poses_path))

        os.makedirs(workdir, exist_ok=True)
        receptor_pdb, warnings = write_receptor(receptor, workdir, ligand_resnames)
        box = site_points(site, site_rank, workdir) if site else None
        flags = self.base_flags() + self.mode_flags(mode, box, padding)

        out_path = output or os.path.join(workdir, "scored.sdf")
        cmd = self._cmd(receptor_pdb, poses_path, flags, out_path)
        started = time.monotonic()
        scored = self._batch(cmd, records, out_path)
        if scored is None:
            # One bad molecule must not cost the rest: score them one at a
            # time and let only the bad ones carry an error.
            scored, written = self._one_by_one(
                receptor_pdb, records, flags, workdir, warnings, out_path
            )
            if not written:
                out_path = ""
        elapsed = time.monotonic() - started

        for pose in scored:
            pose.heavy = sdf.heavy_atoms(records[pose.index])
            pose.name = pose.name or sdf.title(records[pose.index])
            if pose.score is not None and pose.heavy:
                pose.le = round(-pose.score / pose.heavy, 3)
        if mode == "rescore":
            out_path = ""  # nothing moved, so there is nothing to keep
        if scored and all(p.score is None for p in scored):
            raise ToolFailed(
                "%s scored none of the %d poses: %s" % (self.name, len(scored), scored[0].error),
                self.name,
            )
        return ScoreRun(
            tool=self.name,
            version=self.version,
            mode=mode,
            command=cmd,
            elapsed=elapsed,
            poses=scored,
            site=box or "",
            minimized=out_path,
            warnings=warnings,
        )

    def _cmd(self, receptor: str, ligand: str, flags: list[str], out: str) -> list[str]:
        return [self.binary or self.name, "-r", receptor, "-l", ligand, *flags, "-o", out]

    def _batch(self, cmd: list[str], records: list[str], out: str) -> list[PoseScore] | None:
        """Every pose in one run, or None if the run has to be taken apart.

        A tool that stops on the first molecule it cannot handle leaves fewer
        records than it was given; that is the signal to go one by one.
        """
        proc = run(cmd, tool=self.name, timeout=self.timeout)
        written = sdf.split(_read(out)) if os.path.isfile(out) else []
        if proc.returncode != 0 or len(written) != len(records):
            return None
        return [self._from_record(i, r) for i, r in enumerate(written)]

    def _one_by_one(
        self,
        receptor: str,
        records: list[str],
        flags: list[str],
        workdir: str,
        warnings: list[str],
        out: str,
    ) -> tuple[list[PoseScore], bool]:
        warnings.append("scored one molecule at a time: the whole file could not be scored at once")
        scored: list[PoseScore] = []
        kept: list[str] = []
        single_dir = os.path.join(workdir, "single")
        os.makedirs(single_dir, exist_ok=True)
        for i, record in enumerate(records):
            one_in = os.path.join(single_dir, "in.sdf")
            one_out = os.path.join(single_dir, "out.sdf")
            with open(one_in, "w") as fh:
                fh.write(sdf.join([record]))
            if os.path.isfile(one_out):
                os.remove(one_out)
            cmd = self._cmd(receptor, one_in, flags, one_out)
            try:
                proc = run(cmd, tool=self.name, timeout=self.pose_timeout)
            except ToolFailed as exc:
                scored.append(PoseScore(i, sdf.title(record), error=str(exc)))
                continue
            written = sdf.split(_read(one_out)) if os.path.isfile(one_out) else []
            if proc.returncode != 0 or not written:
                scored.append(
                    PoseScore(
                        i,
                        sdf.title(record),
                        error="%s could not score this pose (exit %s)"
                        % (self.name, proc.returncode),
                    )
                )
                continue
            scored.append(self._from_record(i, written[0]))
            kept.append(written[0])
        if kept:
            with open(out, "w") as fh:
                fh.write(sdf.join(kept))
        return scored, bool(kept)

    def _from_record(self, index: int, record: str) -> PoseScore:
        tags = sdf.tags(record)
        score = sdf.number(tags.get(self.SCORE_TAG))
        pose = PoseScore(index, sdf.title(record), score=score)
        pose.rmsd_min = sdf.number(tags.get(self.RMSD_TAG))
        if score is None:
            pose.error = "%s wrote no %s for this pose" % (self.name, self.SCORE_TAG)
        return pose

    def _missing(self) -> str:
        return "%s not found. Install it and put it on PATH, or set %s_BIN to its location." % (
            self.name,
            self.name.upper(),
        )


class ScorerRegistry:
    """Scorers by name, in the order registered."""

    def __init__(self, scorers: Iterable[PoseScorer] = ()):
        self._by_name: dict[str, PoseScorer] = {}
        for s in scorers:
            self.register(s)

    def register(self, scorer: PoseScorer) -> None:
        if scorer.name in self._by_name:
            raise ValueError("scorer %r registered twice" % scorer.name)
        self._by_name[scorer.name] = scorer

    def get(self, name: str | None) -> PoseScorer:
        """The scorer asked for, or -- given nothing -- the first one that can
        actually run.
        """
        if not name:
            found = next((s for s in self if s.available), None)
            if found is None:
                raise ToolUnavailable(
                    "no pose scorer found. Install smina or gnina and put it on PATH, "
                    "or set SMINA_BIN or GNINA_BIN.",
                    "scoring",
                )
            return found
        try:
            return self._by_name[name]
        except KeyError:
            raise BadInput(
                "unknown scorer %r (want %s)" % (name, " or ".join(self._by_name))
            ) from None

    def names(self) -> list[str]:
        return list(self._by_name)

    def __iter__(self):
        return iter(self._by_name.values())

    def describe(self) -> dict[str, dict]:
        """Each scorer's status, its options and its one-line summary."""
        return {
            s.name: {
                **s.describe(),
                "options": {o.name: o.as_dict() for o in s.OPTIONS},
                "modes": dict(MODES),
                "status": s.status_line(),
            }
            for s in self
        }


def default_scorers(smina_bin: str | None = None, gnina_bin: str | None = None) -> ScorerRegistry:
    """Every scorer pocketbench offers. A binary given here wins over
    ``SMINA_BIN`` / ``GNINA_BIN``, which win over where each tool usually
    lives.
    """
    from .smina import GninaScorer, SminaScorer

    return ScorerRegistry(
        [
            SminaScorer(smina_bin or os.environ.get("SMINA_BIN")),
            GninaScorer(gnina_bin or os.environ.get("GNINA_BIN")),
        ]
    )


def pyenv_binaries(name: str) -> list[str]:
    """``name`` in any pyenv environment.

    gnina ships as a binary that pip can install into an environment, so the
    one this machine has may be in an environment that is not on PATH --
    NEAT-POCKET's own, for instance. Found the same way the generation runner
    finds its interpreter.
    """
    pattern = os.path.expanduser(os.path.join("~/.pyenv/versions", "*", "bin", name))
    return sorted(glob.glob(pattern))


def _read(path: str) -> str:
    with open(path, errors="replace") as fh:
        return fh.read()
