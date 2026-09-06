/**
 * The widget renderer: a preset's HTML template, filled in with a skill's data.
 *
 * The same contract as `markdown.js`, for the same reason. The templates are
 * written in this repository and reviewed; everything substituted into one
 * arrived over the wire -- a web result's title is a stranger's sentence, an
 * MCP tool's summary is whatever some other server chose to return -- so every
 * value is escaped on the way in and nothing that arrives at runtime can add a
 * tag, an attribute or a handler to a card.
 *
 * That is why this is a template language and not JSX. A card is a shape with
 * a name, agreed with the server in `app/widgets/catalog.py`; keeping it as
 * markup in one file means a new preset is HTML and CSS rather than a
 * component, and means the escaping happens in exactly one function instead of
 * being re-decided per card.
 *
 * The language is three forms and stops there:
 *
 *   {{field}}              the value, escaped
 *   {{field|url}}          the value as an href, escaped, http(s) only
 *   {{#if field}}…{{else}}…{{/if}}
 *   {{#each list}}…{{/each}}
 *
 * Inside `each`, a name is looked up on the row first and then on the card, so
 * a row's `title` shadows the card's without either having to be renamed.
 */

// Spelt with its extension, unlike every other import in this client. The
// renderer is the one file here with a security contract -- everything a skill
// sends is escaped by it -- so it is tested by `widgets.test.mjs` under plain
// `node --test`, and node resolves no extension for you.
import { PRESETS } from "./widgetPresets.js";

const TAG = /\{\{\s*([#/]?)\s*([^}]+?)\s*\}\}/g;

const ENTITIES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

/** Every character that could end an attribute or open a tag. */
export function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (ch) => ENTITIES[ch]);
}

/**
 * A link a card may carry, or "#".
 *
 * Escaping alone does not make a URL safe: `javascript:alert(1)` survives it
 * intact and still runs on click. Only the two schemes a result can legitimately
 * be reached at are allowed through, and everything else -- data:, blob:, a
 * relative path that would leave the app -- becomes a dead anchor rather than a
 * working one.
 */
export function safeUrl(value) {
  const url = String(value ?? "").trim();
  return /^https?:\/\//i.test(url) ? escapeHtml(url) : "#";
}

/**
 * Whether a field should draw its block.
 *
 * Arrays are the one departure from the language's own truthiness: `[]` is
 * true in JavaScript, and a card asking `{{#if results}}` means "are there
 * any", not "is there a list". Getting that wrong draws an empty <ul> where
 * the empty-state line should be.
 */
function truthy(value) {
  if (Array.isArray(value)) return value.length > 0;
  return Boolean(value);
}

function lookup(name, scopes) {
  for (let i = scopes.length - 1; i >= 0; i -= 1) {
    const scope = scopes[i];
    if (scope && typeof scope === "object" && name in scope) return scope[name];
  }
  return undefined;
}

/**
 * One template as a tree of nodes.
 *
 * Recursive rather than a single pass with a stack of open blocks, because
 * `if` inside `each` is the common case -- a row with an optional second line
 * -- and a flat scan has to reconstruct that nesting anyway.
 */
function parse(template) {
  // Throws on an unbalanced block. That is a typo in this repository rather
  // than something a card can arrive carrying, so it should be loud and is
  // only ever seen once -- see the parse test in `widgets.test.mjs`.
  const [nodes] = block(template, 0, null);
  return nodes;
}

function block(template, from, until) {
  const nodes = [];
  let cursor = from;
  TAG.lastIndex = from;

  let match;
  while ((match = TAG.exec(template))) {
    const [tag, mark, body] = match;
    if (match.index > cursor) {
      nodes.push({ type: "text", value: template.slice(cursor, match.index) });
    }
    cursor = match.index + tag.length;

    if (mark === "/") {
      if (until === null) throw new Error(`widget template closes ${body} that never opened`);
      if (body !== until) throw new Error(`widget template closes ${body} inside ${until}`);
      return [nodes, cursor];
    }

    if (mark === "#") {
      const [kind, path] = body.split(/\s+/);
      const [inner, after] = block(template, cursor, kind);
      let otherwise = [];
      let end = after;
      // `block` stops at the closing tag; an `{{else}}` inside it comes back
      // as a text-less marker node, so the split happens here rather than in
      // the scanner.
      const split = inner.findIndex((node) => node.type === "else");
      if (split >= 0) {
        otherwise = inner.slice(split + 1);
        inner.length = split;
      }
      nodes.push({ type: kind, path, body: inner, otherwise });
      cursor = end;
      TAG.lastIndex = cursor;
      continue;
    }

    if (body === "else") {
      nodes.push({ type: "else" });
      continue;
    }

    const [path, filter] = body.split("|");
    nodes.push({ type: "var", path: path.trim(), filter: (filter || "").trim() });
  }

  if (until) throw new Error(`widget template never closes ${until}`);
  if (cursor < template.length) nodes.push({ type: "text", value: template.slice(cursor) });
  return [nodes, template.length];
}

function render(nodes, scopes) {
  let out = "";
  for (const node of nodes) {
    if (node.type === "text") {
      out += node.value;
    } else if (node.type === "var") {
      const value = lookup(node.path, scopes);
      if (node.filter === "url") out += safeUrl(value);
      else if (value !== undefined && value !== null) out += escapeHtml(value);
    } else if (node.type === "if") {
      const branch = truthy(lookup(node.path, scopes)) ? node.body : node.otherwise;
      out += render(branch, scopes);
    } else if (node.type === "each") {
      const rows = lookup(node.path, scopes);
      if (Array.isArray(rows)) {
        for (const row of rows) out += render(node.body, [...scopes, row]);
      }
    }
  }
  return out;
}

// Parsed once per preset, not once per card. A conversation that searched the
// web four times draws four cards from the same template, and the parse is the
// only expensive part of drawing one.
const compiled = new Map();

function templateFor(kind) {
  if (!compiled.has(kind)) {
    const preset = PRESETS[kind];
    compiled.set(kind, preset ? parse(preset.template) : null);
  }
  return compiled.get(kind);
}

/**
 * One card's inner HTML, or null when there is no preset for its kind.
 *
 * Null rather than a placeholder: an unknown kind means this client is older
 * than the server that sent the card, and the caller already has something
 * honest to fall back to -- the skill's own result, in the trace it would have
 * shown before widgets existed.
 */
export function renderWidget(widget) {
  if (!widget || typeof widget.kind !== "string") return null;
  const nodes = templateFor(widget.kind);
  if (!nodes) return null;
  return render(nodes, [widget.data || {}]);
}

/** The label drawn above a card, as in the reference boards. */
export function labelFor(widget) {
  return PRESETS[widget?.kind]?.label || "Skill";
}
