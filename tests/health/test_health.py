"""Does this environment actually work? ``pytest -m health``.

The rest of the suite proves the code; this proves the machine. It imports the
runtime dependencies, finds the external tools the same way the code does, and
runs a kernel on the GPU from NEAT-POCKET's interpreter. Every check is its own
test, so one run reports every problem, and the summary at the end is the thing
to paste into a bug report.

A missing tool skips by default: a plain checkout has no fpocket and that is
fine. ``DRUGHUNTER_REQUIRE_TOOLS`` turns absence into failure, for the setup
script to check what it just installed:

    DRUGHUNTER_REQUIRE_TOOLS=1          every tool and the weights
    DRUGHUNTER_REQUIRE_TOOLS=fpocket,scorer
                                        just those (fpocket, lacuna, scorer,
                                        neat-pocket, weights, gpu)

The GPU is required only when ``gpu`` is named, since ``1`` must also hold on a
machine without one. Two GPU cases fail whatever the setting, because they are
broken, not absent:

- a card the driver lists (``nvidia-smi -L``) that torch cannot see, usually a
  CPU-only wheel; unless ``CUDA_VISIBLE_DEVICES`` hides it on purpose;
- a card torch can see but cannot run a kernel on (``sm_120`` with cu121
  wheels: ``is_available()`` says yes, the first kernel says no).

CI has no GPU, so it only ever takes the skip path. The fail path is checked by
hand on a real card. Do not "fix" the skip into a failure to make CI cover it.

Nothing here is imported at module level beyond the stdlib and pytest, so a
broken numpy or a missing gemmi fails its own test instead of the collection.
"""

from __future__ import annotations

import importlib.metadata as md
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import run, tail

pytestmark = pytest.mark.health

PARTS = ("proteinviewer", "pocketbench", "quickprep")
# The checkpoints pocketgen/neat_pocket.py loads unless a job names others.
WEIGHTS = ("trained_models/neat_spindr", "trained_models/bond_predictor/bp_spindr")
# Distributions whose import name is not their own name.
IMPORT_NAMES: dict[str, str] = {}


def runtime_requirements():
    """The runtime requirements drughunter was installed with (no extras)."""
    from packaging.requirements import Requirement

    try:
        reqs = [Requirement(r) for r in md.requires("drughunter") or ()]
    except md.PackageNotFoundError:
        return []
    return [r for r in reqs if r.marker is None or r.marker.evaluate({"extra": ""})]


# -- this interpreter ------------------------------------------------------


def test_interpreter(health_report):
    try:
        version = md.version("drughunter")
    except md.PackageNotFoundError:
        version = None
    health_report.append("python      %s  %s" % (platform.python_version(), sys.executable))
    health_report.append("platform    %s" % platform.platform())
    health_report.append("drughunter  %s" % (version or "NOT INSTALLED"))
    assert version, "drughunter is not installed in this environment (pip install -e .)"


@pytest.mark.parametrize("req", runtime_requirements(), ids=lambda r: r.name)
def test_dependency(req, health_report):
    """Installed, inside the declared range, and importable on its own.

    The import runs in a fresh interpreter: a compiled extension built for the
    wrong numpy can crash rather than raise, and that must not take the rest of
    the run with it.
    """
    try:
        have = md.version(req.name)
    except md.PackageNotFoundError:
        health_report.append("%-11s NOT INSTALLED  (wants %s)" % (req.name, req.specifier or "any"))
        pytest.fail("%s is not installed (pip install -e .)" % req.name)
    ok = req.specifier.contains(have, prereleases=True)
    health_report.append(
        "%-11s %s%s" % (req.name, have, "" if ok else "  OUTSIDE %s" % req.specifier)
    )
    assert ok, "%s %s is installed; drughunter wants %s" % (req.name, have, req.specifier)

    module = IMPORT_NAMES.get(req.name.lower(), req.name.lower().replace("-", "_"))
    proc = run([sys.executable, "-c", "import " + module])
    assert proc.returncode == 0, "import %s failed:\n%s" % (module, tail(proc))


@pytest.mark.parametrize("part", PARTS)
def test_part_imports(part):
    proc = run([sys.executable, "-c", "import " + part])
    assert proc.returncode == 0, "import %s failed:\n%s" % (part, tail(proc))


# -- external tools, found the way the code finds them ----------------------


@pytest.mark.parametrize("name", ("fpocket", "lacuna"))
def test_detector(name, tools, health_report):
    from pocketbench.detectors import default_detectors

    detector = default_detectors().get(name)
    health_report.append(detector.status_line())
    if not detector.available:
        tools.absent(name, detector.status_line())
    tools.ran(name, detector.status_line())


