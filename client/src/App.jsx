// UI only. Zero durable state beyond the bearer token: if this device is
// wiped, nothing is lost, because the server is the source of truth.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Composer } from "./components/Composer";
import { Memory } from "./components/Memory";
import { MessageList } from "./components/MessageList";
import { NavRail } from "./components/NavRail";
import { Settings } from "./components/Settings";
import { Skills } from "./components/Skills";
import { Starters } from "./components/Starters";
import { TokenGate } from "./components/TokenGate";
import { TopBar } from "./components/TopBar";
import { useChat } from "./hooks/useChat";
import { useRailWidth } from "./hooks/useRailWidth";
import { useSessions } from "./hooks/useSessions";
import { UnauthorizedError, createApi } from "./lib/api";
import { ApiContext } from "./lib/api-context";
import { fetchDevToken } from "./lib/dev-token";

const TOKEN_KEY = "unified-llm-token";
// Whether the rail stays out. A layout preference rather than data, so it is
// the one thing besides the token this client is allowed to remember.
const PIN_KEY = "unified-llm-rail-pinned";

// "boot" is the silent pass with a token already in storage -- the common
// case, and the one that must not flash a login screen on every launch.
// "connecting" is the same work with the gate on screen, after someone typed.
const BOOT = "boot";
const GATE = "gate";
const CONNECTING = "connecting";
const READY = "ready";

