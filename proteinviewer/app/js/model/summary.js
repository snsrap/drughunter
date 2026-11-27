/* The dataset summary, as the page reasons about it: which of the panel's states
   to show, how each number is worded, and the geometry of the two charts. Pure
   -- no DOM, no fetch -- so it is testable without a browser (`npm test`).

   The server sends one answer (ligandbench/summary.py): counts that name the
   population they are counts of, a target that is a histogram or class counts
   (or says there is none), scaffold concentration, and missing cells. Every
   number is already computed; what is here is wording and layout, never
   arithmetic on rows -- the page is never given one. */

/* A histogram never has more bars than this (the server's own ceiling); the
   chart draws one rect per bin, so it is bounded however many rows there are. */
export const MAX_BARS = 50;

/* ---------------- numbers, as a person would read them ---------------- */

export const count = n => String(Math.round(n)).replace(/\B(?=(\d{3})+(?!\d))/g, ",");

/* "12.3%" of a whole, "<0.1%" for a share too small to show (and ">99.9%" for one
   that is not quite all), "—" with no whole. */
export function percent(part, whole){
  if (!whole) return "—";
  const p = 100 * part / whole;
  if (part > 0 && p < 0.1) return "<0.1%";
  if (part < whole && p > 99.9) return ">99.9%";
  return String(Number(p.toFixed(1))) + "%";
}

/* A measurement: four significant figures, no trailing zeros, and an exponent
   only where a plain number would be a wall of digits. */
export function number(v){
  if (v === null || v === undefined || !Number.isFinite(+v)) return "—";
  const x = +v;
  if (x === 0) return "0";
  const a = Math.abs(x);
  if (a < 1e-3 || a >= 1e7) return x.toExponential(2).replace(/\.?0+e/, "e");
  return String(Number(x.toPrecision(4)));
}

const upper = s => s ? s[0].toUpperCase() + s.slice(1) : s;
const plural = (n, one, many) => count(n) + " " + (n === 1 ? one : (many || one + "s"));

/* ---------------- the request's life ---------------- */

/* One summary request: for which dataset, and how it stands. */
export const idleTask = () => ({id: null, status: "idle", data: null, error: null});
export const startTask = id => id ? {id, status: "loading", data: null, error: null} : idleTask();

/* An answer or a failure applies only to the task it was asked for; one that
   arrives after the page has moved to another dataset changes nothing. */
export const finishTask = (task, id, data) =>
  task.id === id ? {id, status: "ready", data, error: null} : task;
export const failTask = (task, id, error) =>
  task.id === id ? {id, status: "error", data: null, error: String(error)} : task;

/* What the server's refusal says, in words that tell the user what to do. A server
   older than this page has no such endpoint and says only that. */
export function errorText(message){
  return /no such endpoint/i.test(message || "")
    ? "This server predates the dataset summary — restart it."
    : message || "The summary could not be made.";
}

/* Which of the panel's four states to show. A "ready" task with no data is a
   fault in the caller, and is shown as an error rather than an empty panel. */
export function panelState(task){
  if (!task || !task.id) return {kind: "idle"};
  if (task.status === "loading") return {kind: "loading"};
  if (task.status === "error") return {kind: "error", message: task.error || "The summary could not be made."};
  if (task.status === "ready" && task.data) return {kind: "ready"};
  return {kind: "error", message: "The summary came back empty."};
}

/* ---------------- named populations ---------------- */

/* "98% of source rows": a count's share of the population it is a share of. */
function ofText(n, pop, pops){
  const whole = pop.of ? pops[pop.of] : null;
  return whole ? percent(n, whole.n) + " of " + whole.label : "";
}

