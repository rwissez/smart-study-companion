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

export function SessionProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [session, setSession] = useState<SessionInfo | null>(null);
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
