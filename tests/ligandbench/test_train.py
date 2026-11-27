"""The tuning, evaluation and refit engine: what it computes, and that no test row or
test score can choose anything.

Everything runs the tiny profile except the one test marked slow. The leakage tests do
not trust the engine's word: they replace the pieces that see test rows and scores, and
recompute the choices from the validation trace.
"""

import inspect
import json
import os

import numpy as np
import pytest

pytest.importorskip("rdkit")
pytest.importorskip("xgboost")

from test_train_data import config, csv_of, dataset, mol  # noqa: E402

from ligandbench import train as T  # noqa: E402
from ligandbench.errors import BadInput  # noqa: E402
from ligandbench.features import FeatureResult, Featuriser  # noqa: E402
from ligandbench.metrics import (  # noqa: E402
    BINARY_METRICS,
    MULTICLASS_METRICS,
    REGRESSION_METRICS,
)
from ligandbench.resolve import resolve  # noqa: E402
from ligandbench.split import REPEATS  # noqa: E402

KINDS = ("regression", "binary", "multiclass")


def run_of(kind="regression", seed=3, production="scaffold", **kw):
    meta, rows = dataset(kind)
    return (
        meta,
        rows,
        T.train(meta, rows, seed=seed, production_strategy=production, profile="tiny", **kw),
    )


def canon(value):
    return json.dumps(value, sort_keys=True, allow_nan=False)


# -- the search --------------------------------------------------------------------------------


def test_the_full_grid_is_the_twelve_candidates_the_issue_names():
    grid = T.grid_for("full")
    assert len(grid) == 12
    assert {(c["max_depth"], c["learning_rate"], c["n_estimators"]) for c in grid} == {
        (d, r, n) for d in (3, 6) for r in (0.05, 0.1) for n in (100, 300, 600)
    }
    assert all(set(c) == {"max_depth", "learning_rate", "n_estimators"} for c in grid)
    assert T.FIXED_PARAMS["subsample"] == 1.0 and T.FIXED_PARAMS["colsample_bytree"] == 1.0
    assert T.FIXED_PARAMS["nthread"] == 1 and T.FIXED_PARAMS["tree_method"] == "hist"


def test_the_tiny_profile_has_two_candidates_that_differ_only_in_trees():
    tiny = T.grid_for("tiny")
    assert len(tiny) == 2
    assert len({(c["max_depth"], c["learning_rate"]) for c in tiny}) == 1
    assert tiny[0]["n_estimators"] != tiny[1]["n_estimators"]
    with pytest.raises(BadInput, match="unknown profile"):
        T.grid_for("huge")


def test_a_grid_is_a_copy_the_caller_cannot_use_to_change_the_search():
    T.grid_for("full")[0]["max_depth"] = 99
    assert T.grid_for("full")[0]["max_depth"] == 3


def test_candidate_seeds_come_from_sha256_of_the_whole_address():
    a = T.candidate_seed(1, "random", 0, 0)
    assert a == T.candidate_seed(1, "random", 0, 0)
    assert 0 <= a < 2**31
    others = {
        T.candidate_seed(2, "random", 0, 0),
        T.candidate_seed(1, "scaffold", 0, 0),
        T.candidate_seed(1, "random", 1, 0),
        T.candidate_seed(1, "random", 0, 1),
        T.candidate_seed(1, "random", "production", 0),
    }
    assert a not in others and len(others) == 5
    # A fixed value: a change here changes every model, so it is a deliberate act.
    assert T.candidate_seed(0, "random", 0, 0) == 910667720


def test_ties_go_to_fewer_trees_then_shallower_then_a_lower_learning_rate():
    def cfg(d, r, n):
        return {"max_depth": d, "learning_rate": r, "n_estimators": n}

    grid = [cfg(6, 0.1, 100), cfg(3, 0.1, 300), cfg(3, 0.05, 100), cfg(3, 0.1, 100)]
    assert T.select([1.0, 1.0, 1.0, 1.0], grid) == 2  # 100 trees: depth 3: lr 0.05 beats 0.1
    assert T.select([1.0, 1.0, 0.5, 1.0], grid) == 2  # a lower loss beats every tie-break
    assert T.select([0.4, 1.0, 1.0, 1.0], grid) == 0
    assert T.select([1.0, 1.0, 1.0, 1.0][:2], grid[:2]) == 0  # fewer trees (100 < 300)
    same = [cfg(3, 0.1, 100), cfg(3, 0.1, 100)]
    assert T.select([2.0, 2.0], same) == 0  # nothing left to break: the first


