import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { seedFromContext } from "../lib/autotheme";
import { DEFAULT_ACCENT, applyPalette, palette, seedOf } from "../lib/theme";

/**
 * The accent the app is currently wearing, and the three places it can be set.
 *
 * Three scopes, nearest wins: this conversation, then the project it is filed
 * under, then the app. A scope with nothing set is not a scope that chose
 * nothing -- it is one that has not chosen, and the decision falls through to
 * the next. That is the difference between clearing an accent and setting it
 * to "off", and it is the only subtle thing in this file.
 *
 * The resolved palette is written onto <html>. Not onto the `.app` div, which
 * would be the tidier-looking choice: the dialog and the token gate render
 * outside it, and a themed app with an unthemed modal on top of it is worse
 * than either.
 */
export function useTheme({ api, sessionId, sessions, projects, title, messages }) {
  const [appAccent, setAppAccent] = useState(null);
  const [loaded, setLoaded] = useState(false);
  // Local echo of what the server holds for a chat or a folder, so a swatch
  // lights up on the click rather than a round trip later. The lists are
  // re-read anyway; this only covers the gap.
  const [sessionOverrides, setSessionOverrides] = useState({});
  const [projectOverrides, setProjectOverrides] = useState({});

  useEffect(() => {
    let live = true;
    api
      .getAppTheme()
      .then((data) => {
        if (!live) return;
        setAppAccent(data.theme || null);
        setLoaded(true);
      })
      // An accent that cannot be fetched is not worth a visible failure. The
      // app has a perfectly good palette without one.
      .catch(() => live && setLoaded(true));
    return () => {
      live = false;
    };
  }, [api]);

  const session = useMemo(
    () => sessions.find((s) => s.id === sessionId) || null,
    [sessions, sessionId],
  );
  const project = useMemo(
    () => projects.find((p) => p.id === session?.project_id) || null,
    [projects, session],
  );

  const sessionAccent =
    (sessionId && sessionOverrides[sessionId] !== undefined
      ? sessionOverrides[sessionId]
      : session?.theme) || null;
  const projectAccent =
    (project && projectOverrides[project.id] !== undefined
      ? projectOverrides[project.id]
      : project?.theme) || null;

  // Nearest scope with an opinion, and the app's own default if none has one.
  const active = sessionAccent || projectAccent || appAccent || DEFAULT_ACCENT;

  /* The auto seed, remembered per conversation.
   *
   * A ref rather than state: it feeds back into its own derivation as the
   * previous value -- that is what keeps a chat from changing colour every
   * time a new word lands -- and a state update for that would re-render for a
   * value nothing renders. */
  const seeds = useRef(new Map());

  /* How often the conversation is re-read.
   *
   * `messages` is a new array on every frame of a streaming answer -- the
   * deltas are coalesced per frame, but the array identity still changes --
   * and re-scanning twenty thousand characters sixty times a second to
   * discover the same hue is not a thing to do while an answer is arriving.
   *
   * So the memo hangs off a signature instead: the number of turns, and the
   * length of the last one rounded down to the nearest 1500 characters. The
   * colour still drifts while a long answer comes in, roughly once a
   * paragraph, and the scan happens a handful of times per turn rather than
   * hundreds. */
  const signature =
    messages.length + ":" + Math.floor((messages.at(-1)?.content?.length || 0) / 1500);

  const contextSeed = useMemo(() => {
    const key = sessionId || "new";
    const derived = seedFromContext(
      { id: key, title, messages },
      seeds.current.get(key) || null,
    );
    seeds.current.set(key, derived);
    return derived;
    // `messages` is read but deliberately not depended on -- `signature` is
    // the throttled stand-in for it, and listing both would defeat the point.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, title, signature]);

  const tokens = useMemo(() => palette(active, contextSeed), [active, contextSeed]);

  useEffect(() => {
    // Nothing at all until the app accent has been read: applying the default
    // first and the stored accent a moment later is a visible flash of the
    // wrong colour on every launch.
    if (!loaded) return;
    applyPalette(document.documentElement, tokens);
  }, [tokens, loaded]);

  // -- setting it -----------------------------------------------------------

  const setApp = useCallback(
    async (accent) => {
      setAppAccent(accent);
      await api.setAppTheme(accent).catch(() => {});
    },
    [api],
  );

  const setForSession = useCallback(
    async (id, accent) => {
      if (!id) return;
      setSessionOverrides((was) => ({ ...was, [id]: accent }));
      await api.setSessionTheme(id, accent).catch(() => {});
    },
    [api],
  );

  const setForProject = useCallback(
    async (id, accent) => {
      if (!id) return;
      setProjectOverrides((was) => ({ ...was, [id]: accent }));
      await api.setProjectTheme(id, accent).catch(() => {});
    },
    [api],
  );

  /** The accent a row in a list is wearing, for its swatch. */
  const accentFor = useCallback(
    (record) =>
      (record && (sessionOverrides[record.id] ?? projectOverrides[record.id])) ??
      record?.theme ??
      null,
    [sessionOverrides, projectOverrides],
  );

  /* What an auto accent resolves to for a row we have not opened.
   *
   * The title only -- a session list carries no message bodies, and fetching
   * every conversation to colour a dot in the rail would be absurd. It is the
   * same function the open chat uses, so a conversation whose title already
   * names its subject wears the same colour in the list as it does on screen. */
  const seedFor = useCallback(
    (record) => seedFromContext({ id: record?.id || "", title: record?.title || "" }),
    [],
  );

  return {
    /** The accent in force, after the three scopes have been resolved. */
    active,
    /** Its hue and chroma, or null when it is off. */
    seed: seedOf(active, contextSeed),
    /* What an auto accent resolves to right now, whether or not auto is in
       force. The auto swatch draws itself in this, so it previews the colour
       it would actually set rather than the one already on screen. */
    contextSeed,
    appAccent,
    sessionAccent,
    projectAccent,
    /** Which of the three scopes the active accent actually came from. */
    source: sessionAccent ? "chat" : projectAccent ? "project" : "app",
    setApp,
    setForSession,
    setForProject,
    accentFor,
    seedFor,
  };
}
