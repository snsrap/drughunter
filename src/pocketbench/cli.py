"""Command line: ``pocketbench <command>`` or ``python -m pocketbench``.

    superpose MOBILE REFERENCE [-o OUT]   move one structure onto another
    apo       APO -o OUT [--chain A]      the detector's input: protein only
    site      APO HOLO LIGAND             a holo ligand's site, in apo terms
    score     POCKETS APO HOLO LIGAND     how well each pocket finds it

Every command takes ``--json`` for output a program reads; ``--on`` restricts
the fit to residues of the reference (the apo structure), e.g.
``--on A:1-29,A:39-58,A:77-169`` to fit KRAS on its core without the switches.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from . import __version__
from .move import move_text
from .pockets import read_pockets
from .score import CRITERIA, Criteria, best_rank, score
from .site import sites
from .structure import STANDARD_AA, Key, Structure
from .superpose import Superposition, superpose

_SPEC = re.compile(r"^([^:,\s]+)(?::(-?\d+)(?:-(-?\d+))?)?$")


def residues(structure: Structure, spec: str | None) -> set[Key] | None:
    """The residues ``A``, ``A:12`` or ``A:10-50`` name, comma-separated."""
    if not spec:
        return None
    want = []
    for part in spec.split(","):
        m = _SPEC.match(part.strip())
        if not m:
            raise ValueError("cannot read residue range %r (expected e.g. A:10-50)" % part)
        lo = int(m.group(2)) if m.group(2) else None
        want.append((m.group(1), lo, int(m.group(3)) if m.group(3) else lo))
    keys = {a.key for a in structure.atoms
            if any(a.chain == c and (lo is None or lo <= a.resi <= hi) for c, lo, hi in want)}
    if not keys:
        raise ValueError("no residues in %s" % spec)
    return keys


def _fit_line(sup: Superposition, mobile: str, ref: str) -> str:
    chains = ", ".join("%s→%s" % c for c in sup.chains)
    line = ("superposed %s onto %s: RMSD %.2f Å over %d of %d Cα, %.0f %% identical (chains %s)"
            % (mobile, ref, sup.rmsd, sup.n, sup.aligned, sup.ident * 100, chains))
    if not sup.related:
        line += ("\n  identity too low to show the two related over %d residues — "
                 "the residue pairing may be chance" % sup.paired)
    return line


def _key(k: Key) -> str:
    return "%s:%d%s" % k


def cmd_superpose(a) -> int:
    mob, ref = Structure.read(a.mobile), Structure.read(a.reference)
    sup = superpose(mob.atoms, ref.atoms, reference_on=residues(ref, a.on))
    if a.output:
        Path(a.output).write_text(move_text(mob.text, sup.transform, mob.fmt))
    if a.json:
        print(json.dumps(sup.as_dict(), indent=1))
    else:
        print(_fit_line(sup, Path(a.mobile).name, Path(a.reference).name))
        if a.output:
            print("wrote %s" % a.output)
    return 0


def cmd_apo(a) -> int:
    """Protein only -- standard amino acids, no hydrogens, chosen chains -- so
    a detector sees no ligand, solvent or additive. From a PDB file the lines
    are copied as they are, so B-factors and occupancies stay; an mmCIF one is
    written out as PDB records."""
    st = Structure.read(a.apo)
    chains = set(a.chain.split(",")) if a.chain else None
    keep = [x for x in st.atoms if x.resn in STANDARD_AA and x.element != "H"
            and (chains is None or x.chain in chains)]
    if not keep:
        raise ValueError("nothing left: no standard amino acids%s" % (" in chain " + a.chain if a.chain else ""))
    out, lines = [], st.text.splitlines()
    for i, x in enumerate(keep, 1):
        if st.fmt == "pdb":
            out.append(lines[x.line])
            continue
        name = x.name if len(x.name) >= 4 else " " + x.name.ljust(3)
        out.append("ATOM  %5d %-4s %-3s %1s%4d%1s   %8.3f%8.3f%8.3f  1.00  0.00          %2s"
                   % (i % 100000, name[:4], x.resn, x.chain[:1], x.resi % 10000, x.icode[:1],
                      x.x, x.y, x.z, x.element.upper()[:2]))
    Path(a.output).write_text("\n".join(out + ["END"]) + "\n")
    dropped = sorted({x.resn for x in st.atoms if x.resn not in STANDARD_AA})
    print("wrote %s: %d heavy atoms, %d residues%s; dropped %s"
          % (a.output, len(keep), len({x.key for x in keep}),
             ", chain " + a.chain if a.chain else "", " ".join(dropped) or "nothing"))
    return 0


def _sites(a):
    apo, holo = Structure.read(a.apo), Structure.read(a.holo)
    sup, found = sites(apo, holo, a.ligand, cutoff=a.cutoff, apo_on=residues(apo, a.on))
    return apo, holo, sup, found


def cmd_site(a) -> int:
    _, _, sup, found = _sites(a)
    if a.json:
        print(json.dumps({"superposition": sup.as_dict(), "sites": [s.as_dict() for s in found]}, indent=1))
        return 0
    print(_fit_line(sup, Path(a.holo).name, Path(a.apo).name))
    for s in found:
        c = s.centroid
        print("\n%s %s: %d heavy atoms, centroid %.2f %.2f %.2f (apo frame)"
              % (a.ligand, _key(s.copy), len(s.atoms), c[0], c[1], c[2]))
        print("  %d residues within %.1f Å%s" % (s.size, a.cutoff,
              ", %d of them missing from the apo structure" % len(s.missing) if s.missing else ""))
        print("  " + " ".join(_key(k) for k in sorted(s.shell)))
        if s.missing:
            print("  missing: " + " ".join(_key(k) for k in sorted(s.missing)))
    return 0


# What a detector reports beside each pocket: column heading and format.
_INFO = {"persistence": ("persist", "%.0f%%"), "crypticity": ("crypt", "%.2f"),
         "druggability": ("drug", "%.2f"), "score": ("score", "%.1f")}


def cmd_score(a) -> int:
    source, pockets = read_pockets(a.pockets)
    if not pockets:
        raise ValueError("the pocket file lists no pockets")
    _, _, sup, found = _sites(a)
    crit = Criteria(a.coverage, a.dcc, a.dca)
    results = [(s, score(pockets, s, crit)) for s in found]
    if a.json:
        print(json.dumps({"source": source, "superposition": sup.as_dict(),
                          "criteria": vars(crit), "sites": [
                              {**s.as_dict(), "best": {c: best_rank(sc, c) for c in CRITERIA},
                               "pockets": [x.as_dict() for x in sc]} for s, sc in results]}, indent=1))
        return 0
    print(_fit_line(sup, Path(a.holo).name, Path(a.apo).name))
    print("%d pockets from %s (%s)" % (len(pockets), Path(a.pockets).name, source))
    extra = [k for k in _INFO if any(k in p.info for p in pockets)]
    for s, sc in results:
        print("\n%s %s: %d shell residues within %.1f Å%s"
              % (a.ligand, _key(s.copy), s.size, a.cutoff,
                 " (%d missing from the apo structure)" % len(s.missing) if s.missing else ""))
        print("%4s %5s %6s %5s %6s %6s" % ("rank", "ovlp", "cover", "jacc", "DCC", "DCA")
              + "".join(" %7s" % _INFO[k][0] for k in extra) + "  meets")
        for x in sc:
            info = x.pocket.info
            print("%4d %5d %5.0f%% %5.2f %6.1f %6.1f" % (x.pocket.rank, x.overlap, x.coverage * 100,
                                                       x.jaccard, x.dcc, x.dca)
                  + "".join(" %7s" % (_INFO[k][1] % (info[k] * (100 if k == "persistence" else 1)) if k in info else "-") for k in extra)
                  + "  " + " ".join(c for c in CRITERIA if x.hits[c]))
        print("  best rank — coverage ≥ %.0f %%: %s · DCC ≤ %.1f Å: %s · DCA ≤ %.1f Å: %s"
              % (crit.coverage * 100, best_rank(sc, "coverage") or "none",
                 crit.dcc, best_rank(sc, "dcc") or "none", crit.dca, best_rank(sc, "dca") or "none"))
    return 0


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="pocketbench", description="Superpose structures, and score pocket detectors against sites known from apo/holo pairs.")
    ap.add_argument("--version", action="version", version="pocketbench " + __version__)
    sub = ap.add_subparsers(dest="command", required=True)

    def common(p, fit=True):
        p.add_argument("--json", action="store_true", help="machine-readable output")
        if fit:
            p.add_argument("--on", metavar="SPEC",
                           help="fit only on these reference residues, e.g. A:1-29,A:39-58")

    p = sub.add_parser("superpose", help="move one structure onto another")
    p.add_argument("mobile"), p.add_argument("reference")
    p.add_argument("-o", "--output", help="write the moved structure here (its own format)")
    common(p)
    p.set_defaults(run=cmd_superpose)

    p = sub.add_parser("apo", help="protein-only input for a detector")
    p.add_argument("apo")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--chain", help="keep only these chains, e.g. A or A,B")
    p.set_defaults(run=cmd_apo)

    for name, helptext in (("site", "a holo ligand's site in the apo structure"),
                           ("score", "score a detector's pockets against a holo ligand")):
        p = sub.add_parser(name, help=helptext)
        if name == "score":
            p.add_argument("pockets", help="lacuna pocket_report.json, or a viewer pockets file")
        p.add_argument("apo"), p.add_argument("holo"), p.add_argument("ligand", help="residue name, e.g. CBT")
        p.add_argument("--cutoff", type=float, default=5.0, help="shell radius, Å (default 5)")
        if name == "score":
            p.add_argument("--coverage", type=float, default=0.5, help="hit: shell coverage (default 0.5)")
            p.add_argument("--dcc", type=float, default=5.0, help="hit: centre to ligand centre, Å (default 5)")
            p.add_argument("--dca", type=float, default=4.0, help="hit: centre to nearest ligand atom, Å (default 4)")
        common(p)
        p.set_defaults(run=cmd_site if name == "site" else cmd_score)
    return ap


def main(argv: list[str] | None = None) -> int:
    a = parser().parse_args(argv)
    try:
        return a.run(a)
    except (ValueError, OSError) as err:
        print("pocketbench %s: %s" % (a.command, err), file=sys.stderr)
        return 1
