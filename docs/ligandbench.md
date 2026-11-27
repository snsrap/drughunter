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
ligandbench info                                    # RDKit version, depiction version
ligandbench inspect export.csv                      # columns, types, structure candidates
ligandbench resolve export.csv --config c.json --out DIR   # rows.json + dataset.json
ligandbench split DIR --strategy scaffold --seed 7  # five repeated holdouts of a resolved dataset
ligandbench depict request.json                     # {"smiles": [...]} -> SVGs
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
  cli.py        the commands above
```
