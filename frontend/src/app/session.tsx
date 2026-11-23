import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import type { SessionInfo } from "../api/types";
import { useQueryClient } from "@tanstack/react-query";
import { setAuthToken } from "../api/client";

interface SessionValue {
  session: SessionInfo | null;
  start: (session: SessionInfo) => void;
  signOut: () => void;
}

const SessionContext = createContext<SessionValue | null>(null);

/**
 * 会话只保存在内存：刷新页面即回到身份选择，符合“不是生产认证”的约定。
 */
export function SessionProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [session, setSession] = useState<SessionInfo | null>(null);

  const start = useCallback((next: SessionInfo) => {
    setAuthToken(next.session_token);
    setSession(next);
  }, []);

  const signOut = useCallback(() => {
    setAuthToken(null);
    queryClient.clear();
    setSession(null);
  }, [queryClient]);

  const value = useMemo(
    () => ({ session, start, signOut }),
    [session, start, signOut],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionValue {
  const value = useContext(SessionContext);
  if (!value) throw new Error("useSession 必须在 SessionProvider 内使用");
  return value;
}
