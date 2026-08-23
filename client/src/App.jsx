// UI only. Zero durable state beyond the bearer token: if this device is
// wiped, nothing is lost, because the server is the source of truth.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Composer } from "./components/Composer";
import { Memory } from "./components/Memory";
import { MessageList } from "./components/MessageList";
import { NavRail } from "./components/NavRail";
import { Settings } from "./components/Settings";
import { Skills } from "./components/Skills";
import { TokenGate } from "./components/TokenGate";
import { TopBar } from "./components/TopBar";
import { useChat } from "./hooks/useChat";
import { useSessions } from "./hooks/useSessions";
import { UnauthorizedError, createApi } from "./lib/api";
import { ApiContext } from "./lib/api-context";

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
  const [phase, setPhase] = useState(() => (localStorage.getItem(TOKEN_KEY) ? BOOT : GATE));
  const [gateError, setGateError] = useState("");
  const [focusToken, setFocusToken] = useState(0);
  // Which of the rail's three destinations is on screen.
  const [view, setView] = useState("chat");
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
      <div className="app" data-rail={railPinned ? "pinned" : undefined}>
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
        <div className="screen">
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
                sessionLabel={chat.sessionId ? chat.title : null}
                onSend={chat.send}
              />
            </>
          ) : view === "memory" ? (
            <Memory />
          ) : view === "skills" ? (
            <Skills />
          ) : (
            <Settings
              status={status}
              provider={provider}
              onProvider={setProvider}
              pinned={railPinned}
              onTogglePin={togglePin}
              onSignOut={() => signOut("")}
            />
          )}
        </div>
      </div>
    </ApiContext.Provider>
  );
}
