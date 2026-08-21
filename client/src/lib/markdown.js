// A small markdown renderer.
//
// A CDN dependency would defeat the point of a local-first tool -- the phone
// must render a conversation on a network that can reach the tailnet and
// nothing else. This covers what a chat assistant actually emits: fenced code,
// headings, lists, quotes, tables, and the usual inline marks.
//
// Everything is escaped before any tag is emitted; no model output ever
// reaches innerHTML unescaped.

const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

// NUL-delimited, so a code-span placeholder can never collide with real text.
// A bare "1" would match an ordinary number in the sentence.
const MARK = String.fromCharCode(0);

function escapeHtml(text) {
  return text.replace(/[&<>"']/g, (ch) => ESCAPES[ch]);
}

function isSafeUrl(url) {
  return /^(https?:|mailto:|\/|#)/i.test(url.trim());
}

function inline(text) {
  // Code spans come out first so their contents are never re-parsed as
  // markdown -- otherwise a_b_c inside backticks turns into an <em>.
  const spans = [];
  let out = text.replace(/(`+)([\s\S]*?)\1/g, (_match, _ticks, code) => {
    spans.push("<code>" + escapeHtml(code.trim()) + "</code>");
    return MARK + (spans.length - 1) + MARK;
  });

  // Maths next, and for the same reason: `$a_i$` is full of characters the
  // emphasis rules below would happily eat.
  out = out.replace(
    /\$\$([\s\S]+?)\$\$|\$([^$\n]+?)\$|\\\(([\s\S]+?)\\\)|\\\[([\s\S]+?)\\\]/g,
    (match, display, inlineMath, paren, bracket) => {
      const tex = display ?? inlineMath ?? paren ?? bracket;
      // A price is not an equation. Only spans carrying an actual TeX
      // construct are converted, so "$5 and $10" is left alone.
      if (!/[\\^_]/.test(tex)) return match;
      spans.push('<span class="math">' + escapeHtml(renderMath(tex)) + "</span>");
      return MARK + (spans.length - 1) + MARK;
    },
  );

  out = escapeHtml(out);

  out = out
    .replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, (_m, alt, src) =>
      isSafeUrl(src) ? '<img src="' + src + '" alt="' + alt + '" />' : alt,
    )
    .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (m, label, href) =>
      isSafeUrl(href)
        ? '<a href="' + href + '" target="_blank" rel="noopener noreferrer">' +
          label +
          "</a>"
        : m,
    )
    .replace(/\*\*\*([^*]+)\*\*\*/g, "<strong><em>$1</em></strong>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[\s(])\*([^*\n]+)\*/g, "$1<em>$2</em>")
    .replace(/(^|[\s(])_([^_\n]+)_(?=[\s).,!?]|$)/g, "$1<em>$2</em>")
    .replace(/~~([^~]+)~~/g, "<del>$1</del>");

  return out.replace(
    new RegExp(MARK + "(\\d+)" + MARK, "g"),
    (_m, index) => spans[Number(index)],
  );
}

// --- maths ------------------------------------------------------------------
//
// Not a TeX engine. Models pepper ordinary prose with `$\rightarrow$` and
// `$O(n^2)$` whether or not anything can render it, and the failure mode --
// raw backslashes in the middle of a sentence -- is worse than the notation
// was ever worth. Translating the handful of symbols that actually show up in
// chat costs a lookup table; KaTeX would cost a third of a megabyte to do the
// same job for text that is rarely more than an arrow.
//
// Anything unrecognised is left exactly as written, on the grounds that a
// visible `\oiint` is more honest than a silently dropped one.

const MATH_SYMBOLS = {
  rightarrow: "→", to: "→", leftarrow: "←", gets: "←", leftrightarrow: "↔",
  Rightarrow: "⇒", Leftarrow: "⇐", Leftrightarrow: "⇔", implies: "⇒", iff: "⇔",
  uparrow: "↑", downarrow: "↓", mapsto: "↦",
  times: "×", div: "÷", pm: "±", mp: "∓", cdot: "·", ast: "∗", star: "⋆",
  le: "≤", leq: "≤", ge: "≥", geq: "≥", ne: "≠", neq: "≠", approx: "≈",
  equiv: "≡", sim: "∼", simeq: "≃", cong: "≅", propto: "∝", ll: "≪", gg: "≫",
  in: "∈", notin: "∉", ni: "∋", subset: "⊂", subseteq: "⊆", supset: "⊃",
  supseteq: "⊇", cup: "∪", cap: "∩", setminus: "∖", emptyset: "∅", varnothing: "∅",
  forall: "∀", exists: "∃", nexists: "∄", neg: "¬", lnot: "¬", land: "∧",
  wedge: "∧", lor: "∨", vee: "∨", therefore: "∴", because: "∵",
  infty: "∞", partial: "∂", nabla: "∇", sum: "∑", prod: "∏", int: "∫",
  oint: "∮", sqrt: "√", angle: "∠", perp: "⊥", parallel: "∥", degree: "°",
  circ: "∘", bullet: "∙", oplus: "⊕", otimes: "⊗", checkmark: "✓",
  dots: "…", ldots: "…", cdots: "⋯", vdots: "⋮", ddots: "⋱", prime: "′",
  alpha: "α", beta: "β", gamma: "γ", delta: "δ", epsilon: "ε", varepsilon: "ε",
  zeta: "ζ", eta: "η", theta: "θ", vartheta: "ϑ", iota: "ι", kappa: "κ",
  lambda: "λ", mu: "μ", nu: "ν", xi: "ξ", pi: "π", rho: "ρ", sigma: "σ",
  tau: "τ", upsilon: "υ", phi: "φ", varphi: "φ", chi: "χ", psi: "ψ", omega: "ω",
  Gamma: "Γ", Delta: "Δ", Theta: "Θ", Lambda: "Λ", Xi: "Ξ", Pi: "Π",
  Sigma: "Σ", Upsilon: "Υ", Phi: "Φ", Psi: "Ψ", Omega: "Ω",
};

const SUPERSCRIPT = {
  0: "⁰", 1: "¹", 2: "²", 3: "³", 4: "⁴", 5: "⁵", 6: "⁶", 7: "⁷", 8: "⁸",
  9: "⁹", "+": "⁺", "-": "⁻", "−": "⁻", "=": "⁼", "(": "⁽", ")": "⁾",
  n: "ⁿ", i: "ⁱ", T: "ᵀ",
};

const SUBSCRIPT = {
  0: "₀", 1: "₁", 2: "₂", 3: "₃", 4: "₄", 5: "₅", 6: "₆", 7: "₇", 8: "₈",
  9: "₉", "+": "₊", "-": "₋", "−": "₋", "=": "₌", "(": "₍", ")": "₎",
  a: "ₐ", e: "ₑ", i: "ᵢ", j: "ⱼ", k: "ₖ", m: "ₘ", n: "ₙ", o: "ₒ",
  p: "ₚ", r: "ᵣ", s: "ₛ", t: "ₜ", u: "ᵤ", v: "ᵥ", x: "ₓ",
};

// All or nothing: a half-converted exponent (x²ʸ with the y left flat) reads
// as a typo, so anything without a Unicode form keeps its original notation.
function toScript(body, table, original) {
  const mapped = [...body].map((character) => table[character]);
  return mapped.every(Boolean) ? mapped.join("") : original;
}

export function renderMath(source) {
  let text = source;

  // Wrappers whose only job is typesetting: keep what is inside them.
  text = text.replace(
    /\\(?:text|textrm|textbf|mathrm|mathbf|mathit|mathsf|mathcal|operatorname)\s*\{([^{}]*)\}/g,
    "$1",
  );
  text = text.replace(/\\frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}/g, "$1/$2");
  text = text.replace(/\\sqrt\s*\{([^{}]*)\}/g, "√($1)");
  text = text.replace(/\\(?:left|right|big|Big|bigg|Bigg|displaystyle|limits)\b/g, "");
  text = text.replace(/\\[,;:!>]/g, " ");

  // Function names are set upright in TeX and spelled normally everywhere
  // else, so the backslash is all that has to go.
  text = text.replace(
    /\\(log|ln|lg|exp|sin|cos|tan|sec|csc|cot|arcsin|arccos|arctan|sinh|cosh|tanh|max|min|lim|sup|inf|det|dim|deg|gcd|arg|bmod|pmod|mod)\b/g,
    "$1",
  );

  text = text.replace(/\\([A-Za-z]+)/g, (match, name) => MATH_SYMBOLS[name] ?? match);

  text = text.replace(/\^\{([^{}]*)\}|\^(\S)/g, (match, braced, single) =>
    toScript(braced ?? single, SUPERSCRIPT, match),
  );
  text = text.replace(/_\{([^{}]*)\}|_(\S)/g, (match, braced, single) =>
    toScript(braced ?? single, SUBSCRIPT, match),
  );

  // Grouping braces have served their purpose by here.
  text = text.replace(/(^|[^\\])[{}]/g, "$1");
  return text.replace(/\s+/g, " ").trim();
}

function renderTable(rows) {
  const cells = (row) =>
    row
      .replace(/^\s*\|/, "")
      .replace(/\|\s*$/, "")
      .split("|")
      .map((cell) => cell.trim());

  const head = cells(rows[0]).map((cell) => "<th>" + inline(cell) + "</th>");
  const body = rows
    .slice(2)
    .map(
      (row) =>
        "<tr>" +
        cells(row)
          .map((cell) => "<td>" + inline(cell) + "</td>")
          .join("") +
        "</tr>",
    );
  return (
    "<table><thead><tr>" +
    head.join("") +
    "</tr></thead><tbody>" +
    body.join("") +
    "</tbody></table>"
  );
}

export function renderMarkdown(source) {
  const lines = String(source == null ? "" : source)
    .replace(/\r\n?/g, "\n")
    .split("\n");
  const out = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    // Fenced code. An unterminated fence still renders -- while streaming we
    // are constantly looking at a half-finished block.
    const fence = line.match(/^\s*(```|~~~)(.*)$/);
    if (fence) {
      const marker = fence[1];
      const lang = fence[2].trim().split(/\s+/)[0] || "";
      const buffer = [];
      i += 1;
      while (i < lines.length && !lines[i].trimStart().startsWith(marker)) {
        buffer.push(lines[i]);
        i += 1;
      }
      i += 1; // consume the closing fence, if there was one
      const cls = lang ? ' class="language-' + escapeHtml(lang) + '"' : "";
      out.push(
        "<pre><code" + cls + ">" + escapeHtml(buffer.join("\n")) + "</code></pre>",
      );
      continue;
    }

    if (!line.trim()) {
      i += 1;
      continue;
    }

    if (/^\s*(?:---+|\*\*\*+|___+)\s*$/.test(line)) {
      out.push("<hr />");
      i += 1;
      continue;
    }

    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      const level = Math.min(heading[1].length, 6);
      out.push("<h" + level + ">" + inline(heading[2].trim()) + "</h" + level + ">");
      i += 1;
      continue;
    }

    // Table: a header row followed by a separator row of dashes.
    if (
      line.includes("|") &&
      i + 1 < lines.length &&
      /^\s*\|?[\s:|-]*-[\s:|-]*$/.test(lines[i + 1])
    ) {
      const rows = [lines[i], lines[i + 1]];
      i += 2;
      while (i < lines.length && lines[i].includes("|") && lines[i].trim()) {
        rows.push(lines[i]);
        i += 1;
      }
      out.push(renderTable(rows));
      continue;
    }

    if (/^\s*>/.test(line)) {
      const buffer = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) {
        buffer.push(lines[i].replace(/^\s*>\s?/, ""));
        i += 1;
      }
      out.push("<blockquote>" + renderMarkdown(buffer.join("\n")) + "</blockquote>");
      continue;
    }

    const bullet = /^\s*[-*+]\s+/;
    const numbered = /^\s*\d+[.)]\s+/;
    if (bullet.test(line) || numbered.test(line)) {
      const ordered = !bullet.test(line);
      const pattern = ordered ? numbered : bullet;
      const items = [];
      while (i < lines.length && pattern.test(lines[i])) {
        const buffer = [lines[i].replace(pattern, "")];
        i += 1;
        // Continuation lines: indented, and not the start of the next item.
        while (
          i < lines.length &&
          lines[i].trim() &&
          /^\s{2,}/.test(lines[i]) &&
          !pattern.test(lines[i])
        ) {
          buffer.push(lines[i].trim());
          i += 1;
        }
        items.push("<li>" + inline(buffer.join(" ")) + "</li>");
      }
      const tag = ordered ? "ol" : "ul";
      out.push("<" + tag + ">" + items.join("") + "</" + tag + ">");
      continue;
    }

    // Paragraph: runs to the next blank line or block-level marker.
    const buffer = [];
    while (
      i < lines.length &&
      lines[i].trim() &&
      !/^\s*(?:```|~~~|#{1,6}\s|>|[-*+]\s|\d+[.)]\s)/.test(lines[i])
    ) {
      buffer.push(lines[i]);
      i += 1;
    }
    if (buffer.length) {
      out.push("<p>" + inline(buffer.join("\n")).replace(/\n/g, "<br />") + "</p>");
    } else {
      i += 1;
    }
  }

  return out.join("");
}
