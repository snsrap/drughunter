"""The QSAR tuning, evaluation and refit engine: pure Python over rows.

A resolved dataset (``dataset.json``'s metadata and ``rows.json``'s rows), a
seed, the production strategy and a search profile in; a ``TrainingRun`` out. It
reads and writes no file and knows no command line -- ``cli.py`` writes the run
directory from what it returns -- so it can be tested with fakes.

*Procedure.* Both strategies are requested from ``split.split`` with the same
seed. The production strategy is frozen first: if its split is unavailable the
engine stops, before featurising or fitting anything, and says so. The other
strategy being unavailable is reported in the run and never replaced. For each
available strategy and each of its five repeats, every grid candidate is fitted
on the repeat's **train rows only** and scored on its **validation rows** (RMSE
for regression, log loss for classification); the lowest score wins, ties going
to fewer trees, then shallower trees, then a lower learning rate; the winner is
then scored **once** on the repeat's test rows. The production candidate is the
one with the lowest *mean* validation loss over the production strategy's five
repeats (same tie-break); it is refitted on exactly all eligible rows.

*No leakage, by construction.* ``tune`` receives the train and validation
matrices and labels and nothing else: it has no parameter a test row could
arrive through, and the production choice (``select``) is a function of
validation losses alone. ``evaluate_test`` is a separate function, called with
the booster ``tune`` already chose. Test scores are recorded; nothing reads them
back.

*Determinism.* Every fit's seed is 31 bits of SHA-256 over the run seed, the
strategy, the repeat (or ``"production"``) and the candidate -- never ``hash()``,
never global state. XGBoost runs ``hist`` on the CPU with one thread, so the same
dataset, seed, profile and library versions give the same model.
"""

from __future__ import annotations

import hashlib
import math
import platform
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .errors import BadInput, ToolUnavailable
from .features import FEATURISER_VERSION, Featuriser, feature_schema, require_all
from .metrics import (
    BINARY_METRICS,
    MULTICLASS_METRICS,
    REGRESSION_METRICS,
    aggregate,
    classification_metrics,
    regression_metrics,
)
from .scaffold import SCAFFOLD_POLICY, murcko_scaffold
from .split import ALGORITHM_VERSION as SPLIT_ALGORITHM
from .split import REPEATS, STRATEGIES, eligible_rows, split

try:
    import xgboost as xgb
except ImportError as _exc:  # reported by the command line as exit 3
    xgb = None
    _XGBOOST_ERROR = str(_exc)

__all__ = [
    "FIXED_PARAMS",
    "FULL_GRID",
    "PROFILES",
    "RUN_SCHEMA",
    "TIE_BREAK",
    "TINY_GRID",
    "Target",
    "Tuned",
    "TrainingRun",
    "candidate_seed",
    "evaluate_test",
    "fit",
    "grid_for",
    "make_target",
    "predict",
    "require_xgboost",
    "select",
    "train",
    "tune",
    "validation_loss",
    "xgb_params",
]

RUN_SCHEMA = "qsar-run-v1"
MODEL_FAMILY = "xgboost"

#: The fixed search: 2 x 2 x 3 = 12 candidates, in this order (depth outermost).
FULL_GRID = tuple(
    {"max_depth": depth, "learning_rate": rate, "n_estimators": trees}
    for depth in (3, 6)
    for rate in (0.05, 0.1)
    for trees in (100, 300, 600)
)
#: The CI profile: two candidates that differ in the number of trees, so that
#: selection and its tie-break still run, with trees small enough to be quick.
TINY_GRID = tuple(
    {"max_depth": 3, "learning_rate": 0.1, "n_estimators": trees} for trees in (10, 20)
)
PROFILES = {"full": FULL_GRID, "tiny": TINY_GRID}

#: Ties in validation loss go to the smaller value of each, in this order.
TIE_BREAK = ("n_estimators", "max_depth", "learning_rate")

