import { useCallback, useEffect, useMemo, useState } from "react";

const pad = (n) => String(n).padStart(2, "0");

/** One date as YYYY-MM-DD, in local time -- the form the server stores. */
export function iso(d) {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/**
 * Six weeks of cells covering the month, Monday-first.
 *
 * Always six rows, never five or seven: a grid that changes height as you page
 * through the year makes everything below it jump, and the empty row costs one
 * line of whitespace.
 *
 * Lives here rather than beside the grid that draws it because the fetch range
 * is derived from it -- see gridRange. Two definitions of "which days are on
 * screen" is exactly the bug this pairing exists to prevent.
 */
export function gridFor(cursor) {
  const first = new Date(cursor.getFullYear(), cursor.getMonth(), 1);
  // getDay() is Sunday-first; shift so Monday is 0.
  const lead = (first.getDay() + 6) % 7;
  const start = new Date(first);
  start.setDate(1 - lead);
  return Array.from({ length: 42 }, (_, i) => {
    const d = new Date(start);
    d.setDate(start.getDate() + i);
    return d;
  });
}

/**
 * The half-open range the grid actually shows.
 *
 * Not the month. The grid is six weeks, so paging to August draws the last
 * days of July and the first days of September in the same view -- and asking
 * the server for the month alone left every one of those cells permanently
 * empty. An event the assistant had just added would be in the database, on
 * screen as a date, and invisible, which reads as a skill that silently did
 * nothing.
 */
export function gridRange(cursor) {
  const cells = gridFor(cursor);
  const end = new Date(cells[cells.length - 1]);
  // `until` is exclusive, so it has to clear the last day rather than land on
  // it -- otherwise everything on that day is dropped.
  end.setDate(end.getDate() + 1);
  return { since: iso(cells[0]), until: iso(end) };
}

/**
 * The events the grid can display. Server-owned; this only mirrors it.
 *
 * Refetches when the month changes, and after any write -- the model can add
 * an event through a skill while this page is open, so the client's copy is
 * never authoritative and there is no point trying to patch it locally.
 */
export function useCalendar(api, cursor) {
  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const { since, until } = useMemo(() => gridRange(cursor), [cursor]);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.listEvents(since, until);
      setEvents(data.events || []);
      setError(null);
    } catch (problem) {
      setError(problem.message || String(problem));
    } finally {
      setLoading(false);
    }
  }, [api, since, until]);

  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const data = await api.listEvents(since, until);
        if (live) {
          setEvents(data.events || []);
          setError(null);
        }
      } catch (problem) {
        if (live) setError(problem.message || String(problem));
      } finally {
        if (live) setLoading(false);
      }
    })();
    return () => {
      live = false;
    };
  }, [api, since, until]);

  const add = useCallback(
    async (event) => {
      await api.createEvent(event);
      await refresh();
    },
    [api, refresh],
  );

  const remove = useCallback(
    async (id) => {
      await api.deleteEvent(id);
      await refresh();
    },
    [api, refresh],
  );

  return { events, loading, error, refresh, add, remove };
}
