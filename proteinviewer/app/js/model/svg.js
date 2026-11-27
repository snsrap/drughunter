/* RDKit's depictions are plain drawings: shapes, text and style attributes.
   Anything else -- a script, an event handler, a reference out to another
   document -- means the drawing was not made by this page's server, so it is
   dropped rather than cleaned up and trusted. Used for the depictions in a
   saved session, which comes off disk, and for the ones a dataset's rows carry. */

const SVG_TAGS = ["svg", "g", "defs", "path", "rect", "circle", "ellipse", "line",
                  "polyline", "polygon", "text", "tspan", "title", "desc"];

export function safeSvg(s){
  if (typeof s !== "string" || !/^\s*<svg[\s>]/i.test(s)) return "";
  if (/\son[a-z]+\s*=|javascript:|<!--|<!\[|<\?|&#|xlink:href|\shref\s*=/i.test(s)) return "";
  const tags = s.match(/<\/?([A-Za-z][A-Za-z0-9:-]*)/g) || [];
  for (const t of tags)
    if (SVG_TAGS.indexOf(t.replace(/^<\/?/, "").toLowerCase()) === -1) return "";
  return s;
}
