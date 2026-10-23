"""Command line: ``pocketbench <command>`` or ``python -m pocketbench``.

    detectors                             what this installation can run
    detect    METHOD STRUCTURE            find pockets with fpocket or lacuna
    superpose MOBILE REFERENCE [-o OUT]   move one structure onto another
    apo       APO -o OUT [--chain A]      a detector's input: protein only
    site      APO HOLO LIGAND             a holo ligand's site, in apo terms
    score     POCKETS APO HOLO LIGAND     how well each pocket finds it
    score-poses RECEPTOR POSES            how each generated pose sits in a pocket

Every command takes ``--json``. The exit code says whose problem a failure
is -- 2 a bad input, 3 a tool that is not installed, 4 a tool that ran and
failed -- and with ``--json`` the error is printed as ``{"error", "kind"}``,
which is how ProteinViewer's server reads it.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

from . import __version__
from .detectors import default_detectors
from .detectors.fpocket import FpocketDetector
from .detectors.lacuna import LacunaDetector
from .errors import BadInput, ToolFailed, ToolUnavailable
from .move import move_text
from .pockets import DEFAULT_CUTOFF
from .reports import read_pockets
from .score import CRITERIA, Criteria, best_rank, score
from .scoring import MODES, default_scorers
from .site import sites
from .structure import ADDITIVES, STANDARD_AA, Key, Structure
from .superpose import Superposition, superpose

# Every detector option, so `detect` takes each as a flag of the same name.
OPTION_NAMES = tuple(
    dict.fromkeys(o.name for d in (FpocketDetector, LacunaDetector) for o in d.OPTIONS)
)

_SPEC = re.compile(r"^([^:,\s]+)(?::(-?\d+)([A-Za-z]?)(?:-(-?\d+))?)?$")


def residues(structure: Structure, spec: str | None) -> set[Key] | None:
    """The residues a spec names, comma-separated: ``A`` (a chain), ``A:52``
    or ``A:52A`` (one residue, insertion code and all), ``A:10-50`` (a range,
    inserted residues included).
    """
    if not spec:
        return None
    keys: set[Key] = set()
    present = {a.key for a in structure.atoms}
    for part in spec.split(","):
        m = _SPEC.match(part.strip())
        if not m:
            raise BadInput("cannot read residue range %r (expected e.g. A:10-50)" % part)
        chain, lo, icode, hi = m.group(1), m.group(2), m.group(3), m.group(4)
        if lo is None:
            keys |= {k for k in present if k[0] == chain}
        elif hi is not None:
            keys |= {k for k in present if k[0] == chain and int(lo) <= k[1] <= int(hi)}
        else:
            keys |= {k for k in present if k == (chain, int(lo), icode)}
    if not keys:
        raise BadInput("no residues in %s" % spec)
    return keys


def _fit_line(sup: Superposition, mobile: str, ref: str) -> str:
    chains = ", ".join("%s→%s" % c for c in sup.chains)
    line = "superposed %s onto %s: RMSD %.2f Å over %d of %d Cα, %.0f %% identical (chains %s)" % (
        mobile,
        ref,
        sup.rmsd,
        sup.n,
        sup.aligned,
        sup.ident * 100,
        chains,
    )
    if not sup.related:
        line += (
            "\n  identity too low to show the two related over %d residues — "
            "the residue pairing may be chance" % sup.paired
        )
    return line


def _key(k: Key) -> str:
    return "%s:%d%s" % k


def _json(obj) -> None:
    print(json.dumps(obj, indent=1))


# ---------------------------------------------------------------- detection
def cmd_detectors(a) -> int:
    reg = default_detectors(a.fpocket_bin, a.lacuna_bin)
    scorers = default_scorers(a.smina_bin, a.gnina_bin)
    if a.json:
        # The additives are what every detector drops on its own, so a caller
        # offering to delete a structure's ligands knows not to offer them.
        _json(
            {
                "version": __version__,
                "detectors": reg.describe(),
                "scorers": scorers.describe(),
                "cutoff": DEFAULT_CUTOFF,
                "additives": sorted(ADDITIVES),
            }
        )
    else:
        print("pocketbench %s" % __version__)
        for d in reg:
            print(d.status_line())
        for s in scorers:
            print(s.status_line())
    return 0


def cmd_detect(a) -> int:
    det = default_detectors(a.fpocket_bin, a.lacuna_bin).get(a.method)
    raw = {k: getattr(a, k) for k in OPTION_NAMES if getattr(a, k) is not None}
    stray = sorted(set(raw) - {o.name for o in det.OPTIONS})
    if stray:
        raise BadInput("%s takes no --%s" % (det.name, stray[0]))
    options = det.options(raw)
    st = Structure.read(a.structure)
    ligands = frozenset(x.strip().upper() for x in (a.ligand or "").split(",") if x.strip())
    work = a.workdir or tempfile.mkdtemp(prefix="pocketbench-")
    try:
        result = det.run(st, work, cutoff=a.cutoff, ligand_resnames=ligands, **options)
    finally:
        if not a.workdir:
            shutil.rmtree(work, ignore_errors=True)
    meta = {**result.meta(), "totalAtoms": len(st), "options": options}
    if a.json:
        _json({"tool": result.tool, "pockets": [p.as_dict() for p in result], "meta": meta})
        return 0
    print(
        "%s: %d pockets in %.2f s · %d receptor atoms of %d, chains %s"
        % (
            det.name,
            len(result),
            result.elapsed,
            result.receptor_atoms,
            len(st),
            ",".join(st.chains()),
        )
    )
    print("$ " + meta["command"])
    print(
        "%4s %8s %6s %8s %8s %6s %6s  %s"
        % ("rank", "score", "drug", "volume", "persist", "crypt", "resid", "centroid")
    )

    def num(v, f):
        return f % v if v == v else "-"

    for p in list(result)[: a.top]:
        print(
            "%4d %8s %6s %8s %8s %6s %6d  %s"
            % (
                p.rank,
                num(p.score, "%.3f"),
                num(p.druggability, "%.2f"),
                num(p.volume, "%.1f"),
                num(p.persistence * 100, "%.0f%%"),
                num(p.crypticity, "%.2f"),
                len(p.residues),
                " ".join("%.2f" % v for v in p.centroid),
            )
        )
    return 0


# ---------------------------------------------------------------- superposition
def cmd_superpose(a) -> int:
    mob, ref = Structure.read(a.mobile), Structure.read(a.reference)
    sup = superpose(
        mob.atoms, ref.atoms, mobile_on=residues(mob, a.mobile_on), reference_on=residues(ref, a.on)
    )
    if a.output:
        Path(a.output).write_text(move_text(mob.text, sup.transform, mob.fmt))
    if a.json:
        _json({**sup.as_dict(), "output": a.output or None})
    else:
        print(_fit_line(sup, Path(a.mobile).name, Path(a.reference).name))
        if a.output:
            print("wrote %s" % a.output)
    return 0


# ---------------------------------------------------------------- apo/holo
def cmd_apo(a) -> int:
    """Protein only -- standard amino acids, no hydrogens, chosen chains -- so
    a detector sees no ligand, solvent or additive. From a PDB file the lines
    are copied as they are, so B-factors and occupancies stay; an mmCIF one is
    written out as PDB records.
    """
    st = Structure.read(a.apo)
    chains = set(a.chain.split(",")) if a.chain else None
    keep = [
        x
        for x in st.atoms
        if x.resn in STANDARD_AA and x.element != "H" and (chains is None or x.chain in chains)
    ]
    if not keep:
        raise BadInput(
            "nothing left: no standard amino acids%s" % (" in chain " + a.chain if a.chain else "")
        )
    out, lines = [], st.text.splitlines()
    for i, x in enumerate(keep, 1):
        if st.fmt == "pdb":
            out.append(lines[x.line])
            continue
        name = x.name if len(x.name) >= 4 else " " + x.name.ljust(3)
        out.append(
            "ATOM  %5d %-4s %-3s %1s%4d%1s   %8.3f%8.3f%8.3f  1.00  0.00          %2s"
            % (
                i % 100000,
                name[:4],
                x.resn,
                x.chain[:1],
                x.resi % 10000,
                x.icode[:1],
                x.x,
                x.y,
                x.z,
                x.element.upper()[:2],
            )
        )
    Path(a.output).write_text("\n".join(out + ["END"]) + "\n")
    dropped = sorted({x.resn for x in st.atoms if x.resn not in STANDARD_AA})
    print(
        "wrote %s: %d heavy atoms, %d residues%s; dropped %s"
        % (
            a.output,
            len(keep),
            len({x.key for x in keep}),
            ", chain " + a.chain if a.chain else "",
            " ".join(dropped) or "nothing",
        )
    )
    return 0


def _sites(a):
    apo, holo = Structure.read(a.apo), Structure.read(a.holo)
    sup, found = sites(apo, holo, a.ligand, cutoff=a.cutoff, apo_on=residues(apo, a.on))
    return apo, holo, sup, found


def cmd_site(a) -> int:
    _, _, sup, found = _sites(a)
    if a.json:
        _json({"superposition": sup.as_dict(), "sites": [s.as_dict() for s in found]})
        return 0
    print(_fit_line(sup, Path(a.holo).name, Path(a.apo).name))
    for s in found:
        c = s.centroid
        print(
            "\n%s %s: %d heavy atoms, centroid %.2f %.2f %.2f (apo frame)"
            % (a.ligand, _key(s.copy), len(s.atoms), c[0], c[1], c[2])
        )
        print(
            "  %d residues within %.1f Å%s"
            % (
                s.size,
                a.cutoff,
                ", %d of them missing from the apo structure" % len(s.missing) if s.missing else "",
            )
        )
        print("  " + " ".join(_key(k) for k in sorted(s.shell)))
        if s.missing:
            print("  missing: " + " ".join(_key(k) for k in sorted(s.missing)))
    return 0


# What a detector reports beside each pocket: column heading and format.
_INFO = {
    "persistence": ("persist", "%.0f%%"),
    "crypticity": ("crypt", "%.2f"),
    "druggability": ("drug", "%.2f"),
    "score": ("score", "%.1f"),
}


def cmd_score(a) -> int:
    source, pockets = read_pockets(a.pockets)
    if not pockets:
        raise BadInput("the pocket file lists no pockets")
    _, _, sup, found = _sites(a)
    crit = Criteria(a.coverage, a.dcc, a.dca)
    results = [(s, score(pockets, s, crit)) for s in found]
    if a.json:
        _json(
            {
                "source": source,
                "superposition": sup.as_dict(),
                "criteria": vars(crit),
                "sites": [
                    {
                        **s.as_dict(),
                        "best": {c: best_rank(sc, c) for c in CRITERIA},
                        "pockets": [x.as_dict() for x in sc],
                    }
                    for s, sc in results
                ],
            }
        )
        return 0
    print(_fit_line(sup, Path(a.holo).name, Path(a.apo).name))
    print("%d pockets from %s (%s)" % (len(pockets), Path(a.pockets).name, source))
    extra = [k for k in _INFO if any(k in p.info for p in pockets)]
    for s, sc in results:
        print(
            "\n%s %s: %d shell residues within %.1f Å%s"
            % (
                a.ligand,
                _key(s.copy),
                s.size,
                a.cutoff,
                " (%d missing from the apo structure)" % len(s.missing) if s.missing else "",
            )
        )
        print(
            "%4s %5s %6s %5s %6s %6s" % ("rank", "ovlp", "cover", "jacc", "DCC", "DCA")
            + "".join(" %7s" % _INFO[k][0] for k in extra)
            + "  meets"
        )
        for x in sc:
            info = x.pocket.info
            print(
                "%4d %5d %5.0f%% %5.2f %6.1f %6.1f"
                % (x.pocket.rank, x.overlap, x.coverage * 100, x.jaccard, x.dcc, x.dca)
                + "".join(
                    " %7s"
                    % (
                        _INFO[k][1] % (info[k] * (100 if k == "persistence" else 1))
                        if k in info
                        else "-"
                    )
                    for k in extra
                )
                + "  "
                + " ".join(c for c in CRITERIA if x.hits[c])
            )
        print(
            "  best rank — coverage ≥ %.0f %%: %s · DCC ≤ %.1f Å: %s · DCA ≤ %.1f Å: %s"
            % (
                crit.coverage * 100,
                best_rank(sc, "coverage") or "none",
                crit.dcc,
                best_rank(sc, "dcc") or "none",
                crit.dca,
                best_rank(sc, "dca") or "none",
            )
        )
    return 0


# ---------------------------------------------------------------- pose scoring
def cmd_score_poses(a) -> int:
    """Score poses as they were generated, in the pocket they were generated
    for. Not docking: nothing searches the pocket for a better placement.
    """
    scorer = default_scorers(a.smina_bin, a.gnina_bin).get(a.scorer)
    options = scorer.options({"mode": a.mode, "padding": a.padding})
    ligands = frozenset(x.strip().upper() for x in (a.ligand or "").split(",") if x.strip())
    work = a.workdir or tempfile.mkdtemp(prefix="pocketbench-")
    try:
        result = scorer.score(
            a.receptor,
            a.poses,
            work,
            mode=options["mode"],
            padding=options["padding"],
            site=a.site,
            site_rank=a.site_rank,
            ligand_resnames=ligands,
            output=a.output,
        )
    finally:
        if not a.workdir and not a.output:
            shutil.rmtree(work, ignore_errors=True)
    meta = result.meta()
    if a.json:
        _json({"poses": [p.as_dict() for p in result], "meta": meta})
        return 0
    print(
        "%s: %d of %d poses scored in %.1f s · %s"
        % (meta["scorer"], meta["scored"], len(result), result.elapsed, MODES[result.mode])
    )
    print("$ " + meta["command"])
    for w in result.warnings:
        print("! " + w)
    print("%5s %-22s %6s %9s %7s %8s" % ("i", "name", "heavy", "score", "le", "rmsd_min"))

    def num(v, f):
        return f % v if isinstance(v, (int, float)) else "-"

    for pose in result:
        print(
            "%5d %-22s %6s %9s %7s %8s  %s"
            % (
                pose.index,
                pose.name[:22],
                num(pose.heavy, "%d"),
                num(pose.score, "%.2f"),
                num(pose.le, "%.3f"),
                num(pose.rmsd_min, "%.2f"),
                pose.error,
            )
        )
    if result.minimized:
        print("relaxed poses: %s" % result.minimized)
    print(
        "a score is a scoring function's value in kcal/mol, not a measured affinity; "
        "a large positive value means the pose clashes with the receptor"
    )
    return 0


# ---------------------------------------------------------------- the parser
def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="pocketbench",
        description="Superpose structures, detect "
        "pockets, and score detectors against sites known from apo/holo pairs.",
    )
    ap.add_argument("--version", action="version", version="pocketbench " + __version__)
    sub = ap.add_subparsers(dest="command", required=True)

    def json_flag(p):
        p.add_argument("--json", action="store_true", help="machine-readable output")

    def tools(p):
        p.add_argument("--fpocket-bin", help="the fpocket binary (default: FPOCKET_BIN, then PATH)")
        p.add_argument(
            "--lacuna-bin", help="the lacuna CLI (default: LACUNA_BIN, then its usual envs)"
        )
        scorer_bins(p)

    def scorer_bins(p):
        p.add_argument("--smina-bin", help="the smina binary (default: SMINA_BIN, then PATH)")
        p.add_argument("--gnina-bin", help="the gnina binary (default: GNINA_BIN, then PATH)")

    def fit_on(p):
        p.add_argument(
            "--on", metavar="SPEC", help="fit only on these reference residues, e.g. A:1-29,A:39-58"
        )

    p = sub.add_parser("detectors", help="what this installation can run")
    tools(p), json_flag(p)
    p.set_defaults(run=cmd_detectors)

    p = sub.add_parser("detect", help="find pockets")
    p.add_argument("method", help="fpocket or lacuna")
    p.add_argument("structure", help="PDB or mmCIF, gzipped or not")
    p.add_argument(
        "--cutoff",
        type=float,
        default=DEFAULT_CUTOFF,
        help="residues within this of a site point, Å (default %g)" % DEFAULT_CUTOFF,
    )
    p.add_argument("--ligand", help="residue names to delete first, comma-separated")
    p.add_argument("--workdir", help="keep the tool's files here (default: a temp dir, removed)")
    p.add_argument("--top", type=int, default=10, help="pockets to list (text output)")
    for name in OPTION_NAMES:
        p.add_argument("--" + name, help="lacuna's --%s" % name)
    tools(p), json_flag(p)
    p.set_defaults(run=cmd_detect)

    p = sub.add_parser("superpose", help="move one structure onto another")
    p.add_argument("mobile"), p.add_argument("reference")
    p.add_argument("-o", "--output", help="write the moved structure here (its own format)")
    fit_on(p)
    p.add_argument(
        "--mobile-on", metavar="SPEC", help="fit only on these residues of the mobile structure"
    )
    json_flag(p)
    p.set_defaults(run=cmd_superpose)

    p = sub.add_parser("apo", help="protein-only input for a detector")
    p.add_argument("apo")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--chain", help="keep only these chains, e.g. A or A,B")
    p.set_defaults(run=cmd_apo, json=False)

    for name, helptext in (
        ("site", "a holo ligand's site in the apo structure"),
        ("score", "score a detector's pockets against a holo ligand"),
    ):
        p = sub.add_parser(name, help=helptext)
        if name == "score":
            p.add_argument(
                "pockets",
                help="lacuna pocket_report.json, or a pockets file from "
                "`detect --json` or a ProteinViewer project",
            )
        (
            p.add_argument("apo"),
            p.add_argument("holo"),
            p.add_argument("ligand", help="residue name, e.g. CBT"),
        )
        p.add_argument("--cutoff", type=float, default=5.0, help="shell radius, Å (default 5)")
        if name == "score":
            p.add_argument(
                "--coverage", type=float, default=0.5, help="hit: shell coverage (default 0.5)"
            )
            p.add_argument(
                "--dcc", type=float, default=5.0, help="hit: centre to ligand centre, Å (default 5)"
            )
            p.add_argument(
                "--dca",
                type=float,
                default=4.0,
                help="hit: centre to nearest ligand atom, Å (default 4)",
            )
        fit_on(p), json_flag(p)
        p.set_defaults(run=cmd_site if name == "site" else cmd_score)

    p = sub.add_parser("score-poses", help="score generated poses in their pocket")
    p.add_argument("receptor", help="the receptor the poses were generated in: PDB or mmCIF")
    p.add_argument("poses", help="an SDF of poses, in the receptor's frame")
    p.add_argument("--scorer", help="smina or gnina (default: whichever is installed)")
    p.add_argument(
        "--mode",
        default=None,
        help="; ".join("%s: %s" % kv for kv in MODES.items()) + " (default minimize)",
    )
    p.add_argument(
        "--site",
        help="a pocket's points, e.g. a project's *.spheres.pdb, to set the box for minimize",
    )
    p.add_argument("--site-rank", type=int, help="which pocket in --site (default: all of them)")
    p.add_argument("--padding", type=int, default=None, help="room around the site points, Å")
    p.add_argument("--ligand", help="residue names to delete from the receptor, comma-separated")
    p.add_argument("-o", "--output", help="write the relaxed poses here (never over the input)")
    p.add_argument("--workdir", help="keep the tool's files here (default: a temp dir, removed)")
    scorer_bins(p), json_flag(p)
    p.set_defaults(run=cmd_score_poses)

    return ap


# Most specific first: BadInput is a ValueError.
EXITS = (
    (ToolUnavailable, 3, "unavailable"),
    (ToolFailed, 4, "failed"),
    ((ValueError, OSError), 2, "bad-input"),
)


def main(argv: list[str] | None = None) -> int:
    a = parser().parse_args(argv)
    try:
        return a.run(a)
    except (ToolUnavailable, ToolFailed, ValueError, OSError) as err:
        code, kind = next((c, k) for cls, c, k in EXITS if isinstance(err, cls))
        if a.json:
            _json({"error": str(err), "kind": kind})
        else:
            print("pocketbench %s: %s" % (a.command, err), file=sys.stderr)
        return code
