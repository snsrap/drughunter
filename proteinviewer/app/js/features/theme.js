/* Light / dark. No saved choice means "follow the system", which is what the
   CSS does already; the inline script in <head> applies a saved choice before
   first paint. */

import {commit} from "../core/state.js";
import {$} from "../core/dom.js";
import * as scene from "../scene.js";

export function bind(){
  const mq = window.matchMedia("(prefers-color-scheme: dark)"),
        root = document.documentElement, th = $("theme");
  const paint = () => {
    const cur = root.getAttribute("data-theme") || (mq.matches ? "dark" : "light");
    th.querySelectorAll("button").forEach(b => b.setAttribute("aria-pressed", b.dataset.themeSet === cur));
  };
  th.querySelectorAll("button").forEach(b => {
    b.onclick = () => {
      root.setAttribute("data-theme", b.dataset.themeSet);
      try{ localStorage.setItem("pv-theme", b.dataset.themeSet); }catch(e){}
    };
  });
  /* Theme colours reach 3Dmol only through code, so redraw on any change. */
  const repaint = () => { paint(); scene.setBackground(); commit(); };
  if (mq.addEventListener) mq.addEventListener("change", repaint);
  new MutationObserver(repaint).observe(root, {attributes: true, attributeFilter: ["data-theme"]});
  paint();
}