#: What every candidate shares. ``nthread`` 1 and ``hist`` on the CPU make a fit
#: reproducible; there is no early stopping.
FIXED_PARAMS = {
    "subsample": 1.0,
    "colsample_bytree": 1.0,
    "tree_method": "hist",
    "device": "cpu",
    "nthread": 1,
    "early_stopping": False,
}

Progress = Callable[[str, str, int, int], None]


def require_xgboost():
    """The ``xgboost`` module, or ``ToolUnavailable`` (exit 3)."""
    if xgb is None:
        raise ToolUnavailable("XGBoost cannot be imported here: %s" % _XGBOOST_ERROR)
    return xgb


def grid_for(profile: str) -> list[dict]:
    """The candidates of a profile, each ``{max_depth, learning_rate, n_estimators}``."""
    if profile not in PROFILES:
        raise BadInput("unknown profile %r (want %s)" % (profile, " or ".join(PROFILES)))
    return [dict(c) for c in PROFILES[profile]]


def candidate_seed(seed: int, strategy: str, scope: int | str, candidate: int) -> int:
    """The seed of one fit: 31 bits of SHA-256 over the run seed, the strategy,
    the repeat (an index, or ``"production"``) and the candidate's index.
    """
    text = "drughunter-train|%d|%s|%s|%d" % (seed, strategy, scope, candidate)
    return int.from_bytes(hashlib.sha256(text.encode()).digest()[:4], "big") >> 1


# -- what is predicted -----------------------------------------------------------------


@dataclass(frozen=True)
class Target:
    """What the model predicts and how its labels are read.

    ``kind`` is ``regression``, ``binary`` or ``multiclass``. A binary label is
    encoded 1 for the positive class and 0 for the other, so the model's output
    is the positive-class probability; a multiclass label is its index in
    ``classes`` (the dataset's stable class order).
    """

    kind: str
    classes: tuple[str, ...]
    positive_class: str | None

    @property
    def objective(self) -> str:
        return {
            "regression": "reg:squarederror",
            "binary": "binary:logistic",
            "multiclass": "multi:softprob",
        }[self.kind]

    @property
    def loss(self) -> str:
        return "rmse" if self.kind == "regression" else "log_loss"

    @property
    def metrics(self) -> tuple[str, ...]:
        return {
            "regression": REGRESSION_METRICS,
            "binary": BINARY_METRICS,
            "multiclass": MULTICLASS_METRICS,
        }[self.kind]

    def encode(self, labels: Sequence) -> np.ndarray:
        """Labels as the numbers XGBoost fits."""
        if self.kind == "regression":
            return np.asarray(labels, dtype=np.float64)
        if self.kind == "binary":
            return np.asarray([1.0 if x == self.positive_class else 0.0 for x in labels])
        index = {c: i for i, c in enumerate(self.classes)}
        return np.asarray([index[x] for x in labels], dtype=np.float64)


def make_target(meta: Mapping[str, Any]) -> Target:
    """The ``Target`` of a resolved dataset, or ``BadInput``."""
    task = meta.get("target_kind")
    if task == "regression":
        return Target("regression", (), None)
    if task != "classification":
        raise BadInput("this dataset has no target, so a model cannot be trained on it")
    classes = tuple(meta.get("classes") or ())
    if len(classes) < 2:
        raise BadInput("a classification dataset needs at least two classes")
    if len(classes) == 2:
        positive = (meta.get("target") or {}).get("positive_class")
        if positive not in classes:
            raise BadInput("a binary dataset needs its positive class, one of %r" % (classes,))
        return Target("binary", classes, positive)
    return Target("multiclass", classes, None)


# -- fitting, scoring ---------------------------------------------------------------------


def xgb_params(target: Target, config: Mapping[str, Any], seed: int) -> dict:
    """The XGBoost parameters of one fit (``n_estimators`` is the round count)."""
    params = {
        "objective": target.objective,
        "max_depth": config["max_depth"],
        "eta": config["learning_rate"],
        "subsample": FIXED_PARAMS["subsample"],
        "colsample_bytree": FIXED_PARAMS["colsample_bytree"],
        "tree_method": FIXED_PARAMS["tree_method"],
        "device": FIXED_PARAMS["device"],
        "nthread": FIXED_PARAMS["nthread"],
        "seed": seed,
        "verbosity": 0,
    }
    if target.kind == "multiclass":
        params["num_class"] = len(target.classes)
    return params


