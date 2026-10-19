/* Opening a session saved here: one file holding every structure and what was
   done to it, the pocket run, and the molecules generated from it.

   Saving is in features/project.js, beside the output directory the same
   state is written into. This half is the one with a decision in it: the
   restored structures carry their old uids, so the models already parsed have
   to go before the file's text is parsed under the same names. */

import {state, commit, nodeOf} from "../core/state.js";
import {esc, note, fault, plural} from "../core/dom.js";
import {restore} from "../model/session.js";
import {adopt} from "./project.js";
import * as scene from "../scene.js";

/* Open a session file's text. Reports in the panel rather than throwing: it
   is called for whichever file the user dropped. */
export function openSession(text){
  let snap;
  try{ snap = JSON.parse(text); }
  catch(err){ return failed("that file is not valid JSON"); }
  /* Into a draft first, so a file that turns out not to be a session leaves
     what is on screen alone. */
  const draft = {};
  let sum;
  try{ sum = restore(draft, snap); }
  catch(err){ return failed(err.message); }
  state.ents = []; state.run = null; state.gen = null;
  commit();
  Object.assign(state, draft);
  /* Parse now, so the picks the file names are checked against the trees they
     were taken from rather than against trees that do not exist yet. */
  scene.ensureModels();
  state.picks = state.picks.filter(k => nodeOf(k));
  if (state.pickAnchor && !nodeOf(state.pickAnchor)) state.pickAnchor = null;
  state.session.msg = note("Restored " + plural(sum.ents, "structure") +
    (sum.pockets ? " · " + plural(sum.pockets, "pocket") : "") +
    (sum.molecules ? " · " + plural(sum.molecules, "molecule") : "") +
    (savedOn(sum.saved) ? " · saved " + esc(savedOn(sum.saved)) : ""));
  commit({fit: true});
  adopt();
}

/* The file records when it was saved in UTC; read it back in the reader's own
   time, or a session saved this evening is dated tomorrow. */
function savedOn(iso){
  const d = new Date(iso);
  return isFinite(+d) ? d.toLocaleString(undefined, {dateStyle: "medium", timeStyle: "short"}) : "";
}

function failed(message){
  state.session.msg = fault("Could not open that session: " + message);
  commit();
}
