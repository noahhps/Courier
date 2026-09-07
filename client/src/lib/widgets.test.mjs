/**
 * The renderer's contract: templates are ours, values are not.
 *
 * `npm test` from `client/`. No bundler and no browser, which is
 * why this is the one file in the client with tests -- everything else here
 * needs a DOM, and this needs a string.
 */

import assert from "node:assert/strict";
import test from "node:test";

import { escapeHtml, labelFor, renderWidget, safeUrl } from "./widgets.js";

test("a value carrying markup arrives as text, not as tags", () => {
  const html = renderWidget({
    kind: "sources",
    data: { query: "<img src=x onerror=alert(1)>", results: [] },
  });
  assert.ok(!html.includes("<img"));
  assert.ok(html.includes("&lt;img"));
});

test("a value cannot break out of an attribute", () => {
  // `data-kind` is written into the markup from a row's own field, which is
  // the only attribute in any preset fed from data.
  const html = renderWidget({
    kind: "files",
    data: { path: "/x", entries: [{ name: "a", kind: '" onmouseover="alert(1)' }] },
  });
  assert.ok(!html.includes('onmouseover="'));
  assert.ok(html.includes("&quot;"));
});

test("a link is rendered only for the two schemes it can honestly be", () => {
  assert.equal(safeUrl("https://example.org/a?b=1&c=2"), "https://example.org/a?b=1&amp;c=2");
  assert.equal(safeUrl("HTTP://example.org"), "HTTP://example.org");
  assert.equal(safeUrl("javascript:alert(1)"), "#");
  assert.equal(safeUrl("data:text/html,<script>"), "#");
  assert.equal(safeUrl(undefined), "#");
});

test("a javascript: result is drawn as a dead anchor rather than a live one", () => {
  const html = renderWidget({
    kind: "sources",
    data: { query: "x", results: [{ title: "Click", url: "javascript:alert(1)" }] },
  });
  assert.ok(html.includes('href="#"'));
  assert.ok(!html.toLowerCase().includes("javascript:"));
});

test("an empty list draws the empty line rather than an empty list", () => {
  const html = renderWidget({
    kind: "agenda",
    data: { title: "Next 7 days", empty: "Nothing on" },
  });
  assert.ok(html.includes("Nothing on"));
  assert.ok(!html.includes("<ul"));
});

test("a row's field shadows the card's field of the same name", () => {
  const html = renderWidget({
    kind: "agenda",
    data: { title: "This week", events: [{ title: "Dentist" }] },
  });
  assert.ok(html.includes("Dentist"));
  assert.ok(html.includes("This week"));
});

test("an absent optional field leaves no empty element behind", () => {
  // A clock always has a day, a time and a date; the zone and the timezone
  // name are the two that may be missing, and neither should leave a mark.
  const html = renderWidget({
    kind: "clock",
    data: { time: "14:05", day: "Friday", date: "6 September 2026" },
  });
  assert.ok(!html.includes("widget-count"));
  assert.ok(html.includes("6 September 2026"));
  // The separator before the timezone name belongs to the timezone name.
  assert.ok(!html.includes("·"));
});

test("a row's second line is dropped rather than drawn empty", () => {
  // The sources template always emits that element, because whether it has
  // anything in it depends on two fields rather than one -- so it has to come
  // out with nothing between the tags for `:empty` to hide it.
  const html = renderWidget({
    kind: "sources",
    data: { query: "x", results: [{ title: "No source, no snippet" }] },
  });
  assert.ok(html.includes('<span class="widget-row-sub"></span>'));
});

test("a news row wears a monogram of its source, and none without one", () => {
  const withSource = renderWidget({
    kind: "sources",
    data: { query: "x", results: [{ title: "A", domain: "reuters.com" }] },
  });
  assert.ok(withSource.includes(">R</span>"));
  assert.ok(/data-tone="[1-6]"/.test(withSource));

  const without = renderWidget({
    kind: "sources",
    data: { query: "x", results: [{ title: "A" }] },
  });
  assert.ok(!without.includes("widget-thumb"));
});

test("a source is the same colour every time it appears", () => {
  const tone = (domain) =>
    renderWidget({ kind: "sources", data: { query: "x", results: [{ title: "A", domain }] } })
      .match(/data-tone="(\d)"/)[1];
  assert.equal(tone("reuters.com"), tone("reuters.com"));
});

test("a kind this client has no template for renders nothing at all", () => {
  // An older client against a newer server. The caller falls back to the
  // skill's own result rather than drawing an empty card.
  assert.equal(renderWidget({ kind: "weather", data: { temp: 58 } }), null);
  assert.equal(renderWidget(null), null);
  assert.equal(renderWidget({}), null);
});

test("every preset draws from an empty card without throwing", () => {
  for (const kind of ["clock", "agenda", "sources", "recall", "fact", "files", "tool"]) {
    assert.equal(typeof renderWidget({ kind, data: {} }), "string");
    assert.ok(labelFor({ kind }).length > 0);
  }
});

test("escaping covers every character that could end an attribute or open a tag", () => {
  assert.equal(escapeHtml(`&<>"'`), "&amp;&lt;&gt;&quot;&#39;");
});
