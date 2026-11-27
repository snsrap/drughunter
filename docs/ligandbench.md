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

## Layout

```
ligandbench/
  table.py      the CSV: quoted commas and newlines, BOM, duplicate/empty headers; standard library
  columns.py    what each column holds; structure candidates; qualifiers; standard library
  chem.py       structure identity (RDKit)
  resolve.py    choices -> rows, verdicts, metadata, content hash (RDKit)
  depict.py     2D depictions as SVG, versioned for caching (RDKit)
  cli.py        the commands above
```
