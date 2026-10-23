"""Superpose structures, find pockets, and score pocket detectors against
sites known from experiment.

Built for apo/holo recovery testing: detect on the apo structure, superpose
the holo entry onto it, carry its ligand's site across, and score what was
found against it. ProteinViewer runs its pocket detection and its
superposition through this package (``python -m pocketbench``).
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _dist_version

try:
    __version__ = _dist_version("drughunter")
except PackageNotFoundError:  # run from a checkout that was never installed
    __version__ = "0+unknown"

from .detectors import DetectorRegistry, PocketDetector, default_detectors
from .errors import BadInput, PocketbenchError, ToolFailed, ToolUnavailable
from .move import move_text
from .options import Option
from .pockets import DEFAULT_CUTOFF, DetectionResult, Pocket, select_residues
from .reports import ReportedPocket, parse_pockets, read_pockets
from .score import Criteria, PocketScore, best_rank, score
from .scoring import MODES, PoseScore, PoseScorer, ScorerRegistry, ScoreRun, default_scorers
from .site import Site, sites
from .structure import ADDITIVES, Atom, Structure
from .superpose import Superposition, Transform, fit, related, superpose

__all__ = [
    "Atom",
    "Structure",
    "ADDITIVES",
    "Superposition",
    "Transform",
    "superpose",
    "fit",
    "related",
    "move_text",
    "Pocket",
    "DetectionResult",
    "select_residues",
    "DEFAULT_CUTOFF",
    "PocketDetector",
    "DetectorRegistry",
    "Option",
    "default_detectors",
    "Site",
    "sites",
    "ReportedPocket",
    "read_pockets",
    "parse_pockets",
    "Criteria",
    "PocketScore",
    "score",
    "best_rank",
    "PoseScorer",
    "ScorerRegistry",
    "PoseScore",
    "ScoreRun",
    "MODES",
    "default_scorers",
    "PocketbenchError",
    "BadInput",
    "ToolUnavailable",
    "ToolFailed",
    "__version__",
]
