"""Read a ligand dataset: a CSV of molecules, perhaps with a measured target.

Turns the file a chemist exports (a CDD vault, a spreadsheet) into rows with a
stable identity: every source cell kept, every structure parsed and
canonicalised by RDKit, and each row marked as fit or unfit to train on and
why. ProteinViewer runs it as a command, ``python -m ligandbench``, and never
imports it, so RDKit stays out of the server's own interpreter.

The submodules are imported by name, not here: ``table`` and ``columns`` need
only the standard library, ``chem``, ``resolve`` and ``depict`` need RDKit.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _dist_version

try:
    __version__ = _dist_version("drughunter")
except PackageNotFoundError:  # run from a checkout that was never installed
    __version__ = "0+unknown"

from .errors import BadInput, LigandbenchError, ToolUnavailable

__all__ = ["BadInput", "LigandbenchError", "ToolUnavailable", "__version__"]
