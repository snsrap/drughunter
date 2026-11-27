"""Structure identity: what a SMILES cell becomes, and what it is called.

One policy, recorded with every dataset so everything downstream -- splits,
predictions, attribution -- groups and compares molecules the same way: RDKit's
canonical isomeric SMILES, with stereochemistry, charges and disconnected
components kept. No salt stripping, tautomer normalisation or neutralisation.
"""

from __future__ import annotations

from rdkit import Chem, RDLogger, rdBase

from .errors import BadInput

__all__ = ["IDENTITY_POLICY", "canonical_smiles", "require_rdkit"]

# RDKit logs every rejected SMILES to stderr; each is reported with its row.
RDLogger.DisableLog("rdApp.*")

IDENTITY_POLICY = {
    "structure": "RDKit canonical isomeric SMILES",
    "standardization": "none",
    "rdkit": rdBase.rdkitVersion,
}


def require_rdkit() -> str:
    """RDKit's version. Importing this module is the check; this is here so a
    caller reads as asking.
    """
    return rdBase.rdkitVersion


def canonical_smiles(cell: str) -> tuple[str | None, str | None]:
    """``(canonical SMILES, None)`` for a cell that parses, otherwise
    ``(None, why)``.
    """
    text = cell.strip()
    if not text:
        return None, "no structure"
    mol = Chem.MolFromSmiles(text)
    if mol is None:
        return None, "not a valid SMILES"
    if mol.GetNumAtoms() == 0:
        return None, "no atoms"
    try:
        return Chem.MolToSmiles(mol, isomericSmiles=True, canonical=True), None
    except Exception as exc:  # RDKit can fail to write what it parsed
        raise BadInput("RDKit could not write %r: %s" % (text[:60], exc)) from None
