"""Evaluation metrics: each convention pinned on a case small enough to do by hand,
plus randomised cross-checks against slow, obviously-correct references written
here (no scipy, no scikit-learn).
"""

import json
import math
import random
from pathlib import Path

import pytest

from ligandbench import metrics as M
from ligandbench.errors import BadInput
from ligandbench.metrics import aggregate, classification_metrics, regression_metrics

DOCS = Path(__file__).resolve().parents[2] / "docs" / "ligandbench.md"


def values(result):
    return result["values"]


# -- references: slow and obviously right -----------------------------------------------


def ref_ranks(x):
    # rank = 1 + (how many are smaller) + (how many other ties) / 2
    return [1 + sum(v < xi for v in x) + (sum(v == xi for v in x) - 1) / 2 for xi in x]


def ref_pearson(a, b):
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    return cov / math.sqrt(va * vb)


def ref_auc(positive, score):
    # the fraction of (positive, negative) pairs the score orders correctly, a tie half
    num = den = 0.0
    for yi, si in zip(positive, score):
        for yj, sj in zip(positive, score):
            if yi and not yj:
                den += 1
                num += 1.0 if si > sj else 0.5 if si == sj else 0.0
    return num / den


def ref_ap(positive, score):
    # walk the distinct scores from high to low; at each, precision and recall of
    # "everything scored at least this"
    n_pos = sum(positive)
    total, previous_recall = 0.0, 0.0
    for threshold in sorted(set(score), reverse=True):
        chosen = [y for y, s in zip(positive, score) if s >= threshold]
        precision = sum(chosen) / len(chosen)
        recall = sum(chosen) / n_pos
        total += (recall - previous_recall) * precision
        previous_recall = recall
    return total


def ref_balanced(labels, predicted, k):
    recalls = []
    for c in range(k):
        members = [p for y, p in zip(labels, predicted) if y == c]
        recalls.append(sum(p == c for p in members) / len(members))
    return sum(recalls) / k


# -- regression, by hand ------------------------------------------------------------------


def test_rmse_mae_r2_on_a_case_done_by_hand():
    # errors 0, 0, 2: rmse = sqrt(4/3), mae = 2/3; mean(y)=2, SS_tot=1+0+1=2, SS_res=4, R2=1-4/2
    out = regression_metrics([1, 2, 3], [1, 2, 5])
    assert values(out)["rmse"] == pytest.approx(math.sqrt(4 / 3))
    assert values(out)["mae"] == pytest.approx(2 / 3)
    assert values(out)["r2"] == pytest.approx(-1.0)
    assert out["undefined"] == {}


def test_a_perfect_prediction_has_zero_error_and_unit_r2_and_correlation():
    out = regression_metrics([1.5, 2.5, 9.0], [1.5, 2.5, 9.0])
    assert values(out) == {"rmse": 0.0, "mae": 0.0, "r2": 1.0, "spearman": 1.0}


def test_spearman_of_a_rank_swap():
    # ranks equal the values; d = 0, 1, -1, 0 so rho = 1 - 6 * 2 / (4 * 15) = 0.8
    assert values(regression_metrics([1, 2, 3, 4], [1, 3, 2, 4]))["spearman"] == pytest.approx(0.8)


def test_spearman_uses_average_ranks_for_ties():
    # x ranks 1, 2.5, 2.5, 4 and y ranks 1, 2, 3, 4: centred (-1.5, 0, 0, 1.5) and
    # (-1.5, -.5, .5, 1.5); cov 4.5, variances 4.5 and 5, rho = 4.5 / sqrt(22.5) = 3 / sqrt(10)
    out = regression_metrics([1, 2, 2, 3], [1, 2, 3, 4])
    assert values(out)["spearman"] == pytest.approx(3 / math.sqrt(10))


def test_a_reversed_ordering_is_minus_one():
    assert values(regression_metrics([1, 2, 3], [3, 2, 1]))["spearman"] == pytest.approx(-1.0)


def test_a_constant_truth_leaves_r2_and_spearman_undefined_with_reasons():
    out = regression_metrics([2, 2, 2], [1, 2, 3])
    assert values(out)["r2"] is None and values(out)["spearman"] is None
    assert out["undefined"] == {"r2": "y_true is constant", "spearman": "y_true is constant"}
    assert values(out)["rmse"] == pytest.approx(math.sqrt(2 / 3))


