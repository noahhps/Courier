// Transport. Knows about HTTP and SSE framing; knows nothing about React.

import { clientContext } from "./clientContext";
import { serverOrigin } from "./serverOrigin";

export class UnauthorizedError extends Error {
  constructor() {
    super("unauthorized");
    this.name = "UnauthorizedError";
  }
}

/** The server answered and said no. Distinct from the network giving out. */
export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

// FastAPI puts the readable part in `detail` -- a string for the errors we
// raise, a list of field problems for a failed validation. Either way the
// composer should show a sentence, not a serialised object.
async function reason(response) {
  const body = await response.text();
  try {
    const { detail } = JSON.parse(body);
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail) && detail[0]?.msg) return detail[0].msg;
  } catch {
    // not JSON; the raw body is the best thing we have
  }
  return body || response.statusText;
}

// SSE over fetch rather than EventSource: EventSource can't set an
// Authorization header, and this way a dropped connection surfaces as a
// normal rejected promise we can show in the thread.
export async function* readEvents(response) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let split;
    while ((split = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, split);
      buffer = buffer.slice(split + 2);

      let event = "message";
      const data = [];
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).trim());
      }
      if (data.length) {
        yield { event, data: JSON.parse(data.join("\n")) };
      }
    }
  }
}

/**
 * Bind a bearer token to the endpoints the UI actually calls.
 *
 * `onUnauthorized` fires before the rejection propagates, so a token that has
 * stopped working drops the whole app back to the gate no matter which call
 * happened to notice first.
 */
