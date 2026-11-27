# ligandbench

Reads a ligand dataset: a CSV of molecules, perhaps with a measured target.
It turns the file a chemist exports (a CDD vault, a spreadsheet) into rows with
a stable identity -- every source cell kept, every structure parsed and
canonicalised by RDKit, each row marked as fit or unfit to train on and why.

ProteinViewer's server runs it as a command and never imports it
(`python -m ligandbench`, in the server's own interpreter unless
`LIGANDBENCH_PYTHON` names another), so RDKit stays out of the server. It does
not need the viewer either; it is a command-line tool and a Python library.

```sh
ligandbench info                                    # RDKit, depiction and summary versions
ligandbench inspect export.csv                      # columns, types, structure candidates
ligandbench resolve export.csv --config c.json --out DIR   # rows.json + dataset.json
ligandbench split DIR --strategy scaffold --seed 7  # five repeated holdouts of a resolved dataset
ligandbench depict request.json                     # {"smiles": [...]} -> SVGs
ligandbench summarize DIR                           # the dashboard's numbers for the dataset in DIR
```

Every command prints one JSON object. The exit code says whose problem a
failure is -- 2 a file or choice that cannot be used, 3 RDKit missing -- and
the error is `{"error", "kind"}`.

## What it decides, and what it asks

Nothing about a column is guessed. A column's numbers do not say which task is
meant (0 and 1 may be classes), and its name does not say what its units are.
`resolve` takes these choices:

```json
{"structure_column": "SMILES",
 "target": {"column": "HIV-1 PR FRET (Q7K): Ki (nM)", "kind": "regression",
            "qualifier_column": "HIV-1 PR FRET (Q7K): Ki Qualifier",
            "transform": "pki", "source_units": "nM"}}
```

- `target` may be `null`: the dataset is for prediction only and cannot train.
- `kind` is `regression` or `classification`. For two classes, `positive_class`
  says which is positive. Labels are kept as strings.
- `qualifier_column` must be present, `null` meaning none. Blank or `=` is exact;
  `<`, `>`, `<=`, `>=` make the value a bound. A bound written in the value
  (`>10000`) counts the same. A bound is kept and shown, never turned into an
  exact value.
- `source_units` must be stated for a regression target. `transform` is
  `identity` or `pki` (`pKi = 9 - log10(Ki)`), and `pki` is refused unless the
  units are `nM`.

## Rows

Every source row is kept, in source order, with an immutable zero-based
`row_id`. Messages count data records from one, after the header, which is what
a person counting rows would say. Each row carries its source `cells`, its
canonical `smiles`, the `target` the model would see, the `target_source` text
it came from, and:

- `prediction_eligible` -- a structure parsed.
- `training_eligible` -- a structure parsed *and* the target is present, valid
  and exact.
- `exclusions` -- every reason a row cannot train (they overlap): `no_target`,
  `invalid_structure`, `missing_target`, `invalid_target`, `censored`,
  `unknown_qualifier`, `conflicting_qualifiers`, `nonpositive_for_pki`.

Duplicate molecules stay separate observations but share a structure identity
(`smiles`), so a split can keep them together; conflicting class labels among
them are reported.

## Structure identity

RDKit's canonical isomeric SMILES, with stereochemistry, charges and
disconnected components kept. There is no automatic salt stripping, tautomer
normalisation or neutralisation. The policy and the RDKit version are recorded
in `dataset.json` (`identity`), so everything downstream uses the same one.
Structure columns are those whose header is, case-insensitively with spaces as
underscores, `smiles`, `canonical_smiles`, `isomeric_smiles`, `mol`, `molecule`,
`romol`, or ends in `_smiles`; `Molecule Name` is not one. Whatever column is
chosen, its cells are parsed as SMILES.

`dataset.json` also holds the counts the page shows -- rows, parse failures
with example record numbers, training- and prediction-eligible rows, exclusion
counts, duplicates -- and a `content_hash`: SHA-256 of the source bytes and the
resolved choices.

## Scaffolds