def test_a_constant_prediction_leaves_spearman_undefined_but_not_r2():
    out = regression_metrics([1, 2, 3], [2, 2, 2])
    assert values(out)["r2"] == pytest.approx(1 - 2 / 2)  # SS_res = 2 = SS_tot
    assert out["undefined"] == {"spearman": "y_pred is constant"}


def test_one_row_has_error_but_no_r2_or_correlation():
    out = regression_metrics([4.0], [3.0])
    assert values(out)["rmse"] == 1.0 and values(out)["mae"] == 1.0
    assert set(out["undefined"]) == {"r2", "spearman"}
    assert out["undefined"]["spearman"] == "fewer than two rows"


def test_no_rows_leaves_every_metric_undefined():
    out = regression_metrics([], [])
    assert values(out) == {name: None for name in M.REGRESSION_METRICS}
    assert set(out["undefined"]) == set(M.REGRESSION_METRICS)


@pytest.mark.parametrize(
    "true,pred",
    [([1, 2], [1]), ([1, float("nan")], [1, 2]), ([1, 2], [1, float("inf")]), ([[1], [2]], [1, 2])],
)
def test_regression_refuses_malformed_input(true, pred):
    with pytest.raises(BadInput):
        regression_metrics(true, pred)


def test_regression_cross_check_against_loop_references():
    rng = random.Random(11)
    for _ in range(40):
        n = rng.randint(3, 25)
        y = [rng.choice([rng.random() * 10, round(rng.random() * 4)]) for _ in range(n)]
        p = [rng.choice([rng.random() * 10, round(rng.random() * 4)]) for _ in range(n)]
        if len(set(y)) < 2 or len(set(p)) < 2:
            continue
        out = values(regression_metrics(y, p))
        assert out["spearman"] == pytest.approx(ref_pearson(ref_ranks(y), ref_ranks(p)), abs=1e-12)
        mean = sum(y) / n
        assert out["r2"] == pytest.approx(
            1 - sum((a - b) ** 2 for a, b in zip(y, p)) / sum((a - mean) ** 2 for a in y), abs=1e-12
        )
        assert out["rmse"] == pytest.approx(math.sqrt(sum((a - b) ** 2 for a, b in zip(y, p)) / n))
        assert out["mae"] == pytest.approx(sum(abs(a - b) for a, b in zip(y, p)) / n)


# -- binary classification, by hand -----------------------------------------------------------

AB = ["neg", "pos"]


def binary(y, p, **kw):
    return classification_metrics(y, p, AB, **kw)


def test_binary_roc_auc_and_average_precision_on_the_textbook_case():
    # scores .1(neg) .4(neg) .35(pos) .8(pos). Positive-negative pairs: .35>.1 ok, .35<.4 no,
    # .8>.1 ok, .8>.4 ok: AUC = 3/4. Descending: .8 pos (P 1, R .5), .4 neg, .35 pos (P 2/3, R 1),
    # .1 neg: AP = .5 * 1 + .5 * 2/3 = 5/6.
    out = binary(["neg", "neg", "pos", "pos"], [0.1, 0.4, 0.35, 0.8])
    assert values(out)["roc_auc"] == pytest.approx(0.75)
    assert values(out)["average_precision"] == pytest.approx(5 / 6)


def test_roc_auc_perfect_and_worst():
    assert values(binary(["neg", "pos"], [0.1, 0.9]))["roc_auc"] == 1.0
    assert values(binary(["neg", "pos"], [0.9, 0.1]))["roc_auc"] == 0.0


def test_tied_scores_across_classes_count_one_half_in_roc_auc():
    assert values(binary(["neg", "pos"], [0.5, 0.5]))["roc_auc"] == 0.5
    # one positive scored .5, negatives .5 (tie, half) and .2 (win): (0.5 + 1) / 2
    assert values(binary(["neg", "neg", "pos"], [0.5, 0.2, 0.5]))["roc_auc"] == pytest.approx(0.75)


def test_tied_scores_are_one_threshold_in_average_precision():
    # all four scored .5: one threshold, precision 2/4, recall 1: AP = .5
    y = ["pos", "neg", "pos", "neg"]
    assert values(binary(y, [0.5] * 4))["average_precision"] == pytest.approx(0.5)
    assert values(binary(y[::-1], [0.5] * 4))["average_precision"] == pytest.approx(0.5)