export function createApi(token, onUnauthorized = () => {}) {
  async function request(path, options = {}) {
    // Absolute under Tauri, relative in a browser -- see serverOrigin.
    const response = await fetch(serverOrigin() + "/api" + path, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        Authorization: "Bearer " + token,
        ...(options.headers || {}),
      },
    });
    if (response.status === 401) {
      onUnauthorized();
      throw new UnauthorizedError();
    }
    if (!response.ok) {
      throw new ApiError(await reason(response), response.status);
    }
    return response;
  }

  const json = async (path, options) => (await request(path, options)).json();

  return {
    request,
    status: () => json("/status"),
    listSkills: () => json("/skills"),
    listTools: () => json("/tools"),
    listMcpServers: () => json("/mcp/servers"),
    listMcpPresets: () => json("/mcp/presets"),
    createMcpServer: (server) =>
      json("/mcp/servers", { method: "POST", body: JSON.stringify(server) }),
    instantiateMcpPreset: (data) =>
      json("/mcp/presets/instantiate", {
        method: "POST",
        body: JSON.stringify(data),
      }),
    updateMcpServer: (id, patch) =>
      json("/mcp/servers/" + encodeURIComponent(id), {
        method: "PATCH",
        body: JSON.stringify(patch),
      }),
    deleteMcpServer: (id) =>
      request("/mcp/servers/" + encodeURIComponent(id), { method: "DELETE" }),
    syncMcpServer: (id) =>
      json("/mcp/servers/" + encodeURIComponent(id) + "/sync", {
        method: "POST",
      }),

    // -- MCP icons and import -------------------------------------------
    // Blobs, not URLs, for the same reason attachments are: these endpoints
    // are authenticated, and a bare <img src> cannot set a bearer header, so
    // it would 401 on every icon. The bytes come through fetch and get wrapped
    // in an object URL locally.
    mcpServerIcon: async (id) =>
      (await request("/mcp/servers/" + encodeURIComponent(id) + "/icon")).blob(),
    mcpPresetIcon: async (id) =>
      (await request("/mcp/presets/" + encodeURIComponent(id) + "/icon")).blob(),
    uploadMcpIcon: (id, data, mime) =>
      json("/mcp/servers/" + encodeURIComponent(id) + "/icon", {
        method: "PUT",
        body: JSON.stringify({ data, mime }),
      }),
    clearMcpIcon: (id) =>
      json("/mcp/servers/" + encodeURIComponent(id) + "/icon", { method: "DELETE" }),
    refreshMcpIcon: (id) =>
      json("/mcp/servers/" + encodeURIComponent(id) + "/icon/refresh", {
        method: "POST",
      }),
    importMcpServers: (config, enabled = true) =>
      json("/mcp/servers/import", {
        method: "POST",
        body: JSON.stringify({ config, enabled }),
      }),
    mcpSettings: () => json("/mcp/settings"),
    setMcpSettings: (patch) =>
      json("/mcp/settings", { method: "PATCH", body: JSON.stringify(patch) }),
    listEvents: (since, until) =>
      json(`/events?since=${encodeURIComponent(since)}&until=${encodeURIComponent(until)}`),
    createEvent: (event) =>
      json("/events", { method: "POST", body: JSON.stringify(event) }),
    deleteEvent: (id) => request("/events/" + encodeURIComponent(id), { method: "DELETE" }),
    setSkillKey: (name, key) =>
      json("/skills/" + encodeURIComponent(name) + "/key", {
        method: "PUT",
        body: JSON.stringify({ key }),
      }),
    setSkillEnabled: (name, enabled) =>
      json("/skills/" + encodeURIComponent(name), {
        method: "PATCH",
        body: JSON.stringify({ enabled }),
      }),
    listSessions: () => json("/sessions"),
    createSession: () =>
      json("/sessions", {
        method: "POST",
        body: JSON.stringify({ client: clientContext() }),
      }),
    listProjects: () => json("/projects"),
    createProject: (name) =>
      json("/projects", { method: "POST", body: JSON.stringify({ name }) }),
    renameProject: (id, name) =>
      json("/projects/" + encodeURIComponent(id), {
        method: "PATCH",
        body: JSON.stringify({ name }),
      }),
    deleteProject: (id) =>
      json("/projects/" + encodeURIComponent(id), { method: "DELETE" }),
    setSessionProject: (sessionId, projectId) =>
      json("/sessions/" + encodeURIComponent(sessionId) + "/project", {
        method: "PUT",
        body: JSON.stringify({ project_id: projectId }),
      }),
    getSession: (id) => json("/sessions/" + id),
    deleteSession: (id) => request("/sessions/" + id, { method: "DELETE" }),
    deleteSessions: (ids) =>
      json("/sessions/delete", {
        method: "POST",
        body: JSON.stringify({ ids, confirm: true }),
      }),
    // Resolves to the raw response -- the caller drives it with readEvents().
    // `provider` is "local", "cloud", or null for whatever the router picks.
    // Never switch silently -- the server records which one answered and the
    // reply's meta frame says so.
    chat: (message, sessionId, attachments = [], thinkingLevel = null, provider = null) =>
      request("/chat", {
        method: "POST",
        body: JSON.stringify({
          message,
          session_id: sessionId,
          // Only the three fields the server's AttachmentIn declares -- the
          // reader also hands back `size`, which would fail validation.
          attachments: attachments.map(({ name, mime, data }) => ({ name, mime, data })),
          think: thinkingLevel,
          provider,
          // Sent every time, kept only the first time. This is the path that
          // matters most: the composer posts here with a null session_id to
          // start a conversation, so without it a new chat begun by typing --
          // rather than by pressing New -- would know nothing about the device
          // that started it.
          client: clientContext(),
        }),
      }),
    // A blob, not a URL: the endpoint is authenticated, so the bytes have to
    // come through fetch with the bearer header and be wrapped locally.
    attachment: async (id) => (await request("/attachments/" + id)).blob(),

    // -- memory ---------------------------------------------------------
    // Facts, switches, document counts and index size arrive together: the
    // page renders them as one screen, so it fetches them as one call.
    getMemory: () => json("/memory"),
    addFact: (text, category = null, pinned = false) =>
      json("/memory", {
        method: "POST",
        body: JSON.stringify({ text, category, pinned }),
      }),
    // Only the fields that changed. The server treats every one as optional,
    // which is what lets Edit, Keep always and Looks right? share a route.
    editFact: (id, patch) =>
      json("/memory/" + encodeURIComponent(id), {
        method: "PATCH",
        body: JSON.stringify(patch),
      }),
    forgetFact: (id) =>
      request("/memory/" + encodeURIComponent(id), { method: "DELETE" }),
    forgetAllFacts: () =>
      json("/memory/forget-all", {
        method: "POST",
        body: JSON.stringify({ confirm: true }),
      }),
    setMemorySettings: (patch) =>
      json("/memory/settings", {
        method: "PATCH",
        body: JSON.stringify(patch),
      }),
    reindexMemory: () => json("/memory/reindex", { method: "POST" }),
    // A blob for the same reason as an attachment: the route is behind the
    // bearer token, so a plain link would 401.
    exportMemory: async () => (await request("/memory/export")).blob(),
  };
}
