import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { setAuthToken, setOwnerKey as setRequestOwnerKey, setUnauthorizedHandler } from "../api/client";
import { dataSource, createSession, type DataSource } from "../api/service";
import type { SessionInfo, SessionScope } from "../api/types";
import type { ScopeKey } from "../lib/queryKeys";

interface SessionContextValue {
  session: SessionInfo | null;
  mode: DataSource;
  epoch: number;
  scopeKey: ScopeKey;
  ownerKey: string | null;
  signingIn: boolean;
  signIn: (role: "teacher" | "student", options?: { datasetId?: string | null; studentId?: string | null }) => Promise<SessionInfo>;
  signOut: () => void;
  switchDataset: (datasetId: string | null) => Promise<SessionInfo | null>;
  rememberOwnerKey: (key: string) => void;
}

const SessionContext = createContext<SessionContextValue | null>(null);

/** 刷新恢复用的同一标签页会话；不是长期登录，关闭标签页即清除。 */
export const SESSION_STORAGE_KEY = "zz-session-v1";
export const LAST_PATH_STORAGE_KEY = "zz-last-path-v1";

/**
 * 跨标签页稳定的会话所有者键。它由服务端按“角色 + 数据集 + 本人”派生并回传，
 * 浏览器只负责持久化；记录里带身份串，切换身份或数据集时读不到旧值，不会串人。
 */
export const OWNER_KEY_STORAGE_KEY = "zz-chat-owner-v1";
const OWNER_KEY_PATTERN = /^ok1_[0-9a-f]{64}$/;

interface StoredOwnerKey {
  identity: string;
  owner_key: string;
}

function ownerIdentity(scope: SessionScope): string {
  return `${scope.role}|${scope.dataset_id ?? ""}|${scope.student_id ?? ""}`;
}

function clearStoredOwnerKey(): void {
  try {
    window.localStorage.removeItem(OWNER_KEY_STORAGE_KEY);
  } catch {
    // 存储不可用不影响本次会话，只是无法跨标签页复用。
  }
}

function readStoredOwnerKey(scope: SessionScope): string | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(OWNER_KEY_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as StoredOwnerKey;
    if (
      !parsed?.owner_key ||
      parsed.identity !== ownerIdentity(scope) ||
      !OWNER_KEY_PATTERN.test(parsed.owner_key)
    ) {
      // 身份已切换或键格式过期：清掉，避免拿别人的键去请求。
      clearStoredOwnerKey();
      return null;
    }
    return parsed.owner_key;
  } catch {
    clearStoredOwnerKey();
    return null;
  }
}

function writeStoredOwnerKey(scope: SessionScope, key: string): void {
  if (!OWNER_KEY_PATTERN.test(key)) return;
  try {
    window.localStorage.setItem(
      OWNER_KEY_STORAGE_KEY,
      JSON.stringify({ identity: ownerIdentity(scope), owner_key: key } satisfies StoredOwnerKey),
    );
  } catch {
    // 存储不可用不影响当前会话，只影响下次跨标签页恢复。
  }
}

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
const initialOwnerKey = initialSession ? readStoredOwnerKey(initialSession.scope) : null;
if (initialSession) setAuthToken(initialSession.session_token);
setRequestOwnerKey(initialOwnerKey);

export function SessionProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [session, setSession] = useState<SessionInfo | null>(initialSession);
  const [ownerKey, setOwnerKeyState] = useState<string | null>(initialOwnerKey);
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
    clearStoredOwnerKey();
    setRequestOwnerKey(null);
    setOwnerKeyState(null);
    invalidateAll();
    setAuthToken(null);
    setSession(null);
  }, [invalidateAll]);

  /** 记住服务端回传的 owner key：写入 localStorage，并让后续请求带上同一个键。 */
  const rememberOwnerKey = useCallback(
    (key: string) => {
      if (!OWNER_KEY_PATTERN.test(key)) return;
      setRequestOwnerKey(key);
      setOwnerKeyState(key);
      if (session) writeStoredOwnerKey(session.scope, key);
    },
    [session],
  );

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
        // 身份/数据集可能已变：只恢复与当前身份匹配的 owner key，读不到就清空。
        const storedOwnerKey = readStoredOwnerKey(info.scope);
        setRequestOwnerKey(storedOwnerKey);
        setOwnerKeyState(storedOwnerKey);
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
    () => ({ session, mode: dataSource, epoch, scopeKey, ownerKey, signingIn, signIn, signOut, switchDataset, rememberOwnerKey }),
    [session, epoch, scopeKey, ownerKey, signingIn, signIn, signOut, switchDataset, rememberOwnerKey],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionContextValue {
  const value = useContext(SessionContext);
  if (!value) throw new Error("useSession 必须在 SessionProvider 内使用。");
  return value;
}
