"""Protein structure preparation in the spirit of MOE's QuickPrep, built from
open tools: gemmi, PDBFixer, PDB2PQR + PROPKA and OpenMM.

    from quickprep import prepare, summary

    report = prepare("1HSG.pdb", "1HSG_prep.pdb", ph=7.0)
    print(summary(report))

The same from the shell: ``quickprep 1HSG.pdb -o 1HSG_prep.pdb``.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _dist_version

try:
    __version__ = _dist_version("drughunter")
except PackageNotFoundError:  # run from a checkout that was never installed
    __version__ = "0+unknown"

from .prep import ADDITIVES, PrepError, prepare, summary  # noqa: E402

__all__ = ["prepare", "summary", "PrepError", "ADDITIVES", "__version__"]
