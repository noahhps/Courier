import { useCallback, useEffect, useState } from "react";

/**
 * The folders conversations can be filed into. Server-owned; this mirrors it.
 *
 * Deliberately thin. A project is a name and an id -- everything that makes it
 * useful (which chats are in it) lives on the sessions themselves, so this
 * hook never has to stay in step with the conversation list.
 */
export function useProjects(api) {
  const [projects, setProjects] = useState([]);
  const [error, setError] = useState(null);

  const refresh = useCallback(async () => {
    try {
      const data = await api.listProjects();
      setProjects(data.projects || []);
      setError(null);
      return data.projects || [];
    } catch (problem) {
      setError(problem.message || String(problem));
      return [];
    }
  }, [api]);

  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const data = await api.listProjects();
        if (live) setProjects(data.projects || []);
      } catch (problem) {
        if (live) setError(problem.message || String(problem));
      }
    })();
    return () => {
      live = false;
    };
  }, [api]);

  const create = useCallback(
    async (name) => {
      const project = await api.createProject(name);
      await refresh();
      return project;
    },
    [api, refresh],
  );

  const rename = useCallback(
    async (id, name) => {
      await api.renameProject(id, name);
      await refresh();
    },
    [api, refresh],
  );

  // The conversations survive -- the column is ON DELETE SET NULL, so they
  // come back as unfiled. The caller still has to refresh the session list,
  // because their `project_id` changed underneath it.
  const remove = useCallback(
    async (id) => {
      await api.deleteProject(id);
      await refresh();
    },
    [api, refresh],
  );

  return { projects, error, refresh, create, rename, remove };
}
