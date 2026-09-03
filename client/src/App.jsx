// UI only. Zero durable state beyond the bearer token: if this device is
// wiped, nothing is lost, because the server is the source of truth.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Composer } from "./components/Composer";
import { Calendar } from "./components/Calendar";
import { Projects } from "./components/Projects";
import { Memory } from "./components/Memory";
import { MessageList } from "./components/MessageList";
import { NavRail } from "./components/NavRail";
import { Settings } from "./components/Settings";
import { Skills } from "./components/Skills";
import { Starters } from "./components/Starters";
import { TokenGate } from "./components/TokenGate";
import { TopBar } from "./components/TopBar";
import { useChat } from "./hooks/useChat";
import { useProjects } from "./hooks/useProjects";
import { useRailWidth } from "./hooks/useRailWidth";
import { useSessions } from "./hooks/useSessions";
import { useTheme } from "./hooks/useTheme";
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
  // After `api`, not before it: hooks run in source order, and reading `api`
  // above its own `const` is a temporal dead zone error that blanks the page.
  const projects = useProjects(api);
  const { refresh } = sessions;
  const onSessionsChanged = useCallback(() => {
    refresh().catch(() => {});
  }, [refresh]);

  const chat = useChat(api, { onSessionsChanged, provider });
  const { setBadge, openSession, startNew } = chat;

  // The accent in force, and the three scopes it can be set from. Given the
  // open conversation as well as the lists, because an accent set to `auto`
  // is derived from what is being talked about -- it needs the messages, not
  // just the session row.
  const theme = useTheme({
    api,
    sessionId: chat.sessionId,
    sessions: sessions.sessions,
    projects: projects.projects,
    title: chat.title,
    messages: chat.messages,
  });

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

  // A chat that begins life already filed. Sessions are normally created
  // lazily by the first message, so this is the one path that has to make an
  // empty one up front -- there is nowhere else to record the project.
  const handleNewSessionIn = useCallback(
    async (projectId) => {
      const created = await api.createSession();
      if (projectId) await api.setSessionProject(created.id, projectId);
      await onSessionsChanged();
      setView("chat");
      await openSession(created.id).catch(() => {});
      setFocusToken((n) => n + 1);
    },
    [api, onSessionsChanged, openSession],
  );

  const handleFileSession = useCallback(
    async (sessionId, projectId) => {
      await api.setSessionProject(sessionId, projectId);
      await onSessionsChanged();
    },
    [api, onSessionsChanged],
  );

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
          projects={projects.projects}
          onFileSession={handleFileSession}
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
                projects={projects.projects}
                canFile={Boolean(chat.sessionId)}
                projectId={
                  sessions.sessions.find((s) => s.id === chat.sessionId)?.project_id ||
                  null
                }
                onProject={async (projectId) => {
                  await api.setSessionProject(chat.sessionId, projectId);
                  await onSessionsChanged();
                }}
                onNewSession={handleNewSession}
                accent={theme.sessionAccent}
                accentSource={theme.source}
                resolvedAccent={theme.active}
                seed={theme.seed}
                contextSeed={theme.contextSeed}
                onAccent={async (accent) => {
                  await theme.setForSession(chat.sessionId, accent);
                  await onSessionsChanged();
                }}
              />

              <MessageList
                messages={chat.messages}
                model={chat.badge?.text}
                scrollToken={chat.scrollToken}
              />

              <Composer
                disabled={chat.streaming}
                onStop={chat.stop}
                focusToken={focusToken}
                draft={draft}
                sessionLabel={chat.sessionId ? chat.title : null}
                // Whichever side is actually answering describes its own
                // reasoning control; the composer draws what it is handed.
                thinking={
                  (provider === "cloud" || status?.serving === "cloud"
                    ? status?.cloud?.thinking
                    : status?.local?.thinking) || null
                }
                onSend={chat.send}
              />

              {chat.messages.length === 0 ? (
                <Starters onPick={(text) => setDraft({ text })} />
              ) : null}
            </>
          ) : view === "projects" ? (
            <Projects
              projects={projects.projects}
              sessions={sessions.sessions}
              onOpenSession={handleOpenSession}
              onNewProject={(name) => projects.create(name)}
              onRenameProject={(id, name) => projects.rename(id, name)}
              onDeleteProject={async (id) => {
                await projects.remove(id);
                await onSessionsChanged();
              }}
              onNewSessionIn={handleNewSessionIn}
              onFileSession={handleFileSession}
              accentOf={theme.accentFor}
              seedOfRecord={theme.seedFor}
              onProjectAccent={async (id, accent) => {
                await theme.setForProject(id, accent);
                await projects.refresh();
              }}
            />
          ) : view === "calendar" ? (
            <Calendar api={api} onSessionsChanged={onSessionsChanged} />
          ) : view === "memory" ? (
            <Memory api={api} />
          ) : view === "skills" ? (
            <Skills api={api} />
          ) : (
            <Settings
              status={status}
              provider={provider}
              onProvider={setProvider}
              pinned={railPinned}
              onTogglePin={togglePin}
              api={api}
              sessions={sessions.sessions}
              onSessionsChanged={onSessionsChanged}
              onSignOut={() => signOut("")}
              theme={theme}
            />
          )}
        </div>
      </div>
    </ApiContext.Provider>
  );
}