def test_average_precision_is_not_the_trapezoid_area():
    # one positive ranked second of two: precision 1/2 at recall 1, so AP = 0.5 (trapezoid: .75)
    assert values(binary(["neg", "pos"], [0.9, 0.1]))["average_precision"] == pytest.approx(0.5)


def test_balanced_accuracy_uses_the_half_threshold_inclusive():
    # probabilities .9 .4 .6 .2 .1 for y 1 1 0 0 0: predicted 1 0 1 0 0;
    # recall(pos) = 1/2, recall(neg) = 2/3: (1/2 + 2/3) / 2 = 7/12
    y = ["pos", "pos", "neg", "neg", "neg"]
    assert values(binary(y, [0.9, 0.4, 0.6, 0.2, 0.1]))["balanced_accuracy"] == pytest.approx(
        7 / 12
    )
    # exactly 0.5 is positive
    assert values(binary(["pos", "neg"], [0.5, 0.5]))["balanced_accuracy"] == pytest.approx(0.5)
    assert values(binary(["pos", "neg"], [0.5, 0.49]))["balanced_accuracy"] == 1.0


def test_binary_log_loss_by_hand():
    # y 1, 0 with p .9, .2: -(ln .9 + ln .8) / 2
    out = binary(["pos", "neg"], [0.9, 0.2])
    assert values(out)["log_loss"] == pytest.approx(-(math.log(0.9) + math.log(0.8)) / 2)


def test_log_loss_clips_at_epsilon_and_never_returns_infinity():
    assert M.LOG_LOSS_EPS == 1e-15
    wrong = values(binary(["pos"], [0.0]))["log_loss"]
    assert wrong == pytest.approx(-math.log(1e-15))
    assert values(binary(["pos"], [1.0]))["log_loss"] == pytest.approx(
        -math.log(1 - 1e-15), abs=1e-12
    )
    assert math.isfinite(wrong)


def test_the_positive_class_defaults_to_the_second_class_and_can_be_chosen():
    y = ["neg", "neg", "pos", "pos"]
    p_pos = [0.1, 0.4, 0.35, 0.8]
    default = binary(y, p_pos)
    flipped = classification_metrics(y, [1 - p for p in p_pos], AB, positive_class="neg")
    assert values(default)["roc_auc"] == pytest.approx(values(flipped)["roc_auc"])
    assert values(default)["log_loss"] == pytest.approx(values(flipped)["log_loss"])
    # asked about "neg" the scores are .9 .6 .65 .2; descending: .9 neg, .65 pos, .6 neg, .2 pos,
    # so AP = .5 * 1 + .5 * 2/3 (the same 5/6 by symmetry of this case)
    assert values(flipped)["average_precision"] == pytest.approx(5 / 6)


def test_a_two_column_matrix_is_read_in_class_order_and_matches_the_vector():
    y = ["neg", "neg", "pos", "pos"]
    p_pos = [0.1, 0.4, 0.35, 0.8]
    matrix = [[1 - p, p] for p in p_pos]
    assert binary(y, matrix) == binary(y, p_pos)
    # the same matrix asked about the other class
    other = classification_metrics(y, matrix, AB, positive_class="neg")
    assert (
        values(other)
        == classification_metrics(y, [1 - p for p in p_pos], AB, positive_class="neg")["values"]
    )


def test_binary_labels_can_be_any_hashable_in_the_class_order_given():
    out = classification_metrics([1, 0, 1], [0.9, 0.2, 0.7], [0, 1])
    assert values(out)["roc_auc"] == 1.0


def test_binary_with_no_positive_rows():
    out = binary(["neg", "neg"], [0.2, 0.7])
    assert values(out)["roc_auc"] is None and values(out)["average_precision"] is None
    assert out["undefined"]["roc_auc"] == "no positive rows"
    assert out["undefined"]["balanced_accuracy"] == "class pos has no rows in y_true"
    assert values(out)["log_loss"] is not None


def test_binary_with_no_negative_rows_keeps_average_precision_but_not_roc_auc():
    out = binary(["pos", "pos"], [0.2, 0.7])
    assert out["undefined"]["roc_auc"] == "no negative rows"
    assert values(out)["average_precision"] == pytest.approx(1.0)
    assert out["undefined"]["balanced_accuracy"] == "class neg has no rows in y_true"


def test_binary_with_no_rows_is_all_undefined():
    out = binary([], [])
    assert all(v is None for v in values(out).values())
    assert set(out["undefined"]) == set(M.BINARY_METRICS)