export function populationRows(s){
  const p = s.populations, noTarget = s.target.state === "no_target";
  const rows = [
    {label: "Source rows", value: count(p.source_rows.n), note: "every row of the file"},
    {label: "Valid structures", value: count(p.valid_structures.n),
     note: ofText(p.valid_structures.n, p.valid_structures, p)},
    {label: "Parse failures", value: count(s.parse_failures.n),
     note: s.parse_failures.n ? percent(s.parse_failures.n, p.source_rows.n) + " of source rows" : "every structure parsed"},
  ];
  rows.push(noTarget
    ? {label: "Training-eligible", value: "—", note: "no target: this dataset is for prediction only"}
    : {label: "Training-eligible", value: count(p.training_eligible.n),
       note: ofText(p.training_eligible.n, p.training_eligible, p) + " have a structure and a usable target"});
  return rows;
}

/* Why rows cannot train. Not shown for a dataset with no target: every row has
   that one reason, and saying so is the "No target" display's job. */
export function exclusionView(s){
  const ex = s.exclusions;
  if (s.target.state === "no_target" || !ex.by_reason.length) return {shown: false, rows: [], note: ""};
  const total = ex.by_reason.reduce((a, r) => a + r.n, 0);
  return {
    shown: true,
    heading: plural(ex.n_rows, "row") + " left out of training",
    rows: ex.by_reason.map(r => ({label: upper(r.label), value: count(r.n),
                                  note: percent(r.n, s.populations.source_rows.n) + " of source rows"})),
    note: "Reasons overlap: a row can have more than one, so these add up to " + count(total) +
          " for " + plural(ex.n_rows, "row") + " left out.",
  };
}

export function duplicateRows(s){
  const d = s.duplicates, valid = s.populations.valid_structures;
  return [
    {label: "Duplicate rows", value: count(d.n_duplicate_rows),
     note: d.n_duplicate_rows
       ? count(valid.n) + " valid rows − " + count(d.n_distinct) + " distinct structures"
       : "every valid row is a different structure"},
    {label: "Repeated structures", value: count(d.n_groups),
     note: d.n_groups ? "found in " + plural(d.n_rows_in_groups, "row") : "none"},
    d.n_conflicting === null
      ? {label: "Conflicting targets", value: "—", note: upper(d.conflicting_reason || "not defined")}
      : {label: "Conflicting targets", value: count(d.n_conflicting),
         note: "repeated structures whose labels differ"},
  ];
}

export function scaffoldRows(s){
  const sc = s.scaffolds, n = sc.n_structures;
  const rows = [
    {label: "Distinct scaffolds", value: count(sc.n_distinct), note: "among " + plural(n, "valid structure")},
    {label: "Largest group", value: plural(sc.largest_group_size, "row"),
     note: percent(sc.largest_group_size, n) + " of valid structures"},
    {label: "Top-10 share", value: sc.top10_share === null ? "—" : percent(sc.top10_rows, n),
     note: count(sc.top10_rows) + " rows in the " + (sc.top10_groups === 10 ? "10 largest groups" : plural(sc.top10_groups, "group"))},
    {label: "Acyclic rows", value: count(sc.acyclic_rows),
     note: "no ring, so no scaffold: counted as one group"},
  ];
  if (sc.n_unscaffolded)
    rows.push({label: "Not scaffolded", value: count(sc.n_unscaffolded), note: "RDKit could not make a scaffold; in no group"});
  return rows;
}

export const scaffoldPolicy = s => "Bemis–Murcko scaffolds, " + s.scaffolds.policy;

/* Every source column, in the file's order, with how much of it is empty. A
   malformed value is not empty and is not here. */
export function missingView(s){
  const whole = s.populations.source_rows.n, cols = s.missing.columns;
  const some = cols.filter(c => c.n_missing > 0).length;
  return {
    heading: some ? plural(some, "column") + " of " + count(cols.length) + " with missing cells"
                  : "no missing cells in any of " + plural(cols.length, "column"),
    note: "A cell that is blank or only spaces is missing. A value that is there but wrong for its column is not.",
    rows: cols.map(c => ({name: c.name, n: c.n_missing, value: count(c.n_missing),
                          share: percent(c.n_missing, whole),
                          frac: whole ? Math.min(1, c.n_missing / whole) : 0})),
  };
}