# -- all three tasks, end to end ---------------------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_each_task_trains_evaluates_and_predicts(kind):
    meta, rows, run = run_of(kind)
    assert run.unavailable is None and run.booster is not None
    names = {"regression": REGRESSION_METRICS, "binary": BINARY_METRICS}.get(
        kind, MULTICLASS_METRICS
    )
    for strategy in ("random", "scaffold"):
        s = run.strategies[strategy]
        assert s["status"] == "ok" and len(s["repeats"]) == REPEATS
        assert set(s["aggregate"]) == set(names)
        for repeat in s["repeats"]:
            assert set(repeat["test"]) == set(names)
            assert set(repeat["undefined"]) <= set(names)
            assert {k for k, v in repeat["test"].items() if v is None} == set(repeat["undefined"])
            assert len(repeat["validation"]) == 2 and repeat["config"] in T.grid_for("tiny")
        for metric, agg in s["aggregate"].items():
            values = [r["test"][metric] for r in s["repeats"]]
            assert agg["n_total"] == REPEATS
            assert agg["n_defined"] == sum(v is not None for v in values)
    # prediction on the training rows, through the production booster
    X = Featuriser().featurise([(r["row_id"], r["smiles"]) for r in rows]).matrix
    out = T.predict(T.make_target(meta), run.booster, X)
    assert out.shape == ((len(rows), 3) if kind == "multiclass" else (len(rows),))
    if kind != "regression":
        assert out.min() >= 0 and out.max() <= 1
    if kind == "multiclass":
        assert np.allclose(out.sum(axis=1), 1.0, atol=1e-4)
    json.dumps(run.report, allow_nan=False)  # canonical-JSON clean: no NaN anywhere


def test_a_regression_model_learns_the_synthetic_signal():
    meta, rows, run = run_of("regression")
    rmse = run.strategies["random"]["aggregate"]["rmse"]["mean"]
    spread = float(np.std([r["target"] for r in rows]))
    assert rmse < spread


def test_class_order_comes_from_the_dataset_and_a_binary_positive_class_is_kept():
    meta, rows, run = run_of("binary")
    target = run.features["target"]
    assert target["classes"] == meta["classes"] and target["positive_class"] == "1"
    assert target["objective"] == "binary:logistic" and target["num_class"] is None
    meta, rows, run = run_of("multiclass")
    target = run.features["target"]
    assert target["classes"] == meta["classes"] and len(target["classes"]) == 3
    assert target["objective"] == "multi:softprob" and target["num_class"] == 3
    assert target["positive_class"] is None


def test_progress_runs_through_the_stages_and_counts_every_fit():
    seen = []
    meta, rows, run = run_of("regression", progress=lambda *a: seen.append(a))
    states = [s[0] for s in seen]
    assert states[0] == "splitting" and states[-1] == "tuning"  # the refit's own fit is a tick
    assert "refitting" in states
    assert states.index("featurizing") < states.index("tuning") < states.index("refitting")
    total = 2 * REPEATS * 2 + 1  # candidates x repeats x strategies, plus the refit
    assert {s[3] for s in seen if s[0] != "splitting"} == {total}
    assert max(s[2] for s in seen) == total
    done = [s[2] for s in seen if s[0] == "tuning"]
    assert done == sorted(done) and done[0] == 1


# -- strategies that are unavailable -----------------------------------------------------------


def acyclic_dataset():
    """Chains only: every molecule shares the empty scaffold, so scaffold cannot split
    (one group holds everything) and random can.
    """
    pairs = [("C" * k + "O", 0.5 * k) for k in range(2, 62)]
    return resolve(csv_of(pairs), config("regression"))


def test_an_unavailable_production_strategy_stops_before_anything_is_trained(monkeypatch):
    meta, rows = acyclic_dataset()
    called = []
    monkeypatch.setattr(T, "fit", lambda *a, **k: called.append(a))
    monkeypatch.setattr(Featuriser, "featurise", lambda *a, **k: called.append("features"))
    run = T.train(meta, rows, seed=0, production_strategy="scaffold", profile="tiny")
    assert called == [] and run.booster is None and run.production is None
    assert run.unavailable["failed_constraints"] and "scaffold" in run.unavailable["message"]
    assert run.splits["scaffold"]["status"] == "split_unavailable"
    assert run.splits["random"]["status"] == "ok"


