import { useCallback, useEffect, useMemo, useState } from "react";

/** First of the month, and first of the next one. */
export function monthRange(cursor) {
  const pad = (n) => String(n).padStart(2, "0");
  const y = cursor.getFullYear();
  const m = cursor.getMonth();
  const next = new Date(y, m + 1, 1);
  return {
    since: `${y}-${pad(m + 1)}-01`,
    // Exclusive, so no arithmetic about how long the month is.
    until: `${next.getFullYear()}-${pad(next.getMonth() + 1)}-01`,
  };
}

/**
 * The events in one month. Server-owned; this only mirrors it.
 *
 * Refetches when the month changes, and after any write -- the model can add
 * an event through a skill while this page is open, so the client's copy is
 * never authoritative and there is no point trying to patch it locally.
 */
export function useCalendar(api, cursor) {
  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const { since, until } = useMemo(() => monthRange(cursor), [cursor]);

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