def test_binary_cross_check_against_loop_references_with_ties():
    rng = random.Random(5)
    for _ in range(60):
        n = rng.randint(4, 30)
        y = [rng.randint(0, 1) for _ in range(n)]
        if sum(y) in (0, n):
            continue
        # coarse scores so ties are common
        p = [rng.randint(0, 6) / 6 for _ in range(n)]
        out = values(classification_metrics(y, p, [0, 1]))
        assert out["roc_auc"] == pytest.approx(ref_auc(y, p), abs=1e-12)
        assert out["average_precision"] == pytest.approx(ref_ap(y, p), abs=1e-12)
        predicted = [int(s >= 0.5) for s in p]
        assert out["balanced_accuracy"] == pytest.approx(ref_balanced(y, predicted, 2), abs=1e-12)
        # each class's probability (p and 1 - p) is clipped on its own
        loss = -sum(math.log(max(s if t else 1 - s, 1e-15)) for t, s in zip(y, p)) / n
        assert out["log_loss"] == pytest.approx(loss, abs=1e-12)


@pytest.mark.parametrize(
    "y,p,classes,kw",
    [
        (["neg", "other"], [0.1, 0.2], AB, {}),  # unknown label
        (["neg", "pos"], [0.1, 1.2], AB, {}),  # outside [0, 1]
        (["neg", "pos"], [0.1], AB, {}),  # wrong length
        (["neg", "pos"], [[0.5, 0.6], [0.4, 0.6]], AB, {}),  # rows do not sum to one
        (["neg", "pos"], [[0.5, 0.5]], AB, {}),  # wrong shape
        (["neg", "pos"], [0.1, float("nan")], AB, {}),
        (["neg", "pos"], [0.1, 0.9], AB, {"positive_class": "maybe"}),
        (["neg", "pos"], [0.1, 0.9], ["pos", "pos"], {}),  # repeated class
        (["neg", "pos"], [0.1, 0.9], ["neg"], {}),  # one class
    ],
)
def test_classification_refuses_malformed_input(y, p, classes, kw):
    with pytest.raises(BadInput):
        classification_metrics(y, p, classes, **kw)


def test_a_one_dimensional_probability_is_refused_for_three_classes():
    with pytest.raises(BadInput):
        classification_metrics(["a", "b", "c"], [0.1, 0.2, 0.3], ["a", "b", "c"])


def test_positive_class_is_refused_for_multiclass():
    with pytest.raises(BadInput):
        classification_metrics(
            ["a", "b", "c"], [[1, 0, 0]] * 3, ["a", "b", "c"], positive_class="a"
        )


# -- multiclass ---------------------------------------------------------------------------------

ABC = ["a", "b", "c"]


def test_multiclass_log_loss_and_perfect_separation_by_hand():
    y = ["a", "b", "c"]
    proba = [[0.7, 0.2, 0.1], [0.1, 0.8, 0.1], [0.2, 0.2, 0.6]]
    out = classification_metrics(y, proba, ABC)
    assert values(out)["log_loss"] == pytest.approx(
        -(math.log(0.7) + math.log(0.8) + math.log(0.6)) / 3
    )
    # every class's own probability is its column's highest: AUC and AP are 1, every argmax is right
    assert values(out)["roc_auc_ovr_macro"] == 1.0
    assert values(out)["average_precision_macro"] == 1.0
    assert values(out)["balanced_accuracy"] == 1.0
    assert out["undefined"] == {}


def test_multiclass_metric_names_say_they_are_macro_one_vs_rest():
    out = classification_metrics(["a", "b", "c"], [[1, 0, 0], [0, 1, 0], [0, 0, 1]], ABC)
    assert list(values(out)) == list(M.MULTICLASS_METRICS)
    assert "roc_auc_ovr_macro" in values(out) and "average_precision_macro" in values(out)


def test_macro_values_are_the_mean_of_the_per_class_one_vs_rest_values():
    # each class is scored against the other two on its own column; the macro value is
    # the plain mean of the three, checked against the loop references class by class
    y = ["a", "b", "c", "b", "a", "c"]
    proba = [
        [0.6, 0.3, 0.1],
        [0.2, 0.5, 0.3],
        [0.3, 0.3, 0.4],
        [0.4, 0.4, 0.2],
        [0.5, 0.2, 0.3],
        [0.1, 0.6, 0.3],
    ]
    out = values(classification_metrics(y, proba, ABC))
    aucs = [ref_auc([int(t == c) for t in y], [row[i] for row in proba]) for i, c in enumerate(ABC)]
    aps = [ref_ap([int(t == c) for t in y], [row[i] for row in proba]) for i, c in enumerate(ABC)]
    assert out["roc_auc_ovr_macro"] == pytest.approx(sum(aucs) / 3)
    assert out["average_precision_macro"] == pytest.approx(sum(aps) / 3)


