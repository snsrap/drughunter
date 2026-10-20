"""ProteinViewer: a browser-based protein structure viewer, and the small
server that runs pocket detection, preparation and generation for it.

Layers, each importing only from the ones above it in this list
(``tests/test_architecture.py`` holds the code to it):

``structure``, ``pockets``
    the domain: parsing, the pocket model, the residue cut. Stdlib only.
``errors``, ``validate``, ``config``
    shared vocabulary.
``process``, ``detectors``, ``prep``, ``generation``
    adapters for the external tools, each run as a subprocess.
``buffer``, ``schemas``, ``service``
    the use cases. Tools arrive through interfaces, never by import.
``web``
    HTTP in and out.
``server``
    the composition root: the one module that picks concrete tools.
"""

# drughunter's version is the installed distribution's, which is the git tag;
# pocketbench and quickprep read the same one (tests/test_packaging.py).
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _dist_version

try:
    __version__ = _dist_version("drughunter")
except PackageNotFoundError:  # run from a checkout that was never installed
    __version__ = "0+unknown"