export default function App() {
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY) || "");
  // A dev run with nothing in storage still boots silently rather than showing
  // the gate: the effect below is about to fetch the token, and a gate that
  // appears for one frame and dismisses itself is worse than no gate at all.
  const [phase, setPhase] = useState(() =>
    localStorage.getItem(TOKEN_KEY) || import.meta.env.DEV ? BOOT : GATE,
  );
  const [gateError, setGateError] = useState("");
  const [focusToken, setFocusToken] = useState(0);
  // Which of the rail's three destinations is on screen.
  const [view, setView] = useState("chat");
  const rail = useRailWidth();
  // The prompt a starter put in the composer. An object, not a string, so
  // picking the same starter twice is two distinct values.
  const [draft, setDraft] = useState(null);
  const [railPinned, setRailPinned] = useState(
    () => localStorage.getItem(PIN_KEY) === "1",
  );
  const togglePin = useCallback(
    () =>
      setRailPinned((was) => {
        localStorage.setItem(PIN_KEY, was ? "0" : "1");
        return !was;
      }),
    [],
  );
  // The last /status payload. Drives the circle at the foot of the rail and
  // the settings page; the per-turn badge in the top bar is separate and more
  // current, because it reports which provider actually answered.
  const [status, setStatus] = useState(null);
  // "local" | "cloud" | null. Null lets the server's router decide, which is
  // the default and usually the right answer.
  const [provider, setProvider] = useState(null);

  const bootstrapped = useRef("");
  const signOutRef = useRef(() => {});
  // Dev-only bookkeeping: whether the token has been asked for on this page
  // load, and whether a deliberate sign-out has taken the offer off the table.
  const devTokenAsked = useRef(false);
  const devSignedOut = useRef(false);

  const signOut = useCallback((message) => {
    localStorage.removeItem(TOKEN_KEY);
    bootstrapped.current = "";
    setToken("");
    setPhase(GATE);
    setGateError(message || "");
  }, []);
  signOutRef.current = signOut;

  // One client per token. The 401 handler goes through a ref so the identity
  // stays stable: every hook below keys its callbacks off this object.
  const api = useMemo(
    () => createApi(token, () => signOutRef.current("That token was rejected.")),
    [token],
  );

  const sessions = useSessions(api);
  const { refresh } = sessions;
  const onSessionsChanged = useCallback(() => {
    refresh().catch(() => {});
  }, [refresh]);

  const chat = useChat(api, { onSessionsChanged, provider });
  const { setBadge, openSession, startNew } = chat;

  // -- development ----------------------------------------------------------

  // Editing the front end shouldn't begin by copying the token out of the
  // terminal, so in `npm run dev` the dev server hands it over and the gate is
  // skipped. Nothing is bypassed: the token is real, every call below still
  // sends it, and the server still checks it. `import.meta.env.DEV` is a
  // compile-time constant, so this whole block is absent from a build.
  useEffect(() => {
    if (!import.meta.env.DEV || token) return;
    // A sign-out from the settings page is deliberate and has to stick.
    if (devSignedOut.current) return;
    // Asked at most once per page load, so a token the server has stopped
    // accepting cannot spin between the gate and a 401. What this does still
    // recover from is the case worth recovering from: a *stored* token that
    // has gone stale, which arrives here as an empty token after the 401 and
    // gets replaced by whatever the server regenerated.
    if (devTokenAsked.current) return;
    // Set synchronously, and no captured `cancelled` flag, for the same reason
    // `bootstrapped` below does it this way: StrictMode tears the first effect
    // down immediately, and this is the only run the guard above will allow.
    devTokenAsked.current = true;

    (async () => {
      const value = await fetchDevToken();
      if (devSignedOut.current) return;
      if (!value) {
        setPhase(GATE);
        return;
      }
      setGateError("");
      setToken(value);
      // BOOT, not CONNECTING: CONNECTING renders the gate with its button
      // disabled, which is right after someone typed and wrong here -- nobody
      // typed, and the gate would appear for the length of one round trip.
      setPhase(BOOT);
    })();
  }, [token]);

  // -- bootstrap ------------------------------------------------------------

  useEffect(() => {
    if (!token || bootstrapped.current === token) return;
    bootstrapped.current = token;

    // Abandoned when the token this run belongs to is no longer the live one --
    // a sign-out mid-bootstrap must not land its results on the gate. Deliberately
    // not a captured `cancelled` flag: StrictMode tears the first effect down
    // immediately, and this run is the only one the guard above will allow.
    const stale = () => bootstrapped.current !== token;

    (async () => {
      try {
        const reported = await api.status();
        if (stale()) return;
        localStorage.setItem(TOKEN_KEY, token);
        setStatus(reported);

        if (reported.serving === "none") {
          setBadge({ text: "no model reachable", tone: "down" });
        } else if (reported.serving === "cloud") {
          setBadge({ text: "cloud · " + reported.cloud.model, tone: "warn" });
        }

        const list = await refresh();
        if (stale()) return;
        if (list.length) await openSession(list[0].id);
        else startNew();
        setPhase(READY);
      } catch (error) {
        // A 401 has already been turned into a sign-out by the api client.
        if (!stale() && !(error instanceof UnauthorizedError)) {
          signOutRef.current("Couldn't reach the server.");
        }
      }
    })();
  }, [api, token, refresh, openSession, startNew, setBadge]);

  // -- actions --------------------------------------------------------------

  const handleConnect = useCallback((value) => {
    if (!value) return;
    setGateError("");
    setToken(value);
    setPhase(CONNECTING);
  }, []);

  const handleSignOut = useCallback(() => {
    devSignedOut.current = true;
    signOut("");
  }, [signOut]);

  const handleNewSession = useCallback(() => {
    setView("chat");
    startNew();
    setFocusToken((n) => n + 1);
  }, [startNew]);

  const handleDelete = useCallback(
    async (id) => {
      await sessions.remove(id);
      if (id === chat.sessionId) startNew();
    },
    [sessions, chat.sessionId, startNew],
  );

  const handleOpenSession = useCallback(
    (id) => {
      setView("chat");
      openSession(id).catch(() => {});
    },
    [openSession],
  );

  // -- render ---------------------------------------------------------------

  if (phase === BOOT) return null;

  if (phase !== READY) {
    return (
      <TokenGate
        error={gateError}
        connecting={phase === CONNECTING}
        onSubmit={handleConnect}
      />
    );
  }

  return (
    <ApiContext.Provider value={api}>
      <div
        className="app"
        data-rail={railPinned ? "pinned" : undefined}
        // While the drag is live the width transition has to come off, or the
        // panel arrives a couple of frames after the pointer and the handle
        // feels loose.
        data-resizing={rail.resizing ? "" : undefined}
        // Omitted below 900px so the stylesheet's phone sizing survives.
        style={rail.enabled ? { "--rail-open": `${rail.width}px` } : undefined}
      >
        {/* The conversation list lives inside the rail now -- it unfolds under
            Chat when the rail opens, so there is no drawer to slide over the
            thread and no second place to look for the same list. */}
        <NavRail
          view={view}
          onView={setView}
          status={status}
          provider={provider}
          onProvider={setProvider}
          pinned={railPinned}
          resizable={rail.enabled}
          resizing={rail.resizing}
          onResizeStart={rail.start}
          onResizeKey={rail.nudge}
          railWidth={rail.width}
          onTogglePin={togglePin}
          sessions={sessions.sessions}
          activeId={chat.sessionId}
          onOpenSession={handleOpenSession}
          onNewSession={handleNewSession}
          onDelete={handleDelete}
        />

        {/* One column beside the rail. The drawer overlays it rather than
            sitting in the flow, so switching destinations never reflows the
            thread underneath. */}
        {/* An empty conversation centres its composer instead of pinning it to
            the bottom of an empty sheet. Flagged here rather than inside the
            thread because the composer is the thread's sibling, not its
            child. */}
        <div
          className="screen"
          data-empty={
            view === "chat" && chat.messages.length === 0 ? "" : undefined
          }
        >
          {view === "chat" ? (
            <>
              <TopBar
                title={chat.title}
                badge={chat.badge}
                onNewSession={handleNewSession}
              />

              <MessageList
                messages={chat.messages}
                model={chat.badge?.text}
                scrollToken={chat.scrollToken}
              />

              <Composer
                disabled={chat.streaming}
                focusToken={focusToken}
                draft={draft}
                sessionLabel={chat.sessionId ? chat.title : null}
                onSend={chat.send}
              />

              {chat.messages.length === 0 ? (
                <Starters onPick={(text) => setDraft({ text })} />
              ) : null}
            </>
          ) : view === "memory" ? (
            <Memory />
          ) : view === "skills" ? (
            <Skills api={api} />
          ) : (
            <Settings
              status={status}
              provider={provider}
              onProvider={setProvider}
              pinned={railPinned}
              onTogglePin={togglePin}
              onSignOut={handleSignOut}
            />
          )}
        </div>
      </div>
    </ApiContext.Provider>
  );
}
