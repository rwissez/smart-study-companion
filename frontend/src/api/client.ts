import type { ApiEnvelope, ApiErrorBody } from "./types";

/** 08 的错误码到页面提示的映射，未列出的码用服务返回的 message */
const FALLBACK_MESSAGES: Record<string, string> = {
  FORBIDDEN: "当前身份不能查看这部分内容。",
  NOT_FOUND: "请求的内容不存在。",
  CONFLICT: "内容已更新，请读取最新版本。",
  RESULT_NOT_READY: "结果还在生成，请稍后再看。",
  MODEL_NOT_CONFIGURED: "模型服务尚未配置。",
  INTERNAL_ERROR: "服务内部错误。",
};

export class HttpError extends Error {
  code: string;
  status: number;

  constructor(code: string, message: string, status: number) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

let authToken: string | null = null;

/** 会话 token 只保存在内存，刷新即失效 */
export function setAuthToken(token: string | null) {
  authToken = token;
}

const BASE = "/api/v1";

export async function request<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (authToken) {
    headers.set("Authorization", `Bearer ${authToken}`);
  }

  const response = await fetch(`${BASE}${path}`, { ...init, headers });
  const text = await response.text();
  const payload = text ? (JSON.parse(text) as ApiEnvelope<T> | ApiErrorBody) : null;

  if (!response.ok || (payload && "error" in payload)) {
    const body = payload as ApiErrorBody | null;
    const code = body?.error?.code ?? `HTTP_${response.status}`;
    const message =
      FALLBACK_MESSAGES[code] ?? body?.error?.message ?? "请求失败。";
    throw new HttpError(code, message, response.status);
  }

  return (payload as ApiEnvelope<T>).data;
}

export async function requestFile(path: string): Promise<Blob> {
  const response = await fetch(`${BASE}${path}`, {
    headers: authToken ? { Authorization: `Bearer ${authToken}` } : {},
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null) as ApiErrorBody | null;
    throw new HttpError(body?.error?.code ?? "DOWNLOAD_ERROR", body?.error?.message ?? "下载失败。", response.status);
  }
  return response.blob();
}

export function toMessage(error: unknown): string {
  if (error instanceof HttpError) return error.message;
  if (error instanceof Error) return error.message;
  return "请求失败，请稍后重试。";
}