def test_scorer(tools, health_report):
    """A pose scorer: smina or gnina, either one will do."""
    from pocketbench.scoring import default_scorers

    scorers = list(default_scorers())
    for s in scorers:
        health_report.append(s.status_line())
    found = next((s for s in scorers if s.available), None)
    if found is None:
        tools.absent(
            "scorer", "neither smina nor gnina was found (put one on PATH or set SMINA_BIN)"
        )
    tools.ran("scorer", found.status_line())


def test_neat_pocket(neat, health_report):
    health_report.append(neat.status_line())


def test_neat_pocket_weights(neat, tools, health_report):
    """Weights are a step of their own: a checkout without them is not broken,
    just not finished, and is reported as such rather than as a missing tool.
    """
    missing = []
    for rel in WEIGHTS:
        d = Path(neat.neat_dir) / rel / "checkpoints"
        found = sorted(d.glob("*.ckpt")) if d.is_dir() else []
        health_report.append(
            "weights     %s: %s" % (rel, found[0].name if found else "NOT YET FETCHED")
        )
        if not found:
            missing.append(str(d))
    if missing:
        tools.absent(
            "weights", "NEAT-POCKET weights not yet fetched: no .ckpt in " + ", ".join(missing)
        )


# -- the GPU, by running something on it -----------------------------------

# Versions first, flushed, so a kernel that kills the interpreter still leaves
# them behind; then one real operation and a synchronise, which is where a
# torch built for the wrong architecture finally says so.
GPU_PROBE = """
import json, torch
info = {"torch": torch.__version__, "cuda": torch.version.cuda, "device": None}
if torch.cuda.is_available():
    major, minor = torch.cuda.get_device_capability(0)
    info.update(device=torch.cuda.get_device_name(0), capability="sm_%d%d" % (major, minor),
                arches=torch.cuda.get_arch_list())
print(json.dumps(info), flush=True)
if info["device"]:
    x = torch.arange(1024, device="cuda", dtype=torch.float32)
    total = (x * 2).sum()
    torch.cuda.synchronize()
    assert total.item() == 1024 * 1023, total.item()
    print("kernel ok", flush=True)
"""


def nvidia_gpus() -> list[str]:
    """What the driver lists, to tell "no GPU" from "a torch that cannot see it"."""
    smi = shutil.which("nvidia-smi")
    if not smi:
        return []
    try:
        out = subprocess.run([smi, "-L"], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [line.strip() for line in out.splitlines() if line.startswith("GPU ")]


def hidden_on_purpose() -> bool:
    """Whether CUDA_VISIBLE_DEVICES was set to hide every card, to run on CPU."""
    value = os.environ.get("CUDA_VISIBLE_DEVICES")
    return value is not None and value.strip().lower() in ("", "-1", "none", "nodevfiles")


def test_gpu(neat, tools, health_report):
    proc = run([neat.python, "-c", GPU_PROBE], timeout=300, cwd=neat.neat_dir)
    lines = proc.stdout.splitlines()
    try:
        info = json.loads(lines[0])
    except (IndexError, ValueError):
        health_report.append("gpu         torch did not start in %s" % neat.python)
        pytest.fail("torch could not be imported by %s:\n%s" % (neat.python, tail(proc)))

    built = "torch %s (CUDA %s)" % (info["torch"], info["cuda"] or "none, CPU build")
    if not info["device"]:
        listed = nvidia_gpus()
        health_report.append(
            "gpu         %s: no CUDA device%s"
            % (built, "; but the driver lists " + listed[0] if listed else "")
        )
        if listed and hidden_on_purpose():
            pytest.skip(
                "CUDA_VISIBLE_DEVICES=%r hides %s from torch"
                % (os.environ["CUDA_VISIBLE_DEVICES"], "; ".join(listed))
            )
        # A card the driver lists and torch cannot see is broken, not absent:
        # most often a CPU-only torch wheel, or one built for another driver.
        mask = os.environ.get("CUDA_VISIBLE_DEVICES")
        assert not listed, "nvidia-smi lists %s, but %s sees no CUDA device. %s" % (
            "; ".join(listed),
            built,
            "Check CUDA_VISIBLE_DEVICES=%r." % mask
            if mask is not None
            else "Install a torch built for this driver's CUDA.",
        )
        tools.absent("gpu", "no CUDA device (%s)" % built)

    device = "%s %s" % (info["device"], info["capability"])
    ok = proc.returncode == 0 and "kernel ok" in lines
    health_report.append(
        "gpu         %s on %s: %s" % (built, device, "kernel OK" if ok else "KERNEL FAILED")
    )
    tools.ran("gpu", "%s  %s" % (device, "kernel OK" if ok else "KERNEL FAILED"))
    assert ok, (
        "torch sees %s but cannot run on it. %s was built for %s; a capability "
        "missing from that list needs a torch built for it.\n%s"
        % (device, built, " ".join(info.get("arches") or ()) or "unknown arches", tail(proc))
    )