/* ---------------- the target: one of four displays ---------------- */

/* What the model target is, in the words of the dataset's own choices. */
export function targetLabel(t){
  if (t.kind === "classification") return "“" + t.column + "”, classes";
  if (t.transform === "pki") return "pKi = 9 − log10(Ki in nM), from “" + t.column + "”";
  return "“" + t.column + "”" + (t.units ? " in " + t.units : "");
}
export const axisLabel = t => t.transform === "pki" ? "pKi" : (t.units || t.column);

/* The bars of a histogram, in a width x height box, from the server's bins.
   Equal-width bins, so a bar's x is its index; one bar per bin, never per row. */
export function histogramBars(bins, width, height){
  const k = bins.counts.length, peak = Math.max.apply(null, bins.counts) || 1;
  const bw = width / k;
  return bins.counts.map((c, i) => {
    const h = c / peak * height;
    return {i, x: i * bw, y: height - h, w: bw, h, count: c, lo: bins.edges[i], hi: bins.edges[i + 1],
            label: binLabel(bins.edges[i], bins.edges[i + 1], c)};
  });
}
export function binLabel(lo, hi, n){
  return (lo === hi ? number(lo) : number(lo) + " – " + number(hi)) + ": " + plural(n, "row");
}
/* Where the axis says what the values are: the smallest, the middle, the largest. */
export function axisTicks(bins){
  const lo = bins.edges[0], hi = bins.edges[bins.edges.length - 1];
  return lo === hi ? [{pos: 0.5, label: number(lo)}]
    : [{pos: 0, label: number(lo)}, {pos: 0.5, label: number((lo + hi) / 2)}, {pos: 1, label: number(hi)}];
}

/* Class counts as bars of equal height and a length in proportion to the
   largest count: the label, the count, its share of the training rows. */
export function classRows(t){
  const peak = Math.max.apply(null, t.classes.map(c => c.count)) || 1;
  return t.classes.map(c => ({label: c.label, positive: c.positive, count: c.count, value: count(c.count),
                              share: percent(c.count, t.n), frac: c.count / peak}));
}

/* What the target block shows. `kind` is the display: a histogram, class
   counts, "No target", or an empty state -- never a chart that looks like data
   when there is none. */
export function targetView(t){
  const training = " training-eligible rows";
  switch (t.state){
    case "regression": {
      const b = t.bins;
      return {
        kind: "regression", heading: "Target distribution",
        caption: "Model target: " + targetLabel(t) + ". " + count(t.n_binned) + training +
                 (t.n_not_binned ? "; " + count(t.n_not_binned) + " with no finite value are not drawn" : "") + ".",
        axis: axisLabel(t), ticks: axisTicks(b), bars: histogramBars(b, 400, 120),
        ruleNote: b.rule,
        stats: [["Smallest", number(t.min)], ["Median", number(t.median)], ["Mean", number(t.mean)],
                ["Largest", number(t.max)], ["Bins", String(b.n_bins)]],
      };
    }
    case "classification":
      return {
        kind: "classification", heading: "Target classes",
        caption: "Model target: " + targetLabel(t) + ". " + count(t.n) + training +
                 (t.positive_class ? "; the positive class is “" + t.positive_class + "”" : "") + ".",
        rows: classRows(t),
      };
    case "no_target":
      return {
        kind: "no_target", heading: "Target distribution", message: "No target",
        detail: "This dataset has no target column, so it is for prediction only and there is no distribution to show.",
      };
    default:
      return {
        kind: "empty", heading: "Target distribution", message: "No training-eligible rows",
        detail: "No row has both a structure and a usable target, so there is nothing to draw. " +
                "The counts above say why each row was left out.",
      };
  }
}
