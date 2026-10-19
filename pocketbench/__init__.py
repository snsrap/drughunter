"""Superpose structures, find pockets, and score pocket detectors against
sites known from experiment.

Built for apo/holo recovery testing: detect on the apo structure, superpose
the holo entry onto it, carry its ligand's site across, and score what was
found against it. ProteinViewer runs its pocket detection and its
superposition through this package (``python -m pocketbench``).
"""

__version__ = "0.2.0"

from .detectors import DetectorRegistry, Option, PocketDetector, default_detectors
from .errors import BadInput, PocketbenchError, ToolFailed, ToolUnavailable
from .move import move_text
from .pockets import DEFAULT_CUTOFF, DetectionResult, Pocket, select_residues
from .reports import ReportedPocket, parse_pockets, read_pockets
from .score import Criteria, PocketScore, best_rank, score
from .site import Site, sites
from .structure import ADDITIVES, Atom, Structure
from .superpose import Superposition, Transform, fit, related, superpose

__all__ = ["Atom", "Structure", "ADDITIVES", "Superposition", "Transform", "superpose", "fit",
           "related", "move_text", "Pocket", "DetectionResult", "select_residues", "DEFAULT_CUTOFF",
           "PocketDetector", "DetectorRegistry", "Option", "default_detectors", "Site", "sites",
           "ReportedPocket", "read_pockets", "parse_pockets", "Criteria", "PocketScore", "score",
           "best_rank", "PocketbenchError", "BadInput", "ToolUnavailable", "ToolFailed", "__version__"]
