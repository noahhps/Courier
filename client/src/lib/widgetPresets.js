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
 * it from across the desk, then the detail underneath.
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
    label: "Calendar",
    template: `${head("title", "subtitle")}
{{#if events}}
<ul class="widget-rows">
  {{#each events}}
  <li class="widget-row">
    {{#if when}}<span class="widget-when">{{when}}</span>{{/if}}
    <span class="widget-row-main">{{title}}</span>
    {{#if detail}}<span class="widget-row-sub">{{detail}}</span>{{/if}}
  </li>
  {{/each}}
</ul>
{{else}}<p class="widget-empty">{{empty}}</p>{{/if}}
${more}`,
  },

  sources: {
    label: "Web",
    template: `${head("query", "subtitle")}
{{#if results}}
<ul class="widget-rows">
  {{#each results}}
  <li class="widget-row">
    {{#if url}}
    <a class="widget-row-main" href="{{url|url}}" target="_blank" rel="noreferrer noopener">{{title}}</a>
    {{else}}<span class="widget-row-main">{{title}}</span>{{/if}}
    {{#if domain}}<span class="widget-when">{{domain}}</span>{{/if}}
    {{#if snippet}}<span class="widget-row-sub">{{snippet}}</span>{{/if}}
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
    {{#if source}}<span class="widget-when">{{source}}</span>{{/if}}
    <span class="widget-row-main" data-quiet>{{text}}</span>
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
<ul class="widget-rows" data-tabular>
  {{#each entries}}
  <li class="widget-row" data-kind="{{kind}}">
    <span class="widget-row-main">{{name}}</span>
    {{#if size}}<span class="widget-size">{{size}}</span>{{/if}}
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