def test_argmax_takes_the_first_class_on_an_exact_tie():
    # p = (.4, .4, .2) twice, for a true a and a true b: argmax is a both times
    y = ["a", "b", "a", "b", "c"]
    proba = [[0.4, 0.4, 0.2], [0.4, 0.4, 0.2], [0.1, 0.1, 0.8], [0.1, 0.8, 0.1], [0.1, 0.1, 0.8]]
    # predictions a, a, c, b, c: recall a = 1/2, b = 1/2, c = 1
    out = classification_metrics(y, proba, ABC)
    assert values(out)["balanced_accuracy"] == pytest.approx((0.5 + 0.5 + 1.0) / 3)


def test_a_rare_class_with_one_row_is_scored_not_dropped():
    # c has a single row, predicted right; a and b are half right
    y = ["a", "a", "b", "b", "c"]
    proba = [
        [0.8, 0.1, 0.1],
        [0.1, 0.8, 0.1],
        [0.1, 0.8, 0.1],
        [0.8, 0.1, 0.1],
        [0.1, 0.1, 0.8],
    ]
    out = classification_metrics(y, proba, ABC)
    assert values(out)["balanced_accuracy"] == pytest.approx((0.5 + 0.5 + 1.0) / 3)
    assert values(out)["roc_auc_ovr_macro"] is not None


def test_a_class_with_no_rows_makes_the_macro_values_and_balanced_accuracy_null_and_names_it():
    y = ["a", "a", "b", "b"]  # c is in the classes but absent from y_true
    proba = [[0.7, 0.2, 0.1], [0.5, 0.3, 0.2], [0.1, 0.7, 0.2], [0.2, 0.6, 0.2]]
    out = classification_metrics(y, proba, ABC)
    assert values(out)["roc_auc_ovr_macro"] is None
    assert values(out)["average_precision_macro"] is None
    assert values(out)["balanced_accuracy"] is None
    assert out["undefined"]["roc_auc_ovr_macro"] == "class c has no positive rows in y_true"
    assert out["undefined"]["average_precision_macro"] == "class c has no positive rows in y_true"
    assert out["undefined"]["balanced_accuracy"] == "class c has no rows in y_true"
    # log loss does not need every class present
    assert values(out)["log_loss"] is not None


def test_a_class_with_no_negatives_leaves_macro_roc_auc_null_but_not_average_precision():
    # every row is class a: a has no negatives (AUC undefined), b and c have no positives
    y = ["a", "a"]
    proba = [[0.6, 0.2, 0.2], [0.5, 0.3, 0.2]]
    out = classification_metrics(y, proba, ABC)
    assert out["undefined"]["roc_auc_ovr_macro"] == "class a has no negative rows in y_true"
    assert out["undefined"]["average_precision_macro"] == "class b has no positive rows in y_true"


def test_multiclass_rows_that_do_not_sum_to_one_are_refused():
    with pytest.raises(BadInput):
        classification_metrics(["a", "b", "c"], [[0.5, 0.5, 0.5]] * 3, ABC)


def test_float32_softmax_rows_are_accepted():
    import numpy as np

    proba = np.array([[0.2, 0.3, 0.5], [0.1, 0.1, 0.8]], dtype=np.float32)
    out = classification_metrics(["c", "c"], proba, ABC)
    assert values(out)["log_loss"] == pytest.approx(-(math.log(0.5) + math.log(0.8)) / 2, abs=1e-6)


