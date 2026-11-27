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

## Layout

```
ligandbench/
  table.py      the CSV: quoted commas and newlines, BOM, duplicate/empty headers; standard library
  columns.py    what each column holds; structure candidates; qualifiers; standard library
  chem.py       structure identity (RDKit)
  scaffold.py   the Bemis-Murcko scaffold of an identity (RDKit)
  resolve.py    choices -> rows, verdicts, metadata, content hash (RDKit)
  depict.py     2D depictions as SVG, versioned for caching (RDKit)
  summary.py    the dashboard's statistics from the rows and metadata (RDKit, for scaffolds)
  cli.py        the commands above
```
