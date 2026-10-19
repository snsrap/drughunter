"""Superpose structures and score pocket detectors against known sites.

Built for apo/holo recovery testing: superpose the holo entry onto the apo one,
carry its ligand's site across, and score what a detector found on the apo
structure against it. The superposition is also what the ProteinViewer's
Superpose panel uses.
"""

__version__ = "0.1.0"

from .move import move_text
from .pockets import Pocket, parse_pockets, read_pockets
from .score import Criteria, PocketScore, best_rank, score
from .site import Site, sites
from .structure import Atom, Structure
from .superpose import Superposition, Transform, fit, related, superpose

__all__ = ["Atom", "Structure", "Superposition", "Transform", "superpose", "fit", "related",
           "move_text", "Site", "sites", "Pocket", "read_pockets", "parse_pockets",
           "Criteria", "PocketScore", "score", "best_rank", "__version__"]
