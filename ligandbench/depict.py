"""2D depictions, as inline SVG.

Black is swapped for ``currentColor`` so a drawing follows the page's text
colour and stays legible in both themes; heteroatom colours are left alone.
The drawing is plain shapes and text, nothing the page's sanitizer would refuse
(proteinviewer/app/js/model/svg.js). ``DEPICTION_VERSION`` is part of every
cache key: change what a drawing looks like and change it, and the old drawings
are no longer found.
"""

from __future__ import annotations

import re

from rdkit import Chem
from rdkit.Chem import rdDepictor
from rdkit.Chem.Draw import rdMolDraw2D

__all__ = ["DEPICTION_VERSION", "depict"]

DEPICTION_VERSION = "1"


def depict(smiles: str, width: int = 180, height: int = 130) -> str:
    """The SVG of one structure, or an empty string when it will not draw."""
    try:
        mol = Chem.MolFromSmiles(smiles)
        if mol is None or mol.GetNumAtoms() == 0:
            return ""
        rdDepictor.Compute2DCoords(mol)
        d = rdMolDraw2D.MolDraw2DSVG(width, height)
        opts = d.drawOptions()
        opts.setBackgroundColour((1, 1, 1, 0))
        opts.bondLineWidth = 1
        rdMolDraw2D.PrepareAndDrawMolecule(d, mol)
        d.FinishDrawing()
        svg = d.GetDrawingText()
        svg = svg.replace("<?xml version='1.0' encoding='iso-8859-1'?>", "")
        # RDKit's "END OF HEADER" comment says nothing, and the page refuses any
        # drawing with a comment in it (model/svg.js).
        svg = re.sub(r"<!--.*?-->", "", svg, flags=re.S).strip()
        return svg.replace("#000000", "currentColor").replace("#000", "currentColor")
    except Exception:
        return ""
