import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../lib/ipc";

const SAVE_DEBOUNCE_MS = 300;

export function useModuleSelection(projectId: string | null) {
  const [selected, setSelectedState] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const projectRef = useRef(projectId);

  const persist = useCallback((id: string, modules: string[]) => {
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => {
      void api.setModuleSelection(id, modules);
    }, SAVE_DEBOUNCE_MS);
  }, []);

  const setSelected = useCallback(
    (next: string[] | ((prev: string[]) => string[])) => {
      setSelectedState((prev) => {
        const modules = typeof next === "function" ? next(prev) : next;
        const pid = projectRef.current;
        if (pid) persist(pid, modules);
        return modules;
      });
    },
    [persist],
  );

  useEffect(() => {
    projectRef.current = projectId;
    if (!projectId) {
      setSelectedState([]);
      return;
    }

    let cancelled = false;
    setLoading(true);

    void (async () => {
      try {
        const [saved, mods] = await Promise.all([
          api.getModuleSelection(projectId),
          api.listModules(projectId),
        ]);
        if (cancelled) return;

        const validIds = new Set(mods.map((m) => m.id));
        const filtered = saved.filter((id) => validIds.has(id));

        if (filtered.length > 0) {
          setSelectedState(filtered);
          return;
        }

        const suggested = await api.getModuleSuggestions(projectId);
        if (cancelled) return;

        const initial = suggested.filter((id) => validIds.has(id));
        setSelectedState(initial);
        if (initial.length > 0) {
          await api.setModuleSelection(projectId, initial);
        }
      } catch {
        if (!cancelled) setSelectedState([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
      if (saveTimer.current) clearTimeout(saveTimer.current);
    };
  }, [projectId]);

  return { selected, setSelected, loading };
}
