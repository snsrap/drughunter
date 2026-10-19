/* DOM helpers shared by the views. */

export const $ = id => document.getElementById(id);

export function esc(t){ const d = document.createElement("div"); d.textContent = t; return d.innerHTML; }
export function attr(t){ return esc(t).replace(/'/g, "&#39;").replace(/"/g, "&quot;"); }

/* Replace an element's markup only when it changed, so re-rendering from
   state costs nothing (and keeps focus) when nothing moved. */
export function setHTML(el, html){
  if (el._html === html) return false;
  el.innerHTML = html; el._html = html;
  return true;
}

/* A panel message: {html, err} or null. */
export function setMsg(el, m){
  el.className = "pdbq-msg" + (m && m.err ? " err" : "");
  setHTML(el, m ? m.html : "");
}
export const note = html => ({html, err: false});
export const fault = text => ({html: esc(text), err: true});

export function plural(n, one, many){ return n + " " + (n === 1 ? one : (many || one + "s")); }
