import {
  type ReactNode,
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import {
  createAssistantSession,
  listAssistantSessions,
} from "../../api/assistant";
import type { AssistantSession } from "../../types/gateway";

interface AssistantUiState {
  open: boolean;
  setOpen: (open: boolean) => void;
  sessions: AssistantSession[];
  activeSessionId: string | null;
  setActiveSessionId: (id: string | null) => void;
  refreshSessions: () => Promise<void>;
  ensureSession: (llmConfigId: number | null) => Promise<string | null>;
}

const AssistantContext = createContext<AssistantUiState | null>(null);

export function AssistantProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const [sessions, setSessions] = useState<AssistantSession[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);

  const refreshSessions = useCallback(async () => {
    try {
      const list = await listAssistantSessions();
      setSessions(list);
      setActiveSessionId((current) =>
        current && list.some((s) => s.id === current) ? current : (list[0]?.id ?? null),
      );
    } catch {
      setSessions([]);
    }
  }, []);

  useEffect(() => {
    void refreshSessions();
  }, [refreshSessions]);

  const ensureSession = useCallback(
    async (llmConfigId: number | null) => {
      const existing = sessions.find((s) => s.llm_config_id === String(llmConfigId));
      if (existing) {
        setActiveSessionId(existing.id);
        return existing.id;
      }
      try {
        const created = await createAssistantSession("新会话", llmConfigId);
        await refreshSessions();
        setActiveSessionId(created.id);
        return created.id;
      } catch {
        return null;
      }
    },
    [sessions, refreshSessions],
  );

  const value = useMemo<AssistantUiState>(
    () => ({
      open,
      setOpen,
      sessions,
      activeSessionId,
      setActiveSessionId,
      refreshSessions,
      ensureSession,
    }),
    [open, sessions, activeSessionId, refreshSessions, ensureSession],
  );

  return <AssistantContext.Provider value={value}>{children}</AssistantContext.Provider>;
}

export function useAssistant(): AssistantUiState {
  const ctx = useContext(AssistantContext);
  if (!ctx) {
    throw new Error("useAssistant 必须在 AssistantProvider 内使用");
  }
  return ctx;
}