def test_the_other_strategy_being_unavailable_is_reported_and_never_replaced():
    meta, rows = acyclic_dataset()
    run = T.train(meta, rows, seed=0, production_strategy="random", profile="tiny")
    assert run.unavailable is None and run.booster is not None
    scaffold = run.strategies["scaffold"]
    assert scaffold["status"] == "split_unavailable" and scaffold["repeats"] == []
    assert scaffold["unavailable"]["failed_constraints"] and scaffold["aggregate"] == {}
    assert run.strategies["random"]["status"] == "ok"
    assert run.validation_trace["strategies"]["scaffold"] == {
        "status": "split_unavailable",
        "repeats": [],
    }
    assert run.production["strategy"] == "random"


def test_a_target_free_dataset_and_a_bad_choice_are_refused():
    meta, rows = resolve(csv_of([("CCO", 1)]), {"structure_column": "smiles", "target": None})
    with pytest.raises(BadInput, match="no target"):
        T.train(meta, rows, seed=0, production_strategy="scaffold", profile="tiny")
    meta, rows = dataset()
    with pytest.raises(BadInput, match="unknown production strategy"):
        T.train(meta, rows, seed=0, production_strategy="cluster", profile="tiny")
    with pytest.raises(BadInput, match="unknown profile"):
        T.train(meta, rows, seed=0, production_strategy="random", profile="enormous")
    with pytest.raises(BadInput, match="seed"):
        T.train(meta, rows, seed=-1, production_strategy="random", profile="tiny")


def test_a_structure_that_does_not_featurise_stops_the_run_naming_its_rows(monkeypatch):
    meta, rows = dataset()

    class Broken(Featuriser):
        def featurise(self, pairs):
            good = super().featurise([p for p in pairs if p[0] not in (4, 9)])
            return FeatureResult(
                good.matrix,
                good.row_ids,
                [{"row_id": 4, "reason": "descriptor tpsa is not finite"}]
                + [{"row_id": 9, "reason": "descriptor tpsa is not finite"}],
            )

    fits = []
    monkeypatch.setattr(T, "fit", lambda *a, **k: fits.append(a))
    with pytest.raises(BadInput, match=r"row ids: 4, 9"):
        T.train(
            meta, rows, seed=0, production_strategy="random", profile="tiny", featuriser=Broken()
        )
    assert fits == []


# -- no leakage, provably ----------------------------------------------------------------------


def test_tuning_has_no_parameter_a_test_row_could_arrive_through():
    names = list(inspect.signature(T.tune).parameters)
    assert names == ["target", "grid", "seeds", "X_train", "y_train", "X_val", "y_val", "tick"]
    assert list(inspect.signature(T.select).parameters) == ["losses", "grid"]


def spy_on_tune(monkeypatch):
    """Records what ``tune`` is given, and what ``evaluate_test`` is given, per call."""
    calls = {"tune": [], "test": []}
    real_tune, real_test = T.tune, T.evaluate_test

    def tune(target, grid, seeds, X_tr, y_tr, X_va, y_va, tick=lambda: None):
        calls["tune"].append((X_tr.copy(), list(y_tr), X_va.copy(), list(y_va)))
        return real_tune(target, grid, seeds, X_tr, y_tr, X_va, y_va, tick)

    def test(target, booster, X_te, y_te):
        calls["test"].append((X_te.copy(), list(y_te)))
        return real_test(target, booster, X_te, y_te)

    monkeypatch.setattr(T, "tune", tune)
    monkeypatch.setattr(T, "evaluate_test", test)
    return calls


@pytest.mark.parametrize("kind", ("regression", "binary"))
def test_selection_is_given_exactly_the_train_and_validation_rows_of_each_repeat(monkeypatch, kind):
    meta, rows = dataset(kind)
    calls = spy_on_tune(monkeypatch)
    run = T.train(meta, rows, seed=5, production_strategy="scaffold", profile="tiny")
    feats = Featuriser().featurise([(r["row_id"], r["smiles"]) for r in rows])
    where = {rid: i for i, rid in enumerate(feats.row_ids)}
    by_id = {r["row_id"]: r for r in rows}

    def labels(ids):
        return [
            float(by_id[i]["target"]) if kind == "regression" else by_id[i]["target"] for i in ids
        ]

    expected = []
    for name in ("random", "scaffold"):
        for repeat in run.splits[name]["repeats"]:
            expected.append((name, repeat))
    assert len(calls["tune"]) == len(calls["test"]) == len(expected) == 2 * REPEATS
    for (_name, repeat), tuned, tested in zip(expected, calls["tune"], calls["test"]):
        X_tr, y_tr, X_va, y_va = tuned
        assert np.array_equal(X_tr, feats.matrix[[where[i] for i in repeat["train"]]])
        assert np.array_equal(X_va, feats.matrix[[where[i] for i in repeat["validation"]]])
        assert y_tr == labels(repeat["train"]) and y_va == labels(repeat["validation"])
        assert np.array_equal(tested[0], feats.matrix[[where[i] for i in repeat["test"]]])
        # the subsets are disjoint, so a test row is in neither argument of tune
        sets = [set(repeat[k]) for k in ("train", "validation", "test")]
        assert not (sets[0] & sets[2]) and not (sets[1] & sets[2]) and not (sets[0] & sets[1])
        # and no tuning row is a test row, by identity of its feature vector too
        test_rows = {tuple(v) for v in tested[0]}
        assert not test_rows & {tuple(v) for v in X_tr} and not test_rows & {tuple(v) for v in X_va}


