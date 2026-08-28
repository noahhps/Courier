import { useCallback, useEffect, useState } from "react";

/**
 * What the server remembers, and the switches governing it.
 *
 * Modelled on useSkills: `loading` starts true so the page can say "checking"
 * rather than flashing "nothing remembered" and then contradicting itself, and
 * every mutation is optimistic with a rollback -- a Forget button that waits
 * for a round trip reads as broken on anything slower than loopback.
 *
 * The server speaks storage: `source`, `created_at`, `used_count`. The mapping
 * to what the page shows ("You told me", "4 Jul", "12 answers") happens here,
 * because those are rendering decisions, they are locale-dependent, and the
 * export wants the raw numbers.
 */

const EMPTY = { facts: [], settings: {}, corpora: [], index: null };

/** "2 min ago", "4 Jul", "4 Jul 2025" -- shortest form that stays unambiguous. */
export function when(timestamp) {
  if (!timestamp) return "";
  const date = new Date(timestamp);
  const seconds = Math.round((Date.now() - timestamp) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  const sameYear = date.getFullYear() === new Date().getFullYear();
  return date.toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    ...(sameYear ? {} : { year: "numeric" }),
  });
}

function toFact(row) {
  return {
    ...row,
    from: row.source,
    when: when(row.created_at),
    used: row.used_count ? `${row.used_count} answer${row.used_count === 1 ? "" : "s"}` : "",
    // The "Looks right?" affordance: a guess the model was not confident
    // about, or one being held back until someone agrees with it.
    check: row.status === "pending" || (row.source === "inferred" && row.confidence < 0.7),
  };
}

export function useMemory(api) {
  const [data, setData] = useState(EMPTY);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState([]);

  const load = useCallback(async () => {
    const fresh = await api.getMemory();
    setData({
      facts: (fresh.facts || []).map(toFact),
      settings: fresh.settings || {},
      corpora: fresh.corpora || [],
      index: fresh.index || null,
    });
    setError(null);
  }, [api]);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      await load();
    } catch (problem) {
      setError(problem.message || String(problem));
    } finally {
      setLoading(false);
    }
  }, [load]);

  useEffect(() => {
    let live = true;
    (async () => {
      try {
        await load();
      } catch (problem) {
        if (live) setError(problem.message || String(problem));
      } finally {
        if (live) setLoading(false);
      }
    })();
    // StrictMode mounts twice in development; the flag stops the first,
    // discarded run from writing its answer over the second one's.
    return () => {
      live = false;
    };
  }, [load]);

  /** Optimistic mutation: move the UI, call the server, put it back if it says no. */
  const apply = useCallback(
    async (id, optimistic, call) => {
      const before = data.facts;
      setData((prev) => ({ ...prev, facts: optimistic(prev.facts) }));
      setBusy((prev) => [...prev, id]);
      try {
        await call();
        setError(null);
        // Re-read rather than trust the local guess: used_count, updated_at
        // and the fading flag are all the server's to compute.
        await load();
      } catch (problem) {
        setData((prev) => ({ ...prev, facts: before }));
        setError(problem.message || String(problem));
      } finally {
        setBusy((prev) => prev.filter((key) => key !== id));
      }
    },
    [data.facts, load],
  );

  const editFact = useCallback(
    (id, patch) =>
      apply(
        id,
        (facts) => facts.map((f) => (f.id === id ? toFact({ ...f, ...patch }) : f)),
        () => api.editFact(id, patch),
      ),
    [api, apply],
  );

  const forgetFact = useCallback(
    (id) =>
      apply(
        id,
        (facts) => facts.filter((f) => f.id !== id),
        () => api.forgetFact(id),
      ),
    [api, apply],
  );

  const addFact = useCallback(
    async (text, category = null) => {
      try {
        await api.addFact(text, category);
        await load();
      } catch (problem) {
        setError(problem.message || String(problem));
      }
    },
    [api, load],
  );

  const forgetAll = useCallback(async () => {
    try {
      await api.forgetAllFacts();
      await load();
    } catch (problem) {
      setError(problem.message || String(problem));
    }
  }, [api, load]);

  const setSetting = useCallback(
    async (name, on) => {
      const before = data.settings;
      setData((prev) => ({ ...prev, settings: { ...prev.settings, [name]: on } }));
      try {
        const fresh = await api.setMemorySettings({ [name]: on });
        setData((prev) => ({ ...prev, settings: fresh }));
        setError(null);
      } catch (problem) {
        setData((prev) => ({ ...prev, settings: before }));
        setError(problem.message || String(problem));
      }
    },
    [api, data.settings],
  );

  const reindex = useCallback(async () => {
    try {
      await api.reindexMemory();
      await load();
    } catch (problem) {
      setError(problem.message || String(problem));
    }
  }, [api, load]);

  return {
    ...data,
    loading,
    error,
    busy,
    refresh,
    addFact,
    editFact,
    forgetFact,
    forgetAll,
    setSetting,
    reindex,
  };
}
