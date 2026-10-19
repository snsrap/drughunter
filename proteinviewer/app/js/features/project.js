/* The session's output directory.

   The server collects what each run produces -- the structure it ran on, the
   prepared PDB and its report, the pockets with their site points and
   residue lists, the generated molecules with their properties -- in one
   directory per session. Nothing here uploads anything: the page only asks
   for the directory to exist (once, before the first run that writes a file),
   names it in each request, and asks for it to be copied somewhere.

   Saving writes the page's own session.json into it first, so what lands on
   disk is both the files other programs read and the state this page reopens.
*/

import {state, commit} from "../core/state.js";
import {$, esc, setHTML, setMsg, note, fault, plural} from "../core/dom.js";
import {save} from "../core/files.js";
import * as api from "../core/api.js";
import {snapshot} from "../model/session.js";

/* ---------------- view ---------------- */
export function render(st){
  const p = st.project, server = st.server;
  $("pj-n").textContent = p.files ? plural(p.files, "file") : "";
  setHTML($("pj-where"), where(st));
  $("pj-path").value = p.dest;
  $("pj-path").disabled = p.busy;
  $("pj-save").disabled = p.busy || !p.id;
  $("pj-save").textContent = p.busy ? "Saving…" : "Save project";
  $("sess-save").disabled = !st.ents.length;
  setMsg($("pj-msg"), p.msg);
  setMsg($("sess-msg"), st.session.msg);
}

function where(st){
  const p = st.project;
  if (p.path)
    return "Collecting in <span class='mono'>" + esc(p.path) + "</span>";
  if (st.server.known && !st.server.projects)
    return "This server was started without a project directory, so nothing is " +
           "collected. The downloads above still work.";
  return "A directory is started for this session as soon as you prepare, " +
         "find pockets or generate." +
         (st.server.projectRoot ? " They live under <span class='mono'>" +
            esc(st.server.projectRoot) + "</span>." : "");
}

/* ---------------- actions ---------------- */
/* The id every request that writes a file carries. Made on demand, so a
   session that only looks at structures never leaves a directory behind;
   named after what is loaded. Artifacts are a side effect, so a server that
   cannot make one does not stop the run: this answers "". */
export async function ensureProject(){
  const p = state.project;
  if (p.id) return p.id;
  if (state.server.known && !state.server.projects) return "";
  try{
    const made = await api.createProject((state.ents[0] || {}).id || "session");
    state.project = {...state.project, id: made.id, path: made.path,
                     files: made.files || 0, bytes: made.bytes || 0};
    commit();
    return made.id;
  }catch(err){
    state.project = {...state.project, msg: fault(
      "No project directory: " + err.message + ". Runs still work; their files are not kept.")};
    commit();
    return "";
  }
}

/* A restored session names the directory it was collecting into. On the same
   server it is still there -- the store reopens a project by its id, even
   across a restart -- and otherwise this session starts a new one with its
   next run, rather than naming one the server would refuse. */
export async function adopt(){
  if (!state.project.id) return;
  try{
    const now = await api.projectStatus(state.project.id);
    state.project = {...state.project, path: now.path,
                     files: now.files || 0, bytes: now.bytes || 0};
  }catch(err){
    state.project = {...state.project, id: null, path: null, files: 0, bytes: 0,
      msg: note("The project directory this session was collecting into is not on " +
                "this server, so a new one starts with the next run.")};
  }
  commit();
}

/* How much is in the project now. Called after a run has written into it;
   failing to count is not worth reporting. */
export async function touch(){
  if (!state.project.id) return;
  try{
    const now = await api.projectStatus(state.project.id);
    state.project = {...state.project, files: now.files || 0, bytes: now.bytes || 0};
    commit();
  }catch(err){ /* the count is decoration */ }
}

async function saveProject(){
  const p = state.project;
  if (!p.id || p.busy) return;
  state.project = {...p, busy: true, msg: note("Saving the project…")};
  commit();
  try{
    const out = await api.saveProject({project: p.id, path: p.dest,
                                       session: snapshot(state)});
    state.project = {...state.project, busy: false, dest: out.path,
      msg: note("Wrote " + plural(out.files.length, "file") + " (" + size(out.bytes) +
                ") to <b>" + esc(out.path) + "</b>" +
                (out.path !== p.dest.replace(/\/+$/, "")
                   ? " — there was already something in the directory you named."
                   : "."))};
  }catch(err){
    state.project = {...state.project, busy: false,
                     msg: fault("Could not save the project: " + err.message)};
  }
  commit();
}

function size(bytes){
  const mb = (bytes || 0) / 1048576;
  return mb >= 1 ? mb.toFixed(1) + " MB" : Math.max(1, Math.round((bytes || 0) / 1024)) + " kB";
}

/* ---------------- the session file on its own ---------------- */
/* One file, straight out of the browser: the whole session's state, without
   the server writing anywhere. It is also written into a saved project. */
function filename(st){
  const ids = st.ents.map(e => e.id);
  return (ids.length === 1 ? ids[0] : ids.length + "-structures") + "-session.json";
}

function saveSessionFile(){
  if (!state.ents.length) return;
  const name = filename(state), text = JSON.stringify(snapshot(state));
  try{
    save(name, text, "application/json");
  }catch(err){
    state.session.msg = fault("Could not save the session file: " + err.message);
    return commit();
  }
  state.session.msg = note("Saved <b>" + esc(name) + "</b> · " + size(text.length) +
    " · open it here to come back to this view.");
  commit();
}

export function bind(){
  $("pj-path").oninput = ev => { state.project.dest = ev.target.value; };
  $("pj-save").onclick = saveProject;
  $("sess-save").onclick = saveSessionFile;
}