`ligandbench.scaffold.murcko_scaffold(smiles)` is the one definition of a
scaffold, shared by the dashboard's counts and the scaffold split. It is the
Bemis-Murcko scaffold of the whole molecule as given -- ring systems and the
linkers between them, side chains removed -- as RDKit canonical SMILES.
Stereochemistry is not part of it (`includeChirality=False`), so stereoisomers
share a scaffold and a split cannot separate them. A molecule with no ring has
the scaffold `""`, and every empty scaffold is one group. The input is not
normalised (no salt stripping, largest fragment or neutralisation): fragments
with no ring add nothing, and if none has a ring the scaffold is `""`. A string
that is not a structure raises `BadInput`. `SCAFFOLD_POLICY` (`murcko-v1`) names
this definition and is recorded beside anything derived from a scaffold; it
changes whenever the answer for a molecule would.

## Splits

`ligandbench split DIR --strategy random|scaffold --seed N` makes five repeated
holdouts of the dataset `resolve` wrote into `DIR`, each aimed at 80% train, 10%
validation and 10% test. They are not five-fold cross-validation: the test sets
of different repeats may overlap and a row need not be tested at all. Within one
repeat every eligible row is in exactly one subset. The function behind it,
`ligandbench.split.split(meta, rows, strategy=, seed=)`, takes the resolved
metadata and rows, not a dataset id, and writes nothing; whoever calls it keeps
the result.

**Rows.** Only `training_eligible` rows are split. Every other row is listed in
`excluded` with the `exclusions` it carried and never appears in a subset. A
dataset without a target (`trainable` false) is refused (exit 2).

**Groups.** A split moves whole groups and never takes a row out of one.
Rows with the same structure identity (`smiles`) are always one group, so a
duplicate never reaches the other side of its twin, and conflicting labels
among duplicates do not separate them. The `random` strategy moves structure
groups. The `scaffold` strategy moves groups of structure groups that share a
`murcko_scaffold`, so a scaffold is on one side only; every acyclic molecule
(scaffold `""`) is one group. A group is never broken to improve a proportion.

**Valid.** A repeat is valid when every subset has at least two rows, each
subset is within five percentage points of its target share (the ends included:
for 100 rows train holds 75 to 85, validation and test 5 to 15), and, for
classification, every class is in every subset. Group integrity comes first: a
split that cannot meet these with whole groups is not made.

**Allocator** (`ALGORITHM_VERSION` `group-greedy-v1`). For each repeat it builds
up to 256 candidate allocations, larger groups first, and keeps the valid one
with the smallest objective: the sum of squared deviations of each subset's row
count from its target count, plus, for classification, the same for each class's
count in each subset. Ties go to the lowest candidate number. A candidate that
reaches the least objective any allocation could have ends the search at once,
which cannot change the answer. Candidate 0 puts each group where it adds least
to the objective (never past a subset's upper limit if another subset has room);
later candidates sometimes take the second or third choice, with a chance that
rises with the candidate number, and odd ones also prefer a subset still lacking
a class the group holds, so a rare class can reach every subset. The result
reports `candidates_tried` and the chosen `candidate`.

**When no candidate is valid** the answer for that strategy is
`status: "split_unavailable"` with no repeats and the reasons (below). It is an
answer, not an error: exit 0, HTTP 200. The allocator found no valid split; that
is not a proof that none exists. Constraints are never relaxed, and a random split
is never substituted for a scaffold split. Either all five repeats come back or
none do, and the first repeat that fails ends the search.

**What is kept about eligibility.** The record does not restate the rules in
words; it keeps what they were applied to. A row trains when its structure parses
and its target is present, valid and exact, so the result holds the structure
column and the target exactly as it was confirmed (`dataset.target`: column,
kind, qualifier column, units, transform, positive class), the dataset's layout
`format`, the structure identity policy and the `content_hash` of the source and
choices, with `row_ids` for the rows used and `excluded` for the rest with their
reasons. ligandbench's own package version is not recorded: `algorithm.version`
and `rdkit` are what an assignment depends on.

**Reproducibility.** Nothing depends on Python's `hash()`, an unseeded generator
or set order. `repeat_seed(seed, r)` is the first six bytes (big-endian) of
SHA-256 over the text `drughunter-split|<seed>|<r>`, so a repeat seed fits a JSON
number. In candidate `i` of a repeat, each group (named by its lowest row id `g`)
draws from SHA-256 over `<repeat seed>|<i>|<g>`: bytes 0-7 order groups of equal
size, byte 8 (modulo 100) below the candidate's noise percent (0 for candidate 0,
otherwise `5 + 45*i//255`) makes it take another choice and byte 9 picks which,
byte 16 breaks ties between subsets. The same dataset (`content_hash`), strategy,
seed, RDKit and `ALGORITHM_VERSION` give the same result in any process, and
the command prints it in canonical form: sorted keys, no spaces, ASCII only. Equal
results are equal bytes. A different seed may give different assignments; it is
not guaranteed to. The stored assignments, not a rerun, are the record of a split
across software upgrades.

