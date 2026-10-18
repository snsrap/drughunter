/* Colours that carry meaning: one per structure, one per pocket. */

export function cssVar(v){ return getComputedStyle(document.documentElement).getPropertyValue(v).trim(); }

/* Cartoon colour per structure; the first slot follows the theme. */
export const ENT_COLORS = [null, "#5f7fa8", "#a0815c", "#6b9a78", "#9b6f95", "#5c9a9e"];
export function entColor(e){ return ENT_COLORS[e.ci] || cssVar("--gl-cartoon"); }

/* One colour per pocket, reused for that pocket's generated molecules so the
   groups are readable against each other. No orange (the selected-molecule
   carbon), so a pocket is never mistaken for it. */
export const POCKET_COLORS = ["#3f8fd0", "#c0569e", "#2fa39a", "#8b7ec8", "#b4656f", "#a08a3a"];
export function pocketColor(i){ return POCKET_COLORS[i % POCKET_COLORS.length]; }
/* The colour of the pocket with this rank in a run (generated groups are
   keyed by rank, colours by position). */
export function rankColor(run, rank){
  const idx = run ? run.pockets.findIndex(p => String(p.rank) === String(rank)) : -1;
  return pocketColor(idx < 0 ? 0 : idx);
}
