"""Evaluation metrics, in numpy, with every convention written down.

A metric that cannot be computed is ``None`` with a reason, never ``nan`` and
never a quiet zero, so a result stays valid canonical JSON and a reader can tell
"bad model" from "this test set cannot say". ``aggregate`` then averages only
the defined values and reports how many there were.

The conventions (the table in docs/ligandbench.md is the same list):

* log loss: natural log; each class probability (``p`` and ``1 - p`` in the binary
  case) is clipped to ``[LOG_LOSS_EPS, 1 - LOG_LOSS_EPS]``, with no renormalisation
  afterwards, so one confident mistake costs at most ``-log(1e-15)`` = 34.54.
* ROC-AUC: the rank-sum (Mann-Whitney) form with average ranks, so tied scores
  between a positive and a negative count one half.
* average precision: ``sum_n (R_n - R_{n-1}) P_n`` over the distinct score
  thresholds, tied scores being one threshold (scikit-learn's definition).
* Spearman: Pearson correlation of average ranks.
* class decisions: probability ``>= 0.5`` for binary; ``argmax`` for multiclass,
  the first class winning an exact tie. Thresholds are never tuned.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np

from .errors import BadInput

__all__ = [
    "LOG_LOSS_EPS",
    "THRESHOLD",
    "REGRESSION_METRICS",
    "BINARY_METRICS",
    "MULTICLASS_METRICS",
    "regression_metrics",
    "classification_metrics",
    "aggregate",
]

LOG_LOSS_EPS = 1e-15
THRESHOLD = 0.5
# how far a probability row may be from summing to one (float32 softmax output)
_ROW_SUM_TOLERANCE = 1e-4

REGRESSION_METRICS = ("rmse", "mae", "r2", "spearman")
BINARY_METRICS = ("roc_auc", "average_precision", "balanced_accuracy", "log_loss")
MULTICLASS_METRICS = (
    "roc_auc_ovr_macro",
    "average_precision_macro",
    "balanced_accuracy",
    "log_loss",
)


# -- inputs -------------------------------------------------------------------------


def _vector(values, name: str) -> np.ndarray:
    try:
        array = np.asarray(values, dtype=np.float64)
    except (TypeError, ValueError):
        raise BadInput("%s must be numbers" % name) from None
    if array.ndim != 1:
        raise BadInput("%s must be one-dimensional, not shape %s" % (name, array.shape))
    if not np.isfinite(array).all():
        raise BadInput("%s holds a value that is not finite" % name)
    return array


def _plain(value) -> float:
    return float(value)


def _empty(names, reason):
    return {"values": {name: None for name in names}, "undefined": {name: reason for name in names}}


def _result(names, outcomes):
    """``outcomes`` is ``{name: float | ("undefined", reason)}``."""
    values, undefined = {}, {}
    for name in names:
        outcome = outcomes[name]
        if isinstance(outcome, tuple):
            values[name] = None
            undefined[name] = outcome[1]
        else:
            values[name] = _plain(outcome)
    return {"values": values, "undefined": undefined}


def _undefined(reason):
    return ("undefined", reason)


# -- ranks ----------------------------------------------------------------------------


def _average_ranks(x: np.ndarray) -> np.ndarray:
    """1-based ranks, tied values sharing the mean of the ranks they span."""
    order = np.argsort(x, kind="mergesort")
    sorted_x = x[order]
    n = len(x)
    ranks = np.empty(n, dtype=np.float64)
    starts = np.concatenate(([0], np.flatnonzero(sorted_x[1:] != sorted_x[:-1]) + 1))
    ends = np.concatenate((starts[1:], [n]))
    for start, end in zip(starts, ends):
        ranks[order[start:end]] = (start + 1 + end) / 2.0
    return ranks


def _pearson(a: np.ndarray, b: np.ndarray) -> float:
    a = a - a.mean()
    b = b - b.mean()
    denominator = math.sqrt(float((a * a).sum()) * float((b * b).sum()))
    return max(-1.0, min(1.0, float((a * b).sum()) / denominator))


def _roc_auc(positive: np.ndarray, score: np.ndarray):
    n_pos = int(positive.sum())
    n_neg = len(positive) - n_pos
    if n_pos == 0:
        return None, "no positive rows"
    if n_neg == 0:
        return None, "no negative rows"
    ranks = _average_ranks(score)
    auc = (float(ranks[positive].sum()) - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)
    return max(0.0, min(1.0, auc)), None


def _average_precision(positive: np.ndarray, score: np.ndarray):
    n_pos = int(positive.sum())
    if n_pos == 0:
        return None, "no positive rows"
    order = np.argsort(-score, kind="mergesort")
    s = score[order]
    y = positive[order].astype(np.float64)
    # the last row of each run of equal scores: one threshold per distinct score
    last = np.concatenate((np.flatnonzero(s[1:] != s[:-1]), [len(s) - 1]))
    tp = np.cumsum(y)[last]
    precision = tp / (last + 1.0)
    recall = tp / n_pos
    previous = np.concatenate(([0.0], recall[:-1]))
    return max(0.0, min(1.0, float(((recall - previous) * precision).sum()))), None


# -- regression -----------------------------------------------------------------------


def regression_metrics(y_true: Sequence[float], y_pred: Sequence[float]) -> dict:
    """RMSE, MAE, R-squared and Spearman, as ``{"values", "undefined"}``.

    Undefined: everything with no rows; R-squared when ``y_true`` is constant
    (its total variance is zero); Spearman with fewer than two rows or when
    either input is constant (a rank correlation needs spread in both).
    Inputs that are not finite, or of different lengths, raise ``BadInput``.
    """
    true = _vector(y_true, "y_true")
    pred = _vector(y_pred, "y_pred")
    if true.shape != pred.shape:
        raise BadInput("y_true has %d values and y_pred has %d" % (len(true), len(pred)))
    n = len(true)
    if n == 0:
        return _empty(REGRESSION_METRICS, "no rows")
    error = pred - true
    out = {
        "rmse": math.sqrt(float((error * error).mean())),
        "mae": float(np.abs(error).mean()),
    }
    total = float(((true - true.mean()) ** 2).sum())
    if total == 0.0:
        out["r2"] = _undefined("y_true is constant")
    else:
        out["r2"] = 1.0 - float((error * error).sum()) / total
    if n < 2:
        out["spearman"] = _undefined("fewer than two rows")
    elif true.max() == true.min():
        out["spearman"] = _undefined("y_true is constant")
    elif pred.max() == pred.min():
        out["spearman"] = _undefined("y_pred is constant")
    else:
        out["spearman"] = _pearson(_average_ranks(true), _average_ranks(pred))
    return _result(REGRESSION_METRICS, out)


# -- classification -------------------------------------------------------------------


def _clip_log(p: np.ndarray) -> np.ndarray:
    return np.log(np.clip(p, LOG_LOSS_EPS, 1.0 - LOG_LOSS_EPS))


def _label_indices(y_true, classes: list) -> np.ndarray:
    lookup = {label: i for i, label in enumerate(classes)}
    out = []
    for label in y_true:
        try:
            out.append(lookup[label])
        except (KeyError, TypeError):
            raise BadInput(
                "y_true holds %r, which is not one of the classes %r" % (label, classes)
            ) from None
    return np.asarray(out, dtype=np.int64)


def _probabilities(proba, n: int, k: int) -> np.ndarray:
    try:
        p = np.asarray(proba, dtype=np.float64)
    except (TypeError, ValueError):
        raise BadInput("proba must be numbers") from None
    if not np.isfinite(p).all():
        raise BadInput("proba holds a value that is not finite")
    if p.size and (p.min() < 0.0 or p.max() > 1.0):
        raise BadInput("proba holds a value outside [0, 1]")
    if p.ndim == 2:
        if p.shape != (n, k):
            raise BadInput("proba has shape %s, expected (%d, %d)" % (p.shape, n, k))
        if n and np.abs(p.sum(axis=1) - 1.0).max() > _ROW_SUM_TOLERANCE:
            raise BadInput("a row of proba does not sum to 1")
    return p


def classification_metrics(y_true: Sequence, proba, classes: Sequence, positive_class=None) -> dict:
    """Metrics for a classifier, as ``{"values", "undefined"}``.

    ``classes`` is the stable class order (two or more distinct labels);
    ``y_true`` holds those labels.

    Binary (two classes): metrics are ``roc_auc``, ``average_precision``,
    ``balanced_accuracy``, ``log_loss``. ``proba`` is the probability of the
    positive class, a vector of length n; or an ``[n, 2]`` matrix in ``classes``
    order, whose rows sum to one and whose positive column is used.
    ``positive_class`` defaults to ``classes[1]``. A row is predicted positive at
    probability ``>= 0.5``.

    Multiclass (three or more): ``proba`` is an ``[n, k]`` matrix in ``classes``
    order; metrics are ``roc_auc_ovr_macro``, ``average_precision_macro``,
    ``balanced_accuracy``, ``log_loss``. The prediction is the argmax, the first
    class winning an exact tie. ``positive_class`` must be ``None``.

    A metric that averages over classes is ``None`` -- with a reason that names
    the class -- if any class cannot contribute; it is never an average over a
    subset. Malformed input (unknown label, probabilities outside [0, 1] or not
    summing to one, wrong shape) raises ``BadInput``.
    """
    classes = list(classes)
    k = len(classes)
    if k < 2 or len(set(classes)) != k:
        raise BadInput("classes must be two or more distinct labels")
    y_list = list(y_true)
    n = len(y_list)
    labels = _label_indices(y_list, classes)
    if k > 2 and positive_class is not None:
        raise BadInput("positive_class applies to a binary task only")
    if n == 0:
        return _empty(BINARY_METRICS if k == 2 else MULTICLASS_METRICS, "no rows")

    if k == 2:
        if positive_class is None:
            positive_class = classes[1]
        if positive_class not in classes:
            raise BadInput(
                "positive_class %r is not one of the classes %r" % (positive_class, classes)
            )
        pos = classes.index(positive_class)
        p = _probabilities(proba, n, 2)
        if p.ndim == 2:
            p = p[:, pos]
        elif p.ndim == 1:
            if len(p) != n:
                raise BadInput("proba has %d values for %d rows" % (len(p), n))
        else:
            raise BadInput("proba must be a vector or an [n, 2] matrix")
        return _binary(labels == pos, p, classes, pos, BINARY_METRICS)

    p = _probabilities(proba, n, k)
    if p.ndim != 2:
        raise BadInput("proba must be an [n, %d] matrix for %d classes" % (k, k))
    return _multiclass(labels, p, classes, MULTICLASS_METRICS)


def _recalls(labels: np.ndarray, predicted: np.ndarray, classes: list):
    """``(mean recall, None)`` over all classes, or ``(None, reason)`` naming a
    class with no rows in ``labels``.
    """
    recalls = []
    for index, name in enumerate(classes):
        member = labels == index
        if not member.any():
            return None, "class %s has no rows in y_true" % name
        recalls.append(float((predicted[member] == index).mean()))
    return math.fsum(recalls) / len(recalls), None


def _outcome(pair):
    value, reason = pair
    return _undefined(reason) if value is None else value


def _binary(positive: np.ndarray, p: np.ndarray, classes: list, pos: int, names):
    neg = 1 - pos
    predicted = np.where(p >= THRESHOLD, pos, neg)
    labels = np.where(positive, pos, neg)
    log_p, log_q = _clip_log(p), _clip_log(1.0 - p)
    out = {
        "roc_auc": _outcome(_roc_auc(positive, p)),
        "average_precision": _outcome(_average_precision(positive, p)),
        "balanced_accuracy": _outcome(_recalls(labels, predicted, classes)),
        "log_loss": -float(np.where(positive, log_p, log_q).mean()),
    }
    return _result(names, out)


def _multiclass(labels: np.ndarray, p: np.ndarray, classes: list, names):
    n, k = p.shape
    predicted = np.argmax(p, axis=1)
    aucs, aps = [], []
    auc_reason = ap_reason = None
    for index, name in enumerate(classes):
        positive = labels == index
        auc, why = _roc_auc(positive, p[:, index])
        if auc is None:
            auc_reason = auc_reason or "class %s has %s in y_true" % (name, why)
        else:
            aucs.append(auc)
        ap, why = _average_precision(positive, p[:, index])
        if ap is None:
            ap_reason = ap_reason or "class %s has %s in y_true" % (name, why)
        else:
            aps.append(ap)
    out = {
        "roc_auc_ovr_macro": _undefined(auc_reason) if auc_reason else math.fsum(aucs) / k,
        "average_precision_macro": _undefined(ap_reason) if ap_reason else math.fsum(aps) / k,
        "balanced_accuracy": _outcome(_recalls(labels, predicted, classes)),
        "log_loss": -float(_clip_log(p)[np.arange(n), labels].mean()),
    }
    return _result(names, out)


# -- aggregation ----------------------------------------------------------------------


def aggregate(values: Sequence) -> dict:
    """``{"mean", "sd", "n_defined", "n_total"}`` over the defined (non-``None``)
    values. ``mean`` and ``sd`` are ``None`` with no defined value; ``sd`` is the
    sample standard deviation (ddof 1) and ``None`` with fewer than two.
    """
    values = list(values)
    defined = []
    for value in values:
        if value is None:
            continue
        number = float(value)
        if not math.isfinite(number):
            raise BadInput("aggregate was given a value that is not finite")
        defined.append(number)
    n = len(defined)
    mean = math.fsum(defined) / n if n else None
    sd = None
    if n >= 2:
        sd = math.sqrt(math.fsum((x - mean) ** 2 for x in defined) / (n - 1))
    return {"mean": mean, "sd": sd, "n_defined": n, "n_total": len(values)}
