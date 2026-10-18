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

__all__ = ["Settings", "PROJECT_DIR"]

PROJECT_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8000
    open_browser: bool = True
    app_dir: Path = PROJECT_DIR / "app"
    # Where buffered structures and generation jobs live; a fresh temp
    # directory, removed on exit, when unset.
    work_root: Path | None = None
    buffer_limit: int = 12      # structures kept on disk at once
    job_limit: int = 32         # finished generation jobs kept
    max_body: int = 64 * 1024 * 1024  # deposited mmCIF entries get large

    fpocket_bin: str | None = None       # FPOCKET_BIN
    lacuna_bin: str | None = None        # LACUNA_BIN
    quickprep_python: str | None = None  # QUICKPREP_PYTHON
    neat_dir: str | None = None          # NEAT_POCKET_DIR
    neat_python: str | None = None       # NEAT_PYTHON

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None,
                 **overrides) -> "Settings":
        env = os.environ if environ is None else environ
        base = cls(
            fpocket_bin=env.get("FPOCKET_BIN") or None,
            lacuna_bin=env.get("LACUNA_BIN") or None,
            quickprep_python=env.get("QUICKPREP_PYTHON") or None,
            neat_dir=env.get("NEAT_POCKET_DIR") or None,
            neat_python=env.get("NEAT_PYTHON") or None,
        )
        return replace(base, **overrides)