def adversarial(booster_scores):
    """An ``evaluate_test`` that returns scores made to point at the wrong candidate."""

    def evaluate(target, booster, X_te, y_te):
        trees = booster.num_boosted_rounds()
        value = booster_scores[trees]
        names = target.metrics
        return {"values": {n: value for n in names}, "undefined": {}}

    return evaluate


@pytest.mark.parametrize("kind", KINDS)
def test_replacing_every_test_score_changes_no_choice_and_no_validation_loss(monkeypatch, kind):
    meta, rows = dataset(kind)
    honest = T.train(meta, rows, seed=2, production_strategy="scaffold", profile="tiny")
    # 20-tree candidate looks terrible on test, the 10-tree one perfect, and the reverse
    for scores in ({10: 0.0, 20: 99.0}, {10: 99.0, 20: 0.0}):
        monkeypatch.setattr(T, "evaluate_test", adversarial(scores))
        lured = T.train(meta, rows, seed=2, production_strategy="scaffold", profile="tiny")
        assert canon(lured.validation_trace) == canon(honest.validation_trace)
        assert lured.production == honest.production
        for name in ("random", "scaffold"):
            for a, b in zip(honest.strategies[name]["repeats"], lured.strategies[name]["repeats"]):
                assert (a["config"], a["candidate"], a["validation"]) == (
                    b["config"],
                    b["candidate"],
                    b["validation"],
                )
        # only the test scores moved
        assert (
            lured.strategies["random"]["repeats"][0]["test"]
            != (honest.strategies["random"]["repeats"][0]["test"])
        )
        assert lured.booster.save_raw() == honest.booster.save_raw()


@pytest.mark.parametrize("kind", ("regression", "binary"))
def test_the_production_config_is_the_lowest_mean_validation_loss_of_the_preselected_strategy(
    monkeypatch, kind
):
    meta, rows = dataset(kind)
    grid = T.grid_for("tiny")
    # Scores that would make the OTHER candidate the test winner, for every repeat.
    monkeypatch.setattr(T, "evaluate_test", adversarial({10: 0.0, 20: 1.0}))
    for strategy in ("random", "scaffold"):
        run = T.train(meta, rows, seed=4, production_strategy=strategy, profile="tiny")
        trace = run.validation_trace["strategies"][strategy]["repeats"]
        mean = [
            sum(rep["candidates"][i]["loss"] for rep in trace) / REPEATS for i in range(len(grid))
        ]
        best = min(
            range(len(grid)), key=lambda i: (mean[i], grid[i]["n_estimators"], grid[i]["max_depth"])
        )
        assert run.production["config"] == grid[best]
        assert run.production["strategy"] == strategy
        assert run.production["mean_validation_loss"] == pytest.approx(mean[best])
        assert run.validation_trace["production"]["selected"] == best
        assert [
            c["mean_validation_loss"] for c in run.validation_trace["production"]["candidates"]
        ] == pytest.approx(mean)


def test_the_choice_is_by_the_preselected_strategy_not_the_better_looking_one(monkeypatch):
    """Both strategies are evaluated; the production one is whichever was named, whatever
    each looks like on test.
    """
    meta, rows = dataset("regression")
    a = T.train(meta, rows, seed=1, production_strategy="random", profile="tiny")
    b = T.train(meta, rows, seed=1, production_strategy="scaffold", profile="tiny")
    assert a.production["strategy"] == "random" and b.production["strategy"] == "scaffold"
    for name in ("random", "scaffold"):
        assert canon(a.strategies[name]) == canon(b.strategies[name])  # evaluation is shared


