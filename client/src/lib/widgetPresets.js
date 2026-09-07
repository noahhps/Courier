/**
 * The cards themselves: one label and one HTML template per kind.
 *
 * This file is one half of a contract whose other half is
 * `server/app/widgets/catalog.py`. The server decides which fields a card may
 * carry and refuses anything else; this decides what those fields look like.
 * Adding a preset means adding it in both places and in `docs/widgets.md`,
 * and the fields used below must be the ones the catalogue declares -- a name
 * that exists here and not there simply never draws.
 *
 * Written as markup rather than as components on purpose. A card is a fixed
 * shape agreed between two processes, so it is easier to review, restyle and
 * extend as fifteen lines of HTML next to its CSS than as a React tree; and
 * keeping every card in one language means the escaping is decided once, in
 * `widgets.js`, rather than per component.
 *
 * Two rules for anything written here:
 *
 * * every value is `{{interpolated}}`, never concatenated into an attribute by
 *   hand -- the renderer escapes what it substitutes and nothing else;
 * * an href is `{{field|url}}`. Escaping a `javascript:` URL leaves it working.
 *
 * The composition is the one in the reference boards: a quiet label, then a
 * card whose first line is the thing you came to read at the size you can read
 * it from across the desk, then the detail underneath. Rows follow the same
 * boards' lists -- the thing on the left, its time or its host right-aligned
 * on that line, and a grey second line under both.
 *
 * Labels are sentence case and name the card rather than the skill: "Up next"
 * over a calendar, because that is what you are looking at, and the skill that
 * produced it is already written along the bottom of the tile.
 */

// The head of every card that has a list under it: what was asked, and how
// much came back. Repeated as a string rather than factored into the renderer,
// because a preset should be readable top to bottom as the markup it produces.
const head = (title, sub) => `
<div class="widget-head">
  <span class="widget-title">{{${title}}}</span>
  {{#if ${sub}}}<span class="widget-count">{{${sub}}}</span>{{/if}}
</div>`;

const more = `{{#if more}}<p class="widget-more">and {{more}} more</p>{{/if}}`;

export const PRESETS = {
  clock: {
    label: "Time",
    template: `
<div class="widget-hero">
  <span class="widget-figure">{{time}}</span>
  {{#if zone}}<span class="widget-unit">{{zone}}</span>{{/if}}
</div>
<p class="widget-line">{{date}}</p>
{{#if note}}<p class="widget-sub">{{note}}</p>{{/if}}`,
  },

  agenda: {
    label: "Up next",
    template: `${head("title", "subtitle")}
{{#if events}}
<ul class="widget-rows">
  {{#each events}}
  <li class="widget-row">
    <div class="widget-row-top">
      <span class="widget-row-main">{{title}}</span>
      {{#if when}}<span class="widget-side">{{when}}</span>{{/if}}
    </div>
    {{#if detail}}<span class="widget-row-sub">{{detail}}</span>{{/if}}
  </li>
  {{/each}}
</ul>
{{else}}<p class="widget-empty">{{empty}}</p>{{/if}}
${more}`,
  },

  // The host reads as part of the grey line under the headline, the way the
  // boards write "AI infrastructure · Reuters" -- not opposite a title that
  // may be running to three lines beside it. Two things about that line:
  //
  // * the separator is nested inside both conditions, so a result with no
  //   snippet gets no dangling dot;
  // * it is written on one line with no space between the blocks, because
  //   whether it has any content at all depends on two fields and `:empty`
  //   -- which hides it when it has none -- does not count whitespace as
  //   nothing. The language has no comment form to explain that in place,
  //   which is why this is out here.
  sources: {
    label: "News",
    template: `${head("query", "subtitle")}
{{#if results}}
<ul class="widget-rows">
  {{#each results}}
  <li class="widget-row">
    <div class="widget-row-top">
      {{#if url}}
      <a class="widget-row-main" href="{{url|url}}" target="_blank" rel="noreferrer noopener">{{title}}</a>
      {{else}}<span class="widget-row-main">{{title}}</span>{{/if}}
    </div>
    <span class="widget-row-sub">{{#if domain}}<span class="widget-source">{{domain}}</span>{{/if}}{{#if snippet}}{{#if domain}}<span class="widget-dot">·</span>{{/if}}{{snippet}}{{/if}}</span>
  </li>
  {{/each}}
</ul>
{{else}}<p class="widget-empty">{{empty}}</p>{{/if}}
${more}`,
  },

  recall: {
    label: "History",
    template: `${head("query", "subtitle")}
{{#if passages}}
<ul class="widget-rows">
  {{#each passages}}
  <li class="widget-row">
    <span class="widget-row-main" data-quiet>{{text}}</span>
    {{#if source}}<span class="widget-row-sub">{{source}}</span>{{/if}}
  </li>
  {{/each}}
</ul>
{{else}}<p class="widget-empty">{{empty}}</p>{{/if}}
${more}`,
  },

  fact: {
    label: "Memory",
    template: `
<div class="widget-head">
  <span class="widget-badge">{{action}}</span>
  {{#if category}}<span class="widget-count">{{category}}</span>{{/if}}
</div>
<p class="widget-quote">{{text}}</p>`,
  },

  files: {
    label: "Files",
    template: `${head("path", "subtitle")}
{{#if entries}}
<ul class="widget-rows" data-tight>
  {{#each entries}}
  <li class="widget-row" data-kind="{{kind}}">
    <div class="widget-row-top">
      <span class="widget-row-main">{{name}}</span>
      {{#if size}}<span class="widget-side">{{size}}</span>{{/if}}
    </div>
  </li>
  {{/each}}
</ul>
{{else}}<p class="widget-empty">{{empty}}</p>{{/if}}
${more}`,
  },

  tool: {
    label: "Tool",
    template: `
<div class="widget-head">
  <span class="widget-state" data-state="{{state}}" aria-hidden="true"></span>
  <span class="widget-title">{{name}}</span>
  {{#if source}}<span class="widget-count">{{source}}</span>{{/if}}
</div>
{{#if summary}}<p class="widget-line" data-quiet>{{summary}}</p>{{/if}}`,
  },
};