def fit(target: Target, config: Mapping[str, Any], seed: int, X: np.ndarray, y: Sequence):
    """One booster, fitted on ``X`` and the raw labels ``y``."""
    lib = require_xgboost()
    matrix = lib.DMatrix(X, label=target.encode(y), nthread=1)
    return lib.train(
        xgb_params(target, config, seed), matrix, num_boost_round=config["n_estimators"]
    )


def predict(target: Target, booster, X: np.ndarray) -> np.ndarray:
    """A float64 vector (regression value; binary positive-class probability) or
    an ``[n, k]`` matrix of class probabilities in ``target.classes`` order.
    """
    lib = require_xgboost()
    out = booster.predict(lib.DMatrix(X, nthread=1))
    return np.asarray(out, dtype=np.float64)


def _score(target: Target, y: Sequence, output: np.ndarray) -> dict:
    """Every metric of the task: ``{"values", "undefined"}``."""
    if not np.isfinite(output).all():
        raise BadInput("the model produced a prediction that is not finite")
    if target.kind == "regression":
        return regression_metrics(list(y), output)
    return classification_metrics(list(y), output, list(target.classes), target.positive_class)


def validation_loss(target: Target, y: Sequence, output: np.ndarray) -> float:
    """RMSE (regression) or log loss (classification) of ``output`` against ``y``."""
    return _score(target, y, output)["values"][target.loss]


def select(losses: Sequence[float], grid: Sequence[Mapping[str, Any]]) -> int:
    """The index of the lowest loss, ties going to fewer trees, then shallower
    trees, then a lower learning rate. A function of validation losses alone.
    """
    return min(
        range(len(grid)),
        key=lambda i: (
            losses[i],
            grid[i]["n_estimators"],
            grid[i]["max_depth"],
            grid[i]["learning_rate"],
            i,
        ),
    )


@dataclass
class Tuned:
    """What ``tune`` found: every candidate's validation loss (``trace``), the
    index of the winner and the winner's booster, fitted on the train rows only.
    """

    trace: list[dict]
    selected: int
    booster: Any = field(repr=False)


def tune(
    target: Target,
    grid: Sequence[Mapping[str, Any]],
    seeds: Sequence[int],
    X_train: np.ndarray,
    y_train: Sequence,
    X_val: np.ndarray,
    y_val: Sequence,
    tick: Callable[[], None] = lambda: None,
) -> Tuned:
    """Fit every candidate on the train rows, score it on the validation rows,
    keep the winner. It is given no other rows: a test row cannot select.
    """
    best = None
    losses: list[float] = []
    trace = []
    for i, config in enumerate(grid):
        booster = fit(target, config, seeds[i], X_train, y_train)
        loss = validation_loss(target, y_val, predict(target, booster, X_val))
        losses.append(loss)
        trace.append({"candidate": i, "config": dict(config), "seed": seeds[i], "loss": loss})
        tick()
        if best is None or select(losses, grid[: len(losses)]) == i:
            best = (i, booster)
    return Tuned(trace, best[0], best[1])


def evaluate_test(target: Target, booster, X_test: np.ndarray, y_test: Sequence) -> dict:
    """The chosen booster's metrics on the test rows, once: ``{"values", "undefined"}``."""
    return _score(target, y_test, predict(target, booster, X_test))


# -- the run -------------------------------------------------------------------------------


@dataclass
class TrainingRun:
    """Everything a run directory is written from. ``unavailable`` is set, and
    nothing else is, when the production strategy has no split.
    """

    production_strategy: str
    splits: dict
    unavailable: dict | None = None
    strategies: dict = field(default_factory=dict)
    validation_trace: dict = field(default_factory=dict)
    production: dict | None = None
    booster: Any = field(default=None, repr=False)
    features: dict | None = None
    scaffolds: dict | None = None
    report: dict = field(default_factory=dict)
    refit_row_ids: list = field(default_factory=list)