def test_the_refit_uses_exactly_all_eligible_rows_and_the_production_config(monkeypatch):
    meta, rows = dataset("regression", extra=[("not-smiles", 1.0), ("CCCC", ""), (mol(0, 9), "")])
    fits = []
    real = T.fit

    def spy(target, config, seed, X, y):
        fits.append((config, seed, X.copy(), list(y)))
        return real(target, config, seed, X, y)

    monkeypatch.setattr(T, "fit", spy)
    run = T.train(meta, rows, seed=6, production_strategy="scaffold", profile="tiny")
    eligible = [r["row_id"] for r in rows if r["training_eligible"]]
    assert len(eligible) == 100 and len(rows) == 103
    assert run.refit_row_ids == eligible == run.splits["scaffold"]["row_ids"]
    config, seed, X, y = fits[-1]
    assert config == run.production["config"] and len(fits) == 2 * REPEATS * 2 + 1
    assert seed == T.candidate_seed(
        6, "scaffold", "production", run.validation_trace["production"]["selected"]
    )
    feats = Featuriser().featurise(
        [(r["row_id"], r["smiles"]) for r in rows if r["training_eligible"]]
    )
    assert np.array_equal(X, feats.matrix)
    assert y == [float(r["target"]) for r in rows if r["training_eligible"]]
    assert run.production["n_refit_rows"] == 100
    assert run.report["dataset"]["n_eligible"] == 100 and run.report["dataset"]["n_rows"] == 103
    # the refit saw every row of every subset: it is not a train-only model
    all_test = {i for rep in run.splits["scaffold"]["repeats"] for i in rep["test"]}
    assert all_test <= set(run.refit_row_ids)


def test_the_scaffold_set_is_the_refit_rows_scaffolds_sorted_and_unique():
    from ligandbench.scaffold import murcko_scaffold

    meta, rows, run = run_of("regression")
    expected = sorted({murcko_scaffold(r["smiles"]) for r in rows if r["training_eligible"]})
    assert run.scaffolds == {"scaffold_policy": "murcko-v1", "scaffolds": expected}
    assert len(expected) == 20


# -- determinism -------------------------------------------------------------------------------


def test_the_same_dataset_seed_and_profile_give_the_same_run_and_model():
    meta, rows = dataset("binary")
    a = T.train(meta, rows, seed=9, production_strategy="scaffold", profile="tiny")
    b = T.train(meta, rows, seed=9, production_strategy="scaffold", profile="tiny")
    assert canon(a.report) == canon(b.report)
    assert canon(a.validation_trace) == canon(b.validation_trace)
    assert a.booster.save_raw() == b.booster.save_raw()
    c = T.train(meta, rows, seed=10, production_strategy="scaffold", profile="tiny")
    assert canon(c.splits) != canon(a.splits)


def test_the_report_names_what_it_ran_on():
    meta, rows, run = run_of("regression", dataset_id="ds-abc")
    r = run.report
    assert r["dataset"] == {
        "id": "ds-abc",
        "content_hash": meta["content_hash"],
        "n_rows": 100,
        "n_eligible": 100,
    }
    assert r["seed"] == 3 and r["profile"] == "tiny" and r["grid"] == T.grid_for("tiny")
    assert r["target"]["task"] == "regression" and r["target"]["column"] == "y"
    assert r["target"]["units"] == "pKi" or r["target"]["units"] == ""
    assert set(r["versions"]) == {
        "ligandbench",
        "rdkit",
        "xgboost",
        "numpy",
        "python",
        "scaffold_policy",
        "split_algorithm",
        "featuriser",
    }
    assert r["feature_schema"]["version"] == r["versions"]["featuriser"]
    assert r["production"]["tie_break"] == ["n_estimators", "max_depth", "learning_rate"]


@pytest.mark.skipif(
    os.environ.get("LIGANDBENCH_SLOW") != "1",
    reason="the full 12-candidate search; LIGANDBENCH_SLOW=1",
)
def test_the_full_twelve_candidate_search_on_a_small_regression_set():
    meta, rows = dataset("regression", groups=12, per=5)
    run = T.train(meta, rows, seed=0, production_strategy="scaffold", profile="full")
    assert run.report["profile"] == "full" and len(run.report["grid"]) == 12
    rep = run.strategies["scaffold"]["repeats"][0]
    assert len(rep["validation"]) == 12 and rep["config"] in T.grid_for("full")
    assert run.production["config"] in T.grid_for("full")
    assert len(run.validation_trace["production"]["candidates"]) == 12