### The result

One JSON object. Counts and seeds are whole numbers (there are no floats), so
re-serialising it with `json.dumps(result, sort_keys=True, separators=(",", ":"),
ensure_ascii=True, allow_nan=False)` gives the same bytes `ligandbench split` printed.

```text
format             1; bumped when this layout changes
status             "ok" | "split_unavailable"
strategy           "random" | "scaffold"
seed               the seed asked for (0 to 4294967295)
repeat_seeds       the five repeat seeds, in repeat order
algorithm          {version, candidates: 256, repeats: 5, target_percent: {train: 80,
                    validation: 10, test: 10}, tolerance_points: 5, min_rows: 2}
rdkit              RDKit's version
scaffold_policy    SCAFFOLD_POLICY ("murcko-v1"); only "scaffold" depends on it
dataset            what the rows came from and which rules decided eligibility:
                    {content_hash, identity (the structure identity policy), format (dataset.json's
                    layout), structure_column, target (as dataset.json confirmed it: column, kind,
                    positive_class, qualifier_column, transform, source_units, output_units),
                    task, classes, n_rows}; n_rows counts every source row, excluded ones
                    included (n_eligible below counts the rows that are split)
n_eligible         training-eligible rows, which are the ones split
n_groups           groups of the strategy among them
row_ids            the eligible row ids, ascending: exactly the rows in every repeat
excluded           [{row_id, reasons: [...]}], ascending: never in a subset
repeats            five repeats when status is "ok", otherwise []
unavailable        null when status is "ok", otherwise the object below
```

A repeat:

```text
repeat             0 to 4
seed               this repeat's seed (= repeat_seeds[repeat])
candidate          the candidate chosen; candidates_tried how many were built
objective          100 x the sum of squared deviations (a whole number; 0 is perfect)
sizes              {train, validation, test}: rows in each
class_counts       {subset: {class: rows}} for classification, {} for regression
train, validation, test    the row ids in each subset, ascending
```

`unavailable`, for the first repeat that failed:

```text
repeat             the repeat that failed
message            what the allocator found, in words
candidates_tried   candidates built for it
n_rows, n_groups   the eligible rows and the groups they form; n_rows is n_eligible, not
                    dataset.n_rows (which counts every source row)
group_size_counts  [[group size, how many groups], ...], largest size first
largest_groups     up to ten, largest first: {key, n_rows, n_structures, class_counts};
                    key is the structure identity or the scaffold ("" is no ring)
class_counts       {class: rows} for classification, {} for regression
size_windows       {subset: {min, max}}: the rows each subset may have (min is at least 2;
                    min above max means no whole number of rows qualifies)
failed_constraints the rules the nearest candidate broke, each {constraint, subset,
                    candidates_violating, detail, ...}; constraint is "min_size"
                    (adds size, the rows the subset had, and required, the 2 it needs),
                    "size_tolerance" (adds size and allowed [fewest, most]) or
                    "class_coverage" (adds class and class_total)
nearest_candidate  {candidate, objective, sizes}: the candidate that broke fewest rules
```

`candidates_violating` counts, among the candidates of that repeat, how many broke
that rule: a rule every candidate broke is the one to look at first (a group larger
than any subset may hold; a class with fewer rows than subsets).

### Through the server