def test_multiclass_cross_check_against_loop_references():
    rng = random.Random(23)
    for _ in range(40):
        k = rng.choice([3, 4])
        n = rng.randint(2 * k, 30)
        y = [rng.randrange(k) for _ in range(n)]
        if any(y.count(c) in (0, n) for c in range(k)):
            continue
        proba = []
        for _ in range(n):
            raw = [rng.randint(1, 4) for _ in range(k)]  # coarse: ties between rows are common
            total = sum(raw)
            proba.append([r / total for r in raw])
        out = values(classification_metrics(y, proba, list(range(k))))
        cols = [[row[i] for row in proba] for i in range(k)]
        assert out["roc_auc_ovr_macro"] == pytest.approx(
            sum(ref_auc([int(t == c) for t in y], cols[c]) for c in range(k)) / k, abs=1e-12
        )
        assert out["average_precision_macro"] == pytest.approx(
            sum(ref_ap([int(t == c) for t in y], cols[c]) for c in range(k)) / k, abs=1e-12
        )
        predicted = [max(range(k), key=lambda c: (row[c], -c)) for row in proba]
        assert out["balanced_accuracy"] == pytest.approx(ref_balanced(y, predicted, k), abs=1e-12)
        loss = -sum(math.log(min(max(row[t], 1e-15), 1 - 1e-15)) for row, t in zip(proba, y)) / n
        assert out["log_loss"] == pytest.approx(loss, abs=1e-12)


# -- aggregate ------------------------------------------------------------------------------------


def test_aggregate_mean_sample_sd_and_counts():
    # 1, 2, 3, 4: mean 2.5, sum of squares 5, sd = sqrt(5 / 3)
    out = aggregate([1.0, 2.0, 3.0, 4.0])
    assert out == {"mean": 2.5, "sd": pytest.approx(math.sqrt(5 / 3)), "n_defined": 4, "n_total": 4}


def test_aggregate_skips_undefined_values_and_counts_both():
    out = aggregate([1.0, None, 3.0, None, None])
    assert out["mean"] == 2.0 and out["n_defined"] == 2 and out["n_total"] == 5
    assert out["sd"] == pytest.approx(math.sqrt(2))  # (1 + 1) / (2 - 1)


def test_aggregate_with_one_defined_value_has_a_mean_and_no_sd():
    assert aggregate([None, 0.4]) == {"mean": 0.4, "sd": None, "n_defined": 1, "n_total": 2}


def test_aggregate_with_nothing_defined_has_neither():
    assert aggregate([None, None]) == {"mean": None, "sd": None, "n_defined": 0, "n_total": 2}
    assert aggregate([]) == {"mean": None, "sd": None, "n_defined": 0, "n_total": 0}


def test_aggregate_refuses_a_nan():
    with pytest.raises(BadInput):
        aggregate([1.0, float("nan")])


# -- output hygiene -------------------------------------------------------------------------------


def test_every_output_is_plain_json_with_no_nan_or_numpy_types():
    import numpy as np

    cases = [
        regression_metrics(np.array([1.0, 2.0, 3.0]), np.array([1.1, 1.9, 3.5])),
        regression_metrics([1, 1], [1, 2]),
        regression_metrics([], []),
        binary(["neg", "pos", "pos"], np.array([0.1, 0.8, 0.6], dtype=np.float32)),
        binary(["neg", "neg"], [0.1, 0.2]),
        classification_metrics(["a", "b", "c"], np.eye(3, dtype=np.float32), ABC),
        classification_metrics(["a", "a"], [[1, 0, 0]] * 2, ABC),
        {"aggregate": aggregate([1.0, None, 2.0])},
    ]
    for case in cases:
        text = json.dumps(case, allow_nan=False)  # raises on nan/inf and on numpy scalars
        assert "NaN" not in text and "Infinity" not in text


def test_every_value_is_a_python_float_or_none():
    import numpy as np

    out = classification_metrics(["a", "b", "c"], np.eye(3, dtype=np.float32), ABC)
    for value in values(out).values():
        assert value is None or type(value) is float
    out = regression_metrics(np.array([1.0, 2.0]), np.array([1.0, 3.0]))
    for value in values(out).values():
        assert type(value) is float
    agg = aggregate([np.float64(1.0), np.float32(2.0)])
    assert type(agg["mean"]) is float and type(agg["n_defined"]) is int


def test_metric_name_lists_match_what_is_returned():
    assert list(values(regression_metrics([1, 2], [1, 2]))) == list(M.REGRESSION_METRICS)
    assert list(values(binary(["neg", "pos"], [0.1, 0.9]))) == list(M.BINARY_METRICS)


# -- the docs ---------------------------------------------------------------------


def test_the_conventions_are_documented():
    text = DOCS.read_text()
    assert "metrics.py" in text
    for phrase in ("average_precision", "roc_auc_ovr_macro", "1e-15", "average ranks", "ddof"):
        assert phrase in text, phrase
