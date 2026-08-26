import { useCallback, useEffect, useState } from "react";

/**
 * The skills the server has registered. Server-owned; this only mirrors it.
 *
 * Unlike the session list, nothing in the UI can change this set -- skills are
 * registered in Python at boot -- so there is no `remove`, and the refresh is
 * here for the case where the server was restarted while the page stayed open.
 */
export function useSkills(api) {
  const [skills, setSkills] = useState([]);
  // `loading` starts true so the page can say "checking" instead of flashing
  // "none registered" for the length of one request and then contradicting it.
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.listSkills();
      setSkills(data.skills || []);
      setError(null);
    } catch (problem) {
      // Shown as written: the server's refusals are already sentences.
      setError(problem.message || String(problem));
    } finally {
      setLoading(false);
    }
  }, [api]);

  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const data = await api.listSkills();
        if (!live) return;
        setSkills(data.skills || []);
        setError(null);
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
  }, [api]);

  // Names with a PATCH in flight. A list rather than a boolean so two quick
  // toggles on different rows do not disable each other's switch.
  const [pending, setPending] = useState([]);

  const setEnabled = useCallback(
    async (name, enabled) => {
      // Optimistic: the switch moves under the finger, and goes back if the
      // server disagrees. A round trip before the control responds reads as a
      // broken button on anything slower than loopback.
      setSkills((prev) =>
        prev.map((s) => (s.name === name ? { ...s, enabled } : s)),
      );
      setPending((prev) => [...prev, name]);
      try {
        await api.setSkillEnabled(name, enabled);
        setError(null);
      } catch (problem) {
        setSkills((prev) =>
          prev.map((s) => (s.name === name ? { ...s, enabled: !enabled } : s)),
        );
        setError(problem.message || String(problem));
      } finally {
        setPending((prev) => prev.filter((n) => n !== name));
      }
    },
    [api],
  );

  return { skills, loading, error, refresh, setEnabled, pending };
}
