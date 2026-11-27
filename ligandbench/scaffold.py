"""Bemis-Murcko scaffolds: the one definition of "same scaffold".

The dataset dashboard counts scaffolds and a scaffold split groups by them. Both
call ``murcko_scaffold``, so a count and a group cannot disagree about a molecule.

A molecule's scaffold is its ring systems and the linkers between them, side
chains removed (atoms double-bonded to a ring or linker atom stay, as RDKit's
``MurckoScaffold`` keeps them), written as RDKit canonical SMILES.

* Stereochemistry is not part of a scaffold: two stereoisomers share one, so a
  scaffold split can never put them on opposite sides.
* A molecule with no ring has the scaffold ``""``. Callers treat every empty
  scaffold as one group.
* The input is taken as it is -- a structure identity (``chem.canonical_smiles``),
  which has no salt stripping, largest-fragment choice or neutralisation. A
  disconnected input is scaffolded fragment by fragment; fragments with no ring
  add nothing, and a charged ring atom keeps its charge.
"""

from __future__ import annotations

from rdkit import Chem
from rdkit.Chem.Scaffolds import MurckoScaffold

from .chem import canonical_smiles
from .errors import BadInput

__all__ = ["SCAFFOLD_POLICY", "murcko_scaffold"]

# Stored beside anything derived from a scaffold. Bump it when this module would
# answer differently for the same molecule, so a stored result says which
# definition it used.
SCAFFOLD_POLICY = "murcko-v1"


def murcko_scaffold(smiles: str) -> str:
    """The canonical SMILES of ``smiles``'s Bemis-Murcko scaffold, without
    stereochemistry; ``""`` when the molecule has no ring.

    Raises ``BadInput`` for a string that is not a structure.
    """
    identity, why = canonical_smiles(smiles)
    if identity is None:
        raise BadInput("no scaffold for %r: %s" % (smiles[:60], why))
    try:
        mol = Chem.MolFromSmiles(identity)
        # RDKit's helper can answer None for "no scaffold"; here that is "".
        return MurckoScaffold.MurckoScaffoldSmiles(mol=mol, includeChirality=False) or ""
    except Exception as exc:  # RDKit can fail on a molecule it parsed
        raise BadInput("RDKit has no scaffold for %r: %s" % (identity[:60], exc)) from None
