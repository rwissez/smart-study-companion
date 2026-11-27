import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { setAuthToken, setUnauthorizedHandler } from "../api/client";
import { dataSource, createSession, type DataSource } from "../api/service";
import type { SessionInfo } from "../api/types";
import type { ScopeKey } from "../lib/queryKeys";

interface SessionContextValue {
  session: SessionInfo | null;
  mode: DataSource;
  epoch: number;
  scopeKey: ScopeKey;
  signingIn: boolean;
  signIn: (role: "teacher" | "student", options?: { datasetId?: string | null; studentId?: string | null }) => Promise<SessionInfo>;
  signOut: () => void;
  switchDataset: (datasetId: string | null) => Promise<SessionInfo | null>;
}

const SessionContext = createContext<SessionContextValue | null>(null);

/** 刷新恢复用的同一标签页会话；不是长期登录，关闭标签页即清除。 */
export const SESSION_STORAGE_KEY = "zz-session-v1";
export const LAST_PATH_STORAGE_KEY = "zz-last-path-v1";

function isExpired(value: string): boolean {
  const expires = new Date(value);
  if (Number.isNaN(expires.getTime())) return true;
  return expires.getTime() <= Date.now();
}

function readStoredSession(): SessionInfo | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.sessionStorage.getItem(SESSION_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as SessionInfo;
    if (!parsed?.session_token || !parsed?.scope || isExpired(parsed.expires_at)) {
      window.sessionStorage.removeItem(SESSION_STORAGE_KEY);
      return null;
    }
    return parsed;
  } catch {
    window.sessionStorage.removeItem(SESSION_STORAGE_KEY);
    return null;
  }
}

function writeStoredSession(info: SessionInfo): void {
  try {
    window.sessionStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(info));
  } catch {
    // 存储不可用不影响当前会话，只影响刷新恢复。
  }
}

function clearStoredSession(): void {
  try {
    window.sessionStorage.removeItem(SESSION_STORAGE_KEY);
    window.sessionStorage.removeItem(LAST_PATH_STORAGE_KEY);
  } catch {
    // ignore
  }
}

// 令牌仍然只放在内存和本标签页 sessionStorage：模块求值时先恢复，避免首屏请求
// 在 Provider effect 之前带着空令牌发出。
const initialSession = readStoredSession();
if (initialSession) setAuthToken(initialSession.session_token);

export function SessionProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [session, setSession] = useState<SessionInfo | null>(initialSession);
  const [signingIn, setSigningIn] = useState(false);
  const [epoch, setEpoch] = useState(1);
  const epochRef = useRef(1);

  const invalidateAll = useCallback(() => {
    void queryClient.cancelQueries();
    queryClient.clear();
    epochRef.current += 1;
    setEpoch(epochRef.current);
  }, [queryClient]);

  const signOut = useCallback(() => {
    clearStoredSession();
    invalidateAll();
    setAuthToken(null);
    setSession(null);
  }, [invalidateAll]);

  const signIn = useCallback(
    async (
      role: "teacher" | "student",
      options: { datasetId?: string | null; studentId?: string | null } = {},
    ) => {
      // 先在旧令牌仍可用时建好新会话，再一次性替换令牌、清缓存、切代次；
      // 避免清空令牌到新令牌就绪之间出现“缺少会话令牌”的并发请求。
      setSigningIn(true);
      try {
        const info = await createSession(role, options);
        invalidateAll();
        setAuthToken(info.session_token);
        setSession(info);
        writeStoredSession(info);
        return info;
      } finally {
        setSigningIn(false);
      }
    },
    [invalidateAll],
  );

  const switchDataset = useCallback(
    async (datasetId: string | null) => {
      const role = session?.scope.role;
      if (!role) return null;
      return signIn(role, {
        datasetId,
        studentId: role === "student" ? session?.scope.student_id : null,
      });
    },
    [session, signIn],
  );

  useEffect(() => {
    setUnauthorizedHandler(() => {
      signOut();
      navigate("/identity", { replace: true });
    });
    return () => setUnauthorizedHandler(null);
  }, [navigate, signOut]);

  const scopeKey = useMemo<ScopeKey>(
    () => ({ mode: dataSource, epoch, datasetId: session?.scope.dataset_id ?? null }),
    [epoch, session],
  );

  const value = useMemo<SessionContextValue>(
    () => ({ session, mode: dataSource, epoch, scopeKey, signingIn, signIn, signOut, switchDataset }),
    [session, epoch, scopeKey, signingIn, signIn, signOut, switchDataset],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionContextValue {
  const value = useContext(SessionContext);
  if (!value) throw new Error("useSession 必须在 SessionProvider 内使用。");
  return value;
}
