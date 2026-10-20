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

# drughunter's version: pyproject.toml reads it from here, and pocketbench and
# quickprep carry the same one (tests/test_packaging.py).
__version__ = "0.3.0"  # x-release-please-version
