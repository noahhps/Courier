import { memo, useMemo, useState } from "react";

import { labelFor, renderWidget } from "../lib/widgets";
import { SkillTrace } from "./SkillTrace";

/**
 * What the skills in a turn produced, as cards.
 *
 * The skill is the trigger. Nothing here is scheduled, subscribed or polled --
 * a card exists because the model reached for something while answering, which
 * is also why a board belongs to a conversation rather than to the app: two
 * chats ask for different things and end up looking different.
 *
 * A turn's skills split in two, and the split is a fact about the skill rather
 * than a choice made here:
 *
 * * the ones that drew a card are the board, above the answer;
 * * the ones that did not -- and the ones still running, which have no result
 *   yet and so cannot have drawn anything -- stay in the trace they have
 *   always been in. That is deliberate: a running skill is the one state the
 *   trace renders better than a card could, because a half-drawn card is
 *   indistinguishable from a wrong one.
 *
 * So a slow skill appears as a trace row saying it is running, and becomes a
 * card the moment its result lands.
 */

function Card({ skill, html }) {
  const [open, setOpen] = useState(false);

  return (
    <article className="widget" data-kind={skill.widget.kind}>
      <div className="widget-label">{labelFor(skill.widget)}</div>
      <div className="widget-card">
        {/* The template is written in this repository and every value
            substituted into it is escaped on the way in -- see lib/widgets.js.
            That is the whole contract, and it is the same one markdown.js
            keeps. */}
        <div className="widget-body" dangerouslySetInnerHTML={{ __html: html }} />
        <button
          type="button"
          className="widget-open"
          aria-expanded={open}
          onClick={() => setOpen((was) => !was)}
        >
          {open ? "Hide result" : skill.name}
        </button>
        {/* What the model was actually given. The card is a reading of it, and
            anyone who wants to check the reading should not have to reopen the
            conversation somewhere else to do it. Inside the tile, so a card
            being checked still looks like one card. */}
        {open ? <pre className="widget-result">{skill.result}</pre> : null}
      </div>
    </article>
  );
}

export const WidgetBoard = memo(function WidgetBoard({ skills }) {
  // Drawn once per change rather than per render: a card's markup is the same
  // string every time, and this component re-renders on every frame of the
  // answer being typed underneath it.
  //
  // The split is made on what actually drew, not on what claims a widget. A
  // kind this client has no template for renders null -- an older client
  // against a newer server -- and belongs in the trace with the rest rather
  // than as an empty frame holding a column of the board open.
  const [drawn, rest] = useMemo(() => {
    const cards = [];
    const others = [];
    for (const skill of skills || []) {
      const html = skill.widget ? renderWidget(skill.widget) : null;
      if (html === null) others.push(skill);
      else cards.push({ skill, html });
    }
    return [cards, others];
  }, [skills]);

  if (!skills?.length) return null;

  return (
    <>
      {drawn.length ? (
        <div className="widget-board">
          {drawn.map(({ skill, html }, index) => (
            <Card key={`${skill.name}-${index}`} skill={skill} html={html} />
          ))}
        </div>
      ) : null}
      <SkillTrace skills={rest} />
    </>
  );
});
