/* The server's API: every request the page makes to serve.py goes through
   here, so errors come back as one kind of Error with a readable message. */

/* The response shape this page expects; see proteinviewer/service.py. */
export const API_VERSION = 5;

export class StaleServerError extends Error {}

/* A serve.py older than an endpoint answers it with an HTML error page, so
   check the content type before parsing -- otherwise the failure surfaces as
   an unhelpful JSON syntax error about "<!DOCTYPE". */
function isJSON(r){
  return (r.headers.get("content-type") || "").indexOf("json") !== -1;
}

async function request(url, init){
  const r = await fetch(url, init);
  if (!isJSON(r)){
    if (r.status === 501 || r.status === 404)
      throw new StaleServerError("this serve.py predates this page — restart it");
    throw new Error("server returned HTTP " + r.status + ", not JSON");
  }
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || ("HTTP " + r.status));
  return j;
}

function post(url, body){
  return request(url, {method: "POST", headers: {"Content-Type": "application/json"},
                       body: JSON.stringify(body)});
}

/* Which tools the server found. Rejects with StaleServerError if the server
   is older than this page. */
export async function status(){
  const j = await request("/api/status");
  if (j.api !== API_VERSION) throw new StaleServerError("this serve.py predates this page — restart it");
  return j;
}
/* Post a structure once; later runs name it by the returned token. */
export function bufferStructure(st){
  return post("/api/structure", {id: st.token, fmt: st.fmt, data: st.data});
}
export function findPockets(body){ return post("/api/pockets", body); }
/* Move one buffered structure onto another: {token, reference, on, mobileOn}
   -> {data, fit}. */
export function superpose(body){ return post("/api/superpose", body); }
/* The session's output directory: started on demand, then named by every
   request that produces a file. */
export function createProject(name){ return post("/api/project", {name}); }
export function projectStatus(id){
  return request("/api/project?id=" + encodeURIComponent(id));
}
export function saveProject(body){ return post("/api/project/save", body); }
export function prepare(body){ return post("/api/prep", body); }
export function startGeneration(body){ return post("/api/generate", body); }
/* Score a finished job's poses in their pockets: {job, token, mode, padding,
   ranks, project} -> {job, ranks, mode}. Its own run, polled with scoreJob. */
export function startScoring(body){ return post("/api/score-poses", body); }
export function scoreJob(id){
  return request("/api/score-job?id=" + encodeURIComponent(id));
}
/* opts.molecules / opts.table also fetch the SDF per pocket and the rows. */
export function job(id, opts){
  opts = opts || {};
  return request("/api/job?id=" + encodeURIComponent(id) +
                 (opts.molecules ? "&mols=1" : "") + (opts.table ? "&table=1" : ""));
}
