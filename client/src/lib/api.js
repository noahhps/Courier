// Transport. Knows about HTTP and SSE framing; knows nothing about React.

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
    const response = await fetch("/api" + path, {
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
    listSessions: () => json("/sessions"),
    getSession: (id) => json("/sessions/" + id),
    deleteSession: (id) => request("/sessions/" + id, { method: "DELETE" }),
    // Resolves to the raw response -- the caller drives it with readEvents().
    chat: (message, sessionId) =>
      request("/chat", {
        method: "POST",
        body: JSON.stringify({ message, session_id: sessionId }),
      }),
  };
}
