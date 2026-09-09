import { useCallback, useEffect, useState } from "react";

import { ApiError } from "../lib/api";

/**
 * The skills the server has registered. Server-owned; this only mirrors it.
 *
 * Unlike the session list, nothing in the UI can change this set -- skills are
 * registered in Python at boot -- so there is no `remove`, and the refresh is
 * here for the case where the server was restarted while the page stayed open.
 */
export function useSkills(api) {
  const [skills, setSkills] = useState([]);
  // The approval switch travels with the list, because the page draws one from
  // the other: a per-skill "always" control means nothing while asking is off.
  const [askFirst, setAskFirst] = useState(false);
  // `loading` starts true so the page can say "checking" instead of flashing
  // "none registered" for the length of one request and then contradicting it.
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.listSkills();
      setSkills(data.skills || []);
      setAskFirst(Boolean(data.ask_first));
      setError(null);
    } catch (problem) {
      // Shown as written: the server's refusals are already sentences.
      setError({
          message: problem.message || String(problem),
          // An ApiError means the server answered and said no; anything else
          // means it was never reached. Same red box, different sentence.
          answered: problem instanceof ApiError,
        });
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
        setAskFirst(Boolean(data.ask_first));
        setError(null);
      } catch (problem) {
        if (live) setError({
          message: problem.message || String(problem),
          // An ApiError means the server answered and said no; anything else
          // means it was never reached. Same red box, different sentence.
          answered: problem instanceof ApiError,
        });
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
        setError({
          message: problem.message || String(problem),
          // An ApiError means the server answered and said no; anything else
          // means it was never reached. Same red box, different sentence.
          answered: problem instanceof ApiError,
        });
      } finally {
        setPending((prev) => prev.filter((n) => n !== name));
      }
    },
    [api],
  );

  const setKey = useCallback(
    async (name, key) => {
      setPending((prev) => [...prev, name]);
      try {
        await api.setSkillKey(name, key);
        setError(null);
        // Refetch rather than patch: saving a key changes `available`, and may
        // change `enabled` too when the server turns a skill off because its
        // key was cleared. The server is the one that knows.
        await refresh();
        return true;
      } catch (problem) {
        setError({
          message: problem.message || String(problem),
          // An ApiError means the server answered and said no; anything else
          // means it was never reached. Same red box, different sentence.
          answered: problem instanceof ApiError,
        });
        return false;
      } finally {
        setPending((prev) => prev.filter((n) => n !== name));
      }
    },
    [api, refresh],
  );

  /**
   * The approval switch, and the standing per-skill grants behind it.
   *
   * Applied to local state before the request rather than after: these are two
   * checkboxes, and a checkbox that waits for a round trip before it moves
   * reads as broken on a slow connection. The server's answer is authoritative
   * and replaces the guess; a failure puts the old value back.
   */
  const setApproval = useCallback(
    async (patch) => {
      const previousAsk = askFirst;
      const previousSkills = skills;
      if (patch.ask_first !== undefined) setAskFirst(patch.ask_first);
      if (patch.auto_approve) {
        setSkills((prev) =>
          prev.map((s) =>
            s.name in patch.auto_approve
              ? { ...s, auto_approve: patch.auto_approve[s.name] }
              : s,
          ),
        );
      }
      try {
        const data = await api.setApprovalSettings(patch);
        setAskFirst(Boolean(data.ask_first));
        const always = new Set(data.auto_approve || []);
        setSkills((prev) => prev.map((s) => ({ ...s, auto_approve: always.has(s.name) })));
      } catch {
        setAskFirst(previousAsk);
        setSkills(previousSkills);
      }
    },
    [api, askFirst, skills],
  );

  return {
    skills,
    loading,
    error,
    refresh,
    setEnabled,
    setKey,
    pending,
    askFirst,
    setApproval,
  };
}
