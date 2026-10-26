"""The viewer's adapters against the real tools, on a real structure.

``tests/test_pocketbench.py`` and ``tests/test_generation.py`` drive these
adapters against stand-ins, which is how their error paths are testable at all.
They are ``unittest`` classes, and pytest does not hand fixtures to those, so
the two runs that need the real tools live here instead of beside them.

Both are ``smoke``: a plain ``pytest`` exercises whichever of pocketbench and
NEAT-POCKET this machine actually has.
"""

import sys
import time
from pathlib import Path

import pytest

from proteinviewer.pocketbench import Pocketbench

DATA = Path(__file__).parent / "data"

# A real surface patch of crambin, as (chain, res_id) pairs -- the shape
# pocketgen/neat_pocket.py reads pockets in.
CRAMBIN_POCKET = [["A", r] for r in (20, 21, 22, 23, 24, 25)]


@pytest.mark.smoke
def test_the_real_pocketbench_subprocess_answers_the_adapter(tmp_path, tools):
    """``python -m pocketbench`` for real, in this interpreter.

    The adapter validates every option against the schema the subprocess
    publishes, so this covers the handshake the fakes stand in for: the
    process starts, its JSON parses, and a detection comes back.
    """
    pb = Pocketbench(sys.executable)
    assert pb.available, pb.status_lines()

    detectors = pb.detectors()
    assert "fpocket" in detectors, sorted(detectors)

    if not detectors["fpocket"].get("available"):
        tools.absent("fpocket", "pocketbench reports fpocket unavailable")
    tools.ran("fpocket", "through the viewer's adapter")

    result = pb.detect("fpocket", str(DATA / "1CRN.pdb"), str(tmp_path), cutoff=6.0)
    assert result["pockets"], result.get("meta")
    # The page's own pocket form: a selection string and the counts beside it.
    for pocket in result["pockets"]:
        assert pocket["sel"], pocket
        assert pocket["nResidues"] > 0 and pocket["nAtoms"] > 0, pocket


@pytest.mark.smoke
@pytest.mark.slow
def test_a_real_generation_writes_molecules(neat):
    """One molecule in one pocket of crambin, through NEAT-POCKET itself.

    ``n=1`` and a small ``max_atoms`` keep it as short as a real generation
    gets; loading the model is most of it. The assertion is that the job
    finished and produced a molecule -- never which molecule, which is the
    model's business and is not reproducible.
    """
    job = neat.start(
        str(DATA / "1CRN.pdb"),
        [{"rank": 1, "residues": CRAMBIN_POCKET}],
        n=1,
        batch_size=1,
        max_atoms=40,
    )

    deadline = time.time() + 1800
    while not job.done and time.time() < deadline:
        time.sleep(5)
    assert job.done, "generation did not finish in 30 min: %s" % job.progress()
    assert job.error is None, job.error

    molecules = job.molecules()
    assert molecules, job.progress()
    assert any(text.strip() for text in molecules.values())
