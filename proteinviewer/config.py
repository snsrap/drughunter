"""Every setting the server reads, and the one place environment variables
are looked at.

The adapters still know where their tools usually live (that knowledge is
tool-specific); what comes from here are the overrides. Tests build a
:class:`Settings` directly instead of patching the environment.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path

__all__ = ["Settings", "APP_DIR", "default_project_root"]

# The page ships inside the package, so an installed copy serves it too.
APP_DIR = Path(__file__).resolve().parent / "app"


def default_project_root(environ: Mapping[str, str] | None = None) -> Path:
    """Where session output directories live when nothing says otherwise.

    Not a temp directory: a project is the point of a session's work and has
    to survive the server stopping. XDG_DATA_HOME is honoured, so a user who
    has moved their data directory keeps it.
    """
    env = os.environ if environ is None else environ
    base = env.get("XDG_DATA_HOME") or "~/.local/share"
    return Path(os.path.expanduser(base)) / "proteinviewer" / "projects"


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"  # PV_HOST; 0.0.0.0 inside a container
    port: int = 8000
    open_browser: bool = True
    app_dir: Path = APP_DIR
    # Where buffered structures and generation jobs live; a fresh temp
    # directory, removed on exit, when unset.
    work_root: Path | None = None
    # Where a session's output directory is collected: kept, not temporary.
    # PV_PROJECT_DIR, or --projects; default_project_root() when unset.
    project_root: Path | None = None
    buffer_limit: int = 12  # structures kept on disk at once
    job_limit: int = 32  # finished generation jobs kept
    max_body: int = 64 * 1024 * 1024  # deposited mmCIF entries get large

    pocketbench_python: str | None = None  # POCKETBENCH_PYTHON
    fpocket_bin: str | None = None  # FPOCKET_BIN, passed on to pocketbench
    lacuna_bin: str | None = None  # LACUNA_BIN, passed on to pocketbench
    quickprep_python: str | None = None  # QUICKPREP_PYTHON
    neat_dir: str | None = None  # NEAT_POCKET_DIR
    neat_python: str | None = None  # NEAT_PYTHON

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None, **overrides) -> Settings:
        env = os.environ if environ is None else environ
        root = env.get("PV_PROJECT_DIR") or None
        base = cls(
            host=env.get("PV_HOST") or cls.host,
            project_root=Path(os.path.expanduser(root)) if root else None,
            pocketbench_python=env.get("POCKETBENCH_PYTHON") or None,
            fpocket_bin=env.get("FPOCKET_BIN") or None,
            lacuna_bin=env.get("LACUNA_BIN") or None,
            quickprep_python=env.get("QUICKPREP_PYTHON") or None,
            neat_dir=env.get("NEAT_POCKET_DIR") or None,
            neat_python=env.get("NEAT_PYTHON") or None,
        )
        return replace(base, **overrides)
