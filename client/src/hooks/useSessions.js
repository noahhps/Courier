import { useCallback, useState } from "react";

/** The conversation list in the drawer. Server-owned; this only mirrors it. */
export function useSessions(api) {
  const [sessions, setSessions] = useState([]);

  const refresh = useCallback(async () => {
    const data = await api.listSessions();
    setSessions(data.sessions);
    return data.sessions;
  }, [api]);

  const remove = useCallback(
    async (id) => {
      await api.deleteSession(id);
      return refresh();
    },
    [api, refresh],
  );

  return { sessions, refresh, remove };
}
