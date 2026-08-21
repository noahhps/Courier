// UI only. Zero durable state beyond the bearer token: if this device is
// wiped, nothing is lost, because the server is the source of truth.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Composer } from "./components/Composer";
import { Drawer } from "./components/Drawer";
import { MessageList } from "./components/MessageList";
import { TokenGate } from "./components/TokenGate";
import { TopBar } from "./components/TopBar";
import { useAccent } from "./hooks/useAccent";
import { useChat } from "./hooks/useChat";
import { useSessions } from "./hooks/useSessions";
import { UnauthorizedError, createApi } from "./lib/api";
import { ApiContext } from "./lib/api-context";

const TOKEN_KEY = "unified-llm-token";

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
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [focusToken, setFocusToken] = useState(0);
  // Applied to the document root, so it dresses the gate as well as the app.
  const [accent, setAccent] = useAccent();

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

  const chat = useChat(api, { onSessionsChanged });
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
        const status = await api.status();
        if (stale()) return;
        localStorage.setItem(TOKEN_KEY, token);

        if (status.serving === "none") {
          setBadge({ text: "no model reachable", tone: "warn" });
        } else if (status.serving === "cloud") {
          setBadge({ text: "cloud · " + status.cloud.model, tone: "warn" });
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
    setDrawerOpen(false);
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
    <div className="app" data-drawer={drawerOpen ? "open" : undefined}>
      <Drawer
        open={drawerOpen}
        sessions={sessions.sessions}
        activeId={chat.sessionId}
        accent={accent}
        onAccent={setAccent}
        onOpenSession={(id) => openSession(id).catch(() => {})}
        onDelete={handleDelete}
        onClose={() => setDrawerOpen(false)}
      />

      {/* The conversation as one column, so that on a wide screen the drawer
          can sit beside it and simply take its width, rather than each piece
          having to be placed against a grid. */}
      <div className="thread">
        <TopBar
          title={chat.title}
          badge={chat.badge}
          onOpenDrawer={() => {
            setDrawerOpen(true);
            onSessionsChanged();
          }}
          onNewSession={handleNewSession}
        />

        <MessageList messages={chat.messages} scrollToken={chat.scrollToken} />

        <Composer
          disabled={chat.streaming}
          focusToken={focusToken}
          onSend={chat.send}
        />
      </div>
    </div>
    </ApiContext.Provider>
  );
}
