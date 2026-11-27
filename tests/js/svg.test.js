// What may be inserted as a molecule's drawing: run with `npm test`.
import {test} from "node:test";
import assert from "node:assert/strict";

import {safeSvg} from "../../proteinviewer/app/js/model/svg.js";

/* What RDKit writes: shapes, paths and styled text. */
const RDKIT = `<svg version='1.1' baseProfile='full' xmlns='http://www.w3.org/2000/svg'
  xmlns:rdkit='http://www.rdkit.org/xml' xmlns:xlink='http://www.w3.org/1999/xlink'
  xml:space='preserve' width='180px' height='130px' viewBox='0 0 180 130'>
<rect style='opacity:1.0;fill:none;stroke:none' width='180.0' height='130.0' x='0.0' y='0.0'> </rect>
<path class='bond-0 atom-0 atom-1' d='M 10,20 L 30,40' style='fill:none;stroke:currentColor' />
<text x='5' y='6' class='atom-2' style='font-size:12px;fill:#FF0000' >O</text>
</svg>`;

test("a drawing made of shapes and text is kept as it is", () => {
  assert.equal(safeSvg(RDKIT), RDKIT);
});

test("anything that could run or fetch is dropped, not cleaned", () => {
  for (const bad of [
    "<svg><script>alert(1)</script></svg>",
    "<svg onload='alert(1)'></svg>",
    "<svg><rect onclick = 'x()'/></svg>",
    "<svg><foreignObject><div>hi</div></foreignObject></svg>",
    "<svg><a href='https://evil.example'><text>x</text></a></svg>",
    "<svg><image href='https://evil.example/x.png'/></svg>",
    "<svg><use xlink:href='#a'/></svg>",
    "<svg><path d='M0 0' style='fill:url(javascript:alert(1))'/></svg>",
    "<svg><!-- x --></svg>",
    "<svg><![CDATA[x]]></svg>",
    "<svg><text>&#60;script&#62;</text></svg>",
    "<?xml version='1.0'?><svg></svg>",
    "<div><svg></svg></div>",
    "",
    null,
    42,
  ]) assert.equal(safeSvg(bad), "", String(bad));
});
