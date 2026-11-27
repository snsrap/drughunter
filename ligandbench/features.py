"""The QSAR featuriser: what a structure becomes before a model sees it.

One vector per molecule, 2055 numbers long: 2048 binary Morgan bits (radius 2,
chirality on, no count simulation), then seven RDKit descriptors in a fixed
order. Nothing is fitted -- no scaling, no imputation -- so the same molecule
gives the same vector in a training run and, years later, at inference.

``feature_schema()`` records everything that determines the vector. A model
saved with it can rebuild exactly this featuriser through
``Featuriser.from_schema`` and is refused, loudly, by a build that would
compute something else: a model is never silently fed a different vector.

The input is the structure identity of ``chem.canonical_smiles`` (the dataset's
``smiles``), parsed the way ``chem.py`` and ``scaffold.py`` parse it: plain
``Chem.MolFromSmiles``, no salt stripping, no neutralisation.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import numpy as np
from rdkit import Chem, rdBase
from rdkit.Chem import Crippen, Descriptors, rdMolDescriptors
from rdkit.Chem import rdFingerprintGenerator as _fpgen

from .errors import BadInput

__all__ = [
    "FEATURISER_VERSION",
    "N_BITS",
    "DESCRIPTOR_NAMES",
    "FeatureResult",
    "Featuriser",
    "feature_schema",
    "require_all",
]

# A short stable id. Bump it when the same molecule would give a different vector
# (a different bit, a different descriptor, a different order or dtype). It is
# stored in every schema and a Featuriser refuses any other.
FEATURISER_VERSION = "morgan2048-desc7-v1"

SCHEMA_KIND = "qsar-features"
N_BITS = 2048
DTYPE = "float32"

# Every option of the generator the vector depends on, written out rather than
# left to RDKit's defaults, so a default that changes in a later RDKit cannot
# change the vector without the schema comparison noticing.
_FINGERPRINT = {
    "kind": "morgan",
    "output": "bit",
    "radius": 2,
    "fpSize": N_BITS,
    "includeChirality": True,
    "countSimulation": False,
    "useBondTypes": True,
    "onlyNonzeroInvariants": False,
    "includeRingMembership": True,
    "includeRedundantEnvironments": False,
    "numBitsPerFeature": 1,
    "atomInvariants": "rdkit-default",
    "bondInvariants": "rdkit-default",
    "countBounds": None,
}

# (column name, the RDKit function behind it, the callable). The callable is
# looked up through this table when a molecule is featurised.
_DESCRIPTORS: list[tuple[str, str, Callable]] = [
    ("mol_wt", "rdkit.Chem.Descriptors.MolWt", Descriptors.MolWt),
    ("log_p", "rdkit.Chem.Crippen.MolLogP", Crippen.MolLogP),
    ("tpsa", "rdkit.Chem.rdMolDescriptors.CalcTPSA", rdMolDescriptors.CalcTPSA),
    ("hbd", "rdkit.Chem.rdMolDescriptors.CalcNumHBD", rdMolDescriptors.CalcNumHBD),
    ("hba", "rdkit.Chem.rdMolDescriptors.CalcNumHBA", rdMolDescriptors.CalcNumHBA),
    (
        "rotatable_bonds",
        "rdkit.Chem.rdMolDescriptors.CalcNumRotatableBonds",
        rdMolDescriptors.CalcNumRotatableBonds,
    ),
    ("heavy_atoms", "rdkit.Chem.rdchem.Mol.GetNumHeavyAtoms", lambda mol: mol.GetNumHeavyAtoms()),
]
DESCRIPTOR_NAMES = tuple(name for name, _, _ in _DESCRIPTORS)
N_DESCRIPTORS = len(_DESCRIPTORS)
N_FEATURES = N_BITS + N_DESCRIPTORS
_FLOAT32_MAX = float(np.finfo(np.float32).max)


def _bit_columns() -> list[str]:
    return ["morgan_%04d" % i for i in range(N_BITS)]


def feature_schema() -> dict:
    """A JSON-serialisable record of everything that determines the vector,
    sufficient for ``Featuriser.from_schema`` to rebuild it.
    """
    return {
        "schema": SCHEMA_KIND,
        "version": FEATURISER_VERSION,
        "structure": {
            "input": "canonical isomeric SMILES (ligandbench.chem identity)",
            "parser": "rdkit.Chem.MolFromSmiles",
            "sanitize": True,
            "normalisation": "none",
        },
        "fingerprint": dict(_FINGERPRINT),
        "descriptors": [{"name": name, "function": fn} for name, fn, _ in _DESCRIPTORS],
        "dtype": DTYPE,
        "n_features": N_FEATURES,
        "columns": _bit_columns() + list(DESCRIPTOR_NAMES),
        "scaling": "none",
        "imputation": "none",
        "dependencies": {"rdkit": rdBase.rdkitVersion, "numpy": np.__version__},
    }


def _without_dependencies(schema: dict) -> dict:
    return {key: value for key, value in schema.items() if key != "dependencies"}


@dataclass
class FeatureResult:
    """``matrix`` is float32 ``[len(row_ids), 2055]``: the rows that featurised,
    in input order. ``failures`` is ``[{"row_id", "reason"}]`` for the rest.
    """

    matrix: np.ndarray
    row_ids: list
    failures: list = field(default_factory=list)


def _parse(smiles: str):
    """A molecule, or the reason there is none. The same checks as
    ``chem.canonical_smiles``, so a structure that is an identity parses here.
    """
    if not isinstance(smiles, str):
        return None, "no structure"
    text = smiles.strip()
    if not text:
        return None, "no structure"
    mol = Chem.MolFromSmiles(text)
    if mol is None:
        return None, "not a valid SMILES"
    if mol.GetNumAtoms() == 0:
        return None, "no atoms"
    return mol, None


class Featuriser:
    """Turns ``(row_id, smiles)`` pairs into feature rows. It has no options:
    the featuriser this build implements is the only one it can be.
    """

    def __init__(self) -> None:
        self._schema = feature_schema()

    @property
    def schema(self) -> dict:
        """A copy of the schema, safe for the caller to change."""
        return _json_copy(self._schema)

    @classmethod
    def from_schema(cls, schema: dict) -> Featuriser:
        """The featuriser ``schema`` describes, or ``BadInput`` if this build does
        not implement exactly that: a different version, or any parameter, bit
        or descriptor that differs. Dependency versions are recorded, not
        enforced; ``dependency_drift`` reports them.
        """
        if not isinstance(schema, dict):
            raise BadInput("a feature schema is a JSON object")
        version = schema.get("version")
        if version != FEATURISER_VERSION:
            raise BadInput(
                "this build implements featuriser %r, not %r" % (FEATURISER_VERSION, version)
            )
        mine = feature_schema()
        theirs = _without_dependencies(schema)
        differing = sorted(
            key
            for key in set(mine) | set(theirs)
            if key != "dependencies" and mine.get(key) != theirs.get(key)
        )
        if differing:
            raise BadInput(
                "the feature schema differs from the featuriser this build implements in: "
                + ", ".join(differing)
            )
        return cls()

    @staticmethod
    def dependency_drift(schema: dict) -> dict:
        """``{name: {"recorded", "current"}}`` for each dependency whose version
        differs from the one in ``schema``; empty when they match.
        """
        current = feature_schema()["dependencies"]
        recorded = schema.get("dependencies") or {}
        return {
            name: {"recorded": recorded.get(name), "current": version}
            for name, version in current.items()
            if recorded.get(name) != version
        }

    def featurise(self, pairs: Sequence[tuple]) -> FeatureResult:
        """One row per pair that featurises, in input order. A bad structure or a
        nonfinite descriptor is reported in ``failures``; this never raises for a
        row and never reorders.
        """
        generator = _fpgen.GetMorganGenerator(
            radius=_FINGERPRINT["radius"],
            fpSize=_FINGERPRINT["fpSize"],
            includeChirality=_FINGERPRINT["includeChirality"],
            countSimulation=_FINGERPRINT["countSimulation"],
            useBondTypes=_FINGERPRINT["useBondTypes"],
            onlyNonzeroInvariants=_FINGERPRINT["onlyNonzeroInvariants"],
            includeRingMembership=_FINGERPRINT["includeRingMembership"],
            includeRedundantEnvironments=_FINGERPRINT["includeRedundantEnvironments"],
        )
        rows = []
        row_ids = []
        failures = []
        for row_id, smiles in pairs:
            try:
                vector, why = self._one(generator, smiles)
            except Exception as exc:  # RDKit can fail on a molecule it parsed
                vector, why = None, "RDKit could not featurise it: %s" % exc
            if vector is None:
                failures.append({"row_id": row_id, "reason": why})
            else:
                rows.append(vector)
                row_ids.append(row_id)
        if rows:
            matrix = np.vstack(rows).astype(np.float32, copy=False)
        else:
            matrix = np.zeros((0, N_FEATURES), dtype=np.float32)
        return FeatureResult(matrix=matrix, row_ids=row_ids, failures=failures)

    @staticmethod
    def _one(generator, smiles):
        mol, why = _parse(smiles)
        if mol is None:
            return None, why
        vector = np.empty(N_FEATURES, dtype=np.float32)
        vector[:N_BITS] = generator.GetFingerprintAsNumPy(mol)
        for i, (name, _, function) in enumerate(_DESCRIPTORS):
            value = float(function(mol))
            # finite as stored: a value float32 cannot hold would become infinity
            if not math.isfinite(value) or abs(value) > _FLOAT32_MAX:
                return None, "descriptor %s is not finite" % name
            vector[N_BITS + i] = value
        return vector, None


def _json_copy(value):
    if isinstance(value, dict):
        return {key: _json_copy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_copy(item) for item in value]
    return value


def require_all(result: FeatureResult) -> FeatureResult:
    """``result`` itself, or ``BadInput`` naming up to 20 failing row ids. Training
    uses it; inference reports failures per row instead.
    """
    failures = result.failures
    if not failures:
        return result
    shown = ", ".join(str(f["row_id"]) for f in failures[:20])
    more = len(failures) - 20
    message = "%d row(s) could not be featurised (row ids: %s%s); first reason: %s" % (
        len(failures),
        shown,
        " and %d more" % more if more > 0 else "",
        failures[0]["reason"],
    )
    raise BadInput(message)
