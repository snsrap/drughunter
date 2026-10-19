/* Files in and out of the page: the text of a file the user chose or dropped,
   and a file handed back for them to keep. Nothing here decides what the text
   means -- that is model/structurefile.js and model/session.js. */

/* A file's text. Gzip is detected by its magic number rather than by the
   name, since a .pdb.gz downloaded from RCSB is often saved without the .gz
   and a plain file is sometimes named with one. */
export async function readText(file){
  const buf = await file.arrayBuffer();
  const head = new Uint8Array(buf, 0, Math.min(2, buf.byteLength));
  if (head[0] === 0x1f && head[1] === 0x8b) return gunzip(buf);
  return new TextDecoder().decode(buf);
}

function gunzip(buf){
  if (typeof DecompressionStream !== "function")
    return Promise.reject(new Error("this browser cannot unpack .gz files — unpack it first"));
  const stream = new Blob([buf]).stream().pipeThrough(new DecompressionStream("gzip"));
  return new Response(stream).text();
}

/* Hand `text` to the user as a file to keep. */
export function save(filename, text, mime){
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], {type: mime || "application/octet-stream"}));
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

/* Whether a drag carries files, as opposed to text dragged out of the page. */
export function hasFiles(ev){
  const types = ev.dataTransfer && ev.dataTransfer.types;
  return !!types && Array.prototype.indexOf.call(types, "Files") !== -1;
}
