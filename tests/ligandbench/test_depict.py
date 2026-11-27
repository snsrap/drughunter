"""Depictions: drawn for the page, and only ever what the page's sanitizer accepts."""

import re

import pytest

pytest.importorskip("rdkit")

from ligandbench.depict import DEPICTION_VERSION, depict  # noqa: E402

# The allowlist of proteinviewer/app/js/model/svg.js, and what it refuses.
SVG_TAGS = {
    "svg", "g", "defs", "path", "rect", "circle", "ellipse", "line",
    "polyline", "polygon", "text", "tspan", "title", "desc",
}  # fmt: skip
REFUSED = re.compile(r"\son[a-z]+\s*=|javascript:|<!--|<!\[|<\?|&#|xlink:href|\shref\s*=", re.I)

MOLECULES = [
    "CCO",
    "c1ccccc1",
    "C[C@H](N)C(=O)O",
    "[Na+].[Cl-]",
    "CC(=O)Oc1ccccc1C(=O)O",
    "COc1ccc(S(=O)(=O)N(CC(C)C)C[C@@H](O)[C@H](Cc2ccccc2)NC(=O)[C@@H]2CN(c3cccc(C(C)=O)c3)C(=O)O2)cc1",
]


@pytest.mark.parametrize("smiles", MOLECULES)
def test_a_drawing_is_nothing_the_page_would_refuse(smiles):
    svg = depict(smiles)
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert not REFUSED.search(svg)
    tags = {t.lower() for t in re.findall(r"</?([A-Za-z][A-Za-z0-9:-]*)", svg)}
    assert tags <= SVG_TAGS, tags - SVG_TAGS


def test_black_follows_the_pages_text_colour():
    svg = depict("CCO")
    assert "currentColor" in svg
    assert "#000000" not in svg


def test_a_structure_that_will_not_draw_is_an_empty_string_not_an_error():
    assert depict("not a smiles") == ""
    assert depict("") == ""


def test_the_depiction_version_is_a_short_token_that_can_live_in_a_path():
    assert re.fullmatch(r"[0-9A-Za-z.]+", DEPICTION_VERSION)