`POST /api/dataset/splits` takes `{"dataset_id", "strategy", "seed"}` -- `strategy`
is `random` or `scaffold` and must be named; `seed` is a whole number from 0 and
defaults to 0 -- and answers with the result above plus `"dataset_id"`, which the
server adds (it is not in the command's output and not part of the canonical bytes).
One strategy per request; ask twice for both. The server stores nothing: the caller
keeps what it is given. A staged dataset (409), a dataset that is unknown (404) or
has no target (400), a bad strategy or seed (400) and a ligandbench that is missing
(503) or fails (502) are errors; `split_unavailable` is not.

## Dataset summary

`ligandbench summarize DIR` reads the `dataset.json` and `rows.json` that
`resolve` wrote into `DIR` and prints the dashboard's statistics as one JSON
object (`ligandbench.summary.summarize(meta, rows)` is the same thing as a
function). It holds no row: a histogram is at most 50 bins and a classification
one count per class, so the answer is a few kilobytes at 10 rows or 10,000.
`ligandbench info` reports its `summary` version, `SUMMARY_VERSION`, which
changes whenever the answer for the same dataset would; a stored summary is
keyed by it. The server keeps each one beside its dataset (see
[proteinviewer](proteinviewer.md)).

Every count is a count *of* a named population, so no percentage is of an
unnamed whole. `populations` names them, each with the population it is a share
of (`of`):

| population | rows |
| --- | --- |
| `source_rows` | every row of the file |
| `valid_structures` | rows whose structure parsed (01's `n_prediction_eligible`) |
| `training_eligible` | rows that parsed and have a valid, exact target (01's `n_training_eligible`) |

Where `dataset.json` holds the same number, the summary equals it and the tests
assert so: `n_rows`, `n_parse_failures`, `n_training_eligible`, `exclusions`,
`duplicates.n_structures` / `n_rows` / `n_conflicting`, and every column's
`n_filled`.

```json
{
  "version": "1",
  "identity": {"structure": "RDKit canonical isomeric SMILES", "standardization": "none", "rdkit": "..."},
  "populations": {"source_rows": {"label": "source rows", "n": 106, "of": null},
                  "valid_structures": {"label": "valid structures", "n": 106, "of": "source_rows"},
                  "training_eligible": {"label": "training-eligible rows", "n": 102, "of": "source_rows"}},
  "parse_failures": {"population": "source_rows", "n": 0},
  "exclusions": {"population": "source_rows", "overlap": true, "n_rows": 4,
                 "by_reason": [{"code": "censored", "label": "the target is a bound (<, >), not a measurement", "n": 4}]},
  "duplicates": {"population": "valid_structures", "n_distinct": 106, "n_duplicate_rows": 0,
                 "n_groups": 0, "n_rows_in_groups": 0,
                 "n_conflicting": null, "conflicting_reason": "not defined for regression"},
  "target": {"state": "regression", "...": "see below"},
  "scaffolds": {"population": "valid_structures", "policy": "murcko-v1", "n_structures": 106,
                "n_distinct": 31, "largest_group_size": 19,
                "top10_groups": 10, "top10_rows": 77, "top10_share": 0.726,
                "acyclic_rows": 0, "n_unscaffolded": 0},
  "missing": {"population": "source_rows",
              "columns": [{"index": 0, "name": "Molecule Name", "n_missing": 0}, "..."]}
}
```

- **Exclusions overlap.** A row can have several reasons (no structure *and* no
  label), so the counts in `by_reason` can add up to more than `n_rows`, the
  number of rows with at least one. `by_reason` is largest first.
- **Duplicates.** `n_duplicate_rows` is valid rows minus distinct structure
  identities (`n_distinct`): of three rows that are one molecule, two are
  duplicates. `n_groups` is the identities that occur more than once, in
  `n_rows_in_groups` rows. `n_conflicting` is `resolve`'s own count, for a
  classification target: groups whose rows do not all carry the same label,
  comparing only labels that are present. For regression, and for a dataset with
  no target, it is `null` and `conflicting_reason` says why: a measured number has
  no definition of "disagree" without inventing a tolerance, and none is invented.
- **Target** is over the training-eligible rows only, in the values the model
  sees, and has one of four shapes, chosen by `state`:
  - `regression`: `column`, `transform`, `source_units`, `units` (`pKi` for a
    pKi, else the stated units), `n`, `n_binned`, `n_not_binned` (a value that
    is not finite cannot occur after `resolve`, and is left out and counted if it
    does), `min`, `max`, `mean`, `median` and `bins`: `{rule, n_bins, edges,
    counts}`. The rule is Rice's, `ceil(2 * n^(1/3))` equal-width bins between
    the smallest and largest value, at most 50, so `edges` has one more entry than
    `counts`. A bin includes its lower edge and the last one its upper edge; a
    value is counted by the published edges, so a picture and its numbers cannot
    disagree about a value on an edge. One distinct value is a single bin whose
    two edges are that value.
  - `classification`: `classes` in the dataset's own class order, each `{label,
    count, positive}`, and `positive_class`.
  - `no_target`: the dataset is for prediction only; there is nothing to draw.
  - `empty`: a target was chosen and no row can train on it, so there is nothing
    to draw either; the exclusions say why. (`resolve` refuses such a dataset, so
    it is for a caller that builds the rows itself.)
- **Scaffolds** are over the valid structures, grouped by
  [`murcko_scaffold`](#scaffolds); `policy` is its `SCAFFOLD_POLICY`. The empty
  scaffold (a molecule with no ring) is one group like any other: it counts once
  in `n_distinct`, can be the largest group or one of the ten, and its rows are
  also given on their own as `acyclic_rows`. `top10_share` is the rows in the ten
  largest groups over `n_structures`, every valid structure. A structure whose
  scaffold RDKit cannot make is in no group and is counted in `n_unscaffolded`;
  the denominator stays every valid structure, so it cannot be hidden.
- **Missing** is per source column, in the file's order. A cell that is blank or
  only whitespace is missing -- exactly the cells `inspect` does not count as
  `n_filled`. A value that is there but wrong for its column (`abc` where a
  number was meant) is *not* missing: it is filled, and is judged where it
  matters, as an `invalid_target` exclusion if it is the target.

## Features

`ligandbench.features` turns a structure identity (a row's `smiles`) into the
vector the QSAR model sees. It is parsed exactly as `chem.py` and `scaffold.py`
parse it -- plain `Chem.MolFromSmiles`, no salt stripping, no neutralisation.
`FEATURISER_VERSION` (`morgan2048-desc7-v1`) names the definition and is bumped
whenever the same molecule would give a different vector.

A vector has 2055 float32 columns: 2048 binary Morgan bits (`morgan_0000` to
`morgan_2047`, exactly 0.0 or 1.0), then seven descriptors in this order:

| Column | RDKit function |
| --- | --- |
| `mol_wt` | `Descriptors.MolWt` |
| `log_p` | `Crippen.MolLogP` |
| `tpsa` | `rdMolDescriptors.CalcTPSA` |
| `hbd` | `rdMolDescriptors.CalcNumHBD` |
| `hba` | `rdMolDescriptors.CalcNumHBA` |
| `rotatable_bonds` | `rdMolDescriptors.CalcNumRotatableBonds` |
| `heavy_atoms` | `Mol.GetNumHeavyAtoms` |

The Morgan generator is radius 2, `fpSize` 2048, `includeChirality` true (so
enantiomers differ), `countSimulation` false, bit output, with RDKit's default
atom and bond invariants written out (`useBondTypes`, `includeRingMembership`,
`onlyNonzeroInvariants`, `numBitsPerFeature`) so a default that changes in a later
RDKit cannot change the vector unnoticed. Nothing is fitted: no scaling, no
imputation.

`feature_schema()` is a JSON object holding all of that -- generator options,
descriptor names and functions, dtype, the column names in order, the schema
version, and the RDKit and numpy versions -- and is what a training run stores
beside its model. `Featuriser.from_schema(schema)` rebuilds the featuriser and
raises `BadInput` when this build implements something else (another version, or
any parameter, descriptor, column or dtype differing). Dependency versions are
recorded but do not refuse a schema; `Featuriser.dependency_drift(schema)` says
which differ so a caller can warn.

`Featuriser().featurise([(row_id, smiles), ...])` returns a `FeatureResult`:
`matrix` (the rows that worked, in input order), `row_ids` (the same rows' ids)
and `failures` (`[{"row_id", "reason"}]`: a structure that does not parse, or a
descriptor that is not finite -- NaN, infinity, or too large for float32 -- named
in the reason). It never raises for a row and never reorders. `require_all(result)`
raises `BadInput` listing up to 20 failing row ids and "and N more"; training
uses it, so a nonfinite descriptor stops the run with row ids, and inference can
instead report failures per row.

## Metrics

`ligandbench.metrics` (numpy only) scores a set of predictions. Each function
returns `{"values": {name: float or null}, "undefined": {name: reason}}`: a
metric that cannot be computed is `null` with a reason, never NaN, infinity or a
quiet zero, so a result is canonical JSON. Inputs that are not finite, of unequal
length, or malformed (an unknown label, probabilities outside [0, 1] or rows that
do not sum to one) raise `BadInput`; that is a bug upstream, not an undefined
metric. Outputs are plain Python floats.

- `regression_metrics(y_true, y_pred)`: `rmse`, `mae`, `r2`, `spearman`.
- `classification_metrics(y_true, proba, classes, positive_class=None)`.
  `classes` is the stable class order and `y_true` holds those labels.
  Binary (two classes): `roc_auc`, `average_precision`, `balanced_accuracy`,
  `log_loss`; `proba` is the positive-class probability vector, or an `[n, 2]`
  matrix in `classes` order whose positive column is used; the positive class is
  `positive_class`, default `classes[1]`. Multiclass: `roc_auc_ovr_macro`,
  `average_precision_macro`, `balanced_accuracy`, `log_loss`; `proba` is `[n, k]`
  in `classes` order and `positive_class` must be `None`.
- `aggregate(values)`: `{"mean", "sd", "n_defined", "n_total"}` over the values
  that are not `null`.

`average_precision` is the metric called "PR-AUC" elsewhere; it is never labelled
that, because the trapezoid area under a precision-recall curve is a different
number.

| Convention | Rule |
| --- | --- |
| Class decision | binary: probability `>= 0.5` is positive (exactly 0.5 is positive); multiclass: argmax, the first class winning an exact tie. Thresholds are never tuned. |
| Log loss | natural log, mean over rows; each class probability (`p` and `1 - p` in the binary case) is clipped to `[1e-15, 1 - 1e-15]`, no renormalisation afterwards; at most 34.54 per row. Needs only one row. |
| ROC-AUC | rank-sum (Mann-Whitney) form with average ranks: a positive and a negative with equal scores count one half. Undefined with no positive rows or no negative rows. |
| Average precision | `sum (R_n - R_{n-1}) P_n` over distinct score thresholds, tied scores being one threshold (scikit-learn's step-wise definition, not the trapezoid). Undefined with no positive rows; with no negative rows it is defined (1.0). |
| Balanced accuracy | mean per-class recall over all of `classes`. Undefined, naming the class, if any class has no row in `y_true`: it is not averaged over the classes present. |
| Macro one-vs-rest (multiclass) | the plain mean over all classes of each class against the rest. Null, with a reason that names the first class that cannot contribute (no positive rows; for ROC-AUC also no negative rows), if any class cannot; never an average over a subset. |
| RMSE, MAE | undefined only with no rows. |
| R-squared | `1 - SS_res / SS_tot`; undefined when `y_true` is constant (`SS_tot` is 0), which includes one row. It can be negative. |
| Spearman | Pearson correlation of average ranks; undefined with fewer than two rows or when either input is constant. |
| No rows | every metric undefined, reason `no rows`. |
| `aggregate` | `mean` over defined values; `sd` is the sample SD (ddof 1), `null` with fewer than two defined values; `mean` and `sd` are `null` with none. `n_defined` and `n_total` report how many counted. A value that is NaN or infinite raises `BadInput`. |

## Layout

```
ligandbench/
  table.py      the CSV: quoted commas and newlines, BOM, duplicate/empty headers; standard library
  columns.py    what each column holds; structure candidates; qualifiers; standard library
  chem.py       structure identity (RDKit)
  scaffold.py   the Bemis-Murcko scaffold of an identity (RDKit)
  split.py      groups, the seeded allocator, five repeated holdouts (RDKit via scaffold)
  resolve.py    choices -> rows, verdicts, metadata, content hash (RDKit)
  depict.py     2D depictions as SVG, versioned for caching (RDKit)
  summary.py    the dashboard's statistics from the rows and metadata (RDKit, for scaffolds)
  features.py   the QSAR featuriser: Morgan bits and seven descriptors, its schema (RDKit, numpy)
  metrics.py    regression and classification metrics, aggregation (numpy)
  cli.py        the commands above
```
