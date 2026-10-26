"""Shared by the whole suite: the environment report, and the tool gating.

Two separate questions, which the ``tools`` fixture keeps apart:

- **is it installed?** If it is, its test runs. A plain ``pytest`` exercises
  every tool this machine has, with no flag to remember; the ``smoke`` marker
  selects those tests, it does not enable them.
- **is its absence a failure?** Normally no: a plain checkout has no fpocket.
  ``DRUGHUNTER_REQUIRE_TOOLS`` says which absences are errors, for the setup
  script checking what it just installed and for CI checking what it installed
  in the job.

Slowness is a third question, and it is a marker (``slow``), never a gate: an
installed tool is exercised unless the run asks not to with ``-m "not slow"``.
"""

import os
import subprocess

import pytest

_HEALTH = pytest.StashKey[list]()
_TOOLS = pytest.StashKey[dict]()

#: Everything that can be required by name in ``DRUGHUNTER_REQUIRE_TOOLS``.
TOOLS = ("fpocket", "lacuna", "scorer", "neat-pocket", "weights", "gpu")


def required() -> set[str]:
    """The tools whose absence this run treats as a failure."""
    raw = os.environ.get("DRUGHUNTER_REQUIRE_TOOLS", "").strip().lower()
    if raw in ("", "0", "no", "false"):
        return set()
    if raw in ("1", "yes", "true", "all"):
        return set(TOOLS)
    return {w.strip() for w in raw.split(",") if w.strip()}


class ToolGate:
    """Whether a tool's test runs here, and the record of what happened.

    The record is what makes an availability-driven suite readable: "132
    passed" means different things on two machines, so the run says which
    tools it actually exercised and which it stood down on.
    """

    def __init__(self, seen: dict):
        self._seen = seen

    def ran(self, what: str, note: str = "") -> None:
        self._seen[what] = "ran" + ("  %s" % note if note else "")

    def absent(self, what: str, why: str):
        """Fail if ``what`` is required here, otherwise skip, either way saying why."""
        if what in required():
            self._seen[what] = "REQUIRED BUT ABSENT  %s" % why
            pytest.fail("%s is required (DRUGHUNTER_REQUIRE_TOOLS) but %s" % (what, why))
        self._seen[what] = "skipped  %s" % why
        pytest.skip(why)


@pytest.fixture
def tools(request):
    """Tool gating, shared by the health tests and the smoke tests."""
    return ToolGate(request.config.stash.setdefault(_TOOLS, {}))


def run(cmd: list[str], timeout: float = 120, cwd: str | None = None):
    """Run a check, turning not starting or hanging into an ordinary failure."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    except subprocess.TimeoutExpired:
        pytest.fail("%s did not finish in %gs" % (cmd[0], timeout))
    except OSError as exc:
        pytest.fail("%s could not be started: %s" % (cmd[0], exc))


def tail(proc: subprocess.CompletedProcess, n: int = 15) -> str:
    text = (proc.stderr or proc.stdout or "").strip()
    return "\n".join(text.splitlines()[-n:])


@pytest.fixture
def scorer(tools):
    """A pose scorer that is really installed -- smina or gnina -- or a skip."""
    from pocketbench.scoring import default_scorers

    found = next((s for s in default_scorers() if s.available), None)
    if found is None:
        tools.absent(
            "scorer", "neither smina nor gnina was found (put one on PATH or set SMINA_BIN)"
        )
    tools.ran("scorer", found.status_line())
    return found


@pytest.fixture
def neat(tmp_path, tools, health_report):
    """NEAT-POCKET's checkout and an interpreter that can run it, or a skip.

    Shared with the smoke tests, which run a real generation through it.
    """
    from proteinviewer.generation import NeatPocket

    gen = NeatPocket(
        tmp_path,
        neat_dir=os.environ.get("NEAT_POCKET_DIR") or None,
        python=os.environ.get("NEAT_PYTHON") or None,
    )
    if not gen.available:
        health_report.append(gen.status_line())
        tools.absent("neat-pocket", gen.status_line())
    # find_python() falls back to whatever python3 is on PATH, which finds an
    # interpreter but not necessarily one that has torch.
    proc = run(
        [gen.python, "-c", "import torch, torch_geometric, rdkit, neat"],
        timeout=300,
        cwd=gen.neat_dir,
    )
    if proc.returncode != 0:
        health_report.append("NEAT-POCKET: %s  (%s)  CANNOT RUN" % (gen.neat_dir, gen.python))
        tools.absent(
            "neat-pocket",
            "%s cannot import NEAT-POCKET's stack (set NEAT_PYTHON):\n%s"
            % (gen.python, tail(proc)),
        )
    tools.ran("neat-pocket", gen.neat_dir)
    return gen


@pytest.fixture
def health_report(request):
    """Lines for the environment summary, printed once at the end of the run.

    The health tests add to it whether they pass, skip or fail, so one run
    leaves a single block a user can paste into a bug report.
    """
    return request.config.stash.setdefault(_HEALTH, [])


def pytest_terminal_summary(terminalreporter, config):
    lines = config.stash.get(_HEALTH, [])
    if lines:
        terminalreporter.section("drughunter environment")
        for line in lines:
            terminalreporter.write_line(line)
    seen = config.stash.get(_TOOLS, {})
    if seen:
        terminalreporter.section("tools exercised")
        for name in TOOLS:
            if name in seen:
                terminalreporter.write_line("%-12s %s" % (name, seen[name]))
        for name, state in seen.items():
            if name not in TOOLS:
                terminalreporter.write_line("%-12s %s" % (name, state))
