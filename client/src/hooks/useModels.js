import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "../lib/api";
import { serverOrigin } from "../lib/serverOrigin";

// How often the sign-in is checked on, and for how long. The flow expires on
// the server after fifteen minutes; this gives up a little sooner, because a
// tab left open for ten minutes is a tab that was abandoned.
const POLL_MS = 2500;
const POLL_LIMIT = 240;

/**
 * The three backends, what each is pointed at, and how to change either.
 *
 * Everything here is server state. The model a provider answers with is not a
 * per-device preference -- the phone and the laptop are talking to the same
 * assistant, and the two off-path passes (titling, memory curation) use it
 * too, with no request of their own to carry a choice on. So this hook mirrors
 * the server rather than owning anything, exactly as `useSkills` does.
 *
 * Which *provider* answers is the one thing that stays in the browser: it is a
 * per-turn field on /api/chat, it is where the existing menu already kept it,
 * and "answer this one on the local model" should not follow you to another
 * device.
 */
export function useModels(api) {
  const [providers, setProviders] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  // The sign-in in flight: {state, status, error}. Null when none is running.
  const [signIn, setSignIn] = useState(null);
  const polling = useRef(0);

  const load = useCallback(async () => {
    try {
      const data = await api.listModels();
      setProviders(data.providers || []);
      setError(null);
      return data.providers || [];
    } catch (problem) {
      setError({
        message: problem.message || String(problem),
        answered: problem instanceof ApiError,
      });
      return [];
    } finally {
      setLoading(false);
    }
  }, [api]);

  // Fetched once per api identity, and again whenever something here changes
  // it. StrictMode's double mount is two identical GETs landing on the same
  // answer, which is why this needs no cancellation flag: unlike a list the UI
  // can edit, there is no in-flight state for a discarded run to overwrite.
  useEffect(() => {
    load();
  }, [load]);

  // Stop the poll when the page holding it goes away. A sign-in that finishes
  // after that is not lost -- the key is written by the server either way, and
  // the next /models call sees it.
  useEffect(() => () => clearInterval(polling.current), []);

  const patch = useCallback((id, fields) => {
    setProviders((current) =>
      current.map((p) => (p.id === id ? { ...p, ...fields } : p)),
    );
  }, []);

  /** Point one backend at a different model. */
  const choose = useCallback(
    async (providerId, model) => {
      // Optimistic: the menu closes on the click, and a name that appears a
      // beat later reads as a click that did not register.
      patch(providerId, { model });
      try {
        const saved = await api.setProviderModel(providerId, model);
        patch(providerId, { model: saved.model, thinking: saved.thinking });
      } catch (problem) {
        await load(); // put the real answer back
        throw problem;
      }
    },
    [api, load, patch],
  );

  /** Paste a key. Empty disconnects. */
  const setKey = useCallback(
    async (key) => {
      const state = await api.setOpenRouterKey(key);
      patch("openrouter", state);
      // A new key sees a different catalogue -- BYOK models appear, free-tier
      // limits change -- so the list is fetched again rather than kept.
      await load();
    },
    [api, load, patch],
  );

  /**
   * Sign in at openrouter.ai, in a tab this page opens.
   *
   * Opened straight from the click that asked for it. A popup opened after an
   * `await` has lost the user gesture that permits it, and the browser blocks
   * it -- so the tab goes up first, empty, and is pointed at the URL once the
   * server has minted one. That is also why this cannot be done by the server:
   * it has no browser, and on the setup this is built for it is usually not
   * even on the device being used.
   */
  const startSignIn = useCallback(async () => {
    // No `noopener`: the tab that comes back needs `window.opener` to close
    // itself, and that is the whole of what the reference is used for here.
    const tab = window.open("", "_blank");
    clearInterval(polling.current);
    setSignIn({ state: "", status: "starting", error: "" });
    let started;
    try {
      started = await api.startOpenRouterSignIn(serverOrigin() || window.location.origin);
    } catch (problem) {
      tab?.close();
      setSignIn({ state: "", status: "failed", error: problem.message || String(problem) });
      return;
    }
    if (tab) tab.location = started.url;
    // No popup permission: the link is still the whole flow, so it is handed
    // back for the page to render as one rather than dropped.
    setSignIn({ state: started.state, status: "pending", error: "", url: started.url });

    let ticks = 0;
    polling.current = setInterval(async () => {
      ticks += 1;
      if (ticks > POLL_LIMIT) {
        clearInterval(polling.current);
        setSignIn((was) =>
          was && was.status === "pending"
            ? { ...was, status: "failed", error: "the sign-in timed out" }
            : was,
        );
        return;
      }
      let report;
      try {
        report = await api.openRouterSignInStatus(started.state);
      } catch {
        return; // a blip in the poll is not a failed sign-in
      }
      if (report.status === "pending") return;
      clearInterval(polling.current);
      setSignIn({ state: started.state, status: report.status, error: report.error || "" });
      if (report.status === "connected") await load();
    }, POLL_MS);
  }, [api, load]);

  const dismissSignIn = useCallback(() => {
    clearInterval(polling.current);
    setSignIn(null);
  }, []);

  return {
    providers,
    loading,
    error,
    refresh: load,
    choose,
    setKey,
    signIn,
    startSignIn,
    dismissSignIn,
  };
}