def _versions() -> dict:
    from . import __version__

    lib = require_xgboost()
    from rdkit import rdBase

    return {
        "ligandbench": __version__,
        "rdkit": rdBase.rdkitVersion,
        "xgboost": lib.__version__,
        "numpy": np.__version__,
        "python": platform.python_version(),
        "scaffold_policy": SCAFFOLD_POLICY,
        "split_algorithm": SPLIT_ALGORITHM,
        "featuriser": FEATURISER_VERSION,
    }


def _target_record(meta: Mapping[str, Any], target: Target) -> dict:
    confirmed = dict(meta.get("target") or {})
    confirmed.update(
        task=meta["target_kind"],
        units=confirmed.get("output_units"),
        classes=list(target.classes) if target.classes else None,
        positive_class=target.positive_class,
    )
    return confirmed


def train(
    meta: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    *,
    seed: int,
    production_strategy: str,
    profile: str = "full",
    dataset_id: str | None = None,
    progress: Progress | None = None,
    featuriser: Featuriser | None = None,
) -> TrainingRun:
    """Tune, evaluate and refit; see the module docstring.

    ``progress(state, stage, completed, total)`` is told about each fit.
    ``BadInput`` for a dataset or a choice that cannot be used, naming the row ids
    of any structure that does not featurise.
    """
    require_xgboost()
    note = progress or (lambda *_: None)
    if production_strategy not in STRATEGIES:
        raise BadInput(
            "unknown production strategy %r (want %s)"
            % (production_strategy, " or ".join(STRATEGIES))
        )
    grid = grid_for(profile)
    target = make_target(meta)

    note("splitting", "requesting both strategies", 0, 0)
    splits = {name: split(meta, rows, strategy=name, seed=seed) for name in STRATEGIES}
    if splits[production_strategy]["status"] != "ok":
        # Frozen before anything is trained, and never replaced.
        return TrainingRun(
            production_strategy,
            splits,
            unavailable=splits[production_strategy]["unavailable"],
        )
    available = [name for name in STRATEGIES if splits[name]["status"] == "ok"]
    total = len(grid) * REPEATS * len(available) + 1
    done = 0

    def tick() -> None:
        nonlocal done
        done += 1
        note("tuning", "fitting candidates", done, total)

    # One featurisation, indexed by row id; every strategy and repeat reads it.
    note("featurizing", "computing features", 0, total)
    use, _ = eligible_rows(meta, rows)
    by_id = {r["row_id"]: r for r in rows}
    featuriser = featuriser or Featuriser()
    result = require_all(featuriser.featurise([(r["row_id"], r["smiles"]) for r in use]))
    position = {rid: i for i, rid in enumerate(result.row_ids)}
    X_all = result.matrix
    if target.kind == "regression":
        y_all = [float(by_id[rid]["target"]) for rid in result.row_ids]
        if not all(math.isfinite(v) for v in y_all):
            raise BadInput("a target value is not finite")
    else:
        y_all = [by_id[rid]["target"] for rid in result.row_ids]

    def take(ids: Sequence[int]):
        idx = [position[i] for i in ids]
        return X_all[idx], [y_all[i] for i in idx]

    strategies: dict[str, Any] = {}
    trace: dict[str, Any] = {}
    for name in STRATEGIES:
        record = splits[name]
        if record["status"] != "ok":
            strategies[name] = {
                "status": "split_unavailable",
                "unavailable": record["unavailable"],
                "repeats": [],
                "aggregate": {},
            }
            trace[name] = {"status": "split_unavailable", "repeats": []}
            continue
        repeats, repeat_trace = [], []
        for r in record["repeats"]:
            seeds = [candidate_seed(seed, name, r["repeat"], i) for i in range(len(grid))]
            X_tr, y_tr = take(r["train"])
            X_va, y_va = take(r["validation"])
            tuned = tune(target, grid, seeds, X_tr, y_tr, X_va, y_va, tick)
            X_te, y_te = take(r["test"])
            scored = evaluate_test(target, tuned.booster, X_te, y_te)
            repeats.append(
                {
                    "repeat": r["repeat"],
                    "seed": r["seed"],
                    "candidate": tuned.selected,
                    "config": dict(grid[tuned.selected]),
                    "validation": [
                        {"candidate": t["candidate"], "loss": t["loss"]} for t in tuned.trace
                    ],
                    "sizes": dict(r["sizes"]),
                    "test": scored["values"],
                    "undefined": scored["undefined"],
                }
            )
            repeat_trace.append(
                {"repeat": r["repeat"], "selected": tuned.selected, "candidates": tuned.trace}
            )
        strategies[name] = {
            "status": "ok",
            "unavailable": None,
            "repeats": repeats,
            "aggregate": {
                m: aggregate([rep["test"][m] for rep in repeats]) for m in target.metrics
            },
        }
        trace[name] = {"status": "ok", "repeats": repeat_trace}

    # The production candidate: the lowest mean validation loss over the production
    # strategy's repeats. Only validation losses are read here.
    chosen = trace[production_strategy]["repeats"]
    per_candidate = [[rep["candidates"][i]["loss"] for rep in chosen] for i in range(len(grid))]
    means = [math.fsum(v) / len(v) for v in per_candidate]
    picked = select(means, grid)
    candidates = [
        {
            "candidate": i,
            "config": dict(grid[i]),
            "validation_losses": per_candidate[i],
            "mean_validation_loss": means[i],
        }
        for i in range(len(grid))
    ]
    production_trace = {
        "strategy": production_strategy,
        "candidates": candidates,
        "selected": picked,
        "tie_break": list(TIE_BREAK),
    }

    note("refitting", "fitting the production model on all eligible rows", done, total)
    refit_ids = list(splits[production_strategy]["row_ids"])
    X_fit, y_fit = take(refit_ids)
    booster = fit(
        target,
        grid[picked],
        candidate_seed(seed, production_strategy, "production", picked),
        X_fit,
        y_fit,
    )
    tick()

    refit_smiles = {r["smiles"] for r in use}
    scaffolds = {
        "scaffold_policy": SCAFFOLD_POLICY,
        "scaffolds": sorted({murcko_scaffold(s) for s in refit_smiles}),
    }
    schema = feature_schema()
    target_record = _target_record(meta, target)
    production = {
        "strategy": production_strategy,
        "config": dict(grid[picked]),
        "mean_validation_loss": means[picked],
        "n_refit_rows": len(refit_ids),
        "tie_break": list(TIE_BREAK),
    }
    features = {
        "feature_schema": schema,
        "target": {
            **target_record,
            "kind": target.kind,
            "objective": target.objective,
            "num_class": len(target.classes) if target.kind == "multiclass" else None,
            "label_encoding": {
                "regression": "the target as a number",
                "binary": "1 for positive_class, 0 for the other; output is P(positive_class)",
                "multiclass": "the index in classes; output is one probability per class, in order",
            }[target.kind],
        },
    }
    run_trace = {
        "loss": target.loss,
        "profile": profile,
        "grid": grid,
        "tie_break": list(TIE_BREAK),
        "strategies": trace,
        "production": production_trace,
    }
    report = {
        "model_family": MODEL_FAMILY,
        "seed": seed,
        "profile": profile,
        "grid": grid,
        "fixed_params": dict(FIXED_PARAMS),
        "objective": target.objective,
        "validation_loss": target.loss,
        "dataset": {
            "id": dataset_id,
            "content_hash": meta.get("content_hash"),
            "n_rows": len(rows),
            "n_eligible": len(use),
        },
        "target": target_record,
        "production": production,
        "strategies": strategies,
        "feature_schema": schema,
        "versions": _versions(),
    }
    return TrainingRun(
        production_strategy,
        splits,
        strategies=strategies,
        validation_trace=run_trace,
        production=production,
        booster=booster,
        features=features,
        scaffolds=scaffolds,
        report=report,
        refit_row_ids=refit_ids,
    )
