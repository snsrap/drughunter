"""How well each pocket of a run finds a known site.

Per pocket: residue overlap with the site's shell (as coverage of the shell,
and as Jaccard), DCC -- the distance from the pocket's centre to the ligand's
centre -- and DCA, from the pocket's centre to the nearest ligand atom.

Three hit criteria, each reported on its own, with no single verdict:

- coverage >= 50 % of the shell and DCC <= 5 Å are the local choices
  docs/validation in ProteinViewer has used so far, and the numbers recorded
  there were found with them. They are not a published convention.
- DCA <= 4 Å is the criterion P2Rank's benchmarks use (Krivák & Hoksza,
  J Cheminform 2018), which makes results comparable outside this project.

Thresholds change conclusions -- at a 40 % coverage bar the recorded TEM-1
and PTP1B ranks both move -- so the columns matter more than the flags.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .pockets import Pocket
from .site import Site
from .superpose import d2

__all__ = ["Criteria", "PocketScore", "score", "best_rank", "CRITERIA"]

CRITERIA = ("coverage", "dcc", "dca")


@dataclass(frozen=True)
class Criteria:
    coverage: float = 0.5
    dcc: float = 5.0
    dca: float = 4.0


@dataclass
class PocketScore:
    pocket: Pocket
    overlap: int
    coverage: float
    jaccard: float
    dcc: float
    dca: float
    hits: dict[str, bool] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"rank": self.pocket.rank, "overlap": self.overlap,
                "coverage": round(self.coverage, 4), "jaccard": round(self.jaccard, 4),
                "dcc": round(self.dcc, 3), "dca": round(self.dca, 3), "hits": self.hits,
                **self.pocket.info}


def score(pockets: list[Pocket], site: Site, crit: Criteria = Criteria()) -> list[PocketScore]:
    centre = site.centroid
    out = []
    for p in pockets:
        inter = p.residues & site.shell
        union = len(p.residues | site.shell) + len(site.missing)
        cov = len(inter) / site.size if site.size else 0.0
        dcc = math.sqrt(d2(p.centroid, centre))
        dca = math.sqrt(min(d2(p.centroid, a) for a in site.atoms))
        out.append(PocketScore(p, len(inter), cov, len(inter) / union if union else 0.0, dcc, dca,
                               {"coverage": cov >= crit.coverage, "dcc": dcc <= crit.dcc,
                                "dca": dca <= crit.dca}))
    return out


def best_rank(scores: list[PocketScore], criterion: str) -> int | None:
    """The best-ranked pocket meeting ``criterion``, or None."""
    ranks = [s.pocket.rank for s in scores if s.hits.get(criterion)]
    return min(ranks) if ranks else None
